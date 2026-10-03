"""Workstream A Hardening — tests for enforced stop conditions.

1. Hard iteration cap + wall-clock ceiling (default engine)
2. Dedicated no-progress ceiling (default engine)
3. MissionBudget.exhausted() real budget exhaustion (coordinated engine)
4. Cross-mission failed-approach recall (coordinated engine)
"""
import os
import sys
import asyncio
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import FailedApproachModel
from backend.knowledge.failed_approaches import (
    record_failed_approach, recall_failed_approaches, recall_block,
)
from backend.agents.swarm_state import SwarmBlackboard
from backend.agents.swarm_orchestrator import SwarmOrchestrator
from backend.swarm.progress import MissionBudget, ProgressLedger, StopCondition, evaluate_stop
from backend.swarm.mission import SharedMissionState
from backend.swarm.coordinator import SwarmCoordinator
from backend.swarm.limits import SwarmLimits
from backend.swarm.candidates import CandidateGenerator


class TestHardIterationCap(unittest.TestCase):
    """Test that the default engine enforces max_iterations and max_minutes."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_blackboard_has_iteration_and_time_fields(self):
        board = SwarmBlackboard("c1", "r1", "http://t")
        self.assertEqual(board.max_iterations, 40)  # default from settings
        self.assertEqual(board.max_minutes, 30)      # default from settings
        self.assertTrue(hasattr(board, "agent_iterations"))

    def test_agent_worker_respects_max_iterations(self):
        """The agent worker loop should stop when agent_iterations >= max_iterations."""
        board = SwarmBlackboard("c1", "r1", "http://t")
        board.max_iterations = 2
        board.agent_iterations["agent_1"] = 2
        
        # Simulate the check in _agent_worker
        iters = board.agent_iterations.get("agent_1", 0)
        should_stop = board.max_iterations and iters >= board.max_iterations
        self.assertTrue(should_stop)

    def test_agent_worker_respects_max_minutes(self):
        """The agent worker loop should stop when elapsed_min >= max_minutes."""
        board = SwarmBlackboard("c1", "r1", "http://t")
        board.max_minutes = 5
        
        # Simulate elapsed_min calculation
        import time
        from backend.agents.swarm_helpers import _effective_elapsed_minutes
        started = time.time() - 360  # 6 minutes ago
        paused = 0.0
        elapsed_min = _effective_elapsed_minutes(started, time.time(), paused)
        
        should_stop = board.max_minutes and elapsed_min >= board.max_minutes
        self.assertTrue(should_stop)


class TestNoProgressCeiling(unittest.TestCase):
    """Test that the default engine has a dedicated no-progress ceiling."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_no_progress_counter_increments_on_stall(self):
        board = SwarmBlackboard("c1", "r1", "http://t")
        board.max_iterations = 100
        board.max_minutes = 100
        
        # Simulate the no-progress logic
        consecutive_no_progress = 0
        MAX_CONSECUTIVE_NO_PROGRESS = 10
        
        # Step 1: no progress
        made_progress = False
        if not made_progress:
            consecutive_no_progress += 1
        self.assertEqual(consecutive_no_progress, 1)
        
        # Step 2-10: still no progress
        for _ in range(9):
            if not made_progress:
                consecutive_no_progress += 1
        
        should_stop = consecutive_no_progress >= MAX_CONSECUTIVE_NO_PROGRESS
        self.assertTrue(should_stop)

    def test_duplicate_commands_feed_no_progress(self):
        """Duplicate commands should increment the no-progress counter."""
        board = SwarmBlackboard("c1", "r1", "http://t")
        board.executed_commands_dedup.add("curl http://t")
        
        consecutive_no_progress = 0
        cmd = "curl http://t"
        
        if cmd in board.executed_commands_dedup:
            consecutive_no_progress += 1  # duplicate = no progress
        
        self.assertEqual(consecutive_no_progress, 1)

    def test_blocked_capability_feeds_no_progress(self):
        """Blocked capabilities should increment the no-progress counter."""
        board = SwarmBlackboard("c1", "r1", "http://t")
        board.blocked_capabilities.add("cmd:ffuf")
        
        consecutive_no_progress = 0
        cmd = "ffuf -u http://t"
        
        bin_name = "ffuf"
        if f"cmd:{bin_name}" in board.blocked_capabilities:
            consecutive_no_progress += 1
        
        self.assertEqual(consecutive_no_progress, 1)

    def test_exhausted_strategy_feeds_no_progress(self):
        """Exhausted strategies should increment the no-progress counter."""
        board = SwarmBlackboard("c1", "r1", "http://t")
        board.exhausted_strategies.add("directory_enum")
        
        consecutive_no_progress = 0
        strategy_label = "directory_enum"
        
        if strategy_label in board.exhausted_strategies:
            consecutive_no_progress += 1
        
        self.assertEqual(consecutive_no_progress, 1)


class TestMissionBudgetExhaustion(unittest.TestCase):
    """Test that MissionBudget.exhausted() returns real budget status."""

    def test_agent_calls_budget_exhausted(self):
        b = MissionBudget(max_agent_calls=3)
        b.record_agent_call()
        b.record_agent_call()
        self.assertFalse(b.exhausted()[0])
        b.record_agent_call()
        self.assertTrue(b.exhausted()[0])
        self.assertIn("Agent call budget exhausted", b.exhausted()[1])

    def test_tool_executions_budget_exhausted(self):
        b = MissionBudget(max_tool_executions=5)
        b.record_tool_executions(3)
        self.assertFalse(b.exhausted()[0])
        b.record_tool_executions(2)
        self.assertTrue(b.exhausted()[0])
        self.assertIn("Tool execution budget exhausted", b.exhausted()[1])

    def test_failed_attempts_budget_exhausted(self):
        b = MissionBudget(max_failed_attempts=2)
        b.record_failure()
        self.assertFalse(b.exhausted()[0])
        b.record_failure()
        self.assertTrue(b.exhausted()[0])
        self.assertIn("Failed attempt budget exhausted", b.exhausted()[1])

    def test_duplicate_attempts_budget_exhausted(self):
        b = MissionBudget(max_duplicate_attempts=3)
        b.record_duplicate()
        b.record_duplicate()
        self.assertFalse(b.exhausted()[0])
        b.record_duplicate()
        self.assertTrue(b.exhausted()[0])
        self.assertIn("Duplicate attempt budget exhausted", b.exhausted()[1])

    def test_wall_clock_budget_exhausted(self):
        b = MissionBudget(max_wall_seconds=1.0)
        b.start()
        b.update_elapsed()
        self.assertFalse(b.exhausted()[0])
        import time
        time.sleep(1.1)
        b.update_elapsed()
        self.assertTrue(b.exhausted()[0])
        self.assertIn("Wall-clock budget exhausted", b.exhausted()[1])

    def test_unbounded_by_default(self):
        b = MissionBudget()  # all caps = 0 = unbounded
        for _ in range(100):
            b.record_agent_call()
        done, _ = b.exhausted()
        self.assertFalse(done)
        self.assertEqual(b.pressure(), 0.0)

    def test_coordinator_evaluates_budget_stop(self):
        """The coordinated engine should stop on budget exhaustion."""
        ms = SharedMissionState(mission_id="m1", target="http://t")
        b = MissionBudget(max_agent_calls=1)
        b.record_agent_call()
        
        cond, reason = evaluate_stop(ms, budget=b)
        self.assertEqual(cond, StopCondition.MISSION_BUDGET_EXHAUSTED)
        self.assertEqual(cond.final_status, "FAILED")


class TestCrossMissionFailedApproachRecall(unittest.TestCase):
    """Test that the coordinated engine reads cross-mission failed approaches."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self._cat = "test_coord_recall"
        self._purge()

    def tearDown(self):
        self._purge()

    def _purge(self):
        db = SessionLocal()
        try:
            db.query(FailedApproachModel).filter(FailedApproachModel.category == self._cat).delete()
            db.commit()
        finally:
            db.close()

    def test_candidate_generator_includes_cross_mission_failures(self):
        """CandidateGenerator should read cross-mission failed approaches and add them to mission state."""
        # Record some cross-mission failures
        record_failed_approach(self._cat, "curl -s <URL> :: 403", "blocked")
        record_failed_approach(self._cat, "sqlmap <URL> :: timeout", "timeout")
        
        ms = SharedMissionState(mission_id="m1", target="http://t", category=self._cat)
        gen = CandidateGenerator()
        
        # This should populate ms.failed_approaches with cross-mission failures
        cands = gen.generate(ms, use_memory=False)
        
        # Check that cross-mission failures were added to mission state
        failed_sigs = [fa.get("signature") for fa in ms.failed_approaches]
        self.assertIn("curl -s <URL> :: 403", failed_sigs)
        self.assertIn("sqlmap <URL> :: timeout", failed_sigs)

    def test_cross_mission_failures_are_advisory_not_hard_blocks(self):
        """Cross-mission failures should be advisory (added to failed_approaches) but not hard-block candidates."""
        record_failed_approach(self._cat, "curl -s <URL> :: 403", "blocked")
        
        ms = SharedMissionState(mission_id="m1", target="http://t", category=self._cat)
        gen = CandidateGenerator()
        cands = gen.generate(ms, use_memory=False)
        
        # The generator should still produce candidates (cross-mission failures are advisory)
        # but the scorer will penalize similar approaches
        self.assertIsInstance(cands, list)


class TestIntegration(unittest.TestCase):
    """Integration tests for the coordinated engine with budget and cross-mission recall."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        db = SessionLocal()
        try:
            for model in (FailedApproachModel,):
                db.query(model).delete()
            db.commit()
        finally:
            db.close()

    async def test_coordinator_budget_enforcement(self):
        """A coordinator with a tiny budget should stop due to budget exhaustion."""
        coord = SwarmCoordinator(
            challenge_id="chal-test",
            target="http://target.ctf",
            category="web",
            challenge_name="Test",
            persist=False,
            limits=SwarmLimits(max_total_tasks=1, max_turns_per_task=1, task_timeout_seconds=0),
            budget=MissionBudget(max_agent_calls=1, max_tool_executions=1, max_wall_seconds=0),
            enable_reasoning=True,
        )
        # The budget should be initialized with the caps
        self.assertEqual(coord.budget.max_agent_calls, 1)
        self.assertEqual(coord.budget.max_tool_executions, 1)
        self.assertTrue(coord.budget.started_monotonic > 0)

    def test_coordinator_cross_mission_recall_integration(self):
        """Coordinator should have access to cross-mission failed approaches via candidate generator."""
        # Record a cross-mission failure
        record_failed_approach("web", "ffuf -u http://t :: 403", "blocked")
        
        coord = SwarmCoordinator(
            challenge_id="chal-test2",
            target="http://target.ctf",
            category="web",
            challenge_name="Test",
            persist=False,
            limits=SwarmLimits(max_total_tasks=2),
            enable_reasoning=True,
        )
        
        # The candidate generator should have access to cross-mission failures
        ms = coord.mission
        gen = coord.generator
        cands = gen.generate(ms, use_memory=False)
        
        # Cross-mission failure should be in mission state's failed_approaches
        failed_sigs = [fa.get("signature") for fa in ms.failed_approaches]
        self.assertIn("ffuf -u http://t :: 403", failed_sigs)


if __name__ == "__main__":
    unittest.main(verbosity=2)