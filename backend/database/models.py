import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Text, Boolean, Float, Integer, DateTime, ForeignKey, Enum as SQLEnum, JSON, create_engine
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

class ProviderConfigModel(Base):
    __tablename__ = "providers"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, nullable=False) # gemini, nvidia, cerebras, openrouter, mock, etc.
    enabled = Column(Boolean, default=True)
    is_paid = Column(Boolean, default=False)
    api_key_configured = Column(Boolean, default=False)
    latency_ms = Column(Float, default=0.0)
    health_status = Column(String, default="healthy") # healthy, degraded, unavailable
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ChallengeModel(Base):
    __tablename__ = "challenges"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    category = Column(String, default="general") # web, recon, forensics, crypto, pwn, rev
    description = Column(Text, default="")
    status = Column(String, default="QUEUED") # QUEUED, RUNNING, PAUSED, COMPLETED, FAILED
    flag = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    targets = relationship("TargetProfileModel", back_populates="challenge", cascade="all, delete-orphan")
    runs = relationship("RunModel", back_populates="challenge", cascade="all, delete-orphan")
    evidence = relationship("EvidenceModel", back_populates="challenge", cascade="all, delete-orphan")

class TargetProfileModel(Base):
    __tablename__ = "targets"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    current_address = Column(String, nullable=False) # IP, URL, or domain
    hostname = Column(String, nullable=True)
    expected_services = Column(JSON, default=list) # e.g. ["http", "ssh"]
    verification_status = Column(String, default="unverified") # verified, changed, stale
    last_verified_at = Column(DateTime, default=datetime.utcnow)
    
    challenge = relationship("ChallengeModel", back_populates="targets")

class RunModel(Base):
    __tablename__ = "runs"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    status = Column(String, default="QUEUED") # QUEUED, RUNNING, WAITING_FOR_TOOL, PAUSED, COMPLETED, CANCELLED
    current_phase = Column(String, default="recon")
    current_agent = Column(String, default="orchestrator")
    started_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    challenge = relationship("ChallengeModel", back_populates="runs")
    checkpoints = relationship("CheckpointModel", back_populates="run", cascade="all, delete-orphan")
    tool_executions = relationship("ToolExecutionModel", back_populates="run", cascade="all, delete-orphan")

class CheckpointModel(Base):
    __tablename__ = "checkpoints"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    state_snapshot = Column(JSON, nullable=False)
    last_successful_action = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    resumable = Column(Boolean, default=True)
    
    run = relationship("RunModel", back_populates="checkpoints")

class ToolExecutionModel(Base):
    __tablename__ = "tool_executions"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    agent = Column(String, nullable=False)
    tool_name = Column(String, nullable=False)
    capability = Column(String, nullable=False)
    command = Column(Text, nullable=False)
    privilege_level = Column(String, default="SAFE") # SAFE, PRIVILEGED, DANGEROUS
    approved = Column(Boolean, default=True)
    status = Column(String, default="PENDING") # PENDING, EXECUTING, SUCCESS, FAILED, KILLED
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    exit_code = Column(Integer, nullable=True)
    duration_ms = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    run = relationship("RunModel", back_populates="tool_executions")

class EvidenceModel(Base):
    __tablename__ = "evidence"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    agent = Column(String, nullable=False)
    evidence_type = Column(String, nullable=False) # http_response, screenshot, credentials, flag, banner
    source = Column(String, nullable=False)
    content = Column(Text, default="")
    file_path = Column(String, nullable=True)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    challenge = relationship("ChallengeModel", back_populates="evidence")

class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    agent = Column(String, nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=True)
    privilege_level = Column(String, default="SAFE")
    approved = Column(Boolean, default=True)
    details = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=datetime.utcnow)
