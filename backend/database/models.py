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
    name = Column(String, unique=True, nullable=False) # gemini, nvidia, cerebras, openrouter, hf, cloudflare, agentrouter, mistral, cohere, groq
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
    difficulty = Column(String, default="MEDIUM") # EASY, MEDIUM, HARD, INSANE
    description = Column(Text, default="")
    working_directory = Column(String, default="")
    platform_name = Column(String, default="")
    status = Column(String, default="QUEUED") # QUEUED, RUNNING, PAUSED, COMPLETED, FAILED
    progress = Column(Integer, default=0)
    flag_status = Column(String, default="UNFOUND")
    flag = Column(String, nullable=True)
    requires_root = Column(Boolean, default=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, default=0)
    mission_plan = Column(JSON, default=dict)
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
    severity = Column(String, default="HIGH")
    endpoint = Column(String, default="")
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


# =============================================================================
# EXPERIENCE / MEMORY LAYER
# -----------------------------------------------------------------------------
# FORGE's experience-based memory. A completed run is distilled into a reusable,
# GENERALIZED ExperienceModel (challenge-specific secrets stripped) so future
# missions can retrieve "we have seen conditions like this before".
#
# Provenance note: source_run_id / source_challenge_id are PLAIN string columns
# (NOT ForeignKeys). Memory must OUTLIVE the challenge/run it came from — a hard
# FK with the challenges cascade ("all, delete-orphan") would wipe learned
# experience the moment an operator deletes the original challenge. The link is
# preserved for traceability (§13) without coupling lifecycle.
# =============================================================================

class ExperienceModel(Base):
    __tablename__ = "experiences"

    id = Column(String, primary_key=True, default=generate_uuid)

    # ── Provenance (§13) — plain strings, no FK cascade, so memory survives deletion ──
    source = Column(String, default="forge_run")          # forge_run | external
    source_run_id = Column(String, nullable=True)
    source_challenge_id = Column(String, nullable=True)
    challenge_name = Column(String, default="")

    # ── Classification ────────────────────────────────────────────────────────
    category = Column(String, default="web")
    difficulty = Column(String, default="MEDIUM")
    technique = Column(String, nullable=False)            # generalized technique label
    tags = Column(JSON, default=list)                     # searchable keywords

    # ── Observed conditions & environment (generalized) ───────────────────────
    target_characteristics = Column(JSON, default=dict)
    initial_observations = Column(Text, default="")
    observed_conditions = Column(Text, default="")        # what FORGE observed
    applicable_conditions = Column(Text, default="")      # when to consider this
    discovered_endpoints = Column(JSON, default=list)
    technologies = Column(JSON, default=list)
    vulnerabilities = Column(JSON, default=list)

    # ── What worked / what did not (§5) ───────────────────────────────────────
    successful_techniques = Column(JSON, default=list)
    failed_techniques = Column(JSON, default=list)        # [{approach, reason}]
    commands_used = Column(JSON, default=list)            # generalized commands
    important_tool_outputs = Column(JSON, default=list)   # generalized snippets
    successful_attack_chain = Column(JSON, default=list)  # ordered steps
    verification_evidence = Column(Text, default="")
    success_indicators = Column(JSON, default=list)
    prerequisites = Column(JSON, default=list)
    generalized_strategy = Column(Text, default="")

    # ── Blue-team knowledge derived from the attack (§11) ─────────────────────
    detection_indicators = Column(JSON, default=dict)

    # ── Outcome + learning-flywheel statistics (§12) ──────────────────────────
    outcome = Column(String, default="success")           # success | failure
    confidence = Column(Float, default=0.6)
    times_retrieved = Column(Integer, default=0)
    times_used = Column(Integer, default=0)
    times_successful = Column(Integer, default=0)
    times_failed = Column(Integer, default=0)
    success_rate = Column(Float, default=1.0)
    last_used = Column(DateTime, nullable=True)

    # ── Playbook promotion link (§10) ─────────────────────────────────────────
    promoted_playbook_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    attempts = relationship("ExperienceAttemptModel", back_populates="experience", cascade="all, delete-orphan")
    usages = relationship("MemoryUsageModel", back_populates="experience", cascade="all, delete-orphan")


class ExperienceAttemptModel(Base):
    __tablename__ = "experience_attempts"

    id = Column(String, primary_key=True, default=generate_uuid)
    experience_id = Column(String, ForeignKey("experiences.id"), nullable=False)
    sequence = Column(Integer, default=0)
    approach = Column(String, default="")                 # generalized approach label
    technique = Column(String, default="")
    outcome = Column(String, default="failure")           # success | failure
    reason = Column(Text, default="")                     # why it failed / how verified
    evidence = Column(Text, default="")                   # generalized supporting output
    created_at = Column(DateTime, default=datetime.utcnow)

    experience = relationship("ExperienceModel", back_populates="attempts")


class MemoryUsageModel(Base):
    __tablename__ = "memory_usage"

    id = Column(String, primary_key=True, default=generate_uuid)
    experience_id = Column(String, ForeignKey("experiences.id"), nullable=False)
    run_id = Column(String, nullable=True)
    challenge_id = Column(String, nullable=True)
    event = Column(String, default="retrieved")           # retrieved | used | success | failure
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    experience = relationship("ExperienceModel", back_populates="usages")
