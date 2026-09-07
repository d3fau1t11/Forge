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
from backend.database.models import RunModel, ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel
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

    async def _broadcast_blackboard(self):
        try:
            total_tasks = len(self.task_pool)
            completed_tasks = sum(1 for t in self.task_pool.values() if t.status == "COMPLETED")
            progress = int((completed_tasks / max(total_tasks, 1)) * 90) if not self.flag_captured else 100

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

            mission_plan = {
                "challenge_id": self.challenge_id,
                "status": "IN_PROGRESS" if not self.flag_captured else "COMPLETED",
                "summary": f"Swarm Intelligence Solver active on {self.target_scope}",
                "tasks": plan_tasks,
                "strategic_reviews": []
            }

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
        difficulty: str = "EASY"
    ):
        """Dispatches parallel Swarm workers on the target."""
        logger.info(f"[SwarmOrchestrator] 🚀 Starting Swarm for Challenge '{challenge_id}' on '{target_scope}'")
        _append_to_challenge_log(challenge_id, "orchestrator", f"Swarm starting | target={target_scope} | category={category} | difficulty={difficulty}")

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

            # Seed Initial Swarm Tasks
            await board.add_task("RECON", f"Initial crawler & header discovery on {target_scope}", priority=5)
            await board.add_task("CODE_AUDIT", f"Inspect source code, HTML comments, and scripts on {target_scope}", priority=4)
            await board.add_task("EXPLOIT", f"Test authentication endpoints and parameters on {target_scope}", priority=3)
            _append_to_challenge_log(challenge_id, "orchestrator", "Initial task pool seeded (RECON, CODE_AUDIT, EXPLOIT)")

            # Define Swarm Workers
            workers = [
                self._recon_worker("worker_recon", board, working_directory),
                self._code_crypto_worker("worker_code_crypto", board, working_directory),
                self._exploit_worker("worker_exploit_pwn", board, working_directory)
            ]

            # Run workers concurrently until flag capture or cancellation.
            # asyncio.gather() already returns an awaitable _GatheringFuture; use
            # ensure_future (NOT create_task, which rejects a Future) so it can be
            # passed to asyncio.wait() below alongside flag_task.
            worker_task = asyncio.ensure_future(asyncio.gather(*workers, return_exceptions=True))
            flag_task = asyncio.create_task(board.flag_event.wait())

            _append_to_challenge_log(challenge_id, "orchestrator", "All 3 swarm workers dispatched")

            # Wait for flag event or worker completion
            done, pending = await asyncio.wait(
                [flag_task, worker_task],
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
                    run_obj.final_flag = board.flag_captured
                if ch_obj:
                    ch_obj.status = "SOLVED"
                    ch_obj.flag = board.flag_captured
                db.commit()
                _append_to_challenge_log(challenge_id, "orchestrator", f"🏁 SOLVED! Flag: {board.flag_captured}")
                logger.info(f"[SwarmOrchestrator] 🏁 Swarm SOLVED challenge '{challenge_id}'! Flag: {board.flag_captured}")
            else:
                if run_obj:
                    run_obj.status = "FAILED"
                    run_obj.completed_at = datetime.now(timezone.utc)
                db.commit()
                candidates_summary = ", ".join([c["flag"] for c in board.flag_candidates]) if board.flag_candidates else "none"
                _append_to_challenge_log(challenge_id, "orchestrator", f"Swarm finished without verified flag. Candidates: {candidates_summary}")

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

    async def _recon_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 1: Fast Recon & Fuzzing (Uses Groq / Minimax / xKiro)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        _append_to_challenge_log(board.challenge_id, worker_id, "Worker started")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 5
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["RECON"])
            if not task:
                # No pending tasks — wait and check again
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Too many errors ({consecutive_errors}), worker pausing for 30s")
                    await asyncio.sleep(30)
                    consecutive_errors = 0  # Reset after long pause
                else:
                    await asyncio.sleep(2.0)
                continue

            try:
                _append_to_challenge_log(board.challenge_id, worker_id, f"Claimed task: {task.description[:100]}")

                # Query model for next recon action
                prompt = (
                    f"You are the Reconnaissance Worker in a parallel CTF Swarm.\n"
                    f"Target: {board.target_scope}\n"
                    f"Discovered Endpoints: {list(board.discovered_endpoints)}\n"
                    f"Headers: {board.extracted_headers}\n"
                    f"Current Task: {task.description}\n"
                    f"Issue a single bash command (e.g. curl, ffuf, httpx) to uncover hidden routes, parameters, or robots.txt.\n"
                    f"IMPORTANT: Output ONLY the command inside a ```bash code block. Do NOT include example flags or flag format references."
                )

                resp = await model_router.route_request(prompt=prompt, capability="recon", target_model="qwen-3.8-27b")

                # Detect ALL_EXHAUSTED / refusal — don't waste time on garbage
                if resp.is_refusal:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"All providers exhausted: {resp.refusal_reason[:100]}")
                    consecutive_errors += 1
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

                    await board.complete_task(task.task_id, result="Recon executed", discoveries={"endpoints": re.findall(r'href=["\'](/[^"\']+)["\']', output)})
                    consecutive_errors = 0  # Reset on success
                else:
                    await board.complete_task(task.task_id, result="Command skipped (dedup)")

            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"[{worker_id}] Error #{consecutive_errors}: {e}")
                _append_to_challenge_log(board.challenge_id, worker_id, f"Error #{consecutive_errors}: {e}")
                # Fail the task so it doesn't stay CLAIMED forever
                if task:
                    await board.complete_task(task.task_id, result=f"Error: {str(e)[:100]}")
                await asyncio.sleep(min(3 * consecutive_errors, 15))

    async def _code_crypto_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 2: Code Audit, Deobfuscation & Cryptanalysis (Uses Mistral Codestral)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        _append_to_challenge_log(board.challenge_id, worker_id, "Worker started")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 5
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["CODE_AUDIT", "CRYPTO_DECODE"])
            if not task:
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Too many errors ({consecutive_errors}), worker pausing for 30s")
                    await asyncio.sleep(30)
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(2.0)
                continue

            try:
                _append_to_challenge_log(board.challenge_id, worker_id, f"Claimed task: {task.description[:100]}")

                prompt = (
                    f"You are the Code & Cryptanalysis Specialist in a CTF Swarm.\n"
                    f"Task: {task.description}\n"
                    f"Target Headers: {board.extracted_headers}\n"
                    f"Analyze any obfuscated strings, ROT13, Base64, JWT tokens, or JS scripts.\n"
                    f"If you find an exploit header or bypass parameter, output it clearly as: HEADER: <Key>: <Value> or SECRET: <DecodedValue>.\n"
                    f"If you decode a real flag value, output it as: DECODED_FLAG: <the_actual_flag>\n"
                    f"IMPORTANT: Do NOT output example or placeholder flags. Only output real decoded values."
                )

                resp = await model_router.route_request(prompt=prompt, capability="code_analysis", target_model="codestral-latest")

                # Detect ALL_EXHAUSTED / refusal
                if resp.is_refusal:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"All providers exhausted: {resp.refusal_reason[:100]}")
                    consecutive_errors += 1
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

                # Check if model identified a header or bypass
                header_match = re.search(r"HEADER:\s*([A-Za-z0-9_-]+)\s*:\s*([^\n\r]+)", analysis, re.IGNORECASE)
                if header_match:
                    h_name, h_val = header_match.group(1).strip(), header_match.group(2).strip()
                    board.extracted_headers[h_name] = h_val
                    logger.info(f"[Swarm Worker] 🔑 Extracted Header: {h_name}: {h_val}")
                    _append_to_challenge_log(board.challenge_id, worker_id, f"🔑 Header found: {h_name}: {h_val}")
                    await board.add_task("EXPLOIT", f"Inject header '{h_name}: {h_val}' into login and API endpoints on {board.target_scope}", priority=5)

                await board.complete_task(task.task_id, result=analysis[:200])
                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                logger.warning(f"[{worker_id}] Error #{consecutive_errors}: {e}")
                _append_to_challenge_log(board.challenge_id, worker_id, f"Error #{consecutive_errors}: {e}")
                if task:
                    await board.complete_task(task.task_id, result=f"Error: {str(e)[:100]}")
                await asyncio.sleep(min(3 * consecutive_errors, 15))

    async def _exploit_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 3: Exploitation, PWN & Payload Delivery (Uses xKiro Qwen Coder / DeepSeek)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        _append_to_challenge_log(board.challenge_id, worker_id, "Worker started")
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 5
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["EXPLOIT", "PWN"])
            if not task:
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"Too many errors ({consecutive_errors}), worker pausing for 30s")
                    await asyncio.sleep(30)
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(2.0)
                continue

            try:
                _append_to_challenge_log(board.challenge_id, worker_id, f"Claimed task: {task.description[:100]}")

                headers_str = " ".join([f"-H '{k}: {v}'" for k, v in board.extracted_headers.items()])
                prompt = (
                    f"You are the Exploitation Solver in a CTF Swarm.\n"
                    f"Target: {board.target_scope}\n"
                    f"Task: {task.description}\n"
                    f"Available Headers to inject: {board.extracted_headers}\n"
                    f"Construct a single curl command or Python solver payload to submit credentials, bypass authentication, and extract the CTF flag.\n"
                    f"IMPORTANT: Output ONLY the command inside a ```bash code block. Do NOT include example flags or flag format references."
                )

                resp = await model_router.route_request(prompt=prompt, capability="web_testing", target_model="xkiro-qwen-coder")

                # Detect ALL_EXHAUSTED / refusal
                if resp.is_refusal:
                    _append_to_challenge_log(board.challenge_id, worker_id, f"All providers exhausted: {resp.refusal_reason[:100]}")
                    consecutive_errors += 1
                    await board.complete_task(task.task_id, result=f"Provider error: {resp.refusal_reason[:80]}")
                    await asyncio.sleep(5)
                    continue

                cmd = self._extract_command(resp.content) or f"curl -s -i {headers_str} {board.target_scope}"

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
                if task:
                    await board.complete_task(task.task_id, result=f"Error: {str(e)[:100]}")
                await asyncio.sleep(min(3 * consecutive_errors, 15))

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

# Global Singleton
swarm_orchestrator = SwarmOrchestrator()
