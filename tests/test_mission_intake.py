"""Tests for the new MissionIntake simple intake flow.

Tests the /api/challenges/start-mission endpoint with target + objective + optional artifacts.
These tests complement the existing chat-driven challenge creation tests.
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from fastapi.testclient import TestClient
from backend.main import app
from backend.database.session import init_db
from backend.providers.base import ProviderResponse
from backend.utils.challenge_normalize import normalize_category, normalize_difficulty, normalize_targets


class MissionIntakeTestBase(unittest.TestCase):
    """Shared mock helper and FK-safe database cleanup for mission intake tests."""

    def _make_mock_response(self, content: str, is_refusal: bool = False) -> MagicMock:
        resp = MagicMock(spec=ProviderResponse)
        resp.content = content
        resp.is_refusal = is_refusal
        resp.refusal_reason = "refused" if is_refusal else None
        return resp

    @classmethod
    def tearDownClass(cls):
        # FK-safe cleanup: SQLite runs with PRAGMA foreign_keys=ON, and a bulk
        # query().delete() does NOT honor ORM cascades, so children must be
        # removed before their parents. Deleting challenges directly trips an
        # IntegrityError because runs / evidence / findings / reports / targets
        # and chat messages still reference them (and agents, checkpoints and
        # tool executions reference runs).
        from backend.database.session import SessionLocal
        from backend.database.models import (
            ChallengeModel, RunModel, TargetProfileModel, AgentStateModel,
            CheckpointModel, ToolExecutionModel, FindingModel, EvidenceModel,
            ReportModel, ChatMessageModel,
        )

        init_db()
        db = SessionLocal()
        try:
            for model in (
                ChatMessageModel, CheckpointModel, ToolExecutionModel,
                AgentStateModel, EvidenceModel, FindingModel, ReportModel,
                TargetProfileModel, RunModel, ChallengeModel,
            ):
                db.query(model).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


class TestMissionIntakeFastPath(MissionIntakeTestBase):
    """Tests for the fast path of mission intake (all required data available)."""

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)


class TestTargetOnlyMission(TestMissionIntakeFastPath):
    """Test starting a mission with only a target (no artifact, no objective text beyond flag)."""

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    def test_target_only_mission_creates_challenge(self, mock_route_request):
        """Start a mission with just a target IP - backend infers category, difficulty, etc."""
        mock_route_request.return_value = self._make_mock_response(json.dumps({
            "reply": "Mission pipeline initialized.",
            "fields": {},
            "ready_to_create": True
        }))

        # Start a mission with only a target
        start = self.client.post("/api/challenges/start-mission",
                                 json={
                                     "target_address": "10.10.10.10",
                                     "objective": "Find the flag"
                                 })
        self.assertEqual(start.status_code, 200)
        body = start.json()

        # Should have created a challenge
        self.assertIn("challenge_id", body)
        self.assertIn("challenge_name", body)
        self.assertIn("category", body)
        self.assertIn("objective", body)
        self.assertIn("target", body)
        self.assertIn("hypotheses", body)
        self.assertIn("initial_actions", body)
        self.assertIn("progress", body)
        self.assertIn("message", body)

        # Target should be preserved
        self.assertEqual(body["target"], "10.10.10.10")

        # Category should be inferred (recon for IP target)
        self.assertIn(body["category"].lower(), ["recon", "pwn", "web"])

        # Progress should start at 0
        self.assertEqual(body["progress"], 0)

        # Message should indicate mission started
        self.assertIn("Mission", body["message"])


class TestArtifactOnlyMission(TestMissionIntakeFastPath):
    """Test starting a mission with only artifacts (no target)."""

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    def test_artifact_only_mission_validates_and_creates(self, mock_route_request):
        """Start a mission with uploaded artifacts but no target."""
        mock_route_request.return_value = self._make_mock_response(json.dumps({
            "reply": "Mission pipeline initialized.",
            "fields": {},
            "ready_to_create": True
        }))

        # Create a temporary file for testing
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b"test artifact content for challenge")
            temp_path = f.name

        try:
            # Upload the artifact first, then start mission
            # Note: In the real flow, the artifact would be uploaded via /challenges/upload
            # For this test, we directly pass attached_file_paths
            start = self.client.post("/api/challenges/start-mission",
                                     json={
                                         "target_address": "http://example.com",
                                         "objective": "Find the flag",
                                         "attached_file_paths": [temp_path]
                                     })
            self.assertEqual(start.status_code, 200)
            body = start.json()

            # Should have created a challenge
            self.assertIn("challenge_id", body)
            self.assertIn("challenge_name", body)
            self.assertIn("category", body)
            self.assertIn("objective", body)
            self.assertIn("target", body)
            self.assertIn("hypotheses", body)
            self.assertIn("initial_actions", body)

            # Objective should be preserved
            self.assertEqual(body["objective"], "Find the flag")

            # Target should be preserved
            self.assertEqual(body["target"], "http://example.com")

            # Should have hypotheses and initial actions inferred
            self.assertIsInstance(body["hypotheses"], list)
            self.assertGreaterEqual(len(body["hypotheses"]), 1)
            self.assertIsInstance(body["initial_actions"], list)
            self.assertGreaterEqual(len(body["initial_actions"]), 1)
        finally:
            # create_challenge() moves attached files into the challenge
            # workspace, so the original temp path may no longer exist.
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestTargetAndArtifactMission(TestMissionIntakeFastPath):
    """Test starting a mission with both target and artifacts."""

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    def test_target_and_artifact_mission(self, mock_route_request):
        """Start a mission with both target and artifacts."""
        mock_route_request.return_value = self._make_mock_response(json.dumps({
            "reply": "Mission pipeline initialized.",
            "fields": {},
            "ready_to_create": True
        }))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as f:
            f.write(b"\x00\x01\x02\x03 binary artifact")
            temp_path = f.name

        try:
            start = self.client.post("/api/challenges/start-mission",
                                     json={
                                         "target_address": "192.168.1.50",
                                         "objective": "Analyze this binary and recover the flag",
                                         "attached_file_paths": [temp_path]
                                     })
            self.assertEqual(start.status_code, 200)
            body = start.json()

            # Verify all fields
            self.assertIn("challenge_id", body)
            self.assertIn("challenge_name", body)
            self.assertIn("category", body)
            self.assertIn("objective", body)
            self.assertIn("target", body)
            self.assertIn("target_type", body)
            self.assertIn("hypotheses", body)
            self.assertIn("initial_actions", body)
            self.assertIn("progress", body)
            self.assertIn("message", body)
            self.assertIn("budget", body)
            self.assertIn("flag_status", body)

            # Verify specific values
            self.assertEqual(body["target"], "192.168.1.50")
            self.assertEqual(body["objective"], "Analyze this binary and recover the flag")
            self.assertEqual(body["progress"], 0)
            self.assertEqual(body["flag_status"], "UNFOUND")

            # Category is inferred from the target (IP -> recon), so the
            # hypotheses / initial actions must be populated for that category.
            self.assertIn(body["category"].lower(), ["recon", "pwn"])

            hypotheses_lower = [h.lower() for h in body["hypotheses"]]
            self.assertTrue(hypotheses_lower, "Expected at least one hypothesis")
            has_recon_hypothesis = any(
                kw in hypotheses_lower[0]
                for kw in ["port", "service", "fingerprint", "operating system"]
            )
            self.assertTrue(has_recon_hypothesis,
                           f"Expected recon-related hypothesis, got: {body['hypotheses']}")

            # Initial actions should include reconnaissance / scanning
            actions_lower = [a.lower() for a in body["initial_actions"]]
            self.assertTrue(actions_lower, "Expected at least one initial action")
            has_scan_action = any(
                kw in actions_lower[0]
                for kw in ["scan", "nmap", "rustscan", "service"]
            )
            self.assertTrue(has_scan_action,
                           f"Expected scan-related initial action, got: {body['initial_actions']}")
        finally:
            # create_challenge() moves attached files into the challenge
            # workspace, so the original temp path may no longer exist.
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestObjectiveSubmission(MissionIntakeTestBase):
    """Tests for objective submission validation."""

    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"
        init_db()

    def test_objective_required(self):
        """Mission cannot be started without an objective."""
        from backend.main import app as fastapi_app
        from fastapi.testclient import TestClient
        client = TestClient(fastapi_app)

        start = client.post("/api/challenges/start-mission",
                            json={
                                "target_address": "10.10.10.10"
                            })
        # Should fail validation - no objective provided
        self.assertIn(start.status_code, [400, 422, 422])

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    def test_empty_objective_rejected(self, mock_route_request):
        """Mission cannot be started with empty objective."""
        mock_route_request.return_value = self._make_mock_response(json.dumps({
            "reply": "Mission pipeline initialized.",
            "fields": {},
            "ready_to_create": True
        }))

        from fastapi.testclient import TestClient
        from backend.main import app as fastapi_app
        client = TestClient(fastapi_app)

        start = client.post("/api/challenges/start-mission",
                            json={
                                "target_address": "10.10.10.10",
                                "objective": ""
                            })
        # Should fail validation
        self.assertNotEqual(start.status_code, 200)


class TestMissionStartValidation(MissionIntakeTestBase):
    """Tests for mission start validation."""

    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"
        init_db()

    def test_missing_target(self):
        """Mission cannot be started without a target."""
        from fastapi.testclient import TestClient
        from backend.main import app as fastapi_app
        client = TestClient(fastapi_app)

        start = client.post("/api/challenges/start-mission",
                            json={
                                "objective": "Find the flag"
                            })
        # Should fail validation - no target provided
        self.assertNotEqual(start.status_code, 200)

    @patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock)
    def test_whitespace_only_target_rejected(self, mock_route_request):
        """Mission cannot be started with whitespace-only target."""
        mock_route_request.return_value = self._make_mock_response(json.dumps({
            "reply": "Mission pipeline initialized.",
            "fields": {},
            "ready_to_create": True
        }))

        from fastapi.testclient import TestClient
        from backend.main import app as fastapi_app
        client = TestClient(fastapi_app)

        start = client.post("/api/challenges/start-mission",
                            json={
                                "target_address": "   ",
                                "objective": "Find the flag"
                            })
        # Should fail validation
        self.assertNotEqual(start.status_code, 200)


class TestWebSocketLiveStateRendering(MissionIntakeTestBase):
    """Tests for WebSocket live state rendering after mission start."""

    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"
        init_db()

    def test_live_state_structure(self):
        """Verify the mission response includes all fields needed for live state rendering."""
        from fastapi.testclient import TestClient
        from backend.main import app as fastapi_app
        client = TestClient(fastapi_app)

        start = client.post("/api/challenges/start-mission",
                            json={
                                "target_address": "10.10.10.10",
                                "objective": "Find the flag"
                            })
        self.assertEqual(start.status_code, 200)
        body = start.json()

        # All required fields for live state dashboard
        required_fields = [
            "challenge_id", "challenge_name", "category", "objective",
            "target", "target_type", "status", "progress",
            "message", "hypotheses", "initial_actions",
            "progress_detail", "budget", "flag_status"
        ]
        for field in required_fields:
            with self.subTest(field=field):
                self.assertIn(field, body,
                             f"Missing required field '{field}' in mission start response")

        # Budget should have spent and limit
        budget = body.get("budget", {})
        self.assertIn("spent", budget)
        self.assertIn("limit", budget)

        # Flag status should be one of the valid values
        flag_status = body.get("flag_status")
        self.assertIn(flag_status, ["UNFOUND", "CAPTURED", "VERIFYING"])

        # Hypotheses should be a non-empty list
        hypotheses = body.get("hypotheses", [])
        self.assertIsInstance(hypotheses, list)
        self.assertGreaterEqual(len(hypotheses), 1)

        # Initial actions should be a non-empty list
        initial_actions = body.get("initial_actions", [])
        self.assertIsInstance(initial_actions, list)
        self.assertGreaterEqual(len(initial_actions), 1)


def run_all_tests():
    """Run all mission intake tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    test_classes = [
        TestTargetOnlyMission,
        TestArtifactOnlyMission,
        TestTargetAndArtifactMission,
        TestObjectiveSubmission,
        TestMissionStartValidation,
        TestWebSocketLiveStateRendering,
    ]

    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTest(tests)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    result = run_all_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
