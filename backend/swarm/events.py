"""WebSocket event names + a safe fire-and-forget broadcaster for the swarm layer.

Phase 4 observability (§13). Mirrors the fire-and-forget pattern used by
``backend.agent_runtime.trajectory._broadcast``: if an event loop is running the
broadcast is scheduled as a task; otherwise (sync/CLI/test) it is skipped
silently. This never raises and never blocks the coordination loop.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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


def broadcast(event: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Fire-and-forget WebSocket broadcast. Never raises, never blocks."""
    try:
        import asyncio

        from backend.websocket.manager import ws_manager

        msg: Dict[str, Any] = {"event": event}
        if payload:
            msg.update(payload)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(ws_manager.broadcast(msg))
        except RuntimeError:
            # No running loop (sync/CLI/test context) — skip silently.
            pass
    except Exception:  # pragma: no cover - defensive; observability must never break a run
        pass
