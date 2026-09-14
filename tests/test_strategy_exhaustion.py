"""
Tests for Strategy-Exhaustion Detection and Forced Pivot.

Validates:
1. _compute_evidence_fingerprint normalizes noise and hashes new facts.
2. _update_strategy_state increments stale count on identical evidence and triggers exhaustion at limit.
3. _update_strategy_state triggers exhaustion at total attempt cap even with novel outputs.
4. Blackboard snapshot/restore serializes and rehydrates strategy tracking fields.
5. build_user_prompt includes STRATEGY GATE banner and REQUIRED PIVOT when exhausted.
6. _force_pivot_if_needed updates pivot directive and bans across blackboard.
7. Verifier FALSE_FLAG_PATTERNS rejects single dummy words in flag envelopes.
8. Verifier looks_like_source_code catches non-whitespace context around flag envelopes.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.agents.agent_prompt import (
    AgentContext,
    SYSTEM_INSTRUCTION_TEMPLATE,
    build_system_instruction,
    build_user_prompt,
    make_context_from_env,
)
from backend.agents.swarm_orchestrator import (
    STRATEGY_ATTEMPT_LIMIT,
    STRATEGY_STALE_LIMIT,
    SwarmBlackboard,
    _compute_evidence_fingerprint,
    _force_pivot_if_needed,
    _update_strategy_state,
)
from backend.agent_runtime.verifier import (
    AnswerResolver,
    FALSE_FLAG_PATTERNS,
    FLAG_REGEX,
)


class TestStrategyExhaustion(unittest.IsolatedAsyncioTestCase):

    def test_01_compute_evidence_fingerprint(self):
        """Fingerprint should normalize volatile tokens and detect meaningful output."""
        self.assertEqual(_compute_evidence_fingerprint(""), "")
        self.assertEqual(_compute_evidence_fingerprint("   \n\t  "), "")

        # Cosmetic variations in UUID / timestamp / pointer address collapse to identical hash
        out1 = "Found item 12345678-1234-1234-1234-123456789abc at 0x7ffd9820 on 2026-09-14 06:00:00"
        out2 = "Found item 87654321-4321-4321-4321-cba987654321 at 0x55aa1234 on 2026-09-14 07:15:30"
        fp1 = _compute_evidence_fingerprint(out1)
        fp2 = _compute_evidence_fingerprint(out2)
        self.assertTrue(bool(fp1))
        self.assertEqual(fp1, fp2)

        # Semantically different outputs produce different fingerprints
        out3 = "User admin authenticated successfully. Welcome to dashboard."
        fp3 = _compute_evidence_fingerprint(out3)
        self.assertNotEqual(fp1, fp3)

    def test_02_update_strategy_state_stale_limit(self):
        """Consecutive stale turns on a strategy trigger exhaustion at STRATEGY_STALE_LIMIT."""
        board = SwarmBlackboard(
            challenge_id="test_chal",
            run_id="run_1",
            target_scope="http://localhost:8000",
        )
        strategy = "credential_stuffing"
        output_same = "[-] Invalid login attempt. Password incorrect."

        # Turn 1: First attempt with this output -> counts as initial evidence, stale count 0
        exhausted = _update_strategy_state(board, "agent_1", strategy, output_same, None)
        self.assertFalse(exhausted)
        self.assertEqual(board.strategy_attempts[strategy], 1)
        self.assertEqual(board.strategy_stale_counts[strategy], 0)
        self.assertNotIn(strategy, board.exhausted_strategies)

        # Turn 2: Stale attempt 1
        exhausted = _update_strategy_state(board, "agent_2", strategy, output_same, None)
        self.assertFalse(exhausted)
        self.assertEqual(board.strategy_attempts[strategy], 2)
        self.assertEqual(board.strategy_stale_counts[strategy], 1)

        # Turn 3: Stale attempt 2
        exhausted = _update_strategy_state(board, "agent_3", strategy, output_same, None)
        self.assertFalse(exhausted)
        self.assertEqual(board.strategy_attempts[strategy], 3)
        self.assertEqual(board.strategy_stale_counts[strategy], 2)

        # Turn 4: Stale attempt 3 (reaches STRATEGY_STALE_LIMIT = 3)
        exhausted = _update_strategy_state(board, "agent_1", strategy, output_same, None)
        self.assertTrue(exhausted)
        self.assertEqual(board.strategy_attempts[strategy], 4)
        self.assertEqual(board.strategy_stale_counts[strategy], 3)
        self.assertIn(strategy, board.exhausted_strategies)

    def test_03_update_strategy_state_attempt_cap(self):
        """Reaching STRATEGY_ATTEMPT_LIMIT triggers exhaustion even if outputs are novel."""
        board = SwarmBlackboard(
            challenge_id="test_chal",
            run_id="run_1",
            target_scope="http://localhost:8000",
        )
        strategy = "sql_injection"

        for i in range(1, STRATEGY_ATTEMPT_LIMIT):
            exhausted = _update_strategy_state(board, f"agent_{i}", strategy, f"Novel output payload {i}", None)
            self.assertFalse(exhausted)
            self.assertNotIn(strategy, board.exhausted_strategies)

        # 5th attempt hits STRATEGY_ATTEMPT_LIMIT
        exhausted = _update_strategy_state(board, "agent_1", strategy, f"Novel output payload {STRATEGY_ATTEMPT_LIMIT}", None)
        self.assertTrue(exhausted)
        self.assertIn(strategy, board.exhausted_strategies)

    def test_04_snapshot_and_restore_strategy_state(self):
        """Blackboard snapshot serializes and restores all strategy fields."""
        board = SwarmBlackboard(
            challenge_id="test_chal",
            run_id="run_1",
            target_scope="http://localhost:8000",
        )
        board.strategy_attempts = {"credential_stuffing": 4, "jwt_tamper": 1}
        board.strategy_stale_counts = {"credential_stuffing": 3, "jwt_tamper": 0}
        board.strategy_evidence_fingerprints = {"credential_stuffing": {"fp1", "fp2"}}
        board.exhausted_strategies = {"credential_stuffing"}
        board.pivot_directive = "Switch from credential stuffing to session forgery"

        plan = board._build_mission_plan()
        bb_state = plan["blackboard_state"]

        self.assertEqual(bb_state["strategy_attempts"]["credential_stuffing"], 4)
        self.assertEqual(bb_state["strategy_stale_counts"]["credential_stuffing"], 3)
        self.assertIn("fp1", bb_state["strategy_evidence_fingerprints"]["credential_stuffing"])
        self.assertIn("credential_stuffing", bb_state["exhausted_strategies"])
        self.assertEqual(bb_state["pivot_directive"], "Switch from credential stuffing to session forgery")

        # Restore into fresh blackboard
        restored = SwarmBlackboard(
            challenge_id="test_chal",
            run_id="run_1",
            target_scope="http://localhost:8000",
        )
        restored.load_snapshot(bb_state)

        self.assertEqual(restored.strategy_attempts["credential_stuffing"], 4)
        self.assertEqual(restored.strategy_stale_counts["credential_stuffing"], 3)
        self.assertEqual(restored.strategy_evidence_fingerprints["credential_stuffing"], {"fp1", "fp2"})
        self.assertIn("credential_stuffing", restored.exhausted_strategies)
        self.assertEqual(restored.pivot_directive, "Switch from credential stuffing to session forgery")

    def test_05_prompt_strategy_gate_injection(self):
        """AgentContext and prompt builder correctly inject STRATEGY GATE banner and rules."""
        ctx = AgentContext(
            platform="PicoCTF",
            challenge_name="No FA",
            category="WEB",
            difficulty="EASY",
            description="Web login challenge",
            target_url="http://saturn.picoctf.net:12345",
            exhausted_strategies=["credential_stuffing", "hash_offline_crack"],
            pivot_directive="Inspect session cookies or test SQL injection on auth bypass endpoints.",
        )

        sys_prompt = build_system_instruction(ctx)
        self.assertIn("STRATEGY:", sys_prompt)
        self.assertIn("7. Begin EVERY response", sys_prompt)

        user_prompt = build_user_prompt(ctx)
        self.assertIn("=== STRATEGY GATE (SWARM-ENFORCED — MANDATORY) ===", user_prompt)
        self.assertIn("credential_stuffing, hash_offline_crack", user_prompt)
        self.assertIn("Inspect session cookies or test SQL injection", user_prompt)

    async def test_06_force_pivot_coroutine(self):
        """_force_pivot_if_needed queries strategic_planner, sets directive and bans."""
        board = SwarmBlackboard(
            challenge_id="test_chal",
            run_id="run_1",
            target_scope="http://localhost:8000",
            category="WEB",
            challenge_name="No FA",
        )
        board.exhausted_strategies.add("credential_stuffing")

        mock_pivot = ("Focus on JWT signature weakness", ["credential_stuffing", "brute_force"])
        with patch("backend.agents.strategic_planner.strategic_planner.review_swarm_pivot",
                   new=AsyncMock(return_value=mock_pivot)):
            await _force_pivot_if_needed(board, "agent_1", "credential_stuffing")

        self.assertEqual(board.pivot_directive, "Focus on JWT signature weakness")
        self.assertIn("credential_stuffing", board.exhausted_strategies)
        self.assertIn("brute_force", board.exhausted_strategies)

    def test_07_verifier_false_flag_patterns(self):
        """FALSE_FLAG_PATTERNS rejects single placeholder dummy words inside flag envelopes."""
        self.assertTrue(bool(FALSE_FLAG_PATTERNS.search("picoCTF{flag}")))
        self.assertTrue(bool(FALSE_FLAG_PATTERNS.search("FLAG{value}")))
        self.assertTrue(bool(FALSE_FLAG_PATTERNS.search("HTB{answer}")))
        self.assertTrue(bool(FALSE_FLAG_PATTERNS.search("CTF{x}")))
        self.assertTrue(bool(FALSE_FLAG_PATTERNS.search("picoCTF{todo}")))
        self.assertTrue(bool(FALSE_FLAG_PATTERNS.search("picoCTF{redacted}")))
        self.assertTrue(bool(FALSE_FLAG_PATTERNS.search("picoCTF{tbd}")))

        # Real flag is NOT matched as false flag
        self.assertFalse(bool(FALSE_FLAG_PATTERNS.search("picoCTF{real_flag_value_12345}")))

    def test_08_verifier_looks_like_source_code_surrounding_junk(self):
        """looks_like_source_code detects interpolation and surrounding junk around flag envelopes."""
        resolver = AnswerResolver()

        # Clean literal flag -> False (not source code)
        self.assertFalse(resolver.looks_like_source_code("picoCTF{n0_f4_succ3ss_9876}"))

        # Surrounding f-string / quote junk -> True (is source code / template)
        self.assertTrue(resolver.looks_like_source_code('f"picoCTF{flag_var}"'))
        self.assertTrue(resolver.looks_like_source_code('picoCTF{real_answer}")'))
        self.assertTrue(resolver.looks_like_source_code('prefix_picoCTF{real_answer}'))
        self.assertTrue(resolver.looks_like_source_code('["picoCTF{test}"]'))


if __name__ == "__main__":
    unittest.main()
