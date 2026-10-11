"""Focused tests for GeminiProvider image input.

Verifies that explicit (image_data, image_mime_type) pairs are turned into Gemini
``inline_data`` parts *inside* the provider, that text-only behaviour is unchanged,
and that invalid / oversized inputs are rejected before any HTTP call.

All HTTP is mocked — these tests make no live API calls and handle no secrets.
"""

import asyncio
import base64
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.providers.real_providers import GeminiProvider


GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "A red square."}]}}]}


def _run(coro):
    return asyncio.run(coro)


def _parts_from(mock_post):
    """Return contents[0].parts from the payload passed to _post_json."""
    payload = mock_post.call_args[0][2]
    return payload["contents"][0]["parts"]


class GeminiProviderImageTestBase(unittest.TestCase):
    def setUp(self):
        self.provider = GeminiProvider(api_key="test-key", api_keys=["test-key"])

    def _mock_post(self):
        mock_post = AsyncMock(return_value=(GEMINI_OK, {}))
        return patch.object(self.provider, "_post_json", mock_post), mock_post


class TestGeminiImagePayloadConstruction(GeminiProviderImageTestBase):
    def test_raw_bytes_become_inline_data_with_matching_base64(self):
        raw = b"\x89PNG\r\n\x1a\nfake-png-bytes"
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response(
                "Describe this image.", image_data=raw, image_mime_type="image/png"
            ))

        self.assertFalse(res.is_refusal)
        self.assertEqual(res.content, "A red square.")
        mock_post.assert_awaited_once()

        payload = mock_post.call_args[0][2]
        parts = payload["contents"][0]["parts"]
        self.assertEqual(parts[0], {"text": "Describe this image."})
        self.assertEqual(parts[1], {
            "inline_data": {
                "mime_type": "image/png",
                "data": base64.b64encode(raw).decode("ascii"),
            }
        })
        # Safety settings from the text-only path are preserved verbatim.
        self.assertEqual(payload["safetySettings"], GeminiProvider.SAFETY_SETTINGS)

    def test_base64_string_is_passed_through_unchanged(self):
        raw = b"jpeg-bytes"
        b64 = base64.b64encode(raw).decode("ascii")
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response(
                "What is this?", image_data=b64, image_mime_type="image/jpeg"
            ))

        self.assertFalse(res.is_refusal)
        parts = _parts_from(mock_post)
        self.assertEqual(parts[1]["inline_data"]["data"], b64)
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/jpeg")

    def test_mime_type_is_normalized(self):
        patcher, mock_post = self._mock_post()
        with patcher:
            _run(self.provider.generate_response(
                "x", image_data=b"abc", image_mime_type="  IMAGE/WEBP  "
            ))
        parts = _parts_from(mock_post)
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/webp")

    def test_image_only_request_has_no_text_part(self):
        patcher, mock_post = self._mock_post()
        with patcher:
            _run(self.provider.generate_response(
                "", image_data=b"abc", image_mime_type="image/png"
            ))
        parts = _parts_from(mock_post)
        self.assertEqual(len(parts), 1)
        self.assertIn("inline_data", parts[0])

    def test_system_instruction_preserved_with_image(self):
        patcher, mock_post = self._mock_post()
        with patcher:
            _run(self.provider.generate_response(
                "x", system_instruction="Be concise.",
                image_data=b"abc", image_mime_type="image/png",
            ))
        payload = mock_post.call_args[0][2]
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "Be concise."}]})

    def test_request_url_and_content_type_preserved(self):
        patcher, mock_post = self._mock_post()
        with patcher:
            _run(self.provider.generate_response(
                "x", model="gemini-1.5-pro",
                image_data=b"abc", image_mime_type="image/png",
            ))
        url = mock_post.call_args[0][0]
        headers = mock_post.call_args[0][1]
        self.assertIn("/gemini-1.5-pro:generateContent?key=test-key", url)
        self.assertEqual(headers["Content-Type"], "application/json")


class TestGeminiTextOnlyBackwardCompatibility(GeminiProviderImageTestBase):
    def test_text_only_payload_is_byte_for_byte_unchanged(self):
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response("Hello world"))

        self.assertFalse(res.is_refusal)
        payload = mock_post.call_args[0][2]
        self.assertEqual(payload["contents"], [{"parts": [{"text": "Hello world"}]}])
        self.assertEqual(payload["safetySettings"], GeminiProvider.SAFETY_SETTINGS)
        self.assertNotIn("inline_data", str(payload))

    def test_text_only_empty_prompt_still_sends_text_part(self):
        patcher, mock_post = self._mock_post()
        with patcher:
            _run(self.provider.generate_response(""))
        payload = mock_post.call_args[0][2]
        self.assertEqual(payload["contents"], [{"parts": [{"text": ""}]}])

    def test_unrelated_kwargs_do_not_trigger_image_path(self):
        patcher, mock_post = self._mock_post()
        with patcher:
            _run(self.provider.generate_response("Hi", capability="code_analysis", max_tokens=10))
        payload = mock_post.call_args[0][2]
        self.assertEqual(payload["contents"], [{"parts": [{"text": "Hi"}]}])


class TestGeminiInvalidImageInput(GeminiProviderImageTestBase):
    def _assert_rejected(self, reason_contains, **kwargs):
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response("describe", **kwargs))
        self.assertTrue(res.is_refusal, f"expected refusal, got content={res.content!r}")
        self.assertIn(reason_contains, res.refusal_reason)
        mock_post.assert_not_awaited()

    def test_unsupported_mime_type_rejected(self):
        self._assert_rejected(
            "Unsupported image MIME type",
            image_data=b"abc", image_mime_type="image/tiff",
        )

    def test_non_image_mime_type_rejected(self):
        self._assert_rejected(
            "Unsupported image MIME type",
            image_data=b"abc", image_mime_type="application/pdf",
        )

    def test_image_data_without_mime_type_rejected(self):
        self._assert_rejected(
            "must be provided together",
            image_data=b"abc",
        )

    def test_mime_type_without_image_data_rejected(self):
        self._assert_rejected(
            "must be provided together",
            image_mime_type="image/png",
        )

    def test_invalid_base64_string_rejected(self):
        self._assert_rejected(
            "not valid base64",
            image_data="!!!not-base64!!!", image_mime_type="image/png",
        )

    def test_empty_image_data_rejected(self):
        self._assert_rejected(
            "empty",
            image_data=b"", image_mime_type="image/png",
        )

    def test_wrong_data_type_rejected(self):
        self._assert_rejected(
            "must be bytes or a base64 string",
            image_data=12345, image_mime_type="image/png",
        )

    def test_missing_mime_type_not_a_string_rejected(self):
        self._assert_rejected(
            "Unsupported image MIME type",
            image_data=b"abc", image_mime_type=123,
        )


class TestGeminiEncodedImageSizeLimit(GeminiProviderImageTestBase):
    """MAX_IMAGE_BYTES is documented as the ceiling on the *encoded* inline_data payload,
    so the limit must be measured against the base64 string that ships in the request."""

    def test_encoded_size_exactly_at_limit_accepted_from_bytes(self):
        # 12 raw bytes -> exactly 16 base64 chars, the configured ceiling.
        self.provider.MAX_IMAGE_BYTES = 16
        raw = b"\x00" * 12
        encoded = base64.b64encode(raw).decode("ascii")
        self.assertEqual(len(encoded), self.provider.MAX_IMAGE_BYTES)
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response(
                "describe", image_data=raw, image_mime_type="image/png"
            ))
        self.assertFalse(res.is_refusal)
        parts = _parts_from(mock_post)
        self.assertEqual(parts[1]["inline_data"]["data"], encoded)

    def test_encoded_size_exactly_at_limit_accepted_from_base64_string(self):
        self.provider.MAX_IMAGE_BYTES = 16
        encoded = base64.b64encode(b"\x00" * 12).decode("ascii")
        self.assertEqual(len(encoded), self.provider.MAX_IMAGE_BYTES)
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response(
                "describe", image_data=encoded, image_mime_type="image/png"
            ))
        self.assertFalse(res.is_refusal)
        parts = _parts_from(mock_post)
        self.assertEqual(parts[1]["inline_data"]["data"], encoded)

    def test_encoded_exceeding_limit_rejected_from_bytes_when_decoded_is_below(self):
        self.provider.MAX_IMAGE_BYTES = 16
        raw = b"\x00" * 13  # decoded 13 < 16, but encoded is 20 > 16
        self.assertLess(len(raw), self.provider.MAX_IMAGE_BYTES)
        self.assertGreater(len(base64.b64encode(raw)), self.provider.MAX_IMAGE_BYTES)
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response(
                "describe", image_data=raw, image_mime_type="image/png"
            ))
        self.assertTrue(res.is_refusal)
        self.assertIn("inline limit", res.refusal_reason)
        mock_post.assert_not_awaited()

    def test_encoded_exceeding_limit_rejected_from_base64_string_when_decoded_is_below(self):
        self.provider.MAX_IMAGE_BYTES = 16
        encoded = base64.b64encode(b"\x00" * 13).decode("ascii")  # 20 chars
        self.assertLess(len(base64.b64decode(encoded)), self.provider.MAX_IMAGE_BYTES)
        self.assertGreater(len(encoded), self.provider.MAX_IMAGE_BYTES)
        patcher, mock_post = self._mock_post()
        with patcher:
            res = _run(self.provider.generate_response(
                "describe", image_data=encoded, image_mime_type="image/png"
            ))
        self.assertTrue(res.is_refusal)
        self.assertIn("inline limit", res.refusal_reason)
        mock_post.assert_not_awaited()


class TestGeminiKeyRotationPreservedWithImage(GeminiProviderImageTestBase):
    def test_rotates_on_429_and_retries_same_image_payload(self):
        provider = GeminiProvider(api_key="", api_keys=["key-one", "key-two"])
        mock_post = AsyncMock(side_effect=[
            RuntimeError("HTTP 429 Rate Limit for gemini"),
            (GEMINI_OK, {}),
        ])
        with patch.object(provider, "_post_json", mock_post):
            res = _run(provider.generate_response(
                "describe", image_data=b"abc", image_mime_type="image/png"
            ))

        self.assertFalse(res.is_refusal)
        self.assertEqual(mock_post.await_count, 2)
        self.assertIn("key=key-one", mock_post.call_args_list[0][0][0])
        self.assertIn("key=key-two", mock_post.call_args_list[1][0][0])
        # The retried request carries the same inline_data part.
        retried_parts = mock_post.call_args_list[1][0][2]["contents"][0]["parts"]
        self.assertEqual(retried_parts[1]["inline_data"]["mime_type"], "image/png")


if __name__ == "__main__":
    unittest.main()
