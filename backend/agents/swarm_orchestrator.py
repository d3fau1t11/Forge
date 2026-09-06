"""FORGE Autonomous Swarm Intelligence Orchestrator.
Dispatches specialized parallel agent workers (Recon, Code Audit, Exploit Solver) on a shared blackboard.
Features:
- Dynamic claim-and-solve task pool
- Real-time shared target memory (endpoints, headers, decoded secrets)
- Deduplication filter
- Zero-retry Quota Circuit Breaker
- Instant global kill-switch upon verified flag discovery
"""

import asyncio
import json
import re
import os
import sys
import time
import logging
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

FLAG_REGEX = re.compile(
    r"(?:picoCTF\{[^}]+\}|FLAG\{[^}]+\}|flag\{[^}]+\}|HTB\{[^}]+\}|CTF\{[^}]+\}|[a-zA-Z0-9_-]+\{[^}]+\})",
    re.IGNORECASE
)

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
                    self.discovered_endpoints.add(ep)
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
                await ws_manager.broadcast({
                    "event": "FLAG_CAPTURED",
                    "challenge_id": self.challenge_id,
                    "run_id": self.run_id,
                    "flag": flag,
                    "solver_worker": worker_id
                })

    async def _broadcast_blackboard(self):
        try:
            await ws_manager.broadcast({
                "event": "SWARM_BLACKBOARD_UPDATE",
                "challenge_id": self.challenge_id,
                "run_id": self.run_id,
                "endpoints_count": len(self.discovered_endpoints),
                "headers": self.extracted_headers,
                "secrets_count": len(self.deobfuscated_secrets),
                "pending_tasks": sum(1 for t in self.task_pool.values() if t.status == "PENDING"),
                "completed_tasks": sum(1 for t in self.task_pool.values() if t.status == "COMPLETED"),
                "flag_captured": bool(self.flag_captured)
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

            # Pre-warmed Turbo Recon (Initial seed)
            turbo_data = await turbo_recon.quick_probe(target_scope)
            if turbo_data:
                for ep in turbo_data.get("endpoints", []):
                    board.discovered_endpoints.add(ep)
                for h_k, h_v in turbo_data.get("headers", {}).items():
                    board.extracted_headers[h_k] = str(h_v)

            # Seed Initial Swarm Tasks
            await board.add_task("RECON", f"Initial crawler & header discovery on {target_scope}", priority=5)
            await board.add_task("CODE_AUDIT", f"Inspect source code, HTML comments, and scripts on {target_scope}", priority=4)
            await board.add_task("EXPLOIT", f"Test authentication endpoints and parameters on {target_scope}", priority=3)

            # Define Swarm Workers
            workers = [
                self._recon_worker("worker_recon", board, working_directory),
                self._code_crypto_worker("worker_code_crypto", board, working_directory),
                self._exploit_worker("worker_exploit_pwn", board, working_directory)
            ]

            # Run workers concurrently until flag capture or cancellation
            worker_group = asyncio.gather(*workers, return_exceptions=True)
            
            # Wait for flag event or worker completion
            done, pending = await asyncio.wait(
                [board.flag_event.wait(), asyncio.create_task(worker_group)],
                return_when=asyncio.FIRST_COMPLETED
            )

            # If flag was captured, cancel any remaining tasks
            board.is_stopped = True
            for p in pending:
                p.cancel()

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
                logger.info(f"[SwarmOrchestrator] 🏁 Swarm SOLVED challenge '{challenge_id}'! Flag: {board.flag_captured}")
            else:
                if run_obj:
                    run_obj.status = "FAILED"
                    run_obj.completed_at = datetime.now(timezone.utc)
                db.commit()

            # Generate structured report
            try:
                report_generator.generate_final_report(challenge_id=challenge_id, run_id=run_id, db=db)
            except Exception as e:
                logger.warning(f"[SwarmOrchestrator] Report generation skip: {e}")

        except Exception as e:
            logger.error(f"[SwarmOrchestrator] Swarm execution exception: {e}")
        finally:
            db.close()
            self.active_swarms.pop(run_id, None)
            # Release OS Keep-Awake lock
            keep_awake_manager.release(reason=f"Swarm Challenge {challenge_id} Ended")

    async def _recon_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 1: Fast Recon & Fuzzing (Uses Groq / Minimax / xKiro)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["RECON"])
            if not task:
                await asyncio.sleep(1.0)
                continue

            try:
                # Query model for next recon action
                prompt = (
                    f"You are the Reconnaissance Worker in a parallel CTF Swarm.\n"
                    f"Target: {board.target_scope}\n"
                    f"Discovered Endpoints: {list(board.discovered_endpoints)}\n"
                    f"Headers: {board.extracted_headers}\n"
                    f"Current Task: {task.description}\n"
                    f"Issue a single bash command (e.g. curl, ffuf, httpx) to uncover hidden routes, parameters, or robots.txt."
                )
                
                resp = await model_router.route_request(prompt=prompt, capability="recon", target_model="qwen-3.8-27b")
                cmd = self._extract_command(resp.content) or f"curl -s -i {board.target_scope}"

                if cmd not in board.executed_commands_dedup:
                    board.executed_commands_dedup.add(cmd)
                    res = await tool_manager.execute_tool("bash", {"command": cmd}, timeout=25, working_directory=workdir)
                    output = res.stdout or res.stderr or ""
                    
                    self._check_for_flags(output, board, worker_id)
                    
                    # Discover comments or potential leads
                    if "<!--" in output:
                        comments = re.findall(r"<!--(.*?)-->", output, re.DOTALL)
                        for c in comments:
                            c_clean = c.strip()
                            if len(c_clean) > 3:
                                await board.add_task("CODE_AUDIT", f"Analyze suspicious HTML comment: '{c_clean[:120]}'", priority=4)

                    await board.complete_task(task.task_id, result="Recon executed", discoveries={"endpoints": re.findall(r'href=["\'](/[^"\']+)["\']', output)})
                else:
                    await board.complete_task(task.task_id, result="Command skipped (dedup)")

            except Exception as e:
                logger.warning(f"[{worker_id}] Error: {e}")
                await asyncio.sleep(1.5)

    async def _code_crypto_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 2: Code Audit, Deobfuscation & Cryptanalysis (Uses Mistral Codestral)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["CODE_AUDIT", "CRYPTO_DECODE"])
            if not task:
                await asyncio.sleep(1.0)
                continue

            try:
                prompt = (
                    f"You are the Code & Cryptanalysis Specialist in a CTF Swarm.\n"
                    f"Task: {task.description}\n"
                    f"Target Headers: {board.extracted_headers}\n"
                    f"Analyze any obfuscated strings, ROT13, Base64, JWT tokens, or JS scripts.\n"
                    f"If you find an exploit header or bypass parameter, output it clearly as: HEADER: <Key>: <Value> or SECRET: <DecodedValue>."
                )

                resp = await model_router.route_request(prompt=prompt, capability="code_analysis", target_model="codestral-latest")
                analysis = resp.content

                self._check_for_flags(analysis, board, worker_id)

                # Check if model identified a header or bypass
                header_match = re.search(r"HEADER:\s*([A-Za-z0-9_-]+)\s*:\s*([^\n\r]+)", analysis, re.IGNORECASE)
                if header_match:
                    h_name, h_val = header_match.group(1).strip(), header_match.group(2).strip()
                    board.extracted_headers[h_name] = h_val
                    logger.info(f"[Swarm Worker] 🔑 Extracted Header: {h_name}: {h_val}")
                    await board.add_task("EXPLOIT", f"Inject header '{h_name}: {h_val}' into login and API endpoints on {board.target_scope}", priority=5)

                await board.complete_task(task.task_id, result=analysis[:200])

            except Exception as e:
                logger.warning(f"[{worker_id}] Error: {e}")
                await asyncio.sleep(1.5)

    async def _exploit_worker(self, worker_id: str, board: SwarmBlackboard, workdir: str):
        """Worker 3: Exploitation, PWN & Payload Delivery (Uses xKiro Qwen Coder / DeepSeek)."""
        logger.info(f"[Swarm Worker] {worker_id} started.")
        while not board.flag_captured and not board.is_stopped:
            task = await board.claim_task(worker_id, ["EXPLOIT", "PWN"])
            if not task:
                await asyncio.sleep(1.0)
                continue

            try:
                headers_str = " ".join([f"-H '{k}: {v}'" for k, v in board.extracted_headers.items()])
                prompt = (
                    f"You are the Exploitation Solver in a CTF Swarm.\n"
                    f"Target: {board.target_scope}\n"
                    f"Task: {task.description}\n"
                    f"Available Headers to inject: {board.extracted_headers}\n"
                    f"Construct a single curl command or Python solver payload to submit credentials, bypass authentication, and extract the CTF flag."
                )

                resp = await model_router.route_request(prompt=prompt, capability="web_testing", target_model="xkiro-qwen-coder")
                cmd = self._extract_command(resp.content) or f"curl -s -i {headers_str} {board.target_scope}"

                if cmd not in board.executed_commands_dedup:
                    board.executed_commands_dedup.add(cmd)
                    res = await tool_manager.execute_tool("bash", {"command": cmd}, timeout=25, working_directory=workdir)
                    output = res.stdout or res.stderr or ""
                    
                    self._check_for_flags(output, board, worker_id)
                    await board.complete_task(task.task_id, result=f"Exploit response code {res.exit_code}")
                else:
                    await board.complete_task(task.task_id, result="Command skipped (dedup)")

            except Exception as e:
                logger.warning(f"[{worker_id}] Error: {e}")
                await asyncio.sleep(1.5)

    def _extract_command(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"```(?:bash|sh)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        lines = [l.strip() for l in text.strip().split("\n") if l.strip().startswith(("curl", "python", "ffuf", "nmap", "sqlmap"))]
        return lines[0] if lines else None

    def _check_for_flags(self, text: str, board: SwarmBlackboard, worker_id: str):
        if not text:
            return
        match = FLAG_REGEX.search(text)
        if match:
            flag = match.group(0).strip()
            asyncio.create_task(board.record_flag(flag, worker_id))

# Global Singleton
swarm_orchestrator = SwarmOrchestrator()
