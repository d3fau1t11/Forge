import asyncio
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("forge.runner")

class WorkflowRunner:
    """Manages execution state, kill switch, and active autonomous run loops."""

    def __init__(self):
        self.active_runs: Dict[str, Dict[str, Any]] = {}
        self.kill_switches: Dict[str, bool] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

    def start_run(self, run_id: str, challenge_id: str, target: str):
        self.active_runs[run_id] = {
            "run_id": run_id,
            "challenge_id": challenge_id,
            "target": target,
            "status": "RUNNING",
            "current_phase": "recon",
            "current_agent": "orchestrator"
        }
        self.kill_switches[run_id] = False

        # Lazy import to avoid circular dependency
        try:
            loop = asyncio.get_running_loop()
            from backend.agents.orchestrator_loop import orchestrator_loop
            task = loop.create_task(orchestrator_loop.run_autonomous_loop(run_id, challenge_id, target))
            self.tasks[run_id] = task
        except RuntimeError:
            pass

        logger.info(f"Started workflow run {run_id} for target {target}")

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
            for rid in list(self.kill_switches.keys()):
                self.kill_switches[rid] = True
                if rid in self.active_runs:
                    self.active_runs[rid]["status"] = "CANCELLED"
                if rid in self.tasks and not self.tasks[rid].done():
                    self.tasks[rid].cancel()
            logger.warning("UNIVERSAL KILL SWITCH ACTIVATED - ALL RUNS HALTED.")

    def is_cancelled(self, run_id: str) -> bool:
        return self.kill_switches.get(run_id, False)

workflow_runner = WorkflowRunner()

