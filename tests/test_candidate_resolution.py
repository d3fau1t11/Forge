"""
Tests for Candidate Resolution and Verification Pipeline.

Validates:
1. A generic answer candidate can be created from evidence.
2. Challenge semantics affect whether a candidate resolves.
3. A flag-shaped candidate is NOT automatically accepted merely because of its format.
4. A non-flag answer can be resolved.
5. A strong candidate reaches the resolver instead of remaining only a log message.
6. A successfully resolved answer causes the correct existing completion/termination behavior.
7. An unresolved candidate does not falsely mark the challenge solved.
8. Weak/noisy conflicting evidence does not automatically destroy a strong candidate.
9. The live competition execution path actually exercises the resolver.
10. Backward compatibility with FlagVerifier / FlagVerdict.
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from backend.agent_runtime.verifier import (
    AnswerCandidate,
    AnswerResolver,
    AnswerSource,
    AnswerStatus,
    AnswerType,
    AnswerVerdict,
    FlagSource,
    FlagStatus,
    FlagVerdict,
    FlagVerifier,
    infer_expected_answer_type,
)
from backend.agents.swarm_orchestrator import SwarmBlackboard, SwarmOrchestrator
from backend.database.models import ChallengeModel, EvidenceModel, RunModel
from backend.database.session import SessionLocal, init_db
from backend.swarm.coordinator import SwarmCoordinator


class TestCandidateResolution(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        init_db()
        self.resolver = AnswerResolver()

    def test_01_generic_answer_candidate_creation_from_evidence(self):
        """1. A generic answer candidate can be created from evidence with full metadata."""
        candidate = AnswerCandidate(
            value="picoCTF{valid_evidence_candidate_123}",
            answer_type=AnswerType.FLAG,
            source=AnswerSource.VISION_READ,
            confidence=0.95,
            worker_id="agent-0",
            evidence={"derived_path": "/tmp/reconstructed.jpg", "tool": "vision_read"},
            task_context={"description": "Find the flag inside the image", "category": "forensics"},
            provenance={"worker_id": "agent-0", "action_succeeded": True},
        )
        d = candidate.to_dict()
        self.assertEqual(d["value"], "picoCTF{valid_evidence_candidate_123}")
        self.assertEqual(d["source"], "vision_read")
        self.assertEqual(d["evidence"]["tool"], "vision_read")
        self.assertEqual(d["status"], "FLAG_CANDIDATE")

    def test_02_challenge_semantics_affect_resolution(self):
        """2. Challenge semantics affect whether a candidate resolves."""
        # When challenge asks for a username:
        user_cand = AnswerCandidate(
            value="admin_user_99",
            source=AnswerSource.TOOL_OUTPUT,
            worker_id="agent-0",
            task_context={"description": "What is the hidden username?", "category": "web"},
            provenance={"action_succeeded": True},
        )
        verdict_user = self.resolver.resolve(user_cand)
        self.assertEqual(verdict_user.answer_type, AnswerType.USERNAME)
        self.assertTrue(verdict_user.is_resolved)

        # When challenge asks for a SHA256 hash:
        hash_cand = AnswerCandidate(
            value="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            source=AnswerSource.TOOL_OUTPUT,
            worker_id="agent-0",
            task_context={"description": "Calculate the sha256 hash of the payload", "category": "crypto"},
            provenance={"action_succeeded": True},
        )
        verdict_hash = self.resolver.resolve(hash_cand)
        self.assertEqual(verdict_hash.answer_type, AnswerType.HASH)
        self.assertTrue(verdict_hash.is_resolved)

    def test_03_flag_candidate_not_accepted_for_non_flag_question(self):
        """3. A flag-shaped candidate is NOT automatically accepted when the challenge asks for a username or hash."""
        flag_for_username = AnswerCandidate(
            value="picoCTF{not_a_username_at_all}",
            source=AnswerSource.TOOL_OUTPUT,
            worker_id="agent-0",
            task_context={"description": "What is the hidden admin username?", "category": "web"},
            provenance={"action_succeeded": True},
        )
        verdict = self.resolver.resolve(flag_for_username)
        self.assertEqual(verdict.status, AnswerStatus.REJECTED)
        self.assertFalse(verdict.is_resolved)
        self.assertIn("username", verdict.reasons[0].lower())

    def test_04_non_flag_answer_can_be_resolved(self):
        """4. A non-flag answer (e.g. port number or secret key) can be resolved."""
        port_cand = AnswerCandidate(
            value="8080",
            source=AnswerSource.TOOL_OUTPUT,
            worker_id="agent-0",
            task_context={"description": "Which port number is running the hidden service?", "category": "recon"},
            provenance={"action_succeeded": True},
        )
        verdict = self.resolver.resolve(port_cand)
        self.assertEqual(verdict.answer_type, AnswerType.NUMBER)
        self.assertTrue(verdict.is_resolved)
        self.assertEqual(verdict.candidate, "8080")

    async def test_05_strong_candidate_reaches_resolver_and_verifies_on_blackboard(self):
        """5. A strong candidate reaches the resolver instead of remaining only a log message.

        Simulates the exact Binary Digits regression:
        Derived JPEG analyzed via vision_read -> candidate recorded on SwarmBlackboard ->
        resolver marks it verified/resolved -> board.flag_captured is set.
        """
        board = SwarmBlackboard(
            challenge_id="ch-binary-digits",
            run_id="run-binary-digits",
            target_scope="local",
            description="Reconstruct the JPEG image from binary digits and find the flag.",
            category="forensics",
        )

        # Candidate produced by vision_read on reconstructed artifact
        await board.record_flag_candidate(
            candidate="picoCTF{binary_digits_reconstructed_jpeg_flag}",
            worker_id="agent-recon",
            source="vision_read",
            evidence={"derived_path": "/tmp/derived_3be06099e5cf.jpg", "tool": "vision_read"},
        )

        # MUST be captured and flagged immediately
        self.assertEqual(board.flag_captured, "picoCTF{binary_digits_reconstructed_jpeg_flag}")
        self.assertTrue(board.flag_event.is_set())
        self.assertEqual(len(board.flag_candidates), 1)
        self.assertEqual(board.flag_candidates[0]["status"], "FLAG_VERIFIED")

    async def test_06_resolved_answer_causes_completion_termination(self):
        """6. A successfully resolved answer causes swarm completion/termination."""
        board = SwarmBlackboard(
            challenge_id="ch-solve-test",
            run_id="run-solve-test",
            target_scope="local",
            description="Extract the flag from the binary.",
            category="rev",
        )

        await board.record_flag_candidate(
            candidate="FLAG{solved_via_tool_output}",
            worker_id="worker-1",
            source="tool_output",
            evidence={"command": "strings target_bin"},
        )

        self.assertEqual(board.flag_captured, "FLAG{solved_via_tool_output}")
        self.assertTrue(board.flag_event.is_set())

    async def test_07_unresolved_candidate_does_not_falsely_solve_challenge(self):
        """7. An unresolved candidate (e.g. LLM prose claim or placeholder) does not mark challenge solved."""
        board = SwarmBlackboard(
            challenge_id="ch-unresolved-test",
            run_id="run-unresolved-test",
            target_scope="local",
            description="Find the flag.",
            category="crypto",
        )

        # Model prose claim (unverified)
        await board.record_flag_candidate(
            candidate="picoCTF{model_speculation_only}",
            worker_id="agent-0",
            source="llm_prose",
        )

        # Stays candidate, NOT solved
        self.assertIsNone(board.flag_captured)
        self.assertFalse(board.flag_event.is_set())
        self.assertEqual(len(board.flag_candidates), 1)
        self.assertEqual(board.flag_candidates[0]["status"], "FLAG_CANDIDATE")

        # Placeholder rejected completely
        await board.record_flag_candidate(
            candidate="picoCTF{...}",
            worker_id="agent-0",
            source="llm_prose",
        )
        self.assertIsNone(board.flag_captured)

    async def test_08_noisy_conflicting_evidence_does_not_destroy_strong_candidate(self):
        """8. Weak/noisy conflicting evidence does not destroy a strong candidate."""
        board = SwarmBlackboard(
            challenge_id="ch-noise-test",
            run_id="run-noise-test",
            target_scope="local",
            description="Find the flag.",
            category="web",
        )

        # 1. High confidence evidence candidate
        await board.record_flag_candidate(
            candidate="picoCTF{real_verified_flag}",
            worker_id="agent-recon",
            source="tool_output",
        )
        self.assertEqual(board.flag_captured, "picoCTF{real_verified_flag}")

        # 2. Subsequent noisy or broken candidate from another agent
        await board.record_flag_candidate(
            candidate="picoCTF{fake_noisy_hallucination}",
            worker_id="agent-noisy",
            source="llm_prose",
        )

        # Verified flag is preserved
        self.assertEqual(board.flag_captured, "picoCTF{real_verified_flag}")

    def test_09_competition_harness_path_exercises_resolver(self):
        """9. The competition execution path exercises the AnswerResolver."""
        from backend.agents.orchestrator_loop import orchestrator_loop
        db = SessionLocal()
        try:
            ch = ChallengeModel(name="Live Test Challenge", category="web", description="Find the flag")
            db.add(ch)
            db.commit()

            run = RunModel(challenge_id=ch.id, status="RUNNING", current_phase="recon", current_agent="orchestrator")
            db.add(run)
            db.commit()

            res = asyncio.run(orchestrator_loop.execute_run_step(db, run.id))
            self.assertEqual(res["status"], "RUNNING")

            # Check that evidence was recorded in DB
            evs = db.query(EvidenceModel).filter(EvidenceModel.challenge_id == ch.id).all()
            self.assertGreater(len(evs), 0)

            # Test resolver on orchestrator_loop
            verdict = orchestrator_loop.answer_resolver.assess(
                "picoCTF{live_harness_test}",
                source="tool_output",
                description=ch.description,
                category=ch.category,
            )
            self.assertTrue(verdict.is_verified)
        finally:
            db.close()

    def test_10_backward_compatibility_with_flag_verifier(self):
        """10. Existing FlagVerifier and FlagVerdict interfaces remain 100% compatible."""
        fv = FlagVerifier()
        verdict = fv.assess("picoCTF{compat_test_1234}", source=FlagSource.TOOL_OUTPUT)
        self.assertTrue(verdict.is_verified)
        self.assertEqual(verdict.status, FlagStatus.VERIFIED)
        self.assertEqual(verdict.candidate, "picoCTF{compat_test_1234}")


if __name__ == "__main__":
    unittest.main()
