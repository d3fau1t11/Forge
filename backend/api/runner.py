import asyncio
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel

logger = logging.getLogger("forge.runner")

class WorkflowRunner:
    """Manages execution state, kill switch, and active autonomous run loops."""

    def __init__(self):
        self.active_runs: Dict[str, Dict[str, Any]] = {}
        self.kill_switches: Dict[str, bool] = {}

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
        logger.info(f"Started workflow run {run_id} for target {target}")

    def activate_kill_switch(self, run_id: Optional[str] = None):
        """Emergency Kill Switch - immediately halts autonomous operations."""
        if run_id and run_id in self.kill_switches:
            self.kill_switches[run_id] = True
            if run_id in self.active_runs:
                self.active_runs[run_id]["status"] = "CANCELLED"
            logger.warning(f"KILL SWITCH ACTIVATED FOR RUN {run_id}")
        else:
            # Universal kill switch
            for rid in self.kill_switches:
                self.kill_switches[rid] = True
                if rid in self.active_runs:
                    self.active_runs[rid]["status"] = "CANCELLED"
            logger.warning("UNIVERSAL KILL SWITCH ACTIVATED - ALL RUNS HALTED.")

    def is_cancelled(self, run_id: str) -> bool:
        return self.kill_switches.get(run_id, False)

workflow_runner = WorkflowRunner()
