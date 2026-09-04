import asyncio
import logging
import shutil
import os
from typing import Dict, Any, Optional

logger = logging.getLogger("forge.runner")

class WorkflowRunner:
    """Manages execution state, kill switch, and active autonomous run loops."""

    def __init__(self):
        self.active_runs: Dict[str, Dict[str, Any]] = {}
        self.kill_switches: Dict[str, bool] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

    def is_kill_switch_active(self, run_id: Optional[str] = None) -> bool:
        if run_id:
            return self.kill_switches.get(run_id, False) or self.kill_switches.get("__global__", False)
        return self.kill_switches.get("__global__", False)

    def is_cancelled(self, run_id: str) -> bool:
        return self.is_kill_switch_active(run_id)

    def start_run(
        self,
        run_id: str,
        challenge_id: str,
        target: str,
        engine_type: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.active_runs[run_id] = {
            "run_id": run_id,
            "challenge_id": challenge_id,
            "target": target,
            "status": "RUNNING",
            "current_phase": "recon",
            "current_agent": engine_type or "auto"
        }
        self.kill_switches[run_id] = False

        # Determine which execution engine to dispatch
        # Check if Claude Code or Codex CLI is installed and preferred
        selected_engine = engine_type or "auto"

        if selected_engine == "auto":
            # If Claude Code or Codex CLI is available on system, pick CLI agent
            has_claude = bool(shutil.which("claude") or shutil.which("claude.cmd") or os.environ.get("CLAUDE_CODE_PATH"))
            has_codex = bool(shutil.which("codex") or shutil.which("codex.cmd") or os.environ.get("CODEX_PATH"))

            if has_claude:
                selected_engine = "claude_code"
            elif has_codex:
                selected_engine = "codex"
            else:
                selected_engine = "react_loop"

        try:
            loop = asyncio.get_running_loop()

            if selected_engine in ["claude_code", "codex"]:
                from backend.agents.cli_agent_runner import cli_agent_runner
                task = loop.create_task(
                    cli_agent_runner.run_cli_agent_loop(
                        run_id=run_id,
                        challenge_id=challenge_id,
                        target=target,
                        agent_type=selected_engine,
                        model=model
                    )
                )
            else:
                from backend.agents.orchestrator_loop import orchestrator_loop
                task = loop.create_task(
                    orchestrator_loop.run_autonomous_loop(
                        run_id=run_id,
                        challenge_id=challenge_id,
                        target=target
                    )
                )

            self.tasks[run_id] = task
            logger.info(f"Started workflow run {run_id} using engine '{selected_engine}' for target {target}")

        except RuntimeError as e:
            logger.error(f"Failed to create run task for run {run_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error starting run task for run {run_id}: {e}")

    def activate_kill_switch(self, run_id: Optional[str] = None):
        """Emergency Kill Switch - immediately halts autonomous operations."""
        if run_id and run_id in self.kill_switches:
            self.kill_switches[run_id] = True
            if run_id in self.active_runs:
                self.active_runs[run_id]["status"] = "CANCELLED"
            if run_id in self.tasks and not self.tasks[run_id].done():
                self.tasks[run_id].cancel()
            logger.warning(f"KILL SWITCH ACTIVATED FOR RUN {run_id}")
        else:
            # Universal kill switch
            self.kill_switches["__global__"] = True
            for rid in list(self.kill_switches.keys()):
                self.kill_switches[rid] = True
                if rid in self.active_runs:
                    self.active_runs[rid]["status"] = "CANCELLED"
                if rid in self.tasks and not self.tasks[rid].done():
                    self.tasks[rid].cancel()
            logger.warning("UNIVERSAL KILL SWITCH ACTIVATED - ALL RUNS HALTED.")

workflow_runner = WorkflowRunner()
