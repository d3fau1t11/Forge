"""Phase 5 — Adaptive reasoning primitives (facts, hypotheses, failed approaches,
candidate actions, failure classification).

Phase 4.x made FORGE able to *execute* more kinds of actions. Phase 5 makes it
better at *selecting* them. This module is the pure, deterministic, fully
unit-testable core of that reasoning layer — it holds only data types and small
helpers, no LLM, no I/O, no database. The heavier orchestration (generating,
scoring, replanning, stopping) is built on top of these primitives by
``candidates.py`` / ``scoring.py`` / ``progress.py`` and driven by the existing
``Supervisor`` and ``SwarmCoordinator``.

Design rules honoured here (see the Phase 5 spec):
  * §5  Facts, hypotheses and failed approaches are *distinct* — an unverified
        hypothesis never silently becomes permanent knowledge.
  * §6  Evidence carries a *reliability* class (DIRECT/DERIVED/INFERRED/SPECULATIVE),
        so strong evidence is preferred over speculation.
  * §7  Failed approaches are remembered structurally (action, params, target,
        result, reason, class, agent, timestamp) with bounded storage.
  * §8  Actions have a normalized *signature* so essentially-identical actions are
        recognised as duplicates.
  * §9  A ``CandidateAction`` is a structured proposal for the next step.
  * §14 A rich failure taxonomy that the planner can react to.

Nothing here imports ``mission``/``evidence``/``coordinator`` — those depend on this
module, never the reverse — which keeps the dependency graph acyclic and this layer
trivially testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from backend.swarm.dedup import action_signature, normalize_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────── #
# §6 Evidence reliability
# ─────────────────────────────────────────────────────────────────────────── #

class Reliability(str, Enum):
    """How trustworthy a piece of evidence is (strongest → weakest)."""

    DIRECT = "direct"            # observed directly in tool output (HTTP body contained "admin")
    DERIVED = "derived"          # produced by a tool/parse step (crawler discovered /admin)
    INFERRED = "inferred"        # reasoned from other facts (probably Flask)
    SPECULATIVE = "speculative"  # a guess / prose suggestion (maybe SSTI)

    @property
    def weight(self) -> float:
        return _RELIABILITY_WEIGHT[self]


_RELIABILITY_WEIGHT: Dict["Reliability", float] = {
    Reliability.DIRECT: 1.0,
    Reliability.DERIVED: 0.75,
    Reliability.INFERRED: 0.5,
    Reliability.SPECULATIVE: 0.25,
}

# Reliability floor below which a statement is treated as a hypothesis, not a fact.
FACT_RELIABILITY_FLOOR = Reliability.DERIVED


def classify_reliability(*, source: str = "", evidence_type: str = "",
                         confidence: float = 0.7, tags: Optional[List[str]] = None) -> Reliability:
    """Deterministically classify how reliable a discovery is (§6).

    Rules (conservative — when unsure we under-claim, never over-claim):
      * evidence pulled straight from tool STDOUT/STDERR         → DIRECT
      * evidence a tool/parser *derived* (an agent observation)  → DERIVED
      * something the supervisor/planner *inferred*              → INFERRED
      * an LLM prose *suggestion* or an explicitly speculative
        note, or anything with very low confidence              → SPECULATIVE
    """
    src = (source or "").strip().lower()
    tags = [str(t).lower() for t in (tags or [])]
    conf = float(confidence or 0.0)

    if "speculative" in tags or "candidate" in tags or src in ("llm", "llm_prose", "prose", "suggestion"):
        return Reliability.SPECULATIVE
    if src in ("command", "tool", "tool_output", "stdout", "output"):
        return Reliability.DIRECT
    if src in ("observation", "agent", "parser", "crawler", "decoder"):
        # A derived observation with a strong confidence is treated as direct-enough.
        return Reliability.DERIVED if conf < 0.9 else Reliability.DIRECT
    if src in ("supervisor", "planner", "inference", "reasoning"):
        return Reliability.INFERRED
    # Unknown provenance: let confidence decide, but never above DERIVED.
    if conf >= 0.85:
        return Reliability.DERIVED
    if conf >= 0.5:
        return Reliability.INFERRED
    return Reliability.SPECULATIVE


# ─────────────────────────────────────────────────────────────────────────── #
# §5 Facts / hypotheses / failed approaches
# ─────────────────────────────────────────────────────────────────────────── #

class HypothesisStatus(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass
class Fact:
    """A CONFIRMED piece of knowledge (§5). Only strong evidence becomes a fact."""

    statement: str
    reliability: str = Reliability.DERIVED.value
    confidence: float = 0.8
    source: str = ""
    evidence_id: str = ""
    agent: str = ""
    created_at: str = field(default_factory=_now)

    def key(self) -> str:
        return normalize_text(self.statement)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Fact":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class Hypothesis:
    """An UNVERIFIED possibility (§5). Must be validated before it can be a fact."""

    statement: str
    status: str = HypothesisStatus.OPEN.value
    confidence: float = 0.4
    reliability: str = Reliability.SPECULATIVE.value
    rationale: str = ""
    supporting_evidence: List[str] = field(default_factory=list)
    refuting_evidence: List[str] = field(default_factory=list)
    agent: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def key(self) -> str:
        return normalize_text(self.statement)

    @property
    def is_open(self) -> bool:
        return self.status == HypothesisStatus.OPEN.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Hypothesis":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class FailedApproach:
    """A remembered failure for the CURRENT mission (§7).

    The planner consults these so an agent does not blindly repeat an action that
    already failed unless *new evidence* justifies a retry.
    """

    action: str                       # human label, e.g. "GET /admin" or the task objective
    signature: str = ""               # normalized action signature (capability::target::params)
    capability: str = ""
    target: str = ""
    params: str = ""
    result: str = ""                  # short result summary, e.g. "404" / "NO_RESULT"
    reason: str = ""
    failure_class: str = ""           # a FailureClass value
    agent: str = ""
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FailedApproach":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────────────────── #
# §14 Failure taxonomy
# ─────────────────────────────────────────────────────────────────────────── #

class FailureClass(str, Enum):
    """A richer failure taxonomy than raw status codes, so replanning can react (§14)."""

    TOOL_FAILURE = "TOOL_FAILURE"
    CAPABILITY_FAILURE = "CAPABILITY_FAILURE"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    AUTH_FAILURE = "AUTH_FAILURE"
    INVALID_INPUT = "INVALID_INPUT"
    NO_RESULT = "NO_RESULT"
    PARTIAL_RESULT = "PARTIAL_RESULT"
    TIMEOUT = "TIMEOUT"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    STRATEGY_FAILURE = "STRATEGY_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    CANCELLED = "CANCELLED"


# How each failure class should steer planning (advisory; the coordinator maps these
# onto the existing retry/reassign/abandon recovery actions).
class RecoveryHint(str, Enum):
    RETRY = "retry"                      # transient — try again
    ALTERNATIVE_PROVIDER = "alt_provider"  # swap LLM/tool provider
    ALTERNATIVE_METHOD = "alt_method"    # accomplish it a different way
    RECOVER_CAPABILITY = "recover_capability"
    FIX_TARGET = "fix_target"            # the target info is wrong
    REPLAN = "replan"                    # the whole approach is wrong — change strategy
    ABANDON = "abandon"                  # nothing to be done — record a dead end


_FAILURE_RECOVERY: Dict[FailureClass, RecoveryHint] = {
    FailureClass.TOOL_FAILURE: RecoveryHint.ALTERNATIVE_METHOD,
    FailureClass.CAPABILITY_FAILURE: RecoveryHint.RECOVER_CAPABILITY,
    FailureClass.TARGET_MISMATCH: RecoveryHint.FIX_TARGET,
    FailureClass.NETWORK_FAILURE: RecoveryHint.RETRY,
    FailureClass.AUTH_FAILURE: RecoveryHint.ALTERNATIVE_METHOD,
    FailureClass.INVALID_INPUT: RecoveryHint.ALTERNATIVE_METHOD,
    FailureClass.NO_RESULT: RecoveryHint.REPLAN,
    FailureClass.PARTIAL_RESULT: RecoveryHint.RETRY,
    FailureClass.TIMEOUT: RecoveryHint.RETRY,
    FailureClass.ENVIRONMENT_FAILURE: RecoveryHint.ALTERNATIVE_METHOD,
    FailureClass.STRATEGY_FAILURE: RecoveryHint.REPLAN,
    FailureClass.PROVIDER_FAILURE: RecoveryHint.ALTERNATIVE_PROVIDER,
    FailureClass.CANCELLED: RecoveryHint.ABANDON,
}


def recovery_hint_for(failure_class: FailureClass) -> RecoveryHint:
    return _FAILURE_RECOVERY.get(failure_class, RecoveryHint.ABANDON)


def classify_failure(result: Any) -> FailureClass:
    """Map an agent/task result to a :class:`FailureClass` (§14).

    Reuses the same signals the Phase 4.x ``Supervisor.classify_failure`` reads
    (``status`` / ``failure_category`` / ``reason``) but returns the richer Phase 5
    taxonomy. Kept deterministic and side-effect free.
    """
    status = (getattr(result, "status", "") or "").upper()
    cat = (getattr(result, "failure_category", "") or "").upper()
    reason = (getattr(result, "reason", "") or "").lower()

    if status == "CANCELLED":
        return FailureClass.CANCELLED
    if cat == "BLOCKED_CAPABILITY" or "blocked_capability" in reason:
        return FailureClass.CAPABILITY_FAILURE
    if cat == "TARGET_MISMATCH" or "target_mismatch" in reason:
        return FailureClass.TARGET_MISMATCH
    if status == "TIMEOUT" or cat == "TIMEOUT" or "timed out" in reason or "timeout" in reason:
        return FailureClass.TIMEOUT
    if cat in ("COMMAND_NOT_FOUND", "MISSING_TOOL", "MISSING_DEPENDENCY"):
        return FailureClass.TOOL_FAILURE
    if cat == "ENVIRONMENT" or "environment" in reason:
        return FailureClass.ENVIRONMENT_FAILURE
    if cat == "NETWORK" or "network" in reason or "connection" in reason or "refused" in reason:
        return FailureClass.NETWORK_FAILURE
    if cat in ("AUTH", "AUTHENTICATION", "PERMISSION") or "unauthor" in reason or \
       "forbidden" in reason or "permission" in reason or "denied" in reason or "401" in reason or "403" in reason:
        return FailureClass.AUTH_FAILURE
    if cat in ("INVALID_INPUT", "BAD_INPUT") or "invalid" in reason or "malformed" in reason:
        return FailureClass.INVALID_INPUT
    if "provider" in reason or "exhausted" in reason or "quota" in reason:
        return FailureClass.PROVIDER_FAILURE
    if status in ("MAX_TURNS",) or cat == "NO_PROGRESS" or "no_progress" in reason or "no progress" in reason:
        return FailureClass.STRATEGY_FAILURE
    if cat == "PARTIAL_RESULT" or "partial" in reason:
        return FailureClass.PARTIAL_RESULT
    if cat == "NO_RESULT" or "no result" in reason or "empty" in reason or "nothing" in reason:
        return FailureClass.NO_RESULT
    # A generic failure with no distinguishing signal → treat as "produced nothing useful".
    return FailureClass.NO_RESULT


# ─────────────────────────────────────────────────────────────────────────── #
# §9 Candidate actions
# ─────────────────────────────────────────────────────────────────────────── #

class InformationGain(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def value_float(self) -> float:
        return {"high": 1.0, "medium": 0.6, "low": 0.25}[self.value]


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def value_float(self) -> float:
        return {"low": 0.1, "medium": 0.4, "high": 0.8}[self.value]


class Cost(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def value_float(self) -> float:
        return {"low": 0.15, "medium": 0.45, "high": 0.85}[self.value]


@dataclass
class CandidateAction:
    """A structured proposal for the next step (§9).

    Fields are advisory — not every one must be populated. The scorer
    (``scoring.ActionScorer``) reads the numeric/enum fields; the coordinator turns a
    selected candidate into a concrete :class:`~backend.swarm.tasks.Task`.
    """

    action_type: str                       # canonical action, e.g. "directory_enum"
    objective: str                         # concrete objective text for the specialist
    role: str = "recon"                    # AgentRole value that would carry it out
    capability: str = ""                   # capability it needs (for the pre-dispatch gate)
    rationale: str = ""                    # WHY this action, in plain language (§37)
    required_evidence: List[str] = field(default_factory=list)  # what must already be known
    target: str = ""
    params: str = ""
    target_type: str = ""                  # a TargetType value the action expects (optional)

    # Scoring inputs (enum-labelled so they are inspectable; §10/§11).
    information_gain: str = InformationGain.MEDIUM.value
    risk: str = Risk.LOW.value
    cost: str = Cost.LOW.value
    success_probability: float = 0.5       # 0..1
    evidence_support: float = 0.3          # 0..1, how strongly current evidence backs it
    novelty: float = 1.0                   # 0..1, 1 = never attempted, decays with duplicates

    source: str = "reasoning"              # reasoning | evidence | memory | playbook | plan
    dependencies: List[str] = field(default_factory=list)
    priority: int = 50                     # derived from the score by the scorer
    signature: str = ""                    # normalized action signature (dedup, §8)

    # Filled by the scorer — inspectable breakdown for observability/tests (§10, §37).
    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    # Phase 6 §13 — WHY learned knowledge influenced this candidate: source, the number
    # of similar past challenges, historical & contextual success rates, memory ids.
    # Advisory/observability only — the scorer never reads it.
    historical_support: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.signature:
            self.signature = action_signature(self.capability or self.action_type,
                                              self.target, self.params)

    # -- convenience ---------------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_task(self, *, mission_id: str, run_id: Optional[str] = None,
                challenge_id: Optional[str] = None,
                dependencies: Optional[List[str]] = None) -> Any:
        """Materialise this candidate as a schedulable :class:`Task`.

        Imported lazily so this pure module never forces the (heavier) tasks module
        to load in a context that only needs the data types.
        """
        from backend.swarm.tasks import Task
        return Task(
            mission_id=mission_id, run_id=run_id, challenge_id=challenge_id,
            role=self.role, objective=self.objective, priority=int(self.priority),
            dependencies=list(dependencies if dependencies is not None else self.dependencies),
            required_capabilities=[self.capability] if self.capability else [],
            target_type=self.target_type or "",
        )


# The canonical action profiles Phase 5 reasons about. These are *reasoning defaults*
# (deterministic policy), NOT hardcoded challenge/demo data — they express, for each
# kind of action, its typical information gain / cost / risk and which specialist and
# capability it maps to. The generator adapts them to the current evidence.
@dataclass(frozen=True)
class ActionProfile:
    action_type: str
    role: str
    capability: str
    information_gain: str
    cost: str
    risk: str
    base_success: float
    # If set, the action is only meaningful once these knowledge gaps are still open
    # ("surface" = attack surface/endpoints unknown, "service" = services/versions
    # unknown, "vuln" = a vulnerability is known but not yet exploited, etc.).
    reduces_uncertainty_about: str = ""


ACTION_PROFILES: Dict[str, ActionProfile] = {
    "service_fingerprint": ActionProfile(
        "service_fingerprint", "recon", "service_enumeration",
        InformationGain.HIGH.value, Cost.LOW.value, Risk.LOW.value, 0.7, "service"),
    "port_scan": ActionProfile(
        "port_scan", "recon", "port_scan",
        InformationGain.HIGH.value, Cost.LOW.value, Risk.LOW.value, 0.7, "service"),
    "directory_enum": ActionProfile(
        "directory_enum", "web", "directory_enum",
        InformationGain.HIGH.value, Cost.LOW.value, Risk.LOW.value, 0.6, "surface"),
    "parameter_discovery": ActionProfile(
        "parameter_discovery", "web", "parameter_discovery",
        InformationGain.MEDIUM.value, Cost.MEDIUM.value, Risk.LOW.value, 0.5, "surface"),
    "http_inspect": ActionProfile(
        "http_inspect", "web", "http_inspect",
        InformationGain.HIGH.value, Cost.LOW.value, Risk.LOW.value, 0.7, "surface"),
    "auth_test": ActionProfile(
        "auth_test", "web", "auth_test",
        InformationGain.MEDIUM.value, Cost.MEDIUM.value, Risk.MEDIUM.value, 0.45),
    "web_exploit": ActionProfile(
        "web_exploit", "web", "web_exploit",
        InformationGain.MEDIUM.value, Cost.MEDIUM.value, Risk.MEDIUM.value, 0.5, "vuln"),
    "vuln_research": ActionProfile(
        "vuln_research", "web", "",
        InformationGain.MEDIUM.value, Cost.LOW.value, Risk.LOW.value, 0.55, "vuln"),
    "vuln_exploit": ActionProfile(
        "vuln_exploit", "web", "web_exploit",
        InformationGain.MEDIUM.value, Cost.MEDIUM.value, Risk.HIGH.value, 0.55, "vuln"),
    "credential_bruteforce": ActionProfile(
        "credential_bruteforce", "web", "auth_test",
        InformationGain.LOW.value, Cost.HIGH.value, Risk.HIGH.value, 0.3),
    "artifact_analysis": ActionProfile(
        "artifact_analysis", "forensics", "file_inspect",
        InformationGain.HIGH.value, Cost.LOW.value, Risk.LOW.value, 0.6, "artifact"),
    "decode": ActionProfile(
        "decode", "crypto", "encoding",
        InformationGain.HIGH.value, Cost.LOW.value, Risk.LOW.value, 0.65, "artifact"),
    "binary_analysis": ActionProfile(
        "binary_analysis", "pwn", "binary_analysis",
        InformationGain.HIGH.value, Cost.MEDIUM.value, Risk.LOW.value, 0.55, "artifact"),
    "reverse_analysis": ActionProfile(
        "reverse_analysis", "rev", "static_analysis",
        InformationGain.HIGH.value, Cost.MEDIUM.value, Risk.LOW.value, 0.55, "artifact"),
    "interactive_probe": ActionProfile(
        "interactive_probe", "pwn", "",
        InformationGain.MEDIUM.value, Cost.MEDIUM.value, Risk.MEDIUM.value, 0.5),
    "ocr_extract": ActionProfile(
        "ocr_extract", "forensics", "ocr",
        InformationGain.MEDIUM.value, Cost.LOW.value, Risk.LOW.value, 0.5, "artifact"),
    "generic": ActionProfile(
        "generic", "recon", "",
        InformationGain.MEDIUM.value, Cost.MEDIUM.value, Risk.MEDIUM.value, 0.4),
}


def profile_for(action_type: str) -> ActionProfile:
    return ACTION_PROFILES.get(action_type, ACTION_PROFILES["generic"])
