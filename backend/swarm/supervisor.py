"""The Supervisor (Phase 4 §1, §9) — deterministic planning & recovery brain.

The supervisor decides *what work remains* and *who should do it*. It is
intentionally pure: every method takes state and returns proposed tasks or a
recovery decision, with no side effects. That makes it fully unit-testable with no
LLM and no database, and it keeps the coordinator (which owns concurrency, I/O and
persistence) small.

The supervisor does NOT perform tool actions itself — it delegates by producing
:class:`~backend.swarm.tasks.Task` objects for specialist agents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from backend.swarm.tasks import Task, TaskStatus
from backend.swarm.roles import (
    AgentRole, ROLE_PROFILES, profile, roles_for_category, roles_activated_by,
)

_VERSION_RE = re.compile(r"\d+\.\d+")
# A "networked" target looks like a URL or host:port / IP — recon (nmap/http) helps.
_NETWORK_RE = re.compile(r"(https?://|:\d{2,5}\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|\.[a-z]{2,})", re.I)

# Roles whose work genuinely benefits from recon output when the target is networked.
_RECON_DEPENDENT = {AgentRole.WEB, AgentRole.PWN}


@dataclass
class RecoveryDecision:
    action: str                 # "retry" | "reassign" | "abandon"
    reason: str
    new_role: Optional[str] = None
    objective: Optional[str] = None


class Supervisor:
    def __init__(self, mission_id: str, *, run_id: Optional[str] = None,
                 challenge_id: Optional[str] = None):
        self.mission_id = mission_id
        self.run_id = run_id
        self.challenge_id = challenge_id

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #

    def plan_initial(self, mission_state: Any) -> List[Task]:
        """Produce the initial task set for a challenge — activated roles, with the
        recon→exploit dependency wired where it applies (the Apache example: a
        version-specific web task waits on recon identifying the service)."""
        target = getattr(mission_state, "target", "") or ""
        category = getattr(mission_state, "category", "") or ""
        description = getattr(mission_state, "description", "") or ""
        artifacts = getattr(mission_state, "artifacts", []) or []

        networked = bool(_NETWORK_RE.search(target)) and not target.strip().startswith("/")
        activated: List[AgentRole] = []
        for r in roles_for_category(category) + roles_activated_by(f"{description} {category}"):
            if r not in activated:
                activated.append(r)
        # Forensics is implied by attached artifacts even if the category didn't say so.
        if artifacts and AgentRole.FORENSICS not in activated:
            activated.append(AgentRole.FORENSICS)

        tasks: List[Task] = []
        recon_task: Optional[Task] = None
        if networked and (AgentRole.RECON in activated or any(r in _RECON_DEPENDENT for r in activated)):
            recon_task = self._make_task(AgentRole.RECON, profile(AgentRole.RECON).objective,
                                         mission_state, priority=90)
            tasks.append(recon_task)

        for role in activated:
            if role is AgentRole.RECON:
                continue
            deps = [recon_task.id] if (recon_task and role in _RECON_DEPENDENT) else []
            prio = 70 if role in _RECON_DEPENDENT else 60
            tasks.append(self._make_task(role, profile(role).objective, mission_state,
                                         priority=prio, dependencies=deps,
                                         parent_task_id=(recon_task.id if deps else None)))
        if not tasks:
            # Nothing matched — default to a recon sweep so the mission still does work.
            tasks.append(self._make_task(AgentRole.RECON, profile(AgentRole.RECON).objective,
                                         mission_state, priority=80))
        return tasks

    # ------------------------------------------------------------------ #
    # React to evidence → follow-up tasks (deterministic rules)
    # ------------------------------------------------------------------ #

    def react_to_evidence(self, ev: Any, mission_state: Any) -> List[Task]:
        """Given a freshly published piece of evidence, propose follow-up tasks.

        The coordinator deduplicates by signature and enforces limits, so this may
        freely propose; identical leads collapse to one task.
        """
        etype = getattr(ev, "evidence_type", "note")
        proposals: List[Task] = []

        if etype in ("service", "technology"):
            tech = (getattr(ev, "related_technology", "") or getattr(ev, "title", "")
                    or getattr(ev, "description", "")).strip()
            if tech:
                if _VERSION_RE.search(tech):
                    # A versioned service justifies targeted vulnerability research (the
                    # Apache 2.4.49 example): high priority, version carried in the objective.
                    proposals.append(self._make_task(
                        AgentRole.WEB,
                        f"Research and exploit known vulnerabilities for the detected service '{tech}'. Extract the flag.",
                        mission_state, priority=85))
                else:
                    # A generic technology still warrants targeted web investigation.
                    proposals.append(self._make_task(
                        AgentRole.WEB,
                        f"Investigate the detected technology '{tech}' for known vulnerabilities, "
                        f"misconfigurations, and default credentials.",
                        mission_state, priority=68))
        elif etype == "endpoint":
            ep = (getattr(ev, "related_endpoint", "") or getattr(ev, "title", "")).strip()
            if ep:
                proposals.append(self._make_task(
                    AgentRole.WEB,
                    f"Enumerate and test the discovered endpoint '{ep}' for access-control and injection flaws.",
                    mission_state, priority=72))
        elif etype == "vulnerability":
            vuln = (getattr(ev, "related_vulnerability", "") or getattr(ev, "title", "")).strip()
            if vuln:
                proposals.append(self._make_task(
                    AgentRole.WEB,
                    f"Develop and run an exploit for the identified vulnerability: {vuln}. Extract the flag.",
                    mission_state, priority=88))
        elif etype == "credential":
            cred = (getattr(ev, "title", "") or getattr(ev, "description", "")).strip()
            if cred:
                proposals.append(self._make_task(
                    AgentRole.WEB,
                    f"Authenticate using discovered credentials ({cred}) and access protected resources.",
                    mission_state, priority=80))
        elif etype == "artifact":
            proposals.append(self._make_task(
                AgentRole.FORENSICS,
                f"Analyze the discovered artifact '{getattr(ev, 'title', '') or getattr(ev, 'artifact_id', '')}'.",
                mission_state, priority=65))
        return proposals

    # ------------------------------------------------------------------ #
    # Failure classification + recovery decision (§9)
    # ------------------------------------------------------------------ #

    @staticmethod
    def classify_failure(result: Any) -> str:
        """Map an agent result to a failure category the recovery policy understands."""
        status = (getattr(result, "status", "") or "").upper()
        cat = (getattr(result, "failure_category", "") or "").upper()
        reason = (getattr(result, "reason", "") or "").lower()

        if status == "CANCELLED":
            return "cancelled"
        if status == "TIMEOUT":
            return "timeout"
        if cat in ("COMMAND_NOT_FOUND", "MISSING_TOOL", "MISSING_DEPENDENCY"):
            return "missing_dependency"
        if cat == "NETWORK" or "network" in reason or "connection" in reason:
            return "network"
        if cat == "PERMISSION" or "permission" in reason or "denied" in reason:
            return "permission"
        if "provider" in reason or "exhausted" in reason:
            return "provider"
        if status == "MAX_TURNS" or "no_progress" in reason or cat == "NO_PROGRESS":
            return "no_progress"
        return "execution"

    def decide_recovery(self, task: Task, result: Any, *, max_retries: int) -> RecoveryDecision:
        category = self.classify_failure(result)

        if category == "cancelled":
            return RecoveryDecision("abandon", "Task cancelled by global stop.")

        # Transient categories are worth a bounded retry.
        if category in ("provider", "timeout", "network") and task.retry_count < max_retries:
            return RecoveryDecision("retry", f"Transient failure ({category}); retrying.")

        # A missing tool/dependency: reassign with an explicit instruction to use an
        # available alternative (do not just crash the mission — §9 ffuf example).
        if category == "missing_dependency":
            alt = (f"{profile(AgentRole.from_value(task.role)).objective} "
                   f"NOTE: a required tool was unavailable last time — accomplish this "
                   f"using an alternative available method (e.g. curl/python instead of a "
                   f"missing scanner).")
            return RecoveryDecision("reassign", "Required tool unavailable; reassigning with an alternative method.",
                                    new_role=task.role, objective=alt)

        # No-progress: reassign to a different specialist lens if one is plausible.
        if category == "no_progress" and task.retry_count < max_retries:
            return RecoveryDecision("retry", "No progress; one more attempt with accumulated evidence.")

        return RecoveryDecision("abandon", f"Unrecoverable failure ({category}); recording dead end.")

    def mission_complete(self, mission_state: Any) -> bool:
        return bool(getattr(mission_state, "verified_flag", None))

    # ------------------------------------------------------------------ #

    def _make_task(self, role: AgentRole, objective: str, mission_state: Any, *,
                   priority: int = 50, dependencies: Optional[List[str]] = None,
                   parent_task_id: Optional[str] = None) -> Task:
        return Task(
            mission_id=self.mission_id, run_id=self.run_id, challenge_id=self.challenge_id,
            role=role.value, objective=objective, priority=priority,
            dependencies=list(dependencies or []), parent_task_id=parent_task_id,
        )
