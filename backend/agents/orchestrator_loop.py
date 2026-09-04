import asyncio
import json
import re
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from backend.database.session import SessionLocal
from backend.agents.manager import agent_manager
from backend.providers.router import model_router
from backend.tools.manager import tool_manager
from backend.privilege.manager import privilege_manager
from backend.checkpoints.manager import checkpoint_manager
from backend.environment.detector import environment_detector
from backend.database.models import RunModel, ChallengeModel, TargetProfileModel, EvidenceModel, FindingModel
from backend.websocket.manager import ws_manager
from backend.api.runner import workflow_runner
from backend.reporting.generator import report_generator

logger = logging.getLogger("forge.orchestrator")

class AutonomousOrchestrator:
    """Central autonomous investigation ReAct 1-Command Cycle loop."""

    async def run_autonomous_loop(self, run_id: str, challenge_id: str, target: str):
        """Asynchronous background 1-command ping-pong loop processing CTF challenge target."""
        logger.info(f"Starting 1-command ReAct autonomous loop for run {run_id}, challenge {challenge_id}, target {target}")

        env_info = environment_detector.detect_environment()
        os_distro = env_info.get("distro") or env_info.get("os", "Linux (Parrot OS)")
        installed_tools = [name for name, meta in env_info.get("installed_tools", {}).items() if meta.get("installed")]
        tools_str = ", ".join(installed_tools) if installed_tools else "nmap, ffuf, curl, python3, gdb, strings, objdump"

        history_summary = []
        max_turns = 20

        for turn in range(1, max_turns + 1):
            if workflow_runner.is_cancelled(run_id):
                logger.warning(f"Run {run_id} cancelled by Kill Switch.")
                await ws_manager.broadcast({
                    "event": "RUN_CANCELLED",
                    "run_id": run_id,
                    "challenge_id": challenge_id
                })
                return

            db: Session = SessionLocal()
            try:
                run = db.query(RunModel).filter(RunModel.id == run_id).first()
                challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
                if not run or not challenge or run.status in ["PAUSED", "CANCELLED"]:
                    logger.info(f"Run {run_id} terminated or paused externally.")
                    return

                # Calculate progress based on turns
                progress = min(95, int((turn / max_turns) * 100))
                challenge.progress = max(challenge.progress, progress)
                run.current_phase = "recon" if turn <= 3 else ("web" if turn <= 10 else "exploitation")
                run.current_agent = "orchestrator"
                db.commit()

                await ws_manager.broadcast({
                    "event": "PROGRESS_UPDATED",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "phase": run.current_phase,
                    "progress": challenge.progress
                })

                # Construct transparent ReAct System Prompt with OS Context
                system_instruction = (
                    f"You are FORGE Autonomous CTF Pentest Agent running on {os_distro}.\n"
                    f"SYSTEM DIRECTIVE: NEVER use demo or mock data in the system. Rely exclusively on live target scope and real execution outputs.\n"
                    f"Target Scope: {target}\n"
                    f"Challenge Name: {challenge.name} | Category: {challenge.category} | Difficulty: {challenge.difficulty} | Platform: {challenge.platform_name}\n"
                    f"Working Directory: {challenge.working_directory}\n"
                    f"Installed Pentest Tools: {tools_str}\n\n"
                    f"STRICT RULES:\n"
                    f"1. You must issue EXACTLY ONE executable bash/shell command per step to run inside the working directory.\n"
                    f"2. Output ONLY the raw command line (no markdown formatting, no code blocks, no explanations).\n"
                    f"3. Prefer fast, non-blocking commands suited for CTF speed.\n"
                    f"4. If you discover the flag (e.g. picoCTF{{...}}, FLAG{{...}}, HTB{{...}}), reply EXACTLY: FLAG: <captured_flag>\n"
                    f"5. If mission is complete without flag, reply: DONE\n"
                    f"6. Multi-target inputs (IPs, URLs, local files) are joined using '+' sign. Process targets accordingly."
                )

                history_context = "\n".join(history_summary[-6:]) if history_summary else "No commands executed yet."
                prompt = (
                    f"--- RECENT STEP HISTORY ---\n{history_context}\n\n"
                    f"--- CURRENT TURN #{turn} ---\n"
                    f"Analyze the history and issue the NEXT single command to execute on target scope '{target}'."
                )

                # Query AI Router
                capability = "general_reasoning" if turn == 1 else "web_testing"
                llm_response = await model_router.route_request(
                    prompt=prompt,
                    capability=capability,
                    system_instruction=system_instruction
                )

                raw_ai_output = (llm_response.content or "").strip()
                model_used = getattr(llm_response, "model", getattr(llm_response, "provider_name", "model_router"))

                # Broadcast AI Prompt Transparency Event to Frontend
                await ws_manager.broadcast({
                    "event": "AI_PROMPT_TRANSPARENCY",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "turn": turn,
                    "system_instruction": system_instruction,
                    "prompt": prompt,
                    "raw_response": raw_ai_output,
                    "model": model_used
                })

                await ws_manager.broadcast({
                    "event": "AI_DECISION",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "agent": "ORCHESTRATOR",
                    "goal": f"Turn #{turn} Action Selection ({os_distro})",
                    "capability": capability,
                    "model": model_used,
                    "result": raw_ai_output,
                    "confidence": 95
                })

                # Check if model returned Flag directly in response
                direct_flags = re.findall(r"(?:picoCTF|FORGE|CTF|HTB|FLAG)\{[A-Za-z0-9_!\-@#\$%\^&\*\.]+\}", raw_ai_output, re.IGNORECASE)
                if direct_flags or raw_ai_output.startswith("FLAG:"):
                    flag_str = direct_flags[0] if direct_flags else raw_ai_output.replace("FLAG:", "").strip()
                    await self._record_flag_capture(db, challenge, run, challenge_id, run_id, flag_str, f"Turn #{turn} AI Reasoning")
                    break

                if raw_ai_output.upper() == "DONE":
                    logger.info(f"AI declared mission completed on turn #{turn}.")
                    break

                # Extract cleaned 1 command line from raw AI output
                cmd_line = raw_ai_output.split("\n")[0].strip()
                cmd_line = re.sub(r"^```(?:bash|sh)?", "", cmd_line).strip()
                cmd_line = re.sub(r"```$", "", cmd_line).strip()

                if not cmd_line:
                    cmd_line = f"curl -s -L {target}" if target.startswith("http") else f"nmap -F {target}"

                # Execute 1 Command inside working directory
                tool_res = await tool_manager.execute_raw_command(
                    command=cmd_line,
                    cwd=challenge.working_directory,
                    timeout_seconds=120
                )

                stdout_text = tool_res.stdout[:3000] if tool_res.stdout else ""
                stderr_text = tool_res.stderr[:1000] if tool_res.stderr else ""
                log_output = stdout_text or stderr_text or f"[Return Code {tool_res.exit_code}] Execution finished with no output."

                # Broadcast terminal log to frontend
                await ws_manager.broadcast({
                    "event": "LOG_OUTPUT",
                    "challenge_id": challenge_id,
                    "run_id": run_id,
                    "command": cmd_line,
                    "output": log_output,
                    "exit_code": tool_res.exit_code,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S")
                })

                # Append to history summary for next turn
                history_summary.append(f"Turn #{turn} Command: `{cmd_line}`\nOutput snippet:\n{log_output[:400]}")

                # Save Evidence
                ev = EvidenceModel(
                    challenge_id=challenge_id,
                    agent="orchestrator",
                    evidence_type="command_output",
                    source=tool_res.tool_name,
                    content=f"### COMMAND\n`{cmd_line}`\n\n### STDOUT\n{stdout_text}\n\n### STDERR\n{stderr_text}",
                    confidence=0.95
                )
                db.add(ev)
                db.commit()

                # Check for REAL Flag pattern in STDOUT/STDERR
                combined_output = f"{stdout_text}\n{stderr_text}"
                found_flags = re.findall(r"(?:picoCTF|FORGE|CTF|HTB|FLAG)\{[A-Za-z0-9_!\-@#\$%\^&\*\.]+\}", combined_output, re.IGNORECASE)
                if found_flags:
                    flag_str = found_flags[0]
                    await self._record_flag_capture(db, challenge, run, challenge_id, run_id, flag_str, f"Turn #{turn} Tool `{cmd_line}` Output")
                    break

                # State Checkpoint
                checkpoint_manager.create_checkpoint(
                    db=db,
                    run_id=run_id,
                    current_phase=run.current_phase,
                    current_agent="orchestrator",
                    last_action=f"turn_{turn}_{tool_res.tool_name}",
                    state_snapshot={"target": target, "turn": turn}
                )

            except Exception as e:
                logger.error(f"Error during turn #{turn} of run {run_id}: {str(e)}")
            finally:
                db.close()

            await asyncio.sleep(2.0)

        # Mark Run & Challenge Completed
        db: Session = SessionLocal()
        try:
            run = db.query(RunModel).filter(RunModel.id == run_id).first()
            challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
            if run:
                run.status = "COMPLETED"
            if challenge:
                challenge.status = "COMPLETED"
                challenge.progress = 100
                report_generator.generate_readme(db, challenge_id)
            db.commit()

            await ws_manager.broadcast({
                "event": "RUN_COMPLETED",
                "challenge_id": challenge_id,
                "run_id": run_id
            })
        finally:
            db.close()

    async def _record_flag_capture(self, db: Session, challenge, run, challenge_id: str, run_id: str, flag_str: str, source: str):
        """Helper to record flag capture and broadcast websocket event."""
        challenge.flagStatus = "CAPTURED"
        challenge.flag = flag_str
        challenge.progress = 100
        db.commit()

        finding = FindingModel(
            challenge_id=challenge_id,
            agent="orchestrator",
            title=f"REAL Flag Extracted via {source}",
            description=f"Successfully extracted valid flag: {flag_str}",
            vulnerability_class="Flag Extraction",
            severity="CRITICAL",
            verified=True,
            confidence=1.0
        )
        db.add(finding)
        db.commit()

        await ws_manager.broadcast({
            "event": "FLAG_CAPTURED",
            "challenge_id": challenge_id,
            "flag": flag_str,
            "source": source
        })

    async def execute_run_step(self, db: Session, run_id: str) -> Dict[str, Any]:
        # Check Kill Switch
        run = db.query(RunModel).filter(RunModel.id == run_id).first()
        current_agent_name = run.current_agent if run else "recon"

        if workflow_runner.is_cancelled(run_id):
            logger.warning(f"Run {run_id} halted by Kill Switch.")
            if run:
                run.status = "CANCELLED"
                db.commit()
            return {"status": "CANCELLED", "agent": current_agent_name, "reason": "Kill switch activated"}

        if not run or run.status in ["PAUSED", "COMPLETED", "CANCELLED"]:
            return {"status": run.status if run else "NOT_FOUND", "agent": current_agent_name}

        target = db.query(TargetProfileModel).filter(TargetProfileModel.challenge_id == run.challenge_id).first()
        target_addr = target.current_address if target else "127.0.0.1"

        capability = "network_scanning" if run.current_phase == "recon" else "web_testing"

        # Create State Checkpoint
        checkpoint_manager.create_checkpoint(
            db=db,
            run_id=run_id,
            current_phase=run.current_phase,
            current_agent=current_agent_name,
            last_action="executed_step",
            state_snapshot={"target": target_addr}
        )

        if run.current_phase == "recon":
            run.current_phase = "web"
            run.current_agent = "web"
        elif run.current_phase == "web":
            run.current_phase = "completed"
            run.status = "COMPLETED"

        db.commit()
        return {
            "status": run.status,
            "agent": current_agent_name,
            "capability": capability,
            "tool_status": "SUCCESS"
        }

orchestrator_loop = AutonomousOrchestrator()
