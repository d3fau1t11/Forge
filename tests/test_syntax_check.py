import sys
import os
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

sys.path.insert(0, "backend")
from tools.manager import classify_tool_execution

class TestPythonSyntaxErrors:
    def test_python_syntax_error_invalid_syntax(self):
        """Test that SyntaxError: invalid syntax is classified as SYNTAX_ERROR."""
        r = classify_tool_execution("python", 1, "", "  File \"solve.py\", line 3\n    SyntaxError: invalid syntax")
        assert r["execution_failure"] == True, "Should flag as execution failure"
        assert r["failure_category"] == "SYNTAX_ERROR", f"Expected SYNTAX_ERROR, got {r['failure_category']}"

    def test_python_syntax_error_syntaxerror(self):
        """Test that SyntaxError: syntaxerror is classified as SYNTAX_ERROR."""
        r = classify_tool_execution("python", 1, "", "SyntaxError: invalid syntax")
        assert r["execution_failure"] == True, "Should flag as execution failure"
        assert r["failure_category"] == "SYNTAX_ERROR", f"Expected SYNTAX_ERROR, got {r['failure_category']}"

    def test_python_syntax_error_unterminated_string(self):
        """Test that SyntaxError:unterminated string is classified as SYNTAX_ERROR."""
        r = classify_tool_execution("python", 1, "", "  File \"solve.py\", line 3\n    SyntaxError: unterminated string")
        assert r["execution_failure"] == True, "Should flag as execution failure"
        assert r["failure_category"] == "SYNTAX_ERROR", f"Expected SYNTAX_ERROR, got {r['failure_category']}"

    def test_python_syntax_error_unexpected_eof(self):
        """Test that SyntaxError:unexpected eof is classified as SYNTAX_ERROR."""
        r = classify_tool_execution("python", 1, "", "  File \"solve.py\", line 3\n    SyntaxError: unexpected eof while parsing")
        assert r["execution_failure"] == True, "Should flag as execution failure"
        assert r["failure_category"] == "SYNTAX_ERROR", f"Expected SYNTAX_ERROR, got {r['failure_category']}"

    def test_python_syntax_error_unexpected_token(self):
        """Test that SyntaxError:unexpected token is classified as SYNTAX_ERROR."""
        r = classify_tool_execution("python", 1, "", "  File \"solve.py\", line 3\n    SyntaxError: unexpected token")
        assert r["execution_failure"] == True, "Should flag as execution failure"
        assert r["failure_category"] == "SYNTAX_ERROR", f"Expected SYNTAX_ERROR, got {r['failure_category']}"

if __name__ == "__main__":
    import unittest
    unittest.main()