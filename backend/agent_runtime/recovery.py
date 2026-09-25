"""
FORGE Agent Runtime — recovery engine (Step 2).

Classifies *why* a turn failed and produces a concrete :class:`RecoveryPlan` — a
strategy plus a plain-language directive that gets injected into the next context so
the agent changes approach instead of blindly repeating a failed action.

Handled conditions: timeout, command-not-found, syntax error, missing dependency,
permission failure, network failure, repeated action, no-progress, provider failure,
malformed model response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from backend.agent_runtime.repetition import RepetitionKind, RepetitionReport


class FailureCategory(str, Enum):
    NONE = "NONE"
    TIMEOUT = "TIMEOUT"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    INVALID_URL = "INVALID_URL"
    INTERPRETER_ASSUMPTION = "INTERPRETER_ASSUMPTION"
    PERMISSION_FAILURE = "PERMISSION_FAILURE"
    CAPABILITY_GAP = "CAPABILITY_GAP"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    REPEATED_ACTION = "REPEATED_ACTION"
    NO_PROGRESS = "NO_PROGRESS"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    GENERIC_FAILURE = "GENERIC_FAILURE"


class RecoveryStrategy(str, Enum):
    CONTINUE = "CONTINUE"                 # nothing wrong — proceed
    RETRY_MODIFIED = "RETRY_MODIFIED"     # same goal, changed command
    INSTALL_DEPENDENCY = "INSTALL_DEPENDENCY"
    ESCALATE_PRIVILEGE = "ESCALATE_PRIVILEGE"
    WAIT_RETRY = "WAIT_RETRY"             # transient (network/timeout) — back off, retry
    SWITCH_PROVIDER = "SWITCH_PROVIDER"   # provider layer should fail over
    REPLAN = "REPLAN"                     # diagnose → new strategy
    ABORT = "ABORT"                       # unrecoverable


@dataclass
class RecoveryPlan:
    category: FailureCategory
    strategy: RecoveryStrategy
    directive: str = ""                   # injected into the next context
    escalate: bool = False
    reasons: List[str] = field(default_factory=list)

    @property
    def needs_action(self) -> bool:
        return self.strategy != RecoveryStrategy.CONTINUE


# Package-name resolution reused conceptually from orchestrator_loop.IMPORT_TO_PACKAGE_MAP;
# kept minimal + local so recovery has no dependency on the orchestrator.
_MISSING_MODULE_RE = re.compile(r"ModuleNotFoundError: No module named ['\"]([\w.]+)['\"]")
_MISSING_TOOL_RE = re.compile(r"(?:command not found|not recognized as).*?['\"]?([\w.\-]+)['\"]?", re.IGNORECASE)
_CMDNAME_RE = re.compile(r"^(\w[\w.\-]*)")


class RecoveryEngine:
    """Deterministic failure diagnosis. Returns a plan; the runtime enacts it."""

    ABORT_AFTER_CONSECUTIVE = 8   # hard ceiling on unbroken failures before ABORT

    def __init__(self):
        self._consecutive_failures = 0

    def note_success(self) -> None:
        self._consecutive_failures = 0

    def diagnose(
        self,
        *,
        exec_result: Optional[Any] = None,
        observation: Optional[Any] = None,
        repetition: Optional[RepetitionReport] = None,
        provider_failed: bool = False,
        malformed_response: bool = False,
        command: str = "",
    ) -> RecoveryPlan:
        # ── Model-layer problems first (no exec happened) ──
        if provider_failed:
            return RecoveryPlan(
                FailureCategory.PROVIDER_FAILURE, RecoveryStrategy.SWITCH_PROVIDER,
                "The model provider failed to respond. Failing over to another provider; "
                "the mission state is intact.",
                reasons=["Provider returned a refusal/exhaustion."],
            )
        if malformed_response:
            return RecoveryPlan(
                FailureCategory.MALFORMED_RESPONSE, RecoveryStrategy.RETRY_MODIFIED,
                "Your previous response could not be parsed into a single command, python "
                "block, FLAG:, or BUDGET_EXHAUSTED. Reply with exactly ONE of those forms.",
                reasons=["Unparseable model output."],
            )

        stderr = (getattr(exec_result, "stderr", "") or "") if exec_result else ""
        stdout = (getattr(exec_result, "stdout", "") or "") if exec_result else ""
        status = getattr(exec_result, "status", "SUCCESS") if exec_result else "SUCCESS"
        failure_cat = getattr(exec_result, "failure_category", None) if exec_result else None
        reason = (getattr(exec_result, "reason", "") or "") if exec_result else ""
        combined = f"{stdout}\n{stderr}"
        cmd = command or (getattr(exec_result, "command", "") if exec_result else "")

        failed = bool(exec_result) and (status not in ("SUCCESS",) or getattr(exec_result, "execution_failure", False))

        # ── Repetition-driven replans take priority over a soft continue ──
        if repetition and repetition.force_replan:
            self._bump(failed)
            return RecoveryPlan(
                FailureCategory.REPEATED_ACTION if repetition.kind in (
                    RepetitionKind.EXACT_REPEAT, RepetitionKind.SEMANTIC_REPEAT,
                    RepetitionKind.SAME_TARGET_SAME_METHOD, RepetitionKind.SAME_FAILURE,
                ) else FailureCategory.NO_PROGRESS,
                RecoveryStrategy.REPLAN,
                self._replan_directive(repetition),
                reasons=repetition.reasons or [repetition.detail],
            )

        if not failed:
            self.note_success()
            return RecoveryPlan(FailureCategory.NONE, RecoveryStrategy.CONTINUE)

        self._bump(True)

        # ── Local-execution failures already classified by the ToolManager/runtime ──
        # (Task 4) Surface the STRUCTURED failure category with an actionable directive
        # that names local-vs-target and whether an identical retry is useful, rather than
        # forcing the model to infer everything from raw log text.
        if failure_cat == "FILE_NOT_FOUND" or (
                not failure_cat and ("[errno 2]" in combined.lower()
                                     or "no such file or directory" in combined.lower())):
            return RecoveryPlan(
                FailureCategory.FILE_NOT_FOUND, RecoveryStrategy.RETRY_MODIFIED,
                "LOCAL execution failure (not a target response): the action referenced a local "
                "file/artifact that does not exist in the workspace. Create, download or generate "
                "that artifact in a prior step, or choose a technique that does not need it. Do NOT "
                "re-run the same action unchanged.",
                reasons=["Referenced local file/artifact is missing."],
            )
        if failure_cat == "INVALID_URL" or (
                not failure_cat and ("no scheme supplied" in combined.lower()
                                     or "invalid url" in combined.lower())):
            return RecoveryPlan(
                FailureCategory.INVALID_URL, RecoveryStrategy.RETRY_MODIFIED,
                "LOCAL execution failure: an HTTP request used a scheme-less/relative URL. Reissue it "
                "against the FULL canonical target URL (with scheme and host) — never pass a bare "
                "filename or path to an HTTP client. Do NOT re-run the same request unchanged.",
                reasons=["Scheme-less/relative URL passed to an HTTP client."],
            )
        if failure_cat == "INTERPRETER_ASSUMPTION":
            return RecoveryPlan(
                FailureCategory.INTERPRETER_ASSUMPTION, RecoveryStrategy.RETRY_MODIFIED,
                "LOCAL execution failure: the script used a Python-2-only construct (e.g. "
                "str.encode('base64')). This host runs Python 3 — use the base64 / binascii / codecs "
                "modules instead, and resend a corrected script.",
                reasons=["Python-2-only construct on a Python-3 host."],
            )

        # ── Concrete failure classification (order = specificity) ──
        if status == "TIMEOUT" or "timed out" in combined.lower():
            return RecoveryPlan(
                FailureCategory.TIMEOUT, RecoveryStrategy.RETRY_MODIFIED,
                "The last command timed out. Narrow its scope (smaller wordlist, shorter "
                "range, add a lower --timeout) or split it into faster steps.",
                reasons=["Execution timed out."],
            )

        mod = _MISSING_MODULE_RE.search(combined)
        if mod or "ModuleNotFoundError" in combined or "ImportError" in combined:
            pkg = mod.group(1).split(".")[0] if mod else "the missing module"
            return RecoveryPlan(
                FailureCategory.MISSING_DEPENDENCY, RecoveryStrategy.INSTALL_DEPENDENCY,
                f"A required Python module is missing ({pkg}). Install it "
                f"(e.g. `pip install {pkg}`) or use a technique that avoids it.",
                reasons=[f"Missing python module: {pkg}"],
            )

        if "command not found" in combined.lower() or "not recognized as" in combined.lower() or failure_cat == "MISSING_TOOL":
            tool = self._guess_tool(cmd, combined)
            return RecoveryPlan(
                FailureCategory.COMMAND_NOT_FOUND, RecoveryStrategy.RETRY_MODIFIED,
                f"The tool '{tool}' is not installed on this host. Use an installed "
                f"alternative (check the environment tool list) or install it first.",
                reasons=[f"Tool not found: {tool}"],
            )

        if (failure_cat in ("CAPABILITY_GAP", "PRIVILEGE_DENIED") or status == "CAPABILITY_GAP"
                or "capability_gap" in reason.lower() or "privilege_denied" in reason.lower()
                or ("privilege" in reason.lower() and "denied" in reason.lower())):
            return RecoveryPlan(
                FailureCategory.CAPABILITY_GAP, RecoveryStrategy.ESCALATE_PRIVILEGE,
                "The requested action was denied due to privilege/capability restrictions (capability gap). "
                "Escalate privilege through the approval pipeline or select an alternative unprivileged approach.",
                escalate=True, reasons=["Privilege requirement denied (capability gap)."],
            )

        if "permission denied" in combined.lower() or "operation not permitted" in combined.lower():
            return RecoveryPlan(
                FailureCategory.PERMISSION_FAILURE, RecoveryStrategy.ESCALATE_PRIVILEGE,
                "The command failed on permissions. If root is genuinely required, request "
                "privilege escalation; otherwise choose an unprivileged technique.",
                escalate=True, reasons=["Permission denied."],
            )

        if any(s in combined for s in ("SyntaxError", "unexpected token", "parse error", "IndentationError")):
            return RecoveryPlan(
                FailureCategory.SYNTAX_ERROR, RecoveryStrategy.RETRY_MODIFIED,
                "The previous script/command had a syntax error. Fix the syntax and resend a "
                "corrected version — do not resend the same broken text.",
                reasons=["Syntax error in command/script."],
            )

        if (failure_cat in ("NETWORK", "DNS", "CONNECTION") or
                any(s in combined.lower() for s in ("could not resolve host", "connection refused",
                                                    "connection timed out", "network is unreachable"))):
            return RecoveryPlan(
                FailureCategory.NETWORK_FAILURE, RecoveryStrategy.WAIT_RETRY,
                "The target appears unreachable (network/DNS). Verify the target is up and the "
                "address/port are correct before retrying; consider re-resolving the host.",
                reasons=["Network/DNS failure reaching target."],
            )

        # ── Generic failure / hard ceiling ──
        if self._consecutive_failures >= self.ABORT_AFTER_CONSECUTIVE:
            return RecoveryPlan(
                FailureCategory.GENERIC_FAILURE, RecoveryStrategy.ABORT,
                f"{self._consecutive_failures} consecutive failures with no progress — the "
                "current approach is exhausted.",
                reasons=["Consecutive-failure ceiling reached."],
            )
        return RecoveryPlan(
            FailureCategory.GENERIC_FAILURE, RecoveryStrategy.RETRY_MODIFIED,
            "The last command failed. Diagnose the error output above and try a materially "
            "different approach — do not repeat the same command.",
            reasons=["Command failed (generic)."],
        )

    # ------------------------------------------------------------------ #

    def _bump(self, failed: bool) -> None:
        if failed:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

    @staticmethod
    def _guess_tool(cmd: str, combined: str) -> str:
        m = _MISSING_TOOL_RE.search(combined)
        if m and m.group(1):
            return m.group(1)
        m2 = _CMDNAME_RE.match((cmd or "").strip())
        return m2.group(1) if m2 else "the tool"

    @staticmethod
    def _replan_directive(rep: RepetitionReport) -> str:
        if rep.kind == RepetitionKind.SAME_FAILURE:
            return ("You have already tried this exact approach and it failed the same way. "
                    "STOP repeating it. Diagnose the root cause, then choose a NEW strategy "
                    "targeting a different attack surface.")
        if rep.kind == RepetitionKind.NO_PROGRESS:
            return ("Several recent turns produced no new information. Step back and REPLAN: "
                    "state your current hypothesis, what evidence would confirm/deny it, and a "
                    "concretely different next action.")
        return ("You are repeating actions you have already run. Do NOT reissue them. Pick a "
                "different technique or target surface, or explicitly explain why a repeat is warranted.")
