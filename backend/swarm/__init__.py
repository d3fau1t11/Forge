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
from backend.swarm.evidence import Evidence, EvidenceType, EvidenceBus
from backend.swarm.tasks import Task, TaskStatus, TERMINAL_STATUSES
from backend.swarm.scheduler import TaskScheduler
from backend.swarm.mission import SharedMissionState
from backend.swarm.supervisor import Supervisor, RecoveryDecision
from backend.swarm.agents import SpecialistAgent, AgentResult
from backend.swarm.limits import SwarmLimits
from backend.swarm.coordinator import SwarmCoordinator, MissionResult, active_missions
from backend.swarm import events, dedup

__all__ = [
    "AgentRole", "RoleProfile", "ROLE_PROFILES", "profile", "roles_for_category",
    "roles_activated_by", "primary_role_for",
    "Evidence", "EvidenceType", "EvidenceBus",
    "Task", "TaskStatus", "TERMINAL_STATUSES",
    "TaskScheduler",
    "SharedMissionState",
    "Supervisor", "RecoveryDecision",
    "SpecialistAgent", "AgentResult",
    "SwarmLimits",
    "SwarmCoordinator", "MissionResult", "active_missions",
    "events", "dedup",
]
