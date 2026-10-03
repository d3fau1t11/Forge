"""Tests for challenge_intake module (Workstream F4/F5).

Uses unittest (not pytest). No live network — stubs the router.
"""
import os
import sys
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.agents.challenge_intake import (
    INTAKE_SYSTEM_INSTRUCTION,
    _parse_model_json,
    build_intake_prompt,
    interpret_operator_message,
)


class TestBuildIntakePrompt(unittest.TestCase):
    """Tests for build_intake_prompt function."""

    def test_includes_transcript_and_missing_fields(self):
        transcript = [
            {"role": "user", "content": "Create a web challenge"},
            {"role": "assistant", "content": "What platform?"},
            {"role": "user", "content": "HackTheBox"},
        ]
        fields = {
            "name": "SQLi Login",
            "platform": "HackTheBox",
            "category": None,
            "difficulty": None,
            "target_address": None,
            "description": None,
        }
        prompt = build_intake_prompt(transcript, fields)

        # Should include transcript
        self.assertIn("Create a web challenge", prompt)
        self.assertIn("HackTheBox", prompt)
        # Should list missing fields
        self.assertIn("category", prompt)
        self.assertIn("difficulty", prompt)
        self.assertIn("target_address", prompt)
        self.assertIn("description", prompt)
        # Should not list present fields as missing
        self.assertNotIn("name", prompt.split("Currently missing fields:")[1].split("\n")[0])
        self.assertNotIn("platform", prompt.split("Currently missing fields:")[1].split("\n")[0])

    def test_empty_transcript(self):
        transcript = []
        fields = {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]}
        prompt = build_intake_prompt(transcript, fields)
        self.assertIn("Conversation so far:", prompt)
        self.assertIn("Currently missing fields:", prompt)


class TestInterpretOperatorMessage(unittest.IsolatedAsyncioTestCase):
    """Tests for interpret_operator_message function."""

    def setUp(self):
        # Import here to avoid circular imports during patching
        from backend.providers.base import ProviderResponse
        self.ProviderResponse = ProviderResponse

    def _make_response(self, content: str, is_refusal: bool = False) -> MagicMock:
        """Create a mock ProviderResponse."""
        resp = MagicMock(spec=self.ProviderResponse)
        resp.content = content
        resp.is_refusal = is_refusal
        resp.refusal_reason = "refused" if is_refusal else None
        return resp

    async def test_returns_none_for_none_response(self):
        """interpret_operator_message returns None when router returns None."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            mock_route.return_value = None
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_returns_none_for_is_refusal_true(self):
        """Returns None when response.is_refusal is True."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            mock_route.return_value = self._make_response("{}", is_refusal=True)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_returns_none_for_non_json_prose(self):
        """Returns None for non-JSON prose (simulating Gemini prose refusal)."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            mock_route.return_value = self._make_response("I cannot help with that request.")
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_returns_none_for_json_missing_reply(self):
        """Returns None when JSON is missing 'reply' field."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            content = json.dumps({"fields": {}, "ready_to_create": False})
            mock_route.return_value = self._make_response(content)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_returns_none_for_fields_with_unknown_key(self):
        """Returns None when fields contains a key outside the six expected."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            content = json.dumps({
                "reply": "OK",
                "fields": {"name": "test", "unknown_field": "value"},
                "ready_to_create": False
            })
            mock_route.return_value = self._make_response(content)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_returns_none_for_ready_to_create_as_string(self):
        """Returns None when ready_to_create is a string instead of bool."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            content = json.dumps({
                "reply": "OK",
                "fields": {},
                "ready_to_create": "true"
            })
            mock_route.return_value = self._make_response(content)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_returns_none_for_numeric_field_value(self):
        """Returns None when a field value is numeric instead of string/None."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            content = json.dumps({
                "reply": "OK",
                "fields": {"name": 123},
                "ready_to_create": False
            })
            mock_route.return_value = self._make_response(content)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_valid_json_returns_normalized_dict(self):
        """Valid JSON returns normalized dict with canonicalized category/difficulty."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            content = json.dumps({
                "reply": "Great, creating challenge",
                "fields": {
                    "name": "SQL Injection",
                    "platform": "HackTheBox",
                    "category": "web exploitation",  # synonym -> "web"
                    "difficulty": "hard",  # lowercase -> "HARD"
                    "target_address": "http://example.com\nhttp://test.com",
                    "description": "A SQLi challenge"
                },
                "ready_to_create": True
            })
            mock_route.return_value = self._make_response(content)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})

            self.assertIsNotNone(result)
            self.assertEqual(result["reply"], "Great, creating challenge")
            self.assertEqual(result["fields"]["category"], "web")
            self.assertEqual(result["fields"]["difficulty"], "HARD")
            self.assertEqual(result["fields"]["target_address"], "http://example.com + http://test.com")
            self.assertTrue(result["ready_to_create"])

    async def test_handles_markdown_fences(self):
        """Strips optional ```json fences from response."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            content = """```json
{
  "reply": "OK",
  "fields": {"name": "Test", "platform": "HTB", "category": "pwn", "difficulty": "easy", "target_address": "nc host 1337", "description": "desc"},
  "ready_to_create": true
}
```"""
            mock_route.return_value = self._make_response(content)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})

            self.assertIsNotNone(result)
            self.assertEqual(result["fields"]["category"], "pwn")
            self.assertEqual(result["fields"]["difficulty"], "EASY")

    async def test_empty_string_field_becomes_none(self):
        """Empty string field values become None after normalization."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            content = json.dumps({
                "reply": "OK",
                "fields": {
                    "name": "Test",
                    "platform": "",
                    "category": "web",
                    "difficulty": "medium",
                    "target_address": "",
                    "description": "desc"
                },
                "ready_to_create": False
            })
            mock_route.return_value = self._make_response(content)
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})

            self.assertIsNotNone(result)
            self.assertIsNone(result["fields"]["platform"])
            self.assertIsNone(result["fields"]["target_address"])

    async def test_exception_in_router_returns_none(self):
        """Any exception from router returns None (never raises)."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            mock_route.side_effect = Exception("Network error")
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)

    async def test_json_parse_exception_returns_none(self):
        """JSON parse exception returns None (never raises)."""
        with patch("backend.agents.challenge_intake.model_router.route_request", new_callable=AsyncMock) as mock_route:
            mock_route.return_value = self._make_response("not valid json {")
            result = await interpret_operator_message([], {k: None for k in ["name", "platform", "category", "difficulty", "target_address", "description"]})
            self.assertIsNone(result)


import json  # needed for test helpers


class TestParseModelJson(unittest.TestCase):
    """Guards the _parse_model_json refactor: same rejections as the old inline logic."""

    def test_rejects_non_json_prose(self):
        self.assertIsNone(_parse_model_json("I cannot help with that request."))

    def test_rejects_missing_reply(self):
        self.assertIsNone(_parse_model_json(json.dumps({"fields": {}, "ready_to_create": False})))

    def test_rejects_unknown_fields_key(self):
        self.assertIsNone(_parse_model_json(json.dumps({
            "reply": "OK",
            "fields": {"name": "test", "unknown_field": "value"},
            "ready_to_create": False,
        })))

    def test_rejects_non_bool_ready_to_create(self):
        self.assertIsNone(_parse_model_json(json.dumps({
            "reply": "OK",
            "fields": {},
            "ready_to_create": "true",
        })))

    def test_rejects_numeric_field_value(self):
        self.assertIsNone(_parse_model_json(json.dumps({
            "reply": "OK",
            "fields": {"name": 123},
            "ready_to_create": False,
        })))

    def test_accepts_and_normalizes_valid_json(self):
        parsed = _parse_model_json(json.dumps({
            "reply": "Great, creating challenge",
            "fields": {
                "name": "SQL Injection",
                "platform": "",
                "category": "web exploitation",
                "difficulty": "hard",
                "target_address": "http://example.com\nhttp://test.com",
                "description": "A SQLi challenge",
            },
            "ready_to_create": True,
        }))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["reply"], "Great, creating challenge")
        self.assertEqual(parsed["fields"]["category"], "web")
        self.assertEqual(parsed["fields"]["difficulty"], "HARD")
        self.assertIsNone(parsed["fields"]["platform"])
        self.assertEqual(parsed["fields"]["target_address"], "http://example.com + http://test.com")
        self.assertTrue(parsed["ready_to_create"])

    def test_strips_markdown_fences(self):
        parsed = _parse_model_json(
            "```json\n"
            '{"reply": "OK", "fields": {"name": "Test"}, "ready_to_create": false}\n'
            "```"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["reply"], "OK")
        self.assertEqual(parsed["fields"]["name"], "Test")

    def test_never_raises(self):
        # A non-dict top level, malformed JSON, and empty input all return None.
        self.assertIsNone(_parse_model_json("[1, 2, 3]"))
        self.assertIsNone(_parse_model_json("not valid json {"))
        self.assertIsNone(_parse_model_json(""))


if __name__ == "__main__":
    unittest.main()