"""Execution surface: tool registry/capability invocation, the operator terminal,
the execution-layer status views, target-type detection, interactive sessions, and
the emergency kill switch that halts a running workflow."""

import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.runner import workflow_runner
from backend.database.models import RunModel, ToolExecutionModel, TerminalCommandModel
from backend.database.session import get_db
from backend.utils.time import utcnow
from backend.execution.service import execution_service
from backend.privilege.gate import require_approval, SHARED_PENDING_APPROVALS
from backend.tools.manager import tool_manager
from backend.tools.registry import tool_registry
from backend.websocket.manager import ws_manager

logger = logging.getLogger("forge.api.execution")

router = APIRouter()


# ----------------------------------------------------
# TOOLS & EXECUTIONS
# ----------------------------------------------------

@router.get("/tools")
def list_tools():
    return [t.dict() for t in tool_registry.tools.values()]

@router.get("/tools/executions")
def list_tool_executions(challenge_id: Optional[str] = None, limit: int = 200, db: Session = Depends(get_db)):
    """Tool execution history, optionally filtered by challenge.

    The swarm now persists every executed command here, so the terminal views can
    be reload-safe instead of live-event-only.
    """
    # Single JOIN query (no per-row lazy loads) so reads stay fast while the swarm writes.
    query = (db.query(ToolExecutionModel, RunModel.challenge_id)
             .join(RunModel, ToolExecutionModel.run_id == RunModel.id))
    if challenge_id:
        query = query.filter(RunModel.challenge_id == challenge_id)
    rows = query.order_by(ToolExecutionModel.created_at.desc()).limit(min(max(limit, 1), 500)).all()
    return [{
        "id": r.id,
        "run_id": r.run_id,
        "challenge_id": challenge_id_col,
        "agent": r.agent,
        "tool_name": r.tool_name,
        "capability": r.capability,
        "command": r.command,
        "privilege_level": r.privilege_level,
        "approved": r.approved,
        "status": r.status,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "exit_code": r.exit_code,
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None
    } for r, challenge_id_col in rows]

# Capabilities that spawn a process from a caller-supplied string rather than running a
# fixed, audited binary. For these, `target` IS the command line (see
# ToolManager.execute_capability → interactive_open), so it must be classified and gated
# as a command — not by the capability name, which the registry marks SAFE.
SHELL_CAPABILITIES = frozenset({"interactive_open", "interactive_start"})


class ExecuteToolRequest(BaseModel):
    capability: str
    target: str


@router.post("/tools/execute")
async def execute_tool(req: ExecuteToolRequest):
    """Execute a tool capability.

    `execute_capability` reaches the shell for the process-spawning capabilities above,
    which makes this an execution entry point — so it passes the SAME operator-approval
    gate as every agent command path. A denied or timed-out request never reaches the
    execution layer.
    """
    capability = (req.capability or "").strip()
    effective_cmd = (req.target or "").strip() if capability in SHELL_CAPABILITIES \
        else f"{capability} {req.target or ''}".strip()

    approved, decision, _sudo_pw = await require_approval(
        cmd=effective_cmd,
        agent_id="api:/tools/execute",
        pending_approvals=SHARED_PENDING_APPROVALS,
        broadcast_fn=ws_manager.broadcast,
        challenge_id=None,
        run_id=None,
    )
    if not approved:
        status_str = "TIMEOUT" if decision is None else "DENIED"
        raise HTTPException(
            status_code=403,
            detail=(f"[PRIVILEGE {status_str}] Operator did not approve capability "
                    f"'{capability}'. Nothing was executed."),
        )

    result = await tool_manager.execute_capability(capability=req.capability, target=req.target)
    return result


class TerminalExecuteRequest(BaseModel):
    command: str
    challenge_id: Optional[str] = None
    working_directory: Optional[str] = None


@router.post("/terminal/execute")
async def execute_terminal_command(req: TerminalExecuteRequest):
    start_time = utcnow()
    # Phase 3: route the operator terminal through the SAME execution layer as the
    # agent (ExecutionService -> LocalBackend -> ProcessManager) instead of spawning a
    # subprocess here. This gives Windows python3->python normalisation, process-tree
    # tracking, and execution-failure classification for free, and keeps a single
    # execution path across the whole system.
    exec_cwd = req.working_directory if (req.working_directory and os.path.exists(req.working_directory)) else None
    try:
        exec_result = await execution_service.run_command(
            req.command,
            cwd=exec_cwd,
            timeout_seconds=60,
            capability="terminal_command",
            tool_name="terminal",
        )
        output = exec_result.stdout or exec_result.stderr or "Command completed with no output."
        exit_code = exec_result.exit_code
    except Exception as e:
        output = f"Execution error: {str(e)}"
        exit_code = -1

    duration_ms = (utcnow() - start_time).total_seconds() * 1000

    # Persist terminal command to database (non-blocking: log failure but continue)
    try:
        db = next(get_db())
        cmd_record = TerminalCommandModel(
            challenge_id=req.challenge_id,
            session_id=None,  # could be extended with a session tracker later
            command=req.command,
            stdout=exec_result.stdout if 'exec_result' in locals() and exec_result.stdout else "",
            stderr=exec_result.stderr if 'exec_result' in locals() and exec_result.stderr else "",
            exit_code=exit_code,
            duration_ms=duration_ms,
        )
        db.add(cmd_record)
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to persist terminal command: {e}")
        # Do not break command execution on DB write failure
    finally:
        try:
            db.close()
        except Exception:
            pass

    event_payload = {
        "event": "LOG_OUTPUT",
        "challenge_id": req.challenge_id,
        "command": req.command,
        "output": output,
        "exit_code": exit_code,
        "timestamp": start_time.strftime("%H:%M:%S")
    }
    await ws_manager.broadcast(event_payload)
    return event_payload


@router.get("/terminal/history")
def get_terminal_history(
    challenge_id: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db)
):
    """Terminal command history for replay, optionally filtered by challenge.

    Returns entries in ascending created_at order (oldest first) so the frontend
    can render them sequentially. Field names match list_tool_executions for
    shared renderer compatibility.
    """
    limit = min(max(limit, 1), 500)
    query = db.query(TerminalCommandModel)
    if challenge_id:
        query = query.filter(TerminalCommandModel.challenge_id == challenge_id)
    rows = query.order_by(TerminalCommandModel.created_at.asc()).limit(limit).all()
    return [{
        "id": r.id,
        "run_id": None,
        "challenge_id": r.challenge_id,
        "agent": "operator",
        "tool_name": "terminal",
        "capability": "terminal_command",
        "command": r.command,
        "privilege_level": "SAFE",
        "approved": True,
        "status": "SUCCESS" if r.exit_code == 0 else "FAILED",
        "stdout": r.stdout,
        "stderr": r.stderr,
        "exit_code": r.exit_code,
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None
    } for r in rows]


@router.get("/execution/status")
def get_execution_status():
    """Phase 3 execution-layer status.

    Read-only snapshot of WHERE/HOW commands run: the reported execution
    environment (OS + installed tools + python libs), the registered execution
    backends, the live process count from the ProcessManager, and the artifact
    store. Surfaces the Phase 3 layer without spawning anything.
    """
    from backend.execution.process_manager import process_manager
    from backend.execution.artifact_store import artifact_store
    from backend.agent_runtime.execution_backend import execution_backend
    from backend.execution.interactive import interactive_manager

    caps = execution_backend.capabilities()
    return {
        "environment": caps.to_dict(),
        "backends": execution_service.list_backends(),
        "processes": {
            "active_count": process_manager.active_count(),
            "active_pids": process_manager.active_pids(),
        },
        "interactive": {
            "active_sessions": interactive_manager.active_count(),
            "tracked_sessions": interactive_manager.count(),
        },
        "artifacts": {
            "total": len(artifact_store.all_records()),
        },
    }


@router.get("/execution/targets")
def detect_target_types(spec: str = Query(..., description="Target spec (multi-target joined with '+')")):
    """Phase 4.x — classify a target spec into structured target types (read-only).

    Conservative: unresolvable parts are reported as UNKNOWN rather than guessed.
    Multiple targets are split on the FORGE '+' delimiter.
    """
    from backend.execution.targets import target_detector
    targets = target_detector.detect_multi(spec)
    return {"spec": spec, "targets": [t.to_dict() for t in targets]}


@router.get("/execution/interactive")
def list_interactive_sessions():
    """Phase 4.x — active interactive sessions as checkpoint-safe specs (no live handles)."""
    from backend.execution.interactive import interactive_manager
    return {
        "active_sessions": interactive_manager.active_count(),
        "tracked_sessions": interactive_manager.count(),
        "sessions": interactive_manager.snapshot_specs(),
    }


# ----------------------------------------------------
# EMERGENCY KILL SWITCH
# ----------------------------------------------------

@router.post("/killswitch")
async def kill_switch(run_id: Optional[str] = None):
    workflow_runner.activate_kill_switch(run_id)
    await ws_manager.broadcast({
        "event": "KILL_SWITCH_ACTIVATED",
        "run_id": run_id
    })
    return {"status": "KILL_SWITCH_ACTIVATED", "run_id": run_id}
