"""Unit tests for local vs network execution-failure classification (Part 4 #2).

A command that never reached the target (unquoted path with a space -> [Errno 2],
Python SyntaxError, permission denied, missing dependency) must be flagged as an
execution failure in a LOCAL category, so the swarm aborts after a couple of hits
instead of re-running the same broken invocation against a rate-limited provider chain.
"""

import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.tools.manager import classify_tool_execution, LOCAL_EXEC_CATEGORIES


class TestLocalExecutionFailures(unittest.TestCase):
    def test_errno2_no_such_file_is_file_not_found(self):
        # The exact production signature: `python solve_agent_1.py` split on a space in
        # ".../WEB/EASY/Old Sessions" -> python can't open "solve_agent_1.py".
        r = classify_tool_execution("python", 2,
                                    "", "python: can't open file 'Sessions/solve.py': [Errno 2] No such file or directory")
        self.assertTrue(r["execution_failure"])
        self.assertEqual(r["failure_category"], "FILE_NOT_FOUND")
        self.assertIn("FILE_NOT_FOUND", LOCAL_EXEC_CATEGORIES)

    def test_python_syntax_error(self):
        r = classify_tool_execution("python", 1, "", "  File \"solve.py\", line 3\n    SyntaxError: invalid syntax")
        self.assertTrue(r["execution_failure"])
        self.assertEqual(r["failure_category"], "SYNTAX_ERROR")

    def test_permission_denied(self):
        r = classify_tool_execution("bash", 126, "", "bash: ./run.sh: Permission denied")
        self.assertTrue(r["execution_failure"])
        self.assertEqual(r["failure_category"], "PERMISSION_DENIED")

    def test_missing_dependency(self):
        r = classify_tool_execution("python", 1, "", "ModuleNotFoundError: No module named 'pwn'")
        self.assertTrue(r["execution_failure"])
        self.assertEqual(r["failure_category"], "MISSING_DEP")


class TestNetworkFailuresStillWork(unittest.TestCase):
    def test_dns_error_preserved(self):
        r = classify_tool_execution("curl", 6, "", "curl: (6) Could not resolve host: bad.ctf")
        self.assertTrue(r["execution_failure"])
        self.assertEqual(r["failure_category"], "DNS_ERROR")

    def test_connection_refused_preserved(self):
        r = classify_tool_execution("curl", 7, "", "curl: (7) Failed to connect")
        self.assertTrue(r["execution_failure"])
        self.assertEqual(r["failure_category"], "CONNECTION_REFUSED")


class TestTargetResponsesNotFlagged(unittest.TestCase):
    def test_normal_200_is_not_a_failure(self):
        r = classify_tool_execution("curl", 0, "HTTP/1.1 200 OK\n{\"success\":false}", "")
        self.assertFalse(r["execution_failure"])
        self.assertIsNone(r["failure_category"])

    def test_target_saying_user_not_found_is_not_local_failure(self):
        # A real target response (even a rejection) must NOT be a local exec failure —
        # it reached the target and is a legitimate signal for the agent to reason about.
        r = classify_tool_execution("curl", 0, '{"error":"User not found."}', "")
        self.assertFalse(r["execution_failure"])


if __name__ == "__main__":
    unittest.main()
