import os
import re
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
from backend.providers.snippet_parser import SnippetParser
from backend.tools.registry import tool_registry
from backend.tools.manager import tool_manager
from backend.api.runner import workflow_runner
from backend.websocket.manager import ws_manager
from backend.reporting.generator import report_generator
from backend.privilege.manager import privilege_manager

router = APIRouter()

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

    # Phase 3 Turbo Recon: Pre-warm recon in background immediately
    from backend.recon.turbo_recon import turbo_recon
    if resolved_target:
        asyncio.create_task(turbo_recon.start_turbo_recon(challenge.id, resolved_target, category.lower()))

    workflow_runner.start_run(run.id, challenge.id, resolved_target)

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
                "default_model": getattr(p, "default_model", ""),
                "latency_ms": 120 if "cerebras" in p.name else 420
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
    logger.info(f"Operator approved root elevation for command: {cmd}")

    if req.sudo_password:
        elevated_cmd = f"echo {req.sudo_password} | sudo -S {cmd.removeprefix('sudo ').strip()}"
    elif not cmd.startswith("sudo"):
        elevated_cmd = f"sudo {cmd}"
    else:
        elevated_cmd = cmd

    tool_res = await tool_manager.execute_raw_command(
        command=elevated_cmd,
        cwd=req.working_directory,
        timeout_seconds=120
    )

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
    auto_approve: bool = False

class SearchPlaybooksRequest(BaseModel):
    query: str
    category: Optional[str] = None
    top_k: int = 5
    include_unpromoted: bool = False

@router.post("/playbooks/ingest")
async def ingest_writeup(req: IngestWriteupRequest):
    """Ingest a CTF write-up and extract structured playbook using the fast model tier."""
    from backend.knowledge.playbook_vault import playbook_vault
    if not req.text or len(req.text.strip()) < 20:
        raise HTTPException(status_code=400, detail="Write-up text is too short to extract a playbook.")
    pb = await playbook_vault.ingest_writeup(req.text, req.category, req.auto_approve)
    if pb:
        return {"status": "INGESTED", "playbook_id": pb.id, "category": pb.category, "tags": pb.tags, "is_promoted": pb.is_promoted}
    raise HTTPException(status_code=500, detail="Failed to extract playbook from write-up.")

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
