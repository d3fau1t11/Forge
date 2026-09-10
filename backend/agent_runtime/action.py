"""
FORGE Agent Runtime — executable actions and the tool-executor abstraction.

Action *validation* is kept strictly separate from action *execution* (Step 2):

* :class:`Action`         — a typed, immutable description of what to do.
* :class:`ActionValidator`— structural / safety checks, no side effects.
* :class:`ToolExecutor`   — protocol for "actually run it"; the runtime is handed
                            one, so production wraps the real ToolManager while
                            tests inject a scripted double (no network, no subprocess).

The executor returns an :class:`ExecResult`, whose fields mirror
``backend.tools.manager.ToolExecutionResult`` exactly, so the real ToolManager
result is consumed directly (duck-typed) and the scripted test double produces the
same shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class ActionType(str, Enum):
    COMMAND = "command"           # a single shell/bash command line
    PYTHON_SCRIPT = "python_script"  # a full python program (saved as solve.py, then run)
    TOOL_CALL = "tool_call"       # a structured capability request routed via ToolManager
    COMPLETE = "complete"         # the agent asserts the mission is finished (flag / budget)


@dataclass(frozen=True)
class Action:
    """A typed, validated-once description of the next thing to do."""
    type: ActionType
    command: str = ""                          # for COMMAND
    script: str = ""                           # for PYTHON_SCRIPT
    stdin: str = ""                            # Phase 4.x: predetermined stdin for COMMAND/PYTHON_SCRIPT
    tool_name: str = ""                        # for TOOL_CALL
    capability: str = ""                       # for TOOL_CALL
    tool_args: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""                           # why the agent chose this (for the trajectory)
    raw: str = ""                              # the original model text this was parsed from

    def display(self) -> str:
        """Human/loggable one-liner for the trajectory + repetition signatures."""
        if self.type == ActionType.COMMAND:
            return self.command
        if self.type == ActionType.PYTHON_SCRIPT:
            first = next((ln for ln in self.script.splitlines() if ln.strip()), "python solve.py")
            return f"python solve.py  # {first[:80]}"
        if self.type == ActionType.TOOL_CALL:
            return f"{self.tool_name or self.capability} {self.tool_args or ''}".strip()
        return "COMPLETE"


@dataclass
class ExecResult:
    """Mirror of ToolExecutionResult so both real and scripted executors share a shape."""
    tool_name: str = "raw_cmd"
    capability: str = "custom_command"
    command: str = ""
    status: str = "SUCCESS"                     # SUCCESS | FAILED | TIMEOUT | MISSING_TOOL | KILLED
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = 0
    duration_ms: float = 0.0
    execution_failure: bool = False
    failure_category: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCESS" and not self.execution_failure

    @classmethod
    def from_tool_result(cls, r: Any) -> "ExecResult":
        return cls(
            tool_name=getattr(r, "tool_name", "raw_cmd"),
            capability=getattr(r, "capability", "custom_command"),
            command=getattr(r, "command", ""),
            status=getattr(r, "status", "SUCCESS"),
            stdout=getattr(r, "stdout", "") or "",
            stderr=getattr(r, "stderr", "") or "",
            exit_code=getattr(r, "exit_code", 0),
            duration_ms=float(getattr(r, "duration_ms", 0.0) or 0.0),
            execution_failure=bool(getattr(r, "execution_failure", False)),
            failure_category=getattr(r, "failure_category", None),
        )


@runtime_checkable
class ToolExecutor(Protocol):
    """The runtime depends only on this narrow protocol — never on a concrete tool stack."""

    async def execute(self, action: Action, *, cwd: Optional[str] = None,
                      timeout_seconds: int = 120, canonical_target: Optional[str] = None) -> ExecResult:
        ...


# A conservative allow/deny structural validator. It deliberately does NOT try to
# be a sandbox (privilege/safety is the PrivilegeManager's job in production); it
# only rejects actions that are structurally unusable so we never waste a turn.
_EMPTY = re.compile(r"^\s*$")
# Obvious destructive patterns we refuse outright even before privilege routing.
_HARD_DENY = re.compile(
    r"(?:\brm\s+-rf\s+/(?:\s|$)|:\(\)\s*\{\s*:\|:&\s*\}|>\s*/dev/sd[a-z]|mkfs\.|\bshutdown\b|\breboot\b)",
    re.IGNORECASE,
)


class ActionValidator:
    """Structural validation only — no execution, no privilege decisions."""

    def validate(self, action: Optional[Action]) -> "ValidationResult":
        if action is None:
            return ValidationResult(False, "no_action", "Model produced no parseable action.")

        if action.type == ActionType.COMMAND:
            if _EMPTY.match(action.command or ""):
                return ValidationResult(False, "empty_command", "Command action has an empty command.")
            if _HARD_DENY.search(action.command):
                return ValidationResult(False, "hard_denied",
                                        "Command matches a hard-denied destructive pattern.")
            return ValidationResult(True, "ok", "")

        if action.type == ActionType.PYTHON_SCRIPT:
            if _EMPTY.match(action.script or ""):
                return ValidationResult(False, "empty_script", "Python action has an empty script body.")
            return ValidationResult(True, "ok", "")

        if action.type == ActionType.TOOL_CALL:
            if not (action.tool_name or action.capability):
                return ValidationResult(False, "no_tool", "Tool call names neither a tool nor a capability.")
            return ValidationResult(True, "ok", "")

        if action.type == ActionType.COMPLETE:
            return ValidationResult(True, "ok", "")

        return ValidationResult(False, "unknown_type", f"Unknown action type: {action.type!r}")


@dataclass
class ValidationResult:
    ok: bool
    code: str
    reason: str
