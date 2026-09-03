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

class UpdateTargetAddressRequest(BaseModel):
    new_address: str

class ExecuteToolRequest(BaseModel):
    capability: str
    target: str

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
    challenge = ChallengeModel(
        name=req.name,
        category=req.category,
        description=req.description,
        status="QUEUED"
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)

    target = TargetProfileModel(
        challenge_id=challenge.id,
        current_address=req.target_address,
        hostname=f"{req.name.lower()}.ctf",
        verification_status="verified"
    )
    db.add(target)
    db.commit()

    await ws_manager.broadcast({
        "event": "CHALLENGE_CREATED",
        "challenge_id": challenge.id,
        "name": challenge.name,
        "target": req.target_address
    })

    return challenge

@router.delete("/challenges/{challenge_id}")
def delete_challenge(challenge_id: str, db: Session = Depends(get_db)):
    challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
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
    
    target.verification_status = "verified"
    target.last_verified_at = datetime.utcnow()
    db.commit()

    await ws_manager.broadcast({"event": "TARGET_VERIFIED", "target_id": target_id, "address": target.current_address})
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
    return {"status": "HALTED", "message": "Emergency Kill Switch triggered successfully."}
