"""Runs and non-interactive persistence checkpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.runner import workflow_runner
from backend.database.models import (
    ChallengeModel,
    CheckpointModel,
    RunModel,
    TargetProfileModel,
    ToolExecutionModel,
)
from backend.database.session import get_db
from backend.websocket.manager import ws_manager

router = APIRouter()


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
