"""Durable event names + broadcaster for the swarm layer.

Phase 4 observability (§13). Every swarm event is FIRST persisted to the existing
``trajectory_events`` table (reusing ``agent_runtime.trajectory.trajectory_store``)
so a dashboard that was disconnected or crashed at emit time can still reconstruct
the mission lifecycle. Only after that write succeeds is the event forwarded to the
existing fire-and-forget WebSocket broadcaster — so the two never silently diverge.

The WS half mirrors the fire-and-forget pattern used by
``backend.agent_runtime.trajectory._broadcast``: if an event loop is running the
broadcast is scheduled as a task; otherwise (sync/CLI/test) it is skipped silently.
The persistence half, however, is synchronous and runs in every context.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from backend.agent_runtime import trajectory_store

logger = logging.getLogger("forge.swarm.events")

# ── Event names (all prefixed SWARM_ so the frontend can filter one family) ──── #
MISSION_STARTED = "SWARM_MISSION_STARTED"
MISSION_COMPLETE = "SWARM_MISSION_COMPLETE"
MISSION_FAILED = "SWARM_MISSION_FAILED"
STATE_CHANGED = "SWARM_STATE_CHANGED"
TASK_CREATED = "SWARM_TASK_CREATED"
TASK_ASSIGNED = "SWARM_TASK_ASSIGNED"
TASK_STARTED = "SWARM_TASK_STARTED"
TASK_COMPLETED = "SWARM_TASK_COMPLETED"
TASK_FAILED = "SWARM_TASK_FAILED"
TASK_CANCELLED = "SWARM_TASK_CANCELLED"
TASK_REASSIGNED = "SWARM_TASK_REASSIGNED"
AGENT_STATUS = "SWARM_AGENT_STATUS"
EVIDENCE_PUBLISHED = "SWARM_EVIDENCE_PUBLISHED"
FLAG_CANDIDATE = "SWARM_FLAG_CANDIDATE"
FLAG_VERIFIED = "SWARM_FLAG_VERIFIED"
# Phase 5 §37 — the reasoning decision (what to try next & why), for observability.
REASONING_DECISION = "SWARM_REASONING_DECISION"
REPLAN = "SWARM_REPLAN"
MISSION_STOP = "SWARM_MISSION_STOP"
# Phase 7 (STEP 2/5) — the authoritative target changed on resume; stale target-
# derived state was invalidated so nothing executes against the old target.
TARGET_CHANGED = "SWARM_TARGET_CHANGED"


def _resolve_provenance(
    payload: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(session_id, run_id, challenge_id)`` for one swarm event.

    Explicit payload fields always win. When the caller's (WS-shaped) payload omitted
    provenance we fall back to the durable mission row keyed by ``mission_id`` — the
    same mission the coordinator already persists — so mission-level events stay
    groupable by challenge/run regardless of which fields a particular call site sends.
    Best-effort: a lookup failure is never fatal and simply leaves the value unset.
    """
    session_id = payload.get("session_id")
    run_id = payload.get("run_id")
    challenge_id = payload.get("challenge_id")
    mission_id = payload.get("mission_id")
    if mission_id and (not session_id or not run_id or not challenge_id):
        try:
            from backend.database.session import SessionLocal
            from backend.database.models import SwarmMissionModel

            db = SessionLocal()
            try:
                row = (db.query(SwarmMissionModel)
                       .filter(SwarmMissionModel.id == mission_id).first())
                if row is not None:
                    session_id = session_id or row.coord_session_id
                    run_id = run_id or row.run_id
                    challenge_id = challenge_id or row.challenge_id
            finally:
                db.close()
        except Exception as e:  # pragma: no cover - provenance is best-effort
            logger.debug(f"[swarm.events] provenance lookup failed for {mission_id}: {e}")
    return session_id, run_id, challenge_id


def _persist(event: str, payload: Dict[str, Any]) -> bool:
    """Durably record one swarm event; return True iff the row was committed.

    Runs synchronously *before* the WebSocket broadcast so an event is never sent
    without first being durable. Never raises: a persistence failure is logged and
    returns False so the caller skips the broadcast and the two cannot diverge.
    """
    try:
        session_id, run_id, challenge_id = _resolve_provenance(payload)
        session_id = (session_id or challenge_id or run_id
                      or payload.get("mission_id") or "swarm")
        agent_id = payload.get("agent_id") or "supervisor"
        ev_id = trajectory_store.record(
            session_id=str(session_id),
            event_type=event,
            run_id=run_id,
            challenge_id=challenge_id,
            agent_id=str(agent_id),
            # The full payload is preserved even for fields not mapped to a column.
            observation=dict(payload),
            # Never re-broadcast as a TRAJECTORY_EVENT: the WS message must stay the
            # original SWARM_* shape sent below.
            broadcast=False,
        )
        if ev_id is None:
            logger.warning(
                f"[swarm.events] persistence returned no id for {event}; "
                f"skipping WebSocket broadcast to avoid divergence.")
            return False
        return True
    except Exception as e:
        logger.warning(
            f"[swarm.events] persistence failed for {event}; skipping WebSocket "
            f"broadcast to avoid divergence: {e}")
        return False


def broadcast(event: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Persist one swarm event, then fire-and-forget its WebSocket broadcast.

    Durability comes first (synchronous, same call); the WS half stays
    fire-and-forget and never blocks the coordination loop. Never raises.
    """
    data: Dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}

    # 1) Durable write — if this fails, do NOT broadcast the now-unrecorded event.
    if not _persist(event, data):
        return

    # 2) Existing fire-and-forget WebSocket broadcast (unchanged shape/semantics).
    try:
        import asyncio

        from backend.websocket.manager import ws_manager

        msg: Dict[str, Any] = {"event": event}
        if data:
            msg.update(data)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(ws_manager.broadcast(msg))
        except RuntimeError:
            # No running loop (sync/CLI/test context) — skip silently.
            pass
    except Exception:  # pragma: no cover - defensive; observability must never break a run
        pass
