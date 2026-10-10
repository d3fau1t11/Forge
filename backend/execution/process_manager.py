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
import shlex
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
    # A stable identifier for ONE concrete execution attempt (distinct from the OS
    # pid, which the kernel may reuse, and from any logical task id). Interactive
    # sessions set this to their session_key so a resumed mission can tell an old
    # attempt apart from a freshly recreated one (Phase 4.x hardening §4).
    execution_id: str = ""


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

        This is the **shell** entry point (``create_subprocess_shell``).  It exists
        for agent-authored commands that genuinely need shell syntax (pipes,
        redirection, chaining).  It is NOT a sandbox: callers composing a command
        from untrusted values must use :meth:`run_argv` instead.
        """
        kwargs = self._spawn_kwargs(cwd, env, input_data)
        # On POSIX start in its own process group so we can kill the tree.
        if not _IS_WINDOWS:
            kwargs["preexec_fn"] = os.setsid  # type: ignore[attr-defined]

        try:
            process = await asyncio.create_subprocess_shell(command, **kwargs)
        except Exception as exc:
            return "", f"Subprocess creation error: {exc}", -1

        return await self._monitor(
            process,
            command=command,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            agent_id=agent_id,
            backend=backend,
            input_data=input_data,
        )

    # ------------------------------------------------------------------ #

    async def run_argv(
        self,
        argv: "list[str]",
        *,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        env: Optional[Dict[str, str]] = None,
        session_id: str = "",
        agent_id: str = "",
        backend: str = "local",
        input_data: Optional[str] = None,
        display_command: str = "",
    ) -> tuple[str, str, int]:
        """Execute a pre-split argument vector WITHOUT a shell (``shell=False``).

        Every element of *argv* is passed to the OS as *one* argument; no element
        is ever parsed by a shell.  This is the execution primitive for commands
        composed from untrusted values (challenge targets, extra tool args), so a
        value like ``127.0.0.1; rm -rf /`` stays a single literal argument instead
        of becoming a second command.

        Lifecycle, timeout, cancellation, and process-tree cleanup are identical
        to :meth:`run` (they share :meth:`_monitor`).
        """
        argv = [str(a) for a in argv]
        if not argv:
            return "", "Empty argv: nothing to execute.", -1

        kwargs = self._spawn_kwargs(cwd, env, input_data)
        if not _IS_WINDOWS:
            kwargs["preexec_fn"] = os.setsid  # type: ignore[attr-defined]

        try:
            # create_subprocess_exec never invokes a shell (no shell=True path).
            process = await asyncio.create_subprocess_exec(*argv, **kwargs)
        except Exception as exc:
            return "", f"Subprocess creation error: {exc}", -1

        return await self._monitor(
            process,
            command=display_command or shlex.join(argv),
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            agent_id=agent_id,
            backend=backend,
            input_data=input_data,
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _spawn_kwargs(
        cwd: Optional[str], env: Optional[Dict[str, str]], input_data: Optional[str]
    ) -> dict:
        """Build the shared subprocess kwargs (pipes, cwd, env, optional stdin)."""
        merged_env = {**os.environ, **env} if env else None
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
        return kwargs

    async def _monitor(
        self,
        process: asyncio.subprocess.Process,
        *,
        command: str,
        timeout_seconds: int,
        session_id: str,
        agent_id: str,
        backend: str,
        input_data: Optional[str],
    ) -> tuple[str, str, int]:
        """Register, await, timeout, and reap a spawned process.

        Shared by the shell (:meth:`run`) and shell-free (:meth:`run_argv`) entry
        points.  The timeout and cancellation branches kill and reap the process
        tree via :meth:`_kill_tree_and_wait` before returning/propagating, so a
        cancelled or timed-out run never orphans a subprocess.
        """
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
            await self._kill_tree_and_wait(process)
            return (
                "",
                f"Command execution timed out after {timeout_seconds} seconds.",
                -1,
            )

        except asyncio.CancelledError:
            # Cancellation (task teardown, event-loop shutdown, kill switch) must
            # not orphan the subprocess: terminate the whole tree and reap it
            # BEFORE propagating the cancellation. Cleanup is safe when the
            # process has already exited.
            managed.state = "killed"
            await self._kill_tree_and_wait(process)
            raise

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

    async def _kill_tree_and_wait(self, process: asyncio.subprocess.Process) -> None:
        """Kill the process tree, then await its exit so the OS reaps it.

        Used by the timeout and cancellation paths. Safe when the process has
        already exited: the kill calls no-op and ``process.wait()`` returns the
        cached return code immediately. The bounded timeout guarantees cleanup
        can never hang the cancelling caller indefinitely.
        """
        await self._kill_tree(process)
        try:
            if process.returncode is None:
                await asyncio.wait_for(process.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
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
        logger.info(
            "PROCESS_REGISTERED pid=%s execution_id=%s session=%s agent=%s backend=%s",
            managed.pid, managed.execution_id or "-", managed.session_id or "-",
            managed.agent_id or "-", managed.backend,
        )

    def unregister(self, pid: int) -> None:
        self._active.pop(pid, None)

    def get(self, pid: int) -> Optional[ManagedProcess]:
        return self._active.get(pid)


    def terminate_pid(self, pid: int, *, reason: str = "terminate") -> bool:
        """Synchronously terminate a FORGE-tracked process by PID.

        This is the bridge between *registry state* and *actual process lifecycle*
        (Phase 4.x hardening §2): clearing a registry entry must never leave the OS
        process running. It is used by the synchronous resume path
        (:meth:`InteractiveSessionManager.mark_all_stale`) where there is no event
        loop to ``await`` a graceful async close.

        Safety guarantees:
        * Only ever acts on a PID that FORGE explicitly tracks in ``_active`` — it
          will NEVER signal an untracked/foreign process (returns ``False``). This
          preserves the security boundary: no arbitrary process termination.
        * Idempotent and crash-free: an already-exited or missing process is handled
          gracefully (``ProcessLookupError`` is swallowed and reported as
          ``PROCESS_ALREADY_EXITED``), and the registry entry is always released.

        Returns True iff a tracked entry was found and cleanup was attempted.
        """
        managed = self._active.get(pid)
        if managed is None:
            # Not FORGE-owned (or already cleaned up) — do nothing. Never kill a
            # process we do not track.
            return False

        logger.info(
            "PROCESS_TERMINATION_REQUESTED pid=%s execution_id=%s backend=%s reason=%s",
            pid, managed.execution_id or "-", managed.backend, reason,
        )
        terminated = False
        already_gone = False
        try:
            if _IS_WINDOWS:
                import subprocess
                completed = subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                )
                # taskkill exits 128 when the PID no longer exists.
                already_gone = completed.returncode == 128
                terminated = completed.returncode == 0
            else:
                import signal
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    terminated = True
                except ProcessLookupError:
                    already_gone = True
                    # Fall back to a direct kill in case the pgid lookup was the miss.
                    try:
                        os.kill(pid, signal.SIGKILL)
                        terminated = True
                    except ProcessLookupError:
                        pass
        except Exception as exc:  # never let cleanup crash the resume path
            logger.warning("PROCESS_CLEANUP_FAILED pid=%s reason=%s error=%s", pid, reason, exc)
        finally:
            managed.state = "killed"
            self._active.pop(pid, None)

        if already_gone and not terminated:
            logger.info("PROCESS_ALREADY_EXITED pid=%s execution_id=%s", pid, managed.execution_id or "-")
        else:
            logger.info("PROCESS_TERMINATED pid=%s execution_id=%s", pid, managed.execution_id or "-")
        return True

    # ------------------------------------------------------------------ #

    def active_count(self) -> int:
        return len(self._active)

    def active_pids(self) -> list:
        return list(self._active.keys())


# Module-level singleton.
process_manager = ProcessManager()
