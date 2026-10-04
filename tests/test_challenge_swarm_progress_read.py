"""Read-path overlay tests: GET /api/challenges and /api/challenges/{id} must reflect
the live SwarmCoordinator mission (swarm_missions) progress/status instead of the
stale ChallengeModel columns the default engine never writes.

Runs exclusively against the isolated test_forge.db (see AGENTS.md Rule 5).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from fastapi.testclient import TestClient

from backend.main import app
from backend.database.session import init_db, SessionLocal
from backend.database.models import ChallengeModel, SwarmMissionModel


_TEST_CHALLENGE_IDS = ("ch-swarm-read-1", "ch-swarm-read-2", "ch-swarm-read-3")


class TestChallengeSwarmProgressRead(unittest.TestCase):
    """Verify the canonical challenge read path overlays swarm mission state."""

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    def setUp(self):
        self.db = SessionLocal()
        self._purge()
        # ChallengeModel parents first in production order, but clean children-free
        # rows here; purge mission rows before challenges.
        self.db.query(SwarmMissionModel).filter(
            SwarmMissionModel.challenge_id.in_(_TEST_CHALLENGE_IDS)
        ).delete(synchronize_session=False)
        self.db.query(ChallengeModel).filter(
            ChallengeModel.id.in_(_TEST_CHALLENGE_IDS)
        ).delete(synchronize_session=False)
        self.db.commit()

    def tearDown(self):
        self._purge()
        if self.db:
            self.db.close()

    def _purge(self):
        self.db.query(SwarmMissionModel).filter(
            SwarmMissionModel.challenge_id.in_(_TEST_CHALLENGE_IDS)
        ).delete(synchronize_session=False)
        self.db.query(ChallengeModel).filter(
            ChallengeModel.id.in_(_TEST_CHALLENGE_IDS)
        ).delete(synchronize_session=False)
        self.db.commit()

    def _add_challenge(self, cid: str, *, progress: int, status: str):
        self.db.add(ChallengeModel(
            id=cid, name=f"Swarm Read {cid}", category="WEB",
            status=status, progress=progress,
        ))
        self.db.commit()

    def _add_mission(self, cid: str, *, progress: int, status: str, mission_id: str):
        self.db.add(SwarmMissionModel(
            id=mission_id, challenge_id=cid, status=status, progress=progress,
        ))
        self.db.commit()

    def test_detail_prefers_swarm_mission_progress(self):
        """A challenge with a SwarmMissionModel row returns the mission's progress."""
        self._add_challenge("ch-swarm-read-1", progress=0, status="QUEUED")
        self._add_mission("ch-swarm-read-1", progress=42, status="RUNNING",
                          mission_id="sm-read-1")

        resp = self.client.get("/api/challenges/ch-swarm-read-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["progress"], 42)
        self.assertEqual(body["status"], "RUNNING")

    def test_detail_falls_back_without_swarm_mission(self):
        """No mission row -> the challenge's own progress/status are unchanged."""
        self._add_challenge("ch-swarm-read-2", progress=7, status="AWAITING_FLAG")

        resp = self.client.get("/api/challenges/ch-swarm-read-2")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["progress"], 7)
        self.assertEqual(body["status"], "AWAITING_FLAG")

    def test_list_prefers_swarm_mission_progress(self):
        """The list endpoint overlays mission progress and preserves fallbacks."""
        self._add_challenge("ch-swarm-read-1", progress=0, status="QUEUED")
        self._add_mission("ch-swarm-read-1", progress=42, status="RUNNING",
                          mission_id="sm-read-list-1")
        self._add_challenge("ch-swarm-read-3", progress=13, status="PAUSED")

        resp = self.client.get("/api/challenges")
        self.assertEqual(resp.status_code, 200)
        by_id = {c["id"]: c for c in resp.json()}

        self.assertEqual(by_id["ch-swarm-read-1"]["progress"], 42)
        self.assertEqual(by_id["ch-swarm-read-1"]["status"], "RUNNING")
        # No mission row -> fall back to ChallengeModel values unchanged.
        self.assertEqual(by_id["ch-swarm-read-3"]["progress"], 13)
        self.assertEqual(by_id["ch-swarm-read-3"]["status"], "PAUSED")

    def test_completed_and_cancelled_status_mapping(self):
        """COMPLETED -> COMPLETED; CANCELLED/FAILED -> FAILED (App.tsx equivalence)."""
        self._add_challenge("ch-swarm-read-1", progress=0, status="RUNNING")
        self._add_mission("ch-swarm-read-1", progress=100, status="COMPLETED",
                          mission_id="sm-read-done")
        done = self.client.get("/api/challenges/ch-swarm-read-1").json()
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(done["progress"], 100)

        self._add_challenge("ch-swarm-read-2", progress=0, status="RUNNING")
        self._add_mission("ch-swarm-read-2", progress=55, status="CANCELLED",
                          mission_id="sm-read-cancel")
        cancelled = self.client.get("/api/challenges/ch-swarm-read-2").json()
        self.assertEqual(cancelled["status"], "FAILED")
        self.assertEqual(cancelled["progress"], 55)


if __name__ == "__main__":
    unittest.main()
