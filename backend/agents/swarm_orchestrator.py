"""FORGE Autonomous Swarm Intelligence Orchestrator.
Dispatches specialized parallel agent workers (Recon, Code Audit, Exploit Solver) on a shared blackboard.
Features:
- Dynamic claim-and-solve task pool
- Real-time shared target memory (endpoints, headers, decoded secrets)
- Deduplication filter
- Zero-retry Quota Circuit Breaker
- Verified flag discovery (tool output only, never LLM prose)
"""

import asyncio
import base64
import codecs
import hashlib
import json
import re
import os
import shlex
import sys
import time
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set

from backend.database.session import SessionLocal
from backend.database.models import RunModel, ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel, ToolExecutionModel, CheckpointModel, TrajectoryEventModel
from backend.providers.router import model_router
from backend.tools.manager import tool_manager, LOCAL_EXEC_CATEGORIES
from backend.privilege.manager import privilege_manager
from backend.privilege.classify import classify_command_privilege
from backend.privilege.gate import require_approval, SHARED_PENDING_APPROVALS
from backend.websocket.manager import ws_manager
from backend.engine.keep_awake import keep_awake_manager
from backend.reporting.generator import report_generator
from backend.knowledge.playbook_vault import playbook_vault
from backend.knowledge.memory_retriever import memory_retriever
from backend.knowledge.experience_memory import experience_memory
from backend.knowledge.experience_extractor import experience_extractor
from backend.recon.turbo_recon import turbo_recon
from backend.config import settings
from backend.environment.detector import environment_detector
from backend.agents.agent_prompt import AgentContext, build_agent_prompt, make_context_from_env
from backend.agents.artifact_acquisition import acquire_artifacts
from backend.agents import checkpoint_pipeline
from backend.agents.checkpoint_pipeline import (
    AgentCheckpointRecord,
    evaluate_suggestions,
    SuggestionDecision,
)
from backend.agents.strategic_planner import strategic_planner
from backend.agent_runtime.verifier import (
    AnswerResolver, AnswerCandidate, AnswerVerdict, AnswerStatus, AnswerSource,
    VerifierAgent, FLAG_REGEX, FALSE_FLAG_PATTERNS,
)
from backend.agents.response_profiler import (
    ResponseProfiler,
    AnomalyResult,
    CandidateArtifact,
    extract_generic_artifacts,
    generate_post_exploitation_probes,
)


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

    # ROT13 over the whole text — cheap and reversible; only keep if it changed
    # the text into something readable (ROT13 of already-plain text is garbage).
    try:
        rot = codecs.decode(text, "rot_13")
        if rot != text and _readable(rot):
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


class SwarmTask:
    def __init__(self, task_id: str, category: str, description: str, priority: int = 1, metadata: Optional[Dict] = None):
        self.task_id = task_id
        self.category = category # RECON, CODE_AUDIT, CRYPTO_DECODE, EXPLOIT, PWN
        self.description = description
        self.priority = priority
        self.metadata = metadata or {}
        self.claimed_by: Optional[str] = None
        self.status = "PENDING" # PENDING, CLAIMED, COMPLETED, FAILED
        self.result: Optional[str] = None

class SwarmBlackboard:
    """Shared in-memory and synchronized state across all parallel swarm workers."""
    def __init__(
        self,
        challenge_id: str,
        run_id: str,
        target_scope: str,
        *,
        description: str = "",
        category: str = "WEB",
        difficulty: str = "EASY",
        challenge_name: str = "",
        flag_pattern: str = "",
    ):
        self.challenge_id = challenge_id
        self.run_id = run_id
        self.target_scope = target_scope
        self.discovered_endpoints: Set[str] = set()
        self.extracted_headers: Dict[str, str] = {}
        self.observed_cookies: Dict[str, str] = {}
        self.deobfuscated_secrets: List[Dict[str, str]] = []
        self.execution_history: List[Dict[str, Any]] = []
        self.executed_commands_dedup: Set[str] = set()
        self.recon_cache: Dict[str, str] = {}
        self.failure_signatures: Dict[str, int] = {}
        self.blocked_failure_sigs: Set[str] = set()
        self.blocked_capabilities: Set[str] = set()
        self.task_pool: Dict[str, SwarmTask] = {}
        self.flag_captured: Optional[str] = None
        self.flag_candidates: List[Dict[str, str]] = []  # Unverified candidates
        self.flag_event = asyncio.Event()
        self.is_stopped = False
        # Set true only by an operator Pause so run_swarm finalizes as PAUSED
        # (resumable) rather than FAILED. Distinct from is_stopped, which also
        # trips on flag capture / kill switch.
        self.pause_requested = False
        # ── Strategy-exhaustion tracking (PRIMARY fix for "No FA" budget burn) ──
        # Keyed by strategy label (snake_case, from the agent's STRATEGY: line).
        # Shared across ALL agents on the blackboard — agent_1's stale result
        # counts against agent_3's budget for the same strategy class.
        self.strategy_attempts: Dict[str, int] = {}           # total turns per label
        self.strategy_stale_counts: Dict[str, int] = {}       # consecutive stale turns
        self.strategy_evidence_fingerprints: Dict[str, Set[str]] = {}  # distinct evidence hashes
        self.exhausted_strategies: Set[str] = set()           # labels that hit the limit
        self.pivot_directive: str = ""                        # injected after a forced pivot
        self.pivot_reviews_in_flight: Set[str] = set()        # labels currently being reviewed
        self.reviewed_pivot_strategies: Set[str] = set()      # labels whose pivot review has completed
        # Stall / refill / persistence state
        self.stall_reason: Optional[str] = None
        self.worker_states: Dict[str, Dict[str, Any]] = {}
        self.refill_count = 0
        self.max_refills = 6
        self.refresh_interval = 15  # seconds between task-pool refill checks
        self.last_activity_ts = time.time()
        self.last_persist_ts = 0.0
        self.persist_interval = 3.0
        self.started_ts = time.time()
        # Signatures ("name: value") of header injections already queued, so the
        # same lead is never re-added to the task pool (kills the injection loop).
        self.tried_header_signatures: Set[str] = set()
        # Candidate Entities Tracking (Usernames, tokens, and decode deduplication)
        self.candidate_usernames: Set[str] = set()
        self.candidate_tokens: Set[str] = set()
        self.tested_user_combinations: Set[str] = set()
        self.processed_decode_hashes: Set[str] = set()
        self.last_target_rejection: Optional[str] = None
        self._lock = asyncio.Lock()
        self._pivot_lock = asyncio.Lock()

        # ── Experience memory (retrieved ONCE per mission, shared by all agents) ──
        # memory_context is a compact, reference-only prompt block; retrieved_memory_ids
        # feeds the post-solve feedback loop (§6, §8, §12).
        self.memory_context: str = ""
        self.retrieved_memory_ids: List[str] = []

        # ── Unified flexible-agent challenge context ────────────────────────────
        # Populated by run_swarm before agents start; baked into every agent's
        # full-context prompt via build_agent_prompt(). No per-role framing.
        self.challenge_name: str = challenge_name
        self.platform: str = ""
        self.category: str = category or "WEB"
        self.difficulty: str = difficulty or "EASY"
        self.description: str = description
        self.flag_pattern: str = flag_pattern or getattr(settings, "DEFAULT_FLAG_PATTERNS",
                                                         "picoCTF{...}|FLAG{...}|flag{...}|HTB{...}|CTF{...}")
        self.max_iterations: int = getattr(settings, "AGENT_MAX_ITERATIONS", 40)
        self.max_minutes: int = getattr(settings, "AGENT_MAX_MINUTES", 30)
        self.attached_file_paths: List[str] = []
        self.artifact_classification = None            # ClassificationResult | None
        self.env_info: Dict[str, Any] = {}
        self.answer_resolver = AnswerResolver()
        router = None
        try:
            from backend.providers.router import model_router
            router = model_router
        except Exception:
            pass
        self.verifier_agent = VerifierAgent(resolver=self.answer_resolver, router=router)

        # ── Encoded-artifact reconstruction & escalation (deterministic) ────────
        # When a tool output or attached artifact turns out to be an ENCODED file
        # (e.g. a wall of ASCII 0/1 that is really a JPEG), the reconstructed file is
        # preserved here as evidence and escalated for analysis — so the swarm never
        # concludes "no flag" while a rebuilt artifact sits un-analyzed.
        self.derived_artifacts: List[Dict[str, Any]] = []   # provenance records
        self.reconstruction_hashes: Set[str] = set()        # output_sha256 dedup
        self.processed_recon_inputs: Set[str] = set()        # input_sha256 fast-skip

        # ── Empirical response profiling, anomaly detection & actionable preemption ──
        self.response_profiler = ResponseProfiler()
        self.actionable_preemptions: List[Dict[str, Any]] = []
        self.seen_candidate_targets: Set[str] = set()

        # ── Per-agent live state (arbitrary N agents, not fixed roles) ──────────
        self.agent_ids: List[str] = []
        self.agent_transcripts: Dict[str, List[str]] = {}   # agent_id -> transcript lines
        self.agent_directives: Dict[str, str] = {}          # agent_id -> injected directive
        self.agent_iterations: Dict[str, int] = {}          # agent_id -> tool-call count
        self.agent_started_ts: Dict[str, float] = {}        # agent_id -> wall-clock start
        # Wall-clock seconds each agent spent idle at a checkpoint pause. Subtracted from
        # elapsed so a 3.5-min operator pause never counts against a 5-min work budget.
        self.agent_paused_seconds: Dict[str, float] = {}    # agent_id -> accumulated pause secs
        # Consecutive LOCAL execution failures (command never reached the target) per agent.
        # Reset the moment a command actually reaches the target; caps unbounded identical retries.
        self.agent_local_fail_streak: Dict[str, int] = {}   # agent_id -> streak count

        # ── HITL checkpoint state (hard pause & wait) ───────────────────────────
        # checkpoint_pause is a RESUMABLE wait (distinct from is_stopped /
        # pause_requested): agents idle at a checkpoint and resume on operator paste.
        self.checkpoint_pause = False
        self.checkpoint_active = False
        self.checkpoint_response_event = asyncio.Event()
        self.latest_pasted_response: Optional[str] = None
        self.cycle_n = 0
        self.last_checkpoint_report: str = ""
        self.instance_expiry_ts: Optional[float] = None     # epoch secs, or None
        self.cycle_window_start_ts: float = time.time()     # start of the current report window

        # ── Command Privilege Approvals (Per-Request HITL Gate) ────────────────
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}

    async def add_task(self, category: str, description: str, priority: int = 1, metadata: Optional[Dict] = None) -> SwarmTask:
        async with self._lock:
            # Check for duplicate description in pending/claimed tasks
            for t in self.task_pool.values():
                if t.description == description and t.status in ["PENDING", "CLAIMED"]:
                    return t
            t_id = f"task_{uuid.uuid4().hex[:8]}"
            task = SwarmTask(t_id, category, description, priority, metadata)
            self.task_pool[t_id] = task
            await self._broadcast_blackboard()
            return task

    async def claim_task(self, worker_id: str, categories: List[str]) -> Optional[SwarmTask]:
        async with self._lock:
            # Sort by priority descending
            pending = [t for t in self.task_pool.values() if t.status == "PENDING" and t.category in categories]
            if not pending:
                return None
            pending.sort(key=lambda t: t.priority, reverse=True)
            chosen = pending[0]
            chosen.status = "CLAIMED"
            chosen.claimed_by = worker_id
            await self._broadcast_blackboard()
            return chosen

    async def complete_task(self, task_id: str, result: str, discoveries: Optional[Dict] = None):
        async with self._lock:
            self.last_activity_ts = time.time()
            task = self.task_pool.get(task_id)
            if task:
                task.status = "COMPLETED"
                task.result = result

            if discoveries:
                for ep in discoveries.get("endpoints", []):
                    if ep not in self.discovered_endpoints:
                        self.discovered_endpoints.add(ep)
                        asyncio.create_task(ws_manager.broadcast({
                            "event": "EVIDENCE_CAPTURED",
                            "challenge_id": self.challenge_id,
                            "evidence_id": f"ev-{uuid.uuid4().hex[:8]}",
                            "type": "endpoint_discovered",
                            "source": "swarm_recon",
                            "description": f"Discovered Endpoint: {ep}"
                        }))
                for k, v in discoveries.get("headers", {}).items():
                    self.extracted_headers[k] = v
                for k, v in discoveries.get("cookies", {}).items():
                    self.observed_cookies[k] = v
                for sec in discoveries.get("secrets", []):
                    self.deobfuscated_secrets.append(sec)

            await self._broadcast_blackboard()

    async def record_flag(self, flag: str, worker_id: str):
        async with self._lock:
            if not self.flag_captured:
                self.flag_captured = flag
                self.last_activity_ts = time.time()
                self.flag_event.set()
                logger.info(f"[SwarmBlackboard] 🚩 FLAG CAPTURED BY WORKER {worker_id}: {flag}")
                _append_to_challenge_log(self.challenge_id, worker_id, f"🚩 FLAG CAPTURED: {flag}")
                await ws_manager.broadcast({
                    "event": "FLAG_CAPTURED",
                    "challenge_id": self.challenge_id,
                    "run_id": self.run_id,
                    "flag": flag,
                    "solver_worker": worker_id
                })

    async def record_flag_candidate(
        self,
        candidate: str,
        worker_id: str,
        source: str,
        evidence: Optional[Dict[str, Any]] = None,
        command: str = "",
        action_succeeded: bool = True,
    ):
        """Record an answer candidate, resolve against challenge semantics, and promote if verified/resolved."""
        # Phase 1 (lock): read board fields to build candidate object (fast, no I/O)
        async with self._lock:
            task_context = {
                "challenge_id": self.challenge_id,
                "challenge_name": self.challenge_name,
                "category": self.category,
                "difficulty": self.difficulty,
                "description": self.description,
                "flag_pattern": self.flag_pattern,
                "target_scope": self.target_scope,
            }
            cand_obj = AnswerCandidate(
                value=candidate,
                source=self.answer_resolver.normalize_source(source),
                worker_id=worker_id,
                evidence=evidence or {},
                task_context=task_context,
                provenance={"worker_id": worker_id, "command": command, "action_succeeded": action_succeeded},
            )

        # Phase 2 (unlocked): invoke Agent #4 async LLM-assisted verification.
        # Lock is released so other workers are not blocked during the LLM call.
        verdict = await self.verifier_agent.verify(cand_obj)

        # Phase 3 (lock): record the verdict (dedup, append, broadcast)
        async with self._lock:
            if verdict.status == AnswerStatus.REJECTED:
                logger.info(f"[SwarmBlackboard] Rejected false/invalid candidate: {candidate} (reasons: {verdict.reasons})")
                _append_to_challenge_log(self.challenge_id, worker_id, f"⚠ Rejected candidate: {candidate} ({'; '.join(verdict.reasons)})")
                return

            # Check if this candidate was already seen
            for existing in self.flag_candidates:
                if existing.get("flag") == verdict.candidate:
                    return

            self.flag_candidates.append({
                "flag": verdict.candidate,
                "worker": worker_id,
                "source": source,
                "status": verdict.status.value,
                "confidence": verdict.confidence,
                "reasons": verdict.reasons,
                "evidence": evidence or {},
            })
            _append_to_challenge_log(
                self.challenge_id, worker_id,
                f"🏳 Answer candidate from {source}: {verdict.candidate} [{verdict.status.value}, conf={verdict.confidence:.2f}]"
            )
            logger.info(f"[SwarmBlackboard] Answer candidate from {source} by {worker_id}: {verdict.candidate} [{verdict.status.value}]")

            await ws_manager.broadcast({
                "event": "FLAG_CANDIDATE",
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "candidate": verdict.candidate,
                "source": source,
                "worker": worker_id,
                "status": verdict.status.value,
                "confidence": verdict.confidence,
            })

        # If resolved or verified (e.g. from tool_output, vision_read, reconstructed_artifact, etc.):
        if verdict.is_verified or verdict.is_resolved:
            logger.info(f"[SwarmBlackboard] 🎯 Candidate verified/resolved: {verdict.candidate} via {source}")
            await self.record_flag(verdict.candidate, worker_id)

    async def register_derived_artifact(self, outcome, derived_path: Optional[str],
                                        worker_id: str) -> bool:
        """Preserve a reconstructed artifact as shared evidence and escalate it.

        Deduplicates by the reconstructed bytes' sha256 so the same artifact is only
        registered once. Appends a persisted derived FILE to ``attached_file_paths`` so
        every subsequent agent sees it as an analyzable input. Broadcasts an
        ENCODED_ARTIFACT_RECONSTRUCTED event for UI observability (requirement #16).
        Returns True if newly registered.
        """
        if outcome is None:
            return False
        out_hash = getattr(outcome, "output_sha256", "") or ""
        async with self._lock:
            if out_hash and out_hash in self.reconstruction_hashes:
                return False
            if out_hash:
                self.reconstruction_hashes.add(out_hash)
            record = outcome.to_evidence_dict()
            record["derived_path"] = derived_path
            record["analyzed"] = False
            record["worker"] = worker_id
            self.derived_artifacts.append(record)
            # A persisted derived FILE becomes a first-class analyzable input.
            if derived_path and derived_path not in self.attached_file_paths:
                self.attached_file_paths.append(derived_path)
        _append_to_challenge_log(
            self.challenge_id, worker_id,
            f"🧩 Reconstructed {outcome.artifact_type} ({outcome.byte_count} bytes) "
            f"from {outcome.scheme} → {os.path.basename(derived_path) if derived_path else 'in-memory'} "
            f"[{outcome.state}]")
        logger.info("[SwarmBlackboard] Derived artifact registered by %s: %s (%s, %d bytes) path=%s",
                    worker_id, outcome.artifact_type, outcome.scheme, outcome.byte_count, derived_path)
        try:
            await ws_manager.broadcast({
                "event": "ENCODED_ARTIFACT_RECONSTRUCTED",
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "worker": worker_id,
                "scheme": outcome.scheme,
                "artifact_type": outcome.artifact_type,
                "byte_count": outcome.byte_count,
                "state": outcome.state,
                "derived_path": derived_path,
                "recommended_tools": list(outcome.recommended_tools),
            })
        except Exception:
            pass
        return True

    def has_unanalyzed_derived(self) -> bool:
        """True if a reconstructed FILE artifact still needs analysis.

        Used by the finish-without-flag path so the swarm does not terminate with
        "no flag" while a rebuilt artifact remains un-analyzed (requirement #11/#12/#14).
        """
        return any(d.get("derived_path") and not d.get("analyzed")
                   for d in self.derived_artifacts)

    def mark_derived_analyzed(self, derived_path: str):
        for d in self.derived_artifacts:
            if d.get("derived_path") == derived_path:
                d["analyzed"] = True

    async def note_exploit_header(self, name: str, value: str, worker_id: str) -> bool:
        """Validate a candidate exploit header and, if genuinely new and concrete,
        record it and queue a single injection task.

        Returns True only if an injection task was queued. Rejects LLM-placeholder
        shapes and de-dups by "name: value" signature, so a header can never spawn
        the same injection task twice (the root cause of the observed loop).
        """
        name = (name or "").strip()
        value = (value or "").strip().strip('"\'')
        # Cut trailing prose the model appends after a concrete value, e.g.
        # "127.0.0.1 (for bypassing IP restrictions)" -> "127.0.0.1".
        value = re.split(r"\s\(|\s--\s|\s//\s|\s#\s|\s{2,}", value, maxsplit=1)[0].strip().strip('"\'')
        if not _is_meaningful_header(name, value):
            _append_to_challenge_log(self.challenge_id, worker_id, f"⚠ Ignored non-actionable header suggestion: {name}: {value}")
            return False

        signature = f"{name.lower()}: {value}"
        async with self._lock:
            if signature in self.tried_header_signatures:
                return False
            self.tried_header_signatures.add(signature)
            self.extracted_headers[name] = value

        _append_to_challenge_log(self.challenge_id, worker_id, f"🔑 Actionable header queued: {name}: {value}")
        logger.info(f"[SwarmBlackboard] Actionable header queued by {worker_id}: {name}: {value}")
        await self.add_task(
            "EXPLOIT",
            f"Inject header '{name}: {value}' into the homepage, login, and API endpoints on {self.target_scope}",
            priority=6,
            metadata={"header_name": name, "header_value": value},
        )
        return True

    def _build_mission_plan(self) -> Dict[str, Any]:
        """Build the current mission plan for UI + DB persist.

        The claim-and-solve task pool was removed with the fixed-worker model, so
        the 'tasks' rows are now derived from live per-agent state (one row per
        active agent) — the frontend Todo/Mission Plan renders these unchanged.
        """
        plan_tasks = []
        for agent_id in (self.agent_ids or list(self.worker_states.keys())):
            ws_ = self.worker_states.get(agent_id, {})
            raw_status = (ws_.get("status") or "PENDING").upper()
            if self.flag_captured:
                row_status = "COMPLETED"
            elif raw_status in ("RUNNING", "ANALYZING", "EXECUTING"):
                row_status = "IN_PROGRESS"
            else:
                row_status = "PENDING"
            plan_tasks.append({
                "id": agent_id,
                "phase": "AGENT",
                "title": ws_.get("current_task") or "General-purpose CTF agent",
                "tool": "bash",
                "reasoning": f"Model: {ws_.get('selected_model', 'auto')} | iterations: {self.agent_iterations.get(agent_id, 0)}",
                "status": row_status,
                "output_summary": ws_.get("last_result", ""),
            })
        if self.checkpoint_active:
            status = "WAITING_FOR_USER"
        elif self.flag_captured:
            status = "COMPLETED"
        elif self.stall_reason:
            status = "STALLED"
        else:
            status = "IN_PROGRESS"
        return {
            "challenge_id": self.challenge_id,
            "status": status,
            "summary": f"Flexible agent swarm active on {self.target_scope}"
                       + (f" — awaiting operator checkpoint response (cycle {self.cycle_n})" if self.checkpoint_active else "")
                       + (f" — STALLED: {self.stall_reason}" if self.stall_reason else ""),
            "tasks": plan_tasks,
            "strategic_reviews": [],
            "stall_reason": self.stall_reason or "",
            # Resume snapshot — rehydrated by run_swarm(resume=True) so a paused
            # challenge continues aware of prior work instead of repeating it.
            "blackboard_state": {
                # Phase 7 (STEP 2/5) — the target this snapshot was taken against, so a
                # resume can detect a changed target and drop stale host-bound endpoints.
                "target_scope": self.target_scope,
                "discovered_endpoints": list(self.discovered_endpoints),
                "extracted_headers": dict(self.extracted_headers),
                "observed_cookies": dict(self.observed_cookies),
                "deobfuscated_secrets": list(self.deobfuscated_secrets),
                "executed_commands": list(self.executed_commands_dedup),
                "flag_candidates": list(self.flag_candidates),
                "candidate_usernames": list(self.candidate_usernames),
                "agent_directives": dict(self.agent_directives),
                # Full per-agent reasoning history so a resumed run continues each agent's
                # thread instead of restarting recon from scratch (#4). Capped to keep the
                # snapshot small; outputs are already truncated by record_agent_step.
                "agent_transcripts": {aid: lines[-20:] for aid, lines in self.agent_transcripts.items()},
                "execution_history": [
                    {"agent": h.get("agent", ""), "command": h.get("command", ""),
                     "output": (h.get("output") or "")[:800], "ts": h.get("ts", ""), "note": h.get("note", "")}
                    for h in self.execution_history[-60:]
                ],
                "agent_ids": list(self.agent_ids),
                "cycle_n": self.cycle_n,
                # Encoded-artifact reconstruction evidence — so reconstructed derived
                # artifacts (and their analyzed/pending state) survive a pause/resume and
                # re-surface as escalation work instead of being silently forgotten.
                "derived_artifacts": [dict(d) for d in self.derived_artifacts[-40:]],
                "reconstruction_hashes": list(self.reconstruction_hashes),
                "processed_recon_inputs": list(self.processed_recon_inputs)[-200:],
                "recon_cache": dict(self.recon_cache),
                "failure_signatures": dict(self.failure_signatures),
                "blocked_failure_sigs": list(self.blocked_failure_sigs),
                "blocked_capabilities": list(self.blocked_capabilities),
                # Strategy-exhaustion state — survived across pause/resume so a
                # resumed run never re-spends budget on already-exhausted approaches.
                "strategy_attempts": dict(self.strategy_attempts),
                "strategy_stale_counts": dict(self.strategy_stale_counts),
                "strategy_evidence_fingerprints": {
                    k: list(v) for k, v in self.strategy_evidence_fingerprints.items()
                },
                "exhausted_strategies": list(self.exhausted_strategies),
                "pivot_directive": self.pivot_directive,
                "reviewed_pivot_strategies": list(self.reviewed_pivot_strategies),
                "response_profiler": self.response_profiler.to_dict(),
                "actionable_preemptions": [dict(p) for p in self.actionable_preemptions[-30:]],
            },
        }

    def load_snapshot(
        self,
        snapshot: Optional[Dict[str, Any]],
        prior_commands: Optional[List[str]] = None,
        prior_tasks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, int]:
        """Rehydrate blackboard state from a persisted mission_plan snapshot + prior
        tool commands so a resumed swarm continues from where it left off.

        Synchronous by design — call before any worker starts, so no lock/broadcast
        is needed. Returns counts for logging.
        """
        counts = {"endpoints": 0, "headers": 0, "commands": 0, "completed_tasks": 0,
                  "pending_tasks": 0, "transcript_lines": 0}
        snapshot = snapshot or {}

        for k, v in (snapshot.get("recon_cache") or {}).items():
            self.recon_cache[k] = v
        for k, v in (snapshot.get("failure_signatures") or {}).items():
            self.failure_signatures[k] = v
        for sig in (snapshot.get("blocked_failure_sigs") or []):
            self.blocked_failure_sigs.add(sig)
        for cap in (snapshot.get("blocked_capabilities") or []):
            self.blocked_capabilities.add(cap)
        # Strategy-exhaustion state — restore so a resumed run doesn't re-burn budget
        # on already-exhausted approaches (checkpoint-safe requirement).
        for k, v in (snapshot.get("strategy_attempts") or {}).items():
            self.strategy_attempts[k] = int(v)
        for k, v in (snapshot.get("strategy_stale_counts") or {}).items():
            self.strategy_stale_counts[k] = int(v)
        for k, vs in (snapshot.get("strategy_evidence_fingerprints") or {}).items():
            self.strategy_evidence_fingerprints.setdefault(k, set()).update(vs or [])
        for label in (snapshot.get("exhausted_strategies") or []):
            if label:
                self.exhausted_strategies.add(label)
        for label in (snapshot.get("reviewed_pivot_strategies") or []):
            if label:
                self.reviewed_pivot_strategies.add(label)
        self.pivot_directive = snapshot.get("pivot_directive") or ""

        if snapshot.get("response_profiler"):
            self.response_profiler.load_dict(snapshot.get("response_profiler"))
        for p in (snapshot.get("actionable_preemptions") or []):
            if isinstance(p, dict):
                self.actionable_preemptions.append(dict(p))
                targ = p.get("normalized_target") or p.get("raw_value")
                if targ:
                    self.seen_candidate_targets.add(targ)

        for ep in (snapshot.get("discovered_endpoints") or []):
            if ep:
                self.discovered_endpoints.add(ep)
        for k, v in (snapshot.get("extracted_headers") or {}).items():
            self.extracted_headers[k] = v
        for k, v in (snapshot.get("observed_cookies") or {}).items():
            self.observed_cookies[k] = v
        for sec in (snapshot.get("deobfuscated_secrets") or []):
            self.deobfuscated_secrets.append(sec)
        for cand in (snapshot.get("flag_candidates") or []):
            if cand not in self.flag_candidates:
                self.flag_candidates.append(cand)

        # Dedup set: prior executed commands from the snapshot + DB tool executions,
        # so workers never re-run a command already tried in an earlier run.
        for cmd in (snapshot.get("executed_commands") or []):
            if cmd:
                self.executed_commands_dedup.add(cmd)
        for cmd in (prior_commands or []):
            if cmd:
                self.executed_commands_dedup.add(cmd)

        # Restore candidate usernames + prior operator directives + checkpoint cycle
        # (the claim-and-solve task pool was removed with the flexible-agent model).
        for u in (snapshot.get("candidate_usernames") or []):
            if u:
                self.candidate_usernames.add(u)
        for aid, directive in (snapshot.get("agent_directives") or {}).items():
            if directive:
                self.agent_directives[aid] = directive

        # Restore each agent's actual reasoning history (#4): per-agent transcripts + the
        # shared execution history, so build_history_context shows "Your recent steps" and
        # agents resume their thread instead of re-running the same initial recon commands.
        for aid, lines in (snapshot.get("agent_transcripts") or {}).items():
            if lines:
                self.agent_transcripts.setdefault(aid, []).extend(list(lines))
        for h in (snapshot.get("execution_history") or []):
            if h:
                self.execution_history.append(h)
        for aid in (snapshot.get("agent_ids") or []):
            if aid and aid not in self.agent_ids:
                self.agent_ids.append(aid)

        try:
            self.cycle_n = int(snapshot.get("cycle_n") or 0)
        except (TypeError, ValueError):
            self.cycle_n = 0

        # Restore encoded-artifact reconstruction evidence so derived artifacts (and
        # whether they were already analyzed) survive a pause/resume: the escalation
        # in build_history_context re-surfaces any still-pending derived artifact, and
        # dedup sets prevent re-reconstructing/re-persisting the same bytes.
        for rec in (snapshot.get("derived_artifacts") or []):
            if not isinstance(rec, dict):
                continue
            self.derived_artifacts.append(dict(rec))
            out_sha = rec.get("output_sha256")
            if out_sha:
                self.reconstruction_hashes.add(out_sha)
            dpath = rec.get("derived_path")
            if dpath and dpath not in self.attached_file_paths:
                self.attached_file_paths.append(dpath)
        for h in (snapshot.get("reconstruction_hashes") or []):
            if h:
                self.reconstruction_hashes.add(h)
        for h in (snapshot.get("processed_recon_inputs") or []):
            if h:
                self.processed_recon_inputs.add(h)

        counts["endpoints"] = len(self.discovered_endpoints)
        counts["headers"] = len(self.extracted_headers)
        counts["commands"] = len(self.executed_commands_dedup)
        counts["transcript_lines"] = sum(len(v) for v in self.agent_transcripts.values())
        return counts

    def reconcile_target(self, persisted_target: str) -> Dict[str, Any]:
        """Phase 7 (STEP 2/5) — reconcile the authoritative target on resume.

        The blackboard is constructed with the CURRENT target (``self.target_scope``);
        this compares it against the target the resumed snapshot was taken against. If
        they differ, the current target stays authoritative (new commands already use
        it) and endpoints rehydrated from the OLD host are dropped so a stale address
        can neither mislead the agents nor be executed against. Non-fatal and
        deterministic. Returns a small summary for logging/broadcast.
        """
        try:
            from backend.swarm.target_reconciliation import reconcile_target, references_stale_host
            recon = reconcile_target(self.target_scope, persisted_target or "")
        except Exception:
            return {"changed": False, "dropped_endpoints": 0}
        if not recon.changed:
            return {"changed": False, "dropped_endpoints": 0}
        before = len(self.discovered_endpoints)
        self.discovered_endpoints = {
            ep for ep in self.discovered_endpoints
            if not references_stale_host(ep, recon.stale_hosts)
        }
        # A rejection signal captured against the old target no longer applies.
        self.last_target_rejection = ""
        return {
            "changed": True,
            "previous": recon.previous,
            "authoritative": recon.authoritative,
            "stale_hosts": recon.stale_hosts,
            "dropped_endpoints": before - len(self.discovered_endpoints),
        }

    def _compute_progress(self) -> int:
        """Progress: 100 on flag capture, else a soft function of agent activity
        (distinct executed commands) capped at 90 — no fixed task pool to measure."""
        if self.flag_captured:
            return 100
        commands = len(self.executed_commands_dedup)
        return min(90, commands * 4)

    def _build_agent_states(self) -> List[Dict[str, Any]]:
        """Live worker fleet state for /api/agents and AGENT_UPDATE events."""
        states = []
        runtime_seconds = int(time.time() - self.started_ts)
        for worker_id, ws_ in self.worker_states.items():
            states.append({
                **ws_,
                "worker_id": worker_id,
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "runtime_seconds": runtime_seconds
            })
        return states

    async def update_worker_state(self, worker_id: str, **fields):
        """Update a worker's live telemetry row (status, current task, counts...)."""
        async with self._lock:
            state = self.worker_states.setdefault(worker_id, {
                "status": "IDLE",
                "current_task": "",
                "current_capability": "",
                "selected_model": "",
                "last_tool": "",
                "last_result": "",
                "commands_run": 0,
                "failures": 0,
                "last_activity": time.time(),
            })
            state.update(fields)
            state["last_activity"] = time.time()
            self.last_activity_ts = time.time()

    async def record_tool_execution(self, worker_id: str, command: str, res, privilege_level: str = "SAFE", approved: bool = True):
        """Persist a swarm tool execution to ToolExecutionModel so terminal history survives restarts/refreshes."""
        try:
            db = SessionLocal()
            try:
                status = "SUCCESS" if res.exit_code == 0 else ("FAILED" if res.status != "TIMEOUT" else "TIMEOUT")
                exec_row = ToolExecutionModel(
                    run_id=self.run_id,
                    agent=worker_id,
                    tool_name=getattr(res, "tool_name", "bash") or "bash",
                    capability="swarm_" + (worker_id or "worker"),
                    command=(command or "")[:2000],
                    privilege_level=privilege_level,
                    approved=approved,
                    status=status,
                    stdout=(getattr(res, "stdout", "") or "")[:4000],
                    stderr=(getattr(res, "stderr", "") or "")[:4000],
                    exit_code=res.exit_code,
                    duration_ms=float(getattr(res, "duration_ms", 0.0) or 0.0)
                )
                db.add(exec_row)
                db.commit()
            except Exception as e:
                logger.debug(f"[SwarmBlackboard] Tool execution persist skip: {e}")
            finally:
                db.close()
        except Exception:
            pass

    def record_agent_step(self, agent_id: str, command: str = "", output: str = "", note: str = ""):
        """Append one agent step to its transcript + shared execution history.

        Feeds both build_history_context() (cross-agent awareness in the prompt)
        and snapshot_agent_records() (the checkpoint report). Verbatim — no
        paraphrasing, so the report's factual fields stay un-fabricated.
        """
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        parts = [f"[{ts}]"]
        if command:
            parts.append(f"CMD: {command}")
        if output:
            parts.append(f"OUT: {output[:600]}")
        if note:
            parts.append(note)
        self.agent_transcripts.setdefault(agent_id, []).append(" ".join(parts))
        self.execution_history.append({
            "agent": agent_id, "command": command,
            "output": (output or "")[:2000], "ts": ts, "note": note,
        })

    def build_history_context(self, agent_id: str) -> str:
        """Shared blackboard state + this agent's and peers' recent steps, injected
        into the full-context prompt each turn (replaces the old task-pool routing)."""
        lines: List[str] = []
        if self.discovered_endpoints:
            lines.append("Discovered endpoints: " + ", ".join(sorted(self.discovered_endpoints)[:15]))
        if self.extracted_headers:
            lines.append("Known / exploit headers: " + "; ".join(f"{k}: {v}" for k, v in list(self.extracted_headers.items())[:10]))
        if self.observed_cookies:
            lines.append("Cookies: " + "; ".join(f"{k}={v}" for k, v in list(self.observed_cookies.items())[:8]))
        if self.deobfuscated_secrets:
            lines.append("Decoded secrets: " + "; ".join(str(s)[:120] for s in self.deobfuscated_secrets[:6]))
        if self.candidate_usernames:
            lines.append("Candidate usernames discovered (try these before generic 'admin'): " + ", ".join(sorted(self.candidate_usernames)))
        if self.candidate_tokens:
            lines.append("Candidate tokens: " + ", ".join(list(self.candidate_tokens)[:6]))
        if self.last_target_rejection:
            lines.append(f"Last target rejection signal: {self.last_target_rejection} — reconcile this with the evidence above (e.g. a 'user not found' means try a discovered username).")
        if self.flag_candidates:
            lines.append("Unverified flag candidates so far (MUST be reproduced from real output before accepting): "
                         + ", ".join(c.get("flag", "") for c in self.flag_candidates[:5]))
        # Strategy-exhaustion awareness: every agent sees the global ban list and
        # pivot directive so no worker independently rediscovers the dead end.
        if self.exhausted_strategies:
            lines.append(
                "[STRATEGY GATE] Exhausted approaches — do NOT repeat these: "
                + ", ".join(sorted(self.exhausted_strategies))
            )
        if self.pivot_directive and self.pivot_directive != "__pending__":
            lines.append(f"[REQUIRED PIVOT] {self.pivot_directive}")
        # Escalation: reconstructed derived artifacts that still need analysis. This is
        # the shared-channel form of an `analyze_derived_artifact` task (WHAT/HOW/WHETHER)
        # — the swarm must not conclude "no flag" while any of these is un-analyzed.
        pending_derived = [d for d in self.derived_artifacts
                           if d.get("derived_path") and not d.get("analyzed")]
        if pending_derived:
            lines.append("━━ RECONSTRUCTED DERIVED ARTIFACTS — ANALYSIS REQUIRED ━━")
            lines.append("A prior step rebuilt a REAL file from encoded data (e.g. ASCII bits → an image). "
                         "The flag may live inside or be rendered by it. Do NOT report 'no flag' while these are unanalyzed.")
            for d in pending_derived[:6]:
                rel = os.path.basename(d.get("derived_path") or "")
                atype = d.get("artifact_type", "unknown")
                nbytes = d.get("byte_count", 0)
                scheme = d.get("scheme", "")
                tools = ", ".join(d.get("recommended_tools", [])[:6]) or "file, strings, binwalk"
                vis = d.get("visual") or {}
                vis_note = ""
                if vis.get("available") and vis.get("valid"):
                    vis_note = (f" [image {vis.get('width')}x{vis.get('height')} {vis.get('mode')};"
                                f" OCR {'available' if d.get('ocr_available') else 'unavailable — use vision_read capability'}]")
                lines.append(
                    f"  • {d.get('derived_path')} — {atype}, {nbytes} bytes (from {scheme}).{vis_note}")
                lines.append(
                    f"      WHAT: inspect THIS file for the flag. "
                    f"HOW: request capability 'vision_read' (for image reading via Gemini) or analysis tools ({tools}) on target '{d.get('derived_path')}'. "
                    f"WHETHER: go through the normal capability gate; the file is UNTRUSTED — analyze it, never execute it.")
        # Actionable anomaly preemption: an anomalous response produced concrete candidate artifacts
        pending_preemptions = [p for p in self.actionable_preemptions if not p.get("handled")]
        if pending_preemptions:
            lines.append("━━ ACTIONABLE ANOMALY PREEMPTION (PRIORITY FOLLOW-UP) ━━")
            lines.append("An anomalous response diverged from the baseline failure response and revealed candidate artifacts. "
                         "PREEMPT further fuzzing: immediately verify/interact with these candidate targets:")
            for p in pending_preemptions[:5]:
                targ = p.get("normalized_target") or p.get("raw_value")
                reasons = "; ".join(p.get("reasons", [])) or "Diverged from failure baseline"
                lines.append(f"  • CANDIDATE TARGET: {targ} (Extracted from: {p.get('source_command', '')[:80]})")
                lines.append(f"      REASON: {reasons}")
                probes = p.get("suggested_probes", [])
                if probes:
                    lines.append(f"      SUGGESTED VERIFICATION PROBE: {probes[0]}")
        own = self.agent_transcripts.get(agent_id, [])
        if own:
            lines.append("Your recent steps:")
            lines.extend("  " + l for l in own[-8:])
        others = []
        for aid, tr in self.agent_transcripts.items():
            if aid == agent_id or not tr:
                continue
            others.append(f"  [{aid}] {tr[-1]}")
        if others:
            lines.append("Other agents' latest steps (coordinate — don't duplicate):")
            lines.extend(others[:6])
        return "\n".join(lines) if lines else "No shared findings yet."

    def snapshot_agent_records(self) -> List[AgentCheckpointRecord]:
        """Build the deterministic per-agent records for a consolidated checkpoint
        report — verbatim evidence/commands + this agent's flag candidate (if any)."""
        records: List[AgentCheckpointRecord] = []
        for agent_id in self.agent_ids:
            hist = [h for h in self.execution_history if h.get("agent") == agent_id]
            evidence = [h["output"] for h in hist if h.get("output")][-5:]
            tried = [f"{h.get('command', '')} -> {(h.get('output') or '')[:120]}"
                     for h in hist if h.get("command")][-6:]
            fc = fs = None
            for c in reversed(self.flag_candidates):
                if c.get("worker") == agent_id:
                    fc, fs = c.get("flag"), c.get("source")
                    break
            records.append(AgentCheckpointRecord(
                agent_id=agent_id,
                evidence=evidence,
                tried=tried,
                transcript="\n".join(self.agent_transcripts.get(agent_id, [])),
                flag_candidate=fc,
                flag_source=fs,
            ))
        return records

    async def _persist_progress_if_due(self, force: bool = False):
        """Throttled DB persist of mission plan (task statuses) + progress so the UI is reload-safe."""
        now = time.time()
        if not force and (now - self.last_persist_ts) < self.persist_interval:
            return
        self.last_persist_ts = now
        try:
            db = SessionLocal()
            try:
                ch = db.query(ChallengeModel).filter(ChallengeModel.id == self.challenge_id).first()
                if ch:
                    ch.mission_plan = self._build_mission_plan()
                    ch.progress = self._compute_progress()
                    db.commit()
            except Exception as e:
                logger.debug(f"[SwarmBlackboard] Progress persist skip: {e}")
            finally:
                db.close()
        except Exception:
            pass

    async def _broadcast_blackboard(self):
        try:
            total_tasks = len(self.task_pool)
            completed_tasks = sum(1 for t in self.task_pool.values() if t.status == "COMPLETED")
            progress = self._compute_progress()
            mission_plan = self._build_mission_plan()

            # Broadcast plan update so frontend Todo List / Mission Plan renders live
            await ws_manager.broadcast({
                "event": "PLAN_UPDATED",
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "plan": mission_plan
            })

            # Broadcast progress update
            await ws_manager.broadcast({
                "event": "PROGRESS_UPDATED",
                "challenge_id": self.challenge_id,
                "progress": progress
            })

            # Broadcast live worker fleet state
            await ws_manager.broadcast({
                "event": "AGENT_UPDATE",
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "agents": self._build_agent_states()
            })

            # Broadcast swarm blackboard stats
            await ws_manager.broadcast({
                "event": "SWARM_BLACKBOARD_UPDATE",
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "endpoints_count": len(self.discovered_endpoints),
                "headers": self.extracted_headers,
                "secrets_count": len(self.deobfuscated_secrets),
                "pending_tasks": sum(1 for t in self.task_pool.values() if t.status == "PENDING"),
                "completed_tasks": completed_tasks,
                "flag_captured": bool(self.flag_captured),
                "flag_candidates_count": len(self.flag_candidates)
            })

            # Throttled DB persist keeps the todo list / progress reload-safe
            await self._persist_progress_if_due()
        except Exception:
            pass

class SwarmOrchestrator:
    """Manages the lifecycle, workers, and blackboard of an active parallel swarm."""

    def __init__(self):
        self.active_swarms: Dict[str, SwarmBlackboard] = {}

    async def run_swarm(
        self,
        run_id: str,
        challenge_id: str,
        target_scope: str,
        working_directory: str,
        category: str = "WEB",
        difficulty: str = "EASY",
        resume: bool = False,
        challenge_name: str = "",
        platform: str = "",
        description: str = "",
        flag_pattern: str = "",
        max_iterations: int = 0,
        max_minutes: int = 0,
        attached_file_paths: Optional[List[str]] = None,
        instance_expiry_ts: Optional[float] = None,
    ):
        """Dispatch N general-purpose full-context agents on the target (flexible-agent
        engine; no fixed recon/crypto/exploit roles or hardcoded task checklist)."""
        # Register the mirrored challenge-log path FIRST before any log writes, so
        # every append (including swarm start) lands under logs/<Platform>/<Category>/
        # <Difficulty>/<Name>/ instead of a flat file.
        try:
            from backend.utils.challenge_paths import register_challenge_log_path
            register_challenge_log_path(challenge_id, platform, category, difficulty,
                                        challenge_name or challenge_id)
        except Exception as exc:
            logger.error(f"[SwarmOrchestrator] Failed to register challenge log path for challenge '{challenge_id}': {exc}", exc_info=True)

        logger.info(f"[SwarmOrchestrator] 🚀 Starting flexible-agent swarm for Challenge '{challenge_id}' on '{target_scope}' (resume={resume})")
        _append_to_challenge_log(challenge_id, "orchestrator", f"Swarm {'resuming' if resume else 'starting'} | target={target_scope} | category={category} | difficulty={difficulty}")

        # Engage OS Keep-Awake lock
        keep_awake_manager.acquire(reason=f"Swarm Challenge {challenge_id}")

        board = SwarmBlackboard(challenge_id, run_id, target_scope)
        self.active_swarms[run_id] = board

        # Challenge context → baked into every agent's full-context prompt.
        board.challenge_name = challenge_name or challenge_id
        board.platform = platform
        board.category = category
        board.difficulty = difficulty
        board.description = description
        if flag_pattern:
            board.flag_pattern = flag_pattern
        if max_iterations:
            board.max_iterations = max_iterations
        if max_minutes:
            board.max_minutes = max_minutes
        board.attached_file_paths = list(attached_file_paths or [])
        board.instance_expiry_ts = instance_expiry_ts

        db = SessionLocal()
        try:
            # Update Run status in DB
            run_obj = db.query(RunModel).filter(RunModel.id == run_id).first()
            if run_obj:
                run_obj.status = "RUNNING"
                run_obj.started_at = datetime.now(timezone.utc)
                db.commit()

            # Pre-warmed Turbo Recon (Initial seed — non-fatal)
            try:
                turbo_data = await turbo_recon.start_turbo_recon(challenge_id, target_scope, category.lower(), working_directory=working_directory)
            except Exception as recon_err:
                logger.warning(f"[SwarmOrchestrator] Turbo recon seed failed (non-fatal): {recon_err}")
                _append_to_challenge_log(challenge_id, "orchestrator", f"Turbo recon skipped: {recon_err}")
                turbo_data = turbo_recon.get_cached_recon(challenge_id)
            if turbo_data:
                for ep in turbo_data.get("endpoints", []):
                    board.discovered_endpoints.add(ep)
                for h_k, h_v in turbo_data.get("headers", {}).items():
                    board.extracted_headers[h_k] = str(h_v)
                _append_to_challenge_log(challenge_id, "orchestrator", f"Turbo recon seeded: {len(board.discovered_endpoints)} endpoints, {len(board.extracted_headers)} headers")

            # Resume: rehydrate prior blackboard state + task pool so the swarm
            # continues aware of earlier work instead of repeating it. Non-fatal —
            # a rehydrate failure just falls through to a fresh start.
            if resume:
                try:
                    ch_row = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
                    mp = (ch_row.mission_plan if ch_row else None) or {}
                    snapshot = mp.get("blackboard_state")
                    prior_tasks = mp.get("tasks")
                    rows = (db.query(ToolExecutionModel.command)
                            .join(RunModel, ToolExecutionModel.run_id == RunModel.id)
                            .filter(RunModel.challenge_id == challenge_id).all())
                    prior_cmds = [r[0] for r in rows if r and r[0]]
                    counts = board.load_snapshot(snapshot, prior_cmds, prior_tasks)
                    # Phase 7 (STEP 2/5) — if the target changed since the snapshot, keep
                    # the current one authoritative and drop stale host-bound endpoints so
                    # a respawned/edited instance address can't silently drive execution.
                    recon = board.reconcile_target((snapshot or {}).get("target_scope", ""))
                    if recon.get("changed"):
                        _append_to_challenge_log(
                            challenge_id, "orchestrator",
                            f"[TARGET CHANGED] '{recon['previous']}' -> '{recon['authoritative']}' — "
                            f"dropped {recon['dropped_endpoints']} stale endpoint(s); current target "
                            f"is authoritative")
                        await ws_manager.broadcast({
                            "event": "TARGET_CHANGED", "challenge_id": challenge_id, "run_id": run_id,
                            "old_target": recon["previous"], "new_target": recon["authoritative"],
                            "dropped_endpoints": recon["dropped_endpoints"],
                            "stale_hosts": recon.get("stale_hosts", []),
                        })
                    _append_to_challenge_log(
                        challenge_id, "orchestrator",
                        f"Resuming from prior progress: {counts['endpoints']} endpoints, "
                        f"{counts['commands']} prior commands, {counts['transcript_lines']} restored "
                        f"transcript lines (agents resume their reasoning thread, not just counters)"
                    )
                    logger.info(f"[SwarmOrchestrator] Resuming challenge '{challenge_id}' with rehydrated state: {counts}")
                except Exception as rehydrate_err:
                    logger.warning(f"[SwarmOrchestrator] Resume rehydrate failed (starting fresh): {rehydrate_err}")

            # ── Artifact acquisition (Part 2) — deterministic, before any agent ──
            # Binary-safe: downloads/uploads never pass through tool_manager's
            # text-decoding capture. If binary, the prompt flips to BINARY MODE.
            try:
                board.env_info = environment_detector.detect_environment()
            except Exception as env_err:
                logger.warning(f"[SwarmOrchestrator] Env detect failed (non-fatal): {env_err}")
                board.env_info = {}
            try:
                targets = [t.strip() for t in target_scope.split("+") if t.strip()]
                manifest = await acquire_artifacts(targets, working_directory, board.attached_file_paths)
                for note in manifest.notes:
                    _append_to_challenge_log(challenge_id, "orchestrator", f"[artifact] {note}")
                for p in manifest.saved_paths:
                    if p not in board.attached_file_paths:
                        board.attached_file_paths.append(p)
                if manifest.primary is not None:
                    board.artifact_classification = manifest.primary
                    _append_to_challenge_log(
                        challenge_id, "orchestrator",
                        f"BINARY ARTIFACT MODE engaged: {manifest.primary.artifact_type} "
                        f"@ {manifest.primary.safe_file_path or '(header-only)'}")
            except Exception as art_err:
                logger.warning(f"[SwarmOrchestrator] Artifact acquisition failed (non-fatal): {art_err}")

            # ── Encoded-artifact pre-scan — deterministic, before any agent ──────
            # If an attached artifact is itself an ENCODED file (e.g. a text file of
            # ASCII 0/1 that reconstructs to a JPEG), rebuild + preserve + escalate it
            # now so it enters the pipeline as a first-class analyzable input.
            try:
                await self._scan_attached_for_encoded(board, working_directory)
            except Exception as recon_err:
                logger.warning(f"[SwarmOrchestrator] Encoded-artifact pre-scan failed (non-fatal): {recon_err}")

            # ── Memory retrieval phase (§6) — ONE shared retrieval before agents ──
            # OBSERVE → RETRIEVE MEMORY: use the seeded recon + challenge context to
            # pull a few relevant past experiences / reference playbooks into a compact,
            # reference-only context shared by every agent. Agents still reason and
            # decide what to run (§8, §22). Fast + non-fatal (§17).
            try:
                evidence_parts = [board.description or ""]
                evidence_parts.extend(sorted(board.discovered_endpoints)[:15])
                evidence_parts.extend(f"{k}: {v}" for k, v in list(board.extracted_headers.items())[:12])
                if board.artifact_classification is not None:
                    evidence_parts.append(f"binary artifact {board.artifact_classification.artifact_type}")
                evidence_text = "\n".join(p for p in evidence_parts if p)
                technologies = experience_extractor._detect_technologies(evidence_text)
                mem_context, memories = memory_retriever.retrieve_and_format(
                    evidence=evidence_text,
                    category=board.category,
                    technologies=technologies,
                    query=f"{board.category} {board.challenge_name}",
                    top_k=int(getattr(settings, "MEMORY_RETRIEVAL_TOP_K", 6) or 6),
                )
                board.memory_context = mem_context
                board.retrieved_memory_ids = [m.id for m in memories if m.kind == "experience" and m.id]
                if memories:
                    experience_memory.record_retrieval(board.retrieved_memory_ids, run_id, challenge_id)
                    _append_to_challenge_log(
                        challenge_id, "orchestrator",
                        f"[MEMORY] Retrieved {len(memories)} relevant memories "
                        f"({len(board.retrieved_memory_ids)} FORGE experiences) for shared agent context")
                    await ws_manager.broadcast({
                        "event": "MEMORY_RETRIEVED", "challenge_id": challenge_id, "run_id": run_id,
                        "count": len(memories), "experiences": len(board.retrieved_memory_ids),
                        "techniques": [m.technique for m in memories][:8],
                    })
                else:
                    _append_to_challenge_log(challenge_id, "orchestrator",
                                             "[MEMORY] No relevant prior experience found (cold start)")
            except Exception as mem_err:
                logger.warning(f"[SwarmOrchestrator] Memory retrieval failed (non-fatal): {mem_err}")

            # ── Spawn N general-purpose agents (config-driven; not roles) ──────────
            pool_size = self._resolve_pool_size(board.env_info)
            board.agent_ids = [f"agent_{i+1}" for i in range(pool_size)]
            # Capabilities rotate only for provider diversity/throughput across the
            # free-tier chain — every agent gets the SAME full-context prompt.
            cap_cycle = ["web_analysis", "code_analysis", "general_reasoning",
                         "reverse_engineering", "fast_reasoning"]
            agents = [
                self._agent_worker(aid, board, working_directory, cap_cycle[i % len(cap_cycle)])
                for i, aid in enumerate(board.agent_ids)
            ]

            # Run agents concurrently until flag capture, all-budget-exhaustion, or
            # cancellation. ensure_future (not create_task) wraps the gather Future so
            # it can sit in asyncio.wait() alongside the flag + checkpoint tasks.
            worker_task = asyncio.ensure_future(asyncio.gather(*agents, return_exceptions=True))
            flag_task = asyncio.create_task(board.flag_event.wait())
            checkpoint_task = asyncio.create_task(self._checkpoint_coordinator(board, working_directory))

            _append_to_challenge_log(challenge_id, "orchestrator", f"{pool_size} flexible-agent workers + checkpoint coordinator dispatched")

            # Wait for flag event, all agents finishing, or coordinator exit.
            done, pending = await asyncio.wait(
                [flag_task, worker_task, checkpoint_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Stop everything and release any coordinator hard-wait so it can exit.
            board.is_stopped = True
            board.checkpoint_response_event.set()
            for p in pending:
                p.cancel()

            # Check if workers raised exceptions
            if worker_task in done:
                try:
                    results = worker_task.result()
                    for i, result in enumerate(results):
                        if isinstance(result, Exception):
                            logger.error(f"[SwarmOrchestrator] Worker {i} raised exception: {result}")
                            _append_to_challenge_log(challenge_id, f"worker_{i}", f"EXCEPTION: {result}")
                except Exception:
                    pass

            # Finalize status in Database
            run_obj = db.query(RunModel).filter(RunModel.id == run_id).first()
            ch_obj = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()

            if board.flag_captured:
                if run_obj:
                    run_obj.status = "COMPLETED"
                    run_obj.completed_at = datetime.now(timezone.utc)
                if ch_obj:
                    ch_obj.status = "SOLVED"
                    ch_obj.flag = board.flag_captured
                    ch_obj.progress = 100
                    ch_obj.completed_at = datetime.now(timezone.utc)
                db.commit()
                await board._persist_progress_if_due(force=True)
                _append_to_challenge_log(challenge_id, "orchestrator", f"🏁 SOLVED! Flag: {board.flag_captured}")
                logger.info(f"[SwarmOrchestrator] 🏁 Swarm SOLVED challenge '{challenge_id}'! Flag: {board.flag_captured}")
                await ws_manager.broadcast({
                    "event": "RUN_COMPLETED",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "flag": board.flag_captured
                })

                # LEARN → STORE: distill this solve into generalized experience (§3, §19).
                await self._learn_from_run(board, outcome="success")
            elif board.pause_requested:
                # Operator paused — persist a resume snapshot and finalize as PAUSED
                # (resumable), never FAILED. Progress and blackboard state are kept.
                if run_obj:
                    run_obj.status = "PAUSED"
                if ch_obj:
                    ch_obj.status = "PAUSED"
                db.commit()
                await board._persist_progress_if_due(force=True)
                _append_to_challenge_log(challenge_id, "orchestrator", "Swarm PAUSED by operator — state saved for resume")
                logger.info(f"[SwarmOrchestrator] Swarm PAUSED for '{challenge_id}' (resumable)")
                await ws_manager.broadcast({
                    "event": "RUN_PAUSED",
                    "challenge_id": challenge_id,
                    "run_id": run_id
                })
            else:
                # If a reconstructed artifact was never analyzed, say so explicitly rather
                # than reporting a clean "no flag" — the run stalled with pending work, and
                # the derived artifact remains on the board for the next resume to pick up
                # (requirement #11/#12/#14).
                if board.has_unanalyzed_derived() and not board.stall_reason:
                    pending = [os.path.basename(d.get("derived_path") or "")
                               for d in board.derived_artifacts
                               if d.get("derived_path") and not d.get("analyzed")]
                    board.stall_reason = (
                        f"DERIVED_ANALYSIS_PENDING — reconstructed artifact(s) not yet analyzed: "
                        f"{', '.join(pending[:5])}")
                stall_msg = board.stall_reason or "Swarm finished without a verified flag (task pool exhausted)"
                if run_obj:
                    run_obj.status = "FAILED"
                    run_obj.completed_at = datetime.now(timezone.utc)
                if ch_obj:
                    ch_obj.status = "FAILED"
                    ch_obj.completed_at = datetime.now(timezone.utc)
                db.commit()
                await board._persist_progress_if_due(force=True)
                candidates_summary = ", ".join([c["flag"] for c in board.flag_candidates]) if board.flag_candidates else "none"
                _append_to_challenge_log(challenge_id, "orchestrator", f"Swarm finished without verified flag. {stall_msg} Candidates: {candidates_summary}")
                logger.info(f"[SwarmOrchestrator] Swarm ended without flag for '{challenge_id}': {stall_msg}")
                await ws_manager.broadcast({
                    "event": "RUN_STALLED",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "reason": stall_msg,
                    "candidates": [c["flag"] for c in board.flag_candidates]
                })

                # LEARN → STORE: a failed/stalled run is still experience (Part 8). Record
                # what was attempted and why it stalled so future missions avoid the same
                # dead ends, and down-weight any retrieved memory that did not help.
                await self._learn_from_run(board, outcome="failed")

            # Generate structured report
            try:
                report_generator.generate_final_report(challenge_id=challenge_id, run_id=run_id, db=db)
            except Exception as e:
                logger.warning(f"[SwarmOrchestrator] Report generation skip: {e}")

        except Exception as e:
            logger.error(f"[SwarmOrchestrator] Swarm execution exception: {e}\n{traceback.format_exc()}")
            _append_to_challenge_log(challenge_id, "orchestrator", f"FATAL EXCEPTION: {e}\n{traceback.format_exc()}")
            # Update run status to ERROR so it's visible in the UI
            try:
                run_obj = db.query(RunModel).filter(RunModel.id == run_id).first()
                if run_obj:
                    run_obj.status = "ERROR"
                    run_obj.completed_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception:
                pass
        finally:
            db.close()
            self.active_swarms.pop(run_id, None)
            # Release OS Keep-Awake lock
            keep_awake_manager.release(reason=f"Swarm Challenge {challenge_id} Ended")
            _append_to_challenge_log(challenge_id, "orchestrator", "Swarm shutdown complete")

    async def request_pause(self, challenge_id: str) -> bool:
        """Gracefully pause every active swarm for a challenge.

        Signals workers to stop after their current step (no hard cancel, so no
        orphaned tasks) and force-persists a resume snapshot. run_swarm then
        finalizes the run as PAUSED. Returns True if an active swarm was found.
        """
        paused_any = False
        for board in list(self.active_swarms.values()):
            if board.challenge_id == challenge_id and not board.is_stopped:
                board.pause_requested = True
                board.is_stopped = True
                # Release a coordinator that is hard-waiting at a checkpoint so it
                # can observe is_stopped and exit cleanly instead of hanging.
                board.checkpoint_response_event.set()
                try:
                    await board._persist_progress_if_due(force=True)
                except Exception:
                    pass
                paused_any = True
                _append_to_challenge_log(challenge_id, "orchestrator", "Pause requested by operator — workers finishing current step")
                logger.info(f"[SwarmOrchestrator] Pause requested for challenge '{challenge_id}'")
        return paused_any

    def _resolve_pool_size(self, env_info: Dict[str, Any]) -> int:
        """How many general-purpose agents to spawn. Config AGENT_POOL_SIZE (int),
        or the string 'auto' -> min(cpu_cores-1, 4). Clamped to [1, 6]."""
        raw = getattr(settings, "AGENT_POOL_SIZE", 3)
        if isinstance(raw, str) and raw.strip().lower() == "auto":
            cores = int(env_info.get("cpu_cores") or 2)
            size = min(max(cores - 1, 1), 4)
        else:
            try:
                size = int(raw)
            except (TypeError, ValueError):
                size = 3
        return max(1, min(size, 6))

    def _build_agent_context(self, board: "SwarmBlackboard", workdir: str, agent_id: str) -> AgentContext:
        """Full challenge context for one agent this turn (identical template for all;
        only history_context + injected_directive differ per agent)."""
        pivot = board.pivot_directive if board.pivot_directive != "__pending__" else ""
        return make_context_from_env(
            env_info=board.env_info or {},
            challenge_name=board.challenge_name,
            platform=board.platform,
            category=board.category,
            difficulty=board.difficulty,
            description=board.description,
            target_url=board.target_scope,
            working_directory=workdir,
            max_iterations=board.max_iterations,
            max_minutes=board.max_minutes,
            flag_pattern=board.flag_pattern,
            attached_file_paths=list(board.attached_file_paths),
            artifact_classification=board.artifact_classification,
            history_context=board.build_history_context(agent_id),
            injected_directive=board.agent_directives.get(agent_id, ""),
            memory_context=board.memory_context,
            exhausted_strategies=list(board.exhausted_strategies),
            pivot_directive=pivot,
        )

    async def _agent_worker(self, agent_id: str, board: "SwarmBlackboard", workdir: str, capability: str):
        """One general-purpose, full-context agent running a budget-bounded ReAct
        loop. No fixed role: it reasons about THIS challenge from the shared
        blackboard context and decides its own next command / solver / flag."""
        logger.info(f"[Agent] {agent_id} started (capability route: {capability}).")
        _append_to_challenge_log(board.challenge_id, agent_id, f"Agent started (provider route: {capability})")
        board.agent_started_ts[agent_id] = time.time()
        board.agent_iterations[agent_id] = 0
        board.agent_paused_seconds[agent_id] = 0.0
        board.agent_local_fail_streak[agent_id] = 0
        await board.update_worker_state(agent_id, status="RUNNING", current_capability=capability,
                                        selected_model="auto (capability chain)", current_task="Booting agent")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 5
        consecutive_duplicates = 0
        MAX_CONSECUTIVE_DUPLICATES = 3

        while not board.flag_captured and not board.is_stopped:
            # Hard-pause at a checkpoint: idle until the operator response resumes us.
            # Time spent idle here is accumulated and subtracted from the work budget so a
            # multi-minute operator pause never counts against the M working-minute clock.
            if board.checkpoint_pause:
                pause_started = time.time()
                await board.update_worker_state(agent_id, status="IDLE", current_task="Paused at operator checkpoint")
                while board.checkpoint_pause and not board.is_stopped and not board.flag_captured:
                    await asyncio.sleep(1.0)
                board.agent_paused_seconds[agent_id] = (
                    board.agent_paused_seconds.get(agent_id, 0.0) + (time.time() - pause_started)
                )
                continue

            # Instance-expiry hard wind-down — the CTF platform kills the target on its own
            # timer (as short as ~15 min). Stop and report best findings a buffer before that
            # deadline rather than being cut off mid-command.
            if board.instance_expiry_ts:
                buffer = int(getattr(settings, "INSTANCE_WINDDOWN_BUFFER_SECONDS", 30))
                if time.time() >= board.instance_expiry_ts - buffer:
                    reason = "WINDING_DOWN: platform instance expiry approaching — reporting best findings"
                    board.record_agent_step(agent_id, note=reason)
                    _append_to_challenge_log(board.challenge_id, agent_id, reason)
                    await board.update_worker_state(agent_id, status="DONE", current_task=reason)
                    if not board.stall_reason:
                        board.stall_reason = "Platform instance expiry reached"
                    break

            # Budget gate — N iterations OR M working-minutes, whichever first. Working-minutes
            # exclude checkpoint-pause idle time (see agent_paused_seconds / #3).
            elapsed_min = _effective_elapsed_minutes(
                board.agent_started_ts[agent_id], time.time(),
                board.agent_paused_seconds.get(agent_id, 0.0),
            )
            iters = board.agent_iterations.get(agent_id, 0)
            if iters >= board.max_iterations or elapsed_min >= board.max_minutes:
                reason = (f"BUDGET_EXHAUSTED (iterations={iters}/{board.max_iterations}, "
                          f"minutes={elapsed_min:.1f}/{board.max_minutes})")
                board.record_agent_step(agent_id, note=reason)
                _append_to_challenge_log(board.challenge_id, agent_id, reason)
                await board.update_worker_state(agent_id, status="DONE", current_task=reason)
                if not board.stall_reason:
                    board.stall_reason = "Agents exhausted their iteration/time budget without a verified flag"
                break

            try:
                ctx = self._build_agent_context(board, workdir, agent_id)
                system_instruction, user_prompt = build_agent_prompt(ctx)
                await board.update_worker_state(agent_id, status="ANALYZING",
                                                current_task=f"Deciding next action (iter {iters + 1})")

                resp = await model_router.route_request(
                    prompt=user_prompt,
                    system_instruction=system_instruction,
                    capability=capability,
                )

                if resp.is_refusal:
                    consecutive_errors += 1
                    _append_to_challenge_log(board.challenge_id, agent_id, f"Provider exhausted/refusal: {resp.refusal_reason[:100]}")
                    await board.update_worker_state(agent_id, status="IDLE", failures=consecutive_errors,
                                                    current_task=f"Provider error: {resp.refusal_reason[:80]}")
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        await asyncio.sleep(30)
                        consecutive_errors = 0
                    else:
                        await asyncio.sleep(5)
                    continue  # provider errors do not consume the iteration budget

                content = resp.content or ""
                model_name = getattr(resp, "model_name", capability)

                # ── Parse STRATEGY: tag (required from agent per system prompt Rule 7) ──
                strategy_label = "unknown"
                strategy_match = re.search(r"^STRATEGY:\s*(\w+)", content, re.MULTILINE | re.IGNORECASE)
                if strategy_match:
                    strategy_label = strategy_match.group(1).strip().lower()
                else:
                    board.record_agent_step(
                        agent_id,
                        note="[FORMAT WARNING] Missing STRATEGY: <label> line. Prefix your response with 'STRATEGY: <strategy_name>' to categorize your action.",
                    )
                    await board.update_worker_state(
                        agent_id,
                        status="RUNNING",
                        current_task="Restating action with valid STRATEGY: <label> line",
                    )

                # ── Parse the agent's single action per the prompt output contract ──
                if re.search(r"\bBUDGET_EXHAUSTED\b", content):
                    board.record_agent_step(agent_id, note=f"Model reported BUDGET_EXHAUSTED: {content[:200]}")
                    await board.update_worker_state(agent_id, status="DONE", current_task="Budget exhausted (model-reported)")
                    break

                # Explicit FLAG: line is an LLM claim → candidate only, never auto-captured.
                flag_line = re.search(r"FLAG:\s*(\S+)", content)
                if flag_line:
                    cand = flag_line.group(1).strip()
                    if FLAG_REGEX.search(cand) and not FALSE_FLAG_PATTERNS.search(cand):
                        await board.record_flag_candidate(cand, agent_id, "llm_reported")
                        # NOTE: the verified/rejected log entry is written inside
                        # record_flag_candidate() once the verifier returns its verdict.
                        # Do NOT add a duplicate record_agent_step here (Secondary Bug 3).

                # A Python solver block -> write solve.py and run it byte-safely.
                py_match = re.search(r"```python\s*\n(.*?)\n```", content, re.DOTALL)
                if py_match:
                    script = py_match.group(1)
                    import ast
                    try:
                        ast.parse(script)
                    except SyntaxError as se:
                        err_msg = f"SyntaxError in generated Python script (line {se.lineno}, col {se.offset}): {se.msg}"
                        board.record_agent_step(agent_id, note=err_msg)
                        _append_to_challenge_log(board.challenge_id, agent_id, err_msg)
                        board.agent_iterations[agent_id] = iters + 1
                        await board.update_worker_state(agent_id, status="RUNNING", current_task=f"Repairing syntax error: {se.msg}")
                        continue
                    # Content-hash the filename so a REVISED script actually runs (its command
                    # string differs), while a byte-identical retry still dedups. Without this,
                    # every revised solver reused one filename -> one command string -> silently
                    # skipped by the dedup set.
                    script_hash = hashlib.md5(script.encode("utf-8", "replace")).hexdigest()[:8]
                    solver_path = os.path.join(workdir, f"solve_{agent_id}_{script_hash}.py")
                    try:
                        with open(solver_path, "w", encoding="utf-8") as fh:
                            fh.write(script)
                    except OSError as werr:
                        werr_msg = f"Could not write solver: {werr}"
                        board.record_agent_step(agent_id, note=werr_msg)
                        _append_to_challenge_log(board.challenge_id, agent_id, werr_msg)
                        board.agent_iterations[agent_id] = iters + 1
                        continue
                    # Quote the path — the workspace path can contain spaces (e.g.
                    # .../WEB/EASY/Old Sessions), which otherwise splits into [Errno 2].
                    cmd = f'python "{solver_path}"'
                else:
                    cmd = self._extract_command(content)
                    if not cmd:
                        board.record_agent_step(agent_id, note="No executable command produced this turn")
                        _append_to_challenge_log(board.challenge_id, agent_id, "[NO PROGRESS] No executable command produced this turn")
                        board.agent_iterations[agent_id] = iters + 1
                        await board.update_worker_state(agent_id, status="RUNNING",
                                                        current_task="No command produced; re-planning")
                        await asyncio.sleep(1.0)
                        continue

                try:
                    await ws_manager.broadcast({
                        "event": "AI_DECISION", "challenge_id": board.challenge_id, "agent": agent_id,
                        "goal": f"{board.category} challenge next step", "capability": capability,
                        "result": cmd[:250], "confidence": 90, "model": model_name,
                    })
                except Exception:
                    pass

                try:
                    db = SessionLocal()
                    try:
                        dec_row = TrajectoryEventModel(
                            session_id=board.run_id or f"swarm-{board.challenge_id}",
                            run_id=board.run_id,
                            challenge_id=board.challenge_id,
                            agent_id=agent_id,
                            event_type="AI_DECISION",
                            action_type="command",
                            command=cmd[:2000],
                            tool_name=capability or "swarm",
                            decision_summary=f"{board.category} challenge next step",
                            strategy=f"Model: {model_name}",
                            result=cmd[:250],
                            model=model_name
                        )
                        db.add(dec_row)
                        db.commit()
                    except Exception as e:
                        logger.debug(f"[SwarmOrchestrator] AI decision persist skip: {e}")
                    finally:
                        db.close()
                except Exception:
                    pass


                board.agent_iterations[agent_id] = iters + 1

                if cmd in board.executed_commands_dedup:
                    board.record_agent_step(agent_id, command=cmd, note="skipped (already executed by the swarm)")
                    _append_to_challenge_log(board.challenge_id, agent_id, f"[DUPLICATE SKIPPED] {cmd[:150]}")
                    consecutive_duplicates += 1
                    if consecutive_duplicates >= MAX_CONSECUTIVE_DUPLICATES:
                        _append_to_challenge_log(
                            board.challenge_id, agent_id,
                            f"[DUPLICATE LOOP] {consecutive_duplicates} identical duplicates in a row — forcing pivot."
                        )
                        asyncio.create_task(_force_pivot_if_needed(board, agent_id, strategy_label))
                        consecutive_duplicates = 0
                    await asyncio.sleep(0.5)
                    continue

                # Bug 4 Pre-check: Blocked capabilities (sudo / missing binaries)
                cmd_tokens = cmd.strip().split()
                bin_name = os.path.basename(cmd_tokens[0]).lower() if cmd_tokens else ""
                if ("sudo" in board.blocked_capabilities and cmd.strip().startswith("sudo ")) or (f"cmd:{bin_name}" in board.blocked_capabilities):
                    board.record_agent_step(agent_id, command=cmd, note=f"[CAPABILITY BLOCKED] Command '{cmd[:80]}' uses a capability/tool blocked on first failure.")
                    await asyncio.sleep(0.5)
                    continue

                # Bug 1 Pre-check: Cross-agent recon deduplication (auth-preserving)
                recon_key = _normalize_recon_target(cmd)
                if recon_key and recon_key in board.recon_cache:
                    cached = board.recon_cache[recon_key]
                    board.record_agent_step(agent_id, command=cmd, output=cached, note="[RECON CACHE HIT] Result already fetched by another agent.")
                    _append_to_challenge_log(board.challenge_id, agent_id, f"[RECON CACHE HIT] {cmd[:100]}")
                    await asyncio.sleep(0.5)
                    continue

                # Bug 3 Pre-check: Blocked failure signature / command shape check
                cmd_shape = _normalize_command_shape(cmd)
                if cmd_shape in board.blocked_failure_sigs:
                    board.record_agent_step(agent_id, command=cmd, note=f"[SIGNATURE BLOCKED] Command shape '{cmd_shape}' is blocked after repeated failures.")
                    await asyncio.sleep(0.5)
                    continue

                # ── Strategy gate pre-check ────────────────────────────────────────────
                # Must run AFTER the dedup check so an exhausted-strategy command that is also a
                # duplicate still hits the (cheaper) dedup guard first.
                if strategy_label in board.exhausted_strategies:
                    _append_to_challenge_log(
                        board.challenge_id, agent_id,
                        f"[STRATEGY BLOCKED] '{strategy_label}' is already exhausted — "
                        f"forcing pivot (this command will not execute)."
                    )
                    board.record_agent_step(
                        agent_id, command=cmd,
                        note=f"[STRATEGY BLOCKED] Strategy '{strategy_label}' exhausted; skipping execution."
                    )
                    asyncio.create_task(_force_pivot_if_needed(board, agent_id, strategy_label))
                    await asyncio.sleep(0.5)
                    continue

                # ── Privilege gate ─────────────────────────────────────────────────────
                # classify/approve/wait/reconcile all live in the shared gate so this
                # path cannot drift from the other agent command paths that use it.
                # `bin_name` is deliberately recomputed here (NOT reused from the
                # lower-cased pre-check above) because the failure-blocking code below
                # keys board.blocked_capabilities on this exact value.
                bin_name = os.path.basename(cmd.strip().split()[0]) if cmd.strip() else ""
                priv_level = classify_command_privilege(cmd, bin_name)

                approved, decision, _sudo_pw_for_exec = await require_approval(
                    cmd=cmd,
                    agent_id=agent_id,
                    pending_approvals=board.pending_approvals,
                    broadcast_fn=ws_manager.broadcast,
                    challenge_id=board.challenge_id,
                    run_id=board.run_id,
                )
                # req_sudo is only consulted inside the sudo-stdin block below, which
                # additionally requires a password the gate returns only on approval —
                # so recomputing it here is behavior-identical to the previous inline gate.
                req_sudo = bool(re.search(r"\bsudo\b", cmd))

                if not approved:
                    _sudo_pw_for_exec = None  # discard on deny/timeout
                    status_str = "timed out" if decision is None else "denied"
                    _append_to_challenge_log(
                        board.challenge_id,
                        agent_id,
                        f"[PRIVILEGE {status_str.upper()}] level={priv_level} cmd={cmd[:150]}"
                    )
                    await asyncio.sleep(0.5)
                    continue

                board.executed_commands_dedup.add(cmd)
                consecutive_duplicates = 0
                _append_to_challenge_log(board.challenge_id, agent_id, f"Executing: {cmd[:200]}")

                # ── Sudo stdin injection (security: password never in command string) ────
                _stdin_data: Optional[str] = None
                _logged_cmd = cmd
                if req_sudo and approved and _sudo_pw_for_exec:
                    # Strip leading 'sudo' tokens and rebuild as 'sudo -S' so the
                    # password is read from stdin, never from a command-line argument.
                    _stripped = cmd.strip()
                    if _stripped.startswith("sudo "):
                        _inner_cmd = _stripped[5:].lstrip()
                        # Remove any -S that is already present to avoid duplication
                        if _inner_cmd.startswith("-S "):
                            _inner_cmd = _inner_cmd[3:].lstrip()
                    else:
                        _inner_cmd = _stripped
                    # Logged command shows the sudo form WITHOUT the password.
                    _logged_cmd = f"sudo -S {_inner_cmd}"
                    cmd = _logged_cmd  # use rewritten form for execution
                    _stdin_data = f"{_sudo_pw_for_exec}\n"
                    # Wipe the local password variable now that we've used it to
                    # build stdin; the password exists only inside _stdin_data for
                    # the duration of the subprocess call below.
                    _sudo_pw_for_exec = None

                try:
                    res = await tool_manager.execute_tool(
                        "bash", {"command": cmd}, timeout=25,
                        working_directory=workdir, canonical_target=board.target_scope,
                        stdin=_stdin_data,
                    )
                finally:
                    # Ensure the password bytes leave scope regardless of outcome.
                    _stdin_data = None
                output = res.stdout or res.stderr or ""
                if getattr(res, "execution_failure", False):
                    _append_to_challenge_log(board.challenge_id, agent_id,
                                             f"Execution failure ({getattr(res, 'failure_category', 'UNKNOWN')}): {res.stderr[:200]}")

                await board.record_tool_execution(agent_id, cmd, res, privilege_level=priv_level, approved=approved)
                board.record_agent_step(agent_id, command=cmd, output=output)

                # Bug 1 Store: populate recon cache on successful execution
                if recon_key and getattr(res, "exit_code", 1) == 0 and output.strip():
                    board.recon_cache[recon_key] = output[:1000]

                # Bug 4 Post-check: First-occurrence terminal failure blocking
                fail_cat = getattr(res, "failure_category", None)
                if getattr(res, "execution_failure", False):
                    if fail_cat == "PERMISSION_DENIED" and "sudo" in (cmd + " " + output).lower():
                        board.blocked_capabilities.add("sudo")
                        board.record_agent_step(agent_id, note="[CAPABILITY BLOCKED] sudo requires password/tty; blocked for remaining run.")
                    elif fail_cat == "COMMAND_NOT_FOUND" and bin_name:
                        board.blocked_capabilities.add(f"cmd:{bin_name}")
                        board.record_agent_step(agent_id, note=f"[CAPABILITY BLOCKED] Binary '{bin_name}' not found; blocked for remaining run.")

                    # Bug 3 Post-check: Track failure signature & block command shape on repeat limit
                    sig_key = _normalize_failure_signature(cmd, fail_cat or "EXEC_FAIL", res.stderr or output)
                    board.failure_signatures[sig_key] = board.failure_signatures.get(sig_key, 0) + 1
                    if board.failure_signatures[sig_key] >= 3:
                        if cmd_shape not in board.blocked_failure_sigs:
                            board.blocked_failure_sigs.add(cmd_shape)
                            board.record_agent_step(agent_id, note=f"[FAILURE REPEAT LIMIT] Command shape '{cmd_shape}' hit threshold (3); blocking repeated attempts.")

                # Local execution failures (Errno 2 / SyntaxError / permission / missing dep)
                # never reached the target — a broken invocation, not a target response. Don't
                # let identical retries burn the shared free-tier budget: inject a corrective
                # note and abort the agent after LOCAL_EXEC_MAX_RETRIES consecutive hits.
                fail_cat = getattr(res, "failure_category", None)
                if getattr(res, "execution_failure", False) and fail_cat in LOCAL_EXEC_CATEGORIES:
                    board.agent_local_fail_streak[agent_id] = board.agent_local_fail_streak.get(agent_id, 0) + 1
                    streak = board.agent_local_fail_streak[agent_id]
                    board.record_agent_step(agent_id, note=(
                        f"LOCAL EXECUTION ERROR ({fail_cat}) — this command never reached the target; "
                        f"it is a local invocation problem, not a target response. Fix the invocation "
                        f"(e.g. quote any path with a space: python \"my dir/solve.py\") or try a different "
                        f"approach. Do NOT re-run the same command."))
                    max_local = int(getattr(settings, "LOCAL_EXEC_MAX_RETRIES", 2))
                    if streak >= max_local:
                        blocker = (f"ABORTING agent after {streak} consecutive local execution failures "
                                   f"({fail_cat}) — blocked on local tooling; reporting instead of retrying.")
                        board.record_agent_step(agent_id, note=blocker)
                        _append_to_challenge_log(board.challenge_id, agent_id, blocker)
                        await board.update_worker_state(agent_id, status="DONE", current_task=blocker)
                        if not board.stall_reason:
                            board.stall_reason = f"An agent was blocked on a local tooling error ({fail_cat})"
                        break
                else:
                    # Command reached the target (or a non-local failure) — reset the streak.
                    board.agent_local_fail_streak[agent_id] = 0

                await board.update_worker_state(
                    agent_id, status="RUNNING", last_tool=cmd[:160],
                    last_result=(output[:200] or f"[Exit {res.exit_code}] no output"),
                    commands_run=board.worker_states.get(agent_id, {}).get("commands_run", 0) + 1,
                )
                _append_to_challenge_log(board.challenge_id, agent_id, f"Output ({len(output)} bytes, exit={res.exit_code}): {output[:300]}")

                try:
                    await ws_manager.broadcast({
                        "event": "LOG_OUTPUT", "challenge_id": board.challenge_id, "run_id": board.run_id,
                        "command": cmd,
                        "output": output[:3000] if output else f"[Exit Code {res.exit_code}] Execution complete with no output.",
                        "exit_code": res.exit_code, "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    })
                except Exception:
                    pass

                # Flags/leads come ONLY from real tool output — never from LLM prose.
                self._check_tool_output_for_flags(output, board, agent_id)
                self._check_tool_output_for_rejections(output, board, agent_id)
                await self._apply_decoded_directives(output, board, agent_id)

                # ── Empirical Baseline Profiling, Anomaly Detection & Actionable Preemption ──
                # Check if this command handled any pending preemption
                for p in board.actionable_preemptions:
                    if not p.get("handled"):
                        targ = p.get("normalized_target") or p.get("raw_value") or ""
                        raw_val = p.get("raw_value") or ""
                        if (targ and targ in cmd) or (raw_val and raw_val in cmd):
                            p["handled"] = True
                            _append_to_challenge_log(
                                board.challenge_id, agent_id,
                                f"✓ Followed up on actionable preemption artifact: {targ}"
                            )

                anom_result = board.response_profiler.profile_and_evaluate(
                    command_or_target=cmd,
                    output=output,
                    status_code=getattr(res, "exit_code", None),
                    base_url=board.target_scope,
                )
                if anom_result.is_anomalous:
                    reasons_str = "; ".join(anom_result.reasons)
                    _append_to_challenge_log(
                        board.challenge_id, agent_id,
                        f"[ANOMALOUS_RESPONSE] Diverged from baseline ({len(output)} bytes, score={anom_result.score:.2f}): {reasons_str}"
                    )
                    try:
                        await ws_manager.broadcast({
                            "event": "ANOMALOUS_RESPONSE",
                            "challenge_id": board.challenge_id,
                            "run_id": board.run_id,
                            "agent": agent_id,
                            "command": cmd[:200],
                            "score": anom_result.score,
                            "reasons": anom_result.reasons,
                            "candidates": [c.to_dict() for c in anom_result.candidate_artifacts],
                        })
                    except Exception:
                        pass

                # If candidate artifacts were extracted:
                for cand in anom_result.candidate_artifacts:
                    targ = cand.normalized_target or cand.raw_value
                    if targ and targ not in board.seen_candidate_targets:
                        board.seen_candidate_targets.add(targ)
                        if cand.artifact_type in ("PATH", "ENDPOINT", "URL"):
                            board.discovered_endpoints.add(targ)
                        probes = generate_post_exploitation_probes(targ, base_url=board.target_scope)
                        preempt_item = {
                            "raw_value": cand.raw_value,
                            "artifact_type": cand.artifact_type,
                            "normalized_target": targ,
                            "source_command": cmd,
                            "reasons": anom_result.reasons,
                            "suggested_probes": probes,
                            "handled": False,
                        }
                        board.actionable_preemptions.append(preempt_item)
                        _append_to_challenge_log(
                            board.challenge_id, agent_id,
                            f"⚡ Actionable candidate artifact discovered: {targ} ({cand.artifact_type}) — prioritizing immediate verification"
                        )

                # ── Strategy evidence fingerprint + stale accounting ───────────────────
                # Runs on EVERY clean execution (exit=0 or not) because a script that
                # exits cleanly and prints "no flag found" is the exact failure mode we
                # need to catch.  _update_strategy_state returns True when the strategy
                # was just exhausted; in that case kick off the pivot immediately.
                just_exhausted = _update_strategy_state(board, agent_id, strategy_label, output, res)
                if just_exhausted:
                    asyncio.create_task(_force_pivot_if_needed(board, agent_id, strategy_label))

                # Encoded-artifact reconstruction: if this output is really an encoded
                # file (e.g. ASCII 0/1 that reconstructs to a JPEG), rebuild it, preserve
                # it as evidence, and escalate its analysis — never conclude "no flag"
                # with an un-analyzed artifact still on the board.
                await self._reconstruct_and_escalate(
                    output, board, agent_id, workdir, origin_label=f"tool_output:{(cmd or '')[:60]}")
                if "<!--" in output:
                    for c in re.findall(r"<!--(.*?)-->", output, re.DOTALL):
                        c_clean = c.strip()
                        if len(c_clean) > 3:
                            await self._apply_decoded_directives(c_clean, board, agent_id)
                for ep in re.findall(r'href=["\'](/[^"\']+)["\']', output):
                    board.discovered_endpoints.add(ep)

                consecutive_errors = 0
                await board._broadcast_blackboard()

            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"[{agent_id}] Error #{consecutive_errors}: {e}")
                _append_to_challenge_log(board.challenge_id, agent_id, f"Error #{consecutive_errors}: {e}")
                await board.update_worker_state(agent_id, status="IDLE", failures=consecutive_errors,
                                                current_task=f"Error: {str(e)[:100]}")
                await asyncio.sleep(min(3 * consecutive_errors, 15))

        await board.update_worker_state(agent_id, status="DONE", current_task="Agent finished")

    # ── HITL checkpoint coordinator (hard pause & wait) ─────────────────────────

    def _make_summarizer(self):
        """Async callable for the strictly-extractive Gemini narrative pass. Returns
        None on any failure so the report still renders deterministically."""
        async def _summarize(prompt: str) -> Optional[str]:
            try:
                resp = await model_router.route_request(
                    prompt=prompt, capability="general_reasoning", target_model="gemini-3.6-flash")
                if resp and not resp.is_refusal:
                    return resp.content
            except Exception:
                return None
            return None
        return _summarize

    def _wind_down_for_expiry(self, board: "SwarmBlackboard"):
        """Stop the run cleanly as the platform instance-expiry deadline nears. Agents
        self-stop on the same deadline (see _agent_worker); this signals run_swarm to
        finalize with best findings instead of the coordinator hard-waiting on a paste."""
        board.stall_reason = board.stall_reason or "Platform instance expiry reached"
        _append_to_challenge_log(board.challenge_id, "checkpoint",
                                 "Instance expiry within wind-down buffer — stopping run with best findings")
        board.is_stopped = True
        board.checkpoint_response_event.set()

    async def _checkpoint_coordinator(self, board: "SwarmBlackboard", workdir: str):
        """Every CHECKPOINT_INTERVAL_SECONDS (or as the instance timer nears expiry,
        or immediately on a verified flag), hard-pause all agents, emit ONE
        consolidated report, and wait for the operator's pasted guidance."""
        _append_to_challenge_log(board.challenge_id, "checkpoint", "Checkpoint coordinator started")
        while not board.flag_captured and not board.is_stopped:
            interval = int(getattr(settings, "CHECKPOINT_INTERVAL_SECONDS", 300))
            buffer = int(getattr(settings, "INSTANCE_WINDDOWN_BUFFER_SECONDS", 30))
            # Too close to instance expiry to run an interactive (operator-paste) checkpoint
            # — wind down instead of hard-waiting for a paste that can't complete in time.
            if board.instance_expiry_ts and (board.instance_expiry_ts - time.time()) <= buffer:
                self._wind_down_for_expiry(board)
                break
            if board.instance_expiry_ts:
                secs_left = board.instance_expiry_ts - time.time()
                if secs_left > 0:
                    interval = min(interval, max(10, int(secs_left - buffer)))
            try:
                await asyncio.wait_for(board.flag_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if board.flag_captured or board.is_stopped:
                break
            # Crossed into the expiry buffer while waiting → wind down, don't hard-wait.
            if board.instance_expiry_ts and (board.instance_expiry_ts - time.time()) <= buffer:
                self._wind_down_for_expiry(board)
                break
            try:
                await self._run_checkpoint_cycle(board, workdir)
            except Exception as e:
                logger.warning(f"[checkpoint] cycle error (non-fatal): {e}")
                _append_to_challenge_log(board.challenge_id, "checkpoint", f"Cycle error (non-fatal): {e}")
                board.checkpoint_pause = False
                board.checkpoint_active = False
        _append_to_challenge_log(board.challenge_id, "checkpoint", "Checkpoint coordinator stopped")

    async def _run_checkpoint_cycle(self, board: "SwarmBlackboard", workdir: str):
        cycle_start = board.cycle_window_start_ts
        board.checkpoint_pause = True
        board.checkpoint_active = True
        board.cycle_n += 1
        board.checkpoint_response_event.clear()
        board.latest_pasted_response = None
        _append_to_challenge_log(board.challenge_id, "checkpoint", f"=== Checkpoint cycle {board.cycle_n}: pausing agents ===")
        # Passive provider-headroom summary (from real response headers; no extra calls).
        try:
            from backend.providers.quota_manager import quota_manager as _qm
            _append_to_challenge_log(board.challenge_id, "checkpoint", f"Provider headroom: {_qm.ratelimit_summary()}")
        except Exception:
            pass
        await asyncio.sleep(2.0)  # brief quiesce so in-flight steps land

        records = board.snapshot_agent_records()
        start_str = datetime.fromtimestamp(cycle_start, tz=timezone.utc).strftime("%H:%M:%S")
        end_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        report = await checkpoint_pipeline.build_consolidated_report(
            challenge_name=board.challenge_name, category=board.category, difficulty=board.difficulty,
            target=board.target_scope, cycle_n=board.cycle_n, start_time=start_str, end_time=end_str,
            records=records, summarizer=self._make_summarizer(),
        )
        board.last_checkpoint_report = report

        db = SessionLocal()
        try:
            db.add(CheckpointModel(
                run_id=board.run_id,
                state_snapshot={"kind": "hitl_checkpoint", "cycle_n": board.cycle_n,
                                "report": report, "agent_ids": list(board.agent_ids)},
                last_successful_action=f"checkpoint_cycle_{board.cycle_n}",
                resumable=True,
            ))
            run_obj = db.query(RunModel).filter(RunModel.id == board.run_id).first()
            if run_obj:
                run_obj.status = "WAITING_FOR_USER"
            ch_obj = db.query(ChallengeModel).filter(ChallengeModel.id == board.challenge_id).first()
            if ch_obj:
                ch_obj.status = "WAITING_FOR_USER"
            db.commit()
        except Exception as e:
            logger.debug(f"[checkpoint] persist skip: {e}")
        finally:
            db.close()

        await ws_manager.broadcast({
            "event": "CHECKPOINT_REACHED", "challenge_id": board.challenge_id, "run_id": board.run_id,
            "cycle": board.cycle_n, "report": report,
        })
        _append_to_challenge_log(board.challenge_id, "checkpoint",
                                 f"Report emitted for cycle {board.cycle_n}; awaiting operator paste")

        # Bounded wait for operator's pasted external-model response.
        # Concurrently monitors operator response, flag capture, and cancellation/pause
        # while respecting CHECKPOINT_TIMEOUT_SECONDS and instance wind-down buffer.
        timeout = float(getattr(settings, "CHECKPOINT_TIMEOUT_SECONDS", 30))
        buffer = float(getattr(settings, "INSTANCE_WINDDOWN_BUFFER_SECONDS", 30))
        if board.instance_expiry_ts:
            secs_left = board.instance_expiry_ts - time.time() - buffer
            if secs_left > 0:
                timeout = min(timeout, max(0.1, secs_left))
            else:
                self._wind_down_for_expiry(board)
                board.checkpoint_pause = False
                board.checkpoint_active = False
                return

        resp_wait = asyncio.create_task(board.checkpoint_response_event.wait())
        flag_wait = asyncio.create_task(board.flag_event.wait())
        timed_out = False
        try:
            if timeout > 0:
                done, pending = await asyncio.wait(
                    [resp_wait, flag_wait],
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    timed_out = True
            else:
                timed_out = True
        except asyncio.CancelledError:
            board.checkpoint_pause = False
            board.checkpoint_active = False
            raise
        except Exception:
            timed_out = True
        finally:
            resp_wait.cancel()
            flag_wait.cancel()

        if board.is_stopped or board.flag_captured:
            board.checkpoint_pause = False
            board.checkpoint_active = False
            return

        if board.instance_expiry_ts and (board.instance_expiry_ts - time.time()) <= buffer:
            self._wind_down_for_expiry(board)
            board.checkpoint_pause = False
            board.checkpoint_active = False
            return

        pasted = board.latest_pasted_response
        if timed_out or not pasted:
            _append_to_challenge_log(
                board.challenge_id, "checkpoint",
                f"No operator guidance received within timeout ({timeout:.1f}s) — resuming autonomous execution"
            )
            await ws_manager.broadcast({
                "event": "CHECKPOINT_RESUMED", "challenge_id": board.challenge_id, "run_id": board.run_id,
                "cycle": board.cycle_n, "routed": [], "timeout": True,
            })
        else:
            pasted_str = pasted or ""
            parsed = checkpoint_pipeline.parse_suggestions(pasted_str, board.agent_ids)

            # -- Pull state for evaluation from the shared mission (best-effort) --
            # We use the board's own fields where available; the suggestion evaluator
            # gracefully handles None/empty iterables.
            _ms = getattr(board, "mission_state", None)
            _exh = list(board.exhausted_strategies) if hasattr(board, "exhausted_strategies") else []
            _fail = list(getattr(_ms, "failed_techniques", []) or []) if _ms else []
            _eps = list(getattr(_ms, "endpoints", []) or getattr(_ms, "known_endpoints", []) or []) if _ms else []
            _files = list(getattr(_ms, "known_files", []) or []) if _ms else []
            _flags = list(getattr(_ms, "flag_candidates", []) or []) if _ms else []

            def _evaluate_and_inject(aid: str, directive: str) -> None:
                """Evaluate one directive; inject only if ACCEPTED or MODIFIED."""
                ev = checkpoint_pipeline.evaluate_suggestion(
                    directive,
                    exhausted_strategies=_exh,
                    failed_techniques=_fail,
                    known_endpoints=_eps,
                    known_files=_files,
                    flag_candidates=_flags,
                )
                if ev.decision == SuggestionDecision.REJECT:
                    _append_to_challenge_log(
                        board.challenge_id, "checkpoint",
                        f"[SUGGESTION_REJECTED:{aid}] {ev.reason} | original: {directive[:120]}",
                    )
                    return  # Do NOT inject rejected suggestions
                action_text = ev.suggested_action or directive
                prev = board.agent_directives.get(aid, "")
                if ev.decision == SuggestionDecision.MODIFY:
                    _append_to_challenge_log(
                        board.challenge_id, "checkpoint",
                        f"[SUGGESTION_MODIFIED:{aid}] {ev.reason}",
                    )
                board.agent_directives[aid] = (prev + "\n\n" + action_text).strip() if prev else action_text

            if parsed.parsed:
                async with board._lock:
                    for aid, directive in parsed.directives.items():
                        _evaluate_and_inject(aid, directive)
                injected = [aid for aid in parsed.directives if board.agent_directives.get(aid)]
                _append_to_challenge_log(board.challenge_id, "checkpoint",
                                         f"Routed evaluated directives to: {', '.join(sorted(parsed.directives.keys()))}"
                                         + (f" | {parsed.note}" if parsed.note else ""))
                await ws_manager.broadcast({
                    "event": "CHECKPOINT_RESUMED", "challenge_id": board.challenge_id, "run_id": board.run_id,
                    "cycle": board.cycle_n, "routed": sorted(parsed.directives.keys()),
                    "unknown_labels": parsed.unknown_labels,
                })
            else:
                if parsed.fallback and parsed.fallback_text:
                    async with board._lock:
                        for aid in board.agent_ids:
                            _evaluate_and_inject(aid, "[GENERAL GUIDANCE] " + parsed.fallback_text)
                _append_to_challenge_log(board.challenge_id, "checkpoint",
                                         f"UNPARSEABLE paste — {parsed.note} Applied as general guidance to all agents.")
                await ws_manager.broadcast({
                    "event": "CHECKPOINT_PARSE_ERROR", "challenge_id": board.challenge_id, "run_id": board.run_id,
                    "cycle": board.cycle_n, "note": parsed.note, "applied_as_general": bool(parsed.fallback_text),
                })

        # Refresh each agent's budget for the new cycle. The operator just re-authorized
        # continuation at the checkpoint, so directives get a fresh iteration/minute window
        # instead of dying instantly on a budget that was already spent before the pause
        # (the checkpoint-interval == budget trap). The dedup set is deliberately NOT reset,
        # so agents still never re-run an identical command.
        now_ts = time.time()
        for aid in board.agent_ids:
            board.agent_started_ts[aid] = now_ts
            board.agent_paused_seconds[aid] = 0.0
            board.agent_iterations[aid] = 0
            board.agent_local_fail_streak[aid] = 0
        _append_to_challenge_log(
            board.challenge_id, "checkpoint",
            f"Budget refreshed for {len(board.agent_ids)} agents on resume (fresh cycle window)")

        board.checkpoint_pause = False
        board.checkpoint_active = False
        board.latest_pasted_response = None
        db = SessionLocal()
        try:
            run_obj = db.query(RunModel).filter(RunModel.id == board.run_id).first()
            if run_obj:
                run_obj.status = "RUNNING"
            ch_obj = db.query(ChallengeModel).filter(ChallengeModel.id == board.challenge_id).first()
            if ch_obj:
                ch_obj.status = "RUNNING"
            db.commit()
        except Exception:
            pass
        finally:
            db.close()
        board.cycle_window_start_ts = time.time()
        _append_to_challenge_log(board.challenge_id, "checkpoint", f"=== Checkpoint cycle {board.cycle_n}: agents resumed ===")

    async def submit_checkpoint_response(self, challenge_id: str, text: str) -> Dict[str, Any]:
        """Deliver the operator's pasted external-model response to a waiting swarm.
        Called by the API. Parsing/injection happens in the coordinator on wake."""
        for board in list(self.active_swarms.values()):
            if board.challenge_id == challenge_id and board.checkpoint_active:
                board.latest_pasted_response = text or ""
                preview = checkpoint_pipeline.parse_suggestions(text or "", board.agent_ids)
                board.checkpoint_response_event.set()
                return {
                    "accepted": True, "parsed": preview.parsed,
                    "routed": sorted(preview.directives.keys()),
                    "fallback": preview.fallback, "unknown_labels": preview.unknown_labels,
                    "note": preview.note,
                }
        return {"accepted": False, "reason": "No active checkpoint is awaiting a response for this challenge."}

    async def submit_approval_response(self, request_id: str, decision: str, sudo_password: Optional[str] = None) -> Dict[str, Any]:
        """Deliver operator's approve/deny decision for a pending privileged command.

        When *decision* is 'approve' and the pending entry has requires_sudo=True,
        *sudo_password* is stored in-memory on the pending entry dict ONLY — it is
        never logged, never persisted, never broadcast.  The worker loop retrieves it
        once, wipes it from the dict before execution, and discards it after the
        subprocess completes.
        """
        if decision not in ("approve", "deny"):
            return {"accepted": False, "reason": "Invalid decision. Must be 'approve' or 'deny'."}

        for board in list(self.active_swarms.values()):
            if request_id in board.pending_approvals:
                entry = board.pending_approvals[request_id]
                entry["decision"] = decision
                # Store sudo_password in-memory only; only when approving a sudo command.
                if decision == "approve" and entry.get("requires_sudo"):
                    entry["sudo_password"] = sudo_password  # may be None if not a sudo command
                entry["event"].set()
                return {"accepted": True}

        # Non-swarm callers (legacy ReAct loop, agent_runtime RealToolExecutor) own no
        # board, so their requests live in the shared registry. Same uuid4 keyspace, so
        # there is no ambiguity with the board lookup above.
        shared_entry = SHARED_PENDING_APPROVALS.get(request_id)
        if shared_entry is not None:
            shared_entry["decision"] = decision
            if decision == "approve" and shared_entry.get("requires_sudo"):
                shared_entry["sudo_password"] = sudo_password
            shared_entry["event"].set()
            return {"accepted": True}

        return {"accepted": False, "reason": "No pending approval with this request_id."}

    def _extract_command(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"```(?:bash|sh)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        lines = [l.strip() for l in text.strip().split("\n") if l.strip().startswith(("curl", "python", "ffuf", "nmap", "sqlmap"))]
        return lines[0] if lines else None

    def _check_tool_output_for_rejections(self, text: str, board: SwarmBlackboard, worker_id: str):
        """Detect target rejection signals (e.g., 'User not found', 'Invalid token')."""
        if not text:
            return
        text_lower = text.lower()
        if "user not found" in text_lower or "unknown user" in text_lower or "no such user" in text_lower or "user does not exist" in text_lower:
            board.last_target_rejection = "USER_NOT_FOUND"
            _append_to_challenge_log(board.challenge_id, worker_id, "ℹ Target rejected username ('User not found'). Triggering candidate entity priority refill.")
        elif "invalid token" in text_lower or "token expired" in text_lower:
            board.last_target_rejection = "INVALID_TOKEN"

    async def _reconstruct_and_escalate(self, text: str, board: "SwarmBlackboard", worker_id: str,
                                        workdir: str, origin_label: str = ""):
        """Deterministically reconstruct an encoded artifact from ``text`` and escalate it.

        If ``text`` contains an encoded representation of another file (ASCII binary,
        hex, base64), rebuild the real bytes, scan any readable rendering for a flag,
        preserve a recognized/opaque file as a derived artifact (provenance sidecar),
        register it as shared evidence + an analyzable input, and attach tesseract-free
        visual metadata for images. Never executes the artifact (requirement #15).
        """
        if not text or len(text) < 64:
            return
        try:
            from backend.agents.artifact_reconstruction import (
                reconstruct_from_text, persist_derived_artifact, is_image_type,
                inspect_image, attempt_ocr,
            )
        except Exception as exc:
            logger.debug("[reconstruct] module unavailable: %s", exc)
            return

        input_hash = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        async with board._lock:
            if input_hash in board.processed_recon_inputs:
                return
            board.processed_recon_inputs.add(input_hash)

        try:
            outcome = reconstruct_from_text(text, origin_label=origin_label)
        except Exception as exc:
            logger.debug("[reconstruct] failed: %s", exc)
            return
        if outcome is None:
            return

        task_context = {
            "description": board.description,
            "challenge_name": board.challenge_name,
            "category": board.category,
            "flag_pattern": board.flag_pattern,
            "target_scope": board.target_scope,
        }

        # Any readable rendering is scanned for candidates with explicit provenance.
        if outcome.decoded_text:
            extracted = board.answer_resolver.extract_candidates(
                outcome.decoded_text, task_context=task_context, source=AnswerSource.RECONSTRUCTED_ARTIFACT
            )
            for cand in extracted:
                await board.record_flag_candidate(cand.value, worker_id, "reconstructed_artifact")
            m = FLAG_REGEX.search(outcome.decoded_text)
            if m and not FALSE_FLAG_PATTERNS.search(m.group(0)):
                await board.record_flag_candidate(m.group(0).strip(), worker_id, "reconstructed_artifact")

        if not outcome.should_persist:
            return

        derived_path = persist_derived_artifact(outcome, workdir)
        newly = await board.register_derived_artifact(outcome, derived_path, worker_id)
        if not newly or not derived_path:
            return

        # Visual metadata (tesseract-free) for image artifacts, so a vision-capable
        # agent knows what it is looking at. OCR is strictly best-effort.
        if is_image_type(outcome.artifact_type):
            meta = inspect_image(derived_path)
            ocr = attempt_ocr(derived_path)
            for d in board.derived_artifacts:
                if d.get("derived_path") == derived_path:
                    d["visual"] = meta
                    d["ocr_available"] = bool(ocr.get("available"))
                    break
            if ocr.get("available") and ocr.get("text"):
                extracted_ocr = board.answer_resolver.extract_candidates(
                    ocr["text"], task_context=task_context, source=AnswerSource.RECONSTRUCTED_ARTIFACT_OCR
                )
                for cand in extracted_ocr:
                    await board.record_flag_candidate(cand.value, worker_id, "reconstructed_artifact_ocr")
                m2 = FLAG_REGEX.search(ocr["text"])
                if m2 and not FALSE_FLAG_PATTERNS.search(m2.group(0)):
                    await board.record_flag_candidate(m2.group(0).strip(), worker_id, "reconstructed_artifact_ocr")
            # Escalate image reading to vision_read capability
            await self.analyze_derived_artifact(board, derived_path, worker_id)

    async def analyze_derived_artifact(
        self, board: "SwarmBlackboard", derived_path: str, worker_id: str
    ) -> Optional[str]:
        """Analyze a derived artifact via vision_read (or applicable tool) and record candidate flags/answers."""
        if not derived_path or not os.path.isfile(derived_path):
            return None

        task_context = {
            "description": board.description,
            "challenge_name": board.challenge_name,
            "category": board.category,
            "flag_pattern": board.flag_pattern,
            "target_scope": board.target_scope,
        }

        # Execute vision_read capability on derived_path
        res = await tool_manager.execute_capability("vision_read", target=derived_path)
        if res.status == "SUCCESS" and res.stdout:
            # Check stdout for generic and flag candidates
            extracted = board.answer_resolver.extract_candidates(
                res.stdout, task_context=task_context, source=AnswerSource.VISION_READ
            )
            for cand in extracted:
                await board.record_flag_candidate(
                    cand.value, worker_id, "vision_read",
                    evidence={"derived_path": derived_path, "tool": "vision_read", "stdout": res.stdout[:500]}
                )
            for m in FLAG_REGEX.finditer(res.stdout):
                cand_str = m.group(0).strip()
                if not FALSE_FLAG_PATTERNS.search(cand_str):
                    await board.record_flag_candidate(
                        cand_str, worker_id, "vision_read",
                        evidence={"derived_path": derived_path, "tool": "vision_read", "stdout": res.stdout[:500]}
                    )
            board.mark_derived_analyzed(derived_path)
            return res.stdout
        elif res.stderr:
            logger.info(f"[{worker_id}] vision_read analysis notice for {derived_path}: {res.stderr}")
            if res.failure_category == "MODEL_REFUSAL" or "PAID_MODEL_ALLOWED" in res.stderr:
                _append_to_challenge_log(board.challenge_id, worker_id, f"ℹ vision_read: {res.stderr}")
        return None

    async def _scan_attached_for_encoded(self, board: "SwarmBlackboard", workdir: str):
        """One-shot scan of attached artifacts for an encoded representation of a file.

        Handles the case where the CHALLENGE FILE ITSELF (not a command's output) is a
        wall of ASCII bits / hex / base64 that reconstructs into another file type —
        e.g. a forensics artifact provided as text. Text-like files only, size-capped,
        and skips anything already carrying a binary magic (real binaries are handled by
        the existing acquisition path) and the forge_derived output dir.
        """
        for path in list(board.attached_file_paths):
            try:
                if not path or "forge_derived" in path.replace("\\", "/"):
                    continue
                if not os.path.isfile(path):
                    continue
                if os.path.getsize(path) > 8 * 1024 * 1024:      # 8 MB cap
                    continue
                with open(path, "rb") as fh:
                    head = fh.read(16)
                # Skip files that already ARE a known binary type.
                from backend.agents.artifact_classifier import _classify_magic
                label, _ = _classify_magic(head)
                if label not in ("text", "unknown"):
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read(9 * 1024 * 1024)
                await self._reconstruct_and_escalate(
                    content, board, "orchestrator", workdir,
                    origin_label=f"attached:{os.path.basename(path)}")
            except Exception as exc:
                logger.debug("[reconstruct] attached scan skip %s: %s", path, exc)

    def _check_tool_output_for_flags(self, text: str, board: SwarmBlackboard, worker_id: str):
        """Scan ONLY tool/command output for flag and answer patterns. Never call this on LLM prose."""
        if not text:
            return
        task_context = {
            "description": board.description,
            "challenge_name": board.challenge_name,
            "category": board.category,
            "flag_pattern": board.flag_pattern,
            "target_scope": board.target_scope,
        }
        extracted = board.answer_resolver.extract_candidates(
            text, task_context=task_context, source=AnswerSource.TOOL_OUTPUT
        )
        for cand in extracted:
            asyncio.create_task(board.record_flag_candidate(cand.value, worker_id, "tool_output"))

        match = FLAG_REGEX.search(text)
        if match:
            candidate = match.group(0).strip()
            if not FALSE_FLAG_PATTERNS.search(candidate):
                asyncio.create_task(board.record_flag_candidate(candidate, worker_id, "tool_output"))

    async def _apply_decoded_directives(self, raw: str, board: SwarmBlackboard, worker_id: str) -> bool:
        """Deterministically decode an artifact (HTML comment, task text) and act
        on concrete leads (headers, candidate usernames, flags).

        - Caches hash of input to prevent 15x duplicate decode loops on unchanged input.
        - Harvests candidate usernames and queues targeted exploit tasks.
        - Extracts header directives (e.g. 'use header "X-Dev-Access: yes"').
        """
        if not raw or not raw.strip():
            return False

        import hashlib
        raw_hash = hashlib.md5(raw.strip().encode("utf-8")).hexdigest()
        async with board._lock:
            if raw_hash in board.processed_decode_hashes:
                return False
            board.processed_decode_hashes.add(raw_hash)

        task_context = {
            "description": board.description,
            "challenge_name": board.challenge_name,
            "category": board.category,
            "flag_pattern": board.flag_pattern,
            "target_scope": board.target_scope,
        }

        acted = False
        try:
            for d in _decode_artifacts(raw):
                decoded = d["decoded"]
                _append_to_challenge_log(board.challenge_id, worker_id, f"🔓 {d['scheme']} decode: {decoded[:160]}")
                # Flags/answers hidden via an encoding inside a real captured artifact.
                extracted = board.answer_resolver.extract_candidates(
                    decoded, task_context=task_context, source=AnswerSource.DECODED_ARTIFACT
                )
                for cand in extracted:
                    await board.record_flag_candidate(cand.value, worker_id, "decoded_artifact")
                    acted = True

                for fm in FLAG_REGEX.finditer(decoded):
                    cand = fm.group(0).strip()
                    if not FALSE_FLAG_PATTERNS.search(cand):
                        await board.record_flag_candidate(cand, worker_id, "decoded_artifact")
                        acted = True

                # Harvest candidate usernames (e.g. 'NOTE: Jack', 'username: Jack', 'dev: Jack', 'account: Jack')
                user_matches = re.findall(r"\b(?:user(?:name)?|developer|account|NOTE)\s*[:=\-]\s*['\"]?([A-Za-z0-9_\-\.]{3,24})", decoded, re.IGNORECASE)
                for u in user_matches:
                    u_clean = u.strip().strip("'\".,;:()")
                    if u_clean.lower() not in ["not", "the", "found", "error", "true", "false", "null", "undefined", "header", "temporary", "bypass", "access"]:
                        if u_clean not in board.candidate_usernames:
                            board.candidate_usernames.add(u_clean)
                            _append_to_challenge_log(board.challenge_id, worker_id, f"👤 Candidate username discovered: {u_clean}")
                            logger.info(f"[SwarmBlackboard] Candidate username discovered by {worker_id}: {u_clean}")

                # Header directives only when the decode explicitly names one.
                if re.search(r"\bheader\b", decoded, re.IGNORECASE):
                    for hm in _HEADER_HINT_RE.finditer(decoded):
                        if await board.note_exploit_header(hm.group(1), hm.group(2), worker_id):
                            acted = True
        except Exception as e:
            logger.debug(f"[{worker_id}] decode-directives skip: {e}")

        return acted

    async def _learn_from_run(self, board: "SwarmBlackboard", outcome: str = "success"):
        """LEARN → STORE (§3, §5, §12, §19; Phase 6 Part 8).

        Distil the finished run into a single GENERALIZED experience, store it, and
        run the feedback loop for any memories that were retrieved this mission. This
        fires on BOTH a solve and a genuine failure/stall: a failed run is still
        experience (what was tried, why it stalled) that future missions use to avoid
        dead ends. The experience layer is the one learning entry point; a *proven*
        experience is promoted into the Playbook Vault by ExperienceMemory itself (§10)
        — a failed one never is (it cannot meet the promotion bar). Deterministic +
        non-fatal — a learning failure can never break run completion.
        """
        try:
            flag = board.flag_captured or ""
            # A failed run only teaches something if it actually attempted work; storing
            # an empty no-op failure would be noise (and violates the no-demo-data rule).
            if outcome != "success" and not (getattr(board, "execution_history", None) or []):
                return
            record = experience_extractor.extract_from_board(board, flag=flag, outcome=outcome)
            exp_id = experience_memory.store(record)
            if exp_id:
                _append_to_challenge_log(
                    board.challenge_id, "orchestrator",
                    f"[MEMORY] Learned {outcome} experience {exp_id}: '{record.technique}' "
                    f"({len(record.successful_attack_chain)}-step chain, "
                    f"{len(record.failed_techniques)} failed approaches recorded)")
                try:
                    await ws_manager.broadcast({
                        "event": "MEMORY_LEARNED", "challenge_id": board.challenge_id,
                        "run_id": board.run_id, "experience_id": exp_id,
                        "technique": record.technique, "outcome": outcome,
                    })
                except Exception:
                    pass
            # Feedback loop (§12): memories retrieved this mission are reinforced by the
            # outcome — positively on a solve, negatively on a run that did not capture
            # the flag, so a memory that led nowhere loses confidence. Failure is
            # CONTEXTUAL, not a blacklist: success_rate/confidence decay but the memory
            # is never deleted or blocked (Part 8).
            solved = (outcome == "success")
            for mid in board.retrieved_memory_ids:
                try:
                    experience_memory.record_feedback(
                        mid, success=solved,
                        note=("Retrieved during a solved run" if solved
                              else "Retrieved during a run that did not capture the flag"),
                        run_id=board.run_id, challenge_id=board.challenge_id)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[SwarmOrchestrator] Experience learning failed (non-fatal): {e}")

# Global Singleton
swarm_orchestrator = SwarmOrchestrator()
