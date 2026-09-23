"""FORGE swarm module-level helpers.

Deterministic helpers shared by the swarm blackboard (``swarm_state``) and the
orchestrator (``swarm_orchestrator``): artifact decoding, HTTP-header validation,
recon/command normalization, failure-signature and evidence fingerprinting,
challenge logging, and strategy-exhaustion accounting.

Relocated verbatim from ``backend/agents/swarm_orchestrator.py`` — no behavior
change; the orchestrator re-imports these names.
"""

import base64
import codecs
import hashlib
import logging
import os
import re
import shlex
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from backend.websocket.manager import ws_manager
from backend.agents.strategic_planner import strategic_planner

if TYPE_CHECKING:  # pragma: no cover - typing only; avoids an import cycle
    from backend.agents.swarm_state import SwarmBlackboard


logger = logging.getLogger("forge.swarm")


# ── Strategy-exhaustion constants (PRIMARY fix: "No FA" budget burn) ─────────
# Number of consecutive/total turns on a strategy that produce NO new evidence
# before the strategy is declared exhausted (mirrors blocked_failure_sigs threshold=3).
STRATEGY_STALE_LIMIT = 3
# Hard cap on total turns spent on any single strategy across all agents.
STRATEGY_ATTEMPT_LIMIT = 5

# HTTP header names are token characters per RFC 7230 (no spaces, no exotic
# punctuation). Values must be printable single-line ASCII. Anything else is
# LLM prose, not a real header, so we refuse to record or inject it.
_VALID_HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")

# Substrings that betray an LLM placeholder rather than a concrete header value.
# These are the exact shapes that caused the X-Forwarded-For injection loop:
# "127.0.0.1; [malicious payload]", "<the flag>", "127.0.0.1**", etc.
_HEADER_VALUE_PLACEHOLDERS = re.compile(
    r"(?:\[[^\]]*\]|<[^>]*>|\bmalicious\b|\bpayload\b|\bexample\b|\byour[_ ]|"
    r"\bplaceholder\b|\binsert\b|\.\.\.|\*\*|`)",
    re.IGNORECASE
)


def _is_meaningful_header(name: str, value: str) -> bool:
    """True only for a concrete, injectable HTTP header.

    Rejects the LLM 'suggestion' shapes — placeholder tokens ("[malicious
    payload]", "<value>", "**") and prose — that previously got scraped back
    into the task pool. It deliberately does NOT reject legitimate techniques
    such as X-Forwarded-For: 127.0.0.1; the injection loop is prevented by
    de-duplication (tried_header_signatures), so a real technique is tried once,
    never dozens of times.
    """
    if not name or not value:
        return False
    name = name.strip()
    value = value.strip()
    if not _VALID_HEADER_NAME.match(name):
        return False
    # Printable single-line ASCII only.
    if any(ord(c) < 0x20 or ord(c) > 0x7E for c in value):
        return False
    if len(value) > 256:
        return False
    if _HEADER_VALUE_PLACEHOLDERS.search(value):
        return False
    return True


# ── English-likeness scoring (ROT13 discrimination) ──────────────────────────
# Relative frequency of each letter in English prose. ROT13 swaps every letter
# with its partner, so letters move across this table: e (12.7%) -> r (6.0%),
# t (9.1%) -> g (2.0%), a (8.2%) -> n (6.8%). Scoring text before and after the
# rotation therefore separates "this was ROT13-encoded" (score goes UP) from
# "this was already plain" (score goes DOWN) — the test the old guard only
# claimed to make, since ROT13 preserves printability and every ASCII string
# passed `_readable`.
_ENGLISH_LETTER_FREQ = {
    "a": 0.0817, "b": 0.0149, "c": 0.0278, "d": 0.0425, "e": 0.1270,
    "f": 0.0223, "g": 0.0202, "h": 0.0609, "i": 0.0697, "j": 0.0015,
    "k": 0.0077, "l": 0.0403, "m": 0.0241, "n": 0.0675, "o": 0.0751,
    "p": 0.0193, "q": 0.0010, "r": 0.0599, "s": 0.0633, "t": 0.0906,
    "u": 0.0276, "v": 0.0098, "w": 0.0236, "x": 0.0015, "y": 0.0197,
    "z": 0.0007,
}
# A rotation must clear this absolute floor to count as a decode. Real English
# prose scores ~0.060+; a rotated/flat letter distribution scores ~0.045, and
# the table's own mean is 0.0385. The floor is what rejects short non-prose
# strings (a flag body, a base64 blob) that no amount of comparison can judge.
_ROT13_ENGLISH_FLOOR = 0.055
# ...and it must beat the original text by at least this much, so a marginal
# wobble between two equally English-ish strings is never reported as a decode.
_ROT13_IMPROVEMENT_MIN = 0.008


def _english_score(text: str) -> float:
    """Mean English letter frequency across the alphabetic characters of *text*.

    Higher means more English-like. Returns 0.0 when there are no letters, so a
    purely numeric or symbolic artifact can never clear the floor.
    """
    letters = [c.lower() for c in text if c.isascii() and c.isalpha()]
    if not letters:
        return 0.0
    return sum(_ENGLISH_LETTER_FREQ.get(c, 0.0) for c in letters) / len(letters)


def _decode_artifacts(text: str) -> List[Dict[str, str]]:
    """Deterministically decode ROT13 / base64 / hex artifacts found in text.

    Returns a list of {"scheme", "input", "decoded"} for any decode that yields
    readable ASCII differing from the input. This is what turns the challenge's
    ROT13 hint ("NOTE: Jack - temporary bypass: use header ...") into a concrete
    lead instead of relying on the LLM to carry the decode through.
    """
    results: List[Dict[str, str]] = []
    if not text:
        return results
    seen: Set[str] = set()

    def _readable(s: str) -> bool:
        if len(s) < 4:
            return False
        printable = sum(1 for c in s if 0x20 <= ord(c) <= 0x7E)
        return printable / max(len(s), 1) > 0.85

    # ROT13 over the whole text — cheap and reversible. Only keep it when the
    # rotation actually turned the text INTO English: ROT13 of already-plain text
    # is more gibberish, not a secret, and reporting it as a decode pollutes the
    # log and feeds junk into candidate extraction.
    try:
        rot = codecs.decode(text, "rot_13")
        if rot != text and _readable(rot):
            rot_score = _english_score(rot)
            if (rot_score >= _ROT13_ENGLISH_FLOOR
                    and rot_score - _english_score(text) >= _ROT13_IMPROVEMENT_MIN):
                key = ("rot13", rot[:200])
                if key not in seen:
                    seen.add(key)
                    results.append({"scheme": "rot13", "input": text[:200], "decoded": rot[:400]})
    except Exception:
        pass

    # base64 tokens (length divisible by 4, >= 12 chars to avoid short false hits)
    for token in re.findall(r"[A-Za-z0-9+/]{12,}={0,2}", text):
        if len(token) % 4 != 0:
            continue
        try:
            dec = base64.b64decode(token, validate=True).decode("utf-8", "strict")
        except Exception:
            continue
        if _readable(dec) and dec != token:
            key = ("base64", dec[:200])
            if key not in seen:
                seen.add(key)
                results.append({"scheme": "base64", "input": token[:200], "decoded": dec[:400]})

    # hex strings (even length, >= 16 nybbles)
    for token in re.findall(r"(?:[0-9a-fA-F]{2}){8,}", text):
        try:
            dec = bytes.fromhex(token).decode("utf-8", "strict")
        except Exception:
            continue
        if _readable(dec) and dec != token:
            key = ("hex", dec[:200])
            if key not in seen:
                seen.add(key)
                results.append({"scheme": "hex", "input": token[:200], "decoded": dec[:400]})

    return results


# Recognizes an instruction like: use header "X-Dev-Access: yes" — the decoded
# form of this challenge's hint. Pulls the concrete header out of decoded prose.
_HEADER_HINT_RE = re.compile(
    r"header[\"'\s:]*[\"']?([A-Za-z0-9][A-Za-z0-9-]{0,63})\s*:\s*([^\"'\n\r]{1,120})",
    re.IGNORECASE
)


def _effective_elapsed_minutes(started_ts: float, now: float, paused_seconds: float) -> float:
    """Wall-clock minutes an agent has actually been WORKING — total elapsed minus any
    time it sat idle at a checkpoint pause. Pure/synchronous so it is unit-testable and
    so the budget gate and the BUDGET_EXHAUSTED message stay consistent."""
    worked = (now - started_ts) - max(0.0, paused_seconds or 0.0)
    return max(0.0, worked) / 60.0


def _get_challenge_log_path(challenge_id: str) -> str:
    """Resolve the path to the challenge log file.

    Delegates to the central resolver so the log lives inside a subtree mirroring
    the challenge's own file structure (Platform/Category/Difficulty/Name) under the
    canonical ``backend/logs`` base, instead of a flat pile of log files.
    """
    from backend.utils.challenge_paths import resolve_challenge_log_path
    return resolve_challenge_log_path(challenge_id)


def _append_to_challenge_log(challenge_id: str, worker_id: str, message: str):
    """Thread-safe append a line to the challenge's dedicated log file."""
    try:
        log_path = _get_challenge_log_path(challenge_id)
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{worker_id}] {message}\n")
    except Exception:
        pass


def _normalize_recon_target(cmd: str) -> Optional[str]:
    """Extract and normalize a cache key for recon-shaped commands.

    Preserves auth state (headers, cookies, user, method, body) while normalizing away
    formatting-only flags (-s, -v, -i, -L, -k). Returns None if not a recon command.
    """
    if not cmd or not isinstance(cmd, str):
        return None
    try:
        tokens = shlex.split(cmd.strip())
    except Exception:
        tokens = cmd.strip().split()
    if not tokens:
        return None

    prog = os.path.basename(tokens[0]).lower()

    if prog in ("curl", "wget"):
        url = None
        method = "GET"
        headers = []
        cookies = []
        auth = ""
        body = []

        i = 1
        while i < len(tokens):
            t = tokens[i]
            # Strip formatting flags
            if t in ("-s", "--silent", "-v", "--verbose", "-i", "--include", "-k", "--insecure",
                     "-L", "--location", "-q", "-N", "-O", "--compressed"):
                i += 1
                continue
            if t in ("-o", "--output"):
                i += 2
                continue

            # Preserve Method
            if t in ("-X", "--request") and i + 1 < len(tokens):
                method = tokens[i + 1].upper()
                i += 2
                continue

            # Preserve Headers
            if t in ("-H", "--header") and i + 1 < len(tokens):
                headers.append(tokens[i + 1].strip())
                i += 2
                continue

            # Preserve Cookies
            if t in ("-b", "--cookie", "-c", "--cookie-jar") and i + 1 < len(tokens):
                cookies.append(tokens[i + 1].strip())
                i += 2
                continue

            # Preserve Auth
            if t in ("-u", "--user") and i + 1 < len(tokens):
                auth = tokens[i + 1].strip()
                i += 2
                continue

            # Preserve Body / Post data
            if t in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form") and i + 1 < len(tokens):
                body.append(tokens[i + 1].strip())
                if method == "GET":
                    method = "POST"
                i += 2
                continue

            # Positional argument: URL
            if not t.startswith("-") and url is None:
                url = t.strip().rstrip("/")
                i += 1
                continue

            i += 1

        if not url:
            return None

        headers_str = ";".join(sorted(headers))
        cookies_str = ";".join(sorted(cookies))
        body_str = ";".join(sorted(body))

        # A bare fetch and the same fetch piped through grep/head/cat/etc. must
        # NOT collapse to the same cache key - only truly identical commands
        # (including identical downstream processing) should dedupe.
        pipeline_match = re.search(r"[|;]|&&|\$\(|`", cmd)
        pipeline_suffix = ""
        if pipeline_match:
            downstream = cmd[pipeline_match.start():]
            pipeline_suffix = re.sub(r"\s+", " ", downstream.strip().lower())

        return f"web:{method}:{url.lower()}:h={headers_str}:c={cookies_str}:u={auth}:b={body_str}:p={pipeline_suffix}"

    elif prog in ("cat", "head", "tail", "strings"):
        files = [t for t in tokens[1:] if not t.startswith("-")]
        if not files:
            return None
        norm_files = sorted([os.path.normpath(f) for f in files])
        return f"file:{prog}:{','.join(norm_files)}"

    elif prog == "nmap":
        targets = [t for t in tokens[1:] if not t.startswith("-")]
        if not targets:
            return None
        return f"nmap:{','.join(sorted(targets)).lower()}"

    return None


def _normalize_failure_signature(cmd: str, category: str, output_or_stderr: str) -> str:
    """Computes a normalized failure signature key incorporating target host, category, and error text."""
    target = "local"
    if cmd:
        url_match = re.search(r"https?://([^/\s'\"]+)", cmd)
        if url_match:
            target = url_match.group(1).lower()
        else:
            ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", cmd)
            if ip_match:
                target = ip_match.group(0)
            else:
                tokens = cmd.strip().split()
                if tokens:
                    target = os.path.basename(tokens[0]).lower()

    lines = [l.strip() for l in (output_or_stderr or "").splitlines() if l.strip()]
    raw_line = lines[0] if lines else "unknown_error"

    norm_line = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", raw_line)
    norm_line = re.sub(r"\b\d+\b", "N", norm_line)
    norm_line = re.sub(r"\s+", " ", norm_line).lower().strip()
    if len(norm_line) > 100:
        norm_line = norm_line[:100]

    cat_str = (category or "EXEC_FAIL").upper()
    return f"{target}:{cat_str}:{norm_line}"


def _normalize_command_shape(cmd: str) -> str:
    """Computes an execution-independent command shape key for pre/post check matching."""
    if not cmd or not isinstance(cmd, str):
        return "empty_cmd"

    recon_key = _normalize_recon_target(cmd)
    if recon_key:
        return recon_key

    try:
        tokens = shlex.split(cmd.strip())
    except Exception:
        tokens = cmd.strip().split()
    if not tokens:
        return "empty_cmd"

    prog = os.path.basename(tokens[0]).lower()

    target_host = "local"
    url_m = re.search(r"https?://([^/\s'\"]+)", cmd)
    if url_m:
        target_host = url_m.group(1).lower()
    elif re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", cmd):
        target_host = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", cmd).group(0)

    norm_tokens = []
    for t in tokens[1:]:
        # Strip content hash from solve scripts
        t_clean = re.sub(r"solve_([a-zA-Z0-9_]+)_[0-9a-fA-F]{8}\.py", r"solve_\1.py", t)
        # Strip query parameters from URL arguments so cosmetic query variations collapse to the same shape
        t_clean = re.sub(r"(https?://[^\s'\"]+?)\?[^\s'\"]*", r"\1", t_clean)
        norm_tokens.append(t_clean)

    args_str = " ".join(norm_tokens[:5]).lower()
    return f"{target_host}:{prog}:{args_str}"


def _compute_evidence_fingerprint(output: str) -> str:
    """Lightweight fingerprint of any NEW fact surfaced by an action output.

    Normalises the output (strips timestamps, memory addresses, UUIDs) so that
    cosmetically-different but semantically-identical outputs hash identically.
    Returns an empty string for empty/whitespace-only output so callers can
    treat falsy-fingerprint as 'produced nothing'.
    """
    if not output or not output.strip():
        return ""
    # Normalise away values that vary between runs but don't represent new facts.
    norm = output.strip()
    norm = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "UUID", norm, flags=re.IGNORECASE)
    norm = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", norm)
    norm = re.sub(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[.\d]*Z?\b", "TIMESTAMP", norm)
    norm = re.sub(r"\s+", " ", norm).lower().strip()
    return hashlib.sha256(norm[:800].encode("utf-8", "replace")).hexdigest()[:16]


def _update_strategy_state(
    board: "SwarmBlackboard",
    agent_id: str,
    strategy_label: str,
    output: str,
    res: object,
) -> bool:
    """Record one strategy turn and return True if the strategy just became exhausted.

    Called after every command execution (including clean exit=0 runs). Updates:
    - ``strategy_attempts``         — total turns on this label
    - ``strategy_evidence_fingerprints`` — set of distinct evidence hashes seen
    - ``strategy_stale_counts``     — consecutive stale turns (reset on new evidence)
    - ``exhausted_strategies``      — set if stale or attempt limit is hit

    This is a plain function (not a method) so it can be unit-tested without
    constructing a live SwarmBlackboard.
    """
    label = (strategy_label or "unknown").strip().lower()
    board.strategy_attempts[label] = board.strategy_attempts.get(label, 0) + 1

    fp = _compute_evidence_fingerprint(output)
    known = board.strategy_evidence_fingerprints.setdefault(label, set())
    if fp and fp not in known:
        known.add(fp)
        board.strategy_stale_counts[label] = 0          # new evidence → reset stale
        stale = 0
    else:
        board.strategy_stale_counts[label] = board.strategy_stale_counts.get(label, 0) + 1
        stale = board.strategy_stale_counts[label]

    attempts = board.strategy_attempts.get(label, 0)
    # "unknown" strategy is exempt from entering exhausted_strategies to prevent locking out unformatted turns
    if label != "unknown" and label not in board.exhausted_strategies:
        if stale >= STRATEGY_STALE_LIMIT or attempts >= STRATEGY_ATTEMPT_LIMIT:
            board.exhausted_strategies.add(label)
            _append_to_challenge_log(
                board.challenge_id, agent_id,
                f"[STRATEGY EXHAUSTED] '{label}' (stale={stale}, total_attempts={attempts})"
            )
            logger.info("[%s] [STRATEGY EXHAUSTED] label=%s stale=%d attempts=%d",
                        agent_id, label, stale, attempts)
            return True
    return False


async def _force_pivot_if_needed(board: "SwarmBlackboard", agent_id: str, exhausted_label: str):
    """Trigger a strategic pivot review when a strategy class is marked exhausted.

    Serializes review calls per-label and globally so only one LLM review runs at a time,
    while queuing/allowing subsequent reviews for newly exhausted strategies. Sets
    board.pivot_directive, appends banned labels to board.exhausted_strategies, logs to
    challenge log, and broadcasts STRATEGY_PIVOT_FORCED over WebSocket.
    """
    label = (exhausted_label or "").strip().lower()
    if not label:
        return

    async with board._lock:
        if (
            label in board.pivot_reviews_in_flight
            or label in board.reviewed_pivot_strategies
        ):
            return
        board.pivot_reviews_in_flight.add(label)

    try:
        async with board._pivot_lock:
            history = [
                h.get("command", "") + " -> " + (h.get("output") or "")[:80]
                for h in board.execution_history[-10:]
            ]
            try:
                pivot_text, banned = await strategic_planner.review_swarm_pivot(
                    challenge_name=board.challenge_name,
                    category=board.category,
                    target=board.target_scope,
                    exhausted_strategies=list(board.exhausted_strategies),
                    recent_history=history,
                    all_strategy_attempts=dict(board.strategy_attempts),
                )
            except Exception as e:
                logger.warning(f"[{agent_id}] Swarm pivot review failed: {e}")
                pivot_text = (
                    f"All {', '.join(sorted(board.exhausted_strategies))} approaches exhausted. "
                    "Switch to a completely different attack class."
                )
                banned = list(board.exhausted_strategies)

            async with board._lock:
                board.pivot_directive = pivot_text
                board.reviewed_pivot_strategies.add(label)
                for b in banned:
                    if b:
                        board.exhausted_strategies.add(b.strip().lower())
    finally:
        async with board._lock:
            board.pivot_reviews_in_flight.discard(label)

    _append_to_challenge_log(
        board.challenge_id, agent_id,
        f"[STRATEGY_PIVOT_FORCED] New directive: {pivot_text[:200]}"
    )
    try:
        await ws_manager.broadcast({
            "event": "STRATEGY_PIVOT_FORCED",
            "challenge_id": board.challenge_id,
            "run_id": board.run_id,
            "exhausted": list(board.exhausted_strategies),
            "pivot": pivot_text,
            "triggered_by": agent_id,
        })
    except Exception:
        pass
