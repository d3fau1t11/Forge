import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy import (
    Column, String, Text, Boolean, Float, Integer, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

class ProviderConfigModel(Base):
    __tablename__ = "providers"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, nullable=False) # gemini, nvidia, cerebras, openrouter, hf, cloudflare, agentrouter, mistral, cohere, groq, mock
    enabled = Column(Boolean, default=True)
    is_paid = Column(Boolean, default=False)
    api_key_configured = Column(Boolean, default=False)
    latency_ms = Column(Float, default=0.0)
    health_status = Column(String, default="healthy") # healthy, degraded, unavailable
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ModelConfigModel(Base):
    __tablename__ = "models"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    provider_name = Column(String, nullable=False)
    model_name = Column(String, nullable=False)
    capability = Column(String, nullable=False) # general_reasoning, code_analysis, recon, etc.
    context_length = Column(Integer, default=8192)
    cost_per_1k_input = Column(Float, default=0.0)
    cost_per_1k_output = Column(Float, default=0.0)
    enabled = Column(Boolean, default=True)

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
    findings = relationship("FindingModel", back_populates="challenge", cascade="all, delete-orphan")

class TargetProfileModel(Base):
    __tablename__ = "targets"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    current_address = Column(String, nullable=False) # IP, URL, domain
    hostname = Column(String, nullable=True)
    expected_services = Column(JSON, default=list) # e.g. ["http", "ssh"]
    verification_status = Column(String, default="unverified") # verified, changed, stale
    last_verified_at = Column(DateTime, default=datetime.utcnow)
    
    challenge = relationship("ChallengeModel", back_populates="targets")

class AgentStateModel(Base):
    __tablename__ = "agents"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    agent_name = Column(String, nullable=False)
    state_data = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RunModel(Base):
    __tablename__ = "runs"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    status = Column(String, default="QUEUED") # QUEUED, RUNNING, WAITING_FOR_TOOL, WAITING_FOR_USER, PAUSED, COMPLETED, CANCELLED
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
    privilege_level = Column(String, default="SAFE")
    approved = Column(Boolean, default=True)
    status = Column(String, default="PENDING") # PENDING, EXECUTING, SUCCESS, FAILED, TIMEOUT, KILLED
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    exit_code = Column(Integer, nullable=True)
    duration_ms = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    run = relationship("RunModel", back_populates="tool_executions")

class FindingModel(Base):
    __tablename__ = "findings"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    agent = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    vulnerability_class = Column(String, default="recon")
    verified = Column(Boolean, default=False)
    confidence = Column(Float, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    challenge = relationship("ChallengeModel", back_populates="findings")

class EvidenceModel(Base):
    __tablename__ = "evidence"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    agent = Column(String, nullable=False)
    evidence_type = Column(String, nullable=False) # http_response, command_output, banner, flag, screenshot
    source = Column(String, nullable=False)
    content = Column(Text, default="")
    file_path = Column(String, nullable=True)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    challenge = relationship("ChallengeModel", back_populates="evidence")

class ReportModel(Base):
    __tablename__ = "reports"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    challenge_id = Column(String, ForeignKey("challenges.id"), nullable=False)
    title = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class KnowledgeEntryModel(Base):
    __tablename__ = "knowledge_entries"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    category = Column(String, nullable=False)
    technique = Column(String, nullable=False)
    technology = Column(String, default="")
    attack_pattern = Column(Text, default="")
    solution_summary = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class ProviderUsageModel(Base):
    __tablename__ = "provider_usage"
    
    id = Column(String, primary_key=True, default=generate_uuid)
    provider_name = Column(String, nullable=False)
    model_name = Column(String, nullable=False)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    latency_ms = Column(Float, default=0.0)
    success = Column(Boolean, default=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

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
