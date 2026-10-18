"""Tests for the single 45-minute mission ceiling, instance-expiry pause, and
the continue-endpoint that resumes a paused run from its persisted snapshot.

These replace the old per-agent iteration/time budget (removed in Step 2) and the
interactive HITL checkpoint pause/wait/paste loop (removed in Step 3) with:

* one 45-minute wall-clock mission ceiling (or the platform-reported instance
  expiry time, whichever is sooner), which PAUSES (resumable) the mission,
* state persisted via the existing mission_plan / CheckpointModel snapshot
  mechanism (reused, not rebuilt),
* a simple "continue" action that rehydrates the snapshot and resumes the agents
  with full prior context — no text field, no paste, no suggestions to parse.

The person is currently testing on 1:30–10 minute challenges, so the ceiling
path is exercised here by simulating mission wall-clock time already past the
deadline rather than waiting 45 minutes in real time.
"""

import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import SessionLocal
from backend.database.models import ChallengeModel, RunModel, ToolExecutionModel, CheckpointModel
from backend.agents.swarm_orchestrator import (
    SwarmOrchestrator,
    SwarmBlackboard,
    model_router,
    tool_manager,
    MISSION_CEILING_SECONDS,
)
from backend.agent_runtime.verifier import (
    FlagVerifier,
    FlagSource,
    FlagStatus,
    EvidenceOrigin,
    AnswerSpec,
)
from backend.tools.manager import ToolExecutionResult

# SSTI1 / Crack the Gate 1 live fixtures (verbatim, from the prior task).
SSTI_OUTPUT = (
    '                <!doctype html>\n'
    '                <h1 style="font-size:100px;" align="center">'
    'academy{s4rv3r_s1d3_t3mp14t3_1nj3ct10n5_4r3_c001_5f46e534}</h1>'
)
SSTI_FLAG = "academy{s4rv3r_s1d3_t3mp14t3_1nj3ct10n5_4r3_c001_5f46e534}"

CTG1_JSON = ('{"success":true,"email":"ctf-player@cylabacademy.org",'
             '"firstName":"pico","lastName":"player",'
             '"flag":"academy{brut4_f0rc4_cf2e78d0}"}')
CTG1_FLAG = "academy{brut4_f0rc4_cf2e78d0}"

TRUSTED_CMD = "curl -s http://target.ctf/"
TRUSTED_SCOPE = "http://target.ctf"
GENERIC_FLAG_PATTERN = "picoCTF{...}|FLAG{...}|flag{...}|HTB{...}|CTF{...}"


def _clean_challenge(db, challenge_id):
    """Remove every row tied to a challenge so tests are hermetic against the
    persistent test_forge.db (FK-cascades don't apply to raw Query.delete)."""
    run_ids = [r[0] for r in db.query(RunModel.id).filter(RunModel.challenge_id == challenge_id).all()]
    if run_ids:
        db.query(ToolExecutionModel).filter(
            ToolExecutionModel.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(CheckpointModel).filter(
            CheckpointModel.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(RunModel).filter(RunModel.challenge_id == challenge_id).delete()
    db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).delete()
    db.commit()


def _result(*, stdout="", stderr="", status="SUCCESS", exit_code=0, execution_failure=False):
    return ToolExecutionResult(
        tool_name="bash", capability="custom_command", command="",
        status=status, stdout=stdout, stderr=stderr, exit_code=exit_code,
        execution_failure=execution_failure,
    )


class TestWorkerLoopNoBudgetStop(unittest.TestCase):
    """Step 5(a): many genuinely distinct commands exceeding the old
    AGENT_MAX_ITERATIONS(40)/AGENT_MAX_MINUTES(30) are not stopped by any
    budget check — the worker only stops on a found flag (or ABORT)."""

    def test_many_distinct_commands_run_past_old_budget_and_stop_on_flag(self):
        board = SwarmBlackboard("chal_nobudget", "run_nobudget", "http://t.ctf")
        n_commands = 50  # exceeds the old 40-iteration budget
        flag_at = n_commands

        call_count = {"i": 0}

        async def fake_route(prompt, system_instruction, capability, **kw):
            call_count["i"] += 1
            n = call_count["i"]
            return SimpleNamespace(
                content=f"```bash\ncurl http://t.ctf/cmd{n}\n```",
                is_refusal=False, model_name="test",
            )

        def fake_execute(*args, **kw):
            n = call_count["i"]
            stdout = f"output for cmd{n}\n"
            if n == flag_at:
                stdout += "picoCTF{found_after_budget_would_have_stopped}\n"
            return ToolExecutionResult(
                tool_name="bash", capability="custom_command",
                command=f"curl http://t.ctf/cmd{n}", status="SUCCESS",
                stdout=stdout, stderr="", exit_code=0, execution_failure=False,
            )

        with patch.object(model_router, "route_request", new=AsyncMock(side_effect=fake_route)), \
             patch.object(tool_manager, "execute_tool", new=AsyncMock(side_effect=fake_execute)):
            orch = SwarmOrchestrator()
            asyncio.run(orch._agent_worker("agent_1", board, "/tmp/workdir_nobudget",
                                           "general_reasoning"))

        # The agent executed 50 distinct commands — well past the old 40-iteration
        # budget — and was NOT stopped by a budget check (none exists any more).
        self.assertEqual(call_count["i"], n_commands)
        self.assertEqual(board.agent_iterations.get("agent_1"), n_commands)
        # It stopped because a flag was found, not because of budget exhaustion.
        self.assertEqual(board.flag_captured, "picoCTF{found_after_budget_would_have_stopped}")
        self.assertTrue(board.flag_event.is_set())


class TestCeilingAndExpiryPause(unittest.TestCase):
    """Step 5(c) and 5(d): the 45-minute ceiling and instance expiry each PAUSE
    the mission (not terminate), and state persists via the real snapshot
    mechanism so a resumed run picks up prior context."""

    def _started_long_ago(self, minutes_ago=50):
        """Simulate mission wall-clock time already past the 45-minute ceiling."""
        return time.time() - (minutes_ago * 60)

    def test_ceiling_pauses_not_terminates(self):
        board = SwarmBlackboard("chal_ceil", "run_ceil", "http://t.ctf")
        board.discovered_endpoints.add("http://t.ctf/login")
        board.extracted_headers["X-Dev-Access"] = "yes"
        board.flag_candidates.append(
            {"flag": "picoCTF{cand}", "worker": "agent_1", "source": "tool_output"})
        board.started_ts = self._started_long_ago(50)  # 50 min of mission time elapsed

        orch = SwarmOrchestrator()
        asyncio.run(orch._ceiling_watcher(board))

        # Paused (resumable), not terminated: flag not captured, is_stopped set for
        # the supervisor to finalize as PAUSED.
        self.assertTrue(board.pause_requested)
        self.assertTrue(board.is_stopped)
        self.assertFalse(board.flag_captured)
        self.assertEqual(board.stall_reason, "45-minute mission ceiling reached")

    def test_ceiling_persists_snapshot_and_resume_rehydrates_context(self):
        db = SessionLocal()
        try:
            _clean_challenge(db, "chal_ceil2")
            ch = ChallengeModel(
                id="chal_ceil2", name="Ceil", category="WEB", difficulty="EASY",
                description="d", working_directory="/tmp/x", platform_name="PicoCTF",
                status="PAUSED",
            )
            db.add(ch)
            db.commit()

            board = SwarmBlackboard("chal_ceil2", "run_ceil2", "http://t.ctf")
            board.discovered_endpoints.add("http://t.ctf/login")
            board.discovered_endpoints.add("http://t.ctf/admin")
            board.extracted_headers["X-Dev-Access"] = "yes"
            board.deobfuscated_secrets.append({"scheme": "base64", "input": "x", "decoded": "s3cr3t"})
            board.flag_candidates.append(
                {"flag": "picoCTF{cand}", "worker": "agent_1", "source": "tool_output"})
            board.candidate_usernames.add("jack")
            board.executed_commands_dedup.add("curl -s http://t.ctf/login")
            board.record_agent_step("agent_1", command="curl -s http://t.ctf/login",
                                    output="403 User not found.")
            board.started_ts = self._started_long_ago(50)

            orch = SwarmOrchestrator()
            asyncio.run(orch._ceiling_watcher(board))
            self.assertTrue(board.pause_requested)

            # Persist full current state via the EXISTING snapshot mechanism
            # (mission_plan blackboard_state), reused — not rebuilt, not mocked.
            asyncio.run(board._persist_progress_if_due(force=True))

            ch_row = db.query(ChallengeModel).filter(ChallengeModel.id == "chal_ceil2").first()
            mp = ch_row.mission_plan or {}
            snap = mp.get("blackboard_state")
            self.assertIsNotNone(snap, "blackboard_state snapshot must be persisted")
            self.assertIn("http://t.ctf/login", snap.get("discovered_endpoints", []))
            self.assertIn("http://t.ctf/admin", snap.get("discovered_endpoints", []))
            self.assertEqual(snap.get("extracted_headers", {}).get("X-Dev-Access"), "yes")
            self.assertEqual(len(snap.get("flag_candidates", [])), 1)
            self.assertEqual(snap.get("target_scope"), "http://t.ctf")

            # A resumed run rehydrates the persisted context — agents pick up with
            # full memory of what they already tried, not a fresh start.
            b2 = SwarmBlackboard("chal_ceil2", "run_ceil2", "http://t.ctf")
            counts = b2.load_snapshot(snap)
            self.assertIn("http://t.ctf/login", b2.discovered_endpoints)
            self.assertIn("http://t.ctf/admin", b2.discovered_endpoints)
            self.assertEqual(b2.extracted_headers.get("X-Dev-Access"), "yes")
            self.assertEqual(len(b2.candidate_usernames), 1)
            self.assertIn("jack", b2.candidate_usernames)
            self.assertTrue(any(h.get("agent") == "agent_1" for h in b2.execution_history))
            self.assertGreater(counts["transcript_lines"], 0)
            self.assertGreater(counts["commands"], 0)
        finally:
            db.close()

    def test_instance_expiry_pauses_not_terminates_when_sooner_than_ceiling(self):
        board = SwarmBlackboard("chal_exp", "run_exp", "http://t.ctf")
        board.started_ts = self._started_long_ago(10)   # mission started 10 min ago
        board.instance_expiry_ts = time.time() - 60      # instance expired 1 min ago (sooner)

        orch = SwarmOrchestrator()
        asyncio.run(orch._ceiling_watcher(board))

        self.assertTrue(board.pause_requested)
        self.assertTrue(board.is_stopped)
        self.assertFalse(board.flag_captured)
        self.assertEqual(board.stall_reason, "Platform instance expiry reached")


class TestFlagVerificationSSTIandCTG1(unittest.TestCase):
    """Step 5(e): the SSTI1 and Crack-the-Gate-1 fixtures now verify DIRECTLY
    (VERIFIED, not CANDIDATE at 0.75) and promote to a captured flag
    (record_flag → flag_event) end to end, with no checkpoint/pause/paste."""

    def base_board(self):
        board = SwarmBlackboard("chal_blind2", "run_blind2", TRUSTED_SCOPE)
        board.flag_pattern = GENERIC_FLAG_PATTERN
        board.answer_spec = AnswerSpec(
            expected_shape="bracketed_token",
            description_summary="Get the academy flag.",
            known_prefix="academy",
        )
        return board

    def _run_flags(self, output, flag):
        board = self.base_board()
        orch = SwarmOrchestrator()
        llm = MagicMock(return_value=flag)
        orch._make_summarizer = lambda: llm
        ok = _result(stdout=output, status="SUCCESS", exit_code=0)
        asyncio.run(orch._check_tool_output_for_flags(
            output, board, "agent_1", command=TRUSTED_CMD, result=ok))
        return board

    def test_ssti1_flag_verified_and_captured(self):
        board = self._run_flags(SSTI_OUTPUT, SSTI_FLAG)
        self.assertEqual(board.flag_captured, SSTI_FLAG)
        self.assertTrue(board.flag_event.is_set())
        self.assertIn(SSTI_FLAG, [c["flag"] for c in board.flag_candidates])

    def test_ctg1_flag_verified_and_captured(self):
        board = self._run_flags(CTG1_JSON, CTG1_FLAG)
        self.assertEqual(board.flag_captured, CTG1_FLAG)
        self.assertTrue(board.flag_event.is_set())
        self.assertIn(CTG1_FLAG, [c["flag"] for c in board.flag_candidates])


class TestContinueEndpoint(unittest.TestCase):
    """Step 5(f): the continue API endpoint resumes a paused run from its
    snapshot and the resumed run rehydrates prior context."""

    def test_continue_resumes_paused_run_from_snapshot(self):
        db = SessionLocal()
        try:
            _clean_challenge(db, "chal_cont")
            ch = ChallengeModel(
                id="chal_cont", name="Cont", category="WEB", difficulty="EASY",
                description="d", working_directory="/tmp/x", platform_name="PicoCTF",
                status="PAUSED",
            )
            db.add(ch)
            db.commit()

            # Persist a resume snapshot with prior discovered context.
            board = SwarmBlackboard("chal_cont", "run_cont", "http://t.ctf")
            board.discovered_endpoints.add("http://t.ctf/login")
            board.extracted_headers["X-Dev-Access"] = "yes"
            board.started_ts = time.time() - (50 * 60)
            orch = SwarmOrchestrator()
            asyncio.run(orch._ceiling_watcher(board))
            asyncio.run(board._persist_progress_if_due(force=True))

            from backend.api import routes as routes_mod
            started = {}

            def fake_start_run(run_id, challenge_id, target, engine_type=None, model=None,
                               resume=False):
                started["resume"] = resume
                started["run_id"] = run_id

            with patch.object(routes_mod.workflow_runner, "start_run", side_effect=fake_start_run):
                result = asyncio.run(routes_mod.continue_challenge("chal_cont", db))

            self.assertEqual(result["status"], "RUNNING")
            self.assertTrue(result["resumed"])
            self.assertTrue(started.get("resume"), "continue must re-dispatch with resume=True")
            self.assertEqual(started["run_id"], result["id"])

            # The challenge is back to RUNNING and a new run was created.
            ch_row = db.query(ChallengeModel).filter(ChallengeModel.id == "chal_cont").first()
            self.assertEqual(ch_row.status, "RUNNING")
            run_row = db.query(RunModel).filter(RunModel.challenge_id == "chal_cont").order_by(
                RunModel.started_at.desc()).first()
            self.assertIsNotNone(run_row)
            self.assertEqual(run_row.status, "RUNNING")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
