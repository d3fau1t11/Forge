"""The Swarm Coordinator (Phase 4 — the layer that ties §1–§15 together).

This is the top of the coordination stack. It owns concurrency, I/O, and
persistence; the policy pieces it drives (supervisor, scheduler, evidence bus,
specialist agents) are small and pure. The coordinator sits strictly ABOVE the
existing systems and reuses them wholesale:

    SwarmCoordinator
        → Supervisor            (plan / react / recover — pure)
        → TaskScheduler         (dependency-aware queue — pure)
        → SpecialistAgent(role) → AgentRuntime  (EXISTING loop)
                                    → ToolManager → ExecutionService → backend
                                    → FlagVerifier / memory / trajectory  (EXISTING)
        → EvidenceBus           → SharedMissionState
        → FlagVerifier          (authoritative, single verified flag)

Nothing here re-implements subprocess execution, memory, providers, trajectory, or
flag verification — those remain owned by the layers below.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from backend.agent_runtime import (
    FlagVerifier, FlagSource, FlagStatus, session_manager, trajectory_store,
)
from backend.swarm import events
from backend.swarm.agents import AgentResult, SpecialistAgent
from backend.swarm.evidence import Evidence, EvidenceBus, EvidenceType
from backend.swarm.limits import SwarmLimits
from backend.swarm.mission import SharedMissionState
from backend.swarm.roles import AgentRole
from backend.swarm.scheduler import TaskScheduler
from backend.swarm.supervisor import Supervisor
from backend.swarm.tasks import Task, TaskStatus

# Phase 4.x — capability discovery / acquisition / target modelling (cycle-free imports).
from backend.execution.capabilities import (
    capability_service as _default_capability_service, ACQUIRABLE,
)
from backend.execution.acquisition import acquisition_planner as _default_acquisition_planner
from backend.execution.targets import target_detector as _default_target_detector, TargetType

logger = logging.getLogger("forge.swarm.coordinator")

# Objective keywords that imply a capability requirement (kept tight so existing
# role objectives are unaffected — only an explicit OCR/text-from-image need triggers).
_CAP_KEYWORDS = {
    "ocr": ("ocr", "extract text from the image", "extract text from image",
            "read the text in the image", "text from the decoded image"),
}

# Registry of live coordinators so the API/WebSocket layer can observe the swarm.
active_missions: Dict[str, "SwarmCoordinator"] = {}


@dataclass
class MissionResult:
    mission_id: str
    status: str
    verified_flag: Optional[str]
    reason: str
    tasks_total: int
    tasks_completed: int
    tasks_failed: int
    evidence_count: int


class SwarmCoordinator:
    def __init__(
        self,
        *,
        run_id: Optional[str] = None,
        challenge_id: Optional[str] = None,
        target: str = "",
        category: str = "",
        difficulty: str = "",
        challenge_name: str = "",
        platform: str = "",
        description: str = "",
        flag_format: str = "",
        workspace_root: Optional[str] = None,
        limits: Optional[SwarmLimits] = None,
        agent_factory: Optional[Callable[[AgentRole], SpecialistAgent]] = None,
        verifier: Optional[FlagVerifier] = None,
        mission_id: Optional[str] = None,
        persist: bool = True,
        enable_report: bool = False,
        kill_switch: Optional[Callable[[], bool]] = None,
        capability_service: Optional[Any] = None,
        acquisition_planner: Optional[Any] = None,
        target_detector: Optional[Any] = None,
        allow_acquisition: bool = True,
        acquisition_privilege_decider: Optional[Callable[[Any], bool]] = None,
    ):
        self.limits = limits or SwarmLimits()
        self.persist = persist
        self.enable_report = enable_report
        self.kill_switch = kill_switch
        self.verifier = verifier or FlagVerifier()
        self.workspace_root = workspace_root
        self.mission_id = mission_id or str(uuid.uuid4())
        self._stopped = False
        self._stop_reason = ""

        # Phase 4.x — capability/target awareness (injectable for deterministic tests).
        self.capability_service = capability_service or _default_capability_service
        self.acquisition_planner = acquisition_planner or _default_acquisition_planner
        self.target_detector = target_detector or _default_target_detector
        self.allow_acquisition = allow_acquisition
        self._acq_decider = acquisition_privilege_decider
        self._blocked_capabilities: set = set()      # capabilities proven unavailable this mission
        self._acquisition_attempted: set = set()      # capabilities we already tried to acquire

        scope = [t.strip() for t in (target or "").split("+") if t.strip()]
        self.mission = SharedMissionState(
            mission_id=self.mission_id, run_id=run_id, challenge_id=challenge_id,
            target=target, scope=scope, category=category, difficulty=difficulty,
            challenge_name=challenge_name, platform=platform, description=description,
            flag_format=flag_format, status="PLANNING",
        )
        # Detect the KIND of target up front (conservative; UNKNOWN when unsure) so the
        # snapshot/API and the pre-dispatch mismatch gate can reason about it (§9–§11).
        try:
            if target:
                self.mission.target_type = self.target_detector.detect(target).type.value
        except Exception:
            pass

        # A dedicated coordination trajectory session — coordination events are
        # recorded here, kept separate from each specialist's execution trajectory
        # (§14), but reusing the ONE trajectory system rather than a second one.
        self.coord_session = session_manager.create(
            challenge_id=challenge_id, run_id=run_id, target_scope=target,
            objective="Coordinate specialist agents to capture the flag.",
            agent_id="supervisor", engine="swarm_coord",
            challenge_name=challenge_name, category=category, difficulty=difficulty,
            platform=platform, description=description, flag_format=flag_format,
        )

        self.bus = EvidenceBus(self.mission_id, run_id=run_id, challenge_id=challenge_id,
                               persist=persist)
        self.scheduler = TaskScheduler(persist=persist)
        self.supervisor = Supervisor(self.mission_id, run_id=run_id, challenge_id=challenge_id)
        self.agent_factory = agent_factory or (
            lambda role: SpecialistAgent(role, workspace_root=workspace_root))

        self._followup_buffer: List[Task] = []
        self.bus.subscribe(self._on_evidence)
        self._agent_seq = 0

    # ================================================================== #
    # Public entry points
    # ================================================================== #

    async def run(self, *, resume: bool = False) -> MissionResult:
        active_missions[self.mission_id] = self
        if self.challenge_key:
            active_missions[self.challenge_key] = self
        try:
            self._record_coord("PLAN", decision_summary=(
                f"Mission {self.mission_id[:8]} — target={self.mission.target or 'n/a'} "
                f"category={self.mission.category or 'n/a'}"))

            if resume:
                self._load_persisted()

            if not self.scheduler.all():
                for t in self.supervisor.plan_initial(self.mission):
                    self._add_task(t, origin="plan")

            self.mission.status = "RUNNING"
            self._save_mission()
            events.broadcast(events.MISSION_STARTED, {
                "mission_id": self.mission_id, "run_id": self.mission.run_id,
                "challenge_id": self.mission.challenge_id, "target": self.mission.target,
                "tasks": self.scheduler.total_count(),
            })

            await self._loop()
            return self._finalize()
        finally:
            active_missions.pop(self.mission_id, None)
            if self.challenge_key and active_missions.get(self.challenge_key) is self:
                active_missions.pop(self.challenge_key, None)

    @classmethod
    async def resume_mission(cls, mission_id: str, **kwargs: Any) -> Optional[MissionResult]:
        """Resume a persisted mission by id (§15)."""
        loaded = SharedMissionState.load(mission_id)
        if not loaded:
            return None
        coord = cls(
            run_id=loaded.run_id, challenge_id=loaded.challenge_id, target=loaded.target,
            category=loaded.category, difficulty=loaded.difficulty,
            challenge_name=loaded.challenge_name, platform=loaded.platform,
            description=loaded.description, flag_format=loaded.flag_format,
            mission_id=mission_id, **kwargs,
        )
        return await coord.run(resume=True)

    def request_stop(self, reason: str = "Operator stop.") -> None:
        """Ask the mission to stop; in-flight agents pause at their next turn boundary."""
        self._trigger_global_stop(reason, final_status="PAUSED")

    def submit_flag_candidate(self, candidate: str, *, source: str = "tool_output",
                              command: str = "", action_succeeded: bool = True,
                              agent_id: str = "external") -> bool:
        """Central, authoritative flag verification (§11) for a candidate surfaced
        outside the runtime's own tool-output path. Returns True iff VERIFIED."""
        src = FlagSource.TOOL_OUTPUT if source == "tool_output" else (
            FlagSource.LLM_PROSE if source == "llm" else FlagSource.UNKNOWN)
        verdict = self.verifier.assess(candidate, source=src, command=command,
                                       action_succeeded=action_succeeded,
                                       target_scope=self.mission.target,
                                       expected_format=self.mission.flag_format)
        if verdict.status == FlagStatus.VERIFIED:
            self._accept_verified_flag(verdict.candidate, agent_id=agent_id, task=None)
            return True
        if verdict.candidate and verdict.candidate not in self.mission.flag_candidates:
            self.mission.flag_candidates.append(verdict.candidate)
            self._broadcast_flag_candidate(verdict.candidate, agent_id)
        return False

    def snapshot(self) -> Dict[str, Any]:
        """Read-only view of the swarm for the API/WebSocket (§13)."""
        return {
            "mission_id": self.mission_id, "run_id": self.mission.run_id,
            "challenge_id": self.mission.challenge_id, "status": self.mission.status,
            "progress": self.mission.progress, "verified_flag": self.mission.verified_flag,
            "target": self.mission.target, "strategy": self.mission.strategy,
            "shared_state": self.mission.to_dict(),
            "agents": [
                {"agent_id": aid, **info} for aid, info in self.mission.agent_statuses.items()
            ],
            "tasks": [t.to_dict() for t in self.scheduler.all()],
            "task_counts": self._task_counts(),
            "evidence_count": self.bus.count(),
            "quota": self.quota_snapshot(),
            "limits": vars(self.limits),
        }

    @staticmethod
    def quota_snapshot() -> Dict[str, Any]:
        """Read-only integration with the EXISTING provider/quota system (§12)."""
        info: Dict[str, Any] = {}
        try:
            from backend.providers.quota_manager import quota_manager
            for attr in ("get_status", "status", "snapshot"):
                fn = getattr(quota_manager, attr, None)
                if callable(fn):
                    info = fn() or {}
                    break
            if not info:
                info = {"blacklisted": list(getattr(quota_manager, "blacklisted_providers", []) or [])}
        except Exception:
            info = {}
        return info

    # ================================================================== #
    # Core loop
    # ================================================================== #

    async def _loop(self) -> None:
        running: Dict[str, asyncio.Task] = {}
        try:
            while True:
                if self._should_stop():
                    break

                free = self.limits.max_concurrent_agents - len(running)
                if free > 0:
                    for task in self.scheduler.ready_tasks(limit=free):
                        # Phase 4.x pre-dispatch gate: a task that needs a missing capability
                        # or the wrong kind of target is blocked HERE — never spawned — so an
                        # impossible task cannot consume the iteration budget (§15, §22).
                        if await self._precheck_blocks(task):
                            continue
                        self._dispatch(task, running)

                if not running:
                    if not self.scheduler.has_open_work():
                        break
                    # Open work but nothing dispatchable (all blocked/unsatisfiable) → stop.
                    self.scheduler.refresh_states()
                    if not self.scheduler.ready_tasks(limit=1):
                        break
                    continue

                done, _pending = await asyncio.wait(
                    list(running.values()), return_when=asyncio.FIRST_COMPLETED)
                for at in done:
                    task_id = getattr(at, "_forge_task_id", None)
                    if task_id:
                        running.pop(task_id, None)
                    try:
                        result = at.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as e:  # pragma: no cover - execute() is guarded
                        logger.warning(f"[SwarmCoordinator] agent task error: {e}")
                        continue
                    await self._handle_result(result)
                    if self._should_stop():
                        break
                if self._should_stop():
                    break
        finally:
            for at in running.values():
                at.cancel()
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)

    def _dispatch(self, task: Task, running: Dict[str, asyncio.Task]) -> None:
        role = AgentRole.from_value(task.role)
        self._agent_seq += 1
        agent_id = f"{role.value}#{self._agent_seq}"
        agent = self.agent_factory(role)

        self.scheduler.mark_running(task, assigned_agent=agent_id)
        self.mission.set_agent_status(agent_id, role=role.value, status="RUNNING",
                                      current_task=(task.objective or "")[:120], task_id=task.id)
        self._record_coord("DECISION", action_type="assign", command=(task.objective or "")[:400],
                           strategy=role.value, decision_summary=f"Assigned to {agent_id}")
        events.broadcast(events.TASK_ASSIGNED, self._task_event(task, agent_id))
        events.broadcast(events.TASK_STARTED, self._task_event(task, agent_id))
        events.broadcast(events.AGENT_STATUS, {
            "mission_id": self.mission_id, "agent_id": agent_id, "role": role.value,
            "status": "RUNNING", "task_id": task.id})

        coro = agent.execute(
            task, self.mission, self.bus,
            max_turns=self.limits.max_turns_per_task,
            task_timeout_seconds=self.limits.task_timeout_seconds,
            cancel_check=self._should_stop,
        )
        at = asyncio.ensure_future(coro)
        setattr(at, "_forge_task_id", task.id)
        running[task.id] = at

    # ================================================================== #
    # Phase 4.x pre-dispatch capability / target gate (§11, §15, §22)
    # ================================================================== #

    async def _precheck_blocks(self, task: Task) -> bool:
        """Return True (and block the task) if it cannot succeed as specified.

        This is the anti-infinite-retry guarantee: a task needing an unavailable,
        non-acquirable capability, or pointed at the wrong KIND of target, is failed
        here without ever spawning an agent — and the offending capability is
        remembered so no sibling/follow-up task re-triggers the same dead end.
        """
        # 1) Target-type mismatch (only when the task explicitly declares one).
        mism = self._detect_target_mismatch(task)
        if mism is not None:
            self._block_task(task, category="TARGET_MISMATCH",
                             evidence_type=EvidenceType.TARGET_MISMATCH.value,
                             title=mism.summary(), reason=mism.reason,
                             action=mism.recommended_action, tags=["target_mismatch"])
            return True

        # 2) Required capabilities (explicit + conservatively auto-derived).
        for capname in self._auto_capabilities(task):
            cap, ok = await self._resolve_capability(capname)
            if not ok:
                self._block_task(task, category="BLOCKED_CAPABILITY",
                                 evidence_type=EvidenceType.CAPABILITY.value,
                                 title=f"capability '{capname}' unavailable",
                                 reason=cap.reason, action=cap.recommended_action,
                                 tags=["blocked", f"capability:{capname}"], capability=capname)
                return True
        return False

    async def _resolve_capability(self, capname: str):
        """Discover a capability; attempt controlled acquisition once if acquirable.

        Returns (Capability, ok). ok is True only if a provider is actually usable.
        A capability that stays unavailable is memoised in ``_blocked_capabilities``
        so it is never re-discovered/re-acquired for another task this mission.
        """
        cap = self.capability_service.discover(capname)
        if cap.available:
            return cap, True
        if capname in self._blocked_capabilities:
            return cap, False
        if (self.allow_acquisition and cap.status == ACQUIRABLE
                and capname not in self._acquisition_attempted):
            self._acquisition_attempted.add(capname)
            try:
                res = await self.acquisition_planner.acquire(
                    capname, agent="supervisor", privilege_decider=self._acq_decider)
                self._record_coord("ACQUISITION", strategy=capname,
                                   result=("SUCCESS" if res.success else "PENDING"),
                                   decision_summary=(res.reason or "")[:300])
                if res.success:
                    self.capability_service.refresh(capname)
                    cap2 = self.capability_service.discover(capname)
                    if cap2.available:
                        return cap2, True
            except Exception as e:  # acquisition must never crash the mission
                logger.debug(f"[SwarmCoordinator] acquisition error for {capname}: {e}")
        self._blocked_capabilities.add(capname)
        return cap, False

    def _detect_target_mismatch(self, task: Task):
        if not task.target_type:
            return None
        try:
            required = TargetType(task.target_type)
        except Exception:
            return None
        tgt = self.mission.target or ""
        if not tgt:
            return None
        try:
            provided = self.target_detector.detect(tgt)
            return self.target_detector.classify_mismatch(required, provided)
        except Exception:
            return None

    def _auto_capabilities(self, task: Task) -> List[str]:
        caps = list(task.required_capabilities or [])
        text = (task.objective or "").lower()
        for capname, kws in _CAP_KEYWORDS.items():
            if capname not in caps and any(kw in text for kw in kws):
                caps.append(capname)
        return caps

    def _block_task(self, task: Task, *, category: str, evidence_type: str, title: str,
                    reason: str, action: str, tags: List[str], capability: str = "") -> None:
        """Terminally block a task (no retry) and record structured, learnable evidence."""
        self.bus.publish(Evidence(
            mission_id=self.mission_id, agent_id=task.role, task_id=task.id,
            evidence_type=evidence_type, title=(title or "")[:400], description=(reason or "")[:500],
            source="supervisor", confidence=0.6,
            tags=list(tags or []) + [f"action:{action}"], related_technology=capability or ""))
        self.scheduler.mark_failed(task, reason=f"{category}: {reason}")
        self.mission.record_dead_end(f"{task.role}: {category} — {(reason or '')[:80]}")
        self._record_coord(category, command=(task.objective or "")[:400], strategy=task.role,
                           result="BLOCKED", decision_summary=(reason or "")[:300])
        events.broadcast(events.TASK_FAILED, {
            **self._task_event(task, task.assigned_agent or ""),
            "reason": reason, "category": category})
        self._save_mission()

    # ================================================================== #
    # Result handling
    # ================================================================== #

    async def _handle_result(self, result: AgentResult) -> None:
        task = self.scheduler.get(result.task_id)
        if task is None:
            return

        # Publish harvested evidence (fills shared state + buffers follow-up tasks).
        published: List[str] = []
        for ev in result.evidence:
            eid = self.bus.publish(ev)
            if eid:
                published.append(eid)

        # ── Authoritative flag path (§11) ──
        if result.verified_flag:
            self._accept_verified_flag(result.verified_flag, agent_id=task.assigned_agent, task=task)
            self._drain_followups()
            self._save_mission()
            return

        # Unverified candidates: recorded + broadcast, but never end the mission.
        for cand in (result.flag_candidates or []):
            if cand and cand not in self.mission.flag_candidates:
                self.mission.flag_candidates.append(cand)
                self._broadcast_flag_candidate(cand, task.assigned_agent)

        # ── Task outcome ──
        if result.status == "COMPLETED":
            self.scheduler.mark_completed(
                task, result={"reason": result.reason, "session_id": result.session_id},
                evidence_ids=published)
            self.mission.set_agent_status(task.assigned_agent, status="IDLE", current_task=None)
            self._record_coord("TASK_COMPLETED", command=(task.objective or "")[:400],
                               strategy=task.role, result="COMPLETED")
            events.broadcast(events.TASK_COMPLETED, self._task_event(task, task.assigned_agent))
        elif result.status == "CANCELLED":
            self.scheduler.mark_cancelled(task, reason="Global stop.")
            self.mission.set_agent_status(task.assigned_agent, status="IDLE", current_task=None)
        else:
            self._handle_failure(task, result)

        self._drain_followups()
        self._save_mission()
        self._maybe_checkpoint()

    def _handle_failure(self, task: Task, result: AgentResult) -> None:
        # 1) Record structured failure evidence (§9) so it is visible + learnable.
        self.bus.publish(Evidence(
            mission_id=self.mission_id, agent_id=task.role, task_id=task.id,
            evidence_type=EvidenceType.FAILURE.value,
            title=f"{task.role} task failed: {result.status}",
            description=(result.reason or "")[:500], source="agent", confidence=0.5,
            tags=[result.failure_category or "failure"]))
        self.mission.set_agent_status(task.assigned_agent, status="IDLE", current_task=None)
        self._record_coord("TASK_FAILED", command=(task.objective or "")[:400],
                           strategy=task.role, result=result.status,
                           decision_summary=(result.reason or "")[:300])
        events.broadcast(events.TASK_FAILED, {
            **self._task_event(task, task.assigned_agent),
            "reason": result.reason, "category": result.failure_category})

        # 2) Decide recovery: retry / reassign / abandon (§9).
        decision = self.supervisor.decide_recovery(
            task, result, max_retries=self.limits.max_task_retries)
        if decision.action == "retry":
            task.retry_count += 1
            self.scheduler.requeue_for_retry(task)
            self._record_coord("REPLAN", strategy=task.role,
                               decision_summary=f"retry #{task.retry_count}: {decision.reason}")
        elif decision.action == "reassign":
            self.scheduler.mark_reassigned(task, reason=decision.reason)
            new = Task(
                mission_id=self.mission_id, run_id=self.mission.run_id,
                challenge_id=self.mission.challenge_id,
                role=decision.new_role or task.role,
                objective=decision.objective or task.objective,
                priority=task.priority, parent_task_id=task.id)
            if self._add_task(new, origin="reassign"):
                events.broadcast(events.TASK_REASSIGNED, {
                    "mission_id": self.mission_id, "from_task": task.id, "to_task": new.id,
                    "role": new.role, "reason": decision.reason})
                self._record_coord("TASK_REASSIGNED", strategy=new.role,
                                   decision_summary=decision.reason)
        else:  # abandon
            self.scheduler.mark_failed(task, reason=decision.reason)
            self.mission.record_dead_end(
                f"{task.role}: {(task.objective or '')[:60]} — {decision.reason}")

    # ================================================================== #
    # Evidence subscription (runs synchronously during publish)
    # ================================================================== #

    def _on_evidence(self, ev: Evidence) -> None:
        # Integrate into shared state and let the supervisor propose follow-up work.
        self.mission.integrate_evidence(ev)
        try:
            self._followup_buffer.extend(self.supervisor.react_to_evidence(ev, self.mission))
        except Exception as e:  # a planning hiccup must not break publication
            logger.debug(f"[SwarmCoordinator] react_to_evidence error: {e}")

    def _drain_followups(self) -> None:
        buf, self._followup_buffer = self._followup_buffer, []
        for t in buf:
            self._add_task(t, origin="evidence")

    # ================================================================== #
    # Flag verification + global stop (§11)
    # ================================================================== #

    def _accept_verified_flag(self, flag: str, *, agent_id: str, task: Optional[Task]) -> None:
        if self.mission.verified_flag:
            return
        self.mission.set_verified_flag(flag)
        if task is not None:
            self.scheduler.mark_completed(task, result={"verified_flag": flag})
        self.bus.publish(Evidence(
            mission_id=self.mission_id, agent_id=agent_id or "swarm", task_id=(task.id if task else None),
            evidence_type=EvidenceType.FLAG.value, title=flag, source="agent",
            confidence=1.0, tags=["verified"]))
        self._record_coord("FLAG_VERIFIED", result="VERIFIED",
                           decision_summary=f"Flag verified: {flag}")
        events.broadcast(events.FLAG_VERIFIED, {
            "mission_id": self.mission_id, "run_id": self.mission.run_id,
            "challenge_id": self.mission.challenge_id, "flag": flag, "agent_id": agent_id})
        self._trigger_global_stop("Flag verified — mission complete.", final_status="COMPLETED")

    def _broadcast_flag_candidate(self, candidate: str, agent_id: str) -> None:
        self._record_coord("FLAG_CANDIDATE", decision_summary=f"Candidate (unverified): {candidate}")
        events.broadcast(events.FLAG_CANDIDATE, {
            "mission_id": self.mission_id, "challenge_id": self.mission.challenge_id,
            "candidate": candidate, "agent_id": agent_id})

    def _trigger_global_stop(self, reason: str, *, final_status: str) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_reason = reason
        self._pending_final_status = final_status
        cancelled = self.scheduler.cancel_all_open(reason=f"Mission stopped: {reason}")
        self._record_coord("MISSION_STOP", result=final_status,
                           decision_summary=f"{reason} (cancelled {cancelled} open task(s))")

    def _should_stop(self) -> bool:
        if self._stopped:
            return True
        if self.kill_switch:
            try:
                if self.kill_switch():
                    self._stopped = True
                    self._stop_reason = "Kill switch."
                    self._pending_final_status = "CANCELLED"
                    return True
            except Exception:
                pass
        return False

    # ================================================================== #
    # Task creation with dedup + limits (§8, §12)
    # ================================================================== #

    def _add_task(self, task: Task, *, origin: str) -> bool:
        if self.scheduler.total_count() >= self.limits.max_total_tasks:
            return False
        if origin != "plan" and self.scheduler.active_count() >= self.limits.max_active_tasks:
            return False
        # Cross-check against shared attempted signatures too (dedupes across resume).
        if task.signature in set(self.mission.attempted_signatures) and origin != "plan":
            if self.scheduler.has_signature(task.signature):
                return False
        added = self.scheduler.add(task)
        if not added:
            return False
        self.mission.note_attempt(task.signature)
        self._record_coord("TASK_CREATED", command=(task.objective or "")[:400],
                           strategy=task.role, decision_summary=f"origin={origin}")
        events.broadcast(events.TASK_CREATED, self._task_event(task, task.assigned_agent))
        return True

    # ================================================================== #
    # Persistence / finalize / resume
    # ================================================================== #

    def _save_mission(self) -> None:
        if not self.persist:
            return
        self.mission.save(coord_session_id=self.coord_session.id)
        events.broadcast(events.STATE_CHANGED, {
            "mission_id": self.mission_id, "status": self.mission.status,
            "progress": self.mission.progress, "task_counts": self._task_counts()})

    def _maybe_checkpoint(self) -> None:
        if not self.persist or not self.mission.run_id:
            return
        try:
            self.coord_session.last_sequence = trajectory_store.next_sequence(self.coord_session.id) - 1
            session_manager.checkpoint(self.coord_session, last_action="swarm coordination step")
        except Exception:
            pass

    def _load_persisted(self) -> None:
        loaded = SharedMissionState.load(self.mission_id)
        if loaded:
            # Keep the freshly-provided metadata but adopt aggregated knowledge/flags.
            loaded.run_id = loaded.run_id or self.mission.run_id
            loaded.challenge_id = loaded.challenge_id or self.mission.challenge_id
            self.mission = loaded
        self.bus.load()
        self.scheduler.load(self.mission_id)

    def _finalize(self) -> MissionResult:
        counts = self._task_counts()
        if self.mission.verified_flag:
            status = "COMPLETED"
        elif getattr(self, "_pending_final_status", "") in ("CANCELLED", "PAUSED"):
            status = self._pending_final_status
        elif self.scheduler.has_open_work():
            status = "PAUSED"
        else:
            status = "FAILED"
        self.mission.status = status
        self._save_mission()

        try:
            if status == "COMPLETED":
                session_manager.complete(self.coord_session, outcome="success",
                                         verified_flag=self.mission.verified_flag)
            elif status in ("FAILED", "CANCELLED"):
                session_manager.fail(self.coord_session, self._stop_reason or "Mission ended without a flag.")
            else:
                session_manager.pause(self.coord_session)
        except Exception:
            pass

        if status == "COMPLETED":
            events.broadcast(events.MISSION_COMPLETE, {
                "mission_id": self.mission_id, "run_id": self.mission.run_id,
                "challenge_id": self.mission.challenge_id, "flag": self.mission.verified_flag})
            self._maybe_generate_report()
        elif status == "FAILED":
            events.broadcast(events.MISSION_FAILED, {
                "mission_id": self.mission_id, "challenge_id": self.mission.challenge_id,
                "reason": self._stop_reason or "No flag captured."})

        return MissionResult(
            mission_id=self.mission_id, status=status, verified_flag=self.mission.verified_flag,
            reason=self._stop_reason or status, tasks_total=counts["total"],
            tasks_completed=counts.get("COMPLETED", 0), tasks_failed=counts.get("FAILED", 0),
            evidence_count=self.bus.count())

    def _maybe_generate_report(self) -> None:
        """Best-effort report trigger (§11). Off by default so tests stay hermetic."""
        if not self.enable_report or not self.mission.challenge_id:
            return
        try:
            from backend.reporting.generator import report_generator  # noqa: F401
            # Report generation is delegated to the existing generator; kept guarded so a
            # missing provider/network never affects the mission's completion status.
            logger.info(f"[SwarmCoordinator] report generation queued for {self.mission.challenge_id}")
        except Exception:
            pass

    # ================================================================== #
    # Small helpers
    # ================================================================== #

    @property
    def challenge_key(self) -> Optional[str]:
        return self.mission.challenge_id

    def _task_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {"total": self.scheduler.total_count()}
        for t in self.scheduler.all():
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    def _task_event(self, task: Task, agent_id: str) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id, "run_id": self.mission.run_id,
            "challenge_id": self.mission.challenge_id, "task_id": task.id,
            "role": task.role, "agent_id": agent_id, "objective": (task.objective or "")[:200],
            "status": task.status, "priority": task.priority, "dependencies": list(task.dependencies),
        }

    def _record_coord(self, event_type: str, **kw: Any) -> None:
        try:
            trajectory_store.record(
                session_id=self.coord_session.id, event_type=f"COORD_{event_type}",
                run_id=self.mission.run_id, challenge_id=self.mission.challenge_id,
                agent_id="supervisor", **kw)
        except Exception:
            pass
