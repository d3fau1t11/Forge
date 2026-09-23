"""Challenge lifecycle: CRUD, control (pause/resume), log, plan, candidates,
decisions, derived artifacts, writeup and report routes."""

import asyncio
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.runner import workflow_runner
from backend.database.models import (
    ChallengeModel,
    ReportModel,
    RunModel,
    TargetProfileModel,
    TrajectoryEventModel,
)
from backend.database.session import get_db
from backend.reporting.generator import report_generator
from backend.utils.workspace import CTF_WORKSPACE_ROOT, is_deletable_working_dir
from backend.websocket.manager import ws_manager

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


class SaveWriteupRequest(BaseModel):
    # The operator-confirmed markdown to persist. When omitted, the backend
    # regenerates a deterministic writeup from the challenge's real telemetry.
    content: Optional[str] = None


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
