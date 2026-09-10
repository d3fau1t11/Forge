"""Specialist agents (Phase 4 §2, §5, §14).

A :class:`SpecialistAgent` is a thin coordination wrapper around the EXISTING
:class:`~backend.agent_runtime.runtime.AgentRuntime`. It does not execute tools
itself and it does not own history — it:

  1. creates an ISOLATED :class:`~backend.agent_runtime.session.AgentSession`
     (its own MissionState + its own trajectory, keyed by a distinct ``agent_id``),
  2. seeds a BOUNDED, RELEVANT slice of shared knowledge into that session — never
     another agent's whole trajectory, never the entire database (§5),
  3. runs the unmodified AgentRuntime loop (which routes every action through
     ToolManager → ExecutionService → backend → ProcessManager, verifies flags,
     retrieves/records memory, and writes the trajectory — all reused, nothing
     duplicated),
  4. harvests what the agent newly discovered and returns it as structured
     :class:`~backend.swarm.evidence.Evidence` for the Evidence Bus.

Tests inject a ``runtime`` or ``runtime_factory`` built with scripted provider/tool
doubles, so no API key / network / subprocess is required.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from backend.agent_runtime import AgentRuntime, RealToolExecutor, session_manager
from backend.swarm.evidence import Evidence, EvidenceType
from backend.swarm.roles import AgentRole
from backend.swarm.tasks import Task

logger = logging.getLogger("forge.swarm.agents")

# Seeding caps — a specialist gets a useful slice, not a database dump (§5).
_CAP_ENDPOINTS = 20
_CAP_TECH = 15
_CAP_SERVICES = 15
_CAP_DEADENDS = 10
_CAP_FAILED = 10

_FAIL_TOKENS = {
    "COMMAND_NOT_FOUND": "missing_dependency",
    "MISSING_TOOL": "missing_dependency",
    "MISSING_DEPENDENCY": "missing_dependency",
    "NETWORK": "network",
    "PERMISSION": "permission",
    "TIMEOUT": "timeout",
}


@dataclass
class AgentResult:
    task_id: str
    role: str
    status: str                              # COMPLETED|FAILED|TIMEOUT|MAX_TURNS|CANCELLED
    session_id: str = ""
    verified_flag: Optional[str] = None
    flag_candidates: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    reason: str = ""
    failure_category: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == "COMPLETED"


class SpecialistAgent:
    def __init__(self, role: AgentRole, *, runtime: Optional[AgentRuntime] = None,
                 runtime_factory: Optional[Callable[[AgentRole], AgentRuntime]] = None,
                 workspace_root: Optional[str] = None):
        self.role = role if isinstance(role, AgentRole) else AgentRole.from_value(role)
        self._runtime = runtime
        self._runtime_factory = runtime_factory
        self.workspace_root = workspace_root

    # ------------------------------------------------------------------ #

    def _get_runtime(self) -> AgentRuntime:
        if self._runtime is not None:
            return self._runtime
        if self._runtime_factory is not None:
            return self._runtime_factory(self.role)
        # Production default: the real tool executor (ToolManager → ExecutionService).
        return AgentRuntime(tool_executor=RealToolExecutor())

    def _agent_cwd(self, task: Task) -> Optional[str]:
        if not self.workspace_root:
            return None
        try:
            path = os.path.join(self.workspace_root, "agents", self.role.value, task.id[:8])
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:
            return self.workspace_root

    async def execute(
        self,
        task: Task,
        mission_state: Any,
        evidence_bus: Any,
        *,
        max_turns: int = 12,
        task_timeout_seconds: int = 0,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AgentResult:
        """Run one task on this specialist and return a structured result."""
        # 1) Isolated session (own agent_id → own MissionState + own trajectory).
        sess = session_manager.create(
            challenge_id=task.challenge_id, run_id=task.run_id,
            target_scope=getattr(mission_state, "target", "") or "",
            objective=self._objective_with_context(task, mission_state, evidence_bus),
            agent_id=self.role.value, engine="swarm_coord",
            challenge_name=getattr(mission_state, "challenge_name", "") or "",
            category=getattr(mission_state, "category", "") or "",
            difficulty=getattr(mission_state, "difficulty", "") or "",
            platform=getattr(mission_state, "platform", "") or "",
            description=getattr(mission_state, "description", "") or "",
            flag_format=getattr(mission_state, "flag_format", "") or "",
        )
        task.agent_session_id = sess.id

        # 2) Seed a bounded, relevant slice of shared knowledge, then snapshot it so we
        #    only report what THIS agent newly discovers.
        seeded = self._seed_context(sess, mission_state)
        session_manager.save(sess)

        # 3) Run the unmodified AgentRuntime loop.
        runtime = self._get_runtime()
        cwd = self._agent_cwd(task)
        try:
            run_result = await runtime.run(
                sess, max_turns=max_turns,
                max_seconds=(task_timeout_seconds or None),
                cwd=cwd, cancel_check=cancel_check,
            )
            status = run_result.status
            reason = run_result.reason
            verified_flag = run_result.verified_flag
            flag_candidates = list(run_result.flag_candidates or [])
        except Exception as e:  # a crashing agent must not crash the mission
            logger.warning(f"[SpecialistAgent:{self.role.value}] runtime error: {e}")
            status, reason, verified_flag, flag_candidates = "FAILED", f"agent runtime error: {e}", None, []

        # 4) Harvest newly-discovered knowledge as structured evidence.
        final = session_manager.get(sess.id) or sess
        evidence = self._harvest_evidence(final, task, seeded)
        failure_category = self._derive_failure_category(final) if status != "COMPLETED" else ""

        return AgentResult(
            task_id=task.id, role=self.role.value, status=status, session_id=sess.id,
            verified_flag=verified_flag, flag_candidates=flag_candidates, evidence=evidence,
            reason=reason, failure_category=failure_category,
            prompt_tokens=getattr(final, "total_prompt_tokens", 0),
            completion_tokens=getattr(final, "total_completion_tokens", 0),
        )

    # ------------------------------------------------------------------ #
    # Context isolation helpers
    # ------------------------------------------------------------------ #

    def _objective_with_context(self, task: Task, mission_state: Any, bus: Any) -> str:
        """Prepend a compact mission summary + role-relevant leads to the task objective."""
        parts = [task.objective]
        try:
            leads = bus.relevant_for(self.role, limit=5)
            if leads:
                titles = "; ".join(f"{e.evidence_type}:{e.title or e.description}"[:80] for e in leads)
                parts.append(f"Relevant leads from the team: {titles}")
        except Exception:
            pass
        return "\n".join(p for p in parts if p)

    def _seed_context(self, sess: Any, mission_state: Any) -> dict:
        """Copy a bounded relevant slice of shared knowledge into the agent's own state."""
        st = sess.state
        snap = {
            "endpoints": set(getattr(mission_state, "endpoints", []) or []),
            "technologies": set(getattr(mission_state, "technologies", []) or []),
            "services": set(getattr(mission_state, "services", []) or []),
            "vulnerabilities": set(getattr(mission_state, "vulnerabilities", []) or []),
            "credentials": set(getattr(mission_state, "credentials", []) or []),
            "artifacts": set(getattr(mission_state, "artifacts", []) or []),
            "flag_candidates": set(getattr(mission_state, "flag_candidates", []) or []),
        }
        st.known_endpoints = list(snap["endpoints"])[:_CAP_ENDPOINTS]
        st.technologies = list(snap["technologies"])[:_CAP_TECH]
        st.known_services = list(snap["services"])[:_CAP_SERVICES]
        st.vulnerabilities = list(snap["vulnerabilities"])
        st.credentials = list(snap["credentials"])
        st.artifacts = list(snap["artifacts"])
        # Web-ish leads: seed headers/cookies too.
        if self.role in (AgentRole.WEB, AgentRole.RECON):
            st.headers = dict(getattr(mission_state, "headers", {}) or {})
            st.cookies = dict(getattr(mission_state, "cookies", {}) or {})
        # Cross-agent dedup awareness: what the team has already ruled out. These flow
        # into the agent's prompt so a specialist avoids repeating another's dead ends (§8).
        st.dead_ends = list(getattr(mission_state, "dead_ends", []) or [])[:_CAP_DEADENDS]
        st.failed_techniques = list(getattr(mission_state, "failed_techniques", []) or [])[:_CAP_FAILED]
        return snap

    def _harvest_evidence(self, sess: Any, task: Task, seeded: dict) -> List[Evidence]:
        """Turn NEWLY discovered knowledge (final state minus seeded) into evidence."""
        st = sess.state
        out: List[Evidence] = []

        def _new(field_items, seeded_key):
            base = seeded.get(seeded_key, set())
            return [x for x in (field_items or []) if x not in base]

        def _ev(etype: str, title: str, **kw) -> Evidence:
            return Evidence(mission_id=task.mission_id, agent_id=self.role.value, task_id=task.id,
                            evidence_type=etype, title=str(title)[:400], source="agent",
                            run_id=task.run_id, challenge_id=task.challenge_id, **kw)

        for ep in _new(st.known_endpoints, "endpoints"):
            out.append(_ev(EvidenceType.ENDPOINT.value, ep, related_endpoint=ep, confidence=0.8))
        for tech in _new(st.technologies, "technologies"):
            out.append(_ev(EvidenceType.TECHNOLOGY.value, tech, related_technology=tech, confidence=0.85))
        for svc in _new(st.known_services, "services"):
            out.append(_ev(EvidenceType.SERVICE.value, svc, confidence=0.85))
        for v in _new(st.vulnerabilities, "vulnerabilities"):
            out.append(_ev(EvidenceType.VULNERABILITY.value, v, related_vulnerability=v, confidence=0.8))
        for c in _new(st.credentials, "credentials"):
            out.append(_ev(EvidenceType.CREDENTIAL.value, c, confidence=0.75))
        for a in _new(st.artifacts, "artifacts"):
            out.append(_ev(EvidenceType.ARTIFACT.value, a, artifact_id=a, confidence=0.7))
        for cand in _new(st.flag_candidates, "flag_candidates"):
            out.append(_ev(EvidenceType.FLAG.value, cand, confidence=0.6,
                           tags=["candidate"]))
        return out

    @staticmethod
    def _derive_failure_category(sess: Any) -> str:
        """Infer a failure category from the agent's recorded failed techniques."""
        for entry in reversed(getattr(sess.state, "failed_techniques", []) or []):
            up = str(entry).upper()
            for token, cat in _FAIL_TOKENS.items():
                if token in up:
                    return cat
        return ""
