"""Regression tests for production-path fixes:
1. Checkpoint does not deadlock on unattended runs (times out cleanly and resumes).
2. Checkpoint human responses (HITL) still work and inject evaluated directives.
3. Mission stop / pause during checkpoint exits cleanly without orphan pauses.
4. Competition harness and UI start paths resolve to the same production engine (swarm).
"""

import os
import sys
import unittest
import asyncio
import time

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import ChallengeModel, RunModel, CheckpointModel
from backend.agents.swarm_orchestrator import (
    SwarmBlackboard,
    SwarmOrchestrator,
    swarm_orchestrator,
)
from backend.api.runner import workflow_runner
from backend.config import settings


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestProductionCheckpointAndHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        workflow_runner.reset()

    def tearDown(self):
        workflow_runner.reset()

    # ── Test 1: Checkpoint Does Not Deadlock on Unattended Run ──────────────
    def test_checkpoint_unattended_timeout_resumes_cleanly(self):
        """Verify that when no operator response is supplied, the checkpoint
        times out cleanly within the bounded timeout instead of blocking forever,
        and agents resume execution."""
        async def scenario():
            import uuid
            uid = uuid.uuid4().hex[:8]
            ch_id = f"ch_deadlock_{uid}"
            run_id = f"run_deadlock_{uid}"
            board = SwarmBlackboard(ch_id, run_id, "http://127.0.0.1:8888")
            board.agent_ids = ["agent_1", "agent_2"]
            board.agent_started_ts = {"agent_1": time.time(), "agent_2": time.time()}
            board.agent_iterations = {"agent_1": 10, "agent_2": 10}

            # Create DB records
            db = SessionLocal()
            ch = ChallengeModel(id=ch_id, name="Deadlock Test", category="WEB")
            run = RunModel(id=run_id, challenge_id=ch_id, status="RUNNING")
            db.add(ch)
            db.add(run)
            db.commit()
            db.close()

            # Temporarily configure a short timeout for test
            orig_timeout = getattr(settings, "CHECKPOINT_TIMEOUT_SECONDS", 30)
            settings.CHECKPOINT_TIMEOUT_SECONDS = 0.5

            try:
                orch = SwarmOrchestrator()
                # Run one checkpoint cycle without operator response
                start_t = time.time()
                await orch._run_checkpoint_cycle(board, ".")
                elapsed = time.time() - start_t

                # Verify bounded wait (< 5s with quiesce, not infinite)
                self.assertLess(elapsed, 5.0)
                # Verify pause flag is cleared
                self.assertFalse(board.checkpoint_pause)
                self.assertFalse(board.checkpoint_active)
                # Verify cycle counter advanced
                self.assertEqual(board.cycle_n, 1)
                # Verify DB status restored to RUNNING
                db_check = SessionLocal()
                run_obj = db_check.query(RunModel).filter(RunModel.id == run_id).first()
                self.assertEqual(run_obj.status, "RUNNING")
                db_check.close()
            finally:
                settings.CHECKPOINT_TIMEOUT_SECONDS = orig_timeout

        _run(scenario())

    # ── Test 2: Checkpoint Human Response Still Works (HITL) ─────────────────
    def test_checkpoint_human_response_still_works(self):
        """Verify that providing an operator response resumes the checkpoint
        immediately and injects evaluated suggestions to the targeted agents."""
        async def scenario():
            import uuid
            uid = uuid.uuid4().hex[:8]
            ch_id = f"ch_hitl_{uid}"
            run_id = f"run_hitl_{uid}"
            board = SwarmBlackboard(ch_id, run_id, "http://127.0.0.1:8888")
            board.agent_ids = ["agent_1", "agent_2"]
            board.agent_started_ts = {"agent_1": time.time(), "agent_2": time.time()}

            db = SessionLocal()
            ch = ChallengeModel(id=ch_id, name="HITL Test", category="WEB")
            run = RunModel(id=run_id, challenge_id=ch_id, status="RUNNING")
            db.add(ch)
            db.add(run)
            db.commit()
            db.close()

            orig_timeout = getattr(settings, "CHECKPOINT_TIMEOUT_SECONDS", 30)
            settings.CHECKPOINT_TIMEOUT_SECONDS = 10.0

            orch = SwarmOrchestrator()
            orch.active_swarms[board.run_id] = board

            async def submit_response_delayed():
                await asyncio.sleep(2.5)  # wait for 2.0s quiesce + small buffer
                resp_text = (
                    "--- suggestion: agent_1 ---\n"
                    "curl -s http://127.0.0.1:8888/admin_panel\n\n"
                    "--- suggestion: agent_2 ---\n"
                    "curl -s http://127.0.0.1:8888/robots.txt\n"
                )
                await orch.submit_checkpoint_response(board.challenge_id, resp_text)

            try:
                submit_task = asyncio.create_task(submit_response_delayed())
                start_t = time.time()
                await orch._run_checkpoint_cycle(board, ".")
                await submit_task
                elapsed = time.time() - start_t

                # Should resume quickly after operator submission (~2.5-4.0s, well before 10s timeout)
                self.assertLess(elapsed, 6.0)
                self.assertFalse(board.checkpoint_pause)
                self.assertFalse(board.checkpoint_active)
                # Verify directives were injected
                self.assertIn("admin_panel", board.agent_directives.get("agent_1", ""))
                self.assertIn("robots.txt", board.agent_directives.get("agent_2", ""))
            finally:
                settings.CHECKPOINT_TIMEOUT_SECONDS = orig_timeout
                orch.active_swarms.pop(board.run_id, None)

        _run(scenario())

    # ── Test 3: Mission Stop During Checkpoint Wait ──────────────────────────
    def test_mission_stop_during_checkpoint_wait_cleans_up(self):
        """Verify that stopping/pausing the mission while waiting at a checkpoint
        aborts the wait immediately and resets pause state cleanly."""
        async def scenario():
            import uuid
            uid = uuid.uuid4().hex[:8]
            ch_id = f"ch_stop_{uid}"
            run_id = f"run_stop_{uid}"
            board = SwarmBlackboard(ch_id, run_id, "http://127.0.0.1:8888")
            board.agent_ids = ["agent_1"]
            board.agent_started_ts = {"agent_1": time.time()}

            db = SessionLocal()
            ch = ChallengeModel(id=ch_id, name="Stop Test", category="WEB")
            run = RunModel(id=run_id, challenge_id=ch_id, status="RUNNING")
            db.add(ch)
            db.add(run)
            db.commit()
            db.close()

            orig_timeout = getattr(settings, "CHECKPOINT_TIMEOUT_SECONDS", 30)
            settings.CHECKPOINT_TIMEOUT_SECONDS = 20.0

            orch = SwarmOrchestrator()
            orch.active_swarms[board.run_id] = board

            async def stop_delayed():
                await asyncio.sleep(2.3)  # wait for quiesce + trigger pause
                await orch.request_pause(board.challenge_id)

            try:
                stop_task = asyncio.create_task(stop_delayed())
                start_t = time.time()
                await orch._run_checkpoint_cycle(board, ".")
                await stop_task
                elapsed = time.time() - start_t

                # Should abort wait immediately upon pause signal
                self.assertLess(elapsed, 5.0)
                self.assertTrue(board.is_stopped)
                self.assertTrue(board.pause_requested)
                self.assertFalse(board.checkpoint_pause)
                self.assertFalse(board.checkpoint_active)
            finally:
                settings.CHECKPOINT_TIMEOUT_SECONDS = orig_timeout
                orch.active_swarms.pop(board.run_id, None)

        _run(scenario())

    # ── Test 4: Competition Harness & WorkflowRunner Production Engine Alignment ──
    def test_workflow_runner_and_harness_use_production_engine(self):
        """Verify that WorkflowRunner.start_run with default engine_type (None)
        resolves to 'swarm' (SwarmOrchestrator), exactly matching the UI start path."""
        async def scenario():
            db = SessionLocal()
            try:
                ch = ChallengeModel(name="Production Engine Verification", category="WEB")
                db.add(ch)
                db.commit()

                run = RunModel(challenge_id=ch.id, status="RUNNING", current_phase="recon", current_agent="swarm")
                db.add(run)
                db.commit()

                # Start run with engine_type=None (default UI path)
                workflow_runner.start_run(run.id, ch.id, "http://127.0.0.1:8888/", engine_type=None)

                self.assertIn(run.id, workflow_runner.active_runs)
                task = workflow_runner.tasks.get(run.id)
                self.assertIsNotNone(task)

                # Allow the event loop to start run_swarm
                await asyncio.sleep(0.2)

                # Check that swarm_orchestrator registered the swarm board
                from backend.agents.swarm_orchestrator import swarm_orchestrator
                active_board = swarm_orchestrator.active_swarms.get(run.id)
                self.assertIsNotNone(active_board)
                self.assertEqual(active_board.challenge_id, ch.id)

                # Cancel run to clean up
                workflow_runner.activate_kill_switch(run.id)
            finally:
                db.close()

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
