from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from backend.database.session import get_db
from backend.database.models import ChallengeModel, TargetProfileModel, RunModel
from backend.environment.detector import environment_detector
from backend.providers.router import model_router
from backend.tools.registry import tool_registry
from backend.api.runner import workflow_runner
from backend.websocket.manager import ws_manager

router = APIRouter()

class CreateChallengeRequest(BaseModel):
    name: str
    category: str = "general"
    description: str = ""
    target_address: str

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

@router.get("/challenges")
def list_challenges(db: Session = Depends(get_db)):
    return db.query(ChallengeModel).all()

@router.post("/challenges")
def create_challenge(req: CreateChallengeRequest, db: Session = Depends(get_db)):
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
        current_address=req.target_address
    )
    db.add(target)
    db.commit()

    return challenge

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

    # Broadcast websocket event
    await ws_manager.broadcast({
        "event": "RUN_STARTED",
        "run_id": run.id,
        "challenge_id": challenge_id,
        "target": target_addr
    })

    return run

@router.post("/killswitch")
async def kill_switch(run_id: str = None):
    workflow_runner.activate_kill_switch(run_id)
    await ws_manager.broadcast({
        "event": "KILL_SWITCH_ACTIVATED",
        "run_id": run_id
    })
    return {"status": "HALTED", "message": "Emergency Kill Switch triggered successfully."}

@router.get("/tools")
def list_tools():
    return [t.dict() for t in tool_registry.tools.values()]

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
