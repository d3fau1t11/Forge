"""
FORGE artifact acquisition — the discrete step that sits between artifact
retrieval (URL download or file upload) and prompt/workflow selection.

Responsibilities
----------------
1. For every target URL: perform ONE binary-safe fetch (raw bytes via httpx),
   run the deterministic pre-classifier (artifact_classifier), and — if the
   response is a binary artifact — persist it byte-for-byte to the challenge
   working directory via save_artifact_binary().
2. For every uploaded / local file path: classify it directly.
3. Return an ArtifactManifest that the orchestrator feeds into AgentContext
   (attached_file_paths + artifact_classification), which in turn flips the
   agent prompt into BINARY ARTIFACT MODE when appropriate.

Why this is a separate module (not part of tool_manager)
--------------------------------------------------------
`tool_manager.execute_tool` decodes subprocess output as UTF-8
(`stdout_bytes.decode(errors="replace")`) — that is exactly what corrupted the
"Transformation" REV binary. Raw artifact bytes must NEVER pass through that
text-decoding layer. This module downloads with httpx (bytes in hand) and writes
with `open(..., "wb")`, so the artifact reaches disk unmodified.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlsplit, unquote

import httpx

from backend.agents.artifact_classifier import (
    ClassificationResult,
    classify_http_response,
    classify_local_file,
    save_artifact_binary,
)

logger = logging.getLogger("forge.artifact_acquisition")

# Never buffer an unbounded response into memory. CTF artifacts are small; a
# multi-hundred-MB "artifact" is almost certainly the wrong target, so above the
# cap we classify by headers only and skip the byte download.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024      # 64 MiB
_FETCH_TIMEOUT_SECONDS = 15.0


@dataclass
class ArtifactManifest:
    """Result of acquiring every artifact referenced by a challenge."""
    saved_paths: List[str] = field(default_factory=list)          # byte-exact files on disk
    classifications: List[ClassificationResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)                # human-readable, logged

    @property
    def is_binary(self) -> bool:
        return any(c.is_binary for c in self.classifications)

    @property
    def primary(self) -> Optional[ClassificationResult]:
        """The first binary artifact (what the prompt's BINARY MODE block uses)."""
        for c in self.classifications:
            if c.is_binary:
                return c
        return None


def _filename_from_url(url: str, content_disposition: str = "") -> str:
    """Derive a safe on-disk filename from a Content-Disposition header or URL path."""
    # Prefer an explicit filename= from Content-Disposition.
    if content_disposition and "filename=" in content_disposition.lower():
        raw = content_disposition.split("filename=", 1)[1].strip().strip('"\'')
        raw = raw.split(";")[0].strip().strip('"\'')
        candidate = os.path.basename(unquote(raw))
        if candidate:
            return _sanitize_name(candidate)

    path = urlsplit(url).path
    candidate = os.path.basename(unquote(path)) if path else ""
    return _sanitize_name(candidate) if candidate else "artifact.bin"


def _sanitize_name(name: str) -> str:
    """Strip path separators and control chars; guarantee a non-empty basename."""
    name = name.replace("\\", "_").replace("/", "_").strip()
    name = "".join(c for c in name if 0x20 <= ord(c) <= 0x7E and c not in '<>:"|?*')
    return name or "artifact.bin"


async def _acquire_url(url: str, working_directory: str, manifest: ArtifactManifest) -> None:
    """Fetch one URL byte-safely, classify it, and save it if binary. Non-fatal."""
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS, verify=False, follow_redirects=True
        ) as client:
            resp = await client.get(url)
    except Exception as exc:                       # noqa: BLE001 — network failure is non-fatal
        manifest.notes.append(f"URL fetch failed for {url} (non-fatal): {exc}")
        logger.warning(f"[ArtifactAcquisition] Fetch failed for {url}: {exc}")
        return

    headers = resp.headers
    content_type = (headers.get("content-type") or "").lower()
    server_header = (headers.get("server") or "").lower()
    try:
        content_length = int(headers.get("content-length")) if headers.get("content-length") else None
    except (TypeError, ValueError):
        content_length = None

    # Body prefix for magic-byte checks — httpx already has the bytes; never decode them.
    body = resp.content or b""
    if content_length is None:
        content_length = len(body)

    clf = classify_http_response(
        url=url,
        content_type=content_type,
        server_header=server_header,
        content_length=content_length,
        response_body_prefix=body[:512],
    )

    if clf.is_binary:
        if len(body) == 0:
            manifest.notes.append(f"{url} classified binary ({clf.reason}) but body was empty; not saved")
        elif len(body) > MAX_DOWNLOAD_BYTES:
            manifest.notes.append(
                f"{url} classified binary ({clf.reason}) but exceeds {MAX_DOWNLOAD_BYTES} bytes; "
                f"header-only classification, not saved"
            )
        else:
            saved = save_artifact_binary(
                body, working_directory,
                suggested_name=_filename_from_url(url, headers.get("content-disposition", "")),
            )
            clf.safe_file_path = saved
            manifest.saved_paths.append(saved)
            manifest.notes.append(f"Saved binary artifact from {url} → {saved} ({clf.reason})")
    else:
        manifest.notes.append(f"{url} → web/text target ({clf.reason})")

    manifest.classifications.append(clf)


async def acquire_artifacts(
    targets: List[str],
    working_directory: str,
    uploaded_paths: Optional[List[str]] = None,
) -> ArtifactManifest:
    """Acquire and classify every artifact for a challenge run.

    Parameters
    ----------
    targets:
        Raw target tokens (already '+'-split by the caller). URLs are fetched;
        existing local paths are classified in place; bare host/IP tokens are
        ignored (not artifacts).
    working_directory:
        Absolute challenge workspace dir; binary downloads are saved here.
    uploaded_paths:
        Absolute paths of operator-uploaded files, classified directly.
    """
    manifest = ArtifactManifest()
    os.makedirs(working_directory, exist_ok=True)

    for t in targets:
        t = (t or "").strip()
        if not t:
            continue
        if t.startswith("http://") or t.startswith("https://"):
            await _acquire_url(t, working_directory, manifest)
        elif os.path.isfile(t):
            clf = classify_local_file(t)
            manifest.classifications.append(clf)
            if clf.is_binary:
                manifest.saved_paths.append(t)     # already on disk, byte-exact
                manifest.notes.append(f"Local artifact {t} classified binary ({clf.reason})")
            else:
                manifest.notes.append(f"Local file {t} classified text/source ({clf.reason})")
        # bare host/IP → not an artifact; the agent handles it as a network target.

    for p in (uploaded_paths or []):
        p = (p or "").strip()
        if not p or not os.path.isfile(p):
            if p:
                manifest.notes.append(f"Uploaded path missing on disk: {p}")
            continue
        clf = classify_local_file(p)
        manifest.classifications.append(clf)
        if clf.is_binary:
            manifest.saved_paths.append(p)
            manifest.notes.append(f"Uploaded artifact {p} classified binary ({clf.reason})")
        else:
            manifest.notes.append(f"Uploaded file {p} classified text/source ({clf.reason})")

    return manifest
