"""
Persistent interactive execution (Phase 4.x §5–§8).

The Phase-3 execution layer runs one-shot commands: spawn → communicate → exit.
Many CTF challenges instead speak a *dialogue* — a local binary or ``nc`` service
that prints a prompt, waits for input, prints more, and so on.  Driving that as a
one-shot ``printf '...' | prog`` pipeline is brittle and cannot react to what the
program actually says.

This module adds a proper persistent-process abstraction that lives ABOVE the same
:class:`~backend.execution.process_manager.ProcessManager` used by the one-shot
path (so all PIDs remain visible in one place and the compliance audit still sees
them).  It is fully asynchronous and concurrency-safe: reads never block the event
loop, so a stuck challenge process cannot freeze the swarm.

Design invariants
-----------------
* An :class:`InteractiveSession` owns exactly one OS process and its I/O.
* Reads are bounded by an *idle timeout* (quiet gap ⇒ return what we have) and an
  overall timeout; the whole session is bounded by a *max lifetime* after which it
  is force-terminated and cleaned up — never left running (clean-lifecycle rule).
* Live OS handles (``Process``/PTY fds) are NEVER serialized.  A checkpoint stores
  only an :class:`InteractiveSessionSpec` (restartable metadata); on resume the
  live process is considered stale and must be restarted (§8).
* Windows uses async pipes; POSIX may optionally use a PTY (``use_pty=True``) and
  falls back to pipes if PTY allocation fails, so the same code path works on the
  Windows dev host and the Linux execution VM.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from backend.execution.process_manager import ManagedProcess, process_manager

logger = logging.getLogger("forge.execution.interactive")

_IS_WINDOWS = sys.platform == "win32"

# ── Read/session outcome tokens ─────────────────────────────────────────────── #
IO_OK = "OK"
IO_TIMEOUT = "TIMEOUT"        # idle/overall read window elapsed with no (more) data
IO_EOF = "EOF"               # process closed its output stream
IO_MATCHED = "MATCHED"       # an `until` marker was seen
IO_CLOSED = "CLOSED"         # session already closed
IO_ERROR = "ERROR"

# ── Trajectory event names (recorded via an injected recorder; §24) ─────────── #
EVENT_START = "INTERACTIVE_START"
EVENT_SEND = "INTERACTIVE_SEND"
EVENT_READ = "INTERACTIVE_READ"
EVENT_TIMEOUT = "INTERACTIVE_TIMEOUT"
EVENT_CLOSE = "INTERACTIVE_CLOSE"

_DEFAULT_IDLE_TIMEOUT = 2.0
_DEFAULT_READ_TIMEOUT = 30.0
_DEFAULT_MAX_LIFETIME = 300.0
_READ_CHUNK = 4096
_TRANSCRIPT_CAP = 200          # bounded transcript entries kept in memory


def _normalise_command(command: str) -> str:
    """Rewrite ``python3``/``python`` to the binary that actually exists here.

    Mirrors LocalBackend._normalise_command so interactive processes start on the
    Windows dev host without manual PATH tweaks. Kept local (no import cycle).
    """
    stripped = (command or "").strip()
    if not stripped:
        return stripped
    parts = stripped.split(None, 1)
    first = parts[0]
    if first in ("python3", "python"):
        try:
            from backend.execution.backends.local import _resolve_python
            resolved = _resolve_python()
        except Exception:
            resolved = None
        if resolved and resolved != first:
            rest = parts[1] if len(parts) > 1 else ""
            return f"{resolved} {rest}".strip()
    return stripped


@dataclass
class ReadResult:
    """Structured result of one :meth:`InteractiveSession.read`."""
    data: str = ""
    status: str = IO_OK
    matched: bool = False
    eof: bool = False
    timed_out: bool = False
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in (IO_OK, IO_MATCHED)


@dataclass
class InteractiveSessionSpec:
    """Serializable, checkpoint-safe description of an interactive session (§8).

    This is the ONLY representation of an interactive session that may be written
    to mission state / checkpoints — it contains no OS handle, so a resumed mission
    treats the process as stale and restarts it rather than resurrecting a dead
    file descriptor.
    """
    session_key: str
    command: str
    cwd: Optional[str] = None
    target: str = ""
    env_keys: List[str] = field(default_factory=list)   # names only, never values
    owner_session_id: str = ""
    owner_agent_id: str = ""
    use_pty: bool = False
    created_at: float = 0.0
    last_interaction_at: float = 0.0
    transcript_tail: str = ""
    state: str = "restartable"       # restartable | closed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_key": self.session_key, "command": self.command, "cwd": self.cwd,
            "target": self.target, "env_keys": list(self.env_keys),
            "owner_session_id": self.owner_session_id, "owner_agent_id": self.owner_agent_id,
            "use_pty": self.use_pty, "created_at": self.created_at,
            "last_interaction_at": self.last_interaction_at,
            "transcript_tail": self.transcript_tail, "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InteractiveSessionSpec":
        data = data or {}
        return cls(
            session_key=data.get("session_key") or str(uuid.uuid4()),
            command=data.get("command", ""), cwd=data.get("cwd"),
            target=data.get("target", ""), env_keys=list(data.get("env_keys", []) or []),
            owner_session_id=data.get("owner_session_id", ""),
            owner_agent_id=data.get("owner_agent_id", ""),
            use_pty=bool(data.get("use_pty", False)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_interaction_at=float(data.get("last_interaction_at", 0.0) or 0.0),
            transcript_tail=data.get("transcript_tail", ""),
            state=data.get("state", "restartable"),
        )


class InteractiveSession:
    """One persistent interactive process with async ``start/read/send/close``."""

    def __init__(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        session_id: str = "",
        agent_id: str = "",
        target: str = "",
        idle_timeout: float = _DEFAULT_IDLE_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
        max_lifetime: float = _DEFAULT_MAX_LIFETIME,
        use_pty: bool = False,
        recorder: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        session_key: Optional[str] = None,
        on_close: Optional[Callable[[str], None]] = None,
    ):
        self.session_key = session_key or str(uuid.uuid4())
        self.command = command
        self.cwd = cwd if (cwd and os.path.isdir(cwd)) else None
        self.env = env
        self.owner_session_id = session_id
        self.owner_agent_id = agent_id
        self.target = target
        self.idle_timeout = float(idle_timeout)
        self.read_timeout = float(read_timeout)
        self.max_lifetime = float(max_lifetime)
        self.use_pty = bool(use_pty) and not _IS_WINDOWS
        self._recorder = recorder
        self._on_close = on_close

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pty_master: Optional[int] = None
        self._pty_reader: Optional[asyncio.StreamReader] = None
        self._pty_transport: Optional[Any] = None
        self._closed = False
        self._started = False
        self._lifetime_exceeded = False
        self.created_at = 0.0
        self.last_interaction_at = 0.0
        self._transcript: Deque[Tuple[str, str]] = deque(maxlen=_TRANSCRIPT_CAP)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> "InteractiveSession":
        """Spawn the process. Idempotent (a second call is a no-op)."""
        if self._started:
            return self
        self._started = True
        command = _normalise_command(self.command)
        merged_env = {**os.environ, **self.env} if self.env else None

        if self.use_pty:
            try:
                await self._start_pty(command, merged_env)
            except Exception as exc:  # PTY not available → portable pipe fallback
                logger.info(f"[InteractiveSession] PTY unavailable ({exc}); using pipes.")
                self.use_pty = False
                await self._start_pipe(command, merged_env)
        else:
            await self._start_pipe(command, merged_env)

        self.created_at = time.time()
        self.last_interaction_at = self.created_at
        if self._proc is not None:
            process_manager.register(ManagedProcess(
                pid=self._proc.pid, command=command, session_id=self.owner_session_id,
                agent_id=self.owner_agent_id, backend="interactive",
                timeout_seconds=int(self.max_lifetime), state="running"))
        self._emit(EVENT_START, {"command": command, "pid": self.pid, "pty": self.use_pty})
        logger.info(f"[InteractiveSession {self.session_key[:8]}] started pid={self.pid} pty={self.use_pty}")
        return self

    async def _start_pipe(self, command: str, env: Optional[Dict[str, str]]) -> None:
        kwargs: dict = dict(
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,   # merge for a single dialogue stream
        )
        if self.cwd:
            kwargs["cwd"] = self.cwd
        if env:
            kwargs["env"] = env
        if not _IS_WINDOWS:
            kwargs["preexec_fn"] = os.setsid  # type: ignore[attr-defined]
        self._proc = await asyncio.create_subprocess_shell(command, **kwargs)

    async def _start_pty(self, command: str, env: Optional[Dict[str, str]]) -> None:
        """POSIX PTY path (best-effort). Falls back to pipes on any failure."""
        import pty  # POSIX-only
        master, slave = pty.openpty()
        kwargs: dict = dict(stdin=slave, stdout=slave, stderr=slave,
                            preexec_fn=os.setsid)  # type: ignore[attr-defined]
        if self.cwd:
            kwargs["cwd"] = self.cwd
        if env:
            kwargs["env"] = env
        try:
            self._proc = await asyncio.create_subprocess_shell(command, **kwargs)
        finally:
            os.close(slave)
        loop = asyncio.get_event_loop()
        self._pty_master = master
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        self._pty_transport, _ = await loop.connect_read_pipe(
            lambda: protocol, os.fdopen(master, "rb", 0))
        self._pty_reader = reader

    # ------------------------------------------------------------------ #
    # I/O
    # ------------------------------------------------------------------ #

    @property
    def _stdout(self) -> Optional[asyncio.StreamReader]:
        if self.use_pty:
            return self._pty_reader
        return self._proc.stdout if self._proc else None

    async def read(
        self,
        *,
        until: Optional[str] = None,
        timeout: Optional[float] = None,
        idle_timeout: Optional[float] = None,
        max_bytes: int = 262_144,
    ) -> ReadResult:
        """Read output until an ``until`` marker, EOF, an idle gap, or *timeout*.

        An idle gap AFTER receiving data is normal for an interactive program that
        printed a prompt and is now waiting for us — it returns the accumulated
        text with ``timed_out=False``.  Only a window that yields NO data at all is
        reported as ``timed_out=True``.
        """
        async with self._lock:
            if self._closed:
                return ReadResult(status=IO_CLOSED)
            if await self._enforce_lifetime():
                return ReadResult(status=IO_TIMEOUT, timed_out=True)
            reader = self._stdout
            if reader is None:
                return ReadResult(status=IO_ERROR)

            t_overall = self.read_timeout if timeout is None else float(timeout)
            t_idle = self.idle_timeout if idle_timeout is None else float(idle_timeout)
            loop = asyncio.get_event_loop()
            start = loop.time()
            deadline = start + t_overall
            chunks: List[bytes] = []
            total = 0
            matched = eof = timed_out = False

            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    timed_out = not chunks
                    break
                slice_to = min(t_idle, remaining)
                try:
                    chunk = await asyncio.wait_for(reader.read(_READ_CHUNK), timeout=slice_to)
                except asyncio.TimeoutError:
                    timed_out = not chunks     # quiet gap: only a timeout if nothing arrived
                    break
                except Exception as exc:
                    logger.debug(f"[InteractiveSession] read error: {exc}")
                    eof = True
                    break
                if chunk == b"":
                    eof = True
                    break
                chunks.append(chunk)
                total += len(chunk)
                if until and until in b"".join(chunks).decode(errors="replace"):
                    matched = True
                    break
                if total >= max_bytes:
                    break

            data = b"".join(chunks).decode(errors="replace")
            elapsed = (loop.time() - start) * 1000.0
            status = IO_MATCHED if matched else (IO_EOF if eof else (IO_TIMEOUT if timed_out else IO_OK))
            if data:
                self.last_interaction_at = time.time()
                self._transcript.append(("read", data))
            self._emit(EVENT_READ, {"bytes": len(data), "status": status,
                                    "preview": data[:200]})
            return ReadResult(data=data, status=status, matched=matched, eof=eof,
                              timed_out=timed_out, elapsed_ms=elapsed)

    async def send(self, data: str, *, add_newline: bool = True) -> bool:
        """Write *data* to the process's stdin. Returns False if it cannot be sent."""
        async with self._lock:
            if self._closed or not self.is_alive():
                return False
            if await self._enforce_lifetime():
                return False
            payload = data + ("\n" if add_newline and not data.endswith("\n") else "")
            raw = payload.encode(errors="replace")
            try:
                if self.use_pty and self._pty_master is not None:
                    os.write(self._pty_master, raw)
                elif self._proc and self._proc.stdin:
                    self._proc.stdin.write(raw)
                    await self._proc.stdin.drain()
                else:
                    return False
            except Exception as exc:
                logger.debug(f"[InteractiveSession] send error: {exc}")
                return False
            self.last_interaction_at = time.time()
            self._transcript.append(("send", payload))
            self._emit(EVENT_SEND, {"data": payload[:200]})
            return True

    async def send_and_read(self, data: str, *, until: Optional[str] = None,
                            add_newline: bool = True, timeout: Optional[float] = None) -> ReadResult:
        """Convenience: send input then read the response."""
        sent = await self.send(data, add_newline=add_newline)
        if not sent:
            return ReadResult(status=IO_CLOSED)
        return await self.read(until=until, timeout=timeout)

    # ------------------------------------------------------------------ #
    # Termination / cleanup
    # ------------------------------------------------------------------ #

    def is_alive(self) -> bool:
        return bool(self._proc) and self._proc.returncode is None and not self._closed

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> Optional[int]:
        return self._proc.returncode if self._proc else None

    async def terminate(self) -> None:
        """Graceful stop, then force-kill the tree; always cleans up."""
        await self.close(reason="terminate")

    async def kill(self) -> None:
        await self.close(reason="kill")

    async def close(self, *, reason: str = "close") -> None:
        """Terminate the process, release all handles, and unregister it. Idempotent."""
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is not None:
            try:
                if proc.returncode is None:
                    await process_manager.kill(proc)
            except Exception:
                pass
            # Reap the child so it cannot linger as a zombie and its returncode is set
            # (clean-lifecycle rule: no orphaned challenge processes left behind).
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                pass
            process_manager.unregister(proc.pid)
        # Release PTY resources.
        try:
            if self._pty_transport is not None:
                self._pty_transport.close()
        except Exception:
            pass
        try:
            if self._pty_master is not None:
                os.close(self._pty_master)
        except Exception:
            pass
        self._emit(EVENT_CLOSE, {"reason": reason, "returncode": self.returncode})
        logger.info(f"[InteractiveSession {self.session_key[:8]}] closed ({reason})")
        if self._on_close is not None:
            try:
                self._on_close(self.session_key)
            except Exception:
                pass

    async def _enforce_lifetime(self) -> bool:
        """If the session exceeded its max lifetime, terminate + record. Returns True then."""
        if self.max_lifetime and self.created_at and (time.time() - self.created_at) > self.max_lifetime:
            if not self._lifetime_exceeded:
                self._lifetime_exceeded = True
                elapsed = time.time() - self.created_at
                self._emit(EVENT_TIMEOUT, {"elapsed_s": round(elapsed, 2), "cleanup": "successful"})
                logger.warning(
                    f"[InteractiveSession {self.session_key[:8]}] max lifetime "
                    f"{self.max_lifetime}s exceeded — terminating.")
                await self.close(reason="lifetime_exceeded")
            return True
        return False

    # ------------------------------------------------------------------ #
    # Observability / checkpoint-safety
    # ------------------------------------------------------------------ #

    def transcript(self) -> List[Tuple[str, str]]:
        return list(self._transcript)

    def transcript_tail(self, limit: int = 400) -> str:
        parts = [f"[{d}] {t}" for d, t in self._transcript]
        return ("\n".join(parts))[-limit:]

    def spec(self) -> InteractiveSessionSpec:
        """Return the checkpoint-safe, serializable description (NO live handle, §8)."""
        return InteractiveSessionSpec(
            session_key=self.session_key, command=self.command, cwd=self.cwd,
            target=self.target, env_keys=sorted((self.env or {}).keys()),
            owner_session_id=self.owner_session_id, owner_agent_id=self.owner_agent_id,
            use_pty=self.use_pty, created_at=self.created_at,
            last_interaction_at=self.last_interaction_at,
            transcript_tail=self.transcript_tail(),
            state="closed" if self._closed else "restartable",
        )

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self._recorder:
            return
        try:
            self._recorder(event_type, {
                "session_key": self.session_key, "owner_session_id": self.owner_session_id,
                "owner_agent_id": self.owner_agent_id, **payload})
        except Exception:
            pass


class InteractiveSessionManager:
    """Registry + lifecycle owner for all live interactive sessions.

    A single manager keeps the process count bounded (a runaway swarm cannot spawn
    unlimited interactive shells) and guarantees no session outlives the process —
    :meth:`close_all` is the clean-lifecycle hook for shutdown/audit.
    """

    def __init__(self, *, max_sessions: int = 16):
        self.max_sessions = max_sessions
        self._sessions: Dict[str, InteractiveSession] = {}

    async def open(self, command: str, **kwargs: Any) -> InteractiveSession:
        # Drop already-dead sessions before enforcing the cap.
        self._reap()
        if len(self._sessions) >= self.max_sessions:
            raise RuntimeError(
                f"Interactive session limit reached ({self.max_sessions}). "
                f"Close an existing session before opening another.")
        sess = InteractiveSession(command, **kwargs)
        # Self-removal from the registry when the session closes (keeps count() honest).
        sess._on_close = lambda key: self._sessions.pop(key, None)
        await sess.start()
        self._sessions[sess.session_key] = sess
        return sess

    def get(self, session_key: str) -> Optional[InteractiveSession]:
        return self._sessions.get(session_key)

    def all(self) -> List[InteractiveSession]:
        return list(self._sessions.values())

    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.is_alive())

    def count(self) -> int:
        return len(self._sessions)

    async def close(self, session_key: str, *, reason: str = "close") -> bool:
        sess = self._sessions.pop(session_key, None)
        if sess is None:
            return False
        await sess.close(reason=reason)
        return True

    async def close_all(self, *, reason: str = "shutdown") -> int:
        n = 0
        for sess in list(self._sessions.values()):
            try:
                await sess.close(reason=reason)
                n += 1
            except Exception:
                pass
        self._sessions.clear()
        return n

    def snapshot_specs(self) -> List[Dict[str, Any]]:
        """Checkpoint-safe specs for every session (§8) — never any live handle."""
        return [s.spec().to_dict() for s in self._sessions.values()]

    def mark_all_stale(self) -> List[InteractiveSessionSpec]:
        """On resume, forget live handles and return restartable specs (§8).

        Any process that was running before the interruption is gone (its handle
        did not survive); we return its spec marked ``restartable`` so the caller
        can decide whether to restart it, and clear the in-memory registry.
        """
        specs: List[InteractiveSessionSpec] = []
        for s in self._sessions.values():
            sp = s.spec()
            sp.state = "restartable"
            specs.append(sp)
        self._sessions.clear()
        return specs

    def _reap(self) -> None:
        dead = [k for k, s in self._sessions.items() if not s.is_alive()]
        for k in dead:
            self._sessions.pop(k, None)


# Module-level singleton — one place all interactive sessions are tracked.
interactive_manager = InteractiveSessionManager()
