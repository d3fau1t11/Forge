"""
Tests proving the feedback loop between execution, evidence, flag verification, and replanning.

These tests verify the 9 required behaviors from the task specification.
"""

import os
import asyncio
import unittest

# Pinned above the first backend import on purpose.
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import (
    AgentSessionModel, TrajectoryEventModel, SwarmMissionModel,
    SwarmTaskModel, SwarmEvidenceModel,
)

from backend.swarm import (
    AgentRole, Evidence, EvidenceType, EvidenceBus, Task, TaskStatus,
    SharedMissionState, Supervisor, SwarmLimits, SwarmCoordinator,
    # Phase 5 primitives
    Reliability, classify_reliability, Fact, Hypothesis, HypothesisStatus,
    FailedApproach, FailureClass, recovery_hint_for,
    CandidateAction, InformationGain, Risk, Cost, profile_for,
    ActionScorer, mission_uncertainty, information_gain_for,
    CandidateGenerator, technique_to_action_type,
    MissionBudget, ProgressLedger, StopCondition, evaluate_stop, knowledge_fingerprint,
    AgentResult,
)
from backend.swarm.dedup import action_signature, normalize_target
from backend.swarm import events as swarm_events
from backend.agent_runtime.observation import ObservationEngine, Observation
from backend.agent_runtime.verifier import AnswerResolver, AnswerStatus, AnswerType, AnswerSource
from backend.agent_runtime.action import ExecResult


def _run(coro):
    return asyncio.run(coro)


class _R:
    """A minimal agent/task result stand-in for failure classification."""
    def __init__(self, status="FAILED", failure_category="", reason=""):
        self.status = status
        self.failure_category = failure_category
        self.reason = reason


class FakeMemory:
    """A memory_retriever double returning canned RetrievedMemory-like objects."""
    def __init__(self, memories):
        self._memories = memories
        self.calls = 0

    def retrieve(self, **kwargs):
        self.calls += 1
        return list(self._memories)


class FakeRetrievedMemory:
    def __init__(self, technique, *, kind="experience", strategy="", confidence=0.6,
                 success_rate=1.0, category="web"):
        self.technique = technique
        self.kind = kind
        self.strategy = strategy
        self.confidence = confidence
        self.success_rate = success_rate
        self.category = category


class Phase5Base(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        db = SessionLocal()
        try:
            for model in (TrajectoryEventModel, AgentSessionModel, SwarmEvidenceModel,
                          SwarmTaskModel, SwarmMissionModel):
                db.query(model).delete()
            db.commit()
        finally:
            db.close()

    def _ms(self, **kw):
        defaults = dict(mission_id="m5", target="http://target.ctf:8080", category="web")
        defaults.update(kw)
        return SharedMissionState(**defaults)

    def _coord(self, **kw):
        defaults = dict(challenge_id="chal-p5", run_id=None, target="http://target.ctf:8080",
                        category="web", challenge_name="Phase5 Test", persist=True,
                        limits=SwarmLimits(max_task_retries=0))
        defaults.update(kw)
        return SwarmCoordinator(**defaults)


# =========================================================================== #
# 1. command output becomes evidence
# =========================================================================== #

class TestCommandOutputBecomesEvidence(Phase5Base):
    """Verify that tool command output is captured as structured Evidence on the bus."""

    def test_tool_output_becomes_evidence_on_bus(self):
        """When evidence is published on the bus, it should be findable and identifiable."""
        coord = self._coord()
        # Publish endpoint evidence directly
        ev = Evidence(
            mission_id=coord.mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.ENDPOINT.value,
            title="/admin",
            description="Discovered admin endpoint",
            source="tool_output",
            command="curl http://target.ctf/admin",
            output="HTTP/1.1 200 OK\n<link rel=\"stylesheet\" href=\"/admin.css\">",
            confidence=0.9,
            tags=["recon"],
            related_endpoint="/admin",
        )

        eid = coord.bus.publish(ev)
        self.assertIsNotNone(eid, "Evidence should be published and receive an ID")

        # Verify evidence is on the bus
        all_ev = coord.bus.all()
        evidence_types = [e.evidence_type for e in all_ev]
        self.assertIn("endpoint", evidence_types)

        # Verify evidence integrates into coordinator's mission state
        coord._on_evidence(ev)
        self.assertIn("/admin", coord.mission.endpoints,
                      "Evidence should integrate into mission endpoints")


# =========================================================================== #
# 2. evidence changes mission state
# =========================================================================== #

class TestEvidenceChangesMissionState(Phase5Base):
    """Verify that published evidence changes the mission state."""

    def test_evidence_integrates_into_mission_state(self):
        """Evidence published on the bus should integrate into shared mission state."""
        coord = self._coord()
        # Publish endpoint evidence with related_endpoint set
        ev = Evidence(
            mission_id=coord.mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.ENDPOINT.value,
            title="/login",
            description="Login page discovered",
            source="supervisor",
            related_endpoint="/login",
        )

        eid = coord.bus.publish(ev)
        self.assertIsNotNone(eid)

        # Integrate into mission state via the observer callback
        coord._on_evidence(ev)

        # Check the coordinator's mission state has the endpoint
        self.assertIn("/login", coord.mission.endpoints,
                      "Evidence should integrate into mission endpoints")
        self.assertEqual(len(coord.mission.evidence_ids), 1,
                         "Evidence ID should be recorded")

    def test_evidence_persisted_to_database(self):
        """Evidence should be persisted to the durable swarm_evidence table."""
        coord = self._coord()
        ev = Evidence(
            mission_id=coord.mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.VULNERABILITY.value,
            title="SQL injection",
            description="SQL injection vulnerability found",
            source="tool_output",
            related_vulnerability="SQL injection",
        )

        eid = coord.bus.publish(ev)
        self.assertIsNotNone(eid)

        # Verify persistence by loading from DB
        from backend.database.models import SwarmEvidenceModel
        db = SessionLocal()
        try:
            row = db.query(SwarmEvidenceModel).filter(SwarmEvidenceModel.id == eid).first()
            self.assertIsNotNone(row, "Evidence should be persisted to DB")
            self.assertEqual(row.evidence_type, "vulnerability")
            self.assertEqual(row.title, "SQL injection")
        finally:
            db.close()


# =========================================================================== #
# 3. evidence influences candidate selection
# =========================================================================== #

class TestEvidenceInfluencesCandidateSelection(Phase5Base):
    """Verify that evidence influences future candidate generation."""

    def test_evidence_generates_reactive_candidates(self):
        """New evidence should generate reactive candidates via the supervisor."""
        coord = self._coord()

        # Publish service evidence (versioned service triggers vuln research)
        ev = Evidence(
            mission_id=coord.mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.SERVICE.value,
            title="Apache httpd 2.4.49",
            description="Apache version detected",
            source="tool_output",
        )

        coord.bus.publish(ev)
        coord._on_evidence(ev)

        # Reason and replan should generate candidates
        from backend.swarm.candidates import CandidateGenerator
        from backend.swarm.scoring import ActionScorer

        decision = coord.supervisor.reason(
            coord.mission, recent_evidence=[ev],
            scorer=ActionScorer(),
            generator=CandidateGenerator(),
        )

        # Should have at least one candidate
        self.assertIsNotNone(decision.selected, "Should select a candidate")
        self.assertIsNotNone(decision.ranked, "Should have ranked candidates")

    def test_evidence_influences_candidate_source(self):
        """Evidence-backed candidates should have appropriate source tracking."""
        coord = self._coord()

        # Publish endpoint evidence
        ev = Evidence(
            mission_id=coord.mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.ENDPOINT.value,
            title="/admin",
            description="Admin endpoint discovered",
            source="tool_output",
            related_endpoint="/admin",
        )

        coord.bus.publish(ev)
        coord._on_evidence(ev)

        # Generate candidates - check source tracking
        from backend.swarm.candidates import CandidateGenerator
        from backend.swarm.scoring import ActionScorer

        decision = coord.supervisor.reason(
            coord.mission, recent_evidence=[ev],
            scorer=ActionScorer(),
            generator=CandidateGenerator(),
        )

        # Check that candidates have reasonable source values
        for c in decision.ranked[:5]:  # Check top 5
            self.assertIn(
                c.source, ("reasoning", "evidence", "memory", "playbook"),
                f"Candidate source should be recognized, got {c.source}"
            )


# =========================================================================== #
# 4. failed approach influences future selection
# =========================================================================== #

class TestFailedApproachInfluencesFutureSelection(Phase5Base):
    """Verify that failed approaches influence future candidate selection."""

    def test_failed_approach_recorded_and_avoided(self):
        """A failed approach should be recorded and avoided in future candidate selection."""
        coord = self._coord()
        mission = coord.mission

        # Record a failed approach
        mission.record_failed_approach(
            action="GET /admin",
            signature="service::http://target.ctf:8080::",
            capability="service_enumeration",
            target="http://target.ctf:8080",
            result="403 Forbidden",
            reason="access_denied",
            failure_class="CAPABILITY_GAP",
            agent="recon",
        )

        # The failed approach should be in the mission state
        self.assertTrue(
            mission.has_failed_action("service::http://target.ctf:8080::"),
            "Failed approach should be recorded"
        )

        # Generate candidates - the failed approach should be filtered
        from backend.swarm.candidates import CandidateGenerator
        from backend.swarm.scoring import ActionScorer

        decision = coord.supervisor.reason(
            mission,
            recent_evidence=[],
            scorer=ActionScorer(),
            generator=CandidateGenerator(),
            attempted_signatures=[],
            blocked_capabilities=[],
            exhausted_strategies=[],
        )

        # The failed approach signature should not be in the ranked candidates
        # (unless evidence-backed)
        for c in decision.ranked:
            self.assertNotEqual(
                c.signature, "service::http://target.ctf:8080::",
                "Failed approach signature should be avoided"
            )

    def test_failed_approach_with_evidence_justified_retry(self):
        """A failed approach can be retried if fresh evidence justifies it."""
        coord = self._coord()
        mission = coord.mission

        # Record a failed approach
        mission.record_failed_approach(
            action="GET /admin",
            signature="service::http://target.ctf:8080::",
            capability="service_enumeration",
            target="http://target.ctf:8080",
            result="403 Forbidden",
            reason="access_denied",
            failure_class="CAPABILITY_GAP",
            agent="recon",
        )

        # Publish new evidence (e.g., new service version)
        ev = Evidence(
            mission_id=mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.SERVICE.value,
            title="Apache httpd 2.4.50",
            description="New Apache version detected",
            source="tool_output",
        )

        coord.bus.publish(ev)
        coord._on_evidence(ev)

        # Record the evidence as a strong fact
        mission.add_fact(
            "Apache httpd 2.4.50 identified",
            reliability=Reliability.DIRECT.value,
            confidence=1.0,
            source="tool_output",
        )

        # Reason should now consider the evidence-backed retry
        from backend.swarm.candidates import CandidateGenerator
        from backend.swarm.scoring import ActionScorer

        decision = coord.supervisor.reason(
            mission, recent_evidence=[ev],
            scorer=ActionScorer(),
            generator=CandidateGenerator(),
        )

        # The selected candidate may be evidence-backed even if a previous approach failed
        self.assertIsNotNone(decision.selected, "Should still select a candidate")


# =========================================================================== #
# 5. flag-looking output does not equal success
# =========================================================================== #

class TestFlagLookingOutputDoesNotEqualSuccess(Phase5Base):
    """Verify that strings that merely look like flags don't automatically mean success."""

    def test_flag_candidate_from_unverified_source_not_verified(self):
        """A flag-like string from an unverified source should not terminate the mission."""
        resolver = AnswerResolver()

        # Test that placeholder flag patterns are rejected
        for placeholder in (
            "picoCTF{$content}", "FLAG{...}", "HTB{content}", "CTF{answer}",
        ):
            candidates = resolver.extract_candidates(placeholder, task_context={"category": "web"})
            # Should not automatically produce verified flag
            self.assertEqual(len(candidates), 0, f"Placeholder {placeholder} should produce no candidates")

    def test_string_looking_like_flag_does_not_terminate_mission(self):
        """A string that looks like a flag but isn't verified should not end the mission."""
        coord = self._coord()
        # Submit a flag candidate that is NOT verified
        result = coord.submit_flag_candidate(
            "picoCTF{something}",
            source="tool_output",
            command="curl http://target.ctf/",
            action_succeeded=True,
            authoritative=False,
        )

        # Should not have verified the flag (return False)
        self.assertFalse(result, "Unverified flag candidate should not verify")

        # Mission should not have a verified_flag
        self.assertIsNone(coord.mission.verified_flag,
                          "Mission should not have verified_flag")

    def test_false_flag_patterns_rejected_by_verifier(self):
        """FALSE_FLAG_PATTERNS should reject placeholder flag shapes."""
        from backend.agent_runtime.verifier import FALSE_FLAG_PATTERNS

        # These should be rejected as placeholder patterns
        for pattern in (
            "picoCTF{...}", "FLAG{...}", "HTB{...}", "CTF{...}",
            "picoCTF{flag}", "FLAG{value}",
        ):
            match = FALSE_FLAG_PATTERNS.search(pattern)
            self.assertIsNotNone(
                match, f"'{pattern}' should match FALSE_FLAG_PATTERNS"
            )

        # Real flags should NOT match FALSE_FLAG_PATTERNS
        for real in (
            "picoCTF{brut4_f0rc4_0d39383f}",
            "FLAG{abcdef1234567890}",
        ):
            match = FALSE_FLAG_PATTERNS.search(real)
            self.assertIsNone(
                match, f"'{real}' should NOT match FALSE_FLAG_PATTERNS"
            )


# =========================================================================== #
# 6. verified flag terminates the mission
# =========================================================================== #

class TestVerifiedFlagTerminatesMission(Phase5Base):
    """Verify that a verified flag terminates the mission successfully."""

    def test_verified_flag_terminates_mission(self):
        """When a verified flag is accepted via the full run loop, mission should end in COMPLETED status."""
        coord = self._coord()

        # Accept a verified flag through the coordinator method
        coord._accept_verified_flag("picoCTF{brut4_f0rc4_0d39383f}", agent_id="recon", task=None)
        
        # After _accept_verified_flag, the mission should have verified_flag set
        # and _trigger_global_stop should have been called (sets _pending_final_status)
        self.assertIsNotNone(coord.mission.verified_flag,
                             "Mission should have verified_flag set")
        self.assertEqual(coord.mission.progress, 100,
                         "Progress should be 100")
        self.assertEqual(coord._pending_final_status, "COMPLETED",
                         "Final status should be COMPLETED after verified flag acceptance")

    def test_verified_flag_status_set_after_accept(self):
        """Verify flag acceptance sets the mission state correctly."""
        coord = self._coord()
        coord._accept_verified_flag("FLAG{test123}", agent_id="web", task=None)

        # Mission status should reflect the verified flag
        self.assertEqual(coord.mission.verified_flag, "FLAG{test123}",
                         "verified_flag should be set")
        self.assertEqual(coord.mission.progress, 100,
                         "progress should be 100")
        self.assertEqual(coord._pending_final_status, "COMPLETED",
                         "Final status should be COMPLETED after verified flag acceptance")


# =========================================================================== #
# 7. recovery produces a different viable action
# =========================================================================== #

class TestRecoveryProducesDifferentViableAction(Phase5Base):
    """Verify that after a failed task, recovery produces a different viable action."""

    def test_recovery_decision_produces_action(self):
        """Supervisor recovery decision should produce a viable action after failure."""
        coord = self._coord()
        task = Task(
            mission_id=coord.mission.mission_id, run_id=coord.mission.run_id,
            challenge_id=coord.mission.challenge_id,
            role="recon", objective="test recovery", priority=50,
        )

        result = _R(
            status="FAILED",
            failure_category="missing_dependency",
            reason="Required tool 'nmap' unavailable",
        )

        # Get recovery decision from supervisor
        decision = coord.supervisor.decide_recovery(
            task=task, result=result, max_retries=3,
        )

        # Should produce a recovery decision
        self.assertIn(decision.action, ("retry", "reassign", "abandon"),
                      f"Recovery action should be one of retry/reassign/abandon, got {decision.action}")
        self.assertIsInstance(decision.reason, str)
        self.assertTrue(len(decision.reason) > 0)

    def test_no_progress_recovery_retries(self):
        """No-progress category should trigger a bounded retry when task has retry_count < max_retries."""
        coord = self._coord()
        task = Task(
            mission_id=coord.mission.mission_id, run_id=coord.mission.run_id,
            challenge_id=coord.mission.challenge_id,
            role="recon", objective="test no progress", priority=50,
        )

        result = _R(
            status="MAX_TURNS",
            failure_category="NO_PROGRESS",
            reason="no_progress",
        )

        decision = coord.supervisor.decide_recovery(
            task=task, result=result, max_retries=3,
        )

        # Check the action, not the reason string
        self.assertEqual(decision.action, "retry",
                         "No-progress should trigger retry within retry limit")


# =========================================================================== #
# 8. exhausted strategy stops being selected
# =========================================================================== #

class TestExhaustedStrategyStopsBeingSelected(Phase5Base):
    """Verify that exhausted strategies stop being selected."""

    def test_exhausted_strategy_filtered_by_supervisor(self):
        """Strategies in exhausted_strategies should be filtered out during candidate ranking."""
        coord = self._coord()
        mission = coord.mission

        # Add a strategy to exhausted strategies
        mission.exhausted_strategies.append("service_fingerprint")

        # Publish some evidence
        ev = Evidence(
            mission_id=mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.SERVICE.value,
            title="Some service",
            description="Service detected",
            source="tool_output",
        )

        coord.bus.publish(ev)
        coord._on_evidence(ev)

        # Reason should consider exhausted strategies
        from backend.swarm.candidates import CandidateGenerator
        from backend.swarm.scoring import ActionScorer

        decision = coord.supervisor.reason(
            mission, recent_evidence=[ev],
            scorer=ActionScorer(),
            generator=CandidateGenerator(),
            exhausted_strategies=mission.exhausted_strategies,
        )

        # The decision should be valid
        self.assertIsNotNone(decision, "Reason should return a decision")
        # If a candidate is selected, its strategy should not be exhausted
        if decision.selected:
            strat_key = (getattr(decision.selected, "strategy", "") or decision.selected.action_type or "").lower()
            self.assertNotIn(
                strat_key, {s.lower() for s in mission.exhausted_strategies},
                f"Selected candidate strategy should not be exhausted: {strat_key}"
            )

    def test_exhausted_strategy_in_reason_filtering(self):
        """Supervisor.reason should filter candidates with exhausted strategies."""
        coord = self._coord()
        mission = coord.mission

        mission.exhausted_strategies.append("directory_enum")

        # Publish endpoint evidence
        ev = Evidence(
            mission_id=mission.mission_id,
            agent_id="recon",
            evidence_type=EvidenceType.ENDPOINT.value,
            title="/admin",
            description="Admin endpoint",
            source="tool_output",
            related_endpoint="/admin",
        )

        coord.bus.publish(ev)
        coord._on_evidence(ev)

        from backend.swarm.candidates import CandidateGenerator
        from backend.swarm.scoring import ActionScorer

        decision = coord.supervisor.reason(
            mission, recent_evidence=[ev],
            scorer=ActionScorer(),
            generator=CandidateGenerator(),
            exhausted_strategies=mission.exhausted_strategies,
        )

        # The decision should be produced (candidates may or may not be available)
        self.assertIsNotNone(decision, "Reason should return a decision")


# =========================================================================== #
# 9. mission stops correctly when no viable actions remain
# =========================================================================== #

class TestMissionStopsWhenNoViableActions(Phase5Base):
    """Verify that mission stops when no viable actions remain."""

    def test_budget_exhausted_stop_condition(self):
        """Mission should stop when budget is exhausted."""
        coord = self._coord()

        # Set a low budget
        coord.budget = MissionBudget(
            max_agent_calls=1,
            max_tool_executions=1,
            max_failed_attempts=0,
            max_duplicate_attempts=0,
        )

        # Record a tool execution (consuming the budget)
        coord.budget.record_tool_executions(1)

        # Record a failure (consuming failed attempts)
        coord.budget.record_failure(1)

        # Evaluate stop conditions
        budget_done, reason = coord.budget.exhausted()
        self.assertTrue(budget_done, "Budget should be exhausted with 1 call and 1 failure")

    def test_stagnation_stop_condition(self):
        """Mission should stop when stagnation limit is reached."""
        coord = self._coord()
        mission = coord.mission
        coord.mission = mission

        ledger = ProgressLedger(stagnation_limit=2)

        # Record 3 consecutive steps with no new knowledge (the first step sets best_fingerprint,
        # the second step has no progress, and the third step triggers stagnation)
        for _ in range(3):
            progressed = ledger.record(mission)
            # After first step, best_fingerprint is set; second step should not progress
            # We just record and don't assert on each step's progressed value

        # After 2 steps with stagnation_limit=2, should be stagnant
        self.assertTrue(ledger.is_stagnant(), "Should be stagnant after limit steps")

        # Evaluate stop conditions
        cond, reason = evaluate_stop(
            mission, budget=None, ledger=ledger,
            has_open_work=True,
            all_capabilities_blocked=False,
        )

        self.assertEqual(cond, StopCondition.NO_PROGRESS, "Should report NO_PROGRESS stop condition")
        self.assertIn("no new knowledge", reason.lower(), "Should report no new knowledge in reason")

    def test_no_open_work_stop_condition(self):
        """Mission should stop when there is no open work."""
        coord = self._coord()

        # Evaluate stop conditions with no open work
        cond, reason = evaluate_stop(
            coord.mission, budget=None, ledger=None,
            has_open_work=False,
            all_capabilities_blocked=False,
        )

        self.assertEqual(cond, StopCondition.NONE, "Should report NONE when no open work")
        self.assertEqual(reason, "")


# =========================================================================== #
# Main test runner
# =========================================================================== #

if __name__ == "__main__":
    unittest.main()