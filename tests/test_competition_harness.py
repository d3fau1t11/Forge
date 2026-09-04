import unittest
import asyncio
import os
import shutil

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import ChallengeModel, TargetProfileModel, RunModel, CheckpointModel, EvidenceModel, FindingModel
from backend.tools.manager import tool_manager
from backend.privilege.manager import privilege_manager
from backend.providers.router import model_router
from backend.agents.orchestrator_loop import orchestrator_loop
from backend.api.runner import workflow_runner
from tests.fixtures.web_target import LocalCTFServer
from tests.fixtures.forensics_fixture import create_forensics_fixture

class TestCompetitionHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.server = LocalCTFServer(port=8888)
        cls.server.start()
        cls.forensics_path = create_forensics_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_real_tool_manager_execution(self):
        """Tool Manager Validation: Execute real CLI tools on host system."""
        async def run_curl():
            return await tool_manager.execute_capability("web_testing", "http://127.0.0.1:8888/")

        res = asyncio.run(run_curl())
        self.assertEqual(res.status, "SUCCESS")
        self.assertIn("FORGE Web Target", res.stdout)
        self.assertEqual(res.exit_code, 0)
        self.assertGreater(res.duration_ms, 0)

    def test_security_validation_denied(self):
        """Security Validation: Verify arbitrary command injection & invalid tool requests are blocked."""
        db = SessionLocal()
        try:
            # Arbitrary shell request attempt
            approved = privilege_manager.evaluate_privilege(
                agent="attacker_llm",
                tool_name="rm -rf /",
                privilege_level="DANGEROUS",
                db=db
            )
            self.assertFalse(approved)
        finally:
            db.close()

    def test_forensics_strings_tool_execution(self):
        """Forensics Execution: Run strings CLI tool on forensic sample."""
        async def run_strings():
            return await tool_manager.execute_capability("file_analysis", self.forensics_path)

        res = asyncio.run(run_strings())
        self.assertIn(res.status, ["SUCCESS", "MISSING_TOOL"])
        if res.status == "SUCCESS":
            self.assertIn("FLAG{forge_forensics_strings_found_9999}", res.stdout)

    def test_checkpoint_chaos_and_resume(self):
        """Checkpoint Chaos Testing: Simulate app restart, DB reload, and run resumption."""
        db = SessionLocal()
        try:
            ch = ChallengeModel(name="Chaos Test Challenge", category="web")
            db.add(ch)
            db.commit()

            target = TargetProfileModel(challenge_id=ch.id, current_address="127.0.0.1:8888")
            db.add(target)
            db.commit()

            run = RunModel(challenge_id=ch.id, status="RUNNING", current_phase="recon", current_agent="orchestrator")
            db.add(run)
            db.commit()

            workflow_runner.start_run(run.id, ch.id, "127.0.0.1:8888")

            # Execute Step 1
            async def step1():
                return await orchestrator_loop.execute_run_step(db, run.id)

            res1 = asyncio.run(step1())
            self.assertEqual(res1["status"], "RUNNING")

            # Simulate Crash & Restart: Reload run & checkpoint from DB
            db_fresh = SessionLocal()
            run_reloaded = db_fresh.query(RunModel).filter(RunModel.id == run.id).first()
            cp = db_fresh.query(CheckpointModel).filter(CheckpointModel.run_id == run.id).first()

            self.assertIsNotNone(cp)
            self.assertEqual(run_reloaded.current_phase, "web")

            db_fresh.close()
        finally:
            db.close()

    def test_kill_switch_process_interruption(self):
        """Kill Switch Validation: Immediate halt of autonomous loop."""
        db = SessionLocal()
        try:
            ch = ChallengeModel(name="Kill Switch E2E Test", category="recon")
            db.add(ch)
            db.commit()

            run = RunModel(challenge_id=ch.id, status="RUNNING", current_phase="recon", current_agent="recon")
            db.add(run)
            db.commit()

            workflow_runner.start_run(run.id, ch.id, "127.0.0.1:8888")
            workflow_runner.activate_kill_switch(run.id)

            async def step():
                return await orchestrator_loop.execute_run_step(db, run.id)

            res = asyncio.run(step())
            self.assertEqual(res["status"], "CANCELLED")
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
