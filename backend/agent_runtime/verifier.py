"""
FORGE Agent Runtime — flag verifier.

Detection and verification are deliberately separate concerns (Step 2):

* A regex match is only ever a **FLAG_CANDIDATE**.
* Only :class:`FlagVerifier` may promote a candidate to **FLAG_VERIFIED**, and only
  the runtime acts on FLAG_VERIFIED to complete the mission.

Verification weighs: the *source* of the value (real command output vs. model
prose), whether the producing action actually succeeded, the format, whether the
value was merely echoed/constructed by the agent (present in the command itself),
placeholder shapes, and target relevance.

The regexes here are the canonical set for the runtime and intentionally mirror the
strict patterns already used by ``swarm_orchestrator`` (known CTF prefixes only, a
minimum body length, and braces excluded from the body so an echoed template like
``picoCTF{{{flag}}}`` can never match as a real flag). They are defined here (rather
than imported from the swarm) so the runtime is a self-contained subsystem and does
not drag the swarm's heavy import chain in just for two patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

# Strict flag regex — known CTF platform prefixes only, ≥4 chars, braces excluded
# from the body (see module docstring). No generic catch-all → no CSS/LaTeX/JSON FPs.
FLAG_REGEX = re.compile(
    r"(?:picoCTF\{[^{}]{4,}\}|FLAG\{[^{}]{4,}\}|flag\{[^{}]{4,}\}|HTB\{[^{}]{4,}\}|CTF\{[^{}]{4,}\}|"
    r"DUCTF\{[^{}]{4,}\}|corctf\{[^{}]{4,}\}|TFCCTF\{[^{}]{4,}\}|pwn\.college\{[^{}]{4,}\}|THM\{[^{}]{4,}\})",
    re.IGNORECASE,
)

# Shapes that look like a flag but are placeholders/examples from model prose.
FALSE_FLAG_PATTERNS = re.compile(
    r"(?:picoCTF\{\.\.\.\}|FLAG\{\.\.\.\}|HTB\{\.\.\.\}|CTF\{\.\.\.\}|"
    r"\{[a-z_]+_here\}|\{example[^}]*\}|\{your[^}]*\}|\{placeholder[^}]*\}|"
    r"\{some[^}]*\}|\{flag[^}]*format[^}]*\}|\{insert[^}]*\}|\{\.\.\.\})",
    re.IGNORECASE,
)


class FlagStatus(str, Enum):
    VERIFIED = "FLAG_VERIFIED"
    CANDIDATE = "FLAG_CANDIDATE"
    REJECTED = "REJECTED"


class FlagSource(str, Enum):
    TOOL_OUTPUT = "tool_output"      # extracted from a real command's stdout/stderr
    LLM_PROSE = "llm_prose"          # asserted by the model, not seen in tool output
    UNKNOWN = "unknown"


@dataclass
class FlagVerdict:
    status: FlagStatus
    confidence: float
    candidate: str
    reasons: List[str] = field(default_factory=list)

    @property
    def is_verified(self) -> bool:
        return self.status == FlagStatus.VERIFIED


class FlagVerifier:
    """Transitions a candidate through REJECTED / CANDIDATE / VERIFIED — nothing else does."""

    def looks_like_flag(self, text: str) -> bool:
        return bool(text) and bool(FLAG_REGEX.search(text)) and not FALSE_FLAG_PATTERNS.search(text)

    def assess(
        self,
        candidate: str,
        *,
        source: FlagSource = FlagSource.UNKNOWN,
        command: str = "",
        action_succeeded: bool = True,
        target_scope: str = "",
        expected_format: str = "",
    ) -> FlagVerdict:
        candidate = (candidate or "").strip()
        reasons: List[str] = []

        # ── Format gate ──
        if not candidate or not FLAG_REGEX.search(candidate):
            return FlagVerdict(FlagStatus.REJECTED, 0.0, candidate,
                               ["Does not match any known CTF flag format."])
        # Normalise to the matched substring (drops surrounding noise).
        m = FLAG_REGEX.search(candidate)
        candidate = m.group(0)

        if FALSE_FLAG_PATTERNS.search(candidate):
            return FlagVerdict(FlagStatus.REJECTED, 0.0, candidate,
                               ["Matches a placeholder/example shape, not a real flag."])

        # ── Echoed / constructed gate ──
        # If the exact value already appears in the command the agent issued, it
        # generated/echoed it — it is NOT evidence of a captured flag.
        if command and candidate in command:
            return FlagVerdict(FlagStatus.REJECTED, 0.1, candidate,
                               ["Value appears in the issued command itself (echoed/constructed, not captured)."])

        # ── Optional expected-format corroboration (soft) ──
        if expected_format:
            prefix = candidate.split("{", 1)[0].lower()
            if prefix and prefix not in expected_format.lower() and "{...}" not in expected_format:
                reasons.append(f"Prefix '{prefix}' differs from expected format '{expected_format}'.")

        # ── Source + action-success determine the verdict ──
        if source == FlagSource.TOOL_OUTPUT:
            if action_succeeded:
                reasons.append("Extracted from the output of a command that succeeded.")
                conf = 0.95 if not any(r.startswith("Prefix") for r in reasons) else 0.8
                return FlagVerdict(FlagStatus.VERIFIED, conf, candidate, reasons)
            reasons.append("From tool output, but the producing action did not report success — needs corroboration.")
            return FlagVerdict(FlagStatus.CANDIDATE, 0.55, candidate, reasons)

        if source == FlagSource.LLM_PROSE:
            reasons.append("Asserted in model prose; not observed in real command output — unverified.")
            return FlagVerdict(FlagStatus.CANDIDATE, 0.35, candidate, reasons)

        reasons.append("Source of the value is unknown — treated as an unverified candidate.")
        return FlagVerdict(FlagStatus.CANDIDATE, 0.4, candidate, reasons)

    def assess_observation(self, obs: Any, *, command: str = "", action_succeeded: bool = True,
                           target_scope: str = "", expected_format: str = "") -> Optional[FlagVerdict]:
        """Verify the first flag candidate found on a structured Observation (tool output)."""
        candidates = getattr(obs, "flag_candidates", None) or []
        if not candidates:
            return None
        return self.assess(candidates[0], source=FlagSource.TOOL_OUTPUT, command=command,
                           action_succeeded=action_succeeded, target_scope=target_scope,
                           expected_format=expected_format)
