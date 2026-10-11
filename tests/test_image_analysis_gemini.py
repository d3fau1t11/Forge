"""Focused regression tests for the image-artifact → Gemini vision integration.

Covers the missing bridge between Forge's reconstructed image artifacts and the
committed Gemini image-input capability: bytes + real MIME reach Gemini, the
challenge-specific prompt accompanies the image, the description is recorded as
ordinary evidence (never a flag / never a solve), failures are safe, and the same
image is not re-sent on every agent iteration.

All provider/API calls are mocked — no live API calls.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.agents.artifact_classifier import ClassificationResult
from backend.agents.image_analysis import (
    GEMINI_VISION_MODEL,
    analyze_image_artifact,
    image_fingerprint,
    image_mime_type_for,
    is_image_artifact,
)
from backend.agents.swarm_orchestrator import SwarmBlackboard, SwarmOrchestrator
from backend.providers.base import ProviderResponse

_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 48      # JPEG SOI/APP0 magic + filler


def _run(coro):
    return asyncio.run(coro)


def _gemini_ok(content="The image shows binary digits: 01100110."):
    return ProviderResponse(
        provider_name="gemini", model_name=GEMINI_VISION_MODEL, content=content)


class _TempImage:
    """Context manager creating a real on-disk image artifact."""

    def __init__(self, name="binary_digits.jpg", data=_JPEG_BYTES):
        self.name = name
        self.data = data

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, self.name)
        with open(self.path, "wb") as fh:
            fh.write(self.data)
        return self.path

    def __exit__(self, *exc):
        self._dir.cleanup()


def _classification(path, artifact_type="jpeg"):
    return ClassificationResult(
        is_binary=True, reason="test image", artifact_type=artifact_type,
        recommended_tools=["file", "exiftool"], safe_file_path=path)


class TestArtifactMimeMapping(unittest.TestCase):
    def test_artifact_labels_map_to_real_mime_types(self):
        self.assertEqual(image_mime_type_for("jpeg"), "image/jpeg")
        self.assertEqual(image_mime_type_for("jpg"), "image/jpeg")
        self.assertEqual(image_mime_type_for("png"), "image/png")
        self.assertEqual(image_mime_type_for("gif"), "image/gif")

    def test_extension_fallback_and_non_images(self):
        self.assertEqual(image_mime_type_for("", "/tmp/x.png"), "image/png")
        self.assertIsNone(image_mime_type_for("elf", "/tmp/x.bin"))
        self.assertIsNone(image_mime_type_for("text", "/tmp/notes.txt"))
        self.assertFalse(is_image_artifact("zip", "/tmp/a.zip"))
        self.assertTrue(is_image_artifact("jpeg", None))


class TestAnalyzeImageArtifact(unittest.TestCase):
    def test_valid_jpeg_reaches_gemini_with_bytes_and_mime(self):
        with _TempImage() as path:
            route = AsyncMock(return_value=_gemini_ok())
            res = _run(analyze_image_artifact(
                image_path=path, artifact_type="jpeg",
                challenge_name="Binary Digits", category="forensics",
                description="read the hidden digits", target="challenge.ctf",
                route_request=route))

        self.assertTrue(res.ok, res.reason)
        route.assert_awaited_once()
        kwargs = route.call_args.kwargs
        self.assertEqual(kwargs["image_mime_type"], "image/jpeg")   # not the bare label "jpeg"
        self.assertEqual(kwargs["image_data"], _JPEG_BYTES)         # actual bytes on disk
        self.assertEqual(kwargs["target_model"], GEMINI_VISION_MODEL)
        self.assertEqual(res.provider, "gemini")
        self.assertEqual(res.mime_type, "image/jpeg")

    def test_prompt_is_challenge_specific_and_accompanies_image(self):
        with _TempImage() as path:
            route = AsyncMock(return_value=_gemini_ok())
            _run(analyze_image_artifact(
                image_path=path, artifact_type="jpeg",
                challenge_name="Binary Digits", category="forensics",
                description="read the hidden digits", target="challenge.ctf",
                route_request=route))

        prompt = route.call_args.kwargs["prompt"]
        self.assertIn("Binary Digits", prompt)
        self.assertIn("read the hidden digits", prompt)
        self.assertIn("image", prompt.lower())
        # The model is never asked to confirm a flag.
        self.assertIn("unverified", prompt.lower())

    def test_leads_are_parsed_from_description(self):
        with _TempImage() as path:
            route = AsyncMock(return_value=_gemini_ok(
                "DESCRIPTION: digits.\nLEAD: try binary decode\nLEAD: check EXIF"))
            res = _run(analyze_image_artifact(
                image_path=path, artifact_type="jpeg", route_request=route))
        self.assertTrue(res.ok)
        self.assertEqual(res.leads, ["try binary decode", "check EXIF"])

    def test_unsupported_image_type_is_skipped_without_calling_provider(self):
        with _TempImage(name="scan.tiff") as path:
            route = AsyncMock(return_value=_gemini_ok())
            res = _run(analyze_image_artifact(
                image_path=path, artifact_type="tiff", route_request=route))
        self.assertEqual(res.status, "SKIPPED")
        self.assertEqual(res.mime_type, "image/tiff")
        route.assert_not_awaited()

    def test_non_image_artifact_is_skipped(self):
        route = AsyncMock(return_value=_gemini_ok())
        res = _run(analyze_image_artifact(
            image_path="/tmp/prog.bin", artifact_type="elf", route_request=route))
        self.assertEqual(res.status, "SKIPPED")
        route.assert_not_awaited()

    def test_missing_file_fails_safely(self):
        route = AsyncMock(return_value=_gemini_ok())
        res = _run(analyze_image_artifact(
            image_path="/no/such/image.jpg", artifact_type="jpeg", route_request=route))
        self.assertEqual(res.status, "FAILED")
        self.assertIn("not found", res.reason)
        route.assert_not_awaited()

    def test_provider_exception_fails_safely(self):
        with _TempImage() as path:
            route = AsyncMock(side_effect=RuntimeError("HTTP 500 Gemini down"))
            res = _run(analyze_image_artifact(
                image_path=path, artifact_type="jpeg", route_request=route))
        self.assertEqual(res.status, "FAILED")
        self.assertIn("Gemini request failed", res.reason)

    def test_refusal_fails_safely(self):
        with _TempImage() as path:
            route = AsyncMock(return_value=ProviderResponse(
                provider_name="gemini", model_name=GEMINI_VISION_MODEL, content="",
                is_refusal=True, refusal_reason="image rejected by safety"))
            res = _run(analyze_image_artifact(
                image_path=path, artifact_type="jpeg", route_request=route))
        self.assertEqual(res.status, "FAILED")
        self.assertIn("rejected by safety", res.reason)

    def test_non_gemini_provider_is_not_reported_as_success(self):
        # A text-only provider that silently ignored the image kwargs must NOT look
        # like a successful vision analysis.
        with _TempImage() as path:
            route = AsyncMock(return_value=ProviderResponse(
                provider_name="groq", model_name="qwen", content="I cannot see images."))
            res = _run(analyze_image_artifact(
                image_path=path, artifact_type="jpeg", route_request=route))
        self.assertEqual(res.status, "FAILED")
        self.assertIn("not fulfilled by Gemini", res.reason)


class TestOrchestratorImageIntegration(unittest.TestCase):
    def _board(self):
        board = SwarmBlackboard("chal_img", "run_img", "http://challenge.ctf")
        board.challenge_name = "Binary Digits"
        board.category = "forensics"
        board.description = "read the hidden digits"
        return board

    def test_description_recorded_as_evidence_not_a_flag(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            route = AsyncMock(return_value=_gemini_ok(
                "DESCRIPTION: the image shows digits 0110.\nLEAD: decode as binary"))
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                res = _run(SwarmOrchestrator()._analyze_image_artifact(board))

            self.assertTrue(res.ok)
            self.assertEqual(len(board.image_analysis_evidence), 1)
            ev = board.image_analysis_evidence[0]
            self.assertIn("0110", ev["description"])
            self.assertEqual(ev["leads"], ["decode as binary"])
            self.assertEqual(ev["mime_type"], "image/jpeg")

            # Evidence is surfaced into the shared prompt context.
            self.assertIn("Gemini visual analysis", board.build_history_context("agent_1"))
            # ...but it is NOT a flag and does not solve the challenge.
            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())

    def test_flag_like_text_becomes_unverified_candidate_only(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            route = AsyncMock(return_value=_gemini_ok(
                "DESCRIPTION: text reads picoCTF{v1sual_only_flag} in the image."))
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                _run(SwarmOrchestrator()._analyze_image_artifact(board))

            # Recorded as an unverified candidate, never captured / solved.
            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())
            self.assertIn("picoCTF{v1sual_only_flag}",
                          [c["flag"] for c in board.flag_candidates])

    def test_same_image_is_not_sent_repeatedly(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            route = AsyncMock(return_value=_gemini_ok())
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                orch = SwarmOrchestrator()
                for _ in range(3):     # simulated agent iterations
                    _run(orch._analyze_image_artifact(board))
            self.assertEqual(route.await_count, 1)

    def test_non_image_artifact_is_ignored(self):
        board = self._board()
        board.artifact_classification = ClassificationResult(
            is_binary=True, reason="elf", artifact_type="elf",
            safe_file_path="/tmp/prog")
        route = AsyncMock(return_value=_gemini_ok())
        with patch("backend.agents.image_analysis.model_router.route_request", route):
            res = _run(SwarmOrchestrator()._analyze_image_artifact(board))
        self.assertIsNone(res)
        route.assert_not_awaited()
        self.assertEqual(board.image_analysis_evidence, [])

    def test_provider_failure_records_no_evidence_but_is_safe(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            route = AsyncMock(side_effect=RuntimeError("Gemini unavailable"))
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                res = _run(SwarmOrchestrator()._analyze_image_artifact(board))
            self.assertEqual(res.status, "FAILED")
            self.assertEqual(board.image_analysis_evidence, [])
            self.assertIsNone(board.flag_captured)

    # ── Retry suppression regression (in-flight vs. successful fingerprints) ──

    def test_successful_analysis_is_not_repeated(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            route = AsyncMock(return_value=_gemini_ok())
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                orch = SwarmOrchestrator()
                first = _run(orch._analyze_image_artifact(board))
                second = _run(orch._analyze_image_artifact(board))
            self.assertTrue(first.ok)
            self.assertIsNone(second)                      # suppressed
            self.assertEqual(route.await_count, 1)         # provider hit exactly once
            self.assertEqual(len(board.analyzed_image_artifacts), 1)
            self.assertEqual(board.image_analysis_inflight, set())

    def test_failed_analysis_can_be_retried(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            refusal = ProviderResponse(
                provider_name="gemini", model_name=GEMINI_VISION_MODEL,
                content="", is_refusal=True, refusal_reason="transient provider error")
            route = AsyncMock(side_effect=[refusal, _gemini_ok("DESCRIPTION: digits 0110.")])
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                orch = SwarmOrchestrator()
                first = _run(orch._analyze_image_artifact(board))
                # The first (failed) attempt must NOT suppress a retry.
                self.assertEqual(first.status, "FAILED")
                self.assertNotIn(image_fingerprint(path), board.analyzed_image_artifacts)
                self.assertEqual(board.image_analysis_inflight, set())
                second = _run(orch._analyze_image_artifact(board))

            self.assertTrue(second.ok)
            self.assertEqual(route.await_count, 2)          # retry actually reached the provider
            self.assertEqual(len(board.image_analysis_evidence), 1)
            self.assertIn("digits 0110", board.image_analysis_evidence[0]["description"])

    def test_raised_exception_does_not_leave_fingerprint_inflight(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            fp = image_fingerprint(path)
            with patch("backend.agents.swarm_orchestrator.analyze_image_artifact",
                       new=AsyncMock(side_effect=RuntimeError("boom"))):
                with self.assertRaises(RuntimeError):
                    _run(SwarmOrchestrator()._analyze_image_artifact(board))
            # The `finally` released the claim; nothing is stuck and nothing is "successful".
            self.assertEqual(board.image_analysis_inflight, set())
            self.assertEqual(board.analyzed_image_artifacts, set())

            # The same image can still be analyzed after the transient error.
            route = AsyncMock(return_value=_gemini_ok())
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                res = _run(SwarmOrchestrator()._analyze_image_artifact(board))
            self.assertTrue(res.ok)
            self.assertIn(fp, board.analyzed_image_artifacts)
            self.assertEqual(route.await_count, 1)

    def test_overlapping_calls_do_not_duplicate_provider_request(self):
        calls = []

        async def slow_route(**kwargs):
            calls.append(1)
            await asyncio.sleep(0.05)      # force the two callers to overlap
            return _gemini_ok()

        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            with patch("backend.agents.image_analysis.model_router.route_request", new=slow_route):
                orch = SwarmOrchestrator()

                async def _both():
                    return await asyncio.gather(
                        orch._analyze_image_artifact(board),
                        orch._analyze_image_artifact(board),
                    )

                results = _run(_both())

            self.assertEqual(len(calls), 1)                 # one provider request, not two
            self.assertEqual(len([r for r in results if r is not None and r.ok]), 1)
            self.assertEqual(len([r for r in results if r is None]), 1)
            self.assertEqual(board.image_analysis_inflight, set())
            self.assertEqual(len(board.image_analysis_evidence), 1)

    def test_evidence_and_flag_verification_unchanged_by_suppression(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            route = AsyncMock(return_value=_gemini_ok(
                "DESCRIPTION: image reads picoCTF{r3tr1ed_flag}.\nLEAD: check EXIF"))
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                orch = SwarmOrchestrator()
                res = _run(orch._analyze_image_artifact(board))
                _run(orch._analyze_image_artifact(board))   # suppressed repeat

            self.assertTrue(res.ok)
            self.assertEqual(route.await_count, 1)
            # Evidence recorded exactly once and surfaced to agents.
            self.assertEqual(len(board.image_analysis_evidence), 1)
            self.assertEqual(board.image_analysis_evidence[0]["leads"], ["check EXIF"])
            self.assertIn("Gemini visual analysis", board.build_history_context("agent_1"))
            # Flag-like LLM prose remains an unverified candidate — never auto-captured.
            self.assertIn("picoCTF{r3tr1ed_flag}",
                          [c["flag"] for c in board.flag_candidates])
            self.assertIsNone(board.flag_captured)
            self.assertFalse(board.flag_event.is_set())


    def test_recording_failure_does_not_mark_image_successful(self):
        with _TempImage() as path:
            board = self._board()
            board.artifact_classification = _classification(path)
            fp = image_fingerprint(path)
            route = AsyncMock(return_value=_gemini_ok("DESCRIPTION: digits 0110."))
            with patch("backend.agents.image_analysis.model_router.route_request", route):
                # Evidence recording fails after result.ok is confirmed.
                with patch.object(board, "record_agent_step",
                                  side_effect=RuntimeError("transcript store write failed")):
                    with self.assertRaises(RuntimeError):
                        _run(SwarmOrchestrator()._analyze_image_artifact(board))

            # The success mark is applied only AFTER evidence recording, so a recording
            # failure can never leave the image "successful" with no evidence: it stays
            # unmarked (and therefore retryable), and the in-flight claim is released.
            self.assertNotIn(fp, board.analyzed_image_artifacts)
            self.assertEqual(board.image_analysis_inflight, set())

            # A retry (recording healthy) completes and only then marks the image.
            route2 = AsyncMock(return_value=_gemini_ok("DESCRIPTION: digits 0110."))
            with patch("backend.agents.image_analysis.model_router.route_request", route2):
                res = _run(SwarmOrchestrator()._analyze_image_artifact(board))
            self.assertTrue(res.ok)
            self.assertIn(fp, board.analyzed_image_artifacts)
            self.assertGreaterEqual(len(board.image_analysis_evidence), 1)


if __name__ == "__main__":
    unittest.main()
