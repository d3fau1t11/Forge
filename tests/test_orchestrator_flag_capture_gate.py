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


class RootElevationGateTests(_LoopHarness):
    """Fix 04A: no unattended root elevation.

    ``challenge.requires_root`` is challenge metadata, never operator
    authorization. The loop must not execute a second ``sudo``-elevated
    command on its own: every detected root requirement routes through the
    existing operator-approval flow (ROOT_PERMISSION_REQUEST /
    ``resolve_root_request``). The tool manager stays a recording stub; no
    real (elevated) command is ever executed here.
    """

    # ------------------------------------------------------------------ #
    # Fix 04A — no unattended root elevation                              #
    # ------------------------------------------------------------------ #

    def _seed_root_gate(self, *, requires_root=False):
        import tempfile
        db = SessionLocal()
        try:
            ch = ChallengeModel(
                name="Root Gate Challenge", category="web",
                requires_root=requires_root,
                working_directory=tempfile.mkdtemp())
            db.add(ch)
            db.commit()
            run = RunModel(challenge_id=ch.id, status="RUNNING",
                           current_phase="recon", current_agent="orchestrator")
            db.add(run)
            db.commit()
            return ch.id, run.id
        finally:
            db.close()

    def _record_tool_manager(self, commands, results=None, default=None):
        """Re-patch the loop's tool manager with a RECORDING stub (wins over
        the fixed stub installed by ``_run_loop``; cleaned up before it).

        Executed commands are appended to ``commands``; ``results`` are served
        in order, then ``default`` (a blank success unless supplied).
        """
        queue = list(results or [])
        fallback = default if default is not None else _tool_result()

        async def execute_raw_command(*_a, **kwargs):
            commands.append(kwargs.get("command"))
            return queue.pop(0) if queue else fallback

        stack = ExitStack()
        stack.enter_context(patch(
            "backend.agents.orchestrator_loop.tool_manager",
            MagicMock(execute_raw_command=execute_raw_command)))
        self.addCleanup(stack.close)

    def _patch_operator(self, events, action):
        """Replace the harness ws stub so a broadcast ROOT_PERMISSION_REQUEST
        triggers a scripted operator action shortly after, emulating the
        operator clicking Approve / Reject in the UI (which drives
        POST /privilege/approve | /privilege/reject). ``action`` is 'approve',
        'deny' or 'timeout' (never resolved)."""
        ws = MagicMock()

        async def broadcast(payload):
            events.append(dict(payload))
            if payload.get("event") == "ROOT_PERMISSION_REQUEST":
                request_id = payload["request_id"]
                loop = asyncio.get_running_loop()
                if action == "approve":
                    loop.call_later(0.02, lambda: orchestrator_loop.resolve_root_request(
                        request_id, success=True,
                        tool_res=_tool_result(stdout="elevated output\n")))
                elif action == "deny":
                    loop.call_later(0.02, lambda: orchestrator_loop.resolve_root_request(
                        request_id, success=False, message="operator denied"))
                # action == "timeout" → intentionally never resolved; the test
                # patches asyncio.wait_for so no real 120s wait occurs.

        ws.broadcast = broadcast
        stack = ExitStack()
        stack.enter_context(patch("backend.agents.orchestrator_loop.ws_manager", ws))
        self.addCleanup(stack.close)

    def _insta_timeout_run(self, run_id, challenge_id, target):
        """Run the loop with asyncio.wait_for raising TimeoutError for the
        root-approval wait (timeout=120); nothing is resolved meanwhile."""
        real_wait_for = asyncio.wait_for

        async def instant_timeout(fut, timeout=None):
            if timeout == 120:
                # The loop hands us the (never-scheduled) Event.wait coroutine;
                # close it so it does not linger unawaited.
                if hasattr(fut, "close"):
                    fut.close()
                raise asyncio.TimeoutError()
            return await real_wait_for(fut, timeout)

        with patch("asyncio.wait_for", instant_timeout):
            asyncio.run(orchestrator_loop.run_autonomous_loop(
                run_id, challenge_id, target))

    def test_requires_root_never_auto_elevates(self):
        """Fix 04A: requires_root=True must NOT re-execute the command with
        sudo unattended; it must route through operator approval instead."""
        challenge_id, run_id = self._seed_root_gate(requires_root=True)
        events = []
        self._run_loop(["sudo nmap -F http://target.ctf", "DONE"],
                       _tool_result(), events)
        self._patch_operator(events, "approve")
        commands = []
        self._record_tool_manager(commands)

        asyncio.run(orchestrator_loop.run_autonomous_loop(
            run_id, challenge_id, "http://target.ctf"))

        self.assertEqual(len([e for e in events
                              if e.get("event") == "ROOT_PERMISSION_REQUEST"]), 1)
        # Exactly ONE loop-side execution (the model-issued command). The old
        # bypass executed it AGAIN, elevated, without any approval.
        self.assertEqual(len(commands), 1)
        # The elevated execution surfaced via the approval flow's tool result.
        self.assertTrue(any(
            e.get("event") == "LOG_OUTPUT"
            and "elevated output" in (e.get("output") or "")
            for e in events))
        self.assertEqual(orchestrator_loop._root_events, {})

    def test_root_denial_does_not_execute_elevated_command(self):
        challenge_id, run_id = self._seed_root_gate(requires_root=True)
        events = []
        self._run_loop(["sudo nmap -F http://target.ctf", "DONE"],
                       _tool_result(), events)
        self._patch_operator(events, "deny")
        commands = []
        self._record_tool_manager(commands)

        asyncio.run(orchestrator_loop.run_autonomous_loop(
            run_id, challenge_id, "http://target.ctf"))

        self.assertEqual(len(commands), 1)  # never re-executed elevated
        self.assertTrue(any(
            e.get("event") == "LOG_OUTPUT"
            and "[ACCESS DENIED]" in (e.get("output") or "")
            for e in events))

    def test_root_approval_timeout_does_not_execute_elevated_command(self):
        challenge_id, run_id = self._seed_root_gate(requires_root=True)
        events = []
        self._run_loop(["sudo nmap -F http://target.ctf", "DONE"],
                       _tool_result(), events)
        commands = []
        self._record_tool_manager(commands)

        self._insta_timeout_run(run_id, challenge_id, "http://target.ctf")

        self.assertEqual(len(commands), 1)  # never re-executed elevated
        self.assertTrue(any(
            e.get("event") == "ROOT_PERMISSION_REQUEST" for e in events))
        self.assertTrue(any(
            e.get("event") == "LOG_OUTPUT" and "[TIMEOUT]" in (e.get("output") or "")
            for e in events))
        self.assertEqual(orchestrator_loop._root_events, {})

    def test_requires_root_metadata_alone_does_not_force_approval(self):
        challenge_id, run_id = self._seed_root_gate(requires_root=True)
        events = []
        ok_scan = _tool_result(stdout="plain scan output\n")
        self._run_loop(["nmap -F http://target.ctf", "DONE"], ok_scan, events)
        commands = []
        self._record_tool_manager(commands, default=ok_scan)

        asyncio.run(orchestrator_loop.run_autonomous_loop(
            run_id, challenge_id, "http://target.ctf"))

        self.assertEqual(len(commands), 1)
        self.assertFalse(any(
            e.get("event") == "ROOT_PERMISSION_REQUEST" for e in events))
        self.assertEqual(orchestrator_loop._root_events, {})

    def test_non_root_command_keeps_existing_successful_path(self):
        challenge_id, run_id = self._seed_root_gate(requires_root=False)
        events = []
        ok_curl = _tool_result(stdout="plain curl response\n")
        self._run_loop(["curl -s http://target.ctf", "DONE"], ok_curl, events)
        commands = []
        self._record_tool_manager(commands, default=ok_curl)

        asyncio.run(orchestrator_loop.run_autonomous_loop(
            run_id, challenge_id, "http://target.ctf"))

        self.assertEqual(commands, ["curl -s http://target.ctf"])
        self.assertFalse(any(
            e.get("event") == "ROOT_PERMISSION_REQUEST" for e in events))
        self.assertTrue(any(
            e.get("event") == "LOG_OUTPUT"
            and "plain curl response" in (e.get("output") or "")
            for e in events))


class RootApprovalRaceTests(RootElevationGateTests):
    """Fix 04B-F1: the event created by ``register_root_request`` must remain
    the event the orchestrator awaits.

    Regression: the loop created a fresh ``asyncio.Event()`` and overwrote
    ``self._root_events[request_id]`` *after* broadcasting
    ``ROOT_PERMISSION_REQUEST``. An approval that landed during that broadcast
    yield set the original (now-discarded) event; the loop then waited on the
    fresh, never-set event and timed out, dropping the operator's approval.
    """

    def _root_turn(self, events):
        """Seed a sudo command turn and return (challenge_id, run_id, commands)."""
        challenge_id, run_id = self._seed_root_gate(requires_root=True)
        # Turn 1 issues sudo (always needs elevation); turn 2 ends the loop.
        self._run_loop(["sudo nmap -F http://target.ctf", "DONE"],
                       _tool_result(stdout="", stderr="Permission denied",
                                    exit_code=1), events)
        commands = []
        self._record_tool_manager(commands)
        return challenge_id, run_id, commands

    def _resolve_during_broadcast(self, events, *, success, tool_res=None,
                                  message="resolved during broadcast"):
        """Patch ws_manager.broadcast so a ROOT_PERMISSION_REQUEST is resolved
        synchronously *inside* the await (during the yield), before the loop
        creates or awaits its own event. Reproduces the race deterministically
        with no arbitrary sleeps."""
        ws = MagicMock()

        async def broadcast(payload):
            events.append(dict(payload))
            if payload.get("event") == "ROOT_PERMISSION_REQUEST":
                rid = payload["request_id"]
                orchestrator_loop.resolve_root_request(
                    rid, success=success, tool_res=tool_res, message=message)

        ws.broadcast = broadcast
        stack = ExitStack()
        stack.enter_context(patch("backend.agents.orchestrator_loop.ws_manager", ws))
        self.addCleanup(stack.close)

    def _run_root_race(self, events, *, success, tool_res=None,
                       message="resolved during broadcast"):
        """Run a root-approval turn where the operator resolves during the
        ROOT_PERMISSION_REQUEST broadcast yield (before the loop awaits).

        Installs spies to capture the registered vs awaited event and collapses
        the 120s wait to 0.1s so the test is fast and deterministic. Restores
        everything after the run."""
        challenge_id, run_id, commands = self._root_turn(events)

        # Capture the event register_root_request stores for this request.
        registered = {}
        real_register = orchestrator_loop.register_root_request

        def _spy_register(command, challenge_id=None, working_directory=None,
                            ttl_seconds=None):
            rid = real_register(command=command, challenge_id=challenge_id,
                                working_directory=working_directory,
                                ttl_seconds=ttl_seconds)
            registered[rid] = orchestrator_loop._root_events[rid]
            return rid

        # Capture whatever event the loop actually awaits.
        awaited = []
        real_wait = asyncio.Event.wait

        async def _wait_spy(self, *a, **k):
            awaited.append(self)
            return await real_wait(self, *a, **k)

        # Collapse the 120s approval wait: an already-set event (the fix)
        # returns immediately; a never-set one (the bug) times out quickly
        # instead of hanging for 120s.
        real_wait_for = asyncio.wait_for

        async def _short_wait_for(fut, timeout=None, *a, **k):
            return await real_wait_for(fut, 0.1, *a, **k)

        orchestrator_loop.register_root_request = _spy_register
        asyncio.Event.wait = _wait_spy
        asyncio.wait_for = _short_wait_for
        self._resolve_during_broadcast(events, success=success,
                                       tool_res=tool_res, message=message)
        try:
            asyncio.run(orchestrator_loop.run_autonomous_loop(
                run_id, challenge_id, "http://target.ctf"))
        finally:
            orchestrator_loop.register_root_request = real_register
            asyncio.Event.wait = real_wait
            asyncio.wait_for = real_wait_for

        return challenge_id, run_id, commands, registered, awaited

    def test_approval_during_broadcast_is_observed_not_overwritten(self):
        """Race: operator approves while the loop is inside the
        ROOT_PERMISSION_REQUEST broadcast yield, before it awaits.

        The registered event must still be the one awaited; the loop observes
        the approval result instead of timing out; the command runs at most
        once."""
        events = []
        challenge_id, run_id, commands, registered, awaited = \
            self._run_root_race(events, success=True,
                                tool_res=_tool_result(stdout="elevated output\n"))

        # 1. The original registered event is the one awaited — not replaced.
        self.assertEqual(len(awaited), 1)
        self.assertTrue(
            any(reg is aw for reg in registered.values() for aw in awaited),
            "register_root_request's event was replaced by a fresh awaited event")

        # 2. The loop observes the approval result rather than timing out.
        self.assertTrue(any(
            e.get("event") == "LOG_OUTPUT"
            and "elevated output" in (e.get("output") or "")
            for e in events))
        self.assertFalse(any(
            e.get("event") == "LOG_OUTPUT"
            and "[TIMEOUT]" in (e.get("output") or "")
            for e in events))

        # 3. The approved command executes at most once: the loop reused the
        #    approval's tool result and did not re-execute; only the initial
        #    command ran.
        self.assertEqual(commands, ["sudo nmap -F http://target.ctf"])

        # Registry is cleaned up after the wait.
        self.assertEqual(orchestrator_loop._root_events, {})
        self.assertEqual(orchestrator_loop._root_results, {})

    def test_denial_during_broadcast_is_observed(self):
        """Operator denies during the broadcast yield; the loop observes the
        denial (no elevated execution, no timeout) and never elevates."""
        events = []
        challenge_id, run_id, commands, registered, awaited = \
            self._run_root_race(events, success=False,
                                message="operator denied during broadcast")

        # Denial also uses the registered event (not replaced).
        self.assertEqual(len(awaited), 1)
        self.assertTrue(
            any(reg is aw for reg in registered.values() for aw in awaited))

        # The loop observes the denial; no elevated execution, no timeout.
        self.assertFalse(any(
            e.get("event") == "LOG_OUTPUT"
            and "elevated output" in (e.get("output") or "")
            for e in events))
        self.assertTrue(any(
            e.get("event") == "LOG_OUTPUT"
            and "[ACCESS DENIED]" in (e.get("output") or "")
            for e in events))
        self.assertFalse(any(
            e.get("event") == "LOG_OUTPUT"
            and "[TIMEOUT]" in (e.get("output") or "")
            for e in events))
        self.assertEqual(commands, ["sudo nmap -F http://target.ctf"])


if __name__ == "__main__":
    unittest.main()
