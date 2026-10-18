"""Regression tests for the blind-extraction fallback in swarm flag verification.

Live evidence (pasted as fixtures): ``academy{...}`` flags appeared multiple times
in raw, direct-to-target curl/JSON output in two separate real runs, with zero
flag-candidate log lines produced either time. Root cause:
``_check_tool_output_for_flags`` returned as soon as the hardcoded FLAG_REGEX
found no match, so the semantic verifier (judge_relevance / assess()) was never
offered the value.

These tests reproduce the ACTUAL captured output (not paraphrased) and prove the
fallback now routes such values through the SAME assess() loop as regex-found
candidates, while respecting the existing rejection gates and the per-mission
Groq-quota dedupe cache.
"""

import asyncio
import os
import unittest
from unittest.mock import Mock

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.agents.swarm_orchestrator import SwarmOrchestrator, SwarmBlackboard
from backend.agent_runtime.verifier import (
    FlagVerifier,
    FlagSource,
    FlagStatus,
    EvidenceOrigin,
    AnswerSpec,
    extract_candidate_blind,
)
from backend.tools.manager import ToolExecutionResult

# ── Live log fixtures (verbatim) ──────────────────────────────────────────
# SSTI1 real output — academy{...} flag, absent from the hardcoded regex allowlist.
SSTI_OUTPUT = (
    '                <!doctype html>\n'
    '                <h1 style="font-size:100px;" align="center">'
    'academy{s4rv3r_s1d3_t3mp14t3_1nj3ct10n5_4r3_c001_5f46e534}</h1>'
)
SSTI_FLAG = "academy{s4rv3r_s1d3_t3mp14t3_1nj3ct10n5_4r3_c001_5f46e534}"

# Crack the Gate 1 real JSON output — academy{...} flag, absent from the regex allowlist.
CTG1_JSON = ('{"success":true,"email":"ctf-player@cylabacademy.org",'
             '"firstName":"pico","lastName":"player",'
             '"flag":"academy{brut4_f0rc4_cf2e78d0}"}')
CTG1_FLAG = "academy{brut4_f0rc4_cf2e78d0}"

# Crack the Gate 1 agent-authored traceback line (the actual false-positive source).
TRACEBACK_LINE = 'flag = response.text.split("flag{")[1].split("}")[0]'

TRUSTED_CMD = "curl -s http://target.ctf/"
TRUSTED_SCOPE = "http://target.ctf"
GENERIC_FLAG_PATTERN = "picoCTF{...}|FLAG{...}|flag{...}|HTB{...}|CTF{...}"


def _board() -> SwarmBlackboard:
    return SwarmBlackboard("chal_blind", "run_blind", TRUSTED_SCOPE)


def _result(*, stdout="", stderr="", status="SUCCESS", exit_code=0,
          execution_failure=False):
    return ToolExecutionResult(
        tool_name="bash", capability="custom_command", command="",
        status=status, stdout=stdout, stderr=stderr, exit_code=exit_code,
        execution_failure=execution_failure,
    )


class TestExtractCandidateBlind(unittest.TestCase):
    """Helper in isolation (verifier.py)."""

    def test_ssti_output_extracts_flag(self):
        """(a) Real SSTI output: mocked LLM returning the flag passes the
        substring check and is returned."""
        llm = Mock(return_value=SSTI_FLAG)
        result = extract_candidate_blind(
            SSTI_OUTPUT,
            AnswerSpec("bracketed_token", "Get the academy flag.", "academy"),
            llm,
        )
        self.assertEqual(result, SSTI_FLAG)
        llm.assert_called_once()

    def test_ctg1_json_extracts_flag(self):
        """(b) Real Crack the Gate 1 JSON output."""
        llm = Mock(return_value=CTG1_FLAG)
        result = extract_candidate_blind(
            CTG1_JSON,
            AnswerSpec("bracketed_token", "Get the academy flag.", "academy"),
            llm,
        )
        self.assertEqual(result, CTG1_FLAG)

    def test_no_answer_returns_none(self):
        """NO_ANSWER (any case) → None; not logged as a rejection."""
        llm = Mock(return_value="NO_ANSWER")
        self.assertIsNone(extract_candidate_blind(
            SSTI_OUTPUT,
            AnswerSpec("bracketed_token", "Get the academy flag.", "academy"),
            llm,
        ))

    def test_no_answer_case_insensitive_returns_none(self):
        llm = Mock(return_value="no_answer")
        self.assertIsNone(extract_candidate_blind(
            SSTI_OUTPUT,
            AnswerSpec("bracketed_token", "Get the academy flag.", "academy"),
            llm,
        ))

    def test_empty_response_returns_none(self):
        llm = Mock(return_value="")
        self.assertIsNone(extract_candidate_blind(
            SSTI_OUTPUT,
            AnswerSpec("bracketed_token", "Get the academy flag.", "academy"),
            llm,
        ))

    def test_hallucination_not_substring_returns_none(self):
        """LLM invents a value not in the output → hallucination guard → None."""
        llm = Mock(return_value="academy{d03s_n0t_3x1st}")
        self.assertIsNone(extract_candidate_blind(
            CTG1_JSON,
            AnswerSpec("bracketed_token", "Get the academy flag.", "academy"),
            llm,
        ))


class TestBlindExtractionFallbackE2E(unittest.TestCase):
    """End-to-end through SwarmOrchestrator._check_tool_output_for_flags."""

    def base_board(self) -> SwarmBlackboard:
        board = _board()
        # Generic default — NOT academy-aware. This is the actual production
        # condition that caused the bug: academy{...} never matches the regex.
        board.flag_pattern = GENERIC_FLAG_PATTERN
        board.answer_spec = AnswerSpec(
            expected_shape="bracketed_token",
            description_summary="Get the academy flag.",
            known_prefix="academy",
        )
        return board

    def test_c_ssti_flag_verified_and_captured(self):
        """(c) Regression test for the actual bug: SSTI output, generic default
        flag_pattern, trusted origin, action succeeded → the flag now verifies
        DIRECTLY (not held as a candidate at 0.75) and is captured end to end:
        record_flag → flag_event set → mission would complete.

        This is the direct evidence that the original bug (academy{...} flag seen
        in real tool output but never captured because the semantic path was held
        below the auto-complete threshold) is fully closed.
        """
        board = self.base_board()
        orch = SwarmOrchestrator()
        # One controlled LLM serves both extract_candidate_blind and the
        # judge_relevance call inside assess(); both return the verbatim flag.
        llm = Mock(return_value=SSTI_FLAG)
        orch._make_summarizer = lambda: llm

        calls = []
        original = board.record_flag_candidate

        async def spy(candidate, worker_id, source, *, verdict=None):
            calls.append((candidate, worker_id, source, verdict))
            return await original(candidate, worker_id, source, verdict=verdict)

        board.record_flag_candidate = spy

        ok = _result(stdout=SSTI_OUTPUT, status="SUCCESS", exit_code=0)
        asyncio.run(orch._check_tool_output_for_flags(
            SSTI_OUTPUT, board, "agent_1", command=TRUSTED_CMD, result=ok))

        self.assertTrue(
            any(c[0] == SSTI_FLAG for c in calls),
            f"SSTI flag not routed to record_flag_candidate; calls={calls}",
        )
        self.assertIn(SSTI_FLAG, [c["flag"] for c in board.flag_candidates])
        # Semantic-path confirmation now promotes directly to VERIFIED → captured.
        self.assertEqual(board.flag_captured, SSTI_FLAG)
        self.assertTrue(board.flag_event.is_set())

    def test_d_agent_source_reject_catches_traceback_derived_value(self):
        """(d) Crack the Gate 1 false-positive: the agent's own traceback line as
        both the output and the agent_source. The agent-source reject inside
        assess() still catches a source-derived value — the new fallback shares
        assess(), so it does NOT bypass this rejection gate.

        Two assertions:
        1. The orchestrator flow rejects the traceback-derived candidate and never
           captures it.
        2. assess() itself rejects a source-derived candidate even when the origin
           is trusted — the exact gate that protects blind-extracted output.
        """
        board = self.base_board()
        script_path = os.path.join("workdir", "solve_agent_1_deadbeef.py")
        board.register_agent_source(script_path, TRACEBACK_LINE)
        orch = SwarmOrchestrator()
        ok = _result(stdout=TRACEBACK_LINE, status="SUCCESS", exit_code=0)
        asyncio.run(orch._check_tool_output_for_flags(
            TRACEBACK_LINE, board, "agent_1",
            command=f'python "{script_path}"', result=ok))

        self.assertIsNone(board.flag_captured)
        self.assertFalse(board.flag_event.is_set())
        # The flag-shaped value scraped from the agent's own traceback is retained as
        # a candidate but REJECTED by the agent-source gate (never captured).
        self.assertIn('flag{")[1].split("}', [c["flag"] for c in board.flag_candidates])

        # Direct verifier-level proof: a blind-extraction-style candidate that is a
        # substring of agent_source is REJECTED by assess() even with a trusted
        # origin — so the fallback (which feeds the same assess()) cannot bypass it.
        verdict = FlagVerifier().assess(
            'flag{")[1].split("}',
            source=FlagSource.TOOL_OUTPUT,
            command=TRUSTED_CMD,
            action_succeeded=True,
            target_scope=TRUSTED_SCOPE,
            origin=EvidenceOrigin.TARGET_RESPONSE,
            agent_source=TRACEBACK_LINE,
        )
        self.assertEqual(verdict.status, FlagStatus.REJECTED)
        self.assertIn("agent-authored source", ";".join(verdict.reasons))

    def test_e_dedupe_llm_called_once_per_mission(self):
        """(e) Identical trusted output screened twice in the same mission/board
        instance → extract_candidate_blind's llm_call is invoked only once
        (protects the Groq quota against repeated/near-identical retries)."""
        board = self.base_board()
        orch = SwarmOrchestrator()
        llm = Mock(return_value="NO_ANSWER")
        orch._make_summarizer = lambda: llm

        ok = _result(stdout=SSTI_OUTPUT, status="SUCCESS", exit_code=0)
        asyncio.run(orch._check_tool_output_for_flags(
            SSTI_OUTPUT, board, "agent_1", command=TRUSTED_CMD, result=ok))
        first_call_count = llm.call_count

        asyncio.run(orch._check_tool_output_for_flags(
            SSTI_OUTPUT, board, "agent_1", command=TRUSTED_CMD, result=ok))

        # The dedupe cache must suppress the second call entirely: no additional
        # LLM invocation for identical trusted output within the same mission.
        self.assertEqual(
            llm.call_count, first_call_count,
            "second identical output must not trigger another LLM call",
        )
        self.assertEqual(llm.call_count, 1)
        self.assertEqual(len(board._blind_extraction_seen), 1)

    def test_f_regex_fast_path_never_triggers_fallback(self):
        """(f) When _flag_candidates_in finds a regex match, the fallback never
        triggers — behavior identical to before this change (no extra LLM call,
        flag still verified/captured through the normal path)."""
        board = self.base_board()
        orch = SwarmOrchestrator()
        llm = Mock(return_value="academy{must_not_be_asked}")
        orch._make_summarizer = lambda: llm

        ok = _result(stdout="picoCTF{real_flag_from_target}\n", status="SUCCESS", exit_code=0)
        asyncio.run(orch._check_tool_output_for_flags(
            "picoCTF{real_flag_from_target}\n", board, "agent_2",
            command=TRUSTED_CMD, result=ok))

        self.assertEqual(
            llm.call_count, 0,
            "fallback must not call the LLM when the regex fast path already found something",
        )
        self.assertEqual(board.flag_captured, "picoCTF{real_flag_from_target}")
        self.assertTrue(board.flag_event.is_set())


if __name__ == "__main__":
    unittest.main()
