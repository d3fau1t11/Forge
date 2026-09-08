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
import sys
import time
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set

from backend.database.session import SessionLocal
from backend.database.models import RunModel, ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel, ToolExecutionModel, CheckpointModel
from backend.providers.router import model_router
from backend.tools.manager import tool_manager, LOCAL_EXEC_CATEGORIES
from backend.websocket.manager import ws_manager
from backend.engine.keep_awake import keep_awake_manager
from backend.reporting.generator import report_generator
from backend.knowledge.playbook_vault import playbook_vault
from backend.recon.turbo_recon import turbo_recon
from backend.config import settings
from backend.environment.detector import environment_detector
from backend.agents.agent_prompt import AgentContext, build_agent_prompt, make_context_from_env
from backend.agents.artifact_acquisition import acquire_artifacts
from backend.agents import checkpoint_pipeline
from backend.agents.checkpoint_pipeline import AgentCheckpointRecord

logger = logging.getLogger("forge.swarm")

# Strict flag regex — only known CTF platform prefixes, minimum 4 chars inside braces.
# Does NOT include a generic catch-all to avoid false positives from CSS, LaTeX, JSON, etc.
FLAG_REGEX = re.compile(
    r"(?:picoCTF\{[^}]{4,}\}|FLAG\{[^}]{4,}\}|flag\{[^}]{4,}\}|HTB\{[^}]{4,}\}|CTF\{[^}]{4,}\}|"
    r"DUCTF\{[^}]{4,}\}|corctf\{[^}]{4,}\}|TFCCTF\{[^}]{4,}\}|pwn\.college\{[^}]{4,}\})",
    re.IGNORECASE
)

# Patterns that look like flags but are actually examples/placeholders from LLM responses
FALSE_FLAG_PATTERNS = re.compile(
    r"(?:picoCTF\{\.\.\.\}|FLAG\{\.\.\.\}|HTB\{\.\.\.\}|CTF\{\.\.\.\}|"
    r"\{[a-z_]+_here\}|\{example[^}]*\}|\{your[^}]*\}|\{placeholder[^}]*\}|"
    r"\{some[^}]*\}|\{flag[^}]*format[^}]*\}|\{insert[^}]*\})",
    re.IGNORECASE
)

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
    """Resolve the path to the challenge log file."""
    logs_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, f"challenge_{challenge_id}.log")


def _append_to_challenge_log(challenge_id: str, worker_id: str, message: str):
    """Thread-safe append a line to the challenge's dedicated log file."""
    try:
        log_path = _get_challenge_log_path(challenge_id)
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{worker_id}] {message}\n")
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
    def __init__(self, challenge_id: str, run_id: str, target_scope: str):
        self.challenge_id = challenge_id
        self.run_id = run_id
        self.target_scope = target_scope
        self.discovered_endpoints: Set[str] = set()
        self.extracted_headers: Dict[str, str] = {}
        self.observed_cookies: Dict[str, str] = {}
        self.deobfuscated_secrets: List[Dict[str, str]] = []
        self.execution_history: List[Dict[str, Any]] = []
        self.executed_commands_dedup: Set[str] = set()
        self.task_pool: Dict[str, SwarmTask] = {}
        self.flag_captured: Optional[str] = None
        self.flag_candidates: List[Dict[str, str]] = []  # Unverified candidates
        self.flag_event = asyncio.Event()
        self.is_stopped = False
        # Set true only by an operator Pause so run_swarm finalizes as PAUSED
        # (resumable) rather than FAILED. Distinct from is_stopped, which also
        # trips on flag capture / kill switch.
        self.pause_requested = False
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

        # ── Unified flexible-agent challenge context ────────────────────────────
        # Populated by run_swarm before agents start; baked into every agent's
        # full-context prompt via build_agent_prompt(). No per-role framing.
        self.challenge_name: str = ""
        self.platform: str = ""
        self.category: str = "WEB"
        self.difficulty: str = "EASY"
        self.description: str = ""
        self.flag_pattern: str = getattr(settings, "DEFAULT_FLAG_PATTERNS",
                                         "picoCTF{...}|FLAG{...}|flag{...}|HTB{...}|CTF{...}")
        self.max_iterations: int = getattr(settings, "AGENT_MAX_ITERATIONS", 40)
        self.max_minutes: int = getattr(settings, "AGENT_MAX_MINUTES", 30)
        self.attached_file_paths: List[str] = []
        self.artifact_classification = None            # ClassificationResult | None
        self.env_info: Dict[str, Any] = {}

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

    async def record_flag_candidate(self, candidate: str, worker_id: str, source: str):
        """Record an unverified flag candidate for review. Only promotes to captured if from tool output."""
        async with self._lock:
            # Reject obvious placeholders / examples
            if FALSE_FLAG_PATTERNS.search(candidate):
                logger.info(f"[SwarmBlackboard] Rejected false-positive flag candidate: {candidate}")
                _append_to_challenge_log(self.challenge_id, worker_id, f"⚠ Rejected false flag: {candidate}")
                return

            # Check if this candidate was already seen
            for existing in self.flag_candidates:
                if existing["flag"] == candidate:
                    return

            self.flag_candidates.append({"flag": candidate, "worker": worker_id, "source": source})
            _append_to_challenge_log(self.challenge_id, worker_id, f"🏳 Flag candidate from {source}: {candidate}")
            logger.info(f"[SwarmBlackboard] Flag candidate from {source} by {worker_id}: {candidate}")

            await ws_manager.broadcast({
                "event": "FLAG_CANDIDATE",
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "candidate": candidate,
                "source": source,
                "worker": worker_id
            })

        # Only auto-promote to captured if the source is actual tool/command output (not LLM prose)
        if source == "tool_output":
            await self.record_flag(candidate, worker_id)

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

        counts["endpoints"] = len(self.discovered_endpoints)
        counts["headers"] = len(self.extracted_headers)
        counts["commands"] = len(self.executed_commands_dedup)
        counts["transcript_lines"] = sum(len(v) for v in self.agent_transcripts.values())
        return counts

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

    async def record_tool_execution(self, worker_id: str, command: str, res):
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
                    privilege_level="SAFE",
                    approved=True,
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
                        board.record_agent_step(agent_id, note=f"Agent reported flag candidate (unverified): {cand}")

                # A Python solver block -> write solve.py and run it byte-safely.
                py_match = re.search(r"```python\s*\n(.*?)\n```", content, re.DOTALL)
                if py_match:
                    script = py_match.group(1)
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
                        board.record_agent_step(agent_id, note=f"Could not write solver: {werr}")
                        board.agent_iterations[agent_id] = iters + 1
                        continue
                    # Quote the path — the workspace path can contain spaces (e.g.
                    # .../WEB/EASY/Old Sessions), which otherwise splits into [Errno 2].
                    cmd = f'python "{solver_path}"'
                else:
                    cmd = self._extract_command(content)
                    if not cmd:
                        board.record_agent_step(agent_id, note="No executable command produced this turn")
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

                board.agent_iterations[agent_id] = iters + 1

                if cmd in board.executed_commands_dedup:
                    board.record_agent_step(agent_id, command=cmd, note="skipped (already executed by the swarm)")
                    await asyncio.sleep(0.5)
                    continue
                board.executed_commands_dedup.add(cmd)

                _append_to_challenge_log(board.challenge_id, agent_id, f"Executing: {cmd[:200]}")
                res = await tool_manager.execute_tool("bash", {"command": cmd}, timeout=25,
                                                      working_directory=workdir, canonical_target=board.target_scope)
                output = res.stdout or res.stderr or ""
                if getattr(res, "execution_failure", False):
                    _append_to_challenge_log(board.challenge_id, agent_id,
                                             f"Execution failure ({getattr(res, 'failure_category', 'UNKNOWN')}): {res.stderr[:200]}")

                await board.record_tool_execution(agent_id, cmd, res)
                board.record_agent_step(agent_id, command=cmd, output=output)

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

        # HARD WAIT for the operator's pasted external-model response.
        await board.checkpoint_response_event.wait()
        if board.is_stopped or board.flag_captured:
            board.checkpoint_pause = False
            board.checkpoint_active = False
            return

        pasted = board.latest_pasted_response or ""
        parsed = checkpoint_pipeline.parse_suggestions(pasted, board.agent_ids)
        if parsed.parsed:
            async with board._lock:
                for aid, directive in parsed.directives.items():
                    prev = board.agent_directives.get(aid, "")
                    board.agent_directives[aid] = (prev + "\n\n" + directive).strip() if prev else directive
            _append_to_challenge_log(board.challenge_id, "checkpoint",
                                     f"Routed directives to: {', '.join(sorted(parsed.directives.keys()))}"
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
                        prev = board.agent_directives.get(aid, "")
                        add = "[GENERAL GUIDANCE] " + parsed.fallback_text
                        board.agent_directives[aid] = (prev + "\n\n" + add).strip() if prev else add
            _append_to_challenge_log(board.challenge_id, "checkpoint",
                                     f"UNPARSEABLE paste — {parsed.note} Applied as general guidance to all agents.")
            await ws_manager.broadcast({
                "event": "CHECKPOINT_PARSE_ERROR", "challenge_id": board.challenge_id, "run_id": board.run_id,
                "cycle": board.cycle_n, "note": parsed.note, "applied_as_general": bool(parsed.fallback_text),
            })

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

    def _check_tool_output_for_flags(self, text: str, board: SwarmBlackboard, worker_id: str):
        """Scan ONLY tool/command output for flag patterns. Never call this on LLM prose."""
        if not text:
            return
        match = FLAG_REGEX.search(text)
        if match:
            candidate = match.group(0).strip()
            # Reject placeholder patterns like picoCTF{...} or FLAG{example}
            if FALSE_FLAG_PATTERNS.search(candidate):
                logger.info(f"[{worker_id}] Rejected false-positive from tool output: {candidate}")
                _append_to_challenge_log(board.challenge_id, worker_id, f"⚠ Rejected placeholder flag: {candidate}")
                return
            # This is from tool output — high confidence, auto-promote to captured
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

        acted = False
        try:
            for d in _decode_artifacts(raw):
                decoded = d["decoded"]
                _append_to_challenge_log(board.challenge_id, worker_id, f"🔓 {d['scheme']} decode: {decoded[:160]}")
                # Flags hidden via an encoding inside a real captured artifact.
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

# Global Singleton
swarm_orchestrator = SwarmOrchestrator()
