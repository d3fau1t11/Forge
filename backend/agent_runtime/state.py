"""
FORGE Agent Runtime — structured mission state.

`MissionState` is the serializable, provider-agnostic "brain" of a mission. It is
owned entirely by FORGE (never by the model or the external CLI), so the mission
survives a provider swap, a process crash, or a pause/resume. Everything the
runtime knows about the target lives here and is JSON-round-trippable.

Design notes
------------
* Collections that must de-duplicate (endpoints, technologies, …) are stored as
  ordered-unique lists rather than sets so that (a) JSON serialization is trivial
  and (b) discovery order is preserved for readable prompts.
* `apply_observation()` is the ONE place state mutates from evidence. It returns a
  compact :class:`StateDelta` describing exactly what changed — that delta is what
  gets persisted on the trajectory event and streamed to the UI, and it is also
  the signal the runtime uses to detect a no-progress condition.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _add_unique(bucket: List[Any], value: Any) -> bool:
    """Append *value* to *bucket* only if absent. Returns True if it was new."""
    if value is None:
        return False
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return False
    if value in bucket:
        return False
    bucket.append(value)
    return True


@dataclass
class StateDelta:
    """A compact record of what changed in MissionState during one turn."""
    new_endpoints: List[str] = field(default_factory=list)
    new_services: List[str] = field(default_factory=list)
    new_technologies: List[str] = field(default_factory=list)
    new_files: List[str] = field(default_factory=list)
    new_credentials: List[str] = field(default_factory=list)
    new_vulnerabilities: List[str] = field(default_factory=list)
    new_flag_candidates: List[str] = field(default_factory=list)
    phase_changed_to: Optional[str] = None
    objective_changed_to: Optional[str] = None
    verified_flag: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True when nothing materially new was learned this turn (no-progress signal)."""
        return not any([
            self.new_endpoints, self.new_services, self.new_technologies,
            self.new_files, self.new_credentials, self.new_vulnerabilities,
            self.new_flag_candidates, self.phase_changed_to,
            self.objective_changed_to, self.verified_flag, self.notes,
        ])

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class MissionState:
    """Complete, serializable mission state — the resumable source of truth."""

    # ── Target & scope ──────────────────────────────────────────────────────
    target: str = ""
    scope: List[str] = field(default_factory=list)     # split on '+' per FORGE convention

    # ── Challenge metadata ──────────────────────────────────────────────────
    challenge_name: str = ""
    category: str = ""
    difficulty: str = ""
    platform: str = ""
    description: str = ""
    flag_format: str = ""

    # ── Phase / objective / progress ────────────────────────────────────────
    phase: str = "recon"                               # recon | analysis | exploit | escalate | exfil
    current_objective: str = ""
    progress: int = 0                                  # 0..100 (heuristic)

    # ── Discovered knowledge ────────────────────────────────────────────────
    known_endpoints: List[str] = field(default_factory=list)
    known_files: List[str] = field(default_factory=list)
    file_provenance: Dict[str, str] = field(default_factory=dict)
    interactive_sessions: List[str] = field(default_factory=list)
    known_services: List[str] = field(default_factory=list)
    technologies: List[str] = field(default_factory=list)
    credentials: List[str] = field(default_factory=list)
    cookies: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    vulnerabilities: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)

    # ── Attempt bookkeeping ─────────────────────────────────────────────────
    commands_attempted: List[str] = field(default_factory=list)
    successful_techniques: List[str] = field(default_factory=list)
    failed_techniques: List[str] = field(default_factory=list)   # ["approach :: reason"]
    dead_ends: List[str] = field(default_factory=list)
    current_hypotheses: List[str] = field(default_factory=list)

    # ── Flag lifecycle ──────────────────────────────────────────────────────
    flag_candidates: List[str] = field(default_factory=list)
    verified_flag: Optional[str] = None

    # ── Latest signal ───────────────────────────────────────────────────────
    last_meaningful_observation: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------ #
    # Mutation from evidence (the only path state changes from output)
    # ------------------------------------------------------------------ #

    def apply_observation(self, obs: "Any") -> StateDelta:
        """Merge a structured Observation into the state and return the delta.

        `obs` is duck-typed (an :class:`~backend.agent_runtime.observation.Observation`
        or any object exposing the same attributes) so this module never imports the
        observation engine (keeps the dependency graph acyclic).
        """
        delta = StateDelta()

        for ep in getattr(obs, "new_endpoints", []) or []:
            if _add_unique(self.known_endpoints, ep):
                delta.new_endpoints.append(ep)
        for svc in getattr(obs, "new_services", []) or []:
            if _add_unique(self.known_services, svc):
                delta.new_services.append(svc)
        for tech in getattr(obs, "new_technologies", []) or []:
            if _add_unique(self.technologies, tech):
                delta.new_technologies.append(tech)
        for f in getattr(obs, "new_files", []) or []:
            if _add_unique(self.known_files, f):
                delta.new_files.append(f)
        for f, prov in (getattr(obs, "file_provenance", {}) or {}).items():
            self.file_provenance[f] = prov
        for sess in getattr(obs, "interactive_sessions", []) or []:
            if _add_unique(self.interactive_sessions, sess):
                delta.notes.append(f"session:{sess}")
        for cred in getattr(obs, "new_credentials", []) or []:
            if _add_unique(self.credentials, cred):
                delta.new_credentials.append(cred)
        for vuln in getattr(obs, "new_vulnerabilities", []) or []:
            if _add_unique(self.vulnerabilities, vuln):
                delta.new_vulnerabilities.append(vuln)
        for cand in getattr(obs, "flag_candidates", []) or []:
            if _add_unique(self.flag_candidates, cand):
                delta.new_flag_candidates.append(cand)

        # Cookies / headers are dict merges (last write wins, but only records novelty).
        for k, v in (getattr(obs, "new_cookies", {}) or {}).items():
            if self.cookies.get(k) != v:
                self.cookies[k] = v
                delta.notes.append(f"cookie:{k}")
        for k, v in (getattr(obs, "new_headers", {}) or {}).items():
            if self.headers.get(k) != v:
                self.headers[k] = v
                delta.notes.append(f"header:{k}")

        summary = (getattr(obs, "summary", "") or "").strip()
        if summary and getattr(obs, "novelty", False):
            self.last_meaningful_observation = summary

        self._touch()
        self._recompute_progress()
        return delta

    # ------------------------------------------------------------------ #
    # Explicit mutators (used by the runtime for decision/planning bookkeeping)
    # ------------------------------------------------------------------ #

    def record_command(self, command: str) -> None:
        if command:
            _add_unique(self.commands_attempted, command.strip())
            self._touch()

    def record_success(self, technique: str) -> None:
        if technique:
            _add_unique(self.successful_techniques, technique.strip())
            self._touch()

    def record_failure(self, approach: str, reason: str = "") -> None:
        entry = f"{approach.strip()} :: {reason.strip()}" if reason else approach.strip()
        if entry:
            _add_unique(self.failed_techniques, entry)
            self._touch()

    def record_dead_end(self, note: str) -> None:
        if note:
            _add_unique(self.dead_ends, note.strip())
            self._touch()

    def set_phase(self, phase: str) -> Optional[str]:
        if phase and phase != self.phase:
            self.phase = phase
            self._touch()
            return phase
        return None

    def set_objective(self, objective: str) -> Optional[str]:
        if objective and objective != self.current_objective:
            self.current_objective = objective
            self._touch()
            return objective
        return None

    def set_verified_flag(self, flag: str) -> None:
        self.verified_flag = flag
        _add_unique(self.flag_candidates, flag)
        self.progress = 100
        self._touch()

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "MissionState":
        data = data or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def _recompute_progress(self) -> None:
        """Heuristic progress so the UI has a monotonic-ish signal (never regresses far)."""
        if self.verified_flag:
            self.progress = 100
            return
        score = 0
        if self.known_endpoints:
            score += 15
        if self.technologies:
            score += 10
        if self.known_services:
            score += 10
        if self.vulnerabilities:
            score += 25
        if self.credentials:
            score += 15
        if self.flag_candidates:
            score += 20
        self.progress = max(self.progress, min(score, 95))
