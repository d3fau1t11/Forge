"""
Controlled capability acquisition (Phase 4.x §17–§18).

When a capability is unavailable AND no installed alternative exists, FORGE may
*request* to acquire it — but it must NEVER become an unrestricted autonomous
package installer.  Acquisition here is:

* **Planned, not assumed.**  We never assume ``apt``/root/internet/Docker exist.
  The plan is derived from what the platform actually offers, preferring the least
  invasive method:  already-installed → workspace-local → user-level (``pip
  --user``) → controlled system-level (a detected package manager).
* **Privilege-gated.**  Every acquisition routes its *whether* decision through the
  existing :class:`~backend.privilege.manager.PrivilegeManager`, which writes an
  audit-log row.  PRIVILEGED/DANGEROUS acquisitions are not approved automatically,
  so nothing mutates the host without an operator decision.
* **Auditable.**  Approved acquisitions run through the same ExecutionService as
  every other command; the attempt, decision, and outcome are all recorded.

This module plans and (only when approved) performs acquisition; it decides nothing
about *what* capability is needed — that is the agent's call, surfaced via
:mod:`backend.execution.capabilities`.
"""
from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional

from backend.execution.capabilities import ProviderKind, ProviderSpec

logger = logging.getLogger("forge.execution.acquisition")

_IS_WINDOWS = sys.platform == "win32"

# System package managers we will USE only if actually present (never assumed).
_PKG_MANAGERS = ("apt-get", "apt", "dnf", "yum", "pacman", "zypper", "apk", "brew", "choco", "winget")


class AcquisitionMethod(str, Enum):
    ALREADY_INSTALLED = "already_installed"
    WORKSPACE_LOCAL = "workspace_local"
    USER_LEVEL = "user_level"        # pip install --user (no root)
    SYSTEM_LEVEL = "system_level"    # package manager (root / admin)
    UNAVAILABLE = "unavailable"


@dataclass
class AcquisitionPlan:
    capability: str
    provider: str
    method: AcquisitionMethod
    command: str = ""
    privilege_level: str = "SAFE"     # SAFE | PRIVILEGED | DANGEROUS
    feasible: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "capability": self.capability, "provider": self.provider,
            "method": self.method.value, "command": self.command,
            "privilege_level": self.privilege_level, "feasible": self.feasible,
            "reason": self.reason,
        }


@dataclass
class AcquisitionResult:
    capability: str
    provider: str
    requested: bool = False
    approved: bool = False
    executed: bool = False
    success: bool = False
    method: str = AcquisitionMethod.UNAVAILABLE.value
    privilege_level: str = "SAFE"
    stdout: str = ""
    stderr: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "capability": self.capability, "provider": self.provider,
            "requested": self.requested, "approved": self.approved,
            "executed": self.executed, "success": self.success, "method": self.method,
            "privilege_level": self.privilege_level, "reason": self.reason,
        }


class AcquisitionPlanner:
    """Plans (and, only when approved, performs) controlled capability acquisition."""

    def __init__(self):
        self._pkg_manager_cache: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Environment probing (never assumes)
    # ------------------------------------------------------------------ #

    def detect_package_manager(self) -> str:
        if self._pkg_manager_cache is not None:
            return self._pkg_manager_cache
        found = ""
        for pm in _PKG_MANAGERS:
            if shutil.which(pm):
                found = pm
                break
        self._pkg_manager_cache = found
        return found

    def _pip(self) -> str:
        return shutil.which("pip") or shutil.which("pip3") or ""

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #

    def can_acquire(self, spec: ProviderSpec) -> bool:
        """Whether *spec* could be acquired here, without unsafe assumptions."""
        plan = self.plan_for_spec(spec)
        return plan.feasible

    def plan_for_spec(self, spec: ProviderSpec) -> AcquisitionPlan:
        cap_hint = ""  # filled by plan()
        if spec.kind is ProviderKind.BUILTIN:
            return AcquisitionPlan(cap_hint, spec.name, AcquisitionMethod.ALREADY_INSTALLED,
                                   feasible=False, reason="Built-in provider — nothing to acquire.")

        if spec.kind is ProviderKind.PYLIB:
            pip = self._pip()
            if pip:
                pkg = self._pip_package_name(spec)
                return AcquisitionPlan(
                    cap_hint, spec.name, AcquisitionMethod.USER_LEVEL,
                    command=f"{pip} install --user {pkg}",
                    privilege_level="PRIVILEGED", feasible=True,
                    reason=f"Python library '{spec.module}' is pip-installable at user level.")
            return AcquisitionPlan(cap_hint, spec.name, AcquisitionMethod.UNAVAILABLE,
                                   feasible=False, reason="pip is not available to install a Python library.")

        # TOOL — only via a package manager that actually exists here.
        pm = self.detect_package_manager()
        if pm:
            return AcquisitionPlan(
                cap_hint, spec.name, AcquisitionMethod.SYSTEM_LEVEL,
                command=self._system_install_command(pm, spec),
                privilege_level="DANGEROUS", feasible=True,
                reason=f"CLI tool '{spec.binary}' can be installed via '{pm}' (requires approval).")
        return AcquisitionPlan(cap_hint, spec.name, AcquisitionMethod.UNAVAILABLE,
                               feasible=False,
                               reason=f"No package manager available to install '{spec.binary}'.")

    def plan(self, capability: str, provider: Optional[str] = None) -> AcquisitionPlan:
        """Plan acquisition for a capability, choosing the least-invasive feasible provider."""
        from backend.execution.capabilities import capability_service
        specs = capability_service.provider_specs(capability)
        if provider:
            specs = [s for s in specs if s.name == provider] or specs
        best: Optional[AcquisitionPlan] = None
        # Preference: USER_LEVEL (pip) before SYSTEM_LEVEL (root pkg manager).
        rank = {AcquisitionMethod.USER_LEVEL: 0, AcquisitionMethod.SYSTEM_LEVEL: 1}
        for spec in specs:
            p = self.plan_for_spec(spec)
            p.capability = capability
            if not p.feasible:
                continue
            if best is None or rank.get(p.method, 9) < rank.get(best.method, 9):
                best = p
        if best is None:
            return AcquisitionPlan(capability, provider or (specs[0].name if specs else ""),
                                   AcquisitionMethod.UNAVAILABLE, feasible=False,
                                   reason="No feasible acquisition method in this environment.")
        return best

    # ------------------------------------------------------------------ #
    # Acquisition (privilege-gated, auditable)
    # ------------------------------------------------------------------ #

    async def acquire(
        self,
        capability: str,
        *,
        provider: Optional[str] = None,
        agent: str = "capability_manager",
        installer: Optional[Callable[[str], Any]] = None,
        privilege_decider: Optional[Callable[[AcquisitionPlan], bool]] = None,
    ) -> AcquisitionResult:
        """Request acquisition of *capability*.

        The WHETHER decision goes through the PrivilegeManager (or *privilege_decider*
        in tests).  Only if approved is the plan's command executed — via the
        injectable *installer* (default: the real ExecutionService), so tests never
        touch the host.  Every attempt is auditable.
        """
        plan = self.plan(capability, provider)
        result = AcquisitionResult(
            capability=capability, provider=plan.provider, requested=True,
            method=plan.method.value, privilege_level=plan.privilege_level)

        if not plan.feasible:
            result.reason = plan.reason
            logger.info(f"[Acquisition] {capability}: not feasible — {plan.reason}")
            return result

        approved = self._decide(plan, agent=agent, privilege_decider=privilege_decider)
        result.approved = approved
        if not approved:
            result.reason = (f"Acquisition of '{plan.provider}' for '{capability}' requires "
                             f"operator approval ({plan.privilege_level}); pending/denied.")
            logger.info(f"[Acquisition] {capability}: awaiting approval ({plan.privilege_level}).")
            return result

        # Approved → perform via the injectable installer (default: ExecutionService).
        run = installer or self._default_installer
        try:
            exec_result = await run(plan.command)
            ok = bool(getattr(exec_result, "succeeded", False)) or getattr(exec_result, "status", "") == "SUCCESS"
            result.executed = True
            result.success = ok
            result.stdout = getattr(exec_result, "stdout", "") or ""
            result.stderr = getattr(exec_result, "stderr", "") or ""
            result.reason = ("Acquisition succeeded." if ok else
                             f"Acquisition command failed: {result.stderr[:200]}")
            if ok:
                # Re-discover so the freshly-installed provider is visible.
                try:
                    from backend.execution.capabilities import capability_service
                    capability_service.refresh(capability)
                except Exception:
                    pass
        except Exception as exc:
            result.executed = True
            result.success = False
            result.reason = f"Acquisition raised: {exc}"
        logger.info(f"[Acquisition] {capability}: executed={result.executed} success={result.success}")
        return result

    # ------------------------------------------------------------------ #

    def _decide(self, plan: AcquisitionPlan, *, agent: str,
                privilege_decider: Optional[Callable[[AcquisitionPlan], bool]]) -> bool:
        if privilege_decider is not None:
            try:
                return bool(privilege_decider(plan))
            except Exception:
                return False
        # Default: route through the real PrivilegeManager (writes an audit row).
        try:
            from backend.privilege.manager import privilege_manager
            from backend.database.session import SessionLocal
            db = SessionLocal()
            try:
                return privilege_manager.evaluate_privilege(
                    agent=agent, tool_name=f"acquire:{plan.provider}",
                    privilege_level=plan.privilege_level, db=db)
            finally:
                db.close()
        except Exception as exc:
            logger.debug(f"[Acquisition] privilege check unavailable ({exc}); defaulting to deny.")
            return False

    async def _default_installer(self, command: str):
        from backend.execution.service import execution_service
        return await execution_service.run_command(command, timeout_seconds=300,
                                                    capability="capability_acquisition",
                                                    tool_name="acquire")

    @staticmethod
    def _pip_package_name(spec: ProviderSpec) -> str:
        # Recipe like "pip install pytesseract" → package token; else fall back to module.
        recipe = (spec.install_recipe or "").strip()
        if recipe.startswith("pip install "):
            return recipe[len("pip install "):].strip()
        return spec.module or spec.name

    @staticmethod
    def _system_install_command(pm: str, spec: ProviderSpec) -> str:
        pkg = spec.binary or spec.name
        if pm in ("apt-get", "apt"):
            return f"sudo {pm} install -y {pkg}"
        if pm in ("dnf", "yum", "zypper"):
            return f"sudo {pm} install -y {pkg}"
        if pm == "pacman":
            return f"sudo pacman -S --noconfirm {pkg}"
        if pm == "apk":
            return f"sudo apk add {pkg}"
        if pm == "brew":
            return f"brew install {pkg}"
        if pm == "choco":
            return f"choco install -y {pkg}"
        if pm == "winget":
            return f"winget install -e --id {pkg}"
        return f"{pm} install {pkg}"


# Module-level singleton.
acquisition_planner = AcquisitionPlanner()
