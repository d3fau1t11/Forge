"""Workstream F: chat-driven challenge creation — conversational missing-field
collection, category/difficulty normalization, and the '+' multi-target convention."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database.session import init_db
from backend.utils.challenge_normalize import (
    normalize_category, normalize_difficulty, normalize_targets,
)


class TestChallengeNormalization(unittest.TestCase):

    def test_category_normalization(self):
        self.assertEqual(normalize_category("binary exploitation"), "pwn")
        self.assertEqual(normalize_category("Reverse Engineering"), "rev")
        self.assertEqual(normalize_category("Web"), "web")
        self.assertEqual(normalize_category("cryptography"), "crypto")
        self.assertIsNone(normalize_category("   "))

    def test_difficulty_normalization(self):
        self.assertEqual(normalize_difficulty("insane"), "INSANE")
        self.assertEqual(normalize_difficulty("beginner"), "EASY")
        self.assertEqual(normalize_difficulty("HARD"), "HARD")
        self.assertEqual(normalize_difficulty(""), "MEDIUM")           # default
        self.assertEqual(normalize_difficulty(None, default="MEDIUM"), "MEDIUM")

    def test_multi_target_joined_with_plus(self):
        self.assertEqual(normalize_targets("http://x:8080, /path/a.pcap, 10.10.14.23"),
                         "http://x:8080 + /path/a.pcap + 10.10.14.23")
        self.assertEqual(normalize_targets("http://x:8080\nnc host 9000"),
                         "http://x:8080 + nc host 9000")
        self.assertEqual(normalize_targets("a + a + b"), "a + b")       # dedupe, order kept
        self.assertEqual(normalize_targets(""), "")


class TestConversationalCreationFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        # These tests commit real challenge rows (plus their chat_messages and
        # targets) into test_forge.db. Leaving them behind makes later suites
        # fail: e.g. test_completion_gate's `DELETE FROM challenges` trips an FK
        # constraint. Remove children before parents, deepest first.
        from backend.database.session import SessionLocal
        from backend.database.models import (
            ChallengeModel, ChatMessageModel, TargetProfileModel, RunModel,
            CheckpointModel, ToolExecutionModel, AgentStateModel, FindingModel,
            EvidenceModel, ReportModel,
        )

        names = ("Impossible Password", "Test", "Full Flow Test")
        db = SessionLocal()
        try:
            ids = [c.id for c in db.query(ChallengeModel)
                   .filter(ChallengeModel.name.in_(names)).all()]
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
                db.commit()
        finally:
            db.close()

    def test_incremental_missing_field_collection(self):
        # 1. Start a session.
        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        # 2. Provide ONLY the name — the bot must ask for the still-missing fields and NOT fail.
        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Impossible Password"})
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        self.assertEqual(body1["step"], 1)                 # still collecting
        self.assertIn("type", body1.get("awaiting", []))
        self.assertIn("difficulty", body1.get("awaiting", []))
        self.assertNotIn("name", body1.get("awaiting", []))  # name already captured

        # 3. Provide the remaining fields (free-text category + difficulty) -> advance.
        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_type": "binary exploitation", "difficulty": "insane"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], 2)                 # all required fields gathered
        self.assertIn("pwn", body2["bot_message"])         # category normalized
        self.assertIn("INSANE", body2["bot_message"])      # difficulty normalized

    def test_client_contract_includes_difficulty(self):
        """Ensure the backend's opening prompt mentions difficulty and the request model accepts it."""
        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        body = start.json()
        # The opening bot message must mention all four required fields including difficulty
        self.assertIn("difficulty", body["bot_message"].lower())
        self.assertIn("easy", body["bot_message"].lower())
        self.assertIn("medium", body["bot_message"].lower())
        self.assertIn("hard", body["bot_message"].lower())
        self.assertIn("insane", body["bot_message"].lower())

        # The request model for turn 1 must accept difficulty field
        sid = body["session_id"]
        r = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Test", "challenge_type": "web", "difficulty": "EASY"})
        self.assertEqual(r.status_code, 200)
        body2 = r.json()
        # Should advance to step 2 since all required fields provided
        self.assertEqual(body2["step"], 2)

    def test_full_conversation_creates_challenge(self):
        """End-to-end: name-only -> asks for remaining -> type+description+difficulty -> completes."""
        start = self.client.post("/api/challenges/chat-session")
        self.assertEqual(start.status_code, 200)
        sid = start.json()["session_id"]

        # Turn 1: provide only name
        r1 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_name": "Full Flow Test"})
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        self.assertEqual(body1["step"], 1)
        self.assertIn("type", body1.get("awaiting", []))
        self.assertIn("difficulty", body1.get("awaiting", []))

        # Turn 2: provide category and difficulty
        r2 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"challenge_type": "crypto", "difficulty": "HARD"})
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertEqual(body2["step"], 2)
        self.assertIn("crypto", body2["bot_message"])
        self.assertIn("HARD", body2["bot_message"])

        # Turn 3: provide description (required) and optional target
        r3 = self.client.post(f"/api/challenges/chat-session/{sid}/message",
                              json={"description": "Break the encryption and find the flag", "target_address": "10.10.10.10"})
        self.assertEqual(r3.status_code, 200)
        body3 = r3.json()
        self.assertEqual(body3["step"], "committed")
        self.assertIn("challenge", body3)
        self.assertEqual(body3["challenge"]["name"], "Full Flow Test")
        self.assertEqual(body3["challenge"]["category"], "CRYPTO")
        self.assertEqual(body3["challenge"]["difficulty"], "HARD")


if __name__ == "__main__":
    unittest.main()
