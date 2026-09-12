"""
FORGE Agent Runtime — Answer & Flag Resolver / Verifier.

WHAT: Generic answer candidate recognition, semantic resolution, and authoritative verification.
HOW:  Model-powered tools (vision_read), deterministic tools, and regex/signature tables.
WHETHER: Privilege, capability, and safety controls (unmodified).

Detection, Resolution, and Verification are distinct concerns:

1. Candidate Extraction:
   A candidate can be discovered from any evidence source (tool output, vision_read,
   reconstructed artifacts, decoded directives, or agent reasoning).
2. Answer Resolution:
   Evaluates "What answer is this challenge actually asking for?" against the challenge's
   semantic question/task context (flag string, username, hash, number, file, key, text),
   filtering placeholders and verifying type alignment.
3. Answer Verification:
   Weighs evidence quality, provenance, and source confidence (e.g., tool_output,
   vision_read on a verified reconstructed artifact, deterministic decode) to promote
   candidates to RESOLVED / VERIFIED, or preserve them as unverified CANDIDATEs without
   losing their provenance.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# Strict flag regex — known CTF platform prefixes only, ≥4 chars, braces excluded
# from the body. No generic catch-all → no CSS/LaTeX/JSON FPs.
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

# Common hash patterns
MD5_REGEX = re.compile(r"\b[a-f0-9]{32}\b", re.IGNORECASE)
SHA1_REGEX = re.compile(r"\b[a-f0-9]{40}\b", re.IGNORECASE)
SHA256_REGEX = re.compile(r"\b[a-f0-9]{64}\b", re.IGNORECASE)


class AnswerType(str, Enum):
    FLAG = "flag"
    HASH = "hash"
    USERNAME = "username"
    NUMBER = "number"
    FILENAME = "filename"
    KEY = "key"
    STRING = "string"
    CUSTOM = "custom"


class AnswerStatus(str, Enum):
    VERIFIED = "FLAG_VERIFIED"
    RESOLVED = "RESOLVED"
    CANDIDATE = "FLAG_CANDIDATE"
    REJECTED = "REJECTED"


# Backward compatibility aliases
FlagStatus = AnswerStatus


class AnswerSource(str, Enum):
    TOOL_OUTPUT = "tool_output"                    # direct command execution stdout/stderr
    VISION_READ = "vision_read"                    # model-powered visual extraction from image/artifact
    RECONSTRUCTED_ARTIFACT = "reconstructed_artifact"  # extracted from reconstructed file/bytes
    RECONSTRUCTED_ARTIFACT_OCR = "reconstructed_artifact_ocr"  # OCR of reconstructed artifact
    DECODED_ARTIFACT = "decoded_artifact"          # deterministic decode of hidden text/stream
    LLM_PROSE = "llm_prose"                        # asserted by model in prose, not observed in evidence
    UNKNOWN = "unknown"


# Backward compatibility alias
FlagSource = AnswerSource


@dataclass
class AnswerCandidate:
    """Represents an answer candidate with full evidence, provenance, and task context."""
    value: str
    answer_type: AnswerType = AnswerType.FLAG
    source: AnswerSource = AnswerSource.UNKNOWN
    confidence: float = 0.5
    worker_id: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    task_context: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    status: AnswerStatus = AnswerStatus.CANDIDATE
    reasons: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "answer_type": self.answer_type.value,
            "source": self.source.value if isinstance(self.source, Enum) else str(self.source),
            "confidence": self.confidence,
            "worker_id": self.worker_id,
            "evidence": self.evidence,
            "task_context": self.task_context,
            "provenance": self.provenance,
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "reasons": self.reasons,
            "created_at": self.created_at,
        }


@dataclass
class AnswerVerdict:
    """Verdict returned by AnswerResolver / FlagVerifier."""
    status: AnswerStatus
    confidence: float
    candidate: str
    reasons: List[str] = field(default_factory=list)
    answer_type: AnswerType = AnswerType.FLAG
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_verified(self) -> bool:
        return self.status in (AnswerStatus.VERIFIED, AnswerStatus.RESOLVED)

    @property
    def is_resolved(self) -> bool:
        return self.status in (AnswerStatus.VERIFIED, AnswerStatus.RESOLVED)


# Backward compatibility alias
FlagVerdict = AnswerVerdict


def infer_expected_answer_type(
    question: str = "",
    description: str = "",
    name: str = "",
    category: str = "",
    flag_pattern: str = ""
) -> Tuple[AnswerType, Optional[str]]:
    """Determine what kind of answer the challenge is actually asking for.

    Returns (expected_type, optional_expected_format_or_pattern).
    """
    combined = f"{question} {description} {name}".lower()

    # Explicit flag pattern requested
    if flag_pattern and ("{" in flag_pattern or "flag" in flag_pattern.lower()):
        return AnswerType.FLAG, flag_pattern

    # Explicit question analysis
    if re.search(r"\b(?:what is the|find the|hidden|admin|account|login)\s+username\b|\bwho is the user\b", combined):
        return AnswerType.USERNAME, None

    if re.search(r"\b(?:what is the|find the|calculate the)\s+(?:sha256|sha1|md5|sha-256|sha-1|hash)\b|\bhash of\b", combined):
        return AnswerType.HASH, None

    if re.search(r"\b(?:what is the|find the|which)\s+(?:port|port number|number of|integer|count)\b|\bhow many\b", combined):
        return AnswerType.NUMBER, None

    if re.search(r"\b(?:what is the|find the)\s+(?:filename|file name|path to the file)\b", combined):
        return AnswerType.FILENAME, None

    if re.search(r"\b(?:secret key|api key|encryption key|access token|auth key)\b", combined):
        return AnswerType.KEY, None

    # CTF default: flags
    cat_lower = (category or "").lower()
    if cat_lower in ["web", "forensics", "crypto", "rev", "reverse", "pwn", "misc", "osint"] or "flag" in combined:
        return AnswerType.FLAG, flag_pattern or None

    return AnswerType.STRING, None


class AnswerResolver:
    """Generic resolver and verifier for answer candidates across all challenge types."""

    def looks_like_flag(self, text: str) -> bool:
        return bool(text) and bool(FLAG_REGEX.search(text)) and not FALSE_FLAG_PATTERNS.search(text)

    def normalize_source(self, source: Any) -> AnswerSource:
        if isinstance(source, AnswerSource):
            return source
        if not source:
            return AnswerSource.UNKNOWN
        s = str(source).lower()
        if s in ("tool_output", "tool", "stdout", "command"):
            return AnswerSource.TOOL_OUTPUT
        if s in ("vision_read", "vision", "gemini_vision"):
            return AnswerSource.VISION_READ
        if s in ("reconstructed_artifact", "artifact", "reconstruction"):
            return AnswerSource.RECONSTRUCTED_ARTIFACT
        if s in ("reconstructed_artifact_ocr", "ocr"):
            return AnswerSource.RECONSTRUCTED_ARTIFACT_OCR
        if s in ("decoded_artifact", "decode"):
            return AnswerSource.DECODED_ARTIFACT
        if s in ("llm", "llm_prose", "llm_reported", "prose", "model"):
            return AnswerSource.LLM_PROSE
        return AnswerSource.UNKNOWN

    def resolve(self, candidate_obj: AnswerCandidate) -> AnswerVerdict:
        """Resolve a full AnswerCandidate against its task_context and evidence."""
        task_ctx = candidate_obj.task_context or {}
        return self.assess(
            candidate_obj.value,
            source=candidate_obj.source,
            command=candidate_obj.provenance.get("command", ""),
            action_succeeded=candidate_obj.provenance.get("action_succeeded", True),
            target_scope=task_ctx.get("target_scope", ""),
            expected_format=task_ctx.get("flag_pattern", ""),
            description=task_ctx.get("description", ""),
            challenge_name=task_ctx.get("challenge_name", ""),
            category=task_ctx.get("category", ""),
            evidence=candidate_obj.evidence,
            worker_id=candidate_obj.worker_id,
        )

    def assess(
        self,
        candidate: str,
        *,
        source: AnswerSource | FlagSource | str = AnswerSource.UNKNOWN,
        command: str = "",
        action_succeeded: bool = True,
        target_scope: str = "",
        expected_format: str = "",
        description: str = "",
        challenge_name: str = "",
        category: str = "",
        evidence: Optional[Dict[str, Any]] = None,
        worker_id: str = "",
    ) -> AnswerVerdict:
        """Assess whether a candidate value resolves the challenge."""
        candidate = (candidate or "").strip()
        reasons: List[str] = []
        evidence = evidence or {}
        src = self.normalize_source(source)

        if not candidate:
            return AnswerVerdict(AnswerStatus.REJECTED, 0.0, candidate,
                                 ["Candidate is empty."], AnswerType.CUSTOM, evidence)

        # ── Echoed / constructed gate ──
        # If the exact value already appears in the command the agent issued, it
        # was generated/echoed by the agent, not captured from the target.
        if command and candidate in command and src in (AnswerSource.LLM_PROSE, AnswerSource.UNKNOWN):
            return AnswerVerdict(AnswerStatus.REJECTED, 0.1, candidate,
                                 ["Value appears in the issued command itself (echoed/constructed, not captured)."],
                                 AnswerType.CUSTOM, evidence)

        # ── Placeholder / False-Positive Gate ──
        if FALSE_FLAG_PATTERNS.search(candidate):
            return AnswerVerdict(AnswerStatus.REJECTED, 0.0, candidate,
                                 ["Matches a placeholder/example shape, not a real answer."],
                                 AnswerType.CUSTOM, evidence)

        # ── Infer Expected Answer Type & Semantic Semantics ──
        expected_type, custom_pattern = infer_expected_answer_type(
            question=description,
            description=description,
            name=challenge_name,
            category=category,
            flag_pattern=expected_format,
        )

        resolved_type = expected_type

        # ── Specific Type Checks ──
        if expected_type == AnswerType.USERNAME:
            # If challenge specifically asks for a username, reject flag envelopes like picoCTF{...}
            if FLAG_REGEX.search(candidate):
                return AnswerVerdict(
                    AnswerStatus.REJECTED, 0.1, candidate,
                    ["Challenge asks for a username, but candidate is formatted as a CTF flag."],
                    AnswerType.FLAG, evidence
                )
            # Username should be an identifier
            if len(candidate) > 64 or " " in candidate:
                return AnswerVerdict(
                    AnswerStatus.CANDIDATE, 0.3, candidate,
                    ["Candidate format is uncertain for a username."],
                    AnswerType.USERNAME, evidence
                )

        elif expected_type == AnswerType.HASH:
            # Hash must match hexadecimal hash length
            clean_hash = candidate.strip().lower()
            if FLAG_REGEX.search(candidate):
                return AnswerVerdict(
                    AnswerStatus.REJECTED, 0.1, candidate,
                    ["Challenge asks for a hash, but candidate is formatted as a CTF flag."],
                    AnswerType.FLAG, evidence
                )
            if not (MD5_REGEX.fullmatch(clean_hash) or SHA1_REGEX.fullmatch(clean_hash) or SHA256_REGEX.fullmatch(clean_hash)):
                return AnswerVerdict(
                    AnswerStatus.CANDIDATE, 0.3, candidate,
                    ["Candidate does not match known hash length/hex format."],
                    AnswerType.HASH, evidence
                )
            candidate = clean_hash

        elif expected_type == AnswerType.NUMBER:
            if not candidate.isdigit():
                return AnswerVerdict(
                    AnswerStatus.CANDIDATE, 0.2, candidate,
                    ["Challenge asks for a numeric answer, but candidate is non-numeric."],
                    AnswerType.NUMBER, evidence
                )

        elif expected_type == AnswerType.FLAG:
            # If standard flag regex matches, extract canonical matched substring
            m = FLAG_REGEX.search(candidate)
            if m:
                candidate = m.group(0)
            elif custom_pattern:
                try:
                    pat = re.compile(custom_pattern.replace("{...}", r"\{[^\}]+\}"), re.IGNORECASE)
                    m2 = pat.search(candidate)
                    if m2:
                        candidate = m2.group(0)
                except Exception:
                    pass

        # ── Evidence Quality & Source Weighting ──
        # High-confidence execution & evidence sources
        is_high_confidence_source = src in (
            AnswerSource.TOOL_OUTPUT,
            AnswerSource.VISION_READ,
            AnswerSource.RECONSTRUCTED_ARTIFACT,
            AnswerSource.RECONSTRUCTED_ARTIFACT_OCR,
            AnswerSource.DECODED_ARTIFACT,
        )

        if is_high_confidence_source:
            if not action_succeeded:
                reasons.append("From execution evidence, but action reported failure — needs corroboration.")
                return AnswerVerdict(AnswerStatus.CANDIDATE, 0.55, candidate, reasons, resolved_type, evidence)

            reasons.append(f"Directly derived from verified execution evidence ({src.value}).")

            # Soft format corroboration if explicit pattern provided
            if expected_format and "{" in candidate:
                prefix = candidate.split("{", 1)[0].lower()
                if prefix and prefix not in expected_format.lower() and "{...}" not in expected_format:
                    reasons.append(f"Prefix '{prefix}' differs from expected format '{expected_format}'.")

            conf = 0.95 if not any(r.startswith("Prefix") for r in reasons) else 0.85
            return AnswerVerdict(AnswerStatus.VERIFIED, conf, candidate, reasons, resolved_type, evidence)

        if src == AnswerSource.LLM_PROSE:
            reasons.append("Asserted in model prose; not verified against direct tool/artifact evidence.")
            return AnswerVerdict(AnswerStatus.CANDIDATE, 0.35, candidate, reasons, resolved_type, evidence)

        reasons.append("Source of candidate is unknown — treated as unverified candidate.")
        return AnswerVerdict(AnswerStatus.CANDIDATE, 0.40, candidate, reasons, resolved_type, evidence)

    def assess_observation(
        self,
        obs: Any,
        *,
        command: str = "",
        action_succeeded: bool = True,
        target_scope: str = "",
        expected_format: str = "",
        description: str = "",
        challenge_name: str = "",
        category: str = ""
    ) -> Optional[AnswerVerdict]:
        """Verify the first answer/flag candidate found on a structured Observation."""
        candidates = getattr(obs, "flag_candidates", None) or []
        if not candidates:
            return None
        return self.assess(
            candidates[0],
            source=AnswerSource.TOOL_OUTPUT,
            command=command,
            action_succeeded=action_succeeded,
            target_scope=target_scope,
            expected_format=expected_format,
            description=description,
            challenge_name=challenge_name,
            category=category,
        )


# Backward compatibility class
FlagVerifier = AnswerResolver
