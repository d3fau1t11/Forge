import asyncio
import time
import shutil
import subprocess
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel
from backend.tools.registry import tool_registry, ToolMetadata
from backend.environment.detector import environment_detector

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

class ToolManager:
    """Resolves agent capability requests to concrete installed tool executions."""

    async def execute_capability(
        self,
        capability: str,
        target: str,
        extra_args: Optional[str] = None
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
            # Pick first candidate & report missing tool with trusted installation recipe
            first_candidate = candidate_tools[0]
            return ToolExecutionResult(
                tool_name=first_candidate.tool_name,
                capability=capability,
                command="",
                status="MISSING_TOOL",
                stderr=f"Tool '{first_candidate.tool_name}' required for capability '{capability}' is not installed. Trusted install recipe: `{first_candidate.installation_recipe}`"
            )

        # 3. Construct safe execution command string
        raw_args = selected_tool.args_template.format(target=target)
        if extra_args:
            raw_args += f" {extra_args}"
        
        full_command = f"{selected_tool.binary} {raw_args}"
        logger.info(f"Executing tool '{selected_tool.tool_name}': {full_command}")

        # 4. Controlled subprocess execution
        try:
            process = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=selected_tool.timeout_seconds
                )
                stdout = stdout_bytes.decode(errors="replace")
                stderr = stderr_bytes.decode(errors="replace")
                exit_code = process.returncode
                status = "SUCCESS" if exit_code == 0 else "FAILED"
            except asyncio.TimeoutError:
                process.kill()
                stdout = ""
                stderr = f"Tool execution timed out after {selected_tool.timeout_seconds} seconds."
                exit_code = -1
                status = "TIMEOUT"

        except Exception as e:
            stdout = ""
            stderr = f"Subprocess creation error: {str(e)}"
            exit_code = -1
            status = "FAILED"

        elapsed_ms = (time.time() - start_time) * 1000

        return ToolExecutionResult(
            tool_name=selected_tool.tool_name,
            capability=capability,
            command=full_command,
            status=status,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=elapsed_ms
        )

tool_manager = ToolManager()
