"""Shared Mission State (Phase 4 §7).

The central, shared, persistent aggregate of everything the swarm has learned —
distinct from each agent's *isolated* per-session ``MissionState``. Specialist
agents keep their own trajectories private; what they discover is promoted here
(via the Evidence Bus) so the whole team benefits without any agent receiving
another agent's entire conversation.

Persisted to the durable ``swarm_missions`` table so a mission resumes with all
aggregated knowledge intact.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _add_unique(items: List[str], value: Optional[str]) -> bool:
    v = (value or "").strip()
    if v and v not in items:
        items.append(v)
        return True
    return False


@dataclass
class SharedMissionState:
    """Serializable, resumable source of truth for a coordinated mission."""

    mission_id: str = ""
    run_id: Optional[str] = None
    challenge_id: Optional[str] = None

    # ── Challenge / target ──────────────────────────────────────────────────
    challenge_name: str = ""
    category: str = ""
    difficulty: str = ""
    platform: str = ""
    description: str = ""
    flag_format: str = ""
    target: str = ""
    scope: List[str] = field(default_factory=list)

    # ── Aggregated knowledge (promoted from agents via evidence) ────────────
    endpoints: List[str] = field(default_factory=list)
    technologies: List[str] = field(default_factory=list)
    services: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    credentials: List[str] = field(default_factory=list)
    cookies: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    vulnerabilities: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)

    # ── Technique bookkeeping ───────────────────────────────────────────────
    attempted_techniques: List[str] = field(default_factory=list)
    successful_techniques: List[str] = field(default_factory=list)
    failed_techniques: List[str] = field(default_factory=list)
    dead_ends: List[str] = field(default_factory=list)
    attempted_signatures: List[str] = field(default_factory=list)  # normalized cmd/task dedup

    # ── Flags ───────────────────────────────────────────────────────────────
    flag_candidates: List[str] = field(default_factory=list)
    verified_flag: Optional[str] = None

    # ── Coordination ────────────────────────────────────────────────────────
    strategy: str = ""
    phase: str = "recon"
    progress: int = 0
    status: str = "PLANNING"                       # PLANNING|RUNNING|PAUSED|COMPLETED|FAILED|CANCELLED
    agent_statuses: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # agent_id -> {...}
    evidence_ids: List[str] = field(default_factory=list)
    updated_at: str = ""

    # ------------------------------------------------------------------ #
    # Evidence integration (the only path knowledge enters shared state)
    # ------------------------------------------------------------------ #

    def integrate_evidence(self, ev: Any) -> None:
        """Merge one :class:`~backend.swarm.evidence.Evidence` into shared knowledge."""
        etype = getattr(ev, "evidence_type", "note")
        title = getattr(ev, "title", "") or ""
        desc = getattr(ev, "description", "") or ""
        label = (title or desc).strip()

        if getattr(ev, "id", ""):
            _add_unique(self.evidence_ids, ev.id)

        if etype == "endpoint":
            _add_unique(self.endpoints, getattr(ev, "related_endpoint", "") or label)
        elif etype == "service":
            _add_unique(self.services, label)
        elif etype == "technology":
            _add_unique(self.technologies, getattr(ev, "related_technology", "") or label)
        elif etype == "credential":
            _add_unique(self.credentials, label)
        elif etype == "vulnerability":
            _add_unique(self.vulnerabilities, getattr(ev, "related_vulnerability", "") or label)
        elif etype == "artifact":
            _add_unique(self.artifacts, getattr(ev, "artifact_id", None) or label)
        elif etype == "flag":
            _add_unique(self.flag_candidates, label)
        elif etype == "failure":
            _add_unique(self.failed_techniques, label)

        # Cross-reference fields can carry a lead regardless of the primary type.
        _add_unique(self.endpoints, getattr(ev, "related_endpoint", ""))
        _add_unique(self.technologies, getattr(ev, "related_technology", ""))
        _add_unique(self.vulnerabilities, getattr(ev, "related_vulnerability", ""))
        self._touch()

    # ── Explicit coordination mutators ──────────────────────────────── #

    def note_attempt(self, signature: str) -> None:
        _add_unique(self.attempted_signatures, signature)
        self._touch()

    def record_dead_end(self, note: str) -> None:
        _add_unique(self.dead_ends, note)
        self._touch()

    def set_agent_status(self, agent_id: str, **fields: Any) -> None:
        cur = self.agent_statuses.get(agent_id, {})
        cur.update(fields)
        self.agent_statuses[agent_id] = cur
        self._touch()

    def set_verified_flag(self, flag: str) -> None:
        self.verified_flag = flag
        _add_unique(self.flag_candidates, flag)
        self.progress = 100
        self.status = "COMPLETED"
        self._touch()

    def recompute_progress(self) -> None:
        if self.verified_flag:
            self.progress = 100
            return
        score = 0
        if self.endpoints:
            score += 15
        if self.technologies:
            score += 10
        if self.services:
            score += 10
        if self.vulnerabilities:
            score += 25
        if self.credentials:
            score += 15
        if self.flag_candidates:
            score += 20
        self.progress = max(self.progress, min(score, 95))

    # ── Bounded summary for prompts / API ────────────────────────────── #

    def summary(self, max_items: int = 8) -> str:
        def _fmt(name: str, items: List[str]) -> str:
            if not items:
                return ""
            shown = items[:max_items]
            more = f" (+{len(items) - len(shown)} more)" if len(items) > len(shown) else ""
            return f"{name}: {', '.join(shown)}{more}"

        lines = [
            f"Target: {self.target or 'n/a'} | category={self.category or 'n/a'} | phase={self.phase}",
        ]
        for name, items in (
            ("Endpoints", self.endpoints), ("Technologies", self.technologies),
            ("Services", self.services), ("Vulnerabilities", self.vulnerabilities),
            ("Credentials", self.credentials), ("Artifacts", self.artifacts),
            ("Dead ends", self.dead_ends), ("Flag candidates", self.flag_candidates),
        ):
            row = _fmt(name, items)
            if row:
                lines.append(row)
        if self.verified_flag:
            lines.append(f"VERIFIED FLAG: {self.verified_flag}")
        return "\n".join(lines)

    # ── Serialization / persistence ──────────────────────────────────── #

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SharedMissionState":
        data = data or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, *, coord_session_id: Optional[str] = None) -> None:
        try:
            from backend.database.session import SessionLocal
            from backend.database.models import SwarmMissionModel
            self.recompute_progress()
            db = SessionLocal()
            try:
                row = db.query(SwarmMissionModel).filter(SwarmMissionModel.id == self.mission_id).first()
                if row is None:
                    row = SwarmMissionModel(id=self.mission_id)
                    db.add(row)
                row.run_id = self.run_id
                row.challenge_id = self.challenge_id
                if coord_session_id:
                    row.coord_session_id = coord_session_id
                row.status = self.status
                row.strategy = self.strategy or ""
                row.progress = int(self.progress)
                row.shared_state = self.to_dict()
                row.verified_flag = self.verified_flag
                if self.status in ("COMPLETED", "FAILED", "CANCELLED"):
                    row.completed_at = datetime.utcnow()
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    @classmethod
    def load(cls, mission_id: str) -> Optional["SharedMissionState"]:
        try:
            from backend.database.session import SessionLocal
            from backend.database.models import SwarmMissionModel
            db = SessionLocal()
            try:
                row = db.query(SwarmMissionModel).filter(SwarmMissionModel.id == mission_id).first()
                if not row:
                    return None
                state = cls.from_dict(row.shared_state or {})
                state.mission_id = row.id
                state.run_id = row.run_id
                state.challenge_id = row.challenge_id
                state.status = row.status or state.status
                state.verified_flag = row.verified_flag or state.verified_flag
                return state
            finally:
                db.close()
        except Exception:
            return None

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
