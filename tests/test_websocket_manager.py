"""Tests for ConnectionManager.broadcast() failure observability.

A send failure on one socket must never break the fan-out for healthy sockets,
must be logged at WARNING, and must evict the dead socket from
``active_connections`` so it cannot silently accumulate forever.
"""
import os
import sys
import asyncio
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# No database is touched here, but every unit test pins DATABASE_URL at the
# isolated test database so an accidental import can never build a production
# engine. See .agents/rules/no_demo_data.md rule 5.
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.websocket.manager import ConnectionManager


class _HealthyWebSocket:
    def __init__(self):
        self.received = []

    async def accept(self):
        pass

    async def send_json(self, message):
        self.received.append(message)


class _BrokenWebSocket:
    def __init__(self):
        self.send_attempts = 0

    async def accept(self):
        pass

    async def send_json(self, message):
        self.send_attempts += 1
        raise RuntimeError("socket is closed")


class TestConnectionManagerBroadcast(unittest.TestCase):

    def test_failed_send_is_evicted_and_healthy_socket_still_delivered(self):
        manager = ConnectionManager()
        healthy = _HealthyWebSocket()
        broken = _BrokenWebSocket()

        asyncio.run(manager.connect(healthy))
        asyncio.run(manager.connect(broken))
        self.assertEqual(len(manager.active_connections), 2)

        payload = {"event": "RUN_STARTED", "status": "RUNNING"}

        # broadcast() is fire-and-forget: it must not raise.
        with self.assertLogs("forge.websocket", level="WARNING") as captured:
            asyncio.run(manager.broadcast(payload))

        # The healthy socket received the message exactly once.
        self.assertEqual(healthy.received, [payload])
        # The broken socket was dropped, leaving only the healthy connection.
        self.assertNotIn(broken, manager.active_connections)
        self.assertEqual(manager.active_connections, [healthy])
        # The failure was reported at WARNING level.
        self.assertTrue(
            any(record.levelname == "WARNING" for record in captured.records),
            "expected a WARNING log record for the failed send",
        )

    def test_broadcast_caps_failure_logging_to_actual_failures(self):
        manager = ConnectionManager()
        healthy = _HealthyWebSocket()

        asyncio.run(manager.connect(healthy))

        # No failures -> no warning records -> assertLogs must fail if one appears.
        with self.assertRaises(AssertionError):
            with self.assertLogs("forge.websocket", level="WARNING"):
                asyncio.run(manager.broadcast({"event": "PING"}))

        self.assertEqual(manager.active_connections, [healthy])


if __name__ == "__main__":
    unittest.main()
