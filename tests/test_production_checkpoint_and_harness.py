"""Regression tests for the removal of the 5-minute interactive operator checkpoint.

The legacy flexible-agent swarm (``run_swarm``) used to hard-pause every agent on a
timer and wait for the operator to paste an external-model response. That mechanism is
gone. These tests pin the removal in place:

1. The interactive checkpoint orchestration methods are no longer present.
2. ``run_swarm`` dispatches no checkpoint task that could pause agents on a timer.
3. ``_agent_worker`` has no checkpoint hard-pause.
4. No ``CHECKPOINT_INTERVAL_SECONDS`` / ``CHECKPOINT_TIMEOUT_SECONDS`` setting exists,
   so a stale .env value cannot resurrect the timer.
5. The operator checkpoint HTTP endpoints are removed; the persistence checkpoint
   endpoints are retained.
6. The production engine routing (SwarmCoordinator default; legacy swarm selectable)
   is unchanged, and the coordinator persistence checkpoint remains.
"""

import inspect
import os
import sys
import unittest
import asyncio

# Pinned above the first backend import on purpose. Importing backend used to repoint
# DATABASE_URL at production forge.db (load_dotenv override=True) at import time, so
# the pin had to be re-applied afterwards. It cannot any more: pydantic-settings gives
# real environment variables precedence over .env, and backend/database/guard.py
# refuses to build an engine for forge.db without an authorization that only the
# server's startup hook makes. Never point this at forge.db: other modules' tearDowns
# delete real rows.
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import ChallengeModel, RunModel
from backend.agents.swarm_orchestrator import (
    SwarmOrchestrator,
    swarm_orchestrator,
)
from backend.agents.swarm_state import SwarmBlackboard
from backend.api.runner import workflow_runner
from backend.config import settings


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestCheckpointInterruptionRemoved(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        workflow_runner.reset()

    def tearDown(self):
        workflow_runner.reset()

    # ── Test 1: interactive checkpoint orchestration is gone ────────────────
    def test_checkpoint_coordinator_methods_removed(self):
        """The 5-minute interactive checkpoint must no longer exist: neither the
        coordinator loop, the per-cycle pause, nor the operator-response entry
        point may remain on the orchestrator."""
        orch = SwarmOrchestrator()
        for name in ("_checkpoint_coordinator", "_run_checkpoint_cycle",
                     "submit_checkpoint_response"):
            self.assertFalse(
                hasattr(orch, name),
                f"{name} should be removed with the checkpoint mechanism")

    def test_run_swarm_has_no_checkpoint_task(self):
        """run_swarm must dispatch only worker + flag tasks — no checkpoint task
        that could pause agents on a timer."""
        src = inspect.getsource(swarm_orchestrator.run_swarm)
        self.assertNotIn("_checkpoint_coordinator", src)
        self.assertNotIn("checkpoint_task", src)
        self.assertNotIn("checkpoint_response_event", src)

    def test_agent_worker_has_no_checkpoint_pause(self):
        src = inspect.getsource(swarm_orchestrator._agent_worker)
        self.assertNotIn("checkpoint_pause", src)
        self.assertNotIn("Paused at operator checkpoint", src)

    # ── Test 2: stale config cannot resurrect the timer ─────────────────────
    def test_checkpoint_settings_removed_and_env_ignored(self):
        """Neither settings attribute may exist, and a stale .env value for them
        must not bring the timer back."""
        for name in ("CHECKPOINT_INTERVAL_SECONDS", "CHECKPOINT_TIMEOUT_SECONDS"):
            self.assertFalse(
                hasattr(settings, name),
                f"{name} must be removed so a stale .env cannot resurrect it")
        # A stale environment variable must be ignored (pydantic extra='ignore',
        # and the fallback Settings never reads it either).
        os.environ["CHECKPOINT_INTERVAL_SECONDS"] = "5"
        try:
            from backend.config import Settings
            fresh = Settings()
            self.assertFalse(
                hasattr(fresh, "CHECKPOINT_INTERVAL_SECONDS"),
                "A stale CHECKPOINT_INTERVAL_SECONDS must not resurrect the timer")
        finally:
            os.environ.pop("CHECKPOINT_INTERVAL_SECONDS", None)

    def test_run_swarm_selectable_for_compatibility(self):
        self.assertTrue(callable(getattr(swarm_orchestrator, "run_swarm", None)))

    # ── Test 3: no WAITING_FOR_USER from the checkpoint mechanism ───────────
    def test_board_never_pauses_for_checkpoint(self):
        board = SwarmBlackboard("ch_x", "run_x", "http://127.0.0.1:8888")
        # Nothing in the autonomous workflow can set this any more.
        self.assertFalse(board.checkpoint_active)
        plan = board.save_snapshot()
        self.assertNotEqual(plan.get("status"), "WAITING_FOR_USER")

    # ── Test 4: operator checkpoint endpoints removed, persistence kept ──────
    def test_operator_checkpoint_endpoints_removed(self):
        from backend.api.routes.runs_and_checkpoints import router
        paths = {getattr(r, "path", "") for r in router.routes}
        self.assertNotIn("/challenges/{challenge_id}/checkpoint", paths)
        self.assertNotIn("/challenges/{challenge_id}/checkpoint/respond", paths)
        # The non-interactive persistence checkpoint endpoints remain.
        self.assertIn("/checkpoints", paths)

    def test_coordinator_persistence_checkpoint_intact(self):
        from backend.swarm.coordinator import SwarmCoordinator
        self.assertTrue(hasattr(SwarmCoordinator, "_maybe_checkpoint"))

    # ── Test 5: Competition Harness & WorkflowRunner Production Engine Path ──
    def test_workflow_runner_and_harness_use_production_engine(self):
        """Verify that WorkflowRunner.start_run with default engine_type (None)
        uses SwarmCoordinator as the production engine, and that explicit
        legacy engine_type="swarm" still selects the flexible-agent swarm."""
        async def scenario():
            import uuid
            from unittest.mock import patch

            uid = uuid.uuid4().hex[:8]
            ch_id = f"ch_engine_{uid}"

            db = SessionLocal()
            try:
                ch = ChallengeModel(id=ch_id, name="Production Engine Verification", category="WEB")
                db.add(ch)
                db.commit()

                # Reset shared state between test halves
                workflow_runner.reset()
                from backend.agents.swarm_orchestrator import swarm_orchestrator
                swarm_orchestrator.active_swarms.clear()

                # Save original run_swarm to call it directly
                original_run_swarm = swarm_orchestrator.run_swarm

                # Test 1: Default engine_type=None uses SwarmCoordinator
                run_id_1 = f"run_coord_{uid}"
                workflow_runner.start_run(
                    run_id=run_id_1, challenge_id=ch_id, target="http://127.0.0.1:8888/",
                    engine_type=None,  # default - should use SwarmCoordinator
                )

                self.assertIn(run_id_1, workflow_runner.active_runs)
                # Verify SwarmCoordinator is used: no legacy swarm_orchestrator board should exist
                self.assertIsNone(swarm_orchestrator.active_swarms.get(run_id_1),
                                  "Default engine should be SwarmCoordinator, not swarm_orchestrator")

                # Clean up
                workflow_runner.reset()
                swarm_orchestrator.active_swarms.clear()

                # Test 2: Explicit engine_type="swarm" uses legacy swarm_orchestrator
                run_id_2 = f"run_legacy_{uid}"
                called_kwargs = None

                async def spied_run_swarm(run_id, challenge_id, target_scope, working_directory,
                                          category="WEB", difficulty="EASY", resume=False,
                                          challenge_name="", platform="", description="",
                                          flag_pattern="", max_iterations=0, max_minutes=0,
                                          max_tokens=0, attached_file_paths=None,
                                          instance_expiry_ts=None):
                    nonlocal called_kwargs
                    called_kwargs = {
                        'run_id': run_id,
                        'challenge_id': challenge_id,
                        'target_scope': target_scope,
                        'working_directory': working_directory,
                        'category': category,
                        'difficulty': difficulty,
                        'resume': resume,
                        'challenge_name': challenge_name,
                        'platform': platform,
                        'description': description,
                        'flag_pattern': flag_pattern,
                        'max_iterations': max_iterations,
                        'max_minutes': max_minutes,
                        'max_tokens': max_tokens,
                        'attached_file_paths': attached_file_paths or [],
                        'instance_expiry_ts': instance_expiry_ts,
                    }
                    # Call the REAL run_swarm (not the patched version)
                    return await original_run_swarm(
                        run_id=run_id, challenge_id=challenge_id, target_scope=target_scope,
                        working_directory=working_directory, category=category, difficulty=difficulty,
                        resume=resume, challenge_name=challenge_name, platform=platform,
                        description=description, flag_pattern=flag_pattern,
                        max_iterations=max_iterations, max_minutes=max_minutes,
                        max_tokens=max_tokens, attached_file_paths=attached_file_paths or [],
                        instance_expiry_ts=instance_expiry_ts
                    )

                # Patch run_swarm with our spied version
                swarm_orchestrator.run_swarm = spied_run_swarm

                workflow_runner.start_run(
                    run_id=run_id_2, challenge_id=ch_id, target="http://127.0.0.1:8888/",
                    engine_type="swarm",  # explicit legacy engine
                )

                self.assertIn(run_id_2, workflow_runner.active_runs)
                # Verify legacy engine: swarm_orchestrator board should exist
                # (give it a moment to spawn agents)
                await asyncio.sleep(0.3)
                active_board = swarm_orchestrator.active_swarms.get(run_id_2)
                self.assertIsNotNone(active_board,
                                    "Explicit engine_type='swarm' should use swarm_orchestrator")
                self.assertEqual(active_board.challenge_id, ch_id)

                # Verify that run_swarm was actually executed with run_id and challenge_id
                self.assertIsNotNone(called_kwargs, "run_swarm should have been called")
                self.assertEqual(called_kwargs.get("run_id"), run_id_2)
                self.assertEqual(called_kwargs.get("challenge_id"), ch_id)

                # Cancel run to clean up
                workflow_runner.activate_kill_switch(run_id_2)

            finally:
                db.close()

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
