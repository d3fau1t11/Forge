"""Shared per-command operator-approval gate.

EVERY code path that can execute an agent-originated command must route through
:func:`require_approval` before reaching the execution layer.  The logic here was
lifted out of ``SwarmOrchestrator._agent_worker`` so the swarm path and every other
caller share ONE implementation — a copy-pasted fork across five files would drift,
and the drifted copy is exactly where an agent gets to run ``rm -rf`` unapproved.

Fail-closed by construction:
  * SAFE                   -> auto-approved (no operator round-trip).
  * PRIVILEGED / DANGEROUS -> require an explicit operator ``approve`` response.
  * timeout / no decision / broadcast failure / ANY exception during the wait
                           -> DENY.

There is no default-allow branch anywhere in this module.
"""

import asyncio
import logging
import os
import re
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from backend.config import settings
from backend.database.session import SessionLocal
from backend.privilege.classify import classify_command_privilege
from backend.privilege.manager import privilege_manager

logger = logging.getLogger("forge.privilege")

# Non-swarm callers — the legacy ReAct loop (agents/orchestrator_loop.py) and the
# agent_runtime RealToolExecutor — have no SwarmBlackboard object to own their pending
# approvals.  ``request_id`` is a uuid4 and therefore globally unique, so a single
# shared registry safely serves all of them without risking cross-context collisions,
# and the operator's ``POST /approvals/{request_id}/respond`` endpoint can resolve
# entries here exactly as it does for swarm boards (see
# ``SwarmOrchestrator.submit_approval_response``).  Swarm runs keep their own
# per-board dict and are unaffected.
SHARED_PENDING_APPROVALS: Dict[str, Dict[str, Any]] = {}

# Matches the swarm gate's long-standing behaviour (settings.CHECKPOINT_TIMEOUT_SECONDS
# with a 30s fallback).  Kept as a named constant so the DANGEROUS clamp below can
# refer to "the default window" without re-reading settings twice.
_DEFAULT_APPROVAL_TIMEOUT = 30.0


def _resolve_timeout(timeout_seconds: Optional[float]) -> float:
    """Resolve the approval window, falling back to the configured default."""
    if timeout_seconds is not None:
        return float(timeout_seconds)
    return float(getattr(settings, "CHECKPOINT_TIMEOUT_SECONDS", _DEFAULT_APPROVAL_TIMEOUT))


async def require_approval(
    cmd: str,
    agent_id: str,
    pending_approvals: Dict[str, Dict[str, Any]],
    broadcast_fn: Callable[[Dict[str, Any]], Any],
    challenge_id: Optional[str],
    run_id: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Classify ``cmd`` and gate its execution behind explicit operator approval.

    Returns ``(approved, decision, sudo_password)``:
      * ``approved``      — True only for a SAFE command or an explicit operator
                            "approve".  False for deny, timeout, a missing decision,
                            or any failure while broadcasting/waiting.
      * ``decision``      — ``"approve"`` / ``"deny"`` / None (no decision arrived).
                            None means the caller should treat it as a TIMEOUT.
      * ``sudo_password`` — the single-use operator-supplied password, returned ONLY
                            when the command was approved; None otherwise.

    Side effects (all present in the original swarm implementation):
      * writes an AuditLogModel row at classification time via
        ``evaluate_privilege_ex`` and reconciles it with the REAL decision via
        ``record_privilege_decision``;
      * registers the request in ``pending_approvals`` under a fresh uuid4 so the
        operator UI can respond, and pops it (wiping any sudo password first) once
        the wait resolves;
      * broadcasts an ``APPROVAL_REQUIRED`` event through ``broadcast_fn``.
    """
    bin_name = os.path.basename(cmd.strip().split()[0]) if cmd.strip() else ""
    priv_level = classify_command_privilege(cmd, bin_name)

    approved = False
    # audit_log_id lets us reconcile the AuditLogModel row (written now, showing
    # approved=False for a non-SAFE command) with the operator's REAL approve/deny
    # decision once the async approval gate resolves.
    audit_log_id: Optional[str] = None
    try:
        db = SessionLocal()
        try:
            approved, audit_log_id = privilege_manager.evaluate_privilege_ex(
                agent=agent_id, tool_name=bin_name, privilege_level=priv_level, db=db
            )
        finally:
            db.close()
    except Exception as e:
        # Classification-time audit logging failed — the command is still NOT
        # approved (approved stays False), so it falls through to the operator gate.
        logger.debug(f"[privilege.gate] Privilege evaluation error: {e}")

    # SAFE (or otherwise pre-approved) — no operator round-trip required.
    if approved:
        return True, None, None

    # ── Operator approval wait gate (per-request) ────────────────────────────────
    req_id = str(uuid.uuid4())
    req_sudo = bool(re.search(r"\bsudo\b", cmd))
    approval_event = asyncio.Event()
    pending_approvals[req_id] = {
        "event": approval_event,
        "decision": None,
        "command": cmd,
        "privilege_level": priv_level,
        "agent_id": agent_id,
        "requires_sudo": req_sudo,
        "sudo_password": None,
    }

    wait_timeout = _resolve_timeout(timeout_seconds)
    if priv_level == "DANGEROUS":
        # HARD RULE: a DANGEROUS command's approval window is never extended, never
        # defaulted away, and never bypassed — no code path may widen it.  The clamp
        # below can only ever SHORTEN the window, never lengthen it.  Combined with
        # the "no decision == deny" resolution at the end, a DANGEROUS command with no
        # operator response within the window always returns approved=False.
        wait_timeout = min(wait_timeout, _resolve_timeout(None))

    try:
        await broadcast_fn({
            "event": "APPROVAL_REQUIRED",
            "request_id": req_id,
            "challenge_id": challenge_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "command": cmd,
            "privilege_level": priv_level,
            "requires_sudo": req_sudo,
        })
        await asyncio.wait_for(approval_event.wait(), timeout=wait_timeout)
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        # A broken WebSocket (or any other failure) must DENY.  Critically, it must
        # neither propagate as an unhandled crash that some outer `except: pass`
        # could swallow into a default-allow path, nor skip the bookkeeping below.
        logger.warning(f"[privilege.gate] Approval broadcast/wait failed; denying: {e}")

    entry = pending_approvals.get(req_id, {})
    decision = entry.get("decision")
    # Retrieve sudo_password BEFORE popping the entry, then immediately wipe it from
    # the dict so it cannot be read again even transiently (single-use, in-memory only).
    sudo_pw: Optional[str] = entry.get("sudo_password")
    if req_id in pending_approvals:
        pending_approvals[req_id]["sudo_password"] = None
    pending_approvals.pop(req_id, None)

    # Only an explicit "approve" allows execution.  A timeout or any other value is a DENY.
    approved = decision == "approve"
    if not approved:
        sudo_pw = None

    # Reconcile the AuditLogModel row written at classification time (approved=False)
    # so the audit trail reflects what actually happened.  approve -> flips
    # False->True; deny/timeout -> already False, so record_privilege_decision confirms
    # it without rewriting.  Runs on BOTH branches; a missing id is a safe no-op.
    if audit_log_id:
        try:
            _adb = SessionLocal()
            try:
                privilege_manager.record_privilege_decision(audit_log_id, approved, _adb)
            finally:
                _adb.close()
        except Exception as _ae:
            logger.debug(f"[privilege.gate] Audit reconcile skip: {_ae}")

    return approved, decision, sudo_pw
