"""
ExecutionService — central coordinator of the Phase 3 execution layer.

The intelligence layer calls execution_service.execute(request) and receives
an ExecutionResult without ever knowing about Windows/Linux/Docker specifics.

Backend selection:
    Currently there is one backend: LocalBackend (handles both Windows and Linux
    by delegating platform differences to ProcessManager + _normalise_command).
    Future backends (DockerBackend, SSHLinuxBackend, VMwareLinuxBackend) can be
    registered here without touching AgentRuntime, ToolManager, or any other
    intelligence-layer code.

ToolManager integration:
    ToolManager calls execution_service.run_raw() and execution_service.run_command()
    which wrap the same backend execute() path.  This removes subprocess ownership
    from ToolManager without changing its public API.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.execution.base import ExecutionRequest, ExecutionResult
from backend.execution.backends.local import LocalBackend

logger = logging.getLogger("forge.execution.service")


class ExecutionService:
    """
    Routes execution requests to the appropriate backend.

    Usage:
        from backend.execution.service import execution_service
        result = await execution_service.execute(request)
    """

    def __init__(self) -> None:
        self._backends: dict[str, object] = {}
        self._default_backend_kind = "local"
        self._register_defaults()

    # ------------------------------------------------------------------ #

    def _register_defaults(self) -> None:
        self._backends["local"] = LocalBackend()

    def register_backend(self, kind: str, backend: object) -> None:
        """Register an additional backend (e.g. docker, ssh_linux)."""
        self._backends[kind] = backend
        logger.info(f"[ExecutionService] registered backend: {kind}")

    def get_backend(self, kind: Optional[str] = None) -> object:
        """Return the backend for *kind* or the default backend."""
        k = kind or self._default_backend_kind
        backend = self._backends.get(k)
        if backend is None:
            logger.warning(
                f"[ExecutionService] unknown backend '{k}', falling back to local"
            )
            backend = self._backends["local"]
        return backend

    def list_backends(self) -> dict:
        """Return the registered backend kinds and the default (for status/diagnostics)."""
        return {
            "default": self._default_backend_kind,
            "registered": list(self._backends.keys()),
        }

    # ------------------------------------------------------------------ #

    async def execute(
        self,
        request: ExecutionRequest,
        *,
        backend_kind: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute *request* through the selected backend.

        backend_kind selects which registered backend to use.  Pass None (or
        omit) to use the default local backend.  The local backend already
        handles both Windows and Linux.
        """
        backend = self.get_backend(backend_kind)
        result: ExecutionResult = await backend.execute(request)
        return result

    # ------------------------------------------------------------------ #
    # Convenience helpers used by the refactored ToolManager so it can keep
    # its existing public API while delegating actual process spawning here.
    # ------------------------------------------------------------------ #

    async def run_command(
        self,
        command: str,
        *,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        capability: str = "custom_command",
        tool_name: str = "",
        session_id: str = "",
        agent_id: str = "",
        canonical_target: Optional[str] = None,
    ) -> ExecutionResult:
        """Run an arbitrary shell command and return an ExecutionResult."""
        request = ExecutionRequest(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            capability=capability,
            tool_name=tool_name,
            session_id=session_id,
            agent_id=agent_id,
            canonical_target=canonical_target,
        )
        return await self.execute(request)


# Module-level singleton.
execution_service = ExecutionService()
