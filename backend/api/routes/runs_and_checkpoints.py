"""Runs, checkpoints, and the HITL (human-in-the-loop) checkpoint exchange that
ships a checkpoint report out to a stronger external model and routes the pasted
response back into the waiting swarm."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
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
# HITL CHECKPOINTS (operator ↔ external model exchange)
# ----------------------------------------------------

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
