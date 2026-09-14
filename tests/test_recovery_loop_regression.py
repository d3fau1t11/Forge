"""
Regression tests for the failure → recovery → next-action loop.

These tests exercise the ACTUAL production orchestration classes
(AgentRuntime, DecisionEngine, RecoveryEngine, RepetitionDetector)
with deterministic fakes for the model provider and tool executor.
No real network, no subprocesses, no API keys required.

What is tested (per the task specification):
  1. A structured execution failure (FILE_NOT_FOUND, INVALID_URL,
     INTERPRETER_ASSUMPTION) produces a RecoveryPlan.
  2. The RecoveryPlan directive is embedded in the NEXT model call prompt.
  3. The NEXT action after a failure is materially different (no unchanged retry).
  4. RepetitionDetector + recovery together prevent thrashing.
  5. Answer verification rejects source-code extraction expressions.
  6. Answer verification accepts non-flag tool-output answers.

Tests FAIL if:
  * RecoveryEngine directive is discarded before reaching the model prompt.
  * The second action is identical to the first failed action.
  * A source-code expression is promoted as a verified answer.
  * A genuine non-flag tool-output answer is rejected.
"""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import AgentSessionModel, TrajectoryEventModel
from backend.agent_runtime import (
    AgentRuntime, ExecResult, RecoveryEngine, RecoveryStrategy, FailureCategory,
    RepetitionDetector, RepetitionKind, AnswerResolver, AnswerSource, AnswerStatus,
    VerifierAgent, session_manager, trajectory_store,
)
from backend.agent_runtime.decision import ProviderCompletion


# ---------------------------------------------------------------------------
# Deterministic test doubles (no network, no subprocess, no API keys)
# ---------------------------------------------------------------------------

class RecordingProvider:
    """
    Scripted provider gateway that also records every (system, prompt) pair.
    The recorded prompts let the test verify the RECOVERY DIRECTIVE appears
    in the turn-2 context — the core wiring assertion.
    """
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.received_prompts = []   # list of (system_instruction, user_prompt)

    async def complete(self, *, prompt, system_instruction="",
                       capability="general_reasoning", urgency="normal",
                       reasoning_depth="fast"):
        self.received_prompts.append((system_instruction, prompt))
        idx = self.calls
        self.calls += 1
        if idx < len(self.responses):
            content = self.responses[idx]
        else:
            content = self.responses[-1] if self.responses else None
        if content is None:
            return ProviderCompletion(is_refusal=True, refusal_reason="exhausted")
        return ProviderCompletion(content=content, provider_name="stub",
                                  model_name="stub-m", prompt_tokens=10, completion_tokens=5)


class RecordingExecutor:
    """
    Scripted tool executor: returns queued ExecResults in order and records
    every action.display() it was asked to run.
    """
    def __init__(self, sequence=None, default=None):
        self.sequence = list(sequence or [])
        self.default = default or ExecResult(status="SUCCESS", stdout="", exit_code=0)
        self.executed = []

    async def execute(self, action, *, cwd=None, timeout_seconds=120, canonical_target=None):
        self.executed.append(action.display())
        return self.sequence.pop(0) if self.sequence else self.default


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class RecoveryLoopBase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(TrajectoryEventModel).delete()
            db.query(AgentSessionModel).delete()
            db.commit()
        finally:
            db.close()

    def _session(self, **kw):
        d = dict(challenge_id="chal-recovery", run_id=None,
                 target_scope="http://target.ctf:8888/",
                 challenge_name="Recovery Loop Test", category="web",
                 difficulty="EASY", description="Find the token on the target server.")
        d.update(kw)
        return session_manager.create(**d)

    def _runtime(self, provider, executor):
        return AgentRuntime(tool_executor=executor, provider_gateway=provider)

    def _prompt_texts(self, provider):
        """Return list of lowercase combined (system+user) prompt texts."""
        return [(s + "\n" + u).lower() for s, u in provider.received_prompts]


# ===========================================================================
# 1) FILE_NOT_FOUND: recovery directive reaches next prompt; identical retry blocked
# ===========================================================================

class TestFileNotFoundRecoveryLoop(RecoveryLoopBase):

    async def test_recovery_directive_in_next_prompt(self):
        """FILE_NOT_FOUND recovery directive must appear in the turn-2 prompt."""
        provider = RecordingProvider([
            "```python\nopen('missing_artifact.bin', 'rb').read()\n```",
            "wget http://target.ctf:8888/artifact.bin",
            "BUDGET_EXHAUSTED: done",
        ])
        executor = RecordingExecutor(sequence=[
            ExecResult(status="FAILED", exit_code=-1, execution_failure=True,
                       failure_category="FILE_NOT_FOUND",
                       stderr="Pre-execution check: the script reads local file(s) that do not "
                              "exist in the workspace: missing_artifact.bin. "
                              "Do NOT re-run the same script unchanged."),
            ExecResult(status="SUCCESS", stdout="artifact.bin saved.", exit_code=0),
        ])
        sess = self._session()
        await self._runtime(provider, executor).run(sess, max_turns=8)

        self.assertGreaterEqual(provider.calls, 2,
            "Loop must call model >=2 times: once for the failing action, once after recovery.")

        texts = self._prompt_texts(provider)
        self.assertGreaterEqual(len(texts), 2, "Expected >=2 prompts.")
        turn2 = texts[1]
        self.assertIn("does not exist in the workspace", turn2,
            "FILE_NOT_FOUND directive must appear in turn-2 prompt. "
            "If absent, RecoveryEngine output is being discarded before reaching the model.")
        self.assertIn("recovery directive", turn2,
            "'RECOVERY DIRECTIVE' block must appear in turn-2 prompt.")

        events = trajectory_store.get_events(sess.id)
        self.assertTrue(any(e.event_type in ("RECOVERY", "REPLAN") for e in events),
            "A RECOVERY or REPLAN trajectory event must be recorded.")

    async def test_identical_failed_action_not_re_executed(self):
        """Model insisting on identical failing command -> at most one execution."""
        provider = RecordingProvider(["cat missing_file.txt"] * 8)
        executor = RecordingExecutor(default=ExecResult(
            command="cat missing_file.txt", status="FAILED", exit_code=1,
            execution_failure=True, failure_category="FILE_NOT_FOUND",
            stderr="cat: missing_file.txt: No such file or directory"))
        sess = self._session()
        await self._runtime(provider, executor).run(sess, max_turns=8)

        runs = [c for c in executor.executed if "cat missing_file.txt" in c]
        self.assertLessEqual(len(runs), 1,
            f"Identical failing action must not be re-executed. Ran {len(runs)} times.")

        events = trajectory_store.get_events(sess.id)
        self.assertTrue(any(e.event_type == "REPLAN" for e in events),
            "REPLAN event must be recorded when identical action is repeated.")


# ===========================================================================
# 2) INVALID_URL: recovery directive reaches next prompt; identical retry blocked
# ===========================================================================

class TestInvalidUrlRecoveryLoop(RecoveryLoopBase):

    async def test_recovery_directive_in_next_prompt(self):
        """INVALID_URL recovery directive must appear in the turn-2 prompt."""
        provider = RecordingProvider([
            "curl upload.php -X POST",
            "curl -X POST http://target.ctf:8888/upload.php -F file=@x.txt",
            "BUDGET_EXHAUSTED: done",
        ])
        executor = RecordingExecutor(sequence=[
            ExecResult(status="FAILED", exit_code=-1, execution_failure=True,
                       failure_category="INVALID_URL",
                       stderr="Pre-execution check: an HTTP request used the scheme-less URL "
                              "'upload.php'. Use the absolute URL 'http://target.ctf:8888/upload.php'. "
                              "Do NOT re-run the same script unchanged."),
            ExecResult(status="SUCCESS", stdout="HTTP/1.1 200 OK\nFile uploaded!", exit_code=0),
        ])
        sess = self._session()
        await self._runtime(provider, executor).run(sess, max_turns=8)

        self.assertGreaterEqual(provider.calls, 2)
        texts = self._prompt_texts(provider)
        self.assertGreaterEqual(len(texts), 2)
        turn2 = texts[1]
        self.assertIn("recovery directive", turn2,
            "INVALID_URL recovery directive must appear in turn-2 prompt.")
        self.assertTrue("scheme" in turn2 or "absolute" in turn2 or "canonical" in turn2,
            "Turn-2 prompt must contain scheme/absolute-URL correction hint.")

    async def test_same_invalid_url_not_repeated(self):
        """Same scheme-less URL failing repeatedly -> at most one execution."""
        provider = RecordingProvider(["curl upload.php -X POST"] * 6)
        executor = RecordingExecutor(default=ExecResult(
            command="curl upload.php", status="FAILED", exit_code=1,
            execution_failure=True, failure_category="INVALID_URL",
            stderr="curl: (1) Protocol 'upload' not supported."))
        sess = self._session()
        await self._runtime(provider, executor).run(sess, max_turns=6)
        runs = [c for c in executor.executed if "upload.php" in c]
        self.assertLessEqual(len(runs), 1,
            f"INVALID_URL action must not be retried. Ran {len(runs)} times.")


# ===========================================================================
# 3) INTERPRETER_ASSUMPTION: recovery directive reaches next prompt
# ===========================================================================

class TestInterpreterAssumptionRecoveryLoop(RecoveryLoopBase):

    async def test_recovery_directive_in_next_prompt(self):
        """INTERPRETER_ASSUMPTION directive must appear in the turn-2 prompt."""
        provider = RecordingProvider([
            "```python\nencoded = 'hello'.encode('base64')\nprint(encoded)\n```",
            "```python\nimport base64\nprint(base64.b64encode(b'hello'))\n```",
            "BUDGET_EXHAUSTED: done",
        ])
        executor = RecordingExecutor(sequence=[
            ExecResult(status="FAILED", exit_code=-1, execution_failure=True,
                       failure_category="INTERPRETER_ASSUMPTION",
                       stderr="Pre-execution check: the script calls .encode('base64'), "
                              "a Python-2-only string codec. Use base64.b64encode(...) instead. "
                              "Do NOT re-run the same script unchanged."),
            ExecResult(status="SUCCESS", stdout="aGVsbG8=", exit_code=0),
        ])
        sess = self._session()
        await self._runtime(provider, executor).run(sess, max_turns=8)

        self.assertGreaterEqual(provider.calls, 2)
        texts = self._prompt_texts(provider)
        self.assertGreaterEqual(len(texts), 2)
        turn2 = texts[1]
        self.assertIn("recovery directive", turn2,
            "INTERPRETER_ASSUMPTION directive must appear in turn-2 prompt.")
        self.assertTrue("python" in turn2 or "base64" in turn2,
            "Turn-2 prompt must reference Python-3 codec correction.")


# ===========================================================================
# 4) RecoveryEngine unit: all three categories diagnosed correctly
# ===========================================================================

class TestRecoveryEngineDiagnosis(unittest.TestCase):

    def _fail(self, category):
        return ExecResult(status="FAILED", exit_code=-1,
                          execution_failure=True, failure_category=category, stderr="x")

    def test_file_not_found(self):
        plan = RecoveryEngine().diagnose(exec_result=self._fail("FILE_NOT_FOUND"),
                                         command="python solve.py")
        self.assertEqual(plan.category, FailureCategory.FILE_NOT_FOUND)
        self.assertEqual(plan.strategy, RecoveryStrategy.RETRY_MODIFIED)
        self.assertTrue(plan.directive)
        self.assertIn("not exist", plan.directive.lower())
        self.assertIn("re-run", plan.directive.lower())

    def test_invalid_url(self):
        plan = RecoveryEngine().diagnose(exec_result=self._fail("INVALID_URL"),
                                          command="python solve.py")
        self.assertEqual(plan.category, FailureCategory.INVALID_URL)
        self.assertEqual(plan.strategy, RecoveryStrategy.RETRY_MODIFIED)
        self.assertTrue(plan.directive)
        self.assertIn("re-run", plan.directive.lower())

    def test_interpreter_assumption(self):
        plan = RecoveryEngine().diagnose(exec_result=self._fail("INTERPRETER_ASSUMPTION"),
                                          command="python solve.py")
        self.assertEqual(plan.category, FailureCategory.INTERPRETER_ASSUMPTION)
        self.assertEqual(plan.strategy, RecoveryStrategy.RETRY_MODIFIED)
        self.assertTrue(plan.directive)
        self.assertIn("python", plan.directive.lower())

    def test_no_continue_on_structured_failure(self):
        """CONTINUE must never be returned for structured failures — it silently discards evidence."""
        eng = RecoveryEngine()
        for cat in ("FILE_NOT_FOUND", "INVALID_URL", "INTERPRETER_ASSUMPTION"):
            plan = eng.diagnose(exec_result=self._fail(cat), command="python solve.py")
            self.assertNotEqual(plan.strategy, RecoveryStrategy.CONTINUE,
                f"RecoveryStrategy.CONTINUE for {cat} would silently discard failure evidence.")


# ===========================================================================
# 5) RepetitionDetector: same failure → force_replan; corrected action OK
# ===========================================================================

class TestRepetitionDetectorWithFailures(unittest.TestCase):

    def test_same_action_same_failure_forces_replan(self):
        det = RepetitionDetector()
        det.observe("python solve.py", failed=True, failure_category="FILE_NOT_FOUND", novel=False)
        rep = det.observe("python solve.py", failed=True, failure_category="FILE_NOT_FOUND", novel=False)
        self.assertEqual(rep.kind, RepetitionKind.SAME_FAILURE)
        self.assertTrue(rep.force_replan, "SAME_FAILURE must force replan.")

    def test_exact_repeat_detected_before_execution(self):
        det = RepetitionDetector()
        det.observe("cat missing.txt", failed=True, failure_category="FILE_NOT_FOUND", novel=False)
        self.assertEqual(det.classify("cat missing.txt"), RepetitionKind.EXACT_REPEAT)

    def test_corrected_different_action_is_not_a_repeat(self):
        det = RepetitionDetector()
        det.observe("cat artifact.bin", failed=True, failure_category="FILE_NOT_FOUND", novel=False)
        self.assertEqual(det.classify("wget http://target.ctf:8888/artifact.bin"), RepetitionKind.NONE,
            "Materially different action must not be blocked.")

    def test_corrected_absolute_url_not_a_repeat(self):
        det = RepetitionDetector()
        det.observe("curl upload.php", failed=True, failure_category="INVALID_URL", novel=False)
        self.assertNotEqual(det.classify("curl http://target.ctf:8888/upload.php"),
            RepetitionKind.EXACT_REPEAT, "Absolute URL correction must not be an exact repeat.")


# ===========================================================================
# 6) Answer verification: source-code expressions rejected; non-flag accepted
# ===========================================================================

class TestAnswerVerification(unittest.TestCase):

    def setUp(self):
        self.resolver = AnswerResolver()

    def test_source_code_expression_rejected(self):
        v = self.resolver.assess(
            "resp.text[resp.text.find('picoCTF{'):resp.text.find('}')+1]",
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertEqual(v.status, AnswerStatus.REJECTED,
            "Python subscript/method expression must be REJECTED as source code.")

    def test_open_call_rejected(self):
        v = self.resolver.assess('open("flag.txt").read()',
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertEqual(v.status, AnswerStatus.REJECTED,
            "open(...) call expression must be REJECTED.")

    def test_print_expression_rejected(self):
        v = self.resolver.assess("print(flag)",
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertEqual(v.status, AnswerStatus.REJECTED,
            "print(flag) contains parentheses — must be REJECTED.")

    def test_prose_flag_is_only_candidate(self):
        v = self.resolver.assess("picoCTF{model_says_so}", source=AnswerSource.LLM_PROSE)
        self.assertEqual(v.status, AnswerStatus.CANDIDATE,
            "LLM-prose flag must remain CANDIDATE, not VERIFIED.")

    def test_auth_token_tool_output_accepted(self):
        v = self.resolver.assess("GENERIC_AUTH_TOKEN_XYZ_abc123",
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertNotEqual(v.status, AnswerStatus.REJECTED,
            "Real auth token from TOOL_OUTPUT must NOT be REJECTED.")

    def test_sha1_hash_accepted(self):
        v = self.resolver.assess("2c26b46b68ffc68ff99b453c1d30413413422d70",
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertNotEqual(v.status, AnswerStatus.REJECTED,
            "SHA-1 hash from tool output must NOT be REJECTED.")

    def test_numeric_answer_accepted(self):
        v = self.resolver.assess("31337",
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertNotEqual(v.status, AnswerStatus.REJECTED,
            "Numeric answer from tool output must NOT be REJECTED.")

    def test_username_accepted(self):
        v = self.resolver.assess("shadow_operator_42",
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertNotEqual(v.status, AnswerStatus.REJECTED,
            "Username from tool output must NOT be REJECTED.")

    def test_url_not_source_code(self):
        self.assertFalse(self.resolver.looks_like_source_code("http://target.ctf/robots.txt"),
            "A plain URL must not be classified as source code.")

    def test_bracket_subscript_rejected(self):
        v = self.resolver.assess("flag[32:]",
            source=AnswerSource.TOOL_OUTPUT, action_succeeded=True)
        self.assertEqual(v.status, AnswerStatus.REJECTED,
            "Bracket subscript expression must be REJECTED.")


# ===========================================================================
# 7) End-to-end: failure → recovery → different action selected
# ===========================================================================

class TestEndToEndRecoveryIntegration(RecoveryLoopBase):

    async def test_failure_leads_to_different_action(self):
        """After FILE_NOT_FOUND, next prompt has recovery directive; action is different."""
        provider = RecordingProvider([
            "```python\ndata = open('secret.bin', 'rb').read()\nprint(data)\n```",
            "wget http://target.ctf:8888/secret.bin",
            "cat secret.bin",
            "BUDGET_EXHAUSTED: done",
        ])
        executor = RecordingExecutor(sequence=[
            ExecResult(status="FAILED", exit_code=-1, execution_failure=True,
                       failure_category="FILE_NOT_FOUND",
                       stderr="FileNotFoundError: secret.bin does not exist."),
            ExecResult(status="SUCCESS", stdout="secret.bin saved.", exit_code=0),
            ExecResult(status="SUCCESS", stdout="auth_token: ADMIN_SECRET_4242", exit_code=0),
        ])
        sess = self._session()
        result = await self._runtime(provider, executor).run(sess, max_turns=10)

        self.assertGreaterEqual(provider.calls, 2, "Expected >=2 model calls.")

        texts = self._prompt_texts(provider)
        self.assertGreaterEqual(len(texts), 2)
        self.assertIn("recovery directive", texts[1],
            "Turn-2 prompt must contain 'RECOVERY DIRECTIVE'. "
            "If missing, RecoveryEngine output is not reaching the model.")

        solve_runs = [c for c in executor.executed if "solve.py" in c]
        self.assertLessEqual(len(solve_runs), 1,
            f"solve.py must not be re-run after FILE_NOT_FOUND. Got: {solve_runs}")

        events = trajectory_store.get_events(sess.id)
        self.assertTrue(any(e.event_type in ("RECOVERY", "REPLAN") for e in events),
            "A RECOVERY or REPLAN trajectory event must be recorded.")

        self.assertTrue(any("wget" in c or "download" in c for c in executor.executed),
            f"After FILE_NOT_FOUND, model must download the file. Executed: {executor.executed}")

    async def test_thrashing_terminates_cleanly(self):
        """Model insisting on the same failing URL -> at most 1 execution; mission terminates."""
        provider = RecordingProvider(["curl upload.php"] * 10)
        executor = RecordingExecutor(default=ExecResult(
            command="curl upload.php", status="FAILED", exit_code=7,
            execution_failure=True, failure_category="INVALID_URL",
            stderr="curl: (1) Protocol 'upload' not supported."))
        sess = self._session()
        result = await self._runtime(provider, executor).run(sess, max_turns=10)

        runs = [c for c in executor.executed if "upload.php" in c]
        self.assertLessEqual(len(runs), 1,
            f"INVALID_URL action must not be retried. Executed {len(runs)} times.")
        self.assertIn(result.status, ("FAILED", "MAX_TURNS", "TIMEOUT", "CANCELLED"),
            f"Mission must terminate cleanly, not loop forever. Status: {result.status}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
