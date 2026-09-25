"""Privilege elevation and operator approval: the HITL approval gate responses
(sudo/root elevation), audit-log decisions, and the audit log itself."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.models import AuditLogModel
from backend.database.session import get_db
from backend.websocket.manager import ws_manager

router = APIRouter()
logger = logging.getLogger("forge.routes")


# ----------------------------------------------------
# OPERATOR APPROVAL RESPONSES (privilege gate)
# ----------------------------------------------------

class ApprovalRespondRequest(BaseModel):
    decision: str  # "approve" or "deny"
    # WARNING: Never log this field.  It is accepted only for sudo approvals and
    # is held in memory for the single execution then discarded.
    sudo_password: Optional[str] = None


@router.post("/approvals/{request_id}/respond")
async def respond_approval(request_id: str, req: ApprovalRespondRequest, db: Session = Depends(get_db)):
    from backend.agents.swarm_orchestrator import swarm_orchestrator
    # NOTE: sudo_password is forwarded to the orchestrator's in-memory dict; it is
    # NEVER passed to any logger, broadcast payload, DB model, or exception message.
    result = await swarm_orchestrator.submit_approval_response(
        request_id, req.decision, sudo_password=req.sudo_password
    )
    if not result.get("accepted"):
        raise HTTPException(status_code=409, detail=result.get("reason", "No pending approval with this request_id."))
    return result


# ----------------------------------------------------
# PENDING APPROVALS (read-only listing)
# ----------------------------------------------------

# Fields safe to expose from a pending-approval registry entry.  This is an ALLOWLIST
# rather than a denylist because the entry dicts hold two things that must never leave
# the process: ``sudo_password`` (transient, single-use) and ``event`` (an asyncio.Event,
# which is not even JSON-serializable).  A field added to a gate entry is therefore
# invisible here until it is deliberately listed below.
_PENDING_APPROVAL_FIELDS = (
    "command", "agent_id", "privilege_level", "requires_sudo",
    "challenge_id", "run_id", "context", "decision_required", "auto_mode_sudo_only",
)


def _public_pending_entry(request_id: str, entry: dict) -> dict:
    out = {"request_id": request_id}
    for field in _PENDING_APPROVAL_FIELDS:
        if field in entry:
            out[field] = entry[field]
    return out


@router.get("/approvals/pending")
def list_pending_approvals():
    """List privileged commands currently awaiting an operator decision.

    The operator console otherwise learns about pending approvals only from the
    APPROVAL_REQUIRED WebSocket push, so a page load (or a dropped socket) would leave it
    showing nothing while the backend is still — indefinitely — waiting for an answer.
    This lets the UI rebuild that list on demand.

    READ-ONLY: decisions are still submitted exclusively through
    ``POST /approvals/{request_id}/respond``.  This endpoint deliberately offers no second
    way to resolve a request, and cannot influence any decision.
    """
    from backend.agents.swarm_orchestrator import swarm_orchestrator
    from backend.privilege.gate import SHARED_PENDING_APPROVALS

    pending = []
    seen = set()
    # Swarm runs own a per-board registry...
    for board in list(swarm_orchestrator.active_swarms.values()):
        for request_id, entry in list(board.pending_approvals.items()):
            if request_id in seen:
                continue
            seen.add(request_id)
            pending.append(_public_pending_entry(request_id, entry))
    # ...and every non-swarm caller (legacy ReAct loop, agent_runtime, ToolManager) shares
    # one global registry. Same uuid4 keyspace, so the `seen` guard is belt-and-suspenders
    # rather than a real collision risk.
    for request_id, entry in list(SHARED_PENDING_APPROVALS.items()):
        if request_id in seen:
            continue
        seen.add(request_id)
        pending.append(_public_pending_entry(request_id, entry))
    return pending


# ----------------------------------------------------
# PRIVILEGE MANAGER
# ----------------------------------------------------

class PrivilegeDecisionRequest(BaseModel):
    audit_id: str
    approved: bool

@router.get("/privilege/pending")
def list_pending_privileges(db: Session = Depends(get_db)):
    return db.query(AuditLogModel).filter(AuditLogModel.approved == False).all()

@router.post("/privilege/decision")
async def privilege_decision(req: PrivilegeDecisionRequest, db: Session = Depends(get_db)):
    audit = db.query(AuditLogModel).filter(AuditLogModel.id == req.audit_id).first()
    if not audit:
        raise HTTPException(status_code=404, detail="Audit decision not found")

    audit.approved = req.approved
    db.commit()

    await ws_manager.broadcast({
        "event": "PRIVILEGE_DECISION_UPDATED",
        "audit_id": req.audit_id,
        "approved": req.approved
    })
    return audit

@router.get("/audit-logs")
def list_audit_logs(db: Session = Depends(get_db)):
    return db.query(AuditLogModel).order_by(AuditLogModel.timestamp.desc()).all()


# ----------------------------------------------------
# ROOT PRIVILEGE ELEVATION APPROVAL SYSTEM
# ----------------------------------------------------

class PrivilegeApprovalRequest(BaseModel):
    request_id: str
    command: str
    challenge_id: Optional[str] = None
    sudo_password: Optional[str] = None
    working_directory: Optional[str] = None

class PrivilegeRejectRequest(BaseModel):
    request_id: str
    challenge_id: Optional[str] = None

@router.post("/privilege/approve")
async def approve_privilege_execution(req: PrivilegeApprovalRequest):
    """Executes an approved root/superuser command on behalf of the operator."""
    import logging
    from backend.agents.orchestrator_loop import orchestrator_loop
    from backend.tools.manager import tool_manager

    logger = logging.getLogger("forge.privilege")
    cmd = req.command.strip()
    # Log only the bare command — NEVER the password.
    logger.info(f"Operator approved root elevation for command: {cmd}")

    # Build the sudo command string WITHOUT the password (password is fed via stdin).
    # SECURITY: do NOT use f"echo {password} | sudo -S ..." — that puts the password
    # in the command string (visible in ps aux, stored in ToolExecutionModel.command,
    # and captured by logger.info above).  Use sudo -S and pass stdin instead.
    inner_cmd = cmd.removeprefix("sudo").lstrip("-S").strip() if cmd.startswith("sudo") else cmd
    elevated_cmd = f"sudo -S {inner_cmd}"

    # Pass the password via stdin only (never embedded in the command string).
    _stdin_input: Optional[str] = f"{req.sudo_password}\n" if req.sudo_password else None

    try:
        tool_res = await tool_manager.execute_raw_command(
            command=elevated_cmd,
            cwd=req.working_directory,
            timeout_seconds=120,
            stdin=_stdin_input,
        )
    finally:
        # Discard the password immediately after the subprocess call.
        _stdin_input = None

    await ws_manager.broadcast({
        "event": "ROOT_PERMISSION_RESULT",
        "request_id": req.request_id,
        "challenge_id": req.challenge_id,
        "success": tool_res.exit_code == 0,
        "exit_code": tool_res.exit_code,
        "output": (tool_res.stdout or tool_res.stderr)[:2000]
    })

    orchestrator_loop.resolve_root_request(
        request_id=req.request_id,
        success=True,
        tool_res=tool_res
    )

    return {
        "status": "APPROVED",
        "exit_code": tool_res.exit_code,
        "stdout": tool_res.stdout[:2000],
        "stderr": tool_res.stderr[:2000]
    }

@router.post("/privilege/reject")
async def reject_privilege_execution(req: PrivilegeRejectRequest):
    """Operator rejected root execution for a command."""
    import logging
    from backend.agents.orchestrator_loop import orchestrator_loop

    logger = logging.getLogger("forge.privilege")
    logger.info(f"Operator rejected root elevation for request: {req.request_id}")

    orchestrator_loop.resolve_root_request(
        request_id=req.request_id,
        success=False,
        message="Root permission rejected by operator."
    )

    await ws_manager.broadcast({
        "event": "ROOT_PERMISSION_RESULT",
        "request_id": req.request_id,
        "challenge_id": req.challenge_id,
        "success": False,
        "output": "Root privilege rejected by operator."
    })

    return {"status": "REJECTED", "request_id": req.request_id}
