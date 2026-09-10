import os
import re
import difflib
from urllib.parse import urlparse
import asyncio
import time
import shutil
import subprocess
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
            or "not permitted" in combined):
        return {"execution_failure": True, "failure_category": "PERMISSION_DENIED"}
    if ("modulenotfounderror" in combined or "no module named" in combined
            or "importerror" in combined):
        return {"execution_failure": True, "failure_category": "MISSING_DEP"}

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
        parsed_target = target.replace("+", ",").split(",")[0].strip()
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
        canonical_target: Optional[str] = None
    ) -> ToolExecutionResult:
        start_time = time.time()
        raw_cmd = sanitize_and_correct_command_target(command.strip(), canonical_target)
        logger.info(f"Executing raw CLI command (cwd={cwd}): {raw_cmd}")

        # Delegate subprocess execution to ExecutionService (Phase 3)
        exec_cwd = cwd if (cwd and os.path.exists(cwd)) else None
        _exec = await execution_service.run_command(
            raw_cmd,
            cwd=exec_cwd,
            timeout_seconds=timeout_seconds,
            capability="custom_command",
            tool_name=os.path.basename(raw_cmd.split()[0]) if raw_cmd.strip() else "raw_cmd",
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
        else:
            target = params.get("target") or params.get("url") or params.get("ip") or canonical_target or ""
            extra_args = params.get("extra_args") or params.get("args") or ""
            return await self.execute_capability(
                capability=tool_name,
                target=target,
                extra_args=extra_args,
                cwd=working_directory
            )


tool_manager = ToolManager()
