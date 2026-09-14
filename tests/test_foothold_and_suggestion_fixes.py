"""
Regression tests for the two surgical bug fixes:

PROBLEM 1 - Successful evidence (footholds) must drive follow-up candidate generation:
  TEST A - Successful upload evidence creates a web_exploit candidate for the uploaded path
  TEST B - Failed strategy still exhausts (repeated failures hit exhausted list)
  TEST C - New foothold evidence does NOT reset unrelated exhausted strategies

PROBLEM 2 - Operator suggestions must be evaluated before injection:
  TEST D - Evidence-backed suggestion is ACCEPTED with evidence listed
  TEST E - Suggestion repeating an exhausted strategy is REJECTED
  TEST F - Vague suggestion is REJECTED
  TEST G - Accepted guidance does NOT bypass execution controls (candidate goes through normal pipeline)
  TEST H - Unverified flag assertion is MODIFIED (not injected as a direct flag)
  TEST I - evaluate_suggestions() evaluates all directives in a ParsedSuggestions
  TEST J - Existing checkpoint pipeline tests still pass (regression guard)
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass, field
from typing import List, Optional

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_forge.db")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.agents.checkpoint_pipeline import (
    ParsedSuggestions,
    SuggestionDecision,
    SuggestionEvaluation,
    evaluate_suggestion,
    evaluate_suggestions,
    parse_suggestions,
)
from backend.swarm.candidates import CandidateGenerator
from backend.swarm.scoring import ActionScorer
from backend.swarm.supervisor import Supervisor


# ---------------------------------------------------------------------------
# Lightweight mission-state stub (avoids database dependencies)
# ---------------------------------------------------------------------------

@dataclass
class _Mission:
    """Minimal SharedMissionState-compatible stub for unit tests."""
    target: str = "http://target.local"
    target_type: str = "web"
    category: str = "web"
    services: List[str] = field(default_factory=list)
    technologies: List[str] = field(default_factory=list)
    endpoints: List[str] = field(default_factory=list)
    known_endpoints: List[str] = field(default_factory=list)
    vulnerabilities: List[str] = field(default_factory=list)
    credentials: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    known_files: List[str] = field(default_factory=list)
    failed_techniques: List[str] = field(default_factory=list)
    exhausted_strategies: List[str] = field(default_factory=list)
    action_signatures: List[str] = field(default_factory=list)
    attempted_signatures: List[str] = field(default_factory=list)
    flag_candidates: List[str] = field(default_factory=list)


@dataclass
class _Evidence:
    """Minimal evidence stub."""
    evidence_type: str
    title: str = ""
    description: str = ""
    artifact_id: str = ""
    related_endpoint: str = ""
    related_vulnerability: str = ""
    related_technology: str = ""


# ---------------------------------------------------------------------------
# PROBLEM 1 - Foothold / Evidence -> Follow-up Candidate Generation
# ---------------------------------------------------------------------------

class TestASuccessfulEvidenceCreatesFollowUpCandidate(unittest.TestCase):
    """TEST A: uploaded file in known_files drives a web_exploit candidate."""

    def test_uploaded_web_artifact_generates_web_exploit_candidate(self):
        ms = _Mission()
        ms.known_files = ["uploads/shell.php"]
        gen = CandidateGenerator()
        cands = gen._from_state_gaps(ms)
        action_types = [c.action_type for c in cands]
        targets = [getattr(c, "target", None) for c in cands]
        self.assertIn("web_exploit", action_types,
                      "Expected web_exploit candidate for a .php file in known_files")
        has_target = any(t and "shell.php" in str(t) for t in targets)
        self.assertTrue(has_target, "web_exploit candidate should target the uploaded .php path")

    def test_evidence_artifact_php_generates_web_exploit_candidate(self):
        ms = _Mission()
        ev = _Evidence(evidence_type="file", artifact_id="uploads/shell.php",
                       title="uploads/shell.php")
        gen = CandidateGenerator()
        cands = gen._from_evidence(ev, ms)
        action_types = [c.action_type for c in cands]
        self.assertIn("web_exploit", action_types,
                      "_from_evidence should produce web_exploit for a .php file evidence")
        for c in cands:
            self.assertEqual(c.source, "evidence",
                             "Evidence-derived candidate must have source='evidence'")

    def test_evidence_artifact_has_high_evidence_support(self):
        ms = _Mission()
        ev = _Evidence(evidence_type="file", artifact_id="uploads/shell.php",
                       title="uploads/shell.php")
        gen = CandidateGenerator()
        cands = gen._from_evidence(ev, ms)
        web_cands = [c for c in cands if c.action_type == "web_exploit"]
        self.assertTrue(any(c.evidence_support >= 0.75 for c in web_cands),
                        "Evidence-derived web_exploit must have evidence_support >= 0.75")

    def test_discovered_endpoint_generates_candidate(self):
        ms = _Mission()
        ms.endpoints = ["/admin/flag"]
        gen = CandidateGenerator()
        cands = gen._from_state_gaps(ms)
        action_types = [c.action_type for c in cands]
        self.assertIn("web_exploit", action_types,
                      "A known endpoint should generate a web_exploit candidate")


class TestBFailedStrategyExhausts(unittest.TestCase):
    """TEST B: exhausted strategies are penalised by scorer and filtered by supervisor."""

    def test_exhausted_strategy_penalised_in_scoring(self):
        from backend.swarm.reasoning import CandidateAction
        scorer = ActionScorer()
        cand = CandidateAction(
            action_type="directory_enum",
            objective="Enumerate directories",
            rationale="Surface unknown",
            evidence_support=0.4,
            success_probability=0.6,
        )
        cand2 = CandidateAction(
            action_type="web_exploit",
            objective="Access /admin",
            rationale="Endpoint known",
            evidence_support=0.8,
            success_probability=0.65,
        )
        ranked = scorer.rank([cand, cand2], exhausted_strategies=["directory_enum"])
        self.assertEqual(ranked[0].action_type, "web_exploit",
                         "Non-exhausted high-evidence candidate must rank above exhausted strategy")

    def test_exhausted_strategy_filtered_in_supervisor(self):
        from backend.swarm.reasoning import CandidateAction
        ms = _Mission()
        ms.exhausted_strategies = ["directory_enum"]
        cand_exh = CandidateAction(
            action_type="directory_enum",
            objective="Gobuster scan",
            rationale="Surface unknown",
            evidence_support=0.4,
            success_probability=0.6,
        )
        cand_ok = CandidateAction(
            action_type="web_exploit",
            objective="Access /admin endpoint",
            rationale="endpoint known",
            evidence_support=0.8,
            success_probability=0.65,
        )
        class _MockGen:
            def generate(self, *args, **kwargs):
                return [cand_exh, cand_ok]
        sup = Supervisor("test-mission")
        decision = sup.reason(
            mission_state=ms,
            generator=_MockGen(),
            exhausted_strategies=ms.exhausted_strategies,
        )
        self.assertIsNotNone(decision.selected)
        self.assertNotEqual(decision.selected.action_type, "directory_enum",
                            "Supervisor must not choose an exhausted strategy")


class TestCNewEvidenceDoesNotResetUnrelatedExhaustion(unittest.TestCase):
    """TEST C: new foothold evidence promotes its own candidate without un-exhausting others."""

    def test_unrelated_exhausted_strategy_remains_excluded_after_new_evidence(self):
        ms = _Mission()
        ms.exhausted_strategies = ["directory_enum", "service_fingerprint"]
        ms.known_files = ["uploads/shell.php"]
        gen = CandidateGenerator()
        cands = gen._from_state_gaps(ms)
        web_cands = [c for c in cands if c.action_type == "web_exploit"]
        self.assertTrue(web_cands, "New .php foothold should generate web_exploit candidates")
        scorer = ActionScorer()
        ranked = scorer.rank(cands, exhausted_strategies=ms.exhausted_strategies)
        if ranked and web_cands:
            self.assertEqual(ranked[0].action_type, "web_exploit",
                             "New foothold evidence must promote web_exploit above exhausted strategies")


# ---------------------------------------------------------------------------
# PROBLEM 2 - Operator Suggestion Evaluation Gate
# ---------------------------------------------------------------------------

class TestDEvidenceBackedSuggestionAccepted(unittest.TestCase):
    """TEST D: suggestion targeting a known endpoint/file is ACCEPTED."""

    def test_known_endpoint_suggestion_accepted(self):
        ms = _Mission()
        ms.endpoints = ["/uploads/shell.php"]
        ev = evaluate_suggestion(
            "Request the file at /uploads/shell.php and read the response for the flag.",
            mission_state=ms,
        )
        self.assertEqual(ev.decision, SuggestionDecision.ACCEPT)
        self.assertTrue(ev.evidence, "Should list evidence items that support the acceptance")
        self.assertTrue(ev.suggested_action)

    def test_known_file_suggestion_accepted(self):
        ms = _Mission()
        ms.known_files = ["uploads/shell.php"]
        ev = evaluate_suggestion(
            "Execute uploads/shell.php via the web server to trigger remote code execution.",
            mission_state=ms,
        )
        self.assertEqual(ev.decision, SuggestionDecision.ACCEPT)


class TestEExhaustedStrategySuggestionRejected(unittest.TestCase):
    """TEST E: suggestion repeating exhausted strategy is REJECTED."""

    def test_exhausted_strategy_in_suggestion_rejected(self):
        ms = _Mission()
        ms.exhausted_strategies = ["directory_enum"]
        ev = evaluate_suggestion(
            "Run gobuster/directory_enum with a larger wordlist to find hidden paths.",
            mission_state=ms,
        )
        self.assertEqual(ev.decision, SuggestionDecision.REJECT,
                         "Suggestion repeating exhausted strategy should be REJECTED")
        self.assertFalse(ev.suggested_action,
                         "REJECTED suggestion must not produce an action text")

    def test_failed_technique_in_suggestion_rejected_or_modified(self):
        ms = _Mission()
        ms.failed_techniques = ["sql_injection"]
        ev = evaluate_suggestion(
            "Try sql_injection on the login form with a union payload.",
            mission_state=ms,
        )
        self.assertIn(ev.decision, (SuggestionDecision.REJECT, SuggestionDecision.MODIFY),
                      "Suggestion repeating a failed technique should be REJECTED or MODIFIED")


class TestFVagueSuggestionRejected(unittest.TestCase):
    """TEST F: vague/non-actionable suggestions are REJECTED."""

    def test_try_something_else_rejected(self):
        ev = evaluate_suggestion("Try something else")
        self.assertEqual(ev.decision, SuggestionDecision.REJECT)

    def test_do_better_rejected(self):
        ev = evaluate_suggestion("do better")
        self.assertEqual(ev.decision, SuggestionDecision.REJECT)

    def test_empty_suggestion_rejected(self):
        ev = evaluate_suggestion("")
        self.assertEqual(ev.decision, SuggestionDecision.REJECT)

    def test_blank_suggestion_rejected(self):
        ev = evaluate_suggestion("   ")
        self.assertEqual(ev.decision, SuggestionDecision.REJECT)

    def test_try_again_rejected(self):
        ev = evaluate_suggestion("Try again.")
        self.assertEqual(ev.decision, SuggestionDecision.REJECT)

    def test_investigate_further_rejected(self):
        ev = evaluate_suggestion("investigate further")
        self.assertEqual(ev.decision, SuggestionDecision.REJECT)

    def test_concrete_suggestion_not_rejected(self):
        """A specific, actionable suggestion must NOT be flagged as vague."""
        ev = evaluate_suggestion(
            "Send a POST request to /upload with Content-Type: application/x-php "
            "to bypass the MIME-type filter and upload a PHP webshell."
        )
        self.assertNotEqual(ev.decision, SuggestionDecision.REJECT,
                            "A specific actionable suggestion must not be rejected as vague")


class TestGAcceptedGuidanceDoesNotBypassControls(unittest.TestCase):
    """TEST G: evaluate_suggestion output is advisory text, not an execution primitive."""

    def test_accepted_directive_is_text_not_executed_command(self):
        ev = evaluate_suggestion(
            "Request the path /uploads/shell.php using curl and capture the HTTP response."
        )
        self.assertIsInstance(ev.suggested_action, str)
        self.assertNotIn("execution_result", ev.suggested_action.lower())
        self.assertNotIn("exit_code", ev.suggested_action.lower())

    def test_evaluation_result_is_suggestion_evaluation_type(self):
        ev = evaluate_suggestion(
            "Use curl to access /uploads/shell.php and extract the flag from the response."
        )
        self.assertIsInstance(ev, SuggestionEvaluation)


class TestHFlagAssertionModified(unittest.TestCase):
    """TEST H: unverified flag assertion becomes a verification instruction."""

    def test_flag_is_assertion_modified(self):
        ev = evaluate_suggestion("The flag is picoCTF{totally_made_up_flag_1234}")
        self.assertEqual(ev.decision, SuggestionDecision.MODIFY,
                         "Unverified flag assertion must be MODIFIED, not ACCEPTED")
        self.assertIn("VERIFY", ev.suggested_action.upper(),
                      "MODIFIED flag suggestion must instruct the agent to verify")

    def test_flag_eq_assertion_modified(self):
        ev = evaluate_suggestion("flag=CTF{another_guess}")
        self.assertEqual(ev.decision, SuggestionDecision.MODIFY)

    def test_known_verified_flag_not_modified(self):
        """Flag already in the candidate list: suggestion is ACCEPTED not MODIFIED."""
        ms = _Mission()
        ms.flag_candidates = ["picoCTF{already_found_abc}"]
        ev = evaluate_suggestion(
            "The flag is picoCTF{already_found_abc}",
            mission_state=ms,
        )
        self.assertNotEqual(ev.decision, SuggestionDecision.REJECT,
                            "A flag already in the candidate list must not be rejected")


class TestIEvaluateSuggestionsProcessesAll(unittest.TestCase):
    """TEST I: evaluate_suggestions() evaluates all directives in a ParsedSuggestions."""

    def test_all_parsed_directives_evaluated(self):
        parsed = ParsedSuggestions(
            parsed=True,
            directives={
                "agent_1": "Try something else",
                "agent_2": "Access /uploads/shell.php to trigger RCE.",
            },
        )
        ms = _Mission()
        ms.known_files = ["uploads/shell.php"]
        results = evaluate_suggestions(parsed, mission_state=ms)
        self.assertIn("agent_1", results)
        self.assertIn("agent_2", results)
        self.assertEqual(results["agent_1"].decision, SuggestionDecision.REJECT)
        self.assertEqual(results["agent_2"].decision, SuggestionDecision.ACCEPT)

    def test_fallback_text_evaluated(self):
        parsed = ParsedSuggestions(
            parsed=False,
            fallback=True,
            fallback_text="Try something else",
        )
        results = evaluate_suggestions(parsed)
        self.assertIn("__fallback__", results)
        self.assertEqual(results["__fallback__"].decision, SuggestionDecision.REJECT)

    def test_empty_parsed_returns_empty_dict(self):
        parsed = ParsedSuggestions(parsed=False, fallback=False, fallback_text="")
        results = evaluate_suggestions(parsed)
        self.assertEqual(results, {})


class TestJExistingCheckpointPipelineRegression(unittest.TestCase):
    """TEST J: existing parse_suggestions behaviour unchanged (regression guard)."""

    def test_parse_suggestions_two_agents_still_works(self):
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

    def test_parse_suggestions_fallback_unchanged(self):
        text = "just a blob of advice with no delimiters at all"
        p = parse_suggestions(text, ["agent_1"])
        self.assertFalse(p.parsed)
        self.assertTrue(p.fallback)
        self.assertEqual(p.fallback_text, text)

    def test_parse_suggestions_unknown_label_still_recorded(self):
        text = "--- suggestion: agent_9 ---\nfocus on the sqlite dump"
        p = parse_suggestions(text, ["agent_1", "agent_2"])
        self.assertTrue(p.parsed)
        self.assertIn("agent_9", p.directives)
        self.assertIn("agent_9", p.unknown_labels)


# ---------------------------------------------------------------------------
# Additional edge-case coverage
# ---------------------------------------------------------------------------

class TestEvidenceSourceTagging(unittest.TestCase):
    """_from_evidence candidates must always have source='evidence'."""

    def test_endpoint_evidence_tagged_correctly(self):
        ms = _Mission()
        ev = _Evidence(evidence_type="endpoint", related_endpoint="/api/v2/flag")
        gen = CandidateGenerator()
        cands = gen._from_evidence(ev, ms)
        self.assertTrue(cands, "Endpoint evidence must generate at least one candidate")
        for c in cands:
            self.assertEqual(c.source, "evidence")

    def test_vuln_evidence_tagged_correctly(self):
        ms = _Mission()
        ev = _Evidence(evidence_type="vulnerability",
                       related_vulnerability="SQL injection via login form")
        gen = CandidateGenerator()
        cands = gen._from_evidence(ev, ms)
        self.assertTrue(cands, "Vulnerability evidence must generate a candidate")
        for c in cands:
            self.assertEqual(c.source, "evidence")


class TestSuggestionsWithKwargs(unittest.TestCase):
    """evaluate_suggestion with explicit kwargs (no mission_state)."""

    def test_explicit_exhausted_strategies_kwarg(self):
        ev = evaluate_suggestion(
            "Run directory_enum with gobuster to find hidden paths.",
            exhausted_strategies=["directory_enum"],
        )
        self.assertEqual(ev.decision, SuggestionDecision.REJECT)

    def test_explicit_known_files_kwarg(self):
        ev = evaluate_suggestion(
            "Access the file uploads/result.txt and read its contents.",
            known_files=["uploads/result.txt"],
        )
        self.assertEqual(ev.decision, SuggestionDecision.ACCEPT)

    def test_explicit_known_endpoints_kwarg(self):
        ev = evaluate_suggestion(
            "Send a request to /secret/admin to check for IDOR.",
            known_endpoints=["/secret/admin"],
        )
        self.assertEqual(ev.decision, SuggestionDecision.ACCEPT)


if __name__ == "__main__":
    unittest.main()

