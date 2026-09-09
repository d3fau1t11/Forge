"""
FORGE Agent Runtime — Execution Backend capability oracle (Step 19, Step 12).

FORGE's *intelligence* (memory, runtime, planning, learning) must be OS-independent:
it is developed and run on Windows, while the security tooling that actually executes
commands lives on Linux (Parrot/Kali) or, later, a Docker container.

This module draws the line between the two:

    FORGE INTELLIGENCE            EXECUTION ENVIRONMENT
    (OS-independent)      ──►     (reports its own capabilities)

An :class:`ExecutionBackend` does NOT decide *what* to do and does NOT own the
command runner (that stays with ``tools.manager`` / ``RealToolExecutor``). Its sole
Phase-2 responsibility is to **report the capabilities available where commands run**
— the OS, the installed CLI tools, and the importable Python libraries — so that
environment-aware skills (Step 12) can be filtered and ranked against what is
actually runnable *here* instead of assuming ``/usr/bin``, ``apt`` and ``bash`` exist.

We deliberately implement only the backend needed now: a :class:`LocalExecutionBackend`
that detects the host it is running on. ``WindowsLocalBackend`` / ``LinuxLocalBackend``
/ ``DockerBackend`` are not separate classes yet — the local backend already reports
the correct capabilities on whichever OS FORGE is started on. The important invariant
is the *interface*: the rest of the system asks a backend "can you run X?" and never
hard-codes an OS assumption.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger("forge.agent_runtime.execution_backend")

# Canonical OS tokens used throughout the skill/environment layer.
OS_ANY = "any"
OS_LINUX = "linux"
OS_WINDOWS = "windows"
OS_DARWIN = "darwin"


@dataclass
class CapabilityReport:
    """A snapshot of what the execution environment can actually run.

    Everything here is derived from the *execution host*, never assumed. The
    intelligence layer treats this as advisory evidence: a skill that requires a
    tool absent from ``available_tools`` is de-prioritised, not silently executed.
    """

    os: str = OS_ANY                       # "linux" | "windows" | "darwin" | "any"
    distro: str = ""
    architecture: str = ""
    backend_kind: str = "local"            # local | docker | remote (future)
    available_tools: List[str] = field(default_factory=list)   # installed CLI tools
    available_python_libs: List[str] = field(default_factory=list)
    is_kali_linux: bool = False
    is_parrot_os: bool = False
    cpu_cores: int = 1
    ram_gb: float = 0.0

    # ------------------------------------------------------------------ #

    def has_tool(self, name: str) -> bool:
        if not name:
            return False
        return name.strip().lower() in {t.lower() for t in self.available_tools}

    def has_python_lib(self, name: str) -> bool:
        if not name:
            return False
        return name.strip().lower() in {l.lower() for l in self.available_python_libs}

    def satisfies_os(self, required_os: Optional[str]) -> bool:
        """True when a skill's required_os is compatible with this environment."""
        req = (required_os or OS_ANY).strip().lower()
        return req in ("", OS_ANY) or req == (self.os or OS_ANY).lower()

    def missing_tools(self, required_tools: Optional[List[str]]) -> List[str]:
        """Tools a skill needs that are NOT available here (empty ⇒ fully runnable)."""
        return [t for t in (required_tools or []) if t and not self.has_tool(t)]

    def satisfies(self, environment: Optional[Dict[str, Any]]) -> bool:
        """True when this environment meets a skill's declared requirements (Step 12)."""
        if not environment:
            return True
        if not self.satisfies_os(environment.get("required_os")):
            return False
        return not self.missing_tools(environment.get("tools") or environment.get("required_tools"))

    def fit_score(self, environment: Optional[Dict[str, Any]]) -> float:
        """A 0..1 environment-fit score for ranking (Step 8).

        1.0 = fully runnable here; lower when the OS mismatches or required tools
        are missing. Never 0 (a skill can still be adapted / the tool installed).
        """
        if not environment:
            return 1.0
        score = 1.0
        if not self.satisfies_os(environment.get("required_os")):
            score *= 0.5
        req_tools = environment.get("tools") or environment.get("required_tools") or []
        if req_tools:
            missing = self.missing_tools(req_tools)
            present = len(req_tools) - len(missing)
            score *= (0.4 + 0.6 * (present / max(1, len(req_tools))))
        return round(max(0.2, min(1.0, score)), 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "os": self.os, "distro": self.distro, "architecture": self.architecture,
            "backend_kind": self.backend_kind, "available_tools": self.available_tools,
            "available_python_libs": self.available_python_libs,
            "is_kali_linux": self.is_kali_linux, "is_parrot_os": self.is_parrot_os,
            "cpu_cores": self.cpu_cores, "ram_gb": self.ram_gb,
        }

    def tool_inventory_str(self) -> str:
        return ", ".join(self.available_tools) if self.available_tools else ""

    def python_libs_str(self) -> str:
        return ", ".join(self.available_python_libs) if self.available_python_libs else ""


@runtime_checkable
class ExecutionBackend(Protocol):
    """Where commands actually run. Phase 2 only needs it to report capabilities."""

    def capabilities(self) -> CapabilityReport:
        ...


class LocalExecutionBackend:
    """Reports the capabilities of the local host (Windows OR Linux OR macOS).

    Backed by the existing ``environment_detector`` so there is a single source of
    truth for "what is installed here". The result is cached because tool discovery
    (``shutil.which`` across the tool list) is mildly expensive; call
    :meth:`refresh` after installing a tool mid-mission.
    """

    def __init__(self, detector: Any = None):
        self._detector = detector
        self._cache: Optional[CapabilityReport] = None

    def _detect(self) -> Dict[str, Any]:
        det = self._detector
        if det is None:
            from backend.environment.detector import environment_detector
            det = environment_detector
        return det.detect_environment()

    def capabilities(self) -> CapabilityReport:
        if self._cache is not None:
            return self._cache
        try:
            env = self._detect()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"[LocalExecutionBackend] detection failed, reporting minimal: {e}")
            self._cache = CapabilityReport(os=OS_ANY)
            return self._cache

        tools_map = env.get("installed_tools", {}) or {}
        available_tools = [name for name, info in tools_map.items()
                           if (info.get("installed") if isinstance(info, dict) else bool(info))]
        libs_map = env.get("installed_python_libs", {}) or {}
        available_libs = [name for name, ok in libs_map.items() if ok]

        self._cache = CapabilityReport(
            os=(env.get("os") or OS_ANY).lower(),
            distro=env.get("distro", "") or "",
            architecture=env.get("architecture", "") or "",
            backend_kind="local",
            available_tools=available_tools,
            available_python_libs=available_libs,
            is_kali_linux=bool(env.get("is_kali_linux")),
            is_parrot_os=bool(env.get("is_parrot_os")),
            cpu_cores=int(env.get("cpu_cores", 1) or 1),
            ram_gb=float(env.get("ram_gb", 0.0) or 0.0),
        )
        return self._cache

    def refresh(self) -> CapabilityReport:
        """Re-detect (e.g. after a mid-mission tool install)."""
        self._cache = None
        return self.capabilities()


# Module singleton — the default capability oracle for the whole intelligence layer.
execution_backend: ExecutionBackend = LocalExecutionBackend()
