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
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger("forge.execution.process")

_IS_WINDOWS = sys.platform == "win32"

_TASKKILL_TIMEOUT_SECONDS = 5.0
_HELPER_REAP_TIMEOUT_SECONDS = 1.0
_HELPER_PIPE_TIMEOUT_SECONDS = 1.0
_DIRECT_CHILD_WAIT_TIMEOUT_SECONDS = 5.0
_CLEANUP_DIAGNOSTIC_LIMIT = 512


class CleanupSignalStatus(str, Enum):
    """What the platform-specific process-tree signaling attempt established."""

    SIGNALED = "signaled"
    ALREADY_EXITED = "already_exited"
    FAILED = "failed"


class DirectChildStatus(str, Enum):
    """What cleanup established about the direct asyncio child."""

    NOT_WAITED = "not_waited"
    EXITED = "exited"
    ALREADY_EXITED = "already_exited"
    WAIT_TIMEOUT = "wait_timeout"
    WAIT_FAILED = "wait_failed"


@dataclass(frozen=True)
class ProcessCleanupOutcome:
    """Bounded cleanup result without claiming descendant-exit verification."""

    signal_status: CleanupSignalStatus
    direct_child_status: DirectChildStatus = DirectChildStatus.NOT_WAITED
    detail: str = ""
    descendants_terminated_verified: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", self.detail[:_CLEANUP_DIAGNOSTIC_LIMIT])

    @property
    def failed(self) -> bool:
        return (
            self.signal_status is CleanupSignalStatus.FAILED
            or self.direct_child_status
            in (DirectChildStatus.WAIT_TIMEOUT, DirectChildStatus.WAIT_FAILED)
        )

    def with_direct_child(self, status: DirectChildStatus, detail: str = "") -> "ProcessCleanupOutcome":
        combined = "; ".join(part for part in (detail, self.detail) if part)
        return ProcessCleanupOutcome(
            signal_status=self.signal_status,
            direct_child_status=status,
            detail=combined[:_CLEANUP_DIAGNOSTIC_LIMIT],
        )

    def summary(self) -> str:
        summary = (
            f"signal={self.signal_status.value} "
            f"direct_child={self.direct_child_status.value} "
            "descendants_verified=false"
        )
        if self.detail:
            summary += f" detail={self.detail}"
        return summary


def _bounded_diagnostic(value: object) -> str:
    if isinstance(value, bytes):
        text = value.decode(errors="replace")
    else:
        text = str(value)
    return " ".join(text.split())[:_CLEANUP_DIAGNOSTIC_LIMIT]


def _exception_diagnostic(exc: BaseException) -> str:
    message = _bounded_diagnostic(exc)
    return type(exc).__name__ if not message else f"{type(exc).__name__}: {message}"


@dataclass
class ManagedProcess:
    pid: int
    command: str
    session_id: str
    agent_id: str
    backend: str
    start_time: float = field(default_factory=time.time)
    timeout_seconds: int = 120
    state: str = "running"   # running | done | killed | timeout | cleanup_failed
    # A stable identifier for ONE concrete execution attempt (distinct from the OS
    # pid, which the kernel may reuse, and from any logical task id). Interactive
    # sessions set this to their session_key so a resumed mission can tell an old
    # attempt apart from a freshly recreated one (Phase 4.x hardening §4).
    execution_id: str = ""


class _TaskkillHelper(asyncio.SubprocessProtocol):
    """Local taskkill ownership, using only public asyncio transport callbacks.

    No StreamReader or reader task accumulates output. The callbacks always
    consume/discard excess bytes; the combined retained prefix is at most 512
    bytes (and therefore at most 512 decoded characters).
    """

    def __init__(self, manager: "ProcessManager", target_pid: int) -> None:
        loop = asyncio.get_running_loop()
        self.manager = manager
        self.target_pid = target_pid
        self.transport: Optional[asyncio.SubprocessTransport] = None
        self.pipes: dict[int, asyncio.BaseTransport] = {}
        self.exit = loop.create_future()
        self.pipe_eof = loop.create_future()
        self.closed = loop.create_future()
        self.closed_pipes: set[int] = set()
        self.output = {1: bytearray(), 2: bytearray()}
        self.retained_bytes = 0
        self.close_requested = False
        self.state = "running"
        self.detail = ""
        self.pipe_error = ""
        self.cleanup_lock = asyncio.Lock()

    @property
    def pid(self) -> Optional[int]:
        return self.transport.get_pid() if self.transport is not None else None

    @property
    def returncode(self) -> Optional[int]:
        return self.transport.get_returncode() if self.transport is not None else None

    @property
    def resources_closed(self) -> bool:
        return (self.exit.done() and self.returncode is not None
                and self.close_requested and self.closed.done()
                and self.closed_pipes == {1, 2}
                and all(pipe.is_closing() for pipe in self.pipes.values()))

    def connection_made(self, transport):
        self.transport = transport
        self.pipes = {fd: transport.get_pipe_transport(fd) for fd in (1, 2)}
        # Register from the callback, before subprocess_exec returns to its caller.
        self.manager._taskkill_helpers[self] = self

    def pipe_data_received(self, fd, data):
        if fd in self.output:
            prefix = data[:max(0, _CLEANUP_DIAGNOSTIC_LIMIT - self.retained_bytes)]
            self.output[fd].extend(prefix)
            self.retained_bytes += len(prefix)

    def pipe_connection_lost(self, fd, exc):
        if fd in (1, 2):
            self.closed_pipes.add(fd)
            if exc is not None:
                self.pipe_error = f"helper_pipe_{fd}={_exception_diagnostic(exc)}"
            if self.closed_pipes == {1, 2} and not self.pipe_eof.done():
                self.pipe_eof.set_result(None)
        self._release_if_closed()

    def process_exited(self):
        if not self.exit.done():
            self.exit.set_result(None)
        self._release_if_closed()

    def connection_lost(self, exc):
        if exc is not None:
            self.pipe_error = f"helper_connection_lost={_exception_diagnostic(exc)}"
        if not self.closed.done():
            self.closed.set_result(None)
        self._release_if_closed()

    def _release_if_closed(self):
        if self.resources_closed:
            self.state = "finalized" if not self.pipe_error else "finalized_with_error"
            self.manager._taskkill_helpers.pop(self, None)

    async def communicate(self):
        # Shield only owned notification futures, never an unbounded worker task.
        # The caller puts a finite timeout around this entire operation.
        await asyncio.shield(self.pipe_eof)
        await asyncio.shield(self.exit)
        if self.pipe_error:
            raise OSError(self.pipe_error)
        return bytes(self.output[1]), bytes(self.output[2])

    async def wait(self):
        await asyncio.shield(self.exit)
        return self.returncode

    def terminate(self):
        self.transport.terminate()

    def kill(self):
        self.transport.kill()


class ProcessManager:
    """
    Owns the lifecycle of subprocesses spawned by execution backends.

    One singleton is shared across backends so all active PIDs are visible in
    one place (useful for diagnostics and the compliance audit).
    """

    def __init__(self) -> None:
        self._active: Dict[int, ManagedProcess] = {}
        # Handle identity, not PID identity; target metadata remains separate.
        self._taskkill_helpers: dict[_TaskkillHelper, _TaskkillHelper] = {}

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
        tree via :meth:`_kill_tree_and_wait` before returning/propagating. Cleanup
        failures are reported without claiming that termination was confirmed.
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
            cleanup = await self._kill_tree_and_wait(process)
            cleanup_note = ""
            if cleanup.failed:
                cleanup_note = f" Cleanup incomplete: {cleanup.summary()}."
                logger.warning(
                    "PROCESS_ASYNC_CLEANUP_FAILED pid=%s reason=timeout outcome=%s",
                    managed.pid, cleanup.summary(),
                )
            return (
                "",
                f"Command execution timed out after {timeout_seconds} seconds.{cleanup_note}",
                -1,
            )

        except asyncio.CancelledError:
            # Cancellation (task teardown, event-loop shutdown, kill switch) must
            # not orphan the subprocess: terminate the whole tree and reap it
            # BEFORE propagating the cancellation. Cleanup is safe when the
            # process has already exited.
            managed.state = "killed"
            cleanup = await self._kill_tree_and_wait(process)
            if cleanup.failed:
                logger.warning(
                    "PROCESS_ASYNC_CLEANUP_FAILED pid=%s reason=cancelled outcome=%s",
                    managed.pid, cleanup.summary(),
                )
            raise

        finally:
            self._active.pop(managed.pid, None)

    # ------------------------------------------------------------------ #

    async def _kill_tree(self, process: asyncio.subprocess.Process) -> ProcessCleanupOutcome:
        """Attempt platform tree signaling and report what was actually established."""
        if process.returncode is not None:
            return ProcessCleanupOutcome(CleanupSignalStatus.ALREADY_EXITED)

        detail = ""
        cancellation = None
        if _IS_WINDOWS:
            # taskkill /F /T requests full-tree termination. Exit status zero proves
            # only that the helper accepted/completed the request, not that every
            # descendant has been independently observed exiting.
            try:
                loop = asyncio.get_running_loop()
                _, kill_proc = await loop.subprocess_exec(
                    lambda: _TaskkillHelper(self, process.pid),
                    "taskkill", "/F", "/T", "/PID", str(process.pid),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except Exception as exc:
                outcome = ProcessCleanupOutcome(
                    CleanupSignalStatus.FAILED,
                    detail=f"taskkill_invocation={_exception_diagnostic(exc)}",
                )
            else:
                helper_output = (b"", b"")
                try:
                    helper_output = await asyncio.wait_for(
                        kill_proc.communicate(),
                        timeout=_TASKKILL_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    helper_detail = await self._stop_taskkill_helper(kill_proc)
                    outcome = ProcessCleanupOutcome(
                        CleanupSignalStatus.FAILED,
                        detail=f"taskkill_timeout {helper_detail}",
                    )
                except asyncio.CancelledError as exc:
                    cancellation = exc
                    try:
                        helper_detail = await self._stop_taskkill_helper(kill_proc)
                    except Exception as cleanup_exc:
                        # Ownership was established at spawn and is retained even
                        # when a secondary cleanup error interrupts this path.
                        helper_detail = f"helper_cleanup={_exception_diagnostic(cleanup_exc)}"
                        kill_proc.state = "unresolved"
                        kill_proc.detail = helper_detail
                    outcome = ProcessCleanupOutcome(
                        CleanupSignalStatus.FAILED,
                        detail=f"taskkill_cancelled {helper_detail}",
                    )
                except Exception as exc:
                    helper_detail = await self._stop_taskkill_helper(kill_proc)
                    outcome = ProcessCleanupOutcome(
                        CleanupSignalStatus.FAILED,
                        detail=f"{helper_detail}; taskkill_wait={_exception_diagnostic(exc)}",
                    )
                else:
                    helper_detail = await self._finalize_taskkill_helper(kill_proc)
                    stdout_b, stderr_b = helper_output
                    output_detail = " ".join(
                        part for part in (
                            f"stdout={_bounded_diagnostic(stdout_b)}" if stdout_b else "",
                            f"stderr={_bounded_diagnostic(stderr_b)}" if stderr_b else "",
                        ) if part
                    )
                    if kill_proc.returncode == 0 and kill_proc.resources_closed and not kill_proc.pipe_error:
                        outcome = ProcessCleanupOutcome(
                            CleanupSignalStatus.SIGNALED,
                            detail=f"{helper_detail}; {output_detail}",
                        )
                    else:
                        outcome = ProcessCleanupOutcome(
                            CleanupSignalStatus.FAILED,
                            detail=(
                                f"{helper_detail}; taskkill_exit_status={kill_proc.returncode}"
                                + (f" {output_detail}" if output_detail else "")
                            ),
                        )
        else:
            import signal
            try:
                process_group = os.getpgid(process.pid)
            except ProcessLookupError:
                # Never fall back to the raw PID after this lookup says it is gone:
                # the PID may already have been reused by an unrelated process.
                return ProcessCleanupOutcome(CleanupSignalStatus.ALREADY_EXITED)
            except Exception as exc:
                outcome = ProcessCleanupOutcome(
                    CleanupSignalStatus.FAILED,
                    detail=f"getpgid={_exception_diagnostic(exc)}",
                )
            else:
                try:
                    os.killpg(process_group, signal.SIGKILL)
                except ProcessLookupError:
                    return ProcessCleanupOutcome(CleanupSignalStatus.ALREADY_EXITED)
                except Exception as exc:
                    outcome = ProcessCleanupOutcome(
                        CleanupSignalStatus.FAILED,
                        detail=f"killpg={_exception_diagnostic(exc)}",
                    )
                else:
                    outcome = ProcessCleanupOutcome(CleanupSignalStatus.SIGNALED)

        outcome = self._signal_direct_child(process, outcome)
        if cancellation is not None:
            # Even kill() callers must not skip the target fallback/wait when a
            # cancellation interrupts helper communication. No second taskkill.
            try:
                outcome = await self._wait_direct_child(process, outcome)
            except Exception as exc:
                outcome = outcome.with_direct_child(
                    DirectChildStatus.WAIT_FAILED, _exception_diagnostic(exc)
                )
            logger.warning(
                "PROCESS_ASYNC_CLEANUP_FAILED pid=%s reason=taskkill_cancelled outcome=%s",
                process.pid, outcome.summary(),
            )
            raise cancellation
        return outcome

    @staticmethod
    def _signal_direct_child(process, outcome):
        # Keep the existing best-effort direct-child signal, but never let it turn
        # a failed tree signal into success or imply descendant termination.
        try:
            process.kill()
        except ProcessLookupError:
            detail = "direct_child_already_exited"
        except Exception as exc:
            detail = f"direct_child_kill={_exception_diagnostic(exc)}"
            # A failed fallback is still a failed termination attempt. Put this
            # diagnostic first so helper output cannot hide it by truncation.
            return ProcessCleanupOutcome(
                CleanupSignalStatus.FAILED,
                detail=f"{detail}; tree_signal={outcome.signal_status.value}; {outcome.detail}",
            )
        else:
            detail = "direct_child_signal_attempted"

        if detail:
            outcome = ProcessCleanupOutcome(
                signal_status=outcome.signal_status,
                detail="; ".join(part for part in (outcome.detail, detail) if part)[
                    :_CLEANUP_DIAGNOSTIC_LIMIT
                ],
            )
        return outcome

    async def _finalize_taskkill_helper(self, helper: _TaskkillHelper) -> str:
        """Explicitly close pipes/transport and bound callback confirmation."""
        details = []
        helper.close_requested = True
        for fd, pipe in helper.pipes.items():
            try:
                pipe.close()
            except Exception as exc:
                details.append(f"helper_pipe_close_{fd}={_exception_diagnostic(exc)}")
        try:
            helper.transport.close()
        except Exception as exc:
            details.append(f"helper_transport_close={_exception_diagnostic(exc)}")
        # A running unresolved child cannot supply connection_lost yet. Still
        # confirm pipe closure independently, rather than abandoning its pipes.
        async def confirmation():
            await asyncio.shield(helper.pipe_eof)
            if helper.exit.done() and helper.returncode is not None:
                await asyncio.shield(helper.closed)
        try:
            await asyncio.wait_for(confirmation(), timeout=_HELPER_PIPE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            details.append("helper_pipe_finalize_timeout")
        except Exception as exc:
            details.append(f"helper_pipe_finalize={_exception_diagnostic(exc)}")
        finally:
            if details:
                helper.pipe_error = "; ".join(details)[:_CLEANUP_DIAGNOSTIC_LIMIT]
            helper._release_if_closed()
            if not helper.resources_closed:
                helper.state = "unresolved"
            helper.detail = "; ".join([
                f"helper_reaped={str(helper.exit.done() and helper.returncode is not None).lower()}",
                f"helper_finalized={str(helper.resources_closed and not helper.pipe_error).lower()}",
                helper.pipe_error,
            ]).rstrip("; ")[:_CLEANUP_DIAGNOSTIC_LIMIT]
            if not helper.resources_closed or helper.pipe_error:
                logger.warning("TASKKILL_HELPER_UNRESOLVED pid=%s target_pid=%s state=%s detail=%s",
                               helper.pid, helper.target_pid, helper.state, helper.detail)
        return helper.detail

    async def _stop_taskkill_helper(self, helper: _TaskkillHelper) -> str:
        """Serialize termination/reaping and always finalize owned pipes."""
        async with helper.cleanup_lock:
            if helper.resources_closed:
                return helper.detail
            helper.state = "cleaning"
            details = []
            try:
                for action in ("terminate", "kill"):
                    if helper.exit.done() and helper.returncode is not None:
                        break
                    try:
                        getattr(helper, action)()
                    except ProcessLookupError:
                        details.append("helper_already_exited")
                    except Exception as exc:
                        details.append(f"helper_{action}={_exception_diagnostic(exc)}")
                    try:
                        await asyncio.wait_for(helper.wait(), timeout=_HELPER_REAP_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        details.append("helper_reap_timeout")
                    except Exception as exc:
                        details.append(f"helper_reap={_exception_diagnostic(exc)}")
                    else:
                        if helper.returncode is not None:
                            break
                        details.append("helper_wait_returned_without_returncode")
            finally:
                finalization = await self._finalize_taskkill_helper(helper)
                helper.detail = "; ".join([finalization, *details])[:_CLEANUP_DIAGNOSTIC_LIMIT]
            return helper.detail

    def taskkill_helpers(self) -> tuple[_TaskkillHelper, ...]:
        """Inspect owned helper handles, target association and unresolved state."""
        return tuple(self._taskkill_helpers.values())

    async def retry_taskkill_helpers(self, *, timeout_seconds: float = 3.0) -> None:
        """Bounded manager/shutdown retry; never discard unresolved ownership.

        The overall budget includes lock contention and all helpers. Cancellation
        can add at most one pipe-finalization budget before it propagates.
        """
        async def retry():
            for helper in self.taskkill_helpers():
                if helper.state == "unresolved":
                    await self._stop_taskkill_helper(helper)
        try:
            await asyncio.wait_for(retry(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("TASKKILL_HELPER_RETRY_TIMEOUT retained=%s", len(self._taskkill_helpers))

    async def _kill_tree_and_wait(
        self, process: asyncio.subprocess.Process
    ) -> ProcessCleanupOutcome:
        """Kill the process tree, then await its exit so the OS reaps it.

        Used by the timeout and cancellation paths. Safe when the process has
        already exited: the kill calls no-op and ``process.wait()`` returns the
        cached return code immediately. The bounded timeout guarantees cleanup
        can never hang the cancelling caller indefinitely.
        """
        if process.returncode is not None:
            return ProcessCleanupOutcome(
                CleanupSignalStatus.ALREADY_EXITED,
                DirectChildStatus.ALREADY_EXITED,
            )

        outcome = await self._kill_tree(process)
        return await self._wait_direct_child(process, outcome)

    async def _wait_direct_child(self, process, outcome):
        try:
            if process.returncode is None:
                await asyncio.wait_for(
                    process.wait(), timeout=_DIRECT_CHILD_WAIT_TIMEOUT_SECONDS
                )
        except asyncio.TimeoutError:
            return outcome.with_direct_child(
                DirectChildStatus.WAIT_TIMEOUT,
                "direct_child_wait_timeout",
            )
        except Exception as exc:
            return outcome.with_direct_child(
                DirectChildStatus.WAIT_FAILED,
                f"direct_child_wait={_exception_diagnostic(exc)} "
                f"direct_child_returncode={process.returncode}",
            )

        if process.returncode is None:
            return outcome.with_direct_child(
                DirectChildStatus.WAIT_FAILED,
                "direct_child_wait_returned_without_returncode",
            )
        return outcome.with_direct_child(DirectChildStatus.EXITED)

    async def kill(self, process: asyncio.subprocess.Process) -> ProcessCleanupOutcome:
        """Public entry point to kill a process tree (used by interactive sessions)."""
        outcome = await self._kill_tree(process)
        if outcome.failed:
            logger.warning(
                "PROCESS_ASYNC_CLEANUP_FAILED pid=%s reason=kill outcome=%s",
                process.pid, outcome.summary(),
            )
        return outcome

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
        cleanup_failure: Optional[str] = None
        try:
            if _IS_WINDOWS:
                import subprocess
                try:
                    completed = subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                    )
                except subprocess.TimeoutExpired:
                    cleanup_failure = "taskkill_timeout"
                else:
                    if completed.returncode == 0:
                        terminated = True
                    else:
                        cleanup_failure = f"taskkill_exit_status_{completed.returncode}"
            else:
                import signal
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    terminated = True
                except ProcessLookupError:
                    # The original process is already gone or unavailable. Never
                    # fall back to signaling the raw PID: it may have been reused
                    # by an unrelated process after the group lookup failed.
                    already_gone = True
        except Exception as exc:  # never let cleanup crash the resume path
            cleanup_failure = type(exc).__name__
        finally:
            if terminated:
                managed.state = "killed"
            elif already_gone:
                # Reuse the established terminal state for a process that completed
                # before synchronous cleanup could signal its process group.
                managed.state = "done"
            else:
                managed.state = "cleanup_failed"
            self._active.pop(pid, None)

        if terminated:
            logger.info("PROCESS_TERMINATED pid=%s execution_id=%s", pid, managed.execution_id or "-")
        elif already_gone:
            logger.info("PROCESS_ALREADY_EXITED pid=%s execution_id=%s", pid, managed.execution_id or "-")
        else:
            logger.warning(
                "PROCESS_CLEANUP_FAILED pid=%s execution_id=%s reason=%s failure=%s",
                pid, managed.execution_id or "-", reason, cleanup_failure or "unknown",
            )
        return True

    # ------------------------------------------------------------------ #

    def active_count(self) -> int:
        return len(self._active)

    def active_pids(self) -> list:
        return list(self._active.keys())


# Module-level singleton.
process_manager = ProcessManager()
