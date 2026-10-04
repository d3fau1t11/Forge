"""Durable swarm event persistence tests.

Verifies that ``backend.swarm.events.broadcast`` writes a ``TrajectoryEventModel``
row BEFORE (and independently of) any WebSocket broadcast, so a disconnected or
crashed dashboard can reconstruct mission-level lifecycle events from the database.

Pinned to the isolated unit-test database, exactly like the other swarm test modules.
"""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

# Pinned above the first backend import on purpose (see tests/test_phase4_swarm.py).
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import TrajectoryEventModel, SwarmMissionModel
from backend.agent_runtime import trajectory_store
from backend.swarm import events as swarm_events
from backend.websocket.manager import ws_manager

_CHALLENGES = ("chal-swarm-ev", "chal-swarm-seq", "chal-swarm-fail", "chal-swarm-mission")
_MISSIONS = ("mission-swarm-ev",)


class SwarmEventPersistenceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self._clear()

    def tearDown(self):
        self._clear()

    def _clear(self):
        db = SessionLocal()
        try:
            (db.query(TrajectoryEventModel)
               .filter(TrajectoryEventModel.challenge_id.in_(_CHALLENGES))
               .delete(synchronize_session=False))
            (db.query(SwarmMissionModel)
               .filter(SwarmMissionModel.id.in_(_MISSIONS))
               .delete(synchronize_session=False))
            db.commit()
        finally:
            db.close()

    def _rows(self, challenge_id):
        db = SessionLocal()
        try:
            return (db.query(TrajectoryEventModel)
                    .filter(TrajectoryEventModel.challenge_id == challenge_id)
                    .order_by(TrajectoryEventModel.sequence.asc()).all())
        finally:
            db.close()

    # ------------------------------------------------------------------ #

    async def test_01_event_is_persisted_with_payload_preserved(self):
        with patch.object(ws_manager, "broadcast", AsyncMock()) as bcast:
            swarm_events.broadcast(swarm_events.FLAG_VERIFIED, {
                "challenge_id": "chal-swarm-ev",
                "run_id": "run-swarm-ev",
                "flag": "FLAG{persisted}",
                "agent_id": "web#1",
            })
            # Flush the scheduled fire-and-forget WS task, when a loop exists.
            await asyncio.sleep(0)

        rows = self._rows("chal-swarm-ev")
        self.assertEqual(len(rows), 1, "exactly one durable row must be written")
        row = rows[0]
        self.assertEqual(row.event_type, "SWARM_FLAG_VERIFIED")
        self.assertEqual(row.run_id, "run-swarm-ev")
        self.assertEqual(row.agent_id, "web#1")
        # The full payload is preserved even for fields with no dedicated column.
        self.assertEqual(row.observation.get("flag"), "FLAG{persisted}")
        # The original SWARM_* WS message is still emitted exactly once.
        self.assertEqual(bcast.await_count, 1)
        self.assertEqual(bcast.await_args.args[0]["event"], "SWARM_FLAG_VERIFIED")

    async def test_02_sequences_increase_monotonically_for_same_session(self):
        payload = {"challenge_id": "chal-swarm-seq", "run_id": "run-swarm-seq"}
        with patch.object(ws_manager, "broadcast", AsyncMock()):
            for _ in range(3):
                swarm_events.broadcast(swarm_events.TASK_CREATED, payload)
            await asyncio.sleep(0)

        seqs = [r.sequence for r in self._rows("chal-swarm-seq")]
        self.assertEqual(len(seqs), 3)
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs), "sequences must be unique")

    async def test_03_persistence_failure_skips_broadcast_and_does_not_raise(self):
        with patch.object(ws_manager, "broadcast", AsyncMock()) as bcast:
            with patch.object(trajectory_store, "record",
                              side_effect=RuntimeError("simulated db outage")):
                # Must not raise.
                swarm_events.broadcast(swarm_events.MISSION_STARTED, {
                    "challenge_id": "chal-swarm-fail", "run_id": "run-swarm-fail"})
                await asyncio.sleep(0)

        bcast.assert_not_called()
        self.assertEqual(self._rows("chal-swarm-fail"), [])

    async def test_04_record_returning_none_also_skips_broadcast(self):
        with patch.object(ws_manager, "broadcast", AsyncMock()) as bcast:
            with patch.object(trajectory_store, "record", return_value=None):
                swarm_events.broadcast(swarm_events.MISSION_STOP, {
                    "challenge_id": "chal-swarm-fail", "run_id": "run-swarm-fail"})
                await asyncio.sleep(0)

        bcast.assert_not_called()


    async def test_05_provenance_falls_back_to_the_mission_row(self):
        # Persist the mission the coordinator would already have saved.
        db = SessionLocal()
        try:
            db.add(SwarmMissionModel(
                id="mission-swarm-ev", run_id="run-from-mission",
                challenge_id="chal-swarm-mission", coord_session_id="coord-sess-ev",
                status="RUNNING"))
            db.commit()
        finally:
            db.close()

        # Payload deliberately omits challenge_id/run_id/session_id (as several
        # coordinator call sites do); provenance must be recovered from the mission.
        with patch.object(ws_manager, "broadcast", AsyncMock()):
            swarm_events.broadcast(swarm_events.AGENT_STATUS, {
                "mission_id": "mission-swarm-ev", "agent_id": "recon#1",
                "status": "RUNNING"})
            await asyncio.sleep(0)

        rows = self._rows("chal-swarm-mission")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].run_id, "run-from-mission")
        self.assertEqual(rows[0].session_id, "coord-sess-ev")


if __name__ == "__main__":
    unittest.main()
