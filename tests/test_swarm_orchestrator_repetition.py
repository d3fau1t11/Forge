"""
Tests for the RepetitionDetector + RecoveryEngine wiring into the default
production engine (backend/agents/swarm_orchestrator.py).

These tests exercise the exact components now consumed by the swarm worker loop
(RepetitionDetector.classify/observe, RecoveryEngine.diagnose), confirming that
the Old Sessions pattern — varying curl flags/headers against the same host/path
— collapses to ONE repeated strategy and triggers REPLAN within the repeat
threshold, while genuinely different commands do not false-positive.

RepetitionDetector/recovery.py/runtime.py are NOT modified by this task — only
consumed by the new wiring in swarm_orchestrator.py.
"""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.agent_runtime.repetition import RepetitionDetector, RepetitionKind
from backend.agent_runtime.recovery import RecoveryEngine, RecoveryStrategy


class TestSwarmOrchestratorRepetitionWiring(unittest.TestCase):
    """The repetition + recovery guards wired into SwarmOrchestrator._agent_worker."""

    # ------------------------------------------------------------------ #
    # 1: Old Sessions pattern — semantically-identical curl commands
    #    with varying flags/headers collapse to ONE strategy.
    # ------------------------------------------------------------------ #

    def test_semantic_signature_collapses_varying_curl_flags(self):
        """curl http://x/login with different -v/--output/-H flags → same signature."""
        d = RepetitionDetector()
        cmds = [
            "curl http://target.ctf/login",
            "curl -v http://target.ctf/login",
            "curl -s --output /dev/null http://target.ctf/login",
            "curl -H 'Cookie: session=abc' http://target.ctf/login",
            "curl --header 'X-Forwarded-For: 1.2.3.4' http://target.ctf/login",
        ]
        sigs = [RepetitionDetector.semantic_signature(c) for c in cmds]
        # All identical: tool|curl, method|GET, host|target.ctf, path|/login
        self.assertEqual(len(set(sigs)), 1)
        self.assertEqual(sigs[0], "curl|GET|target.ctf|/login")

    def test_repeat_threshold_triggers_replan_within_two_repeats(self):
        """After repeat_threshold (2) repeats, observe() signals force_replan → REPLAN."""
        d = RepetitionDetector()
        recovery = RecoveryEngine()
        cmd = "curl http://target.ctf/login"

        # First occurrence: not a repeat.
        self.assertEqual(d.classify(cmd), RepetitionKind.NONE)
        report = d.observe(cmd, failed=True, failure_category=None, novel=False)
        self.assertFalse(report.force_replan)

        # Second occurrence: repeat → repeat_streak=1, still below threshold.
        self.assertEqual(d.classify(cmd), RepetitionKind.EXACT_REPEAT)
        report = d.observe(cmd, failed=True, failure_category="repeat", novel=False)
        self.assertFalse(report.force_replan)

        # Third occurrence: repeat_streak=2 >= repeat_threshold(2) → force_replan.
        self.assertEqual(d.classify(cmd), RepetitionKind.EXACT_REPEAT)
        report = d.observe(cmd, failed=True, failure_category="repeat", novel=False)
        self.assertTrue(report.force_replan)

        # RecoveryEngine diagnoses REPLAN from the repetition report.
        plan = recovery.diagnose(repetition=report, command=cmd)
        self.assertEqual(plan.strategy, RecoveryStrategy.REPLAN)
        self.assertTrue(plan.directive)

    def test_replan_log_message_contains_agent_signature_and_decision(self):
        """The REPLAN log line (what _append_to_challenge_log records) includes
        agent id, collapsed semantic signature, and REPLAN decision."""
        d = RepetitionDetector()
        recovery = RecoveryEngine()
        cmd = "curl -v -H 'Cookie: session=abc' http://target.ctf/login"
        agent_id = "recon#1"

        # Drive to force_replan.
        d.observe(cmd, failed=True, failure_category=None, novel=False)
        d.observe(cmd, failed=True, failure_category="repeat", novel=False)
        report = d.observe(cmd, failed=True, failure_category="repeat", novel=False)
        self.assertTrue(report.force_replan)

        plan = recovery.diagnose(repetition=report, command=cmd)
        sig = RepetitionDetector.semantic_signature(cmd)

        # Compose the same log line the worker writes via _append_to_challenge_log.
        log_line = (
            f"REPLAN: agent {agent_id} repeating strategy '{sig}' without progress — "
            f"{plan.directive[:160]}"
        )
        self.assertIn(agent_id, log_line)
        self.assertIn(sig, log_line)
        self.assertIn("REPLAN", log_line)
        # Signature collapses flags/headers: curl|GET|target.ctf|/login
        self.assertEqual(sig, "curl|GET|target.ctf|/login")

    # ------------------------------------------------------------------ #
    # 2: Genuinely different commands do NOT false-positive.
    # ------------------------------------------------------------------ #

    def test_different_host_or_path_does_not_trigger_replan(self):
        d = RepetitionDetector()
        recovery = RecoveryEngine()

        cmds = [
            "curl http://alpha.ctf/login",     # host alpha
            "curl http://bravo.ctf/admin",     # host bravo + different path
            "curl -X POST http://charlie.ctf/api",  # host charlie + POST
            "wget http://delta.ctf/file",      # different tool + host delta
            "python ./local_tool.py",          # non-HTTP tool
        ]
        for c in cmds:
            report = d.observe(c, failed=True, failure_category=None, novel=True)
            self.assertFalse(report.force_replan, f"false force_replan for {c!r}")
            plan = recovery.diagnose(repetition=report, command=c)
            self.assertNotEqual(plan.strategy, RecoveryStrategy.REPLAN)
            self.assertNotEqual(plan.strategy, RecoveryStrategy.ABORT)

    # ------------------------------------------------------------------ #
    # 3: ABORT path — recovery.diagnose signals ABORT after consecutive failures.
    # ------------------------------------------------------------------ #

    def test_abort_after_consecutive_failures(self):
        """RecoveryEngine.ABORT_AFTER_CONSECUTIVE (8) consecutive failures → ABORT.

        Uses a generic (non-network) failure so diagnose falls through to the
        consecutive-failure ceiling rather than returning WAIT_RETRY early.
        """
        recovery = RecoveryEngine()
        failed_res = type("R", (), {
            "status": "FAILED", "execution_failure": True,
            "stderr": "generic invocation failure", "stdout": "",
            "command": "curl http://target.ctf/x",
            "failure_category": None,
        })()

        plan = None
        for _ in range(RecoveryEngine.ABORT_AFTER_CONSECUTIVE):
            plan = recovery.diagnose(exec_result=failed_res, command="curl http://target.ctf/x")

        self.assertEqual(plan.strategy, RecoveryStrategy.ABORT)
        self.assertTrue(plan.directive)
        self.assertIn("consecutive", plan.directive.lower())

    # ------------------------------------------------------------------ #
    # 4: Regression guard — normal non-repetitive run is unaffected.
    # ------------------------------------------------------------------ #

    def test_normal_run_no_replan_no_abort(self):
        """A sequence of distinct, successful commands produces no REPLAN/ABORT."""
        d = RepetitionDetector()
        recovery = RecoveryEngine()

        cmds = [
            "curl http://alpha.ctf/",
            "curl http://bravo.ctf/login",
            "nmap -sV charlie.ctf",
            "curl http://delta.ctf/admin",
        ]
        for c in cmds:
            report = d.observe(c, failed=False, failure_category=None, novel=True)
            self.assertFalse(report.force_replan)
            plan = recovery.diagnose(repetition=report, command=c)
            self.assertEqual(plan.strategy, RecoveryStrategy.CONTINUE)


if __name__ == "__main__":
    unittest.main()
