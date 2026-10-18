"""Tests for the semantic flag/objective verification fallback path.

This path is strictly additive and opt-in: FlagVerifier.assess() only reaches it
when FLAG_REGEX and _matches_expected_format both miss, the origin is trusted,
and the caller supplies an answer_spec + llm_call. Any caller that omits those
gets bit-for-bit identical behavior to before.

Covers:
* judge_relevance — the verbatim LLM judge (precondition, NO_ANSWER,
  hallucination re-check, accept).
* FlagVerifier.assess() new branch — non-standard answers verified at 0.75,
  rejected at 0.0, and the untouched picoCTF{...} fast path (regression guard).
* derive_answer_spec — defensive parsing, free_text degradation.
"""

import unittest
from unittest.mock import Mock

from backend.agent_runtime.verifier import (
    FlagVerifier, FlagSource, FlagStatus, EvidenceOrigin,
    AnswerSpec, RelevanceVerdict, derive_answer_spec, judge_relevance,
)

TRUSTED_CMD = "curl -s http://target.ctf/count"
TRUSTED_SCOPE = "http://target.ctf"


def _llm(return_value):
    return Mock(return_value=return_value)


class TestJudgeRelevance(unittest.TestCase):

    def test_not_verbatim_in_output_llm_never_called(self):
        """Candidate absent from trusted output → reject immediately, LLM not called."""
        llm = _llm("47")
        verdict = judge_relevance(
            "99", "There are 47 users registered.",
            AnswerSpec("integer", "How many users are registered."), llm)
        llm.assert_not_called()
        self.assertFalse(verdict.is_answer)
        self.assertEqual(verdict.extracted_value, "")
        self.assertEqual(verdict.justification, "not verbatim in trusted output")

    def test_llm_says_no_answer(self):
        llm = _llm("NO_ANSWER")
        verdict = judge_relevance(
            "47", "There are 47 users registered.",
            AnswerSpec("integer", "How many users are registered."), llm)
        self.assertFalse(verdict.is_answer)
        self.assertEqual(verdict.extracted_value, "")
        self.assertEqual(verdict.justification, "judged not relevant to description")

    def test_llm_says_no_answer_case_insensitive(self):
        llm = _llm("no_answer")
        verdict = judge_relevance(
            "47", "There are 47 users registered.",
            AnswerSpec("integer", "How many users are registered."), llm)
        self.assertFalse(verdict.is_answer)
        self.assertEqual(verdict.extracted_value, "")

    def test_llm_empty_response_rejected(self):
        llm = _llm("")
        verdict = judge_relevance(
            "47", "There are 47 users registered.",
            AnswerSpec("integer", "How many users are registered."), llm)
        self.assertFalse(verdict.is_answer)
        self.assertEqual(verdict.justification, "judged not relevant to description")

    def test_llm_hallucination_guard_rejects_non_substring(self):
        """LLM paraphrased/invented → re-check fails → rejected, never accepted."""
        llm = _llm("forty-seven")
        verdict = judge_relevance(
            "47", "There are 47 users registered.",
            AnswerSpec("integer", "How many users are registered."), llm)
        self.assertFalse(verdict.is_answer)
        self.assertEqual(verdict.extracted_value, "")
        self.assertEqual(verdict.justification,
                         "LLM response failed verbatim re-check — treated as hallucination")

    def test_valid_substring_is_answer(self):
        llm = _llm("47")
        verdict = judge_relevance(
            "47", "There are 47 users registered.",
            AnswerSpec("integer", "How many users are registered."), llm)
        self.assertTrue(verdict.is_answer)
        self.assertEqual(verdict.extracted_value, "47")
        self.assertTrue(verdict.justification)


class TestAssessSemanticFallback(unittest.TestCase):

    def base_kwargs(self, **overrides):
        kwargs = dict(
            source=FlagSource.TOOL_OUTPUT,
            command=TRUSTED_CMD,
            action_succeeded=True,
            target_scope=TRUSTED_SCOPE,
            origin=EvidenceOrigin.TARGET_RESPONSE,
        )
        kwargs.update(overrides)
        return kwargs

    def test_nonstandard_integer_answer_candidate_at_075_held_for_review(self):
        """Non-regex candidate judged relevant → CANDIDATE at 0.75, not VERIFIED.

        With the default SEMANTIC_VERIFY_AUTO_COMPLETE_THRESHOLD of 0.85, the
        fixed 0.75 semantic-path confidence does not auto-complete, so the match
        is held for operator review instead of ending the mission immediately.
        """
        llm = _llm("47")
        spec = AnswerSpec(
            expected_shape="integer",
            description_summary="How many users are registered.",
            known_prefix=None,
        )
        verdict = FlagVerifier().assess(
            "47", raw_trusted_output="There are 47 users registered.",
            answer_spec=spec, llm_call=llm, **self.base_kwargs())
        self.assertEqual(verdict.status, FlagStatus.CANDIDATE)
        self.assertEqual(verdict.confidence, 0.75)
        self.assertEqual(verdict.candidate, "47")
        self.assertIn("semantic match below auto-complete threshold", verdict.reasons[-1])

    def test_nonstandard_integer_answer_verified_when_threshold_low(self):
        """Lowering SEMANTIC_VERIFY_AUTO_COMPLETE_THRESHOLD below 0.75 makes the
        same semantic-path inputs produce VERIFIED, proving the constant is
        load-bearing (not dead code)."""
        llm = _llm("47")
        spec = AnswerSpec(
            expected_shape="integer",
            description_summary="How many users are registered.",
            known_prefix=None,
        )
        with unittest.mock.patch(
            "backend.agent_runtime.verifier.SEMANTIC_VERIFY_AUTO_COMPLETE_THRESHOLD", 0.5
        ):
            verdict = FlagVerifier().assess(
                "47", raw_trusted_output="There are 47 users registered.",
                answer_spec=spec, llm_call=llm, **self.base_kwargs())
        self.assertEqual(verdict.status, FlagStatus.VERIFIED)
        self.assertEqual(verdict.confidence, 0.75)
        self.assertEqual(verdict.candidate, "47")

    def test_nonstandard_answer_rejected_when_llm_hallucinates(self):
        """Judge hallucination → clean REJECTED at 0.0, never CANDIDATE."""
        llm = _llm("forty-seven")
        spec = AnswerSpec("integer", "How many users are registered.", None)
        verdict = FlagVerifier().assess(
            "47", raw_trusted_output="There are 47 users registered.",
            answer_spec=spec, llm_call=llm, **self.base_kwargs())
        self.assertEqual(verdict.status, FlagStatus.REJECTED)
        self.assertEqual(verdict.confidence, 0.0)
        self.assertEqual(verdict.candidate, "47")
        self.assertEqual(verdict.reasons,
                         ["LLM response failed verbatim re-check — treated as hallucination"])

    def test_nonstandard_without_answer_spec_unchanged(self):
        """Opt-in: no answer_spec/llm_call → bit-for-bit today's behavior."""
        verdict = FlagVerifier().assess(
            "47", **self.base_kwargs())
        self.assertEqual(verdict.status, FlagStatus.REJECTED)
        self.assertEqual(verdict.confidence, 0.0)
        self.assertEqual(verdict.reasons, ["Does not match any known CTF flag format."])

    def test_picoctf_fast_path_unchanged_regression(self):
        """Standard picoCTF{...} flag: FLAG_REGEX fast path untouched (VERIFIED 0.95)."""
        verdict = FlagVerifier().assess(
            "picoCTF{r3al_c4ptur3_0k}",
            **self.base_kwargs(command="curl -s http://target.ctf/flag"))
        self.assertEqual(verdict.status, FlagStatus.VERIFIED)
        self.assertEqual(verdict.confidence, 0.95)
        self.assertEqual(verdict.candidate, "picoCTF{r3al_c4ptur3_0k}")


class TestDeriveAnswerSpec(unittest.TestCase):

    def test_integer_shape_from_description(self):
        llm = Mock(return_value=(
            "expected_shape: integer\n"
            "description_summary: How many users are registered.\n"
            "known_prefix: none\n"
        ))
        spec = derive_answer_spec(
            "How many users are registered on the site?", "picoCTF{...}", llm)
        self.assertEqual(spec.expected_shape, "integer")
        self.assertEqual(spec.description_summary, "How many users are registered.")
        self.assertIsNone(spec.known_prefix)

    def test_no_shape_hint_degrades_to_free_text(self):
        llm = Mock(return_value=(
            "expected_shape: free_text\n"
            "description_summary: Retrieve the secret value from the endpoint.\n"
            "known_prefix: none\n"
        ))
        spec = derive_answer_spec(
            "Retrieve the secret value from the endpoint.", "", llm)
        self.assertEqual(spec.expected_shape, "free_text")
        self.assertIsNone(spec.known_prefix)

    def test_known_prefix_when_implied(self):
        llm = Mock(return_value=(
            "expected_shape: bracketed_token\n"
            "description_summary: Get the academy flag.\n"
            "known_prefix: academy\n"
        ))
        spec = derive_answer_spec("Find the academy flag.", "academy{...}", llm)
        self.assertEqual(spec.expected_shape, "bracketed_token")
        self.assertEqual(spec.known_prefix, "academy")

    def test_malformed_response_degrades_to_free_text(self):
        llm = Mock(return_value="this is not structured at all !!!")
        spec = derive_answer_spec("Give me the answer now.", "", llm)
        self.assertEqual(spec.expected_shape, "free_text")
        self.assertIsNone(spec.known_prefix)
        self.assertEqual(spec.description_summary, "Give me the answer now.")

    def test_llm_failure_degrades_to_free_text(self):
        llm = Mock(side_effect=RuntimeError("provider down"))
        spec = derive_answer_spec("How many users?", "", llm)
        self.assertEqual(spec.expected_shape, "free_text")
        self.assertIsNone(spec.known_prefix)
        self.assertEqual(spec.description_summary, "How many users?")


if __name__ == "__main__":
    unittest.main()
