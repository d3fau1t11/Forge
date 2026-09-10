"""Mission-level resource / concurrency limits (Phase 4 §12).

These are the swarm's own bounds. LLM provider quota is NOT re-implemented here —
the agent runtime already routes through the existing ``model_router`` /
``quota_manager`` and fails over on provider exhaustion; the coordinator only reads
that system's status for observability.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SwarmLimits:
    max_concurrent_agents: int = 3     # how many specialists run at once
    max_active_tasks: int = 12         # cap on simultaneously non-terminal tasks
    max_task_retries: int = 2          # per-task retry budget before abandon/reassign
    task_timeout_seconds: int = 0      # wall-clock per task (0 = none; tests use 0)
    max_turns_per_task: int = 12       # AgentRuntime max_turns for one task
    max_total_tasks: int = 40          # safety cap on total tasks created in a mission
