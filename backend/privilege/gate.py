"""Shared per-command operator-approval gate.

EVERY code path that can execute an agent-originated command must route through
:func:`require_approval` before reaching the execution layer.  The logic here was
lifted out of ``SwarmOrchestrator._agent_worker`` so the swarm path and every other
caller share ONE implementation — a copy-pasted fork across five files would drift,
and the drifted copy is exactly where an agent gets to run ``rm -rf`` unapproved.

Two operator-configured modes (``settings.FORGE_APPROVAL_MODE``), chosen globally,
never per-command:

  * ``"manual"`` (default, safer) — every PRIVILEGED / DANGEROUS command broadcasts an
    ``APPROVAL_REQUIRED`` event and waits **indefinitely** for the operator's explicit
    approve or deny.  There is no timeout to fall back on: silence is not consent, it
    is simply still-waiting.
  * ``"auto"`` — PRIVILEGED / DANGEROUS commands run unattended.  The ONE thing that
    still halts execution is a command that literally requires ``sudo``: that waits
    (again, indefinitely) for the operator to supply the password, which is a
    credential prompt, not a yes/no decision.

Fail-closed by construction:
  * SAFE                   -> auto-approved (no operator round-trip).  Note that
                              common script interpreters (python/bash/node/...) are
                              SAFE by classification.
  * PRIVILEGED / DANGEROUS -> gated per the mode above.
  * no decision / broadcast failure / ANY exception during the wait
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

# Operator-approval modes.  Anything else resolves to MANUAL — the mode that always
# asks — so a misconfigured value can never silently widen execution.
_MODE_AUTO = "auto"
_MODE_MANUAL = "manual"


def _resolve_approval_mode() -> str:
    """Read the operator-configured approval mode, fail-closed on anything unknown.

    ``backend/config.py`` already validates the value at import time; re-checking here
    keeps the gate safe on its own (and for tests that swap ``settings`` wholesale).
    """
    mode = str(getattr(settings, "FORGE_APPROVAL_MODE", _MODE_MANUAL) or "").strip().lower()
    return mode if mode in (_MODE_AUTO, _MODE_MANUAL) else _MODE_MANUAL


def _reconcile_audit(audit_log_id: Optional[str], approved: bool) -> None:
    """Reconcile the classification-time AuditLogModel row with the FINAL outcome.

    approve / auto-approve -> flips the row's ``approved=False`` to True.
    deny / no-decision     -> the row is already False; the manager confirms it without
                              rewriting.  A missing id is a safe no-op, and any failure
                              is swallowed so audit reconciliation can never abort a run.
    """
    if not audit_log_id:
        return
    try:
        _db = SessionLocal()
        try:
            privilege_manager.record_privilege_decision(audit_log_id, approved, _db)
        finally:
            _db.close()
    except Exception as _ae:
        logger.debug(f"[privilege.gate] Audit reconcile skip: {_ae}")


async def require_approval(
    cmd: str,
    agent_id: str,
    pending_approvals: Dict[str, Dict[str, Any]],
    broadcast_fn: Callable[[Dict[str, Any]], Any],
    challenge_id: Optional[str],
    run_id: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Classify ``cmd`` and gate its execution behind operator authorization.

    Whether the operator is asked at all — and what they are asked FOR — depends on the
    configured approval mode (see the module docstring).  There is no timeout in either
    mode; the wait ends when the operator responds or the task is cancelled.

    Returns ``(approved, decision, sudo_password)``:
      * ``approved``      — True for a SAFE command, an auto-mode approval, or an
                            explicit operator "approve".  False for deny, a missing
                            decision, or any failure while broadcasting/waiting.
      * ``decision``      — ``"approve"`` / ``"deny"`` / ``"auto-approved"`` / None.
                            None means no decision ever arrived (e.g. the broadcast
                            failed) and must be treated as a denial.
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

    mode = _resolve_approval_mode()
    req_sudo = bool(re.search(r"\bsudo\b", cmd))

    # ── AUTO mode, no sudo: run unattended, ask nobody anything ──────────────────
    if mode == _MODE_AUTO and not req_sudo:
        # AUTO MODE TRADEOFF (deliberate, operator-requested): a DANGEROUS command that
        # does not literally contain "sudo" — `rm -rf ./workdir/scratch`, `dd`, a fork
        # bomb, `curl | sh` — executes here with NO operator interaction whatsoever.
        # Auto mode means auto: there is deliberately no extra confirmation net below
        # this line.  If you want a gate on those commands, run FORGE_APPROVAL_MODE=manual.
        _reconcile_audit(audit_log_id, True)
        return True, "auto-approved", None

    # ── Operator interaction required (manual mode, or auto + sudo credential) ────
    # auto_sudo_only: the operator is shown a password prompt instead of Approve/Deny,
    # because the only thing blocking execution is the missing credential.
    auto_sudo_only = mode == _MODE_AUTO and req_sudo

    req_id = str(uuid.uuid4())
    approval_event = asyncio.Event()
    entry: Dict[str, Any] = {
        "event": approval_event,
        "decision": None,
        "command": cmd,
        "privilege_level": priv_level,
        "agent_id": agent_id,
        "requires_sudo": req_sudo,
        "sudo_password": None,
    }
    if auto_sudo_only:
        # Lets the operator UI (and any other observer of the pending registry) tell
        # this apart from a genuine approve/deny request.
        entry["auto_mode_sudo_only"] = True
    pending_approvals[req_id] = entry

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
            # False only in auto mode, where the payload is a credential prompt and the
            # UI must NOT offer approve/deny buttons — just the password + cancel.
            "decision_required": not auto_sudo_only,
        })
        # NO timeout, in either mode.  Manual mode waits indefinitely for an explicit
        # approve/deny; auto+sudo waits indefinitely for the credential.  Note that
        # `except Exception` below deliberately does NOT catch asyncio.CancelledError
        # (a BaseException since Python 3.8; this project targets 3.10+), so a
        # kill-switch cancellation of the parent task still propagates and the run
        # really stops instead of being swallowed as a denial.
        await approval_event.wait()
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

    if auto_sudo_only:
        # The operator was asked for a CREDENTIAL, not a yes/no.  A non-empty password
        # IS the approval; an explicit Cancel, and equally an approve submitted with a
        # blank/missing password, is a DENY — an empty submission must never be read as
        # permission to run a sudo command.
        if decision == "deny":
            approved = False
        elif sudo_pw and sudo_pw.strip():
            approved = True
            decision = "approve"
        else:
            approved = False
            # A still-None decision means the wait produced nothing at all (e.g. the
            # broadcast failed) and is left as None so callers can report it as such.
            decision = "deny" if decision == "approve" else decision
    else:
        # Only an explicit "approve" allows execution.  No decision is a DENY.
        approved = decision == "approve"

    if not approved:
        sudo_pw = None

    # Reconcile the AuditLogModel row written at classification time (approved=False)
    # so the audit trail reflects what actually happened.  Runs on BOTH branches; a
    # missing id is a safe no-op.
    _reconcile_audit(audit_log_id, approved)

    return approved, decision, sudo_pw
