"""FORGE Swarm — Phase 4 multi-agent coordination layer.

Built strictly ABOVE the existing systems (AgentRuntime, ExecutionService,
memory/experience/playbooks, trajectory, checkpoints, provider router). This
package adds coordination — a supervisor, specialist roles, a structured task
model with a dependency-aware scheduler, an evidence bus, and a shared, persistent
mission state — without duplicating any lower layer.

    SwarmCoordinator          — top-level orchestration (concurrency, I/O, persistence)
      Supervisor              — deterministic planning / evidence reaction / recovery
      TaskScheduler           — dependency-aware, priority, concurrency-limited queue
      SpecialistAgent(role)   — wraps the EXISTING AgentRuntime in an isolated session
      EvidenceBus             — structured agent↔agent communication
      SharedMissionState      — central resumable aggregate
"""
from backend.swarm.roles import (
    AgentRole, RoleProfile, ROLE_PROFILES, profile, roles_for_category,
    roles_activated_by, primary_role_for,
)
from backend.swarm.reasoning import (
    Reliability, classify_reliability, Fact, Hypothesis, HypothesisStatus,
    FailedApproach, FailureClass, RecoveryHint, classify_failure, recovery_hint_for,
    CandidateAction, InformationGain, Risk, Cost, ActionProfile, ACTION_PROFILES, profile_for,
)
from backend.swarm.scoring import (
    ActionScorer, ScoreWeights, mission_uncertainty, information_gain_for,
)
from backend.swarm.candidates import CandidateGenerator, technique_to_action_type
from backend.swarm.progress import (
    MissionBudget, ProgressLedger, StopCondition, evaluate_stop, knowledge_fingerprint,
)
from backend.swarm.evidence import Evidence, EvidenceType, ProvenanceType, EvidenceBus
from backend.swarm.tasks import Task, TaskStatus, TERMINAL_STATUSES
from backend.swarm.scheduler import TaskScheduler
from backend.swarm.mission import SharedMissionState
from backend.swarm.supervisor import Supervisor, RecoveryDecision, ReasoningDecision
from backend.swarm.agents import SpecialistAgent, AgentResult
from backend.swarm.limits import SwarmLimits
from backend.swarm.coordinator import SwarmCoordinator, MissionResult, active_missions
from backend.swarm.target_reconciliation import (
    TargetReconciliation, reconcile_target, references_stale_host, hosts_of,
)
from backend.swarm import events, dedup

__all__ = [
    "AgentRole", "RoleProfile", "ROLE_PROFILES", "profile", "roles_for_category",
    "roles_activated_by", "primary_role_for",
    # Phase 5 reasoning primitives
    "Reliability", "classify_reliability", "Fact", "Hypothesis", "HypothesisStatus",
    "FailedApproach", "FailureClass", "RecoveryHint", "classify_failure", "recovery_hint_for",
    "CandidateAction", "InformationGain", "Risk", "Cost", "ActionProfile",
    "ACTION_PROFILES", "profile_for",
    "ActionScorer", "ScoreWeights", "mission_uncertainty", "information_gain_for",
    "CandidateGenerator", "technique_to_action_type",
    "MissionBudget", "ProgressLedger", "StopCondition", "evaluate_stop", "knowledge_fingerprint",
    "Evidence", "EvidenceType", "ProvenanceType", "EvidenceBus",
    "Task", "TaskStatus", "TERMINAL_STATUSES",
    "TaskScheduler",
    "SharedMissionState",
    "Supervisor", "RecoveryDecision", "ReasoningDecision",
    "SpecialistAgent", "AgentResult",
    "SwarmLimits",
    "SwarmCoordinator", "MissionResult", "active_missions",
    "TargetReconciliation", "reconcile_target", "references_stale_host", "hosts_of",
    "events", "dedup",
]
