"""Unit tests for the HITL checkpoint pipeline (Part 3): consolidated report
assembly (deterministic + strictly-extractive) and the suggestion parser/router."""

import asyncio
import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.agents.checkpoint_pipeline import (
    AgentCheckpointRecord,
    build_consolidated_report,
    build_response_instructions,
    parse_suggestions,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestReportAssembly(unittest.TestCase):
    def _report(self, records, summarizer=None):
        return _run(build_consolidated_report(
            challenge_name="Crack the Gate", category="WEB", difficulty="EASY",
            target="http://t.ctf", cycle_n=1, start_time="00:00:00", end_time="00:05:00",
            records=records, summarizer=summarizer,
        ))

    def test_header_and_agent_blocks(self):
        recs = [AgentCheckpointRecord(agent_id="agent_1", evidence=["HTTP/1.1 200"], tried=["curl http://t.ctf -> 200"])]
        report = self._report(recs)
        self.assertIn("=== FORGE CHECKPOINT — Cycle 1 (00:00:00–00:05:00) ===", report)
        self.assertIn("Challenge: Crack the Gate | WEB | EASY", report)
        self.assertIn("Target: http://t.ctf", report)
        self.assertIn("--- agent: agent_1 ---", report)
        self.assertIn("HTTP/1.1 200", report)

    def test_flag_candidate_none_when_absent(self):
        recs = [AgentCheckpointRecord(agent_id="agent_1")]
        report = self._report(recs)
        self.assertIn("Flag candidate: NONE", report)

    def test_flag_candidate_marked_unverified(self):
        recs = [AgentCheckpointRecord(agent_id="agent_1", flag_candidate="picoCTF{real_from_output}", flag_source="tool_output")]
        report = self._report(recs)
        self.assertIn("picoCTF{real_from_output}", report)
        self.assertIn("UNVERIFIED", report)
        self.assertIn("tool_output", report)

    def test_extractive_guard_transcript_flag_not_upgraded(self):
        # A fabricated flag that appears ONLY in the transcript (never as a real
        # candidate) must NOT surface as a flag candidate in the report.
        recs = [AgentCheckpointRecord(
            agent_id="agent_1",
            transcript="the model mused: I think the flag is picoCTF{fabricated_guess}",
            flag_candidate=None,
        )]
        report = self._report(recs)          # no summarizer → deterministic only
        self.assertIn("Flag candidate: NONE", report)
        self.assertNotIn("picoCTF{fabricated_guess}", report)

    def test_summarizer_fills_hypothesis_and_blocked(self):
        async def stub(_prompt):
            return "HYPOTHESIS: pivot to /admin with dev header\nBLOCKED: need the developer username"
        recs = [AgentCheckpointRecord(agent_id="agent_1", transcript="curl ...; got 403")]
        report = self._report(recs, summarizer=stub)
        self.assertIn("Hypothesis: pivot to /admin with dev header", report)
        self.assertIn("Blocked on: need the developer username", report)

    def test_summarizer_failure_falls_back(self):
        async def bad(_prompt):
            raise RuntimeError("provider exhausted")
        recs = [AgentCheckpointRecord(agent_id="agent_1", transcript="something")]
        report = self._report(recs, summarizer=bad)
        self.assertIn("Hypothesis: none stated", report)
        self.assertIn("Blocked on: none stated", report)

    def test_report_includes_response_format_instructions(self):
        # Part 4 #1: the report MUST end with the response-format block, listing the actual
        # agent ids — its absence caused the live 'UNPARSEABLE paste' failure.
        recs = [AgentCheckpointRecord(agent_id="agent_1"), AgentCheckpointRecord(agent_id="agent_2")]
        report = self._report(recs)
        self.assertIn("=== INSTRUCTIONS FOR YOUR RESPONSE ===", report)
        self.assertIn("--- suggestion: agent_1 ---", report)
        self.assertIn("--- suggestion: agent_2 ---", report)

    def test_instructions_block_round_trips_through_parser(self):
        # The exact format the report tells the model to use must parse deterministically.
        report = self._report([AgentCheckpointRecord(agent_id="agent_1"),
                               AgentCheckpointRecord(agent_id="agent_2")])
        tail = report[report.index("=== INSTRUCTIONS FOR YOUR RESPONSE ==="):]
        parsed = parse_suggestions(tail, ["agent_1", "agent_2"])
        self.assertTrue(parsed.parsed)
        self.assertIn("agent_1", parsed.directives)
        self.assertIn("agent_2", parsed.directives)

    def test_response_instructions_defaults_when_no_agents(self):
        block = build_response_instructions([])
        self.assertIn("--- suggestion: agent_1 ---", block)


class TestSuggestionParser(unittest.TestCase):
    def test_happy_path_two_agents(self):
        text = (
            "--- suggestion: agent_1 ---\n"
            "Try the X-Dev-Access: yes header on /login.\n\n"
            "--- suggestion: agent_2 ---\n"
            "Decode the base64 blob in the JS bundle."
        )
        p = parse_suggestions(text, ["agent_1", "agent_2"])
        self.assertTrue(p.parsed)
        self.assertIn("agent_1", p.directives)
        self.assertIn("agent_2", p.directives)
        self.assertIn("X-Dev-Access", p.directives["agent_1"])
        self.assertIn("base64", p.directives["agent_2"])

    def test_cross_agent_routing_by_own_label(self):
        # Suggestion labelled agent_2 even though it references agent_1's finding —
        # must route to agent_2 (its own label), not agent_1.
        text = (
            "--- suggestion: agent_2 ---\n"
            "Agent_1 found a username; you (agent_2) should try it against /login."
        )
        p = parse_suggestions(text, ["agent_1", "agent_2"])
        self.assertTrue(p.parsed)
        self.assertIn("agent_2", p.directives)
        self.assertNotIn("agent_1", p.directives)

    def test_unparseable_falls_back_to_all_agents(self):
        text = "just a blob of advice with no delimiters at all"
        p = parse_suggestions(text, ["agent_1"])
        self.assertFalse(p.parsed)
        self.assertTrue(p.fallback)
        self.assertEqual(p.fallback_text, text)

    def test_unknown_label_recorded_but_routed(self):
        text = "--- suggestion: agent_9 ---\nfocus on the sqlite dump"
        p = parse_suggestions(text, ["agent_1", "agent_2"])
        self.assertTrue(p.parsed)
        self.assertIn("agent_9", p.directives)
        self.assertIn("agent_9", p.unknown_labels)

    def test_duplicate_label_appended(self):
        text = (
            "--- suggestion: agent_1 ---\nfirst directive\n"
            "--- suggestion: agent_1 ---\nsecond directive"
        )
        p = parse_suggestions(text, ["agent_1"])
        self.assertTrue(p.parsed)
        self.assertIn("first directive", p.directives["agent_1"])
        self.assertIn("second directive", p.directives["agent_1"])


if __name__ == "__main__":
    unittest.main()
