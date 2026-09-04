import os
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from backend.database.session import get_db
from backend.database.models import (
    ChallengeModel, TargetProfileModel, RunModel, AgentStateModel,
    CheckpointModel, ToolExecutionModel, FindingModel, EvidenceModel,
    ReportModel, KnowledgeEntryModel, ProviderUsageModel, AuditLogModel,
    ProviderConfigModel
)
from backend.environment.detector import environment_detector
from backend.providers.router import model_router
from backend.tools.registry import tool_registry
from backend.tools.manager import tool_manager
from backend.api.runner import workflow_runner
from backend.websocket.manager import ws_manager
from backend.reporting.generator import report_generator
from backend.privilege.manager import privilege_manager

router = APIRouter()

# Request Models
class CreateChallengeRequest(BaseModel):
    name: str
    category: str = "WEB"
    difficulty: str = "MEDIUM"
    description: str = ""
    target_address: str
    working_directory: Optional[str] = ""
    platform_name: Optional[str] = ""

class UpdateTargetAddressRequest(BaseModel):
    new_address: str

class ExecuteToolRequest(BaseModel):
    capability: str
    target: str

class TerminalExecuteRequest(BaseModel):
    command: str
    challenge_id: Optional[str] = None
    working_directory: Optional[str] = None

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

    challenge = ChallengeModel(
        name=name,
        category=category,
        difficulty=difficulty,
        description=req.description,
        working_directory=working_dir,
        platform_name=platform,
        status="RUNNING"
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    multi_targets = [t.strip() for t in req.target_address.replace("+", ",").split(",") if t.strip()]
    first_target = multi_targets[0] if multi_targets else req.target_address
    is_file = os.path.exists(first_target) or len(multi_targets) > 1
    target = TargetProfileModel(
        challenge_id=challenge.id,
        current_address=req.target_address,
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

    workflow_runner.start_run(run.id, challenge.id, req.target_address)

    # Initialize Challenge Dedicated Log File
    logs_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
    os.makedirs(logs_dir, exist_ok=True)
    ch_log_path = os.path.join(logs_dir, f"challenge_{challenge.id}.log")
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

    await ws_manager.broadcast({
        "event": "CHALLENGE_CREATED",
        "challenge_id": challenge.id,
        "name": challenge.name,
        "target": req.target_address,
        "working_directory": working_dir,
        "log_file": ch_log_path
    })

    return challenge

@router.delete("/challenges")
def delete_all_challenges(db: Session = Depends(get_db)):
    challenges = db.query(ChallengeModel).all()
    for ch in challenges:
        if ch.working_directory and os.path.exists(ch.working_directory):
            try:
                import shutil
                shutil.rmtree(ch.working_directory, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to delete directory {ch.working_directory}: {e}")

    db.query(TargetProfileModel).delete()
    db.query(RunModel).delete()
    db.query(EvidenceModel).delete()
    db.query(FindingModel).delete()
    db.query(ChallengeModel).delete()
    db.commit()
    return {"status": "ALL_CHALLENGES_DELETED"}

@router.delete("/challenges/{challenge_id}")
def delete_challenge(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    if challenge.working_directory and os.path.exists(challenge.working_directory):
        try:
            import shutil
            shutil.rmtree(challenge.working_directory, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to delete working directory {challenge.working_directory}: {e}")

    db.query(TargetProfileModel).filter(TargetProfileModel.challenge_id == challenge_id).delete()
    db.query(RunModel).filter(RunModel.challenge_id == challenge_id).delete()
    db.query(EvidenceModel).filter(EvidenceModel.challenge_id == challenge_id).delete()
    db.query(FindingModel).filter(FindingModel.challenge_id == challenge_id).delete()
    db.delete(challenge)
    db.commit()
    return {"status": "DELETED", "id": challenge_id}

@router.post("/challenges/{challenge_id}/pause")
async def pause_challenge(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    challenge.status = "PAUSED"
    db.commit()
    await ws_manager.broadcast({"event": "CHALLENGE_PAUSED", "challenge_id": challenge_id})
    return {"status": "PAUSED", "id": challenge_id}

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
def generate_report(challenge_id: str, db: Session = Depends(get_db)):
    report_path = report_generator.generate_readme(db, challenge_id)
    if not report_path:
        raise HTTPException(status_code=404, detail="Could not generate report for challenge")
    
    report_entry = ReportModel(
        challenge_id=challenge_id,
        title=f"README_{challenge_id}.md",
        file_path=report_path
    )
    db.add(report_entry)
    db.commit()
    
    return {"status": "GENERATED", "file_path": report_path}

# ----------------------------------------------------
# TARGET IDENTITY MANAGEMENT (TARGET IP ≠ IDENTITY)
# ----------------------------------------------------

@router.get("/targets")
def list_targets(db: Session = Depends(get_db)):
    return db.query(TargetProfileModel).all()

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
async def start_run(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    target = db.query(TargetProfileModel).filter(TargetProfileModel.challenge_id == challenge_id).first()
    target_addr = target.current_address if target else "127.0.0.1"

    run = RunModel(
        challenge_id=challenge_id,
        status="RUNNING",
        current_phase="recon",
        current_agent="orchestrator"
    )
    db.add(run)
    challenge.status = "RUNNING"
    db.commit()
    db.refresh(run)

    workflow_runner.start_run(run.id, challenge_id, target_addr)

    await ws_manager.broadcast({
        "event": "RUN_STARTED",
        "run_id": run.id,
        "challenge_id": challenge_id,
        "target": target_addr
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
def list_tool_executions(db: Session = Depends(get_db)):
    return db.query(ToolExecutionModel).order_by(ToolExecutionModel.created_at.desc()).all()

@router.post("/tools/execute")
async def execute_tool(req: ExecuteToolRequest):
    result = await tool_manager.execute_capability(capability=req.capability, target=req.target)
    return result

@router.post("/terminal/execute")
async def execute_terminal_command(req: TerminalExecuteRequest):
    start_time = datetime.utcnow()
    try:
        exec_cwd = req.working_directory if (req.working_directory and os.path.exists(req.working_directory)) else None
        process = await asyncio.create_subprocess_shell(
            req.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=exec_cwd
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=60)
        output = stdout_bytes.decode(errors="replace") or stderr_bytes.decode(errors="replace") or "Command completed with no output."
        exit_code = process.returncode
    except asyncio.TimeoutError:
        output = "Command timed out after 60 seconds."
        exit_code = -1
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
def get_providers():
    return [
        {
            "name": p.name,
            "is_paid": p.is_paid,
            "status": "healthy"
        }
        for p in model_router.providers.values()
    ]

@router.get("/providers/health")
def get_providers_health():
    return {
        "paid_allowed": model_router.paid_allowed,
        "budget_usd": model_router.daily_budget_usd,
        "spent_usd": model_router.current_spent_usd,
        "providers": [
            {
                "name": p.name,
                "is_paid": p.is_paid,
                "status": "healthy",
                "latency_ms": 120 if "cerebras" in p.name else 420
            }
            for p in model_router.providers.values()
        ]
    }

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
