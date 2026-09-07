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
from backend.database.models import RunModel, ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel, ToolExecutionModel
from backend.providers.router import model_router
from backend.tools.manager import tool_manager
from backend.websocket.manager import ws_manager
from backend.engine.keep_awake import keep_awake_manager
from backend.reporting.generator import report_generator
from backend.knowledge.playbook_vault import playbook_vault
from backend.recon.turbo_recon import turbo_recon

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
        self._lock = asyncio.Lock()

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
        """Build the current mission plan from the task pool (shared by broadcast + DB persist)."""
        total_tasks = len(self.task_pool)
        completed_tasks = sum(1 for t in self.task_pool.values() if t.status == "COMPLETED")
        plan_tasks = []
        for idx, t in enumerate(self.task_pool.values()):
            plan_tasks.append({
                "id": t.task_id,
                "phase": t.category,
                "title": t.description,
                "tool": "bash",
                "reasoning": f"Claimed by {t.claimed_by or 'swarm_pool'}",
                "status": "COMPLETED" if t.status == "COMPLETED" else ("IN_PROGRESS" if t.status == "CLAIMED" else "PENDING"),
                "output_summary": t.result or ""
            })
        return {
            "challenge_id": self.challenge_id,
            "status": "COMPLETED" if self.flag_captured else ("STALLED" if self.stall_reason else "IN_PROGRESS"),
            "summary": f"Swarm Intelligence Solver active on {self.target_scope}"
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
        counts = {"endpoints": 0, "headers": 0, "commands": 0, "completed_tasks": 0, "pending_tasks": 0}
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

        # Rebuild the task pool, preserving COMPLETED so progress carries and finished
        # work isn't repeated; anything unfinished is made claimable again.
        for t in (prior_tasks or []):
            title = t.get("title") or ""
            if not title:
                continue
            t_id = t.get("id") or f"task_{uuid.uuid4().hex[:8]}"
            task = SwarmTask(t_id, t.get("phase", "RECON"), title, priority=2)
            if t.get("status") == "COMPLETED":
                task.status = "COMPLETED"
                task.result = t.get("output_summary", "")
                counts["completed_tasks"] += 1
            else:
                task.status = "PENDING"
                counts["pending_tasks"] += 1
            self.task_pool[t_id] = task

        counts["endpoints"] = len(self.discovered_endpoints)
        counts["headers"] = len(self.extracted_headers)
        counts["commands"] = len(self.executed_commands_dedup)
        return counts

    def _compute_progress(self) -> int:
        """Progress percentage: 0-90 from task completion, 100 on flag capture."""
        if self.flag_captured:
            return 100
        total_tasks = len(self.task_pool)
        if total_tasks == 0:
            return 0
        completed_tasks = sum(1 for t in self.task_pool.values() if t.status == "COMPLETED")
        return int((completed_tasks / total_tasks) * 90)

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
        resume: bool = False
    ):
        """Dispatches parallel Swarm workers on the target."""
        logger.info(f"[SwarmOrchestrator] 🚀 Starting Swarm for Challenge '{challenge_id}' on '{target_scope}' (resume={resume})")
        _append_to_challenge_log(challenge_id, "orchestrator", f"Swarm {'resuming' if resume else 'starting'} | target={target_scope} | category={category} | difficulty={difficulty}")

        # Engage OS Keep-Awake lock
        keep_awake_manager.acquire(reason=f"Swarm Challenge {challenge_id}")

        board = SwarmBlackboard(challenge_id, run_id, target_scope)
        self.active_swarms[run_id] = board

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
                turbo_data = await turbo_recon.start_turbo_recon(challenge_id, target_scope, category.lower())
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
                        f"{counts['commands']} prior commands, {counts['completed_tasks']} completed tasks"
                    )
                    logger.info(f"[SwarmOrchestrator] Resuming challenge '{challenge_id}' with rehydrated state: {counts}")
                except Exception as rehydrate_err:
                    logger.warning(f"[SwarmOrchestrator] Resume rehydrate failed (starting fresh): {rehydrate_err}")

            # Seed Initial Swarm Tasks — only when the pool has no pending work (a fresh
            # start, or a resume where every prior task was already completed).
            if not any(t.status == "PENDING" for t in board.task_pool.values()):
                await board.add_task("RECON", f"Initial crawler & header discovery on {target_scope}", priority=5)
                await board.add_task("CODE_AUDIT", f"Inspect source code, HTML comments, and scripts on {target_scope}", priority=4)
                await board.add_task("EXPLOIT", f"Test authentication endpoints and parameters on {target_scope}", priority=3)
                _append_to_challenge_log(challenge_id, "orchestrator", "Initial task pool seeded (RECON, CODE_AUDIT, EXPLOIT)")
            else:
                pending_n = sum(1 for t in board.task_pool.values() if t.status == "PENDING")
                completed_n = sum(1 for t in board.task_pool.values() if t.status == "COMPLETED")
                _append_to_challenge_log(challenge_id, "orchestrator", f"Resumed task pool: {pending_n} pending, {completed_n} completed")

            # Define Swarm Workers
            workers = [
                self._recon_worker("worker_recon", board, working_directory),
                self._code_crypto_worker("worker_code_crypto", board, working_directory),
                self._exploit_worker("worker_exploit_pwn", board, working_directory)
            ]

            # Run workers concurrently until flag capture, stall, or cancellation.
            # asyncio.gather() already returns an awaitable _GatheringFuture; use
            # ensure_future (NOT create_task, which rejects a Future) so it can be
            # passed to asyncio.wait() below alongside flag_task.
            worker_task = asyncio.ensure_future(asyncio.gather(*workers, return_exceptions=True))
            flag_task = asyncio.create_task(board.flag_event.wait())
            refiller_task = asyncio.create_task(self._task_refiller(board, working_directory))

            _append_to_challenge_log(challenge_id, "orchestrator", "All 3 swarm workers + task refiller dispatched")

            # Wait for flag event, worker completion, or stall
            done, pending = await asyncio.wait(
                [flag_task, worker_task, refiller_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # If flag was captured, cancel any remaining tasks
            board.is_stopped = True
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
                try:
                    await board._persist_progress_if_due(force=True)
                except Exception:
                    pass
                paused_any = True
                _append_to_challenge_log(challenge_id, "orchestrator", "Pause requested by operator — workers finishing current step")
                logger.info(f"[SwarmOrchestrator] Pause requested for challenge '{challenge_id}'")
        return paused_any

    async def _recon_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 1: Fast Recon & Fuzzing (Uses Groq / Minimax / xKiro)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        _append_to_challenge_log(board.challenge_id, worker_id, "Worker started")
        await board.update_worker_state(worker_id, status="RUNNING", current_capability="recon",
                                        selected_model="Groq Qwen / xKiro (free)", current_task="Booting recon worker")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 5
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["RECON"])
            if not task:
                # No pending tasks — wait and check again
                await board.update_worker_state(worker_id, status="IDLE", current_task="Idle — waiting for tasks")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Too many errors ({consecutive_errors}), worker pausing for 30s")
                    await asyncio.sleep(30)
                    consecutive_errors = 0  # Reset after long pause
                else:
                    await asyncio.sleep(2.0)
                continue

            try:
                _append_to_challenge_log(board.challenge_id, worker_id, f"Claimed task: {task.description[:100]}")
                await board.update_worker_state(worker_id, status="ANALYZING", current_task=task.description[:140])

                # Query model for next recon action
                prompt = (
                    f"You are the Reconnaissance Worker in a parallel CTF Swarm.\n"
                    f"Target: {board.target_scope}\n"
                    f"Discovered Endpoints: {list(board.discovered_endpoints)}\n"
                    f"Headers: {board.extracted_headers}\n"
                    f"Current Task: {task.description}\n"
                    f"Issue a single bash command (e.g. curl, ffuf, httpx) to uncover hidden routes, parameters, or robots.txt.\n"
                    f"For directory brute force, only reference wordlists you are sure exist; otherwise prefer curl-based checks or an inline heredoc wordlist so the command cannot fail on a missing file.\n"
                    f"IMPORTANT: Output ONLY the command inside a ```bash code block. It MUST be directly executable — no placeholder tokens like [payload], <value>, or parenthetical notes. Do NOT include example flags or flag format references."
                )

                resp = await model_router.route_request(prompt=prompt, capability="recon", target_model="qwen-3.8-27b")

                # Detect ALL_EXHAUSTED / refusal — don't waste time on garbage
                if resp.is_refusal:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"All providers exhausted: {resp.refusal_reason[:100]}")
                    consecutive_errors += 1
                    await board.update_worker_state(worker_id, status="IDLE",
                                                    failures=consecutive_errors,
                                                    current_task=f"Provider error: {resp.refusal_reason[:80]}")
                    await board.complete_task(task.task_id, result=f"Provider error: {resp.refusal_reason[:80]}")
                    await asyncio.sleep(5)
                    continue

                cmd = self._extract_command(resp.content) or f"curl -s -i {board.target_scope}"

                # Broadcast AI Decision to UI
                try:
                    await ws_manager.broadcast({
                        "event": "AI_DECISION",
                        "challenge_id": board.challenge_id,
                        "agent": "SWARM_RECON",
                        "goal": task.description,
                        "capability": "recon",
                        "result": cmd,
                        "confidence": 92,
                        "model": getattr(resp, "model_name", "qwen-3.8-27b")
                    })
                except Exception:
                    pass

                if cmd not in board.executed_commands_dedup:
                    board.executed_commands_dedup.add(cmd)
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Executing: {cmd[:200]}")
                    res = await tool_manager.execute_tool("bash", {"command": cmd}, timeout=25, working_directory=workdir)
                    output = res.stdout or res.stderr or ""

                    # Persist the execution so terminal history survives refreshes/restarts
                    await board.record_tool_execution(worker_id, cmd, res)
                    await board.update_worker_state(
                        worker_id, status="RUNNING", last_tool=cmd[:160],
                        last_result=(output[:200] or f"[Exit {res.exit_code}] no output"),
                        commands_run=board.worker_states.get(worker_id, {}).get("commands_run", 0) + 1
                    )

                    _append_to_challenge_log(board.challenge_id, worker_id, f"Output ({len(output)} bytes, exit={res.exit_code}): {output[:300]}")

                    # Broadcast Terminal Log to UI
                    try:
                        await ws_manager.broadcast({
                            "event": "LOG_OUTPUT",
                            "challenge_id": board.challenge_id,
                            "run_id": board.run_id,
                            "command": cmd,
                            "output": output[:3000] if output else f"[Exit Code {res.exit_code}] Execution complete with no output.",
                            "exit_code": res.exit_code,
                            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S")
                        })
                    except Exception:
                        pass

                    # Only scan TOOL OUTPUT for flags (never LLM prose)
                    self._check_tool_output_for_flags(output, board, worker_id)

                    # Discover comments or potential leads
                    if "<!--" in output:
                        comments = re.findall(r"<!--(.*?)-->", output, re.DOTALL)
                        for c in comments:
                            c_clean = c.strip()
                            if len(c_clean) > 3:
                                await board.add_task("CODE_AUDIT", f"Analyze suspicious HTML comment: '{c_clean[:120]}'", priority=4)
                                # Deterministically decode the comment right away —
                                # a ROT13/base64/hex hint becomes an actionable lead
                                # without waiting on (or trusting) LLM formatting.
                                await self._apply_decoded_directives(c_clean, board, worker_id)

                    await board.complete_task(task.task_id, result="Recon executed", discoveries={"endpoints": re.findall(r'href=["\'](/[^"\']+)["\']', output)})
                    consecutive_errors = 0  # Reset on success
                else:
                    await board.complete_task(task.task_id, result="Command skipped (dedup)")

            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"[{worker_id}] Error #{consecutive_errors}: {e}")
                _append_to_challenge_log(board.challenge_id, worker_id, f"Error #{consecutive_errors}: {e}")
                await board.update_worker_state(worker_id, status="IDLE", failures=consecutive_errors,
                                                current_task=f"Error: {str(e)[:100]}")
                # Fail the task so it doesn't stay CLAIMED forever
                if task:
                    await board.complete_task(task.task_id, result=f"Error: {str(e)[:100]}")
                await asyncio.sleep(min(3 * consecutive_errors, 15))

    async def _code_crypto_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 2: Code Audit, Deobfuscation & Cryptanalysis (Uses Mistral Codestral)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        _append_to_challenge_log(board.challenge_id, worker_id, "Worker started")
        await board.update_worker_state(worker_id, status="RUNNING", current_capability="code_analysis",
                                        selected_model="Mistral Codestral / xKiro", current_task="Booting code-crypto worker")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 5
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["CODE_AUDIT", "CRYPTO_DECODE"])
            if not task:
                await board.update_worker_state(worker_id, status="IDLE", current_task="Idle — waiting for tasks")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Too many errors ({consecutive_errors}), worker pausing for 30s")
                    await asyncio.sleep(30)
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(2.0)
                continue

            try:
                _append_to_challenge_log(board.challenge_id, worker_id, f"Claimed task: {task.description[:100]}")
                await board.update_worker_state(worker_id, status="ANALYZING", current_task=task.description[:140])

                # Deterministic decode FIRST: if the task carries a captured artifact
                # (e.g. an HTML comment), decode ROT13/base64/hex and act on any
                # concrete header/flag now — independent of how the LLM formats output.
                await self._apply_decoded_directives(task.description, board, worker_id)

                prompt = (
                    f"You are the Code & Cryptanalysis Specialist in a CTF Swarm.\n"
                    f"Task: {task.description}\n"
                    f"Target Headers: {board.extracted_headers}\n"
                    f"Analyze any obfuscated strings, ROT13, Base64, JWT tokens, or JS scripts. Fully decode them.\n"
                    f"If you decode or observe a concrete exploit header, output exactly one line: HEADER: <Key>: <Value>\n"
                    f"The <Value> MUST be the literal, concrete value only — never a placeholder like [payload], <value>, "
                    f"an IP you are guessing, or an explanatory note in parentheses. If you have no concrete header, omit the HEADER line.\n"
                    f"If you decode a real flag value, output it as: DECODED_FLAG: <the_actual_flag>\n"
                    f"IMPORTANT: Do NOT output example, guessed, or placeholder values. Only output values you actually decoded or observed."
                )

                resp = await model_router.route_request(prompt=prompt, capability="code_analysis", target_model="codestral-latest")

                # Detect ALL_EXHAUSTED / refusal
                if resp.is_refusal:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"All providers exhausted: {resp.refusal_reason[:100]}")
                    consecutive_errors += 1
                    await board.update_worker_state(worker_id, status="IDLE", failures=consecutive_errors,
                                                    current_task=f"Provider error: {resp.refusal_reason[:80]}")
                    await board.complete_task(task.task_id, result=f"Provider error: {resp.refusal_reason[:80]}")
                    await asyncio.sleep(5)
                    continue

                analysis = resp.content

                _append_to_challenge_log(board.challenge_id, worker_id, f"Analysis ({len(analysis)} chars): {analysis[:300]}")

                # Broadcast AI Decision to UI
                try:
                    await ws_manager.broadcast({
                        "event": "AI_DECISION",
                        "challenge_id": board.challenge_id,
                        "agent": "SWARM_CODE_CRYPTO",
                        "goal": task.description,
                        "capability": "code_analysis",
                        "result": analysis[:250],
                        "confidence": 95,
                        "model": getattr(resp, "model_name", "codestral-latest")
                    })
                except Exception:
                    pass

                # Do NOT scan LLM prose with FLAG_REGEX — it produces false positives.
                # Instead, only check if the model explicitly declared a decoded flag via DECODED_FLAG: prefix.
                decoded_flag_match = re.search(r"DECODED_FLAG:\s*(\S+)", analysis)
                if decoded_flag_match:
                    candidate = decoded_flag_match.group(1).strip()
                    if FLAG_REGEX.search(candidate):
                        await board.record_flag_candidate(candidate, worker_id, "llm_decoded")

                # A model-suggested header is a *candidate*, not a discovery. Route it
                # through note_exploit_header, which sanitizes the value, rejects
                # placeholder prose (e.g. "127.0.0.1; [malicious payload]"), and de-dups
                # by signature — so a suggestion can never spawn an injection loop.
                header_match = re.search(r"HEADER:\s*([A-Za-z0-9_-]+)\s*:\s*([^\n\r]+)", analysis, re.IGNORECASE)
                if header_match:
                    await board.note_exploit_header(header_match.group(1), header_match.group(2), worker_id)

                await board.update_worker_state(worker_id, status="RUNNING",
                                                last_result=(analysis[:200] or "Analysis complete"),
                                                commands_run=board.worker_states.get(worker_id, {}).get("commands_run", 0) + 1)
                await board.complete_task(task.task_id, result=analysis[:200])
                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"[{worker_id}] Error #{consecutive_errors}: {e}")
                _append_to_challenge_log(board.challenge_id, worker_id, f"Error #{consecutive_errors}: {e}")
                await board.update_worker_state(worker_id, status="IDLE", failures=consecutive_errors,
                                                current_task=f"Error: {str(e)[:100]}")
                if task:
                    await board.complete_task(task.task_id, result=f"Error: {str(e)[:100]}")
                await asyncio.sleep(min(3 * consecutive_errors, 15))

    async def _exploit_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 3: Exploitation, PWN & Payload Delivery (Uses xKiro Qwen Coder / DeepSeek)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        _append_to_challenge_log(board.challenge_id, worker_id, "Worker started")
        await board.update_worker_state(worker_id, status="RUNNING", current_capability="web_testing",
                                        selected_model="xKiro Qwen Coder / DeepSeek", current_task="Booting exploit worker")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 5
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["EXPLOIT", "PWN"])
            if not task:
                await board.update_worker_state(worker_id, status="IDLE", current_task="Idle — waiting for tasks")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Too many errors ({consecutive_errors}), worker pausing for 30s")
                    await asyncio.sleep(30)
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(2.0)
                continue

            try:
                _append_to_challenge_log(board.challenge_id, worker_id, f"Claimed task: {task.description[:100]}")
                await board.update_worker_state(worker_id, status="ANALYZING", current_task=task.description[:140])

                headers_str = " ".join([f"-H '{k}: {v}'" for k, v in board.extracted_headers.items()])
                prompt = (
                    f"You are the Exploitation Solver in a CTF Swarm.\n"
                    f"Target: {board.target_scope}\n"
                    f"Task: {task.description}\n"
                    f"Available Headers to inject: {board.extracted_headers}\n"
                    f"Construct a single curl command or Python solver payload to submit credentials, bypass authentication, and extract the CTF flag.\n"
                    f"IMPORTANT: Output ONLY the command inside a ```bash code block. It MUST be directly executable — no placeholder tokens like [payload], <value>, or parenthetical notes. Do NOT include example flags or flag format references."
                )

                resp = await model_router.route_request(prompt=prompt, capability="web_testing", target_model="xkiro-qwen-coder")

                # Detect ALL_EXHAUSTED / refusal
                if resp.is_refusal:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"All providers exhausted: {resp.refusal_reason[:100]}")
                    consecutive_errors += 1
                    await board.update_worker_state(worker_id, status="IDLE", failures=consecutive_errors,
                                                    current_task=f"Provider error: {resp.refusal_reason[:80]}")
                    await board.complete_task(task.task_id, result=f"Provider error: {resp.refusal_reason[:80]}")
                    await asyncio.sleep(5)
                    continue

                cmd = self._extract_command(resp.content) or f"curl -s -i {headers_str} {board.target_scope}"

                # If this task carries a concrete decoded/known header, guarantee it
                # actually rides on the request — even if the model's command omitted
                # it. This is what forces the winning "X-Dev-Access: yes" onto the wire.
                h_name = task.metadata.get("header_name")
                h_val = task.metadata.get("header_value")
                if h_name and h_val and cmd.strip().startswith("curl") and h_name.lower() not in cmd.lower():
                    cmd = cmd.replace("curl", f"curl -H '{h_name}: {h_val}'", 1)

                # Broadcast AI Decision to UI
                try:
                    await ws_manager.broadcast({
                        "event": "AI_DECISION",
                        "challenge_id": board.challenge_id,
                        "agent": "SWARM_EXPLOIT",
                        "goal": task.description,
                        "capability": "web_testing",
                        "result": cmd,
                        "confidence": 90,
                        "model": getattr(resp, "model_name", "xkiro-qwen-coder")
                    })
                except Exception:
                    pass

                if cmd not in board.executed_commands_dedup:
                    board.executed_commands_dedup.add(cmd)
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Executing: {cmd[:200]}")
                    res = await tool_manager.execute_tool("bash", {"command": cmd}, timeout=25, working_directory=workdir)
                    output = res.stdout or res.stderr or ""

                    # Persist the execution so terminal history survives refreshes/restarts
                    await board.record_tool_execution(worker_id, cmd, res)
                    await board.update_worker_state(
                        worker_id, status="RUNNING", last_tool=cmd[:160],
                        last_result=(output[:200] or f"[Exit {res.exit_code}] no output"),
                        commands_run=board.worker_states.get(worker_id, {}).get("commands_run", 0) + 1
                    )

                    _append_to_challenge_log(board.challenge_id, worker_id, f"Output ({len(output)} bytes, exit={res.exit_code}): {output[:300]}")

                    # Broadcast Terminal Log to UI
                    try:
                        await ws_manager.broadcast({
                            "event": "LOG_OUTPUT",
                            "challenge_id": board.challenge_id,
                            "run_id": board.run_id,
                            "command": cmd,
                            "output": output[:3000] if output else f"[Exit Code {res.exit_code}] Execution complete with no output.",
                            "exit_code": res.exit_code,
                            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S")
                        })
                    except Exception:
                        pass

                    # Only scan TOOL OUTPUT for flags (never LLM prose)
                    self._check_tool_output_for_flags(output, board, worker_id)
                    await board.complete_task(task.task_id, result=f"Exploit response code {res.exit_code}")
                    consecutive_errors = 0
                else:
                    await board.complete_task(task.task_id, result="Command skipped (dedup)")

            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"[{worker_id}] Error #{consecutive_errors}: {e}")
                _append_to_challenge_log(board.challenge_id, worker_id, f"Error #{consecutive_errors}: {e}")
                await board.update_worker_state(worker_id, status="IDLE", failures=consecutive_errors,
                                                current_task=f"Error: {str(e)[:100]}")
                if task:
                    await board.complete_task(task.task_id, result=f"Error: {str(e)[:100]}")
                await asyncio.sleep(min(3 * consecutive_errors, 15))

    async def _task_refiller(self, board: SwarmBlackboard, workdir: str):
        """Keep the task pool fed with fresh exploratory tasks while the swarm runs.

        The swarm must never stop on its own — it runs until the flag is captured or
        the operator pauses / kills it. So the refiller cycles its prompt list
        indefinitely (round-robin), re-seeding whenever the pool drains. It only adds
        when there is no pending work, so a productive swarm isn't spammed, and the
        15s interval + per-worker error backoff keep provider usage gentle during
        outages instead of hammering.
        """
        refill_prompts = [
            ("RECON", "Probe HTTP methods, headers, cookies, and hidden parameters on all discovered endpoints"),
            ("CODE_AUDIT", "Analyze response headers, cookies, and any JS/source references for tokens or logic flaws"),
            ("EXPLOIT", "Attempt auth bypass: default creds, SQLi, SSTI, JWT manipulation, and IDOR parameter fuzzing"),
            ("RECON", "Enumerate additional paths and file extensions (php, bak, env, git, swagger) on the target"),
            ("EXPLOIT", "Test for command injection, path traversal, and file read primitives"),
            ("CODE_AUDIT", "Decode any base64/JWT/hex artifacts observed so far and check for hardcoded secrets"),
        ]
        logger.info(f"[SwarmOrchestrator] Task refiller started for challenge {board.challenge_id}")
        _append_to_challenge_log(board.challenge_id, "refiller", "Task refiller started (runs until flag or operator stop)")
        while not board.flag_captured and not board.is_stopped:
            await asyncio.sleep(board.refresh_interval)
            if board.flag_captured or board.is_stopped:
                break
            pending = sum(1 for t in board.task_pool.values() if t.status == "PENDING")
            if pending == 0:
                cat, prompt = refill_prompts[board.refill_count % len(refill_prompts)]
                await board.add_task(cat, prompt, priority=2)
                board.refill_count += 1
                _append_to_challenge_log(board.challenge_id, "refiller",
                                         f"Task pool refill #{board.refill_count}: {prompt[:100]}")
                logger.info(f"[SwarmOrchestrator] Refill #{board.refill_count} for {board.challenge_id}: {prompt[:60]}")
                await board._broadcast_blackboard()

    def _extract_command(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"```(?:bash|sh)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        lines = [l.strip() for l in text.strip().split("\n") if l.strip().startswith(("curl", "python", "ffuf", "nmap", "sqlmap"))]
        return lines[0] if lines else None

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
        on any concrete lead WITHOUT depending on the LLM to format its output.

        - A header directive (e.g. the decoded 'use header "X-Dev-Access: yes"')
          is queued as a prioritized injection task via note_exploit_header.
        - A real flag hidden by an encoding is recorded as a candidate.

        This is the deterministic path that turns the ROT13 comment into the
        winning move, instead of hoping the model carries the decode through.
        Returns True if any actionable lead was found.
        """
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
