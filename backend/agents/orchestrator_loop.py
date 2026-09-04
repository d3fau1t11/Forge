import asyncio
import json
import re
import os
import sys
import logging
import uuid
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

# Common import-name to pip-package-name mappings
IMPORT_TO_PACKAGE_MAP = {
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "Crypto": "pycryptodome",
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "flask_unsign": "flask-unsign",
    "pwn": "pwntools",
}

class AutonomousOrchestrator:
    """Central autonomous investigation ReAct 1-Command Cycle loop."""

    def __init__(self):
        # Tracks pending install requests: request_id -> asyncio.Event
        self._install_events: dict[str, asyncio.Event] = {}
        self._install_results: dict[str, bool] = {}
        # Tracks pending root/privilege requests: request_id -> asyncio.Event
        self._root_events: dict[str, asyncio.Event] = {}
        self._root_results: dict[str, dict] = {}

    def resolve_install_request(self, request_id: str, success: bool):
        """Called by the API route after user approves and pip install completes."""
        self._install_results[request_id] = success
        event = self._install_events.get(request_id)
        if event:
            event.set()

    def resolve_root_request(self, request_id: str, success: bool, tool_res: Optional[Any] = None, message: str = ""):
        """Called by the API route when operator approves or denies root execution."""
        self._root_results[request_id] = {
            "success": success,
            "tool_res": tool_res,
            "message": message
        }
        event = self._root_events.get(request_id)
        if event:
            event.set()

    async def run_autonomous_loop(self, run_id: str, challenge_id: str, target: str):
        """Asynchronous background 1-command ping-pong loop processing CTF challenge target."""
        logger.info(f"Starting 1-command ReAct autonomous loop for run {run_id}, challenge {challenge_id}, target {target}")

        env_info = environment_detector.detect_environment()
        os_distro = env_info.get("distro") or env_info.get("os", "Linux (Parrot OS)")
        installed_tools = [name for name, meta in env_info.get("installed_tools", {}).items() if meta.get("installed")]
        tools_str = ", ".join(installed_tools) if installed_tools else "nmap, ffuf, curl, python3, gdb, strings, objdump"
        
        # Audit importable Python libraries for solver scripts
        installed_py_libs = [lib for lib, active in env_info.get("installed_python_libs", {}).items() if active]
        py_libs_str = ", ".join(installed_py_libs) if installed_py_libs else "requests, beautifulsoup4, flask-unsign, cryptography"
        
        for req_lib in ["requests", "bs4", "flask_unsign", "cryptography"]:
            if not env_info.get("installed_python_libs", {}).get(req_lib, True):
                logger.warning(f"[SOLVER LIB WARNING] Advertised library '{req_lib}' is NOT importable in host Python runtime ({sys.executable}).")

        history_summary = []
        normalized_history = []
        max_turns = 20
        
        # Structured State Memory Object
        state_memory = {
            "discovered_endpoints": set(),
            "observed_cookies": set(),
            "headers_found": set(),
            "repetition_warnings": 0
        }

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

                # Calculate realistic progress based on investigation state milestones
                if challenge.flag_status == "CAPTURED":
                    progress = 100
                elif state_memory["discovered_endpoints"] or state_memory["observed_cookies"]:
                    progress = min(85, 30 + (turn * 4))
                else:
                    progress = min(40, 10 + (turn * 3))

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

                # Format Structured State Memory for Prompt Injection
                memory_json = json.dumps({
                    "discovered_endpoints": list(state_memory["discovered_endpoints"])[:10],
                    "observed_cookies": list(state_memory["observed_cookies"])[:5],
                    "headers_found": list(state_memory["headers_found"])[:5],
                    "loop_warning": state_memory["repetition_warnings"] > 0
                }, indent=2)

                # Construct transparent ReAct System Prompt with Python Execution Mode & OS Context
                system_instruction = (
                    f"You are FORGE Autonomous CTF Pentest Agent running on {os_distro}.\n"
                    f"SYSTEM DIRECTIVE: Rely exclusively on live target scope and real execution outputs. Zero fake flags allowed.\n"
                    f"Target Scope: {target}\n"
                    f"Challenge Name: {challenge.name} | Category: {challenge.category} | Difficulty: {challenge.difficulty} | Platform: {challenge.platform_name}\n"
                    f"Working Directory: {challenge.working_directory}\n"
                    f"Installed Pentest Tools & Python Libraries: {tools_str}, python3, {py_libs_str}\n\n"
                    f"ACTION SELECTION MODES:\n"
                    f"Mode A (CLI Command): Output a SINGLE executable bash/shell command line.\n"
                    f"Mode B (Python Solver Script): Output a complete Python script inside ```python ... ``` blocks. FORGE will save it to `solve.py` and run `{sys.executable} solve.py` automatically.\n\n"
                    f"STRICT RULES:\n"
                    f"1. Output ONLY the bash command line OR the ```python ... ``` solver script block. No surrounding conversation or Markdown headers.\n"
                    f"2. If you discover the real flag (e.g. picoCTF{{...}}, FLAG{{...}}, HTB{{...}}), reply EXACTLY: FLAG: <captured_flag>\n"
                    f"3. If mission is complete without flag, reply: DONE\n"
                    f"4. Multi-target inputs are joined using '+' sign. Process targets accordingly."
                )

                history_context = "\n".join(history_summary[-6:]) if history_summary else "No commands executed yet."
                
                # Check for Strategic Repetition Override
                repetition_warning_prompt = ""
                if state_memory["repetition_warnings"] > 0:
                    repetition_warning_prompt = (
                        "\n⚠️ STRATEGIC OVERRIDE ALERT: Your recent actions are repeating similar commands on the target without progress.\n"
                        "YOU MUST PIVOT METHODOLOGY IMMEDIATELY!\n"
                        "- Do NOT repeat curl requests with similar cookies or endpoints.\n"
                        "- Use Mode B to write a Python script using requests.Session(), flask-unsign, pwntools, or bs4.\n"
                        "- Inspect HTML comments, script tags, or unusual HTTP response headers.\n"
                    )

                prompt = (
                    f"--- STRUCTURED TARGET MEMORY STATE ---\n{memory_json}\n\n"
                    f"--- RECENT STEP HISTORY ---\n{history_context}\n"
                    f"{repetition_warning_prompt}\n"
                    f"--- CURRENT TURN #{turn} ---\n"
                    f"Analyze the target memory and step history. Issue the NEXT single command OR Python solver script to execute on '{target}'."
                )

                # Query AI Router
                capability = "code_analysis" if state_memory["repetition_warnings"] > 0 else ("general_reasoning" if turn == 1 else "web_testing")
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
                    "result": raw_ai_output[:300],
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

                # Extract Python script block OR single CLI command line
                cmd_line = ""
                is_python_script = False
                script_code = ""
                py_match = re.search(r"```python\s*(.*?)\s*```", raw_ai_output, re.DOTALL)
                if not py_match:
                    py_match = re.search(r"```(?:sh|bash)?\s*(import\s+.*|from\s+.*)\s*```", raw_ai_output, re.DOTALL)

                if py_match or raw_ai_output.startswith("import ") or raw_ai_output.startswith("from "):
                    is_python_script = True
                    script_code = py_match.group(1) if py_match else raw_ai_output
                    solve_file_path = os.path.join(challenge.working_directory, "solve.py")
                    try:
                        with open(solve_file_path, "w", encoding="utf-8") as sf:
                            sf.write(script_code)
                        cmd_line = f'"{sys.executable}" solve.py'
                        logger.info(f"Synthesized Python solver script `solve.py` for turn #{turn} (interpreter: {sys.executable})")
                    except Exception as sf_err:
                        logger.error(f"Failed to save solve.py: {sf_err}")
                        cmd_line = f'"{sys.executable}" -c ' + json.dumps(script_code)
                else:
                    cmd_line = raw_ai_output.split("\n")[0].strip()
                    cmd_line = re.sub(r"^```(?:bash|sh)?", "", cmd_line).strip()
                    cmd_line = re.sub(r"```$", "", cmd_line).strip()

                # Sanitize nmap target if AI passes http:// or port
                if cmd_line.startswith("nmap"):
                    clean_host = target.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
                    cmd_line = re.sub(r"https?://[^\s]+", clean_host, cmd_line)

                # Normalized Anti-Repetition Loop Guard
                if is_python_script and script_code:
                    import hashlib
                    norm_cmd = "python_script:" + hashlib.md5(script_code.strip().encode("utf-8")).hexdigest()
                else:
                    norm_cmd = self._normalize_command(cmd_line)

                if normalized_history and normalized_history.count(norm_cmd) >= 1:
                    state_memory["repetition_warnings"] += 1
                    logger.warning(f"Normalized loop detected for `{norm_cmd[:30]}` (Count={normalized_history.count(norm_cmd)}). Forcing pivot.")
                    if "login" in cmd_line:
                        cmd_line = f"curl -i -s -c cookies.txt {target.rstrip('/')}/register"
                    elif "nmap" in cmd_line:
                        clean_host = target.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
                        cmd_line = f"nmap -sV -F {clean_host}"
                    else:
                        cmd_line = f"curl -i -L {target}"
                    norm_cmd = self._normalize_command(cmd_line)
                
                normalized_history.append(norm_cmd)

                if not cmd_line:
                    cmd_line = f"curl -s -L {target}" if target.startswith("http") else f"nmap -F {target}"

                # Execute Command inside working directory
                tool_res = await tool_manager.execute_raw_command(
                    command=cmd_line,
                    cwd=challenge.working_directory,
                    timeout_seconds=120
                )

                stdout_text = tool_res.stdout[:3000] if tool_res.stdout else ""
                stderr_text = tool_res.stderr[:1000] if tool_res.stderr else ""
                log_output = stdout_text or stderr_text or f"[Return Code {tool_res.exit_code}] Execution finished with no output."

                # ── ImportError Auto-Install Detection ──
                if is_python_script and tool_res.exit_code != 0:
                    combined_err = f"{stdout_text}\n{stderr_text}"
                    import_err_match = re.search(
                        r"(?:ModuleNotFoundError|ImportError):\s*No module named ['\"]([^'\"]+)['\"]",
                        combined_err
                    )
                    if import_err_match:
                        missing_import = import_err_match.group(1).split('.')[0]
                        pip_package = IMPORT_TO_PACKAGE_MAP.get(missing_import, missing_import)
                        request_id = str(uuid.uuid4())
                        error_snippet = combined_err.strip()[-300:]

                        logger.info(f"Detected missing module '{missing_import}' (pip: {pip_package}). Requesting user install approval.")

                        # Send install request popup to frontend
                        await ws_manager.broadcast({
                            "event": "PACKAGE_INSTALL_REQUEST",
                            "request_id": request_id,
                            "challenge_id": challenge_id,
                            "challenge_name": challenge.name,
                            "package_name": pip_package,
                            "import_name": missing_import,
                            "error_snippet": error_snippet,
                            "timestamp": datetime.utcnow().strftime("%H:%M:%S")
                        })

                        # Wait for user decision (up to 120s)
                        install_event = asyncio.Event()
                        self._install_events[request_id] = install_event
                        try:
                            await asyncio.wait_for(install_event.wait(), timeout=120)
                            if self._install_results.get(request_id, False):
                                logger.info(f"Package '{pip_package}' installed. Retrying solver script.")
                                # Re-run the same solver script
                                tool_res = await tool_manager.execute_raw_command(
                                    command=cmd_line,
                                    cwd=challenge.working_directory,
                                    timeout_seconds=120
                                )
                                stdout_text = tool_res.stdout[:3000] if tool_res.stdout else ""
                                stderr_text = tool_res.stderr[:1000] if tool_res.stderr else ""
                                log_output = stdout_text or stderr_text or f"[Return Code {tool_res.exit_code}] Re-execution finished."
                            else:
                                logger.warning(f"Package install for '{pip_package}' failed or was rejected.")
                        except asyncio.TimeoutError:
                            logger.warning(f"Install approval for '{pip_package}' timed out after 120s. Continuing.")
                        finally:
                            self._install_events.pop(request_id, None)
                            self._install_results.pop(request_id, None)

                # ── Root / Privilege Elevation Detection ──
                requires_root_pattern = r"(?:Permission denied|Operation not permitted|You must be root|need root privileges|requires root|must be run as root|sudo:\s*a password is required|superuser privileges|EACCES)"
                combined_output = f"{stdout_text}\n{stderr_text}"
                needs_root_elevation = (
                    cmd_line.strip().startswith("sudo ") or
                    (tool_res.exit_code != 0 and re.search(requires_root_pattern, combined_output, re.I))
                )

                if needs_root_elevation and not is_python_script:
                    root_request_id = str(uuid.uuid4())
                    root_reason = "This command requires elevated root / superuser privileges to execute."
                    error_snippet = (stderr_text or stdout_text).strip()[-300:]
                    logger.info(f"Command '{cmd_line}' requires root/sudo privileges. Requesting user approval [ID: {root_request_id}].")

                    await ws_manager.broadcast({
                        "event": "ROOT_PERMISSION_REQUEST",
                        "request_id": root_request_id,
                        "challenge_id": challenge_id,
                        "challenge_name": challenge.name,
                        "command": cmd_line,
                        "reason": root_reason,
                        "error_snippet": error_snippet,
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S")
                    })

                    root_event = asyncio.Event()
                    self._root_events[root_request_id] = root_event
                    try:
                        await asyncio.wait_for(root_event.wait(), timeout=120)
                        res = self._root_results.get(root_request_id, {})
                        if res.get("success"):
                            logger.info(f"Root permission approved for '{cmd_line}'.")
                            if res.get("tool_res"):
                                tool_res = res["tool_res"]
                            else:
                                sudo_cmd = cmd_line if cmd_line.startswith("sudo ") else f"sudo {cmd_line}"
                                tool_res = await tool_manager.execute_raw_command(
                                    command=sudo_cmd,
                                    cwd=challenge.working_directory,
                                    timeout_seconds=120
                                )
                            stdout_text = tool_res.stdout[:3000] if tool_res.stdout else ""
                            stderr_text = tool_res.stderr[:1000] if tool_res.stderr else ""
                            log_output = stdout_text or stderr_text or f"[Return Code {tool_res.exit_code}] Elevated execution completed."
                        else:
                            logger.warning(f"Root permission was rejected/denied by operator for '{cmd_line}'.")
                            stdout_text = ""
                            stderr_text = f"[ACCESS DENIED] Root/sudo permission was rejected by the operator for: {cmd_line}. Please pivot to an unprivileged user-space alternative."
                            log_output = stderr_text
                    except asyncio.TimeoutError:
                        logger.warning(f"Root approval request timed out for '{cmd_line}'.")
                        stderr_text = f"[TIMEOUT] Root permission request timed out after 120s. Please pivot to an unprivileged user-space alternative."
                        log_output = stderr_text
                    finally:
                        self._root_events.pop(root_request_id, None)
                        self._root_results.pop(root_request_id, None)

                # Update Structured State Memory from Command Output
                self._update_state_memory(state_memory, stdout_text)

                # Append Full AI Conversation & Telemetry to Dedicated Challenge Log File
                logs_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
                ch_log_path = os.path.join(logs_dir, f"challenge_{challenge_id}.log")
                try:
                    with open(ch_log_path, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.utcnow().strftime('%H:%M:%S')}] TURN #{turn} (Model Provider: {model_used})\n")
                        f.write(f"  --- SYSTEM INSTRUCTION SENT TO AI ---\n{system_instruction}\n\n")
                        f.write(f"  --- PROMPT / RECENT HISTORY SENT TO AI ---\n{prompt}\n\n")
                        f.write(f"  --- RAW AI RESPONSE RECEIVED ---\n{raw_ai_output}\n\n")
                        f.write(f"  --- EXECUTED COMMAND ---\n{cmd_line}\n\n")
                        f.write(f"  --- STDOUT / STDERR OUTPUT (Exit Code {tool_res.exit_code}) ---\n{log_output}\n")
                        f.write(f"================================================================================\n\n")
                except Exception as log_err:
                    logger.warning(f"Failed to append to challenge log file: {log_err}")

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
                import traceback
                logger.error(f"Error during turn #{turn} of run {run_id}: {str(e)}\n{traceback.format_exc()}")
                # Append error to challenge log for visibility
                try:
                    logs_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs"))
                    ch_log_path = os.path.join(logs_dir, f"challenge_{challenge_id}.log")
                    with open(ch_log_path, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.utcnow().strftime('%H:%M:%S')}] TURN #{turn} ERROR: {str(e)}\n{traceback.format_exc()}\n")
                except Exception:
                    pass
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
        challenge.flag_status = "CAPTURED"
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
        db.commit()
        return {
            "status": run.status,
            "agent": current_agent_name,
            "capability": capability,
            "tool_status": "SUCCESS"
        }

    def _normalize_command(self, command: str) -> str:
        """Normalize command string by removing dynamic cookies, timestamps, and redundant whitespace."""
        cmd = command.strip()
        cmd = re.sub(r"session=[a-zA-Z0-9_\-\.%]+", "session=COOKIESUB", cmd)
        cmd = re.sub(r"Cookie:\s*['\"][^'\"]+['\"]", "Cookie: COOKIESUB", cmd, flags=re.IGNORECASE)
        cmd = re.sub(r"\?t=\d+", "?t=TIMESTAMP", cmd)
        cmd = re.sub(r"\s+", " ", cmd)
        return cmd.lower()

    def _update_state_memory(self, state_memory: Dict[str, Any], output_text: str):
        """Parse command stdout to update structured target state memory."""
        if not output_text:
            return
        
        # Extract Discovered URLs / Endpoints
        urls = re.findall(r"https?://[^\s\"'>]+", output_text)
        for u in urls:
            if len(u) < 120:
                state_memory["discovered_endpoints"].add(u)
        
        # Extract Cookies
        cookies = re.findall(r"Set-Cookie:\s*([^;\r\n]+)", output_text, re.IGNORECASE)
        for c in cookies:
            state_memory["observed_cookies"].add(c.strip())

        # Extract Server / Tech Headers
        headers = re.findall(r"(?:Server|X-Powered-By|X-Framework):\s*([^\r\n]+)", output_text, re.IGNORECASE)
        for h in headers:
            state_memory["headers_found"].add(h.strip())

orchestrator_loop = AutonomousOrchestrator()

