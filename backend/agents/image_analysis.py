"""Focused bridge from Forge's image-artifact workflow to the Gemini vision capability.

This is the *missing integration* identified by the Gemini vision audit: the
Gemini provider already accepts explicit ``(image_data, image_mime_type)`` pairs
and turns them into ``inline_data`` parts (see ``GeminiProvider``), but nothing in
Forge ever recovered an image artifact and handed it over. This module is that
bridge and nothing more:

  * It reads a reconstructed image artifact from the path already recorded on the
    artifact metadata (``ClassificationResult.safe_file_path``) and maps the
    artifact label (``"jpeg"``) to a real MIME type (``"image/jpeg"``) — a bare
    artifact label is never shipped as a MIME type.
  * It sends the bytes plus a challenge-specific prompt through the EXISTING
    router, explicitly selecting Gemini via ``target_model`` (the same supported
    mechanism ``_make_summarizer`` uses) rather than relying on the generic
    capability chain to pick a vision-capable provider.
  * It verifies the response actually came from Gemini, so a text-only provider
    silently ignoring the image kwargs is never reported as a successful vision
    analysis.
  * It returns plain analysis data. It never marks a challenge solved and never
    promotes a flag — that stays with Forge's existing flag-verification path.

Local OCR and deterministic artifact analysis are untouched; a Gemini failure
simply returns a structured non-OK result and the caller keeps its existing paths.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.providers.real_providers import GeminiProvider
from backend.providers.router import model_router

logger = logging.getLogger("forge.image_analysis")

# Artifact labels produced by backend.agents.artifact_classifier._classify_magic
# (and common extensions) mapped to the MIME type Gemini expects. Deliberately a
# whitelist: an unknown label is never guessed into a MIME type.
_ARTIFACT_TYPE_TO_MIME: Dict[str, str] = {
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "tiff": "image/tiff",      # recognised as an image, but unsupported by Gemini
    "tif": "image/tiff",
}

_EXTENSION_TO_MIME: Dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

# The MIME types actually accepted by the committed Gemini image capability.
# Referenced directly from the provider so this integration can never drift from
# it (and so an unsupported type is refused before any HTTP request).
_SUPPORTED_MIME = frozenset(GeminiProvider.SUPPORTED_IMAGE_MIME_TYPES)

# Explicit Gemini selection via the router's existing MODEL_PROVIDER_MAP entry.
# Kept as a named constant so the integration point is obvious and testable.
GEMINI_VISION_MODEL = "gemini-3.6-flash"
GEMINI_PROVIDER_NAME = "gemini"

_SYSTEM_INSTRUCTION = (
    "You are a careful image-analysis assistant for an authorized CTF training lab. "
    "Report only what is actually visible in the image; never invent content."
)


@dataclass
class ImageAnalysisResult:
    """Outcome of a single Gemini image-analysis pass. Never carries a verdict."""

    status: str                       # "OK" | "SKIPPED" | "FAILED"
    description: str = ""
    leads: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    image_path: str = ""
    mime_type: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def image_mime_type_for(artifact_type: str = "", image_path: str = "") -> Optional[str]:
    """Map an artifact label / file extension to a real image MIME type.

    The artifact label wins when recognised; otherwise the path extension is used.
    Returns ``None`` when neither identifies an image — the caller must NOT pass a
    bare label like ``"jpeg"`` as a MIME type.
    """
    label = (artifact_type or "").strip().lower()
    if label in _ARTIFACT_TYPE_TO_MIME:
        return _ARTIFACT_TYPE_TO_MIME[label]
    ext = os.path.splitext(image_path or "")[1].lower()
    return _EXTENSION_TO_MIME.get(ext)


def is_image_artifact(artifact_type: str = "", image_path: str = "") -> bool:
    """True when the artifact metadata / path identifies a recognised image."""
    return image_mime_type_for(artifact_type, image_path) is not None


def image_fingerprint(image_path: str) -> str:
    """Stable identity for an on-disk artifact, used to de-duplicate analysis.

    Combines absolute path + size + mtime so the SAME image is never sent to Gemini
    more than once, while a revised/regenerated file at the same path is new work.
    ``""`` for a missing/unreadable path (nothing to analyse).
    """
    if not image_path:
        return ""
    try:
        st = os.stat(image_path)
    except OSError:
        return ""
    return f"{os.path.abspath(image_path)}:{st.st_size}:{st.st_mtime_ns}"


def build_image_analysis_prompt(
    *,
    challenge_name: str = "",
    category: str = "",
    description: str = "",
    target: str = "",
    artifact_type: str = "",
    image_path: str = "",
) -> str:
    """Challenge-specific prompt: describe the image and surface investigative leads.

    The model is asked to transcribe any flag-like string VERBATIM and label it as an
    unverified candidate — it is never asked to capture or confirm a flag.
    """
    desc = (description or "").strip()
    return (
        "A challenge artifact has been recovered from a CTF target and needs visual "
        "inspection.\n\n"
        f"Challenge: {challenge_name or '(unknown)'}\n"
        f"Category : {category or '(unknown)'}\n"
        f"Target   : {target or '(none)'}\n"
        f"Artifact : {artifact_type or 'image'} saved at {image_path or '(path unknown)'}\n"
        f"Description: {desc or '(no description provided)'}\n\n"
        "Describe what the image actually shows in concrete, specific terms. If the "
        "image contains text (including binary digits, encoded strings, or a hint), "
        "transcribe it EXACTLY character-for-character. Then list concrete "
        "investigative leads (hidden data, stego indicators, encodings, or next steps).\n\n"
        "Report only what is visibly present. Do NOT invent or confirm a flag. If you "
        "see a string that looks like a flag, transcribe it verbatim and label that line "
        "'FLAG_CANDIDATE:' — it is unverified and will be checked separately.\n\n"
        "Format:\n"
        "DESCRIPTION: <what the image shows>\n"
        "LEAD: <investigative lead>   (repeat one LEAD per line, optional)\n"
    )


def _parse_leads(text: str) -> List[str]:
    """Best-effort extraction of ``LEAD:`` lines from the model's prose."""
    leads: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("LEAD:"):
            lead = stripped.split(":", 1)[1].strip()
            if lead:
                leads.append(lead)
    return leads


async def analyze_image_artifact(
    *,
    image_path: str,
    artifact_type: str = "",
    challenge_name: str = "",
    category: str = "",
    description: str = "",
    target: str = "",
    model: str = GEMINI_VISION_MODEL,
    route_request: Optional[Callable[..., Awaitable[Any]]] = None,
) -> ImageAnalysisResult:
    """Analyse one image artifact with the configured Gemini provider.

    Returns an :class:`ImageAnalysisResult`. A non-OK status is a normal, safe
    outcome: the caller records it and keeps its existing local paths. This function
    never raises for expected failures and never touches flag/verification state.
    """
    mime_type = image_mime_type_for(artifact_type, image_path)
    if not mime_type:
        return ImageAnalysisResult(
            status="SKIPPED", image_path=image_path, mime_type="",
            reason=f"Not a recognised image artifact type: {artifact_type!r}")
    if mime_type not in _SUPPORTED_MIME:
        return ImageAnalysisResult(
            status="SKIPPED", image_path=image_path, mime_type=mime_type,
            reason=f"Unsupported image MIME type for Gemini: {mime_type}")

    if not image_path or not os.path.isfile(image_path):
        return ImageAnalysisResult(
            status="FAILED", image_path=image_path, mime_type=mime_type,
            reason=f"Image file not found: {image_path}")

    try:
        with open(image_path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return ImageAnalysisResult(
            status="FAILED", image_path=image_path, mime_type=mime_type,
            reason=f"Could not read image bytes: {exc}")

    if not raw:
        return ImageAnalysisResult(
            status="SKIPPED", image_path=image_path, mime_type=mime_type,
            reason="Image file is empty")

    prompt = build_image_analysis_prompt(
        challenge_name=challenge_name, category=category, description=description,
        target=target, artifact_type=artifact_type, image_path=image_path)

    router = route_request or model_router.route_request
    try:
        resp = await router(
            prompt=prompt,
            system_instruction=_SYSTEM_INSTRUCTION,
            capability="general_reasoning",
            target_model=model,
            image_data=raw,
            image_mime_type=mime_type,
        )
    except Exception as exc:  # a provider/transport failure must never crash a run
        logger.warning(f"[ImageAnalysis] Gemini request failed: {exc}")
        return ImageAnalysisResult(
            status="FAILED", image_path=image_path, mime_type=mime_type,
            reason=f"Gemini request failed: {exc}")

    if resp is None:
        return ImageAnalysisResult(
            status="FAILED", image_path=image_path, mime_type=mime_type,
            reason="No response from router")

    if getattr(resp, "is_refusal", False):
        return ImageAnalysisResult(
            status="FAILED", image_path=image_path, mime_type=mime_type,
            reason=f"Gemini refusal: {getattr(resp, 'refusal_reason', '') or 'unknown'}")

    provider = getattr(resp, "provider_name", "") or ""
    # Explicitly verify the request actually reached GeminiProvider. A text-only
    # provider that silently ignored image_data would otherwise look like success.
    if provider != GEMINI_PROVIDER_NAME:
        return ImageAnalysisResult(
            status="FAILED", image_path=image_path, mime_type=mime_type, provider=provider,
            reason=(f"Image request was not fulfilled by Gemini (provider={provider or 'unknown'}); "
                    f"the image may have been ignored"))

    content = (getattr(resp, "content", "") or "").strip()
    if not content:
        return ImageAnalysisResult(
            status="FAILED", image_path=image_path, mime_type=mime_type, provider=provider,
            reason="Gemini returned an empty description")

    return ImageAnalysisResult(
        status="OK",
        description=content,
        leads=_parse_leads(content),
        provider=provider,
        model=getattr(resp, "model_name", model) or model,
        image_path=image_path,
        mime_type=mime_type,
    )
