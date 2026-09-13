"""
FORGE Agent Runtime — repetition detection (Step 7).

A first-class runtime component (not just another prompt warning). It normalises
actions and distinguishes genuinely-different attempts from disguised repeats:

    curl /login
    curl /login          → EXACT_REPEAT
    curl /login          → EXACT_REPEAT

but

    curl /login
    curl -X POST /login  → different method  (NOT a repeat)
    python exploit.py    → different tool     (NOT a repeat)

Detection kinds:
* EXACT_REPEAT            — byte-identical (after normalisation) to a prior action.
* SEMANTIC_REPEAT         — same (tool, method, target, path) signature as a prior action.
* SAME_TARGET_SAME_METHOD — same (tool, method, host) but a different path/param.
* SAME_FAILURE            — a semantically-equal action that already failed the same way.
* NO_PROGRESS             — a streak of turns whose observations were non-novel.

When the no-progress / repeat streak crosses a threshold the runtime is told to
DIAGNOSE → REPLAN → NEW STRATEGY rather than to inject yet another warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from urllib.parse import urlsplit


class RepetitionKind(str, Enum):
    NONE = "NONE"
    EXACT_REPEAT = "EXACT_REPEAT"
    SEMANTIC_REPEAT = "SEMANTIC_REPEAT"
    SAME_TARGET_SAME_METHOD = "SAME_TARGET_SAME_METHOD"
    SAME_FAILURE = "SAME_FAILURE"
    NO_PROGRESS = "NO_PROGRESS"


_HTTP_TOOLS = {"curl", "wget", "http", "https", "httpie"}
_STATEFUL_STREAM_TOOLS = {"interactive_open", "interactive_read", "interactive_send", "interactive_send_and_read", "interactive_close"}
_WS = re.compile(r"\s+")
_METHOD_RE = re.compile(r"-X\s+([A-Za-z]+)|--request\s+([A-Za-z]+)", re.IGNORECASE)



@dataclass
class _Entry:
    normalized: str
    semantic_sig: str
    host_method_sig: str
    failed: bool
    failure_category: Optional[str]
    novel: bool


@dataclass
class RepetitionReport:
    kind: RepetitionKind = RepetitionKind.NONE
    detail: str = ""
    no_progress_streak: int = 0
    repeat_streak: int = 0
    force_replan: bool = False
    reasons: List[str] = field(default_factory=list)


class RepetitionDetector:
    """Stateful across a session; `classify()` is pre-exec, `observe()` is post-exec."""

    def __init__(self, no_progress_threshold: int = 3, repeat_threshold: int = 2):
        self.no_progress_threshold = no_progress_threshold
        self.repeat_threshold = repeat_threshold
        self._history: List[_Entry] = []
        self.no_progress_streak = 0
        self.repeat_streak = 0

    # ------------------------------------------------------------------ #
    # Normalisation
    # ------------------------------------------------------------------ #

    @staticmethod
    def normalize(action_text: str) -> str:
        t = _WS.sub(" ", (action_text or "").strip().lower())
        # Strip matching surrounding quotes on the whole thing; keep internal structure.
        return t.strip("'\"").strip()

    @classmethod
    def semantic_signature(cls, action_text: str) -> str:
        """(tool, method, host, path) — differs when method or path differ."""
        text = (action_text or "").strip()
        if not text:
            return ""
        tokens = text.split()
        tool = tokens[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        method = cls._http_method(text) if tool in _HTTP_TOOLS else ""
        host, path = cls._host_path(text) if tool in _HTTP_TOOLS else ("", "")
        if tool in _HTTP_TOOLS:
            return f"{tool}|{method}|{host}|{path}"
        # Non-HTTP tools: tool + sorted significant (non-value) flags + first operand basename.
        flags = sorted(tok for tok in tokens[1:] if tok.startswith("-"))
        operand = next((tok for tok in tokens[1:] if not tok.startswith("-")), "")
        operand = operand.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return f"{tool}|{'.'.join(flags)}|{operand}"

    @classmethod
    def host_method_signature(cls, action_text: str) -> str:
        text = (action_text or "").strip()
        tokens = text.split()
        if not tokens:
            return ""
        tool = tokens[0].lower().rsplit("/", 1)[-1]
        if tool not in _HTTP_TOOLS:
            return ""
        host, _ = cls._host_path(text)
        return f"{tool}|{cls._http_method(text)}|{host}"

    @staticmethod
    def _http_method(text: str) -> str:
        m = _METHOD_RE.search(text)
        if m:
            return (m.group(1) or m.group(2) or "").upper()
        if re.search(r"(?:^|\s)(?:--data|--data-raw|-d|-F|--form)(?:\s|=)", text):
            return "POST"
        return "GET"

    @staticmethod
    def _host_path(text: str):
        m = re.search(r"https?://[^\s\"']+", text)
        if not m:
            # bare host[:port]/path token
            m2 = re.search(r"(?<!\S)([a-z0-9.\-]+(?::\d+)?(/[^\s\"']*)?)", text, re.IGNORECASE)
            if m2:
                raw = m2.group(1)
                host = raw.split("/", 1)[0]
                path = "/" + raw.split("/", 1)[1] if "/" in raw else "/"
                return host, path.split("?", 1)[0]
            return "", ""
        parts = urlsplit(m.group(0))
        return parts.netloc.lower(), (parts.path or "/")

    # ------------------------------------------------------------------ #
    # Classification (pre-exec) — is this action a repeat of something already tried?
    # ------------------------------------------------------------------ #

    def classify(self, action_text: str) -> RepetitionKind:
        tokens = (action_text or "").strip().split()
        if tokens and tokens[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1] in _STATEFUL_STREAM_TOOLS:
            return RepetitionKind.NONE

        norm = self.normalize(action_text)
        if not norm:
            return RepetitionKind.NONE
        sem = self.semantic_signature(action_text)
        hm = self.host_method_signature(action_text)

        for e in self._history:
            if e.normalized == norm:
                return RepetitionKind.EXACT_REPEAT
        for e in self._history:
            if sem and e.semantic_sig == sem:
                return RepetitionKind.SEMANTIC_REPEAT
        for e in self._history:
            if hm and e.host_method_sig == hm:
                return RepetitionKind.SAME_TARGET_SAME_METHOD
        return RepetitionKind.NONE

    # ------------------------------------------------------------------ #
    # Observation (post-exec) — record outcome + update streaks, return a report.
    # ------------------------------------------------------------------ #

    def observe(self, action_text: str, *, failed: bool, failure_category: Optional[str],
                novel: bool) -> RepetitionReport:
        norm = self.normalize(action_text)
        sem = self.semantic_signature(action_text)
        hm = self.host_method_signature(action_text)
        tokens = (action_text or "").strip().split()
        is_stateful = bool(tokens and tokens[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1] in _STATEFUL_STREAM_TOOLS)

        report = RepetitionReport()

        # SAME_FAILURE: a semantically-equal action already failed the same way.
        if failed:
            for e in self._history:
                if e.failed and sem and e.semantic_sig == sem and e.failure_category == failure_category:
                    report.kind = RepetitionKind.SAME_FAILURE
                    report.detail = f"Action failed identically before ({failure_category})."
                    break

        # Streaks.
        if novel:
            self.no_progress_streak = 0
        else:
            self.no_progress_streak += 1

        already_seen = not is_stateful and any(e.normalized == norm for e in self._history)
        if already_seen:
            self.repeat_streak += 1
            if report.kind == RepetitionKind.NONE:
                report.kind = RepetitionKind.EXACT_REPEAT
                report.detail = "Exact action repeated."
        else:
            self.repeat_streak = 0


        self._history.append(_Entry(norm, sem, hm, failed, failure_category, novel))

        report.no_progress_streak = self.no_progress_streak
        report.repeat_streak = self.repeat_streak
        if self.no_progress_streak >= self.no_progress_threshold:
            report.force_replan = True
            report.reasons.append(f"{self.no_progress_streak} consecutive non-novel turns.")
            if report.kind == RepetitionKind.NONE:
                report.kind = RepetitionKind.NO_PROGRESS
        if self.repeat_streak >= self.repeat_threshold:
            report.force_replan = True
            report.reasons.append(f"{self.repeat_streak} repeated actions.")
        if report.kind == RepetitionKind.SAME_FAILURE:
            report.force_replan = True
            report.reasons.append("Same action, same failure.")
        return report

    def reset_streaks(self) -> None:
        """Called after a forced replan so a new strategy gets a clean slate."""
        self.no_progress_streak = 0
        self.repeat_streak = 0
