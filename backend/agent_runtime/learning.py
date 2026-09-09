"""
FORGE Agent Runtime — session → experience learning bridge (Phase 2, Steps 4-6, 20).

Phase 1 gave FORGE a provider-agnostic runtime that persists every mission as a
:class:`~backend.agent_runtime.session.AgentSession` + a durable trajectory. Phase 1
did NOT close the learning loop for that path: only the swarm's ``_learn_from_run``
fed :mod:`backend.knowledge.experience_memory`. A mission solved by the AgentRuntime
was remembered as *episodic* trajectory but never *distilled* into a reusable,
generalized experience.

This module is that missing bridge. It unifies the two systems rather than adding a
third:

    AgentRuntime session + trajectory (episodic memory, Level 1)
        │  RuntimeBoardAdapter  (duck-types a completed session as a "board")
        ▼
    ExperienceExtractor  ── REUSED, not reimplemented ──►  ExperienceRecord (Level 2)
        ▼
    experience_memory.store()  ──► FTS index + promotion pipeline (Level 3 playbooks)

Key design rules honoured:
* Knowledge is grounded in EVIDENCE only (§4). The adapter feeds the extractor the
  real captured ``command``/``stdout`` pairs from the trajectory — never the model's
  prose. A "success" is only claimed when the session actually holds a verified flag.
* Failed missions still teach (§5). A run that ended without a verified flag is stored
  as a ``outcome="failure"`` experience so its dead-ends are recalled next time.
* Deterministic + non-fatal (§17): a learning failure can never break run completion.
* Provider-independent (§, "THE MODEL IS NOT THE MEMORY"): everything is read from
  FORGE-owned state, so the same distillation happens regardless of which provider
  drove the mission.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.agent_runtime.session import AgentSession
from backend.agent_runtime.trajectory import trajectory_store

logger = logging.getLogger("forge.agent_runtime.learning")


class RuntimeBoardAdapter:
    """Presents a completed AgentRuntime session as the duck-typed "board" that
    :class:`~backend.knowledge.experience_extractor.ExperienceExtractor` already knows
    how to distil. This is why Phase 2 does not reimplement extraction: the extractor's
    contract is a small set of attributes, and a finished session can supply all of them
    from FORGE-owned state + the persisted trajectory.
    """

    def __init__(self, session: AgentSession, events: Optional[List[Any]] = None):
        st = session.state
        self.run_id = session.run_id
        self.challenge_id = session.challenge_id
        self.challenge_name = st.challenge_name or ""
        self.category = st.category or "web"
        self.difficulty = st.difficulty or "MEDIUM"
        self.description = st.description or ""
        self.target_scope = st.target or ""

        # Discovered knowledge straight from MissionState (already evidence-derived).
        self.discovered_endpoints = set(st.known_endpoints or [])
        self.extracted_headers = dict(st.headers or {})
        self.observed_cookies = dict(st.cookies or {})
        self.candidate_tokens = set(st.flag_candidates or [])
        self.candidate_usernames = set()
        self.deobfuscated_secrets = list(st.credentials or [])
        self.artifact_classification = None

        # Reconstruct the execution history the extractor expects: one dict per
        # COMMAND trajectory event, folding in the captured output + any recovery note.
        events = events if events is not None else trajectory_store.get_events(session.id)
        self.execution_history = self._build_execution_history(events)

        # The verified flag, if any — the ONLY thing that makes this a "success" (§4, §6).
        self.flag_captured = session.verified_flag or st.verified_flag or ""

    @staticmethod
    def _build_execution_history(events: List[Any]) -> List[Dict[str, Any]]:
        """Fold COMMAND events (with their stdout/stderr) into command/output/note steps.

        REPLAN/RECOVERY events immediately following a command carry the "why it failed"
        note, which the extractor uses to label a failed approach (§5). We attach the most
        recent recovery directive to the preceding command.
        """
        history: List[Dict[str, Any]] = []
        last_cmd_idx: Optional[int] = None
        for ev in sorted(events, key=lambda e: getattr(e, "sequence", 0) or 0):
            et = getattr(ev, "event_type", "")
            if et == "COMMAND":
                out = (getattr(ev, "stdout", "") or "")
                err = (getattr(ev, "stderr", "") or "")
                combined = out if not err else f"{out}\n{err}"
                history.append({
                    "command": getattr(ev, "command", "") or "",
                    "output": combined,
                    "note": "",
                    "result": getattr(ev, "result", "") or "",
                })
                last_cmd_idx = len(history) - 1
            elif et in ("REPLAN", "RECOVERY") and last_cmd_idx is not None:
                note = getattr(ev, "decision_summary", "") or ""
                if note and not history[last_cmd_idx]["note"]:
                    history[last_cmd_idx]["note"] = note
        return history


class RuntimeLearner:
    """Distils a finished runtime session into a stored, generalized experience and
    runs the feedback loop for memories that were retrieved during the mission.

    This is the AgentRuntime counterpart of ``SwarmOrchestrator._learn_from_run`` — same
    entry point (``experience_memory``), same generalization guarantees, just sourced
    from a session/trajectory instead of a blackboard.
    """

    def __init__(self, extractor: Any = None, memory: Any = None):
        self._extractor = extractor
        self._memory = memory

    def _deps(self):
        extractor = self._extractor
        memory = self._memory
        if extractor is None:
            from backend.knowledge.experience_extractor import experience_extractor
            extractor = experience_extractor
        if memory is None:
            from backend.knowledge.experience_memory import experience_memory
            memory = experience_memory
        return extractor, memory

    def learn_from_session(
        self,
        session: AgentSession,
        *,
        outcome: Optional[str] = None,
        retrieved_memory_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Extract → store an experience from a completed session. Returns its id.

        ``outcome`` is inferred from the session when not given: success iff a flag was
        actually verified. Never raises — a learning failure is logged and swallowed so
        it can never affect the mission result (§17).
        """
        try:
            extractor, memory = self._deps()
            flag = session.verified_flag or session.state.verified_flag or ""
            resolved_outcome = outcome or ("success" if flag else "failure")

            board = RuntimeBoardAdapter(session)
            record = extractor.extract_from_board(board, flag=flag, outcome=resolved_outcome)
            # Provenance: this experience came from the runtime path, not the swarm.
            record.source = "forge_trajectory"
            exp_id = memory.store(record)

            if exp_id:
                logger.info(
                    f"[RuntimeLearner] Learned experience {exp_id} from session {session.id} "
                    f"('{record.technique}', outcome={resolved_outcome}, "
                    f"env={record.required_os}/{record.required_tools})")

            # Feedback: reinforce memories that were retrieved during a SOLVED mission (§12),
            # and log a fine-grained "contributed" usage event so FORGE can later analyse
            # which memories actually help — beyond the coarse success/confidence bump (§14).
            if resolved_outcome == "success":
                for mid in (retrieved_memory_ids or []):
                    try:
                        memory.record_feedback(
                            mid, success=True, note="Retrieved during a solved runtime mission",
                            run_id=session.run_id, challenge_id=session.challenge_id)
                        memory.record_usage_event(
                            mid, "contributed",
                            note="Present in the context of a solved runtime mission",
                            run_id=session.run_id, challenge_id=session.challenge_id)
                    except Exception:
                        pass
            return exp_id
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[RuntimeLearner] learn_from_session failed (non-fatal): {e}")
            return None


runtime_learner = RuntimeLearner()
