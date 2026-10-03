"""Tests for durable intake session persistence (Workstream F: Task 2 of 3).

Tests that intake sessions survive process restart, the fast-path skips LLM calls,
malformed LLM output falls back gracefully, and turn-2 commits correctly.
"""
import os
import sys
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database.session import SessionLocal, init_db
from backend.database.models import (
    ChallengeModel, ChatMessageModel, TargetProfileModel, RunModel,
    CheckpointModel, ToolExecutionModel, AgentStateModel, FindingModel,
    EvidenceModel, ReportModel, IntakeSessionModel, IntakeTurnModel,
)
from backend.providers.base import ProviderResponse
from backend.api.routes.challenges import _INTAKE_OPENING_PROMPT
import json


class TestIntakePersistence(unittest.IsolatedAsyncioTestCase):
    """Tests for durable intake session persistence."""

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        # Clean up all test data - children first to respect FK constraints
        db = SessionLocal()
        try:
            # Find all test challenges by name pattern
            test_names = (
                "Durability Test", "Fast Path Test", "Malformed LLM Test",
                "Turn2 Commit Test", "Resume Session Test"
            )
            ids = [c.id for c in db.query(ChallengeModel)
                   .filter(ChallengeModel.name.in_(test_names)).all()]
            if ids:
                run_ids = [r.id for r in db.query(RunModel)
                           .filter(RunModel.challenge_id.in_(ids)).all()]
                if run_ids:
                    for model in (CheckpointModel, ToolExecutionModel, AgentStateModel):
                        (db.query(model).filter(model.run_id.in_(run_ids))
                         .delete(synchronize_session=False))
                    (db.query(RunModel).filter(RunModel.id.in_(run_ids))
                     .delete(synchronize_session=False))
                for model in (ChatMessageModel, TargetProfileModel, FindingModel,
                              EvidenceModel, ReportModel):
                    (db.query(model).filter(model.challenge_id.in_(ids))
                     .delete(synchronize_session=False))
                (db.query(ChallengeModel).filter(ChallengeModel.id.in_(ids))
                 .delete(synchronize_session=False))

            # Also clean up any intake sessions/turns left over
            db.query(IntakeTurnModel).delete(synchronize_session=False)
            db.query(IntakeSessionModel).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

    def setUp(self):
        self.db = SessionLocal()
        # Clean intake tables before each test
        self.db.query(IntakeTurnModel).delete(synchronize_session=False)
        self.db.query(IntakeSessionModel).delete(synchronize_session=False)
        self.db.commit()

    def tearDown(self):
        if hasattr(self, "db") and self.db:
            self.db.close()

    def _make_response(self, content: str, is_refusal: bool = False) -> MagicMock:
        """Create a mock ProviderResponse."""
        resp = MagicMock(spec=ProviderResponse)
        resp.content = content
        resp.is_refusal = is_refusal
        resp.refusal_reason = "refused" if is_refusal else None
        return resp

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_session_durability_survives_restart(self, mock_route_request):
        """Create a session, clear in-memory cache, resume by session_id."""
        # Mock model to return no fields (deterministic gate will ask for missing)
        mock_route_request.return_value = self._make_response(json.dumps({
            "reply": "I need more info.",
            "fields": {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]},
            "ready_to_create": False
        }))

        # 1. Start a session
        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        # 2. Send turn 1 with name only
        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Durability Test"})
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        self.assertEqual(body1["step"], 1)
        self.assertIn("type", body1.get("awaiting", []))
        self.assertIn("difficulty", body1.get("awaiting", []))

        # 3. Simulate process restart by creating a NEW TestClient (new in-memory state)
        # The database should still have the session
        new_client = TestClient(app)

        # 4. Resume by sending turn 1 with remaining fields
        r2 = new_client.post(f"/api/challenges/chat-session/{sid}/message",
                             json={"challenge_type": "web", "difficulty": "MEDIUM"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], 2)
        self.assertIn("web", body2["bot_message"])
        self.assertIn("MEDIUM", body2["bot_message"])

        # 5. Complete turn 2
        r3 = new_client.post(f"/api/challenges/chat-session/{sid}/message",
                             json={"description": "Test durability", "target_address": "10.10.10.10"})
        self.assertEqual(r3.status_code, 200)
        body3 = r3.json()
        self.assertEqual(body3["step"], "committed")
        self.assertIn("challenge", body3)
        self.assertEqual(body3["challenge"]["name"], "Durability Test")

        # Verify intake session is marked COMMITTED
        session = self.db.query(IntakeSessionModel).filter(IntakeSessionModel.id == sid).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.status, "COMMITTED")
        self.assertIsNotNone(session.challenge_id)

        # Verify turns were persisted
        turns = self.db.query(IntakeTurnModel).filter(IntakeTurnModel.session_id == sid).all()
        self.assertGreaterEqual(len(turns), 4)  # opening, user1, bot1, user2, bot2 at minimum

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_fast_path_skips_llm(self, mock_route_request):
        """Request with all step-1 fields advances without calling the LLM."""
        mock_route_request.return_value = self._make_response("{}")

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        # The opening message consults the model exactly once.
        calls_after_start = mock_route_request.call_count

        # Send ALL required fields in one request - should hit fast path
        r = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                             json={
                                 "challenge_name": "Fast Path Test",
                                 "challenge_type": "pwn",
                                 "difficulty": "HARD",
                                 "platform_name": "HTB"
                             })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["step"], 2)

        # LLM should NOT have been called for the fast-path message
        self.assertEqual(mock_route_request.call_count, calls_after_start)

        # Verify session has correct fields in DB
        session = self.db.query(IntakeSessionModel).filter(IntakeSessionModel.id == sid).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.fields.get("name"), "Fast Path Test")
        self.assertEqual(session.fields.get("type"), "pwn")
        self.assertEqual(session.fields.get("difficulty"), "HARD")
        self.assertEqual(session.fields.get("platform"), "HTB")

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_step1_raw_message_reaches_model(self, mock_route_request):
        """A step-1 request carrying only raw_message must reach the model prompt."""
        mock_route_request.return_value = self._make_response(json.dumps({
            "reply": "Thanks — could you also tell me the category?",
            "fields": {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]},
            "ready_to_create": False
        }))

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        raw = "it's a web challenge called WebApp50"
        r = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                             json={"raw_message": raw})
        self.assertEqual(r.status_code, 200)

        # The model router must have been called for this message (the opener
        # call also hits the router), and the message prompt must contain the
        # operator's raw text (not a placeholder).
        self.assertGreaterEqual(mock_route_request.call_count, 2)
        message_calls = [
            c for c in mock_route_request.call_args_list
            if raw in c.kwargs.get("prompt", "")
        ]
        self.assertEqual(len(message_calls), 1)

        # Persisted turn content must be the raw sentence too
        turns = self.db.query(IntakeTurnModel).filter(
            IntakeTurnModel.session_id == sid,
            IntakeTurnModel.role == "user",
        ).all()
        self.assertTrue(any(t.content == raw for t in turns))

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_malformed_llm_output_falls_back_to_gate(self, mock_route_request):
        """Malformed/refused LLM output falls back to deterministic gate, no 500."""
        # Return invalid JSON (prose refusal)
        mock_route_request.return_value = self._make_response("I cannot help with that request.")

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        # Send only name - LLM will be called but returns garbage
        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Malformed LLM Test"})
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        # Should still be at step 1, asking for missing fields (deterministic gate)
        self.assertEqual(body1["step"], 1)
        self.assertIn("type", body1.get("awaiting", []))
        self.assertIn("difficulty", body1.get("awaiting", []))

        # Now provide remaining fields - should advance
        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_type": "crypto", "difficulty": "EASY"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], 2)
        self.assertIn("crypto", body2["bot_message"])
        self.assertIn("EASY", body2["bot_message"])

        # Complete turn 2
        r3 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"description": "Test malformed fallback"})
        self.assertEqual(r3.status_code, 200)
        body3 = r3.json()
        self.assertEqual(body3["step"], "committed")

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_llm_refusal_falls_back_to_gate(self, mock_route_request):
        """LLM refusal (is_refusal=True) falls back to deterministic gate."""
        mock_route_request.return_value = self._make_response("{}", is_refusal=True)

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Malformed LLM Test"})
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        self.assertEqual(body1["step"], 1)
        self.assertIn("type", body1.get("awaiting", []))

        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_type": "rev", "difficulty": "INSANE"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], 2)

        r3 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"description": "Test refusal fallback"})
        self.assertEqual(r3.status_code, 200)
        body3 = r3.json()
        self.assertEqual(body3["step"], "committed")

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_turn2_commits_status_and_challenge_id(self, mock_route_request):
        """Turn 2 sets status=COMMITTED and stores challenge_id."""
        mock_route_request.return_value = self._make_response(json.dumps({
            "reply": "Challenge created!",
            "fields": {},
            "ready_to_create": True
        }))

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        # Turn 1: provide all required fields
        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Turn2 Commit Test",
                                    "challenge_type": "forensics",
                                    "difficulty": "MEDIUM"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["step"], 2)

        # Turn 2: provide description
        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"description": "Analyze the memory dump",
                                    "target_address": "memdump.raw"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], "committed")
        self.assertIn("challenge", body2)
        challenge_id = body2["challenge"]["id"]

        # Verify intake session
        session = self.db.query(IntakeSessionModel).filter(IntakeSessionModel.id == sid).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.status, "COMMITTED")
        self.assertEqual(session.challenge_id, challenge_id)
        self.assertEqual(session.step, 2)

        # Verify challenge was created
        challenge = self.db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
        self.assertIsNotNone(challenge)
        self.assertEqual(challenge.name, "Turn2 Commit Test")
        self.assertEqual(challenge.category, "FORENSICS")
        self.assertEqual(challenge.difficulty, "MEDIUM")

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_resume_session_at_step2(self, mock_route_request):
        """Resume a session that's already at step 2."""
        mock_route_request.return_value = self._make_response(json.dumps({
            "reply": "Challenge created!",
            "fields": {},
            "ready_to_create": True
        }))

        # Create session and advance to step 2 via fast path
        start = self.client.post("/api/challenges/chat-session")
        sid = start.json()["session_id"]

        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Resume Session Test",
                                    "challenge_type": "recon",
                                    "difficulty": "EASY"})
        self.assertEqual(r1.json()["step"], 2)

        # Simulate restart - new client
        new_client = TestClient(app)

        # Resume at step 2 with description
        r2 = new_client.post(f"/api/challenges/chat-session/{sid}/message",
                             json={"description": "Scan the network", "target_address": "192.168.1.1"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], "committed")
        self.assertEqual(body2["challenge"]["name"], "Resume Session Test")
        self.assertEqual(body2["challenge"]["category"], "RECON")

    async def test_404_on_unknown_session(self):
        """Unknown session_id returns 404."""
        r = self.client.post("/api/challenges/chat-session/nonexistent/message",
                             json={"challenge_name": "Test"})
        self.assertEqual(r.status_code, 404)

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_404_on_committed_session(self, mock_route_request):
        """Committed session cannot be reused."""
        mock_route_request.return_value = self._make_response(json.dumps({
            "reply": "Challenge created!",
            "fields": {},
            "ready_to_create": True
        }))

        start = self.client.post("/api/challenges/chat-session")
        sid = start.json()["session_id"]

        # Complete the session
        self.client.post(f"/api/challenges/chat-session/{sid}/message",
                         json={"challenge_name": "Test", "challenge_type": "web", "difficulty": "EASY"})
        self.client.post(f"/api/challenges/chat-session/{sid}/message",
                         json={"description": "Done"})

        # Try to reuse - should 404
        r = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                             json={"description": "Again"})
        self.assertEqual(r.status_code, 404)

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_opener_uses_model_reply(self, mock_route_request):
        """A valid model JSON reply becomes the opening bot_message."""
        model_reply = "Welcome! What is the challenge name, category, and difficulty?"
        mock_route_request.return_value = self._make_response(json.dumps({
            "reply": model_reply,
            "fields": {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]},
            "ready_to_create": False
        }))

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        body = start.json()
        self.assertEqual(body["step"], 1)
        self.assertEqual(body["bot_message"], model_reply)
        self.assertNotEqual(body["bot_message"], _INTAKE_OPENING_PROMPT)

        # Persisted opening turn must match the returned message.
        turns = self.db.query(IntakeTurnModel).filter(
            IntakeTurnModel.session_id == body["session_id"],
            IntakeTurnModel.role == "assistant",
        ).all()
        self.assertEqual([t.content for t in turns], [model_reply])

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_opener_malformed_output_falls_back(self, mock_route_request):
        """Malformed model output falls back to the fixed opening prompt."""
        mock_route_request.return_value = self._make_response("I cannot help with that request.")

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        body = start.json()
        self.assertEqual(body["bot_message"], _INTAKE_OPENING_PROMPT)

        turns = self.db.query(IntakeTurnModel).filter(
            IntakeTurnModel.session_id == body["session_id"],
            IntakeTurnModel.role == "assistant",
        ).all()
        self.assertEqual([t.content for t in turns], [_INTAKE_OPENING_PROMPT])

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_opener_router_exception_falls_back(self, mock_route_request):
        """A router exception falls back to the fixed opening prompt, still HTTP 200."""
        mock_route_request.side_effect = Exception("Network error")

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.json()["bot_message"], _INTAKE_OPENING_PROMPT)

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_llm_exception_returns_none_falls_back(self, mock_route_request):
        """Exception from router returns None, falls back to deterministic gate."""
        mock_route_request.side_effect = Exception("Network error")

        start = self.client.post("/api/challenges/chat-session")
        sid = start.json()["session_id"]

        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Exception Test"})
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        self.assertEqual(body1["step"], 1)
        self.assertIn("type", body1.get("awaiting", []))

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_conversational_category_advances_in_same_turn(self, mock_route_request):
        """
        Conversational turn where operator provides only category (e.g. "it's a web challenge"),
        with name and difficulty already present. Model returns fields={"category": "WEB", ...}.
        Assert:
        1. Category is stored as fields["type"] (not fields["category"])
        2. Session advances to step 2 in the same turn (not on a later message)
        """
        # First call is the opening message; then turn 1 (no category);
        # then turn 2 (model returns category).
        mock_route_request.side_effect = [
            self._make_response(json.dumps({
                "reply": "What is the challenge name, category, and difficulty?",
                "fields": {
                    "name": None,
                    "platform": None,
                    "category": None,
                    "difficulty": None,
                    "target_address": None,
                    "description": None
                },
                "ready_to_create": False
            })),
            self._make_response(json.dumps({
                "reply": "I need more info.",
                "fields": {
                    "name": None,
                    "platform": None,
                    "category": None,
                    "difficulty": None,
                    "target_address": None,
                    "description": None
                },
                "ready_to_create": False
            })),
            self._make_response(json.dumps({
                "reply": "Got it, a web challenge.",
                "fields": {
                    "name": None,  # already have
                    "platform": None,
                    "category": "WEB",  # model uses "category"
                    "difficulty": None,  # already have
                    "target_address": None,
                    "description": None
                },
                "ready_to_create": False
            })),
        ]

        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        # Turn 1: Provide name and difficulty via structured fields (slow path, not fast path)
        # This leaves category missing, so LLM will be called
        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Conv Category Test", "difficulty": "EASY"})
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        # Should still be at step 1, awaiting category
        self.assertEqual(body1["step"], 1)
        self.assertIn("type", body1.get("awaiting", []))  # gate uses "type"

        # Turn 2: Operator provides category via conversational message (simulated by empty structured fields)
        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={})  # no structured fields, just conversational
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()

        # Should advance to step 2 in THIS turn (not require a third message)
        self.assertEqual(body2["step"], 2, "Session should advance to step 2 when model provides missing category")

        # Verify session fields: category should be stored as "type", not "category"
        # Model normalizes category to lowercase canonical form (e.g., "WEB" -> "web")
        session = self.db.query(IntakeSessionModel).filter(IntakeSessionModel.id == sid).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.fields.get("type"), "web", "Category should be stored as 'type' key (normalized)")
        self.assertNotIn("category", session.fields, "Session should not have 'category' key")
        self.assertEqual(session.fields.get("name"), "Conv Category Test")
        self.assertEqual(session.fields.get("difficulty"), "EASY")

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_step1_advance_keeps_model_reply_and_guidance(self, mock_route_request):
        """When intake completes on the slow path, bot_message keeps the model's
        reply AND still carries the step-2 guidance."""
        distinctive = "Great, that's everything I need."
        mock_route_request.side_effect = [
            self._make_response(json.dumps({
                "reply": "What is the challenge name, category, and difficulty?",
                "fields": {}, "ready_to_create": False,
            })),
            self._make_response(json.dumps({
                "reply": "I still need the difficulty.",
                "fields": {}, "ready_to_create": False,
            })),
            self._make_response(json.dumps({
                "reply": distinctive,
                "fields": {}, "ready_to_create": False,
            })),
        ]

        start = self.client.post("/api/challenges/chat-session")
        sid = start.json()["session_id"]

        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Model Reply Test",
                                    "challenge_type": "web"})
        self.assertEqual(r1.json()["step"], 1)

        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"difficulty": "EASY"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], 2)
        self.assertIn(distinctive, body2["bot_message"])
        self.assertIn("what is your goal for this challenge", body2["bot_message"])

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    async def test_step1_advance_without_model_reply_keeps_guidance(self, mock_route_request):
        """When the model returns None, bot_message still carries step-2 guidance."""
        mock_route_request.side_effect = [
            self._make_response(json.dumps({
                "reply": "What is the challenge name, category, and difficulty?",
                "fields": {}, "ready_to_create": False,
            })),
            self._make_response(json.dumps({
                "reply": "I still need the difficulty.",
                "fields": {}, "ready_to_create": False,
            })),
            Exception("Network error"),
        ]

        start = self.client.post("/api/challenges/chat-session")
        sid = start.json()["session_id"]

        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "No Reply Test",
                                    "challenge_type": "crypto"})
        self.assertEqual(r1.json()["step"], 1)

        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"difficulty": "MEDIUM"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], 2)
        self.assertIn("what is your goal for this challenge", body2["bot_message"])


if __name__ == "__main__":
    unittest.main()