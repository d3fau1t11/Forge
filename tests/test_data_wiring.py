import os
import sys
import unittest
sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from backend.main import app
from backend.database.session import get_db, get_engine, SessionLocal, init_db
from backend.database.models import Base, ChallengeModel, TargetProfileModel, ProviderUsageModel, TrajectoryEventModel


class TestDataWiringEndpoints(unittest.TestCase):
    """Test suite verifying full data wiring endpoints for candidates, artifacts, decisions, targets, and providers."""

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)


    def setUp(self):
        self.db = SessionLocal()

        # Clean test records
        self.db.query(TargetProfileModel).delete()
        self.db.query(ChallengeModel).delete()
        self.db.query(ProviderUsageModel).delete()
        self.db.query(TrajectoryEventModel).delete()
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_challenge_candidates_artifact_decisions_endpoints(self):
        """Verify GET /challenges/{id}/candidates, /derived-artifacts, and /decisions return persisted/live state."""
        # 1. Create a challenge with persisted blackboard snapshot
        ch = ChallengeModel(
            id="ch-test-wiring-1",
            name="Test Wiring Challenge",
            category="WEB",
            status="RUNNING",
            mission_plan={
                "blackboard_state": {
                    "flag_candidates": [{"flag": "picoCTF{test_candidate_123}", "worker": "RECON", "source": "regex"}],
                    "derived_artifacts": [{"filename": "decoded_secret.png", "artifact_type": "image", "size_bytes": 1024, "status": "RECONSTRUCTED"}]
                }
            }
        )
        self.db.add(ch)

        # 2. Add a trajectory event decision
        ev = TrajectoryEventModel(
            id="ev-dec-1",
            session_id="s-test-1",
            challenge_id="ch-test-wiring-1",
            event_type="AI_DECISION",
            agent_id="ORCHESTRATOR",
            decision_summary="Pivot to directory fuzzing on /admin",
            action_type="ffuf",
            tool_name="ffuf",
            result="SUCCESS"
        )
        self.db.add(ev)
        self.db.commit()

        # 3. Query GET /challenges/{id}/candidates
        resp_cand = self.client.get("/api/challenges/ch-test-wiring-1/candidates")
        self.assertEqual(resp_cand.status_code, 200)
        cands = resp_cand.json().get("candidates", [])
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["flag"], "picoCTF{test_candidate_123}")

        # 4. Query GET /challenges/{id}/derived-artifacts
        resp_art = self.client.get("/api/challenges/ch-test-wiring-1/derived-artifacts")
        self.assertEqual(resp_art.status_code, 200)
        arts = resp_art.json().get("artifacts", [])
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["filename"], "decoded_secret.png")

        # 5. Query GET /challenges/{id}/decisions
        resp_dec = self.client.get("/api/challenges/ch-test-wiring-1/decisions")
        self.assertEqual(resp_dec.status_code, 200)
        decs = resp_dec.json().get("decisions", [])
        self.assertTrue(len(decs) >= 1)
        self.assertEqual(decs[0]["selectedTool"], "ffuf")

    def test_targets_endpoint_returns_real_fields(self):
        """Verify GET /targets returns expected_services, technologies, address_history, discovery_method."""
        ch = ChallengeModel(id="ch-target-test", name="Target Test", category="WEB", status="RUNNING")
        self.db.add(ch)

        target = TargetProfileModel(
            id="t-1",
            challenge_id="ch-target-test",
            current_address="10.10.10.10",
            hostname="target.ctf",
            expected_services=[{"port": 8080, "proto": "tcp", "service": "HTTP-ALT", "version": "Apache 2.4"}],
            technologies=["Linux", "Apache", "PHP"],
            address_history=["10.10.10.10", "192.168.1.50"],
            discovery_method="TURBO RECON",
            verification_status="verified_network"
        )
        self.db.add(target)
        self.db.commit()

        resp = self.client.get("/api/targets")
        self.assertEqual(resp.status_code, 200)
        targets_list = resp.json()
        self.assertTrue(len(targets_list) >= 1)
        t_data = targets_list[0]
        self.assertEqual(t_data["current_address"], "10.10.10.10")
        self.assertEqual(t_data["expected_services"][0]["port"], 8080)
        self.assertIn("PHP", t_data["technologies"])
        self.assertEqual(t_data["discovery_method"], "TURBO RECON")

    def test_providers_endpoint_returns_requests_errors_priority(self):
        """Verify GET /providers returns calculated requests, last_error, and fallback_priority."""
        usage = ProviderUsageModel(
            provider_name="groq",
            model_name="groq-compound",
            prompt_tokens=100,
            completion_tokens=50,
            success=True
        )
        self.db.add(usage)
        self.db.commit()

        resp = self.client.get("/api/providers")
        self.assertEqual(resp.status_code, 200)
        provs = resp.json()
        self.assertTrue(len(provs) > 0)
        groq_prov = next((p for p in provs if p["name"] == "groq"), None)
        self.assertIsNotNone(groq_prov)
        self.assertIn("requests", groq_prov)
        self.assertIn("last_error", groq_prov)
        self.assertIn("fallback_priority", groq_prov)
        self.assertEqual(groq_prov["requests"], 1)


if __name__ == "__main__":
    unittest.main()
