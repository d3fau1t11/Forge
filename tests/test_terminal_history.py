"""
Tests for terminal command history persistence and replay.

Run with: ./.venv/Scripts/python.exe -m unittest tests.test_terminal_history
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from typing import Generator

# Set test database URL BEFORE any backend imports
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import app
from backend.database.session import init_db, get_db, SessionLocal
from backend.database.models import TerminalCommandModel, Base
from backend.utils.time import utcnow


class TestTerminalHistory(unittest.TestCase):
    """Test terminal command history persistence and retrieval."""

    @classmethod
    def setUpClass(cls):
        """Initialize test database and client."""
        # Import here to ensure DATABASE_URL is set
        from backend.database.session import get_engine
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        init_db()
        cls.client = TestClient(app)
        cls.db = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        """Clean up test database rows created during tests."""
        try:
            # Delete all terminal_commands rows created by tests
            cls.db.query(TerminalCommandModel).delete()
            cls.db.commit()
        except Exception:
            pass
        finally:
            cls.db.close()
            # Dispose the cached engine to release file handles before deleting the DB file
            from backend.database.session import get_engine, _engine_cache
            eng = get_engine()
            eng.dispose()
            _engine_cache.clear()
            # Remove test database file
            test_db_path = "./test_forge.db"
            if os.path.exists(test_db_path):
                try:
                    os.remove(test_db_path)
                except Exception:
                    pass
            # Also clean up WAL/SHM files
            for suffix in ["-shm", "-wal"]:
                f = test_db_path + suffix
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass

    def setUp(self):
        """Track row count before each test."""
        self.initial_count = self.db.query(TerminalCommandModel).count()

    def tearDown(self):
        """Rollback any uncommitted changes."""
        self.db.rollback()

    def _delete_test_rows(self):
        """Helper to delete rows created during a test."""
        current_count = self.db.query(TerminalCommandModel).count()
        if current_count > self.initial_count:
            # Delete the newest rows (test rows)
            rows = self.db.query(TerminalCommandModel).order_by(
                TerminalCommandModel.created_at.desc()
            ).limit(current_count - self.initial_count).all()
            for row in rows:
                self.db.delete(row)
            self.db.commit()

    # --- Test 1: POST /terminal/execute writes exactly one terminal_commands row ---
    def test_post_terminal_execute_writes_row(self):
        """POST /terminal/execute on a harmless local command writes exactly one terminal_commands row."""
        self._delete_test_rows()  # Clean slate

        # Execute a harmless command
        response = self.client.post(
            "/api/terminal/execute",
            json={"command": "echo hello", "challenge_id": "test-challenge-1"}
        )
        self.assertEqual(response.status_code, 200, f"Response: {response.text}")
        data = response.json()
        self.assertIn("command", data)
        self.assertEqual(data["command"], "echo hello")

        # Verify exactly one row was written
        rows = self.db.query(TerminalCommandModel).filter(
            TerminalCommandModel.challenge_id == "test-challenge-1"
        ).all()
        self.assertEqual(len(rows), 1, "Expected exactly one terminal_commands row")

        row = rows[0]
        self.assertEqual(row.command, "echo hello")
        self.assertEqual(row.challenge_id, "test-challenge-1")
        self.assertIsNotNone(row.exit_code)
        self.assertGreaterEqual(row.duration_ms, 0)
        self.assertIsNotNone(row.created_at)

        self._delete_test_rows()

    # --- Test 2: GET /terminal/history returns row in ascending created_at order ---
    def test_get_terminal_history_returns_row_ascending(self):
        """GET /terminal/history returns the row in ascending created_at order."""
        self._delete_test_rows()

        # Execute multiple commands to test ordering
        for i in range(3):
            self.client.post(
                "/api/terminal/execute",
                json={"command": f"echo test{i}", "challenge_id": "test-challenge-2"}
            )

        # Fetch history
        response = self.client.get("/api/terminal/history?challenge_id=test-challenge-2&limit=10")
        self.assertEqual(response.status_code, 200, f"Response: {response.text}")
        history = response.json()

        self.assertEqual(len(history), 3, "Expected 3 history entries")

        # Verify ascending order by created_at
        for i in range(len(history) - 1):
            current_time = history[i]["created_at"]
            next_time = history[i + 1]["created_at"]
            self.assertLessEqual(current_time, next_time,
                f"History not in ascending order: {current_time} > {next_time}")

        # Verify commands are in correct order
        self.assertEqual(history[0]["command"], "echo test0")
        self.assertEqual(history[1]["command"], "echo test1")
        self.assertEqual(history[2]["command"], "echo test2")

        self._delete_test_rows()

    # --- Test 3: GET /terminal/history?challenge_id=... filters correctly ---
    def test_get_terminal_history_filters_by_challenge_id(self):
        """GET /terminal/history?challenge_id=... filters correctly."""
        self._delete_test_rows()

        # Execute commands for two different challenges
        self.client.post("/api/terminal/execute", json={"command": "echo challenge-a", "challenge_id": "challenge-a"})
        self.client.post("/api/terminal/execute", json={"command": "echo challenge-b", "challenge_id": "challenge-b"})
        self.client.post("/api/terminal/execute", json={"command": "echo challenge-a-2", "challenge_id": "challenge-a"})

        # Fetch history for challenge-a only
        response = self.client.get("/api/terminal/history?challenge_id=challenge-a&limit=10")
        self.assertEqual(response.status_code, 200)
        history_a = response.json()
        self.assertEqual(len(history_a), 2, "Expected 2 entries for challenge-a")
        for entry in history_a:
            self.assertEqual(entry["challenge_id"], "challenge-a")

        # Fetch history for challenge-b only
        response = self.client.get("/api/terminal/history?challenge_id=challenge-b&limit=10")
        self.assertEqual(response.status_code, 200)
        history_b = response.json()
        self.assertEqual(len(history_b), 1, "Expected 1 entry for challenge-b")
        self.assertEqual(history_b[0]["challenge_id"], "challenge-b")
        self.assertEqual(history_b[0]["command"], "echo challenge-b")

        # Fetch history without filter (should get all)
        response = self.client.get("/api/terminal/history?limit=10")
        self.assertEqual(response.status_code, 200)
        history_all = response.json()
        # Scope to rows this test created to avoid cross-suite pollution
        history_all = [
            e for e in history_all if e["challenge_id"] in ("challenge-a", "challenge-b")
        ]
        self.assertEqual(len(history_all), 3, "Expected 3 entries across both challenges")

        self._delete_test_rows()

    # --- Test 4: DB write failure during persistence does not prevent command response ---
    def test_db_write_failure_does_not_break_execution(self):
        """A DB write failure during persistence does not prevent the command response."""
        self._delete_test_rows()

        # Patch the database session to raise an exception on commit
        with patch("backend.api.routes.execution.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.commit.side_effect = Exception("Simulated DB failure")
            mock_db.close = MagicMock()
            mock_get_db.return_value = iter([mock_db])

            response = self.client.post(
                "/api/terminal/execute",
                json={"command": "echo test-db-failure", "challenge_id": "test-challenge-db-fail"}
            )
            # Command should still succeed despite DB failure
            self.assertEqual(response.status_code, 200, f"Response: {response.text}")
            data = response.json()
            self.assertEqual(data["command"], "echo test-db-failure")

        self._delete_test_rows()

    # --- Test 5: Existing list_tool_executions behaviour is unchanged ---
    def test_list_tool_executions_unchanged(self):
        """Existing list_tool_executions behaviour is unchanged."""
        # This test verifies that the tool executions endpoint still works
        # and returns the expected field structure
        response = self.client.get("/api/tools/executions?limit=10")
        self.assertEqual(response.status_code, 200, f"Response: {response.text}")
        executions = response.json()

        # Should return a list (possibly empty)
        self.assertIsInstance(executions, list)

        # If there are entries, verify field structure matches expectation
        if executions:
            entry = executions[0]
            expected_fields = [
                "id", "run_id", "challenge_id", "agent", "tool_name",
                "capability", "command", "privilege_level", "approved",
                "status", "stdout", "stderr", "exit_code", "duration_ms", "created_at"
            ]
            for field in expected_fields:
                self.assertIn(field, entry, f"Missing field: {field}")

    # --- Additional: Verify field names match between history and tool executions ---
    def test_history_field_names_match_tool_executions(self):
        """Terminal history returns same field names as list_tool_executions for shared renderer."""
        self._delete_test_rows()

        # Execute a command to create history
        self.client.post(
            "/api/terminal/execute",
            json={"command": "echo field-test", "challenge_id": "test-fields"}
        )

        # Get terminal history
        response = self.client.get("/api/terminal/history?challenge_id=test-fields&limit=10")
        self.assertEqual(response.status_code, 200)
        history = response.json()
        self.assertEqual(len(history), 1)

        history_entry = history[0]

        # Get tool executions (for field comparison)
        response = self.client.get("/api/tools/executions?limit=1")
        self.assertEqual(response.status_code, 200)
        tool_executions = response.json()

        # Verify terminal history has the same fields as tool executions
        if tool_executions:
            tool_entry = tool_executions[0]
            for field in tool_entry.keys():
                self.assertIn(field, history_entry,
                    f"Terminal history missing field '{field}' that tool_executions has")

        # Verify specific fields that should be present in history
        required_fields = [
            "id", "run_id", "challenge_id", "agent", "tool_name",
            "capability", "command", "privilege_level", "approved",
            "status", "stdout", "stderr", "exit_code", "duration_ms", "created_at"
        ]
        for field in required_fields:
            self.assertIn(field, history_entry, f"Missing required field: {field}")

        # Verify terminal-specific values
        self.assertEqual(history_entry["agent"], "operator")
        self.assertEqual(history_entry["tool_name"], "terminal")
        self.assertEqual(history_entry["capability"], "terminal_command")
        self.assertEqual(history_entry["privilege_level"], "SAFE")
        self.assertEqual(history_entry["approved"], True)
        self.assertIsNone(history_entry["run_id"])

        self._delete_test_rows()

    # --- Additional: Test limit clamping ---
    def test_history_limit_clamping(self):
        """GET /terminal/history clamps limit to 1-500."""
        self._delete_test_rows()

        # Create a few entries
        for i in range(5):
            self.client.post("/api/terminal/execute", json={"command": f"echo limit{i}", "challenge_id": "test-limit"})

        # Test limit=0 clamps to 1
        response = self.client.get("/api/terminal/history?challenge_id=test-limit&limit=0")
        self.assertEqual(response.status_code, 200)
        history = response.json()
        self.assertEqual(len(history), 1)

        # Test limit=1000 clamps to 500
        response = self.client.get("/api/terminal/history?challenge_id=test-limit&limit=1000")
        self.assertEqual(response.status_code, 200)
        history = response.json()
        self.assertLessEqual(len(history), 5)  # Only 5 entries exist

        # Test default limit (200)
        response = self.client.get("/api/terminal/history?challenge_id=test-limit")
        self.assertEqual(response.status_code, 200)
        history = response.json()
        self.assertEqual(len(history), 5)

        self._delete_test_rows()


if __name__ == "__main__":
    unittest.main()