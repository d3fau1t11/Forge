import os
import re
import asyncio
import logging
import shutil
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timezone

from backend.database.session import get_db
from backend.database.models import (
    ChallengeModel, TargetProfileModel, RunModel, AgentStateModel,
    CheckpointModel, ToolExecutionModel, FindingModel, EvidenceModel,
    ReportModel, KnowledgeEntryModel, ProviderUsageModel, AuditLogModel,
    ProviderConfigModel, TrajectoryEventModel,
    SwarmMissionModel, SwarmTaskModel, SwarmEvidenceModel,
)
from backend.environment.detector import environment_detector
from backend.providers.router import model_router
from backend.providers.snippet_parser import SnippetParser
from backend.tools.registry import tool_registry
from backend.tools.manager import tool_manager
from backend.execution.service import execution_service
from backend.api.runner import workflow_runner
from backend.websocket.manager import ws_manager
from backend.reporting.generator import report_generator
from backend.privilege.manager import privilege_manager
from backend.privilege.gate import require_approval, SHARED_PENDING_APPROVALS
from backend.utils.workspace import (
    CTF_WORKSPACE_ROOT,
    is_deletable_working_dir,
    resolve_safe_working_dir,
)

router = APIRouter()
logger = logging.getLogger("forge.routes")

def _safe_delete_working_dir(working_dir: str):
    """Delete a challenge working directory ONLY if it is safely inside the CTF workspace root.

    Historic bug: challenges created before the ~/Documents/CTF hierarchy stored
    working_directory="." which resolved to the project root, so rmtree wiped the
    whole project. Path safety now lives in backend.utils.workspace and uses a
    strict allowlist instead of a blocklist.
    """
    if not working_dir or not isinstance(working_dir, str):
        return
    clean_path = os.path.abspath(working_dir.strip())
    if not is_deletable_working_dir(clean_path):
        logger.warning(
            f"Refused to delete working directory outside CTF workspace root: {clean_path} "
            f"(workspace root: {CTF_WORKSPACE_ROOT})"
        )
        return
    if os.path.exists(clean_path) and os.path.isdir(clean_path):
        try:
            shutil.rmtree(clean_path, ignore_errors=True)
            logger.info(f"Successfully deleted challenge working directory: {clean_path}")
        except Exception as e:
            logger.warning(f"Error removing working directory {clean_path}: {e}")

def _delete_challenge_log(challenge_id: str):
    """Delete the dedicated challenge log file(s) if they exist.

    Removes both the mirrored-structure log (logs/<Platform>/<Category>/
    <Difficulty>/<Name>/challenge_<id>.log) and any legacy flat log, then prunes
    now-empty parent directories up to (but not including) the logs base.
    """
    try:
        from backend.utils.challenge_paths import (
            iter_candidate_log_paths, forget_challenge_log_path, logs_base,
        )
        base = logs_base()
        removed_dirs: set = set()
        for log_file in iter_candidate_log_paths(challenge_id):
            try:
                if os.path.exists(log_file):
                    os.remove(log_file)
                removed_dirs.add(os.path.dirname(log_file))
            except Exception as e:
                logger.debug(f"Error removing log {log_file}: {e}")
        # Prune empty mirrored parent dirs (never the base itself).
        for start in removed_dirs:
            d = start
            while d and os.path.abspath(d) != os.path.abspath(base) and \
                    os.path.abspath(d).startswith(os.path.abspath(base) + os.sep):
                try:
                    if os.path.isdir(d) and not os.listdir(d):
                        os.rmdir(d)
                        d = os.path.dirname(d)
                    else:
                        break
                except Exception:
                    break
        forget_challenge_log_path(challenge_id)
    except Exception as e:
        logger.debug(f"Error removing log for challenge {challenge_id}: {e}")

def extract_target_from_text(text: str) -> str:
    """Intelligently extracts target network endpoint, URL, netcat connection, or artifact from description."""
    if not text:
        return ""
    # Look for http(s) URL
    url_match = re.search(r'https?://[^\s]+', text, re.IGNORECASE)
    if url_match:
        return url_match.group(0).rstrip(".,;)\"'>")
    # Look for netcat connection: nc <host> <port>
    nc_match = re.search(r'nc\s+([a-zA-Z0-9.\-_]+)\s+(\d+)', text, re.IGNORECASE)
    if nc_match:
        return f"{nc_match.group(1)}:{nc_match.group(2)}"
    # Look for IP:port or IP
    ip_match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b', text)
    if ip_match:
        return ip_match.group(0)
    # Look for hostname:port
    host_match = re.search(r'\b([a-zA-Z0-9-]+\.[a-zA-Z0-9.\-]+:\d+)\b', text)
    if host_match:
        return host_match.group(0)
    # Look for artifact or file path
    file_match = re.search(r'(?:[a-zA-Z]:[\\/]|(?:\/|~\/|\.\/))[^\s]+?\.(?:pcap|zip|bin|elf|tar|gz|py|c|exe|txt|raw)', text, re.IGNORECASE)
    if file_match:
        return file_match.group(0)
    return ""

# Request Models
class CreateChallengeRequest(BaseModel):
    name: str
    category: str = "WEB"
    difficulty: str = "MEDIUM"
    description: str = ""
    target_address: Optional[str] = ""
    working_directory: Optional[str] = ""
    platform_name: Optional[str] = ""
    requires_root: bool = False
    # Per-run agent config (flexible-agent engine + HITL checkpoint).
    flag_pattern: Optional[str] = ""            # VALIDATION FILTER only, never a target
    max_iterations: Optional[int] = 0           # 0 -> config default AGENT_MAX_ITERATIONS
    max_minutes: Optional[int] = 0              # 0 -> config default AGENT_MAX_MINUTES
    instance_expiry_minutes: Optional[int] = 0  # minutes from now the instance dies; 0 -> none
    attached_file_paths: Optional[List[str]] = None   # server paths from /challenges/upload

class UpdateTargetAddressRequest(BaseModel):
    new_address: str

class ExecuteToolRequest(BaseModel):
    capability: str
    target: str

class TerminalExecuteRequest(BaseModel):
    command: str
    challenge_id: Optional[str] = None
    working_directory: Optional[str] = None

class SaveWriteupRequest(BaseModel):
    # The operator-confirmed markdown to persist. When omitted, the backend
    # regenerates a deterministic writeup from the challenge's real telemetry.
    content: Optional[str] = None

class EvidenceCreateRequest(BaseModel):
    challenge_id: str
    agent: str
    evidence_type: str
    source: str
    content: str
    confidence: float = 1.0

class FindingCreateRequest(BaseModel):
    challenge_id: str
    agent: str
    title: str
    description: str = ""
    vulnerability_class: str = "web"
    severity: str = "HIGH"

class PrivilegeDecisionRequest(BaseModel):
    audit_id: str
    approved: bool

class UpdateSystemSettingsRequest(BaseModel):
    execution_mode: str = "CTF_OFFENSIVE_CONTROLLED"
    auto_approve_privileged: bool = False
    command_timeout_seconds: int = 300
    daily_budget_usd: float = 5.00
    session_budget_usd: float = 2.00
    paid_model_allowed: bool = True
    default_strategy: str = "EXPLOIT_FIRST"

class ParseSnippetRequest(BaseModel):
    snippet: str

class RegisterSnippetRequest(BaseModel):
    snippet: Optional[str] = None
    provider_name: Optional[str] = "nvidia"
    api_key: Optional[str] = None
    model_id: Optional[str] = None
    base_url: Optional[str] = None
    test_connection: bool = True

class UpdateProviderKeyRequest(BaseModel):
    provider_name: str
    api_key: str
    model_id: Optional[str] = None
    base_url: Optional[str] = None

# ----------------------------------------------------
# SYSTEM & ENVIRONMENT
# ----------------------------------------------------

@router.get("/health")
def health_check():
    env = environment_detector.detect_environment()
    return {
        "status": "healthy",
        "system": env["os"],
        "distro": env["distro"],
        "installed_tools_count": sum(1 for t in env["installed_tools"].values() if t["installed"]),
        "paid_models_allowed": model_router.paid_allowed,
        "daily_budget_usd": model_router.daily_budget_usd,
        "current_spent_usd": model_router.current_spent_usd
    }

@router.get("/environment")
def get_environment():
    return environment_detector.detect_environment()

# ----------------------------------------------------
# CHALLENGES CRUD & CONTROL
# ----------------------------------------------------

@router.get("/challenges")
def list_challenges(db: Session = Depends(get_db)):
    return db.query(ChallengeModel).all()

@router.get("/challenges/{challenge_id}")
def get_challenge(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return challenge

@router.get("/challenges/{challenge_id}/plan")
def get_challenge_plan(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return challenge.mission_plan or {"tasks": [], "status": "PENDING"}

@router.get("/challenges/{challenge_id}/log")
def get_challenge_log(challenge_id: str, db: Session = Depends(get_db)):
    """Retrieve dedicated challenge log path and file content."""
    from backend.utils.challenge_paths import resolve_challenge_log_path
    log_path = resolve_challenge_log_path(challenge_id)
    content = ""
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            logger.warning(f"Error reading challenge log {log_path}: {e}")
    return {
        "status": "SUCCESS",
        "challenge_id": challenge_id,
        "log_file": log_path,
        "content": content,
    }


@router.get("/challenges/{challenge_id}/candidates")
def get_challenge_candidates(challenge_id: str, db: Session = Depends(get_db)):
    """Retrieve flag candidates (live or persisted snapshot) for a challenge."""
    try:
        from backend.agents.swarm_orchestrator import swarm_orchestrator
        if challenge_id in swarm_orchestrator.active_swarms:
            board = swarm_orchestrator.active_swarms[challenge_id]
            return {"challenge_id": challenge_id, "candidates": list(board.flag_candidates)}
    except Exception:
        pass

    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    
    plan = challenge.mission_plan or {}
    bb_state = plan.get("blackboard_state", {}) if isinstance(plan, dict) else {}
    candidates = bb_state.get("flag_candidates", [])
    return {"challenge_id": challenge_id, "candidates": candidates}


@router.get("/challenges/{challenge_id}/derived-artifacts")
def get_challenge_derived_artifacts(challenge_id: str, db: Session = Depends(get_db)):
    """Retrieve reconstructed derived artifacts (live or persisted snapshot) for a challenge."""
    try:
        from backend.agents.swarm_orchestrator import swarm_orchestrator
        if challenge_id in swarm_orchestrator.active_swarms:
            board = swarm_orchestrator.active_swarms[challenge_id]
            return {"challenge_id": challenge_id, "artifacts": list(board.derived_artifacts)}
    except Exception:
        pass

    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    
    plan = challenge.mission_plan or {}
    bb_state = plan.get("blackboard_state", {}) if isinstance(plan, dict) else {}
    artifacts = bb_state.get("derived_artifacts", [])
    return {"challenge_id": challenge_id, "artifacts": artifacts}


@router.get("/challenges/{challenge_id}/decisions")
def get_challenge_decisions(challenge_id: str, db: Session = Depends(get_db)):
    """Retrieve AI decision history from TrajectoryEventModel and live/persisted execution history."""
    decisions = []
    # Query database trajectory events for this challenge
    events = (db.query(TrajectoryEventModel)
              .filter(TrajectoryEventModel.challenge_id == challenge_id)
              .filter(TrajectoryEventModel.event_type.in_(["DECISION", "AI_DECISION", "PLAN", "REPLAN"]))
              .order_by(TrajectoryEventModel.created_at.desc())
              .all())
    
    for ev in events:
        decisions.append({
            "id": ev.id,
            "timestamp": ev.created_at.isoformat() if ev.created_at else None,
            "agent": ev.agent_id or "ORCHESTRATOR",
            "goal": ev.decision_summary or ev.strategy or "Strategic Action",
            "capability": ev.tool_name or ev.action_type or "reasoning",
            "selectedTool": ev.tool_name or ev.command or "reasoning",
            "reason": ev.decision_summary,
            "result": ev.result or ev.stdout[:200] or "SUCCESS",
            "confidence": 90,
            "model": ev.model or "FORGE Router",
            "challengeId": challenge_id
        })

    # Check live blackboard execution history if active
    try:
        from backend.agents.swarm_orchestrator import swarm_orchestrator
        if challenge_id in swarm_orchestrator.active_swarms:
            board = swarm_orchestrator.active_swarms[challenge_id]
            for idx, item in enumerate(board.execution_history):
                decisions.append({
                    "id": f"live-dec-{idx}-{item.get('ts', '')}",
                    "timestamp": item.get("ts") or "Just now",
                    "agent": item.get("agent", "SWARM_WORKER"),
                    "goal": item.get("note") or f"Executed {item.get('command', '')[:50]}",
                    "capability": "execution",
                    "selectedTool": item.get("command", "")[:40],
                    "result": (item.get("output") or "")[:200],
                    "confidence": 95,
                    "model": "Swarm Worker",
                    "challengeId": challenge_id
                })
    except Exception:
        pass

    return {"challenge_id": challenge_id, "decisions": decisions}


@router.delete("/challenges/{challenge_id}")
async def delete_challenge(challenge_id: str, db: Session = Depends(get_db)):
    """Deletes a challenge from the database, cascading to runs/findings, and deletes its working directory on disk."""
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    
    working_dir = challenge.working_directory
    
    # 1. Delete associated reports
    db.query(ReportModel).filter(ReportModel.challenge_id == challenge_id).delete()
    
    # 2. Delete challenge (SQLAlchemy relationship cascade deletes runs, targets, checkpoints, tool_executions, evidence, findings)
    db.delete(challenge)
    db.commit()
    
    # 3. Clean up challenge working directory on disk safely
    _safe_delete_working_dir(working_dir)
    
    # 4. Clean up challenge dedicated log file
    _delete_challenge_log(challenge_id)
    
    # 5. Broadcast real-time WebSocket event
    try:
        await ws_manager.broadcast({
            "event": "CHALLENGE_DELETED",
            "challenge_id": challenge_id
        })
    except Exception:
        pass
        
    return {
        "status": "SUCCESS",
        "message": f"Challenge '{challenge_id}' and associated working directory deleted successfully.",
        "challenge_id": challenge_id
    }

@router.delete("/challenges")
async def delete_all_challenges(db: Session = Depends(get_db)):
    """Deletes all challenges from the database and deletes all associated working directories on disk."""
    challenges = db.query(ChallengeModel).all()
    deleted_count = 0
    for ch in challenges:
        working_dir = ch.working_directory
        ch_id = ch.id
        db.query(ReportModel).filter(ReportModel.challenge_id == ch_id).delete()
        db.delete(ch)
        _safe_delete_working_dir(working_dir)
        _delete_challenge_log(ch_id)
        deleted_count += 1
    
    db.commit()
    
    try:
        await ws_manager.broadcast({
            "event": "ALL_CHALLENGES_DELETED",
            "count": deleted_count
        })
    except Exception:
        pass
        
    return {
        "status": "SUCCESS",
        "message": f"All {deleted_count} challenges and associated working directories deleted successfully.",
        "deleted_count": deleted_count
    }

@router.post("/challenges")
async def create_challenge(req: CreateChallengeRequest, db: Session = Depends(get_db)):
    platform = req.platform_name.strip() if (req.platform_name and req.platform_name.strip()) else "PicoCTF"
    category = req.category.strip().upper() if req.category else "WEB"
    difficulty = req.difficulty.strip().upper() if req.difficulty else "MEDIUM"
    name = req.name.strip() if (req.name and req.name.strip()) else "Challenge_Target"

    # Enforce structured hierarchy: ~/Documents/CTF/<Platform>/<Category>/<Difficulty>/<Name>
    ctf_root_dir = os.path.expanduser(os.path.join("~", "Documents", "CTF"))
    working_dir = os.path.abspath(os.path.join(ctf_root_dir, platform, category, difficulty, name))
    os.makedirs(working_dir, exist_ok=True)

    # Move any operator-uploaded artifacts (staged by POST /challenges/upload) into
    # the challenge workspace byte-for-byte, and collect their final paths.
    attached_final: List[str] = []
    for _src in (req.attached_file_paths or []):
        try:
            if _src and os.path.isfile(_src):
                _dest = os.path.join(working_dir, os.path.basename(_src))
                if os.path.abspath(_src) != os.path.abspath(_dest):
                    shutil.move(_src, _dest)
                attached_final.append(_dest)
        except Exception as _move_err:
            logger.warning(f"Could not stage uploaded artifact '{_src}': {_move_err}")

    challenge = ChallengeModel(
        name=name,
        category=category,
        difficulty=difficulty,
        description=req.description,
        working_directory=working_dir,
        platform_name=platform,
        requires_root=req.requires_root,
        status="RUNNING"
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    resolved_target = (req.target_address or "").strip()
    if not resolved_target:
        resolved_target = extract_target_from_text(req.description)
    if not resolved_target:
        resolved_target = f"{name.lower().replace(' ', '_')}.ctf"

    multi_targets = [t.strip() for t in resolved_target.replace("+", ",").split(",") if t.strip()]
    first_target = multi_targets[0] if multi_targets else resolved_target
    is_file = os.path.exists(first_target) or len(multi_targets) > 1
    target = TargetProfileModel(
        challenge_id=challenge.id,
        current_address=resolved_target,
        hostname=os.path.basename(first_target) if (is_file and os.path.exists(first_target)) else f"{name.lower()}.ctf",
        verification_status="verified_file" if is_file else "verified_network"
    )
    db.add(target)
    db.commit()

    run = RunModel(
        challenge_id=challenge.id,
        status="RUNNING",
        current_phase="ingest",
        current_agent="orchestrator"
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    # Pre-flight Mission Plan Generation
    from backend.agents.strategic_planner import strategic_planner
    try:
        initial_plan = await strategic_planner.generate_initial_plan(
            challenge_id=challenge.id,
            challenge_name=challenge.name,
            category=challenge.category,
            difficulty=challenge.difficulty,
            target=resolved_target,
            description=challenge.description
        )
        challenge.mission_plan = initial_plan
        challenge.started_at = datetime.utcnow()
        db.commit()
        db.refresh(challenge)
    except Exception as plan_err:
        logger.warning(f"Initial plan creation fallback: {plan_err}")

    # Persist per-run agent config into mission_plan.run_config so WorkflowRunner can
    # thread budget / flag pattern / uploaded artifacts / instance timer into run_swarm.
    expiry_ts = None
    if req.instance_expiry_minutes and req.instance_expiry_minutes > 0:
        expiry_ts = datetime.now(timezone.utc).timestamp() + (req.instance_expiry_minutes * 60)
    run_config = {
        "flag_pattern": (req.flag_pattern or "").strip(),
        "max_iterations": int(req.max_iterations or 0),
        "max_minutes": int(req.max_minutes or 0),
        "attached_file_paths": attached_final,
        "instance_expiry_ts": expiry_ts,
    }
    try:
        mp = dict(challenge.mission_plan or {})
        mp["run_config"] = run_config
        challenge.mission_plan = mp
        db.commit()
        db.refresh(challenge)
    except Exception as rc_err:
        logger.warning(f"Could not persist run_config: {rc_err}")

    # Initialize Challenge Dedicated Log File FIRST — stored in a subtree mirroring the
    # challenge's own structure (logs/<Platform>/<Category>/<Difficulty>/<Name>/).
    # Must happen BEFORE background recon or runner execution starts writing log appends.
    from backend.utils.challenge_paths import register_challenge_log_path
    ch_log_path = register_challenge_log_path(
        challenge.id, platform, category, difficulty, challenge.name)
    with open(ch_log_path, "w", encoding="utf-8") as f:
        f.write(f"=== FORGE CTF CHALLENGE LOG STARTED ===\n")
        f.write(f"Timestamp: {datetime.utcnow().isoformat()} UTC\n")
        f.write(f"Challenge ID: {challenge.id}\n")
        f.write(f"Challenge Name: {challenge.name}\n")
        f.write(f"Platform: {platform} | Category: {category} | Difficulty: {difficulty}\n")
        f.write(f"Target Scope: {req.target_address}\n")
        f.write(f"Working Directory: {working_dir}\n")
        f.write(f"Run ID: {run.id}\n")
        f.write(f"=======================================\n\n")

    # Phase 3 Turbo Recon: Pre-warm recon in background immediately
    from backend.recon.turbo_recon import turbo_recon
    if resolved_target:
        asyncio.create_task(turbo_recon.start_turbo_recon(challenge.id, resolved_target, category.lower(), working_directory=working_dir))

    workflow_runner.start_run(run.id, challenge.id, resolved_target)

    await ws_manager.broadcast({
        "event": "CHALLENGE_CREATED",
        "challenge_id": challenge.id,
        "name": challenge.name,
        "target": req.target_address,
        "working_directory": working_dir,
        "log_file": ch_log_path
    })

    if challenge.mission_plan:
        await ws_manager.broadcast({
            "event": "PLAN_GENERATED",
            "challenge_id": challenge.id,
            "run_id": run.id,
            "plan": challenge.mission_plan
        })

    return challenge


@router.post("/challenges/upload")
async def upload_artifact(file: UploadFile = File(...)):
    """Byte-safe upload of a challenge artifact. Streams to a staging dir under the
    CTF workspace and returns its absolute path; create_challenge then moves it into
    the challenge workspace. Bytes never pass through any text-decoding layer."""
    import uuid as _uuid
    safe_name = os.path.basename(file.filename or "artifact.bin").replace("\\", "_").replace("/", "_")
    safe_name = "".join(c for c in safe_name if c not in '<>:"|?*').strip() or "artifact.bin"
    staging_dir = os.path.join(CTF_WORKSPACE_ROOT, "_uploads", _uuid.uuid4().hex[:12])
    os.makedirs(staging_dir, exist_ok=True)
    dest = os.path.join(staging_dir, safe_name)
    try:
        with open(dest, "wb") as fh:                     # wb — byte-for-byte, never decoded
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
    finally:
        await file.close()
    size = os.path.getsize(dest) if os.path.exists(dest) else 0
    logger.info(f"[upload] staged artifact {dest} ({size} bytes)")
    return {"path": dest, "filename": safe_name, "size": size}


@router.get("/challenges/{challenge_id}/checkpoint")
def get_checkpoint(challenge_id: str, db: Session = Depends(get_db)):
    """Latest HITL checkpoint report for a challenge (operator copies it out to a
    stronger external model, then pastes the response back)."""
    run_ids = [r[0] for r in db.query(RunModel.id).filter(RunModel.challenge_id == challenge_id).all()]
    if not run_ids:
        return {"has_checkpoint": False}
    cps = (db.query(CheckpointModel)
           .filter(CheckpointModel.run_id.in_(run_ids))
           .order_by(CheckpointModel.created_at.desc())
           .limit(30).all())
    cp = next((c for c in cps
               if isinstance(c.state_snapshot, dict) and c.state_snapshot.get("kind") == "hitl_checkpoint"), None)
    if not cp:
        return {"has_checkpoint": False}
    snap = cp.state_snapshot or {}
    return {
        "has_checkpoint": True,
        "cycle": snap.get("cycle_n"),
        "report": snap.get("report", ""),
        "agent_ids": snap.get("agent_ids", []),
        "created_at": cp.created_at.isoformat() if cp.created_at else None,
    }


class CheckpointRespondRequest(BaseModel):
    text: str = ""


@router.post("/challenges/{challenge_id}/checkpoint/respond")
async def respond_checkpoint(challenge_id: str, req: CheckpointRespondRequest, db: Session = Depends(get_db)):
    """Deliver the operator's pasted external-model response to a waiting swarm. The
    orchestrator parses '--- suggestion: {agent} ---' blocks, routes each directive by
    its own label, and resumes. Any flag still passes the normal validation gate."""
    from backend.agents.swarm_orchestrator import swarm_orchestrator
    result = await swarm_orchestrator.submit_checkpoint_response(challenge_id, req.text or "")
    if not result.get("accepted"):
        raise HTTPException(status_code=409, detail=result.get("reason", "No active checkpoint is awaiting a response."))
    await ws_manager.broadcast({
        "event": "CHECKPOINT_RESPONSE_ACCEPTED",
        "challenge_id": challenge_id,
        "parsed": result.get("parsed"),
        "routed": result.get("routed", []),
        "fallback": result.get("fallback"),
    })
    return result


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


# NOTE: The guarded DELETE /challenges and DELETE /challenges/{challenge_id}
# endpoints are defined earlier in this file (see delete_challenge /
# delete_all_challenges near the top). Duplicate unguarded definitions that
# called shutil.rmtree(working_directory) with no path validation used to live
# here and were removed — they were the destructive path that could wipe the
# project root. Do not reintroduce disk deletion without _safe_delete_working_dir.

@router.post("/challenges/{challenge_id}/pause")
async def pause_challenge(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    # Gracefully suspend any live swarm for this challenge and persist a resume
    # snapshot. run_swarm finalizes the run as PAUSED; a later /start resumes it.
    from backend.agents.swarm_orchestrator import swarm_orchestrator
    paused_live = await swarm_orchestrator.request_pause(challenge_id)

    challenge.status = "PAUSED"
    db.commit()
    await ws_manager.broadcast({"event": "CHALLENGE_PAUSED", "challenge_id": challenge_id, "paused_live_swarm": paused_live})
    return {"status": "PAUSED", "id": challenge_id, "paused_live_swarm": paused_live}

@router.post("/challenges/{challenge_id}/resume")
async def resume_challenge(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    challenge.status = "RUNNING"
    db.commit()
    await ws_manager.broadcast({"event": "CHALLENGE_RESUMED", "challenge_id": challenge_id})
    return {"status": "RUNNING", "id": challenge_id}

@router.post("/challenges/{challenge_id}/report")
async def generate_report(challenge_id: str, db: Session = Depends(get_db)):
    """Author a detailed technical writeup (Gemini-first AI chain, deterministic
    fallback) from the real run telemetry and persist it under reports/."""
    content, generated_by = await report_generator.craft_writeup(db, challenge_id)
    if not content:
        raise HTTPException(status_code=404, detail="Could not generate report for challenge")
    report_path = report_generator.save_writeup(db, challenge_id, content, output_dir="reports")
    return {"status": "GENERATED", "file_path": report_path,
            "content": content, "generated_by": generated_by}


@router.get("/challenges/{challenge_id}/writeup")
async def get_writeup(challenge_id: str, refresh: bool = False, db: Session = Depends(get_db)):
    """Return the challenge writeup.

    Task #2: once the operator has SAVED a writeup, this returns THAT saved artifact
    (``saved=true``) instead of authoring a brand-new one on every open. Pass
    ``?refresh=1`` to force a fresh AI-crafted draft (Gemini-first for the
    report_generation capability; provider-chain fallback, then a deterministic
    writeup from the same real telemetry — never fabricated or empty filler).
    A saved writeup is NOT overwritten until the operator explicitly saves again.
    """
    if not refresh:
        saved = report_generator.load_saved_writeup(db, challenge_id)
        if saved is not None:
            content, file_path = saved
            return {"content": content, "generated_by": "saved",
                    "saved": True, "file_path": file_path}

    content, generated_by = await report_generator.craft_writeup(db, challenge_id)
    if not content:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return {"content": content, "generated_by": generated_by, "saved": False}


@router.post("/challenges/{challenge_id}/writeup/save")
def save_writeup_endpoint(challenge_id: str, req: SaveWriteupRequest,
                          db: Session = Depends(get_db)):
    """Persist the operator-confirmed writeup markdown into the challenge working
    folder (path-safety enforced by backend.utils.workspace)."""
    content = req.content
    if content is None:
        ctx = report_generator.gather_context(db, challenge_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail="Challenge not found")
        content = report_generator.render_deterministic(ctx)
    report_path = report_generator.save_writeup(db, challenge_id, content)
    if not report_path:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return {"status": "SAVED", "file_path": report_path,
            "content": content, "generated_by": "saved", "saved": True}

# ----------------------------------------------------
# TARGET IDENTITY MANAGEMENT (TARGET IP ≠ IDENTITY)
# ----------------------------------------------------

@router.get("/targets")
def list_targets(db: Session = Depends(get_db)):
    targets = db.query(TargetProfileModel).all()
    out = []
    for t in targets:
        svcs = t.expected_services or []
        if not svcs:
            svcs = [{"port": 80, "proto": "tcp", "service": "HTTP", "version": "Target Server"}]
        techs = getattr(t, "technologies", None) or ["Linux", "HTTP"]
        addr_hist = getattr(t, "address_history", None) or [t.current_address]
        disc_method = getattr(t, "discovery_method", None) or "FORGE Auto Ingest"
        last_ver = t.last_verified_at.isoformat() if t.last_verified_at else None
        
        out.append({
            "id": t.id,
            "challenge_id": t.challenge_id,
            "current_address": t.current_address,
            "hostname": t.hostname or t.current_address,
            "expected_services": svcs,
            "technologies": techs,
            "address_history": addr_hist,
            "discovery_method": disc_method,
            "verification_status": t.verification_status,
            "last_verified_at": last_ver
        })
    return out


@router.get("/targets/{target_id}")
def get_target(target_id: str, db: Session = Depends(get_db)):
    target = db.query(TargetProfileModel).filter(TargetProfileModel.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target identity not found")
    return target

@router.post("/targets/{target_id}/verify")
async def verify_target(target_id: str, db: Session = Depends(get_db)):
    target = db.query(TargetProfileModel).filter(TargetProfileModel.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target identity not found")
    
    is_file = os.path.exists(target.current_address)
    target.verification_status = "verified_file" if is_file else "verified_network"
    target.last_verified_at = datetime.utcnow()
    db.commit()

    await ws_manager.broadcast({"event": "TARGET_VERIFIED", "target_id": target_id, "address": target.current_address, "status": target.verification_status})
    return target

@router.post("/targets/{target_id}/rediscover")
async def rediscover_target(target_id: str, db: Session = Depends(get_db)):
    target = db.query(TargetProfileModel).filter(TargetProfileModel.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target identity not found")
    
    target.verification_status = "rediscovered"
    target.last_verified_at = datetime.utcnow()
    db.commit()

    await ws_manager.broadcast({"event": "TARGET_REDISCOVERED", "target_id": target_id, "address": target.current_address})
    return target

@router.put("/targets/{target_id}/address")
async def update_target_address(target_id: str, req: UpdateTargetAddressRequest, db: Session = Depends(get_db)):
    target = db.query(TargetProfileModel).filter(TargetProfileModel.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target identity not found")
    
    old_addr = target.current_address
    target.current_address = req.new_address
    target.verification_status = "address_updated"
    target.last_verified_at = datetime.utcnow()
    db.commit()

    await ws_manager.broadcast({
        "event": "TARGET_ADDRESS_UPDATED",
        "target_id": target_id,
        "old_address": old_addr,
        "new_address": req.new_address
    })
    return target

# ----------------------------------------------------
# RUNS & CHECKPOINTS
# ----------------------------------------------------

@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    return db.query(RunModel).all()

@router.post("/runs/{challenge_id}/start")
async def start_run(challenge_id: str, engine: Optional[str] = Query(default=None), db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    target = db.query(TargetProfileModel).filter(TargetProfileModel.challenge_id == challenge_id).first()
    target_addr = target.current_address if target else "127.0.0.1"

    # Decide fresh vs resume: if the challenge already has progress — a persisted
    # blackboard snapshot, a non-zero progress bar, or prior tool executions — the
    # swarm resumes aware of that work; otherwise it starts fresh.
    mission_plan = challenge.mission_plan or {}
    has_prior_exec = (
        db.query(ToolExecutionModel.id)
        .join(RunModel, ToolExecutionModel.run_id == RunModel.id)
        .filter(RunModel.challenge_id == challenge_id)
        .first() is not None
    )
    resume = bool(mission_plan.get("blackboard_state")) or (challenge.progress or 0) > 0 or has_prior_exec

    run = RunModel(
        challenge_id=challenge_id,
        status="RUNNING",
        current_phase="recon",
        current_agent=(engine or "orchestrator")
    )
    db.add(run)
    challenge.status = "RUNNING"
    db.commit()
    db.refresh(run)

    workflow_runner.start_run(run.id, challenge_id, target_addr, engine_type=engine, resume=resume)

    await ws_manager.broadcast({
        "event": "RUN_STARTED",
        "run_id": run.id,
        "challenge_id": challenge_id,
        "target": target_addr,
        "engine": engine or "swarm",
        "resume": resume
    })

    if challenge.mission_plan:
        await ws_manager.broadcast({
            "event": "PLAN_GENERATED",
            "challenge_id": challenge.id,
            "plan": challenge.mission_plan
        })

    return run

@router.get("/checkpoints")
def list_checkpoints(db: Session = Depends(get_db)):
    return db.query(CheckpointModel).order_by(CheckpointModel.created_at.desc()).all()

@router.post("/checkpoints/{checkpoint_id}/resume")
async def resume_checkpoint(checkpoint_id: str, db: Session = Depends(get_db)):
    checkpoint = db.query(CheckpointModel).filter(CheckpointModel.id == checkpoint_id).first()
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    
    run = db.query(RunModel).filter(RunModel.id == checkpoint.run_id).first()
    if run:
        run.status = "RUNNING"
        db.commit()

    await ws_manager.broadcast({"event": "CHECKPOINT_RESUMED", "checkpoint_id": checkpoint_id})
    return {"status": "RESUMED", "checkpoint_id": checkpoint_id}

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

@router.get("/agents")
def get_agents():
    """Live swarm worker fleet state. Empty when no swarms are active."""
    from backend.agents.swarm_orchestrator import swarm_orchestrator
    agents = []
    for _run_id, board in swarm_orchestrator.active_swarms.items():
        agents.extend(board._build_agent_states())
    return agents


# ============================================================================ #
# Phase 4 — coordinated swarm observability (§13). Read-only views of the
# supervisor + specialist agents, task queue, evidence bus, and mission state.
# Serves the LIVE coordinator when a mission is running, else the durable rows.
# ============================================================================ #

def _swarm_task_dict(t) -> dict:
    return {
        "id": t.id, "mission_id": t.mission_id, "role": t.role, "assigned_agent": t.assigned_agent,
        "objective": t.objective, "priority": t.priority, "status": t.status,
        "dependencies": t.dependencies or [], "evidence_ids": t.evidence_ids or [],
        "retry_count": t.retry_count, "parent_task_id": t.parent_task_id,
        "failure_reason": t.failure_reason, "agent_session_id": t.agent_session_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def _swarm_evidence_dict(e) -> dict:
    return {
        "id": e.id, "mission_id": e.mission_id, "agent_id": e.agent_id, "task_id": e.task_id,
        "type": e.evidence_type, "title": e.title, "description": e.description,
        "source": e.source, "confidence": e.confidence, "tags": e.tags or [],
        "related_endpoint": e.related_endpoint, "related_technology": e.related_technology,
        "related_vulnerability": e.related_vulnerability,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _assemble_swarm_snapshot(mission_row, db) -> dict:
    tasks = (db.query(SwarmTaskModel)
             .filter(SwarmTaskModel.mission_id == mission_row.id)
             .order_by(SwarmTaskModel.created_at.asc()).all())
    evidence = (db.query(SwarmEvidenceModel)
                .filter(SwarmEvidenceModel.mission_id == mission_row.id)
                .order_by(SwarmEvidenceModel.created_at.desc()).all())
    state = mission_row.shared_state or {}
    counts: dict = {"total": len(tasks)}
    for t in tasks:
        counts[t.status] = counts.get(t.status, 0) + 1
    return {
        "mission_id": mission_row.id, "run_id": mission_row.run_id,
        "challenge_id": mission_row.challenge_id, "status": mission_row.status,
        "progress": mission_row.progress, "strategy": mission_row.strategy,
        "verified_flag": mission_row.verified_flag, "shared_state": state,
        "agents": [{"agent_id": aid, **(info or {})}
                   for aid, info in (state.get("agent_statuses") or {}).items()],
        "tasks": [_swarm_task_dict(t) for t in tasks],
        "task_counts": counts,
        "evidence_count": len(evidence),
        "evidence": [_swarm_evidence_dict(e) for e in evidence[:100]],
        "live": False,
    }


def _live_swarm_snapshot(coord) -> dict:
    snap = coord.snapshot()
    try:
        snap["evidence"] = [e.to_dict() for e in coord.bus.all()[:100]]
    except Exception:
        snap["evidence"] = []
    snap["live"] = True
    return snap


@router.get("/swarm/missions")
def list_swarm_missions(db: Session = Depends(get_db)):
    """All coordinated missions (most recent first)."""
    rows = db.query(SwarmMissionModel).order_by(SwarmMissionModel.created_at.desc()).all()
    return [{
        "mission_id": r.id, "run_id": r.run_id, "challenge_id": r.challenge_id,
        "status": r.status, "progress": r.progress, "verified_flag": r.verified_flag,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.get("/swarm/missions/{mission_id}")
def get_swarm_mission(mission_id: str, db: Session = Depends(get_db)):
    """Full mission snapshot — live coordinator if running, else durable rows."""
    try:
        from backend.swarm.coordinator import active_missions
        coord = active_missions.get(mission_id)
        if coord is not None:
            return _live_swarm_snapshot(coord)
    except Exception:
        pass
    row = db.query(SwarmMissionModel).filter(SwarmMissionModel.id == mission_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Mission not found")
    return _assemble_swarm_snapshot(row, db)


@router.get("/swarm/challenges/{challenge_id}")
def get_swarm_for_challenge(challenge_id: str, db: Session = Depends(get_db)):
    """Latest coordinated mission for a challenge (live if running)."""
    try:
        from backend.swarm.coordinator import active_missions
        coord = active_missions.get(challenge_id)
        if coord is not None:
            return _live_swarm_snapshot(coord)
    except Exception:
        pass
    row = (db.query(SwarmMissionModel)
           .filter(SwarmMissionModel.challenge_id == challenge_id)
           .order_by(SwarmMissionModel.created_at.desc()).first())
    if not row:
        return {"mission_id": None, "challenge_id": challenge_id, "status": "NONE",
                "tasks": [], "evidence": [], "agents": [], "evidence_count": 0,
                "task_counts": {"total": 0}, "live": False}
    return _assemble_swarm_snapshot(row, db)

# Capabilities that spawn a process from a caller-supplied string rather than running a
# fixed, audited binary. For these, `target` IS the command line (see
# ToolManager.execute_capability → interactive_open), so it must be classified and gated
# as a command — not by the capability name, which the registry marks SAFE.
SHELL_CAPABILITIES = frozenset({"interactive_open", "interactive_start"})


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

@router.post("/terminal/execute")
async def execute_terminal_command(req: TerminalExecuteRequest):
    start_time = datetime.utcnow()
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


@router.get("/capabilities")
def list_capabilities():
    """Phase 4.x — discovered capabilities in this environment (read-only).

    Each entry reports availability, the chosen/alternative providers, and the
    recommended action (execute / request_acquisition / replan). Nothing is spawned
    or installed; discovery only inspects PATH and importable libraries.
    """
    from backend.execution.capabilities import capability_service
    return {"capabilities": capability_service.summary()}


@router.get("/capabilities/{name}")
def get_capability(name: str):
    """Discover a single capability by name (read-only)."""
    from backend.execution.capabilities import capability_service
    cap = capability_service.discover(name)
    return cap.to_dict()


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
# PRIVILEGE MANAGER
# ----------------------------------------------------

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

# ----------------------------------------------------
# PROVIDERS & MODEL ROUTER
# ----------------------------------------------------

@router.get("/providers")
def get_providers(db: Session = Depends(get_db)):
    from backend.providers.quota_manager import quota_manager
    in_claude_window = quota_manager.is_in_claude_allowed_window()
    next_batch_str = quota_manager.get_next_batch_time_str()

    # Pre-aggregate usage counts and last errors per provider
    usage_counts = {}
    last_errors = {}
    try:
        from sqlalchemy import func
        counts = db.query(ProviderUsageModel.provider_name, func.count(ProviderUsageModel.id)).group_by(ProviderUsageModel.provider_name).all()
        usage_counts = {pname: cnt for pname, cnt in counts}

        err_rows = (db.query(ProviderUsageModel)
                    .filter(ProviderUsageModel.success == False)
                    .order_by(ProviderUsageModel.timestamp.desc())
                    .all())
        for er in err_rows:
            if er.provider_name not in last_errors:
                last_errors[er.provider_name] = f"Last failure at {er.timestamp.strftime('%H:%M:%S')}" if er.timestamp else "Request error"
    except Exception as e:
        logger.debug(f"[get_providers] DB aggregate skip: {e}")

    # Derive fallback priorities from router routing map order
    priority_order = model_router.DEFAULT_ROUTING_MAP.get("general_reasoning", [])

    provider_list = []
    for idx, p in enumerate(model_router.providers.values()):
        models_under_provider = [m for m, (pname, _) in model_router.MODEL_PROVIDER_MAP.items() if pname == p.name]
        is_quota_limited = any(quota_manager.is_quota_limited_model(m) for m in models_under_provider)
        
        quota_label = "100% Available"
        if is_quota_limited:
            if in_claude_window:
                quota_label = "3h Window Active (Trial Batch)"
            else:
                quota_label = f"Standby (Next Reset: {next_batch_str})"
        elif p.name == "agentrouter_codex":
            quota_label = "∞ Always Available (No Limit)"

        # Fallback priority index (1-based index in router priority list, or default)
        priority = (priority_order.index(p.name) + 1) if p.name in priority_order else (idx + 10)
        req_count = usage_counts.get(p.name, getattr(p, "request_count", 0))
        err_msg = last_errors.get(p.name, getattr(p, "last_error", "None"))

        provider_list.append({
            "name": p.name,
            "is_paid": p.is_paid,
            "status": "HEALTHY",
            "models": models_under_provider,
            "default_model": getattr(p, "default_model", getattr(p, "cli_binary_default", p.name)),
            "quota": quota_label,
            "is_quota_limited": is_quota_limited,
            "in_batch_window": in_claude_window if is_quota_limited else True,
            "transport": "CLI" if "agentrouter" in p.name else "API",
            "requests": req_count,
            "last_error": err_msg,
            "fallback_priority": priority
        })
    return provider_list


@router.get("/providers/health")
def get_providers_health():
    from backend.providers.quota_manager import quota_manager
    in_claude_window = quota_manager.is_in_claude_allowed_window()

    return {
        "paid_allowed": model_router.paid_allowed,
        "budget_usd": model_router.daily_budget_usd,
        "spent_usd": model_router.current_spent_usd,
        "in_claude_allowed_window": in_claude_window,
        "next_batch_replenishment": quota_manager.get_next_batch_time_str(),
        "registered_models": [
            {
                "model_id": model_id,
                "provider": p_info[0],
                "transport": p_info[1],
                "is_quota_limited": quota_manager.is_quota_limited_model(model_id),
                "is_always_available": quota_manager.is_always_available_model(model_id) or not quota_manager.is_quota_limited_model(model_id),
                "status": "ACTIVE" if (not quota_manager.is_quota_limited_model(model_id) or in_claude_window) else "STANDBY (Outside 3h Window)"
            }
            for model_id, p_info in model_router.MODEL_PROVIDER_MAP.items()
        ],
        "providers": [
            {
                "name": p.name,
                "is_paid": p.is_paid,
                "status": "HEALTHY",
                "default_model": getattr(p, "default_model", ""),
                "latency_ms": 120 if "cerebras" in p.name else (680 if "codex" in p.name else 420)
            }
            for p in model_router.providers.values()
        ]
    }

@router.post("/providers/parse-snippet")
def parse_provider_snippet(req: ParseSnippetRequest):
    result = SnippetParser.parse_snippet(req.snippet)
    return result

@router.post("/providers/register-snippet")
async def register_provider_snippet(req: RegisterSnippetRequest):
    if req.snippet:
        parsed = SnippetParser.parse_snippet(req.snippet)
        if not parsed.get("success"):
            raise HTTPException(status_code=400, detail="Could not parse API key or model from snippet.")
        api_key = parsed.get("api_key")
        model_id = parsed.get("model") or req.model_id or "default"
        base_url = parsed.get("base_url") or req.base_url or "https://integrate.api.nvidia.com/v1"
        provider_name = parsed.get("provider_name") or req.provider_name or "nvidia"
    else:
        if not req.api_key:
            raise HTTPException(status_code=400, detail="API key is required.")
        api_key = req.api_key
        model_id = req.model_id or "default"
        base_url = req.base_url or "https://integrate.api.nvidia.com/v1"
        provider_name = req.provider_name or "nvidia"

    # Register in router
    provider = model_router.register_custom_model(
        provider_name=provider_name,
        api_key=api_key,
        model_id=model_id,
        base_url=base_url
    )

    test_status = "untested"
    latency_ms = 0
    test_response = ""

    if req.test_connection:
        start_time = asyncio.get_event_loop().time()
        try:
            res = await provider.generate_response(
                prompt="Say 'pong'",
                model=model_id,
                capability="general_reasoning"
            )
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            if res.is_refusal:
                test_status = "failed"
                test_response = res.refusal_reason or "Refusal"
            else:
                test_status = "healthy"
                test_response = res.content[:200]
        except Exception as e:
            test_status = "failed"
            test_response = str(e)

    return {
        "success": True,
        "provider_name": provider_name,
        "model_id": model_id,
        "base_url": base_url,
        "api_key_masked": api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***",
        "test_status": test_status,
        "latency_ms": latency_ms,
        "test_response": test_response
    }

@router.post("/providers/update-key")
async def update_provider_key(req: UpdateProviderKeyRequest):
    p_name = req.provider_name.lower()
    if p_name in model_router.providers:
        prov = model_router.providers[p_name]
        if hasattr(prov, 'api_key'):
            prov.api_key = req.api_key
        if req.model_id and hasattr(prov, 'default_model'):
            prov.default_model = req.model_id
        if req.base_url and hasattr(prov, 'base_url'):
            prov.base_url = req.base_url.rstrip("/")
    else:
        model_router.register_custom_model(
            provider_name=p_name,
            api_key=req.api_key,
            model_id=req.model_id or "default",
            base_url=req.base_url or "https://integrate.api.nvidia.com/v1"
        )
    return {"success": True, "provider_name": req.provider_name, "status": "updated"}

# ----------------------------------------------------
# EVIDENCE, FINDINGS, REPORTS & KNOWLEDGE
# ----------------------------------------------------

@router.get("/evidence")
def list_evidence(db: Session = Depends(get_db)):
    return db.query(EvidenceModel).order_by(EvidenceModel.created_at.desc()).all()

@router.post("/evidence")
def create_evidence(req: EvidenceCreateRequest, db: Session = Depends(get_db)):
    ev = EvidenceModel(
        challenge_id=req.challenge_id,
        agent=req.agent,
        evidence_type=req.evidence_type,
        source=req.source,
        content=req.content,
        confidence=req.confidence
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev

@router.get("/findings")
def list_findings(db: Session = Depends(get_db)):
    return db.query(FindingModel).order_by(FindingModel.created_at.desc()).all()

@router.post("/findings")
def create_finding(req: FindingCreateRequest, db: Session = Depends(get_db)):
    finding = FindingModel(
        challenge_id=req.challenge_id,
        agent=req.agent,
        title=req.title,
        description=req.description,
        vulnerability_class=req.vulnerability_class,
        verified=True,
        confidence=0.9
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding

@router.get("/reports")
def list_reports(db: Session = Depends(get_db)):
    return db.query(ReportModel).order_by(ReportModel.created_at.desc()).all()

@router.get("/knowledge")
def list_knowledge(db: Session = Depends(get_db)):
    return db.query(KnowledgeEntryModel).all()

@router.get("/audit-logs")
def list_audit_logs(db: Session = Depends(get_db)):
    return db.query(AuditLogModel).order_by(AuditLogModel.timestamp.desc()).all()

@router.get("/system/requirements")
def check_system_requirements():
    return environment_detector.perform_requirements_audit()

@router.get("/system/settings")
def get_system_settings():
    from backend.config import settings
    return {
        "execution_mode": getattr(settings, "EXECUTION_MODE", "CTF_OFFENSIVE_CONTROLLED"),
        "auto_approve_privileged": getattr(settings, "AUTO_APPROVE_PRIVILEGED", False),
        "command_timeout_seconds": getattr(settings, "COMMAND_TIMEOUT_SECONDS", 300),
        "daily_budget_usd": settings.DAILY_BUDGET_USD,
        "session_budget_usd": settings.SESSION_BUDGET_USD,
        "paid_model_allowed": settings.PAID_MODEL_ALLOWED,
        "default_strategy": getattr(settings, "DEFAULT_STRATEGY", "EXPLOIT_FIRST")
    }

@router.post("/system/settings")
def update_system_settings(req: UpdateSystemSettingsRequest):
    from backend.config import settings
    settings.PAID_MODEL_ALLOWED = req.paid_model_allowed
    settings.DAILY_BUDGET_USD = req.daily_budget_usd
    settings.SESSION_BUDGET_USD = req.session_budget_usd
    setattr(settings, "EXECUTION_MODE", req.execution_mode)
    setattr(settings, "AUTO_APPROVE_PRIVILEGED", req.auto_approve_privileged)
    setattr(settings, "COMMAND_TIMEOUT_SECONDS", req.command_timeout_seconds)
    setattr(settings, "DEFAULT_STRATEGY", req.default_strategy)
    return {
        "status": "SUCCESS",
        "message": "System Settings updated successfully across backend framework.",
        "settings": get_system_settings()
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

# ----------------------------------------------------
# PACKAGE INSTALL APPROVAL SYSTEM
# ----------------------------------------------------

class PackageInstallRequest(BaseModel):
    request_id: str
    package_name: str
    challenge_id: Optional[str] = None

# In-memory registry of pending install requests from the orchestrator
pending_install_requests: dict = {}

@router.post("/package/install")
async def install_package(req: PackageInstallRequest):
    """User-approved pip install for a missing solver dependency."""
    import subprocess, sys, logging
    logger = logging.getLogger("forge.package_installer")

    package_name = req.package_name.strip()
    # Basic safety check: only allow simple package names
    if not all(c.isalnum() or c in '-_.[]=<>!' for c in package_name):
        raise HTTPException(status_code=400, detail=f"Invalid package name: {package_name}")

    logger.info(f"User approved pip install: {package_name}")
    await ws_manager.broadcast({
        "event": "PACKAGE_INSTALL_STARTED",
        "request_id": req.request_id,
        "package_name": package_name,
        "challenge_id": req.challenge_id
    })

    try:
        result = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(result.communicate(), timeout=120)
        stdout_text = stdout_bytes.decode(errors="replace")
        stderr_text = stderr_bytes.decode(errors="replace")
        success = result.returncode == 0

        await ws_manager.broadcast({
            "event": "PACKAGE_INSTALL_RESULT",
            "request_id": req.request_id,
            "package_name": package_name,
            "challenge_id": req.challenge_id,
            "success": success,
            "output": stdout_text[:2000] if success else stderr_text[:2000]
        })

        # Remove from pending queue
        pending_install_requests.pop(req.request_id, None)

        # Signal orchestrator to resume if it was waiting
        from backend.agents.orchestrator_loop import orchestrator_loop
        orchestrator_loop.resolve_install_request(req.request_id, success)

        return {
            "status": "SUCCESS" if success else "FAILED",
            "package_name": package_name,
            "output": stdout_text[:2000] if success else stderr_text[:2000]
        }
    except asyncio.TimeoutError:
        await ws_manager.broadcast({
            "event": "PACKAGE_INSTALL_RESULT",
            "request_id": req.request_id,
            "package_name": package_name,
            "success": False,
            "output": "Installation timed out after 120 seconds."
        })
        return {"status": "TIMEOUT", "package_name": package_name}
    except Exception as e:
        return {"status": "ERROR", "package_name": package_name, "error": str(e)}

class PackageSkipRequest(BaseModel):
    request_id: str
    challenge_id: Optional[str] = None

@router.post("/package/skip")
async def skip_package_install(req: PackageSkipRequest):
    """User skipped or rejected package installation."""
    from backend.agents.orchestrator_loop import orchestrator_loop
    pending_install_requests.pop(req.request_id, None)
    orchestrator_loop.resolve_install_request(req.request_id, False)
    return {"status": "SKIPPED", "request_id": req.request_id}

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


# DIRECTORY BROWSER & CREATOR API
# ----------------------------------------------------

class CreateDirRequest(BaseModel):
    parent_path: str
    dir_name: str

@router.get("/system/browse-dir")
def browse_directory(path: Optional[str] = None):
    if not path or not path.strip():
        current_path = os.getcwd()
    else:
        current_path = os.path.abspath(path.strip())
    
    if not os.path.exists(current_path):
        current_path = os.getcwd()

    parent_path = os.path.dirname(current_path)
    
    drives = []
    if os.name == 'nt':
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
    else:
        drives = ["/"]

    directories = []
    try:
        with os.scandir(current_path) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.'):
                        directories.append(entry.name)
                except OSError:
                    continue
    except OSError:
        pass

    directories.sort()

    return {
        "current_path": current_path,
        "parent_path": parent_path,
        "drives": drives,
        "directories": directories
    }

@router.post("/system/create-dir")
def create_directory(req: CreateDirRequest):
    target_path = os.path.abspath(os.path.join(req.parent_path, req.dir_name.strip()))
    try:
        os.makedirs(target_path, exist_ok=True)
        return {"status": "SUCCESS", "created_path": target_path}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create directory: {str(e)}")

@router.post("/system/select-folder-dialog")
async def open_native_folder_dialog():
    selected_path = ""
    def _open_tkinter():
        nonlocal selected_path
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            selected = filedialog.askdirectory(title="FORGE CTF — Select Challenge Working Directory")
            root.destroy()
            if selected:
                selected_path = os.path.abspath(selected)
        except Exception:
            pass

    await asyncio.to_thread(_open_tkinter)
    if selected_path:
        return {"status": "SUCCESS", "selected_path": selected_path}
    return {"status": "CANCELLED", "selected_path": ""}

# ----------------------------------------------------
# AGENTROUTER QUOTA STATUS
# ----------------------------------------------------

@router.get("/providers/quota-status")
def get_agentrouter_quota_status():
    """
    Returns the current AgentRouter quota status.

    AgentRouter provides Claude & GPT models on a limited daily quota,
    released in 2 batches per day (Beijing 07:00/19:00, UTC 23:00/11:00).
    When a batch is exhausted, HTTP 402 is returned.
    DeepSeek & GLM models are always available (no quota limit).
    """
    return model_router.get_quota_status()

# ----------------------------------------------------
# PLAYBOOK VAULT (Phase 2)
# ----------------------------------------------------

class IngestWriteupRequest(BaseModel):
    text: str
    category: str = "web"
    source_type: str = "raw_text"  # "url", "raw_text", or "file"
    title: Optional[str] = None
    auto_approve: bool = True

class SearchPlaybooksRequest(BaseModel):
    query: str
    category: Optional[str] = None
    top_k: int = 5
    include_unpromoted: bool = False


class MemorySearchRequest(BaseModel):
    query: str = ""
    category: Optional[str] = None
    top_k: int = 6
    include_failures: bool = True


class MemoryFeedbackRequest(BaseModel):
    success: bool
    note: str = ""
    run_id: Optional[str] = None
    challenge_id: Optional[str] = None

@router.post("/playbooks/ingest")
async def ingest_writeup(req: IngestWriteupRequest):
    """Ingest a CTF writeup (from URL, raw text, or markdown file) into the Playbook Vault."""
    from backend.knowledge.ingest_writeup import ingest_url, ingest_raw_text
    
    if not req.text or len(req.text.strip()) < 5:
        raise HTTPException(status_code=400, detail="Source text or URL is required.")
        
    source_str = req.text.strip()
    if req.source_type == "url" or source_str.startswith("http://") or source_str.startswith("https://"):
        try:
            pb = ingest_url(source_str, category=req.category, title=req.title)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch and parse URL: {str(e)}")
    else:
        pb = ingest_raw_text(source_str, category=req.category, title=req.title)
        
    return {
        "status": "INGESTED",
        "playbook_id": pb.id,
        "category": pb.category,
        "tags": pb.tags,
        "is_promoted": pb.is_promoted,
        "playbook": pb.model_dump()
    }

@router.post("/playbooks/search")
def search_playbooks(req: SearchPlaybooksRequest):
    """Search the Playbook Vault using FTS5 index."""
    from backend.knowledge.playbook_vault import playbook_vault
    results = playbook_vault.search_playbooks(req.query, req.category, req.top_k, req.include_unpromoted)
    return {
        "query": req.query,
        "count": len(results),
        "playbooks": [pb.model_dump() for pb in results]
    }

@router.get("/playbooks")
def list_playbooks():
    """List all indexed playbooks from the vault."""
    from backend.knowledge.playbook_vault import playbook_vault
    playbook_vault.reload_index()
    all_pbs = []
    import yaml
    for root, _, files in os.walk(playbook_vault.base_dir):
        for file in files:
            if file.endswith(".yaml") or file.endswith(".yml"):
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        if isinstance(data, dict) and "id" in data:
                            all_pbs.append(data)
                except Exception:
                    pass
    return {"count": len(all_pbs), "playbooks": all_pbs}


# -------------------------------------------------------------------
# KNOWLEDGE COVERAGE OBSERVABILITY
# -------------------------------------------------------------------

COVERAGE_CATEGORIES = [
    "web", "pwn", "reverse", "crypto", "forensics",
    "osint", "network", "mobile", "cloud", "hardware", "ai_llm", "misc"
]

CATEGORY_ALIASES = {
    "web": "web", "pwn": "pwn", "binary": "pwn", "bof": "pwn",
    "reverse": "reverse", "rev": "reverse", "reversing": "reverse",
    "crypto": "crypto", "cryptography": "crypto",
    "forensics": "forensics", "stego": "forensics", "steganography": "forensics", "memory": "forensics",
    "osint": "osint", "recon": "osint",
    "network": "network", "pcap": "network", "wireshark": "network",
    "mobile": "mobile", "android": "mobile", "ios": "mobile",
    "cloud": "cloud", "aws": "cloud", "azure": "cloud", "gcp": "cloud",
    "hardware": "hardware", "iot": "hardware", "embedded": "hardware",
    "ai_llm": "ai_llm", "ai": "ai_llm", "llm": "ai_llm", "ml": "ai_llm",
    "misc": "misc", "auto_generated": "misc", "pending_review": "misc"
}

def _normalize_category(raw_cat: str) -> str:
    return CATEGORY_ALIASES.get(raw_cat.lower().strip(), "misc")


@router.get("/knowledge/coverage")
def get_knowledge_coverage():
    """Returns per-category coverage stats for the Knowledge Coverage observability view."""
    import yaml as _yaml
    from backend.knowledge.playbook_vault import playbook_vault

    # Initialize empty stats for all 12 categories
    stats = {}
    for cat in COVERAGE_CATEGORIES:
        stats[cat] = {
            "category": cat,
            "total": 0,
            "by_source": {},
            "by_confidence_tier": {"pending": 0, "low": 0, "trusted": 0},
            "tags": {},  # tag -> count
        }

    # Scan all playbook YAML files
    for root, _, files in os.walk(playbook_vault.base_dir):
        for file in files:
            if not (file.endswith(".yaml") or file.endswith(".yml")):
                continue
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f)
                if not isinstance(data, dict) or "id" not in data:
                    continue

                raw_cat = data.get("category", "misc")
                cat = _normalize_category(raw_cat)
                source = data.get("source", "ingested")
                conf = float(data.get("confidence_score", 0.5))
                tags = data.get("tags", [])

                bucket = stats[cat]
                bucket["total"] += 1

                # Source breakdown
                bucket["by_source"][source] = bucket["by_source"].get(source, 0) + 1

                # Confidence tier
                if conf < 0.5:
                    bucket["by_confidence_tier"]["pending"] += 1
                elif conf < 0.8:
                    bucket["by_confidence_tier"]["low"] += 1
                else:
                    bucket["by_confidence_tier"]["trusted"] += 1

                # Tag frequency
                if isinstance(tags, list):
                    for tag in tags:
                        tag_str = str(tag).lower().strip()
                        if tag_str:
                            bucket["tags"][tag_str] = bucket["tags"].get(tag_str, 0) + 1
            except Exception:
                continue

    # Build sorted response (lowest coverage first)
    categories_list = sorted(stats.values(), key=lambda c: c["total"])

    # Convert tag dicts to sorted lists for frontend
    for cat_data in categories_list:
        tag_dict = cat_data.pop("tags")
        cat_data["distinct_tags"] = sorted(
            [{"tag": t, "count": c} for t, c in tag_dict.items()],
            key=lambda x: x["count"],
            reverse=True
        )

    grand_total = sum(c["total"] for c in categories_list)

    return {
        "grand_total": grand_total,
        "categories": categories_list
    }


# =============================================================================
# EXPERIENCE MEMORY — FORGE-learned experience layer (§14)
# Read/search/feedback over experiences distilled from real runs. No demo data:
# every row originates from a verified/failed FORGE execution.
# =============================================================================

@router.get("/memory")
def list_memory(limit: int = Query(100), category: Optional[str] = None, outcome: Optional[str] = None):
    """Memory dashboard payload: aggregate stats + the experience list (§15)."""
    from backend.knowledge.experience_memory import experience_memory
    return {
        "stats": experience_memory.get_stats(),
        "experiences": experience_memory.list_experiences(limit=limit, category=category, outcome=outcome),
    }


@router.get("/memory/stats")
def memory_stats():
    """Aggregate memory counters + leaderboards for the Memory UI header (§15)."""
    from backend.knowledge.experience_memory import experience_memory
    return experience_memory.get_stats()


@router.post("/memory/search")
def search_memory(req: MemorySearchRequest):
    """Unified memory search (FORGE experience + reference playbooks), ranked (§6, §7)."""
    from backend.knowledge.memory_retriever import memory_retriever
    memories = memory_retriever.retrieve(
        query=req.query, category=req.category, top_k=req.top_k, include_failures=req.include_failures
    )
    return {"query": req.query, "count": len(memories), "memories": [m.model_dump() for m in memories]}


@router.get("/memory/technique-stats")
def technique_statistics(
    technique: str = Query(..., description="Technique/strategy label to look up"),
    category: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    technologies: Optional[str] = Query(None, description="Comma-separated technology list"),
):
    """Phase 6 §9/§13 — global + contextual success statistics for a learned technique.

    Answers "how well has this technique worked, overall vs. against targets like the
    current one?" — the observability behind why a memory-sourced candidate scored the
    way it did. Deterministic aggregation over stored experiences; no demo data.
    """
    from backend.knowledge.technique_stats import technique_stats
    techs = [t.strip() for t in (technologies or "").split(",") if t.strip()]
    return technique_stats.lookup(technique, category=category, target_type=target_type,
                                  technologies=techs)


@router.get("/memory/{experience_id}")
def get_memory(experience_id: str):
    """Full experience detail incl. attempts + usage log + provenance (§13)."""
    from backend.knowledge.experience_memory import experience_memory
    exp = experience_memory.get(experience_id, with_children=True)
    if not exp:
        raise HTTPException(status_code=404, detail="Experience not found")
    return exp


@router.post("/memory/{experience_id}/feedback")
def memory_feedback(experience_id: str, req: MemoryFeedbackRequest):
    """Record whether a retrieved memory actually helped — updates its stats (§12)."""
    from backend.knowledge.experience_memory import experience_memory
    ok = experience_memory.record_feedback(
        experience_id, success=req.success, note=req.note, run_id=req.run_id, challenge_id=req.challenge_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Experience not found")
    return {"status": "RECORDED", "experience_id": experience_id, "success": req.success}


@router.get("/experiences")
def list_experiences_alias(limit: int = Query(100), category: Optional[str] = None, outcome: Optional[str] = None):
    """Alias returning the raw experience list (§14)."""
    from backend.knowledge.experience_memory import experience_memory
    return experience_memory.list_experiences(limit=limit, category=category, outcome=outcome)
