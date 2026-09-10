"""
Platform-aware process manager for the FORGE execution layer.

Responsibilities: spawn, monitor, timeout, kill, cleanup.
Windows and Linux differ in how process trees are killed; both are handled here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("forge.execution.process")

_IS_WINDOWS = sys.platform == "win32"


@dataclass
class ManagedProcess:
    pid: int
    command: str
    session_id: str
    agent_id: str
    backend: str
    start_time: float = field(default_factory=time.time)
    timeout_seconds: int = 120
    state: str = "running"   # running | done | killed | timeout


class ProcessManager:
    """
    Owns the lifecycle of subprocesses spawned by execution backends.

    One singleton is shared across backends so all active PIDs are visible in
    one place (useful for diagnostics and the compliance audit).
    """

    def __init__(self) -> None:
        self._active: Dict[int, ManagedProcess] = {}

    # ------------------------------------------------------------------ #

    async def run(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        env: Optional[Dict[str, str]] = None,
        session_id: str = "",
        agent_id: str = "",
        backend: str = "local",
        input_data: Optional[str] = None,
    ) -> tuple[str, str, int]:
        """
        Run *command* in a subprocess and return (stdout, stderr, exit_code).

        When *input_data* is supplied it is written once to the process's stdin
        (Tier-1 scripted interactive execution, Phase 4.x §4): the structured
        equivalent of ``printf '...' | command``.  On timeout the process tree is
        killed and exit_code -1 is returned with a descriptive stderr message.
        The caller decides what status to assign.
        """
        merged_env = None
        if env:
            merged_env = {**os.environ, **env}

        try:
            kwargs: dict = dict(
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if input_data is not None:
                kwargs["stdin"] = asyncio.subprocess.PIPE
            if cwd and os.path.isdir(cwd):
                kwargs["cwd"] = cwd
            if merged_env:
                kwargs["env"] = merged_env

            # On POSIX start in its own process group so we can kill the tree.
            if not _IS_WINDOWS:
                kwargs["preexec_fn"] = os.setsid  # type: ignore[attr-defined]

            process = await asyncio.create_subprocess_shell(command, **kwargs)

        except Exception as exc:
            return "", f"Subprocess creation error: {exc}", -1

        managed = ManagedProcess(
            pid=process.pid,
            command=command,
            session_id=session_id,
            agent_id=agent_id,
            backend=backend,
            timeout_seconds=timeout_seconds,
        )
        self._active[process.pid] = managed

        stdin_bytes = input_data.encode(errors="replace") if input_data is not None else None
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(input=stdin_bytes), timeout=float(timeout_seconds)
            )
            managed.state = "done"
            return (
                stdout_b.decode(errors="replace"),
                stderr_b.decode(errors="replace"),
                process.returncode if process.returncode is not None else -1,
            )

        except asyncio.TimeoutError:
            managed.state = "timeout"
            await self._kill_tree(process)
            return (
                "",
                f"Command execution timed out after {timeout_seconds} seconds.",
                -1,
            )

        finally:
            self._active.pop(managed.pid, None)

    # ------------------------------------------------------------------ #

    async def _kill_tree(self, process: asyncio.subprocess.Process) -> None:
        """Kill the process and, where the platform supports it, its whole tree."""
        try:
            if _IS_WINDOWS:
                # taskkill /F /T kills the full process tree on Windows.
                kill_proc = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(kill_proc.wait(), timeout=5)
            else:
                import signal
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except Exception:
            pass

        try:
            process.kill()
        except ProcessLookupError:
            pass

    async def kill(self, process: asyncio.subprocess.Process) -> None:
        """Public entry point to kill a process tree (used by interactive sessions)."""
        await self._kill_tree(process)

    # ------------------------------------------------------------------ #
    # External process registration — lets long-lived interactive sessions
    # (which own their own asyncio.subprocess.Process) still appear in the one
    # place all active PIDs are visible, for diagnostics and the compliance audit.
    # ------------------------------------------------------------------ #

    def register(self, managed: ManagedProcess) -> None:
        self._active[managed.pid] = managed

    def unregister(self, pid: int) -> None:
        self._active.pop(pid, None)

    def get(self, pid: int) -> Optional[ManagedProcess]:
        return self._active.get(pid)

    # ------------------------------------------------------------------ #

    def active_count(self) -> int:
        return len(self._active)

    def active_pids(self) -> list:
        return list(self._active.keys())


# Module-level singleton.
process_manager = ProcessManager()
