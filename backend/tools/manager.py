import os
import re
import difflib
from urllib.parse import urlparse
import asyncio
import time
import shutil
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel
from backend.tools.registry import tool_registry, ToolMetadata
from backend.environment.detector import environment_detector
from backend.execution.service import execution_service

logger = logging.getLogger("forge.tools")


class ToolExecutionResult(BaseModel):
    tool_name: str
    capability: str
    command: str
    status: str # SUCCESS, FAILED, TIMEOUT, MISSING_TOOL
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_ms: float = 0.0
    execution_failure: bool = False       # True if DNS failure, connection refused, or timeout
    failure_category: Optional[str] = None # DNS_ERROR, TIMEOUT, CONNECTION_REFUSED, COMMAND_NOT_FOUND


def sanitize_and_correct_command_target(command: str, canonical_target_url: Optional[str] = None) -> str:
    """
    Detects and auto-corrects typo'd hostnames in generated CLI commands
    (e.g., 'amiable-citidel' -> 'amiable-citadel') before subprocess execution.
    """
    if not command or not canonical_target_url:
        return command

    try:
        parsed_canonical = urlparse(canonical_target_url if "://" in canonical_target_url else f"http://{canonical_target_url}")
        canonical_host = (parsed_canonical.hostname or canonical_target_url.split(":")[0]).replace("http://", "").replace("https://", "").strip("/")
        if not canonical_host or len(canonical_host) < 4:
            return command

        corrected_cmd = command
        # Extract potential host tokens in command
        tokens = re.findall(r"https?://([a-zA-Z0-9_\-\.]+)|([a-zA-Z0-9_\-\.]+\.[a-zA-Z]{2,})", command)
        flat_tokens = [t[0] or t[1] for t in tokens if (t[0] or t[1])]

        for token in flat_tokens:
            token_clean = token.split(":")[0].strip()
            if token_clean.lower() != canonical_host.lower() and len(token_clean) >= 4:
                ratio = difflib.SequenceMatcher(None, token_clean.lower(), canonical_host.lower()).ratio()
                if 0.75 <= ratio < 1.0:
                    logger.warning(f"[AUTO-CORRECT HOST] Correcting typo'd host '{token_clean}' -> '{canonical_host}' in command: {command[:80]}")
                    corrected_cmd = corrected_cmd.replace(token_clean, canonical_host)
        return corrected_cmd
    except Exception as e:
        logger.debug(f"Target correction skip: {e}")
        return command


def classify_tool_execution(tool_name: str, exit_code: Optional[int], stdout: str, stderr: str) -> Dict[str, Any]:
    """
    Classifies tool exit codes and output into target responses vs execution-level failures.

    Two families of execution failure are distinguished:
    - NETWORK-level (the command left the host but never got a target response):
      curl exit 6 (DNS), exit 7 (connection refused), exit 28 (timeout).
    - LOCAL-level (the command never ran / never reached the target at all): a bad
      invocation such as an unquoted path with a space (`[Errno 2] No such file or
      directory`), a Python SyntaxError, a permission error, or a missing dependency.
      These must NOT be retried identically — the caller aborts after a couple of hits
      (see LOCAL_EXEC_CATEGORIES) instead of burning the iteration/time budget.
    """
    # A command that exited 0 CLEANLY SUCCEEDED — a bad invocation (file-not-found,
    # syntax error, permission denied, missing dependency) or a transport failure
    # (DNS/refused/timeout) always exits non-zero. So any failure-looking phrase in a
    # zero-exit command's OUTPUT is data, not a diagnostic, and must not be flagged.
    # Observed in production: a `curl … | strings | grep` pipeline that dumped an 11MB
    # Node heap-dump (whose body literally contains "no such file or directory") exited
    # 0 and captured the flag, yet was tagged FILE_NOT_FOUND — which feeds the swarm's
    # "abort after 2 local failures" guard and the coordinated recovery logic. Guarding
    # here also avoids lower-casing a multi-megabyte stdout on every successful command.
    if exit_code == 0:
        return {"execution_failure": False, "failure_category": None}

    combined = ((stdout or "") + " " + (stderr or "")).lower()

    # ── LOCAL-level failures first: unambiguous text signatures win over exit-code
    #    heuristics (a bad invocation can share exit_code -1 with a subprocess error). ──
    if ("[errno 2]" in combined or "no such file or directory" in combined
            or "can't open file" in combined or "cannot open file" in combined):
        return {"execution_failure": True, "failure_category": "FILE_NOT_FOUND"}
    if ("syntaxerror" in combined or "invalid syntax" in combined
            or "unterminated string" in combined or "unexpected eof while parsing" in combined
            or "unexpected token" in combined):
        return {"execution_failure": True, "failure_category": "SYNTAX_ERROR"}
    if ("[errno 13]" in combined or "permission denied" in combined
            or "not permitted" in combined or "sudo: a password is required" in combined
            or "sudo: no tty present" in combined or "a password is required" in combined
            or "no tty present" in combined):
        return {"execution_failure": True, "failure_category": "PERMISSION_DENIED"}
    if ("modulenotfounderror" in combined or "no module named" in combined
            or "importerror" in combined):
        return {"execution_failure": True, "failure_category": "MISSING_DEP"}
    if ("no scheme supplied" in combined or "invalid url" in combined
            or "missing scheme" in combined or "unknown url type" in combined):
        return {"execution_failure": True, "failure_category": "INVALID_URL"}

    # ── NETWORK-level failures (left the host, no usable target response) ──────────────
    if exit_code in [6] or "could not resolve host" in combined or "name or service not known" in combined:
        return {"execution_failure": True, "failure_category": "DNS_ERROR"}
    if exit_code in [7] or "connection refused" in combined or "failed to connect" in combined:
        return {"execution_failure": True, "failure_category": "CONNECTION_REFUSED"}
    if exit_code in [28, -1] or "operation timed out" in combined or "timed out after" in combined:
        return {"execution_failure": True, "failure_category": "TIMEOUT"}
    if "command not found" in combined or "is not recognized as an internal or external command" in combined:
        return {"execution_failure": True, "failure_category": "COMMAND_NOT_FOUND"}

    return {"execution_failure": False, "failure_category": None}


# Failure categories that mean the command never reached the target (a local invocation
# problem, not a target response). The swarm aborts an agent after a couple of these in a
# row rather than re-running the same broken command against a shared, rate-limited chain.
# COMMAND_NOT_FOUND is included: a missing binary is a local problem, not a target signal.
LOCAL_EXEC_CATEGORIES = frozenset({
    "FILE_NOT_FOUND", "SYNTAX_ERROR", "PERMISSION_DENIED", "MISSING_DEP", "COMMAND_NOT_FOUND",
    "INVALID_URL", "INTERPRETER_ASSUMPTION",
})


class ToolManager:
    """Resolves agent capability requests to concrete installed tool executions."""

    async def execute_capability(
        self,
        capability: str,
        target: str,
        extra_args: Optional[str] = None,
        cwd: Optional[str] = None
    ) -> ToolExecutionResult:
        start_time = time.time()
        parsed_target = target.replace("+", ",").split(",")[0].strip()

        # Direct model-powered capabilities like vision_read
        if capability == "vision_read":
            target_path = parsed_target
            if cwd and not os.path.isabs(target_path) and not os.path.exists(target_path):
                alt_path = os.path.join(cwd, target_path)
                if os.path.exists(alt_path):
                    target_path = alt_path

            if not target_path or not os.path.isfile(target_path):
                elapsed_ms = (time.time() - start_time) * 1000
                return ToolExecutionResult(
                    tool_name="vision_read",
                    capability=capability,
                    command=f"vision_read {target}",
                    status="FAILED",
                    stderr=f"Target image file not found: {target_path}",
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="FILE_NOT_FOUND"
                )

            from backend.providers.router import model_router
            vision_prompt = (
                "Extract any readable text visible in this image. Report it EXACTLY as "
                "it appears, character for character, with no paraphrasing. If any text "
                "resembles a CTF flag format (e.g. flag{...}, picoCTF{...}, or similar), "
                "quote it verbatim first before anything else."
            )
            resp = await model_router.route_request(
                prompt=vision_prompt,
                capability="vision_read",
                image_path=target_path
            )
            elapsed_ms = (time.time() - start_time) * 1000
            if resp.is_refusal:
                refusal_msg = resp.refusal_reason or resp.content or "Model refusal for vision_read"
                return ToolExecutionResult(
                    tool_name="vision_read",
                    capability=capability,
                    command=f"vision_read {target_path}",
                    status="FAILED",
                    stderr=refusal_msg,
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="MODEL_REFUSAL"
                )
            else:
                return ToolExecutionResult(
                    tool_name="vision_read",
                    capability=capability,
                    command=f"vision_read {target_path}",
                    status="SUCCESS",
                    stdout=resp.content,
                    stderr="",
                    exit_code=0,
                    duration_ms=elapsed_ms,
                    execution_failure=False,
                    failure_category=None
                )

        # Persistent interactive process capabilities
        if capability in ("interactive_open", "interactive_start"):
            cmd_to_run = (target or "").strip()
            if extra_args:
                cmd_to_run = f"{cmd_to_run} {extra_args}".strip() if cmd_to_run else extra_args.strip()
            if not cmd_to_run:
                elapsed_ms = (time.time() - start_time) * 1000
                return ToolExecutionResult(
                    tool_name="interactive_open",
                    capability="interactive_open",
                    command="interactive_open",
                    status="FAILED",
                    stderr="No command specified to start interactive session.",
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="SYNTAX_ERROR",
                )

            exec_cwd = cwd if (cwd and os.path.exists(cwd)) else None
            try:
                sess = await execution_service.open_interactive(
                    cmd_to_run,
                    cwd=exec_cwd,
                    target=target,
                )
                read_res = await sess.read(timeout=3.0, idle_timeout=1.0)
                elapsed_ms = (time.time() - start_time) * 1000
                banner = read_res.data if read_res.data else "(Process started, waiting for input)"
                stdout_text = f"[SESSION: {sess.session_key}]\n{banner}"
                return ToolExecutionResult(
                    tool_name="interactive_open",
                    capability="interactive_open",
                    command=f"interactive_open {cmd_to_run}",
                    status="SUCCESS",
                    stdout=stdout_text,
                    stderr="",
                    exit_code=0 if sess.is_alive() else (sess.returncode or 0),
                    duration_ms=elapsed_ms,
                    execution_failure=False,
                    failure_category=None,
                )
            except Exception as e:
                elapsed_ms = (time.time() - start_time) * 1000
                logger.warning(f"Failed to open interactive session for '{cmd_to_run}': {e}")
                return ToolExecutionResult(
                    tool_name="interactive_open",
                    capability="interactive_open",
                    command=f"interactive_open {cmd_to_run}",
                    status="FAILED",
                    stderr=f"Failed to open interactive session: {e}",
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="EXECUTION_ERROR",
                )

        if capability == "interactive_send":
            session_key = (target or "").strip().strip('"').strip("'")
            data = extra_args if extra_args is not None else ""
            from backend.execution.interactive import interactive_manager
            sess = interactive_manager.get(session_key)
            elapsed_ms = (time.time() - start_time) * 1000
            if not sess or not sess.is_alive():
                return ToolExecutionResult(
                    tool_name="interactive_send",
                    capability="interactive_send",
                    command=f"interactive_send {session_key}",
                    status="FAILED",
                    stderr=f"Interactive session '{session_key}' not found or already closed.",
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="SESSION_NOT_FOUND",
                )
            sent = await sess.send(data)
            elapsed_ms = (time.time() - start_time) * 1000
            if not sent:
                return ToolExecutionResult(
                    tool_name="interactive_send",
                    capability="interactive_send",
                    command=f"interactive_send {session_key}",
                    status="FAILED",
                    stderr="Failed to send data to interactive session.",
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="SEND_FAILED",
                )
            return ToolExecutionResult(
                tool_name="interactive_send",
                capability="interactive_send",
                command=f"interactive_send {session_key}",
                status="SUCCESS",
                stdout=f"[SESSION: {session_key}] Sent {len(data)} characters.",
                stderr="",
                exit_code=0,
                duration_ms=elapsed_ms,
                execution_failure=False,
                failure_category=None,
            )

        if capability == "interactive_read":
            session_key = (target or "").strip().strip('"').strip("'")
            until_marker = extra_args.strip() if extra_args else None
            from backend.execution.interactive import interactive_manager
            sess = interactive_manager.get(session_key)
            elapsed_ms = (time.time() - start_time) * 1000
            if not sess:
                return ToolExecutionResult(
                    tool_name="interactive_read",
                    capability="interactive_read",
                    command=f"interactive_read {session_key}",
                    status="FAILED",
                    stderr=f"Interactive session '{session_key}' not found or already closed.",
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="SESSION_NOT_FOUND",
                )
            read_res = await sess.read(until=until_marker)
            elapsed_ms = (time.time() - start_time) * 1000
            status = "SUCCESS" if (read_res.ok or read_res.data) else ("TIMEOUT" if read_res.timed_out else "FAILED")
            return ToolExecutionResult(
                tool_name="interactive_read",
                capability="interactive_read",
                command=f"interactive_read {session_key}",
                status=status,
                stdout=read_res.data,
                stderr="" if (read_res.ok or read_res.data) else ("Read timed out with no data" if read_res.timed_out else "Read failed/EOF"),
                exit_code=0 if (sess.is_alive() or read_res.data) else (sess.returncode or 0),
                duration_ms=elapsed_ms,
                execution_failure=False if (read_res.ok or read_res.data) else read_res.timed_out,
                failure_category="TIMEOUT" if (read_res.timed_out and not read_res.data) else None,
            )

        if capability == "interactive_send_and_read":
            session_key = (target or "").strip().strip('"').strip("'")
            data = extra_args if extra_args is not None else ""
            from backend.execution.interactive import interactive_manager
            sess = interactive_manager.get(session_key)
            elapsed_ms = (time.time() - start_time) * 1000
            if not sess or not sess.is_alive():
                return ToolExecutionResult(
                    tool_name="interactive_send_and_read",
                    capability="interactive_send_and_read",
                    command=f"interactive_send_and_read {session_key}",
                    status="FAILED",
                    stderr=f"Interactive session '{session_key}' not found or already closed.",
                    exit_code=1,
                    duration_ms=elapsed_ms,
                    execution_failure=True,
                    failure_category="SESSION_NOT_FOUND",
                )
            read_res = await sess.send_and_read(data)
            elapsed_ms = (time.time() - start_time) * 1000
            status = "SUCCESS" if (read_res.ok or read_res.data) else ("TIMEOUT" if read_res.timed_out else "FAILED")
            return ToolExecutionResult(
                tool_name="interactive_send_and_read",
                capability="interactive_send_and_read",
                command=f"interactive_send_and_read {session_key}",
                status=status,
                stdout=read_res.data,
                stderr="" if (read_res.ok or read_res.data) else ("Read timed out with no data" if read_res.timed_out else "Read failed/EOF"),
                exit_code=0 if (sess.is_alive() or read_res.data) else (sess.returncode or 0),
                duration_ms=elapsed_ms,
                execution_failure=False if (read_res.ok or read_res.data) else read_res.timed_out,
                failure_category="TIMEOUT" if (read_res.timed_out and not read_res.data) else None,
            )

        if capability == "interactive_close":
            session_key = (target or "").strip().strip('"').strip("'")
            from backend.execution.interactive import interactive_manager
            closed = await interactive_manager.close(session_key)
            elapsed_ms = (time.time() - start_time) * 1000
            return ToolExecutionResult(
                tool_name="interactive_close",
                capability="interactive_close",
                command=f"interactive_close {session_key}",
                status="SUCCESS" if closed else "FAILED",
                stdout=f"[SESSION: {session_key}] Closed." if closed else "",
                stderr="" if closed else f"Interactive session '{session_key}' not found or already closed.",
                exit_code=0 if closed else 1,
                duration_ms=elapsed_ms,
                execution_failure=not closed,
                failure_category=None if closed else "SESSION_NOT_FOUND",
            )

        # 1. Resolve candidate tools for capability
        candidate_tools = tool_registry.get_tools_for_capability(capability)
        if not candidate_tools:
            return ToolExecutionResult(
                tool_name="none",
                capability=capability,
                command="",
                status="MISSING_TOOL",
                stderr=f"No approved tool registered for capability '{capability}'."
            )

        # 2. Check host environment for installed tool
        env_tools = environment_detector.detect_environment()["installed_tools"]
        selected_tool: Optional[ToolMetadata] = None

        for tool in candidate_tools:
            if env_tools.get(tool.binary, {}).get("installed") or shutil.which(tool.binary):
                selected_tool = tool
                break

        if not selected_tool:
            first_candidate = candidate_tools[0]
            return ToolExecutionResult(
                tool_name=first_candidate.tool_name,
                capability=capability,
                command="",
                status="MISSING_TOOL",
                stderr=f"Tool '{first_candidate.tool_name}' required for capability '{capability}' is not installed. Trusted install recipe: `{first_candidate.installation_recipe}`"
            )

        # 3. Construct safe execution command string & sanitize target format for specific tools
        target_port = None

        if parsed_target.startswith("http://") or parsed_target.startswith("https://"):
            u = urlparse(parsed_target)
            host_only = u.hostname or parsed_target
            target_port = u.port
            base_url = f"{u.scheme}://{u.netloc}"
        else:
            host_only = parsed_target.split(":")[0]
            base_url = parsed_target

        if selected_tool.tool_name == "nmap":
            target_for_cmd = host_only
            extra_port = f" -p {target_port}" if target_port else ""
            raw_args = selected_tool.args_template.format(target=target_for_cmd) + extra_port
        elif selected_tool.tool_name == "ffuf":
            from backend.execution.wordlist import wordlist_resolver  # lazy — avoids circular import
            clean_url = base_url.rstrip("/")
            wl_path = wordlist_resolver.resolve("web_common")
            raw_args = f"-u {clean_url}/FUZZ -w {wl_path} -mc 200,301,302,401,403 -s"
        else:
            raw_args = selected_tool.args_template.format(target=parsed_target)

        if extra_args:
            raw_args += f" {extra_args}"

        full_command = f"{selected_tool.binary} {raw_args}"
        full_command = sanitize_and_correct_command_target(full_command, target)
        logger.info(f"Executing tool '{selected_tool.tool_name}' (cwd={cwd}): {full_command}")

        # 4. Delegate subprocess execution to ExecutionService (Phase 3)
        exec_cwd = cwd if (cwd and os.path.exists(cwd)) else None
        _exec = await execution_service.run_command(
            full_command,
            cwd=exec_cwd,
            timeout_seconds=selected_tool.timeout_seconds,
            capability=capability,
            tool_name=selected_tool.tool_name,
        )
        stdout = _exec.stdout
        stderr = _exec.stderr
        exit_code = _exec.exit_code
        status = _exec.status

        elapsed_ms = (time.time() - start_time) * 1000
        classification = classify_tool_execution(selected_tool.tool_name, exit_code, stdout, stderr)

        return ToolExecutionResult(
            tool_name=selected_tool.tool_name,
            capability=capability,
            command=full_command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=elapsed_ms,
            execution_failure=classification["execution_failure"],
            failure_category=classification["failure_category"]
        )

    async def execute_raw_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        canonical_target: Optional[str] = None,
        stdin: Optional[str] = None
    ) -> ToolExecutionResult:
        start_time = time.time()
        raw_cmd = sanitize_and_correct_command_target(command.strip(), canonical_target)
        logger.info(f"Executing raw CLI command (cwd={cwd}): {raw_cmd}")

        # Model capability intercept: vision_read
        if raw_cmd.startswith("vision_read ") or raw_cmd.startswith("vision_read\t") or raw_cmd == "vision_read":
            target_file = raw_cmd.split(None, 1)[1].strip().strip('"').strip("'") if " " in raw_cmd or "\t" in raw_cmd else ""
            return await self.execute_capability(capability="vision_read", target=target_file, cwd=cwd)

        # Model capability intercept: interactive execution primitives
        if raw_cmd.startswith("interactive_open ") or raw_cmd.startswith("interactive_open\t") or raw_cmd == "interactive_open":
            cmd_arg = raw_cmd.split(None, 1)[1].strip() if " " in raw_cmd or "\t" in raw_cmd else ""
            return await self.execute_capability(capability="interactive_open", target=cmd_arg, cwd=cwd)

        if raw_cmd.startswith("interactive_send ") or raw_cmd.startswith("interactive_send\t"):
            parts = raw_cmd.split(None, 2)
            sess_key = parts[1].strip() if len(parts) > 1 else ""
            data_arg = parts[2] if len(parts) > 2 else ""
            return await self.execute_capability(capability="interactive_send", target=sess_key, extra_args=data_arg, cwd=cwd)

        if raw_cmd.startswith("interactive_read ") or raw_cmd.startswith("interactive_read\t") or raw_cmd == "interactive_read":
            parts = raw_cmd.split(None, 2)
            sess_key = parts[1].strip() if len(parts) > 1 else ""
            until_arg = parts[2].strip() if len(parts) > 2 else ""
            return await self.execute_capability(capability="interactive_read", target=sess_key, extra_args=until_arg, cwd=cwd)

        if raw_cmd.startswith("interactive_send_and_read ") or raw_cmd.startswith("interactive_send_and_read\t"):
            parts = raw_cmd.split(None, 2)
            sess_key = parts[1].strip() if len(parts) > 1 else ""
            data_arg = parts[2] if len(parts) > 2 else ""
            return await self.execute_capability(capability="interactive_send_and_read", target=sess_key, extra_args=data_arg, cwd=cwd)

        if raw_cmd.startswith("interactive_close ") or raw_cmd.startswith("interactive_close\t") or raw_cmd == "interactive_close":
            sess_key = raw_cmd.split(None, 1)[1].strip() if " " in raw_cmd or "\t" in raw_cmd else ""
            return await self.execute_capability(capability="interactive_close", target=sess_key, cwd=cwd)

        # Delegate subprocess execution to ExecutionService (Phase 3). When *stdin* is
        # supplied it is fed to the process once (Tier-1 scripted interactive, Phase 4.x §4).
        exec_cwd = cwd if (cwd and os.path.exists(cwd)) else None
        _exec = await execution_service.run_command(
            raw_cmd,
            cwd=exec_cwd,
            timeout_seconds=timeout_seconds,
            capability="custom_command",
            tool_name=os.path.basename(raw_cmd.split()[0]) if raw_cmd.strip() else "raw_cmd",
            stdin=stdin,
        )
        stdout = _exec.stdout
        stderr = _exec.stderr
        exit_code = _exec.exit_code
        status = _exec.status

        elapsed_ms = (time.time() - start_time) * 1000
        first_word = raw_cmd.split()[0] if raw_cmd else "raw_cmd"
        binary_name = os.path.basename(first_word)
        classification = classify_tool_execution(binary_name, exit_code, stdout, stderr)

        return ToolExecutionResult(
            tool_name=binary_name,
            capability="custom_command",
            command=raw_cmd,
            status=status,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=elapsed_ms,
            execution_failure=classification["execution_failure"],
            failure_category=classification["failure_category"]
        )

    async def execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        timeout: int = 60,
        working_directory: Optional[str] = None,
        canonical_target: Optional[str] = None
    ) -> ToolExecutionResult:
        """Universal tool invocation supporting CLI/bash commands and registered capabilities."""
        if tool_name in ["bash", "sh", "cli", "terminal", "command", "raw"]:
            cmd = params.get("command") or params.get("cmd") or ""
            return await self.execute_raw_command(
                command=cmd,
                cwd=working_directory,
                timeout_seconds=timeout,
                canonical_target=canonical_target
            )
        elif tool_name in ["interactive_open", "interactive_send", "interactive_read", "interactive_send_and_read", "interactive_close", "interactive"]:
            action = tool_name
            if tool_name == "interactive":
                action = params.get("action") or "open"
                if not action.startswith("interactive_"):
                    action = f"interactive_{action}"
            target = params.get("target") or params.get("command") or params.get("cmd") or params.get("session_key") or params.get("session_id") or ""
            extra_args = params.get("extra_args") or params.get("data") or params.get("input") or params.get("until") or params.get("args") or ""
            return await self.execute_capability(
                capability=action,
                target=str(target),
                extra_args=str(extra_args) if extra_args else None,
                cwd=working_directory
            )
        else:
            target = params.get("target") or params.get("url") or params.get("ip") or params.get("file_path") or params.get("image_path") or params.get("path") or canonical_target or ""
            extra_args = params.get("extra_args") or params.get("args") or ""
            return await self.execute_capability(
                capability=tool_name,
                target=target,
                extra_args=extra_args,
                cwd=working_directory
            )


tool_manager = ToolManager()
