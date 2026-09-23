"""Target identity management — target IP ≠ identity. A verified target can change
address without losing its accumulated profile."""

import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.models import TargetProfileModel
from backend.database.session import get_db
from backend.websocket.manager import ws_manager

router = APIRouter()


class UpdateTargetAddressRequest(BaseModel):
    new_address: str


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
