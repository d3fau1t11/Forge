"""Regression tests for the legacy orchestrator engine's flag-capture gate.

The orchestrator's ``run_autonomous_loop`` historically had three unguarded capture
sites that promoted a flag-shaped value straight to a completed mission:

* a model JSON completion (``{"action": "complete", "flag": ...}``),
* a flag-shaped value in the model's raw prose,
* a flag-shaped value in tool output — with no provenance check at all.

All three called ``_record_flag_capture`` directly, which marked the challenge
COMPLETED, wrote a verified finding, triggered success learning, and broadcast
``FLAG_CAPTURED``.

These tests drive the **real** ``run_autonomous_loop`` (with the provider, tool
manager, planning, playbook vault, report generator and websocket manager stubbed)
to prove every one of those paths now fails closed, and that a genuine, simply
scoped target request still reaches the supported verification path.

Honesty note: the "genuine" values below are synthetic responses produced by a
stubbed tool manager. They prove the *mechanism* (a target-derived response is
still verifiable), not that any real challenge flag was captured.
"""

import os
import sys
import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import SessionLocal, init_db
from backend.database.models import ChallengeModel, EvidenceModel, FindingModel, RunModel
from backend.tools.manager import ToolExecutionResult
from backend.agents.orchestrator_loop import orchestrator_loop
from backend.agent_runtime.verifier import (
    FlagSource, FlagStatus, FlagVerifier, EvidenceOrigin,
)


async def _no_sleep(*_args, **_kwargs):
    return None


def _tool_result(*, stdout="", stderr="", status="SUCCESS", exit_code=0, execution_failure=False):
    return ToolExecutionResult(
        tool_name="bash", capability="custom_command", command="",
        status=status, stdout=stdout, stderr=stderr, exit_code=exit_code,
        execution_failure=execution_failure,
    )


class _LoopHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def _seed(self, *, working_directory=""):
        db = SessionLocal()
        try:
            ch = ChallengeModel(name="Gate Challenge", category="web",
                                working_directory=working_directory or tempfile.mkdtemp())
            db.add(ch)
            db.commit()
            run = RunModel(challenge_id=ch.id, status="RUNNING",
                           current_phase="recon", current_agent="orchestrator")
            db.add(run)
            db.commit()
            return ch.id, run.id
        finally:
            db.close()

    def _run_loop(self, responses, tool_result, events):
        """Run the real loop with all external collaborators stubbed.

        ``responses`` is the model's reply per turn; ``tool_result`` is returned for
        every command execution. Returns the playbook vault double so callers can
        assert success-learning did not fire.
        """
        stack = ExitStack()
        self.addCleanup(stack.close)

        router = MagicMock()
        seq = list(responses)

        async def route_request(*_a, **_k):
            return SimpleNamespace(content=(seq.pop(0) if seq else "DONE"), model="scripted")

        router.route_request = route_request
        stack.enter_context(patch("backend.agents.orchestrator_loop.model_router", router))

        tm = MagicMock()

        async def execute_raw_command(*_a, **_k):
            return tool_result

        tm.execute_raw_command = execute_raw_command
        stack.enter_context(patch("backend.agents.orchestrator_loop.tool_manager", tm))

        ws = MagicMock()

        async def broadcast(payload):
            events.append(payload)

        ws.broadcast = broadcast
        stack.enter_context(patch("backend.agents.orchestrator_loop.ws_manager", ws))

        sp = MagicMock()
        sp.generate_initial_plan = AsyncMock(return_value={"tasks": [], "status": "IN_PROGRESS"})
        sp.calculate_progress = MagicMock(return_value=0)
        sp.update_task_progress = MagicMock(return_value={"tasks": [], "status": "IN_PROGRESS"})
        sp.review_and_adapt_plan = AsyncMock(return_value={"tasks": [], "status": "IN_PROGRESS"})
        stack.enter_context(patch("backend.agents.orchestrator_loop.strategic_planner", sp))

        pb = MagicMock()
        pb.search_playbooks = MagicMock(return_value=[])
        pb.synthesize_from_run = MagicMock()
        stack.enter_context(patch("backend.agents.orchestrator_loop.playbook_vault", pb))

        tr = MagicMock()
        tr.get_cached_recon = MagicMock(return_value=None)
        stack.enter_context(patch("backend.agents.orchestrator_loop.turbo_recon", tr))

        rg = MagicMock()
        rg.generate_readme = MagicMock()
        stack.enter_context(patch("backend.agents.orchestrator_loop.report_generator", rg))

        sc = MagicMock()
        sc.condense_output = MagicMock(return_value="condensed")
        stack.enter_context(patch("backend.agents.orchestrator_loop.stream_condenser", sc))

        ck = MagicMock()
        ck.create_checkpoint = MagicMock()
        stack.enter_context(patch("backend.agents.orchestrator_loop.checkpoint_manager", ck))

        ed = MagicMock()
        ed.detect_environment = MagicMock(return_value={
            "distro": "Test", "installed_tools": {}, "installed_python_libs": {}})
        stack.enter_context(patch("backend.agents.orchestrator_loop.environment_detector", ed))

        wr = MagicMock()
        wr.is_cancelled = MagicMock(return_value=False)
        wr.is_kill_switch_active = MagicMock(return_value=False)
        stack.enter_context(patch("backend.agents.orchestrator_loop.workflow_runner", wr))

        stack.enter_context(patch("asyncio.sleep", new=_no_sleep))
        return pb

    def _reload(self, challenge_id):
        db = SessionLocal()
        try:
            return db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
        finally:
            db.close()

    def _assert_no_capture(self, challenge_id, events, pb, *, expected_status="AWAITING_FLAG"):
        ch = self._reload(challenge_id)
        self.assertNotEqual(ch.flag_status, "CAPTURED")
        self.assertNotEqual(ch.status, "COMPLETED")
        self.assertEqual(ch.status, expected_status)
        self.assertIsNone(ch.flag)
        self.assertFalse(any(e.get("event") == "FLAG_CAPTURED" for e in events))

        db = SessionLocal()
        try:
            verified = db.query(FindingModel).filter(
                FindingModel.challenge_id == challenge_id,
                FindingModel.verified.is_(True)).all()
            self.assertEqual(verified, [])
        finally:
            db.close()
        pb.synthesize_from_run.assert_not_called()

    # ------------------------------------------------------------------ #
    # 1. Model JSON completion → candidate only
    # ------------------------------------------------------------------ #

    def test_model_json_completion_fabricated_flag_is_candidate_only(self):
        challenge_id, run_id = self._seed()
        events = []
        completion = ('{"action": "complete", "flag": "picoCTF{f4br1c4t3d_by_m0d3l}", '
                      '"proof": "I definitely solved it and here is a long explanation."}')
        pb = self._run_loop([completion, "DONE"], _tool_result(), events)

        asyncio.run(orchestrator_loop.run_autonomous_loop(run_id, challenge_id, "http://target.ctf"))

        self._assert_no_capture(challenge_id, events, pb)
        self.assertTrue(any(e.get("event") == "FLAG_CANDIDATE" for e in events))

    # ------------------------------------------------------------------ #
    # 2. Raw model prose → candidate only
    # ------------------------------------------------------------------ #

    def test_raw_model_prose_fabricated_flag_is_candidate_only(self):
        challenge_id, run_id = self._seed()
        events = []
        prose = "Great news, I found it! The flag is picoCTF{pr0s3_f4br1c4t3d} and we are done."
        pb = self._run_loop([prose, "DONE"], _tool_result(), events)

        asyncio.run(orchestrator_loop.run_autonomous_loop(run_id, challenge_id, "http://target.ctf"))

        self._assert_no_capture(challenge_id, events, pb)
        self.assertTrue(any(
            e.get("event") == "FLAG_CANDIDATE" and "pr0s3_f4br1c4t3d" in (e.get("candidate") or "")
            for e in events))

    # ------------------------------------------------------------------ #
    # 3. Tool output from a FAILED script → no capture
    # ------------------------------------------------------------------ #

    def test_failed_script_tool_output_is_not_captured(self):
        challenge_id, run_id = self._seed()
        events = []
        script = "```python\nprint('picoCTF{f4k3_scr1pt_0utput}')\n```"
        failed = _tool_result(stdout="picoCTF{f4k3_scr1pt_0utput}", status="FAILED",
                              exit_code=1, execution_failure=True)
        pb = self._run_loop([script, "DONE"], failed, events)

        asyncio.run(orchestrator_loop.run_autonomous_loop(run_id, challenge_id, "http://target.ctf"))

        self._assert_no_capture(challenge_id, events, pb)

    # ------------------------------------------------------------------ #
    # 4. A simple, validly scoped target request still verifies
    # ------------------------------------------------------------------ #

    def test_simple_scoped_target_request_still_verifies(self):
        challenge_id, run_id = self._seed()
        events = []
        ok = _tool_result(stdout="picoCTF{r34l_t4rg3t_r3sp0ns3}\n")
        pb = self._run_loop(["curl -s http://target.ctf/flag", "DONE"], ok, events)

        asyncio.run(orchestrator_loop.run_autonomous_loop(run_id, challenge_id, "http://target.ctf"))

        ch = self._reload(challenge_id)
        self.assertEqual(ch.flag_status, "CAPTURED")
        self.assertEqual(ch.flag, "picoCTF{r34l_t4rg3t_r3sp0ns3}")
        self.assertEqual(ch.status, "COMPLETED")
        self.assertTrue(any(e.get("event") == "FLAG_CAPTURED" for e in events))
        db = SessionLocal()
        try:
            verified = db.query(FindingModel).filter(
                FindingModel.challenge_id == challenge_id,
                FindingModel.verified.is_(True)).all()
            self.assertEqual(len(verified), 1)
        finally:
            db.close()
        pb.synthesize_from_run.assert_called_once()

    # ------------------------------------------------------------------ #
    # 5. `_record_flag_capture` itself requires an explicit VERIFIED verdict
    # ------------------------------------------------------------------ #

    def test_record_flag_capture_requires_verified_verdict(self):
        challenge_id, run_id = self._seed()
        events = []
        self._run_loop([], _tool_result(), events)

        db = SessionLocal()
        try:
            ch = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
            run = db.query(RunModel).filter(RunModel.id == run_id).first()

            async def flow():
                # No verdict at all → fails closed.
                ok_none = await orchestrator_loop._record_flag_capture(
                    db, ch, run, challenge_id, run_id, "picoCTF{n0_v3rd1ct}",
                    "direct call")
                # A non-VERIFIED verdict → fails closed.
                candidate_verdict = FlagVerifier().assess(
                    "picoCTF{c4nd1d4t3_v3rd1ct}", source=FlagSource.TOOL_OUTPUT,
                    action_succeeded=True, target_scope="http://target.ctf",
                    origin=EvidenceOrigin.LOCAL_OBSERVATION)
                self.assertFalse(candidate_verdict.is_verified)
                ok_cand = await orchestrator_loop._record_flag_capture(
                    db, ch, run, challenge_id, run_id, "picoCTF{c4nd1d4t3_v3rd1ct}",
                    "local observation", verdict=candidate_verdict)
                return ok_none, ok_cand

            ok_none, ok_cand = asyncio.run(flow())
            self.assertFalse(ok_none)
            self.assertFalse(ok_cand)

            db.refresh(ch)
            self.assertNotEqual(ch.flag_status, "CAPTURED")
            self.assertNotEqual(ch.status, "COMPLETED")
            self.assertFalse(any(e.get("event") == "FLAG_CAPTURED" for e in events))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
