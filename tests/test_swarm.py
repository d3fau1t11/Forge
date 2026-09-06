"""Unit tests for FORGE Swarm Architecture, Blackboard, Circuit Breaker, and Keep-Awake Engine."""

import unittest
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.engine.keep_awake import keep_awake_manager
from backend.agents.swarm_orchestrator import SwarmBlackboard, SwarmTask
from backend.providers.quota_manager import quota_manager

class TestSwarmEngine(unittest.TestCase):

    def test_keep_awake_lifecycle(self):
        initial_holds = keep_awake_manager.active_holds_count
        
        # Acquire hold 1
        keep_awake_manager.acquire("Test Challenge Run 1")
        self.assertEqual(keep_awake_manager.active_holds_count, initial_holds + 1)
        self.assertTrue(keep_awake_manager.is_active)

        # Acquire hold 2
        keep_awake_manager.acquire("Test Challenge Run 2")
        self.assertEqual(keep_awake_manager.active_holds_count, initial_holds + 2)

        # Release hold 2
        keep_awake_manager.release("Test Challenge Run 2 finished")
        self.assertEqual(keep_awake_manager.active_holds_count, initial_holds + 1)

        # Release hold 1
        keep_awake_manager.release("Test Challenge Run 1 finished")
        self.assertEqual(keep_awake_manager.active_holds_count, initial_holds)

    def test_session_circuit_breaker(self):
        test_provider = "test_rate_limited_provider"
        self.assertFalse(quota_manager.is_blacklisted_for_session(test_provider))

        # Blacklist provider
        quota_manager.blacklist_for_session(test_provider, "HTTP 429 Rate Limit")
        self.assertTrue(quota_manager.is_blacklisted_for_session(test_provider))

        # Clean reset
        quota_manager.reset_session_blacklists()
        self.assertFalse(quota_manager.is_blacklisted_for_session(test_provider))

    def test_swarm_blackboard_lifecycle(self):
        async def run_blackboard_flow():
            board = SwarmBlackboard("test_ch_1", "test_run_1", "http://127.0.0.1:8000")

            # 1. Add tasks
            t1 = await board.add_task("RECON", "Scan web ports", priority=5)
            t2 = await board.add_task("CODE_AUDIT", "Deobfuscate JS script", priority=3)
            self.assertEqual(len(board.task_pool), 2)

            # 2. Claim recon task
            claimed = await board.claim_task("worker_recon", ["RECON"])
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.task_id, t1.task_id)
            self.assertEqual(claimed.status, "CLAIMED")
            self.assertEqual(claimed.claimed_by, "worker_recon")

            # 3. Complete task with discoveries
            await board.complete_task(
                claimed.task_id,
                result="Found /admin",
                discoveries={"endpoints": ["/admin", "/login"], "headers": {"X-Dev-Access": "yes"}}
            )
            self.assertEqual(claimed.status, "COMPLETED")
            self.assertIn("/admin", board.discovered_endpoints)
            self.assertEqual(board.extracted_headers.get("X-Dev-Access"), "yes")

            # 4. Record flag and verify event set
            self.assertFalse(board.flag_event.is_set())
            await board.record_flag("picoCTF{test_swarm_flag_123}", "worker_exploit")
            self.assertTrue(board.flag_event.is_set())
            self.assertEqual(board.flag_captured, "picoCTF{test_swarm_flag_123}")

        asyncio.run(run_blackboard_flow())

if __name__ == "__main__":
    unittest.main()
