import os
import re
import sys
import time
import shutil
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from backend.database.session import SessionLocal
from backend.database.models import RunModel, ChallengeModel, EvidenceModel, FindingModel
from backend.websocket.manager import ws_manager
from backend.api.runner import workflow_runner
from backend.config import settings
from backend.reporting.generator import report_generator
from backend.providers.cli.base import redact_secrets

logger = logging.getLogger("forge.cli_agent_runner")

FLAG_REGEX = re.compile(
    r"(picoCTF\{[^\}\s]+\}|flag\{[^\}\s]+\}|CTF\{[^\}\s]+\}|HTB\{[^\}\s]+\}|FLAG\{[^\}\s]+\}|THM\{[^\}\s]+\})",
    re.IGNORECASE
)

class CLIAgentRunner:
    """Runs Claude Code CLI (`claude`) or Codex CLI (`codex`) as autonomous CTF investigation agents."""

    def find_executable(self, binary_name: str, custom_path: Optional[str] = None) -> Optional[str]:
        if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
            return custom_path
        which_path = shutil.which(binary_name)
        if which_path:
            return which_path
        # Windows .cmd / .ps1 search
        if sys.platform == "win32":
            which_cmd = shutil.which(f"{binary_name}.cmd") or shutil.which(f"{binary_name}.exe")
            if which_cmd:
                return which_cmd
        return None

    def resolve_agent_credentials(self, agent_type: str, model_name: Optional[str] = None) -> Dict[str, str]:
        """Resolves environment variables for Claude Code or Codex."""
        env = os.environ.copy()

        if agent_type == "claude_code":
            api_key = (
                getattr(settings, "AGENTROUTER_CLAUDE_OPUS_5_KEY", None)
                or getattr(settings, "AGENTROUTER_CLAUDE_OPUS_4_8_KEY", None)
                or getattr(settings, "AGENTROUTER_API_KEY", None)
                or os.environ.get("AGENTROUTER_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY")
                or ""
            ).strip()

            env["ANTHROPIC_API_KEY"] = ""
            env["ANTHROPIC_BASE_URL"] = "https://agentrouter.org"
            env["ANTHROPIC_AUTH_TOKEN"] = api_key
            env["AGENTROUTER_API_KEY"] = api_key

        elif agent_type == "codex":
            api_key = (
                getattr(settings, "AGENTROUTER_GPT_5_6_KEY", None)
                or getattr(settings, "AGENTROUTER_GPT_5_6_SOL_KEY", None)
                or getattr(settings, "AGENTROUTER_DEEPSEEK_V4_FLASH_KEY", None)
                or getattr(settings, "AGENTROUTER_API_KEY", None)
                or os.environ.get("AGENTROUTER_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or ""
            ).strip()

            env["OPENAI_API_KEY"] = api_key
            env["OPENAI_BASE_URL"] = "https://agentrouter.org/v1"
            env["AGENTROUTER_API_KEY"] = api_key

        return env

    async def run_cli_agent_loop(
        self,
        run_id: str,
        challenge_id: str,
        target: str,
        agent_type: str = "claude_code",
        model: Optional[str] = None
    ):
        """Launches Claude Code CLI or Codex CLI directly inside the challenge workspace directory."""
        logger.info(f"Starting Autonomous CLI Agent ({agent_type}) for run {run_id}, challenge {challenge_id}, target {target}")

        db: Session = SessionLocal()
        try:
            challenge = db.query(ChallengeModel).filter(ChallengeModel.id == challenge_id).first()
            if not challenge:
                logger.error(f"Challenge {challenge_id} not found.")
                return

            working_dir = challenge.working_directory or os.path.abspath(os.path.join("workspaces", challenge.id))
            os.makedirs(working_dir, exist_ok=True)
            challenge.working_directory = working_dir
            challenge.status = "RUNNING"
            db.commit()

            # Ensure solver scripts and notes file exist
            notes_file = os.path.join(working_dir, "MISSION.md")
            mission_prompt = (
                f"# FORGE AUTONOMOUS CTF MISSION OBJECTIVE\n\n"
                f"- **Challenge Name**: {challenge.name}\n"
                f"- **Platform / Competition**: {challenge.platform_name or 'CTF'}\n"
                f"- **Category**: {challenge.category}\n"
                f"- **Difficulty**: {challenge.difficulty}\n"
                f"- **Resolved Target**: {target}\n"
                f"- **Working Directory**: `{working_dir}`\n\n"
                f"## Challenge Brief & Details\n"
                f"{challenge.description or 'Extract the hidden CTF flag from the target.'}\n\n"
                f"## Instructions for Autonomous Agent\n"
                f"1. You are operating inside the challenge workspace directory `{working_dir}`.\n"
                f"2. Inspect the target and instructions above using curl, nmap, dirb/ffuf, or python3 scripts.\n"
                f"3. Exploit vulnerabilities, bypass authentications, or reverse engineer files to capture the flag.\n"
                f"4. Once the flag is found (e.g. `picoCTF{{...}}`, `flag{{...}}`, `CTF{{...}}`), print it clearly in your output.\n"
            )
            try:
                with open(notes_file, "w", encoding="utf-8") as f:
                    f.write(mission_prompt)
            except Exception as e:
                logger.warning(f"Failed to write MISSION.md: {e}")

            binary_name = "claude" if agent_type == "claude_code" else "codex"
            custom_path = settings.CLAUDE_CODE_PATH if agent_type == "claude_code" else settings.CODEX_PATH
            executable = self.find_executable(binary_name, custom_path)

            if not executable:
                err_msg = f"Autonomous CLI Agent binary `{binary_name}` was not found in PATH or configured paths."
                logger.error(err_msg)
                await ws_manager.broadcast({
                    "event": "LOG_OUTPUT",
                    "challenge_id": challenge_id,
                    "command": f"{binary_name} --init",
                    "output": f"[ERROR] {err_msg}\nPlease install `{binary_name}` (e.g. `npm install -g @anthropic-ai/claude-code` or `@openai/codex-cli`). Falling back to internal ReAct engine.",
                    "exit_code": 1,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S")
                })
                # Fallback to AutonomousOrchestrator
                from backend.agents.orchestrator_loop import orchestrator_loop
                await orchestrator_loop.run_autonomous_loop(run_id, challenge_id, target)
                return

            sub_env = self.resolve_agent_credentials(agent_type, model)
            redact_list = [
                sub_env.get("ANTHROPIC_AUTH_TOKEN", ""),
                sub_env.get("OPENAI_API_KEY", ""),
                sub_env.get("AGENTROUTER_API_KEY", "")
            ]

            # Construct CLI launch arguments
            initial_prompt = (
                f"Solve CTF challenge '{challenge.name}' ({challenge.category} - {challenge.difficulty}) on {challenge.platform_name or 'CTF'}.\n"
                f"Target / Scope: {target}\n"
                f"Challenge Brief: {challenge.description or 'Find and extract the flag.'}\n\n"
                f"Inspect files in this workspace, scan the target, find vulnerabilities, and extract the CTF flag."
            )

            if agent_type == "claude_code":
                model_arg = model or "claude-opus-5"
                cmd_args = [
                    executable,
                    "-p", initial_prompt,
                    "--output-format", "text"
                ]
            else: # codex
                model_arg = model or "deepseek-v4-flash"
                cmd_args = [
                    executable,
                    "exec",
                    "--skip-git-repo-check",
                    "--model", model_arg,
                    initial_prompt
                ]

            await ws_manager.broadcast({
                "event": "AI_DECISION",
                "challenge_id": challenge_id,
                "agent": agent_type.upper(),
                "goal": f"Autonomous CLI Investigation via {binary_name}",
                "capability": "cli_autonomous_agent",
                "model": model or agent_type,
                "result": f"Spawning autonomous {binary_name} process in {working_dir}...",
                "confidence": 99
            })

            logs_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
            os.makedirs(logs_dir, exist_ok=True)
            ch_log_path = os.path.join(logs_dir, f"challenge_{challenge_id}.log")

            # Spawn subprocess in challenge working directory
            logger.info(f"Spawning `{binary_name}` process in cwd: {working_dir}")
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                cwd=working_dir,
                env=sub_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            captured_flag: Optional[str] = None
            accumulated_output = []

            async def stream_reader(stream, is_stderr=False):
                nonlocal captured_flag
                while True:
                    if workflow_runner.is_kill_switch_active(run_id):
                        logger.warning(f"Kill switch triggered. Terminating {binary_name} process.")
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break

                    line_bytes = await stream.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode(errors="replace")
                    clean_line = redact_secrets(line, redact_list)
                    accumulated_output.append(clean_line)

                    # Log to dedicated challenge log file
                    try:
                        with open(ch_log_path, "a", encoding="utf-8") as f:
                            f.write(f"[{datetime.utcnow().strftime('%H:%M:%S')}] [{binary_name}] {clean_line}")
                    except Exception:
                        pass

                    # Broadcast real-time log event to frontend terminal
                    await ws_manager.broadcast({
                        "event": "LOG_OUTPUT",
                        "challenge_id": challenge_id,
                        "command": f"{binary_name} output",
                        "output": clean_line,
                        "exit_code": 0 if not is_stderr else 1,
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S")
                    })

                    # Check for flag match in stream
                    if not captured_flag:
                        match = FLAG_REGEX.search(clean_line)
                        if match:
                            captured_flag = match.group(1)
                            logger.info(f"[FLAG CAPTURED BY {binary_name.upper()}] Flag: {captured_flag}")
                            await ws_manager.broadcast({
                                "event": "FLAG_CAPTURED",
                                "challenge_id": challenge_id,
                                "flag": captured_flag
                            })

            # Run stream readers concurrently
            await asyncio.gather(
                stream_reader(proc.stdout, is_stderr=False),
                stream_reader(proc.stderr, is_stderr=True)
            )

            await proc.wait()
            logger.info(f"{binary_name} process exited with return code {proc.returncode}")

            # Finalize Challenge State
            full_output = "".join(accumulated_output)
            # Check for AgentRouter 402 Quota Exhaustion
            from backend.providers.quota_manager import quota_manager
            is_quota_error = quota_manager.detect_quota_error(full_output) or "402" in full_output or "budget pool" in full_output.lower()

            if proc.returncode != 0 and is_quota_error and not captured_flag:
                failed_model = model or ("claude-opus-5" if agent_type == "claude_code" else "gpt-5.6")
                quota_manager.record_quota_exhaustion(failed_model, full_output[:200])
                next_batch = quota_manager.get_next_batch_time_str()

                logger.warning(
                    f"[CLIAgentRunner] AgentRouter quota EXHAUSTED for '{agent_type}'. "
                    f"Next replenishment: {next_batch}. Auto-fallback to Autonomous Orchestrator..."
                )

                await ws_manager.broadcast({
                    "type": "PROVIDER_FALLBACK_TRIGGERED",
                    "data": {
                        "failed_provider": f"{binary_name.upper()} (Claude/GPT Batch Quota Exhausted)",
                        "reason": f"HTTP 402 Budget pool quota exhausted. Next batch at {next_batch}",
                        "next_provider": "Autonomous Orchestrator (RapidAPI / DeepSeek)",
                        "timestamp": datetime.utcnow().isoformat()
                    }
                })

                await ws_manager.broadcast({
                    "event": "LOG_OUTPUT",
                    "challenge_id": challenge_id,
                    "command": f"{binary_name} fallback",
                    "output": f"[QUOTA NOTICE] Claude batch quota exhausted (402). Auto-switching to Autonomous Orchestrator ReAct engine with RapidAPI/DeepSeek...",
                    "exit_code": 0,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S")
                })

                # Seamlessly hand off to Autonomous Orchestrator without failing
                from backend.agents.orchestrator_loop import orchestrator_loop
                await orchestrator_loop.run_autonomous_loop(run_id, challenge_id, target)
                return

            if captured_flag:
                challenge.flag = captured_flag
                challenge.flag_status = "CAPTURED"
                challenge.status = "COMPLETED"
                challenge.progress = 100

                finding = FindingModel(
                    challenge_id=challenge.id,
                    agent=agent_type.lower(),
                    title=f"Flag Extracted via {binary_name.upper()}",
                    description=f"Flag extracted by autonomous {binary_name} execution: {captured_flag}",
                    vulnerability_class="Flag Extraction",
                    severity="CRITICAL",
                    endpoint=target,
                    verified=True,
                    confidence=1.0
                )
                db.add(finding)
            else:
                challenge.status = "COMPLETED" if proc.returncode == 0 else "FAILED"
                challenge.progress = 80 if proc.returncode == 0 else 30

            # Store full session evidence
            evidence = EvidenceModel(
                challenge_id=challenge.id,
                agent=agent_type.lower(),
                evidence_type="command_output",
                source=binary_name,
                content=f"### Autonomous {binary_name} Session Output\n\n{full_output[:10000]}",
                confidence=1.0
            )
            db.add(evidence)
            db.commit()

            # Generate final markdown report
            output_dir = getattr(challenge, 'working_directory', '') or 'reports'
            report_generator.generate_readme(db=db, challenge_id=challenge.id, output_dir=output_dir)

            await ws_manager.broadcast({
                "event": "RUN_COMPLETED",
                "run_id": run_id,
                "challenge_id": challenge_id,
                "status": challenge.status,
                "flag": captured_flag
            })

        except Exception as e:
            logger.error(f"Error in CLI agent loop: {e}", exc_info=True)
            await ws_manager.broadcast({
                "event": "LOG_OUTPUT",
                "challenge_id": challenge_id,
                "command": f"{agent_type} session error",
                "output": f"[CRITICAL ERROR] {str(e)}",
                "exit_code": 1,
                "timestamp": datetime.utcnow().strftime("%H:%M:%S")
            })
        finally:
            db.close()

cli_agent_runner = CLIAgentRunner()
