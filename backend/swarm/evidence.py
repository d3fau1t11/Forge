"""The Evidence Bus (Phase 4 §6).

Agents communicate through *structured evidence*, never by sharing whole
conversations. A specialist publishes what it learned (a service, an endpoint, a
vulnerability, a decoded secret, a flag candidate); the bus deduplicates it,
persists it durably, broadcasts it for observability, and notifies subscribers
(the supervisor) so relevant follow-up work can be scheduled — and so another
specialist can *consume* the lead without re-deriving it.

Persistence uses the durable ``swarm_evidence`` table (no-FK provenance, like the
Phase-1 trajectory layer) so evidence outlives challenge deletion and lets a
mission resume after a crash.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from backend.swarm import events
from backend.swarm.dedup import normalize_command, normalize_text
from backend.swarm.roles import AgentRole, ROLE_PROFILES

logger = logging.getLogger("forge.swarm.evidence")


class EvidenceType(str, Enum):
    SERVICE = "service"
    ENDPOINT = "endpoint"
    TECHNOLOGY = "technology"
    CREDENTIAL = "credential"
    VULNERABILITY = "vulnerability"
    ARTIFACT = "artifact"
    FLAG = "flag"
    NOTE = "note"
    FAILURE = "failure"


@dataclass
class Evidence:
    """One structured discovery published on the bus."""

    mission_id: str
    agent_id: str = ""
    task_id: Optional[str] = None
    evidence_type: str = EvidenceType.NOTE.value
    title: str = ""
    description: str = ""
    source: str = ""                 # command | observation | agent | supervisor
    command: str = ""
    output: str = ""
    artifact_id: Optional[str] = None
    confidence: float = 0.7
    tags: List[str] = field(default_factory=list)
    related_endpoint: str = ""
    related_technology: str = ""
    related_vulnerability: str = ""
    # provenance (set by the bus)
    run_id: Optional[str] = None
    challenge_id: Optional[str] = None
    id: str = ""
    timestamp: str = ""

    def signature(self) -> str:
        """Stable dedup key: type + normalized identifying content."""
        ident = self.related_endpoint or self.related_vulnerability or self.title or self.description
        extra = self.related_technology or normalize_command(self.command)
        return f"{self.evidence_type}::{normalize_text(ident)}::{normalize_text(extra)}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def matches_role(self, role: AgentRole) -> bool:
        """Whether this evidence is a relevant lead for *role* (keyword/type based)."""
        prof = ROLE_PROFILES.get(role)
        if not prof:
            return False
        hay = " ".join([
            self.title, self.description, self.related_endpoint,
            self.related_technology, self.related_vulnerability,
            " ".join(self.tags or []),
        ]).lower()
        return any(kw in hay for kw in prof.keywords)


class EvidenceBus:
    """In-memory pub/sub over durable evidence, scoped to one mission."""

    def __init__(self, mission_id: str, *, run_id: Optional[str] = None,
                 challenge_id: Optional[str] = None, persist: bool = True):
        self.mission_id = mission_id
        self.run_id = run_id
        self.challenge_id = challenge_id
        self.persist = persist
        self._items: List[Evidence] = []
        self._sigs: set = set()
        self._subscribers: List[Callable[[Evidence], None]] = []

    # ------------------------------------------------------------------ #

    def subscribe(self, callback: Callable[[Evidence], None]) -> None:
        self._subscribers.append(callback)

    def publish(self, ev: Evidence) -> Optional[str]:
        """Publish evidence. Returns its id if new, or ``None`` if a duplicate.

        Duplicates (by signature) are dropped — this is the cross-agent half of
        duplicate-work prevention (§8): two specialists that discover the same
        service produce the same signature, so it lands on the bus once.
        """
        ev.mission_id = self.mission_id
        ev.run_id = ev.run_id or self.run_id
        ev.challenge_id = ev.challenge_id or self.challenge_id
        sig = ev.signature()
        if sig in self._sigs:
            return None
        self._sigs.add(sig)
        if not ev.id:
            import uuid
            ev.id = str(uuid.uuid4())
        if not ev.timestamp:
            ev.timestamp = datetime.now(timezone.utc).isoformat()
        self._items.append(ev)

        if self.persist:
            self._persist(ev, sig)

        events.broadcast(events.EVIDENCE_PUBLISHED, {
            "mission_id": self.mission_id, "run_id": self.run_id,
            "challenge_id": self.challenge_id, "evidence_id": ev.id,
            "agent_id": ev.agent_id, "type": ev.evidence_type, "title": ev.title,
            "confidence": ev.confidence, "timestamp": ev.timestamp,
        })

        for cb in list(self._subscribers):
            try:
                cb(ev)
            except Exception as e:  # a subscriber must never break publication
                logger.debug(f"[EvidenceBus] subscriber error: {e}")
        return ev.id

    # ------------------------------------------------------------------ #

    def all(self) -> List[Evidence]:
        return list(self._items)

    def count(self) -> int:
        return len(self._items)

    def by_type(self, evidence_type: str) -> List[Evidence]:
        return [e for e in self._items if e.evidence_type == evidence_type]

    def relevant_for(self, role: AgentRole, *, limit: int = 12) -> List[Evidence]:
        """Evidence that is a relevant lead for *role* — its isolated context slice."""
        hits = [e for e in self._items if e.matches_role(role)]
        # Highest-confidence, most-recent leads first.
        hits.sort(key=lambda e: (e.confidence, e.timestamp), reverse=True)
        return hits[:limit]

    def flag_candidates(self) -> List[Evidence]:
        return self.by_type(EvidenceType.FLAG.value)

    # ------------------------------------------------------------------ #

    def _persist(self, ev: Evidence, sig: str) -> None:
        try:
            from backend.database.session import SessionLocal
            from backend.database.models import SwarmEvidenceModel
            db = SessionLocal()
            try:
                row = SwarmEvidenceModel(
                    id=ev.id, mission_id=self.mission_id, run_id=ev.run_id,
                    challenge_id=ev.challenge_id, agent_id=ev.agent_id, task_id=ev.task_id,
                    evidence_type=ev.evidence_type, title=ev.title[:500],
                    description=ev.description or "", source=ev.source,
                    command=(ev.command or "")[:4000], output=(ev.output or "")[:8000],
                    artifact_id=ev.artifact_id, confidence=float(ev.confidence or 0.0),
                    tags=list(ev.tags or []), related_endpoint=ev.related_endpoint or "",
                    related_technology=ev.related_technology or "",
                    related_vulnerability=ev.related_vulnerability or "", signature=sig,
                )
                db.add(row)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[EvidenceBus] persist skipped: {e}")

    def load(self) -> int:
        """Rehydrate evidence for this mission from the DB (used on resume). Idempotent."""
        try:
            from backend.database.session import SessionLocal
            from backend.database.models import SwarmEvidenceModel
            db = SessionLocal()
            try:
                rows = (db.query(SwarmEvidenceModel)
                        .filter(SwarmEvidenceModel.mission_id == self.mission_id)
                        .order_by(SwarmEvidenceModel.created_at.asc()).all())
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[EvidenceBus] load skipped: {e}")
            return 0
        loaded = 0
        for r in rows:
            ev = Evidence(
                mission_id=r.mission_id, agent_id=r.agent_id or "", task_id=r.task_id,
                evidence_type=r.evidence_type or "note", title=r.title or "",
                description=r.description or "", source=r.source or "", command=r.command or "",
                output=r.output or "", artifact_id=r.artifact_id,
                confidence=r.confidence or 0.0, tags=list(r.tags or []),
                related_endpoint=r.related_endpoint or "", related_technology=r.related_technology or "",
                related_vulnerability=r.related_vulnerability or "", run_id=r.run_id,
                challenge_id=r.challenge_id, id=r.id,
                timestamp=(r.created_at.isoformat() if r.created_at else ""),
            )
            sig = r.signature or ev.signature()
            if sig in self._sigs:
                continue
            self._sigs.add(sig)
            self._items.append(ev)
            loaded += 1
        return loaded
