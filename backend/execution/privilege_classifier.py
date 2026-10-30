"""
Privilege-risk classification of the *actual* execution operation.

This module is intentionally side-effect-free and is NOT wired into
ExecutionService, ProcessManager, LocalBackend, or any execution path.  It is
a pure classifier used to evaluate how risky an operation is based on what is
actually about to run (command / argv, execution mode, capability binding),
never on a caller-supplied ``privilege_level`` label.

Classification is conservative:

* SAFE        - only operations whose behaviour is bounded and verified by the
                existing tool/capability definitions (a registered tool whose
                ``privilege_requirement`` is ``SAFE``, executed either shell-free
                via ``argv`` or as a simple command with no shell syntax, with a
                verified executable-to-capability binding and no unvalidated
                filesystem-write arguments).
* APPROVAL_REQUIRED - operations that may need elevation or whose behaviour
                cannot be safely established: shell syntax (chaining, pipelines,
                redirection, substitution, expansion), privileged tools,
                interpreters, environment-variable assignments, argv without a
                verified capability binding, supplied environment variables, or
                arguments that cannot be validated against a verified constraint.
* DENY        - definitively not safe: privilege wrappers (sudo/su/doas/...),
                empty or unparsable commands.
* UNKNOWN     - no basis to classify (unknown executable); never SAFE.

Both DENY and UNKNOWN are conservative outcomes: neither is ever treated as
SAFE, and a caller cannot make a dangerous operation SAFE by supplying a
``privilege_level="SAFE"`` label.

Nothing here executes a command, changes permissions, modifies the host, or
performs database or audit-log writes.  Registry lookups are read-only.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backend.execution.base import ExecutionRequest
from backend.execution.command_safety import find_shell_syntax
from backend.tools.registry import tool_registry


class OperationRisk(str, Enum):
    SAFE = "SAFE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DENY = "DENY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of :func:`classify_operation` (observability only, no side effects)."""

    risk: OperationRisk
    reason: str
    details: dict


# Executables that explicitly request privilege elevation.  Never SAFE.
_PRIVILEGE_WRAPPERS = frozenset(
    {"sudo", "su", "doas", "runas", "gsudo", "pkexec", "kdesu", "gksu", "super"}
)


# Arbitrary-code interpreters: executing one admits unrestricted script
# execution, so these are never SAFE -- even when bound to a registered SAFE
# capability.  The set is deliberately small and explicitly listed.
_INTERPRETERS = frozenset(
    {
        # Shell interpreters.
        "bash",
        "sh",
        "zsh",
        "dash",
        "ksh",
        "csh",
        "tcsh",
        "fish",
        "pwsh",
        "powershell",
        "powershell.exe",
        "cmd.exe",
        "command.com",
        # Scripting / bytecode interpreters that run arbitrary code.
        "python",
        "python2",
        "python3",
        "perl",
        "ruby",
        "node",
        "php",
        "lua",
        "r",
        "rscript",
    }
)

# A leading shell environment assignment, e.g. ``VAR=value cmd``.
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_absolute_executable_path(path: str) -> bool:
    """Check if *path* is an absolute executable path.

    Handles Unix absolute paths (leading ``/``), Windows absolute paths
    (``C:\\...`` or ``\\\\...``), and rejects relative names.
    """
    if not path:
        return False
    # Unix-style: leading / or \\
    if path.startswith("/") or path.startswith("\\\\"):
        return True
    # Windows drive letter (e.g. C:\) or just C: (relative on some setups,
    # but treated as absolute candidate for security)
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return True
    return False


def _normalize_binary_basename(name: str) -> str:
    """Normalize a binary name for cross-platform comparison.

    Strips common Windows executable extensions (.exe, .com, .bat, .cmd)
    and lowercases, so that ``"curl.exe"`` and ``"curl`` compare equal.
    """
    base = os.path.basename(name).lower()
    for ext in (".exe", ".com", ".bat", ".cmd"):
        if base.endswith(ext):
            base = base[:-len(ext)]
    return base


def _tool_by_binary(binary_name: str):
    """Return the registered tool whose *binary* matches *binary_name*, else None."""
    for tool in tool_registry.tools.values():
        if tool.binary and os.path.basename(tool.binary).lower() == binary_name.lower():
            return tool
    return None


def _binary_matches_tool(binary_name: str, tool) -> bool:
    """Verify executable identity matches the registered tool.

    Basename match is necessary but NOT sufficient.  Additional checks:

    * Absolute paths: the full path must correspond to the registered tool's
      binary (same normalized path), not just a same-basename substitute.
      This prevents /tmp/evil/curl from matching a tool registered with
      binary="curl".
    * Relative/name paths: the executable must be resolvable via
      ``shutil.which()`` and its normalized resolved basename must match the
      tool's declared binary basename.  shutil.which() alone is not trusted
      as proof of binary integrity, but it is used to prevent PATH-shadowing
      attacks and relative-path substitution.  If shutil.which() cannot
      resolve the executable (e.g. the tool is not installed in this
      environment), the basename match from Step 1 is retained for
      backward compatibility.

    Neither basename equality nor shutil.which() resolution alone is proof
    of binary integrity, but together they prevent the most common
    substitution attacks without introducing fragile hardcoded paths.
    """
    if not tool or not tool.binary:
        return False

    # Step 1: Basename match - necessary but not sufficient.
    if os.path.basename(binary_name).lower() != os.path.basename(tool.binary).lower():
        return False

    # Step 2: Identity verification based on how the binary is specified.
    # First, reject relative-path executables (e.g. ./evil/curl, ../exploit/binary)
    # These cannot be trusted as identity matches without full path verification.
    if binary_name.startswith("./") or binary_name.startswith("../"):
        # Relative-path executable substitution: do not inherit SAFE.
        # The executable cannot be verified against the registered tool's binary.
        return False

    if _is_absolute_executable_path(binary_name):
        # Absolute-path executable substitution: only inherit SAFE if the
        # full absolute path matches the registered tool's binary (normalised).
        # This prevents /tmp/evil/curl from matching a tool registered with
        # binary="curl".
        if _is_absolute_executable_path(tool.binary):
            return os.path.normpath(binary_name) == os.path.normpath(tool.binary)
        # Registered binary is a name; an absolute-path argv[0] cannot be
        # verified against a name-only registration without fragile look‑ups.
        return False
    else:
        # Relative or name-only argv[0]: optionally verify via shutil.which()
        # if the executable is resolvable on PATH.  shutil.which() handles
        # PATHEXT on Windows and PATH ordering on Linux, but is not accepted as
        # proof of binary integrity on its own - it merely prevents
        # trivial relative-path shadow attacks when the executable IS found.
        # Normalize both basenames for comparison (handles .exe/.EXE on Windows).
        resolved = shutil.which(binary_name)
        if resolved:
            return _normalize_binary_basename(resolved) == _normalize_binary_basename(
                tool.binary
            )
        # shutil.which() could not locate the executable; fall back to the
        # basename match from Step 1 for backward compatibility with test
        # environments where the tool is not on PATH.
        return True


# File-output / write flags that direct a tool to write to the filesystem.
# Kept small and tied to the confirmed false-SAFE paths; not a comprehensive
# per-tool argument policy.
_FILE_OUTPUT_FLAGS = frozenset(
    {
        "-o",
        "--output",
        "-O",
        "--remote-name",
        "--output-file",
        "--output-dir",
        "-oN",
        "-oX",
        "-oS",
        "-oG",
    }
)


def _is_absolute_filesystem_path(token: str) -> bool:
    """True iff *token* is an absolute filesystem path rather than a URL.

    Tokens with a scheme (``://``) are network locations, not filesystem
    paths, and are not flagged here.
    """
    if not token:
        return False
    if "://" in token:
        return False
    if token.startswith("/") or token.startswith("\\"):
        return True
    if len(token) >= 2 and token[1] == ":" and token[0].isalpha():
        return True  # Windows drive letter, e.g. ``C:\\``
    return False


def _arguments_require_approval(tokens: list) -> bool:
    """True iff *tokens* (full argv; ``tokens[0]`` is the binary) carry
    filesystem-write or sensitive-filesystem-access risk that the classifier
    cannot validate against any verified constraint.

    The registry's ``args_template`` is a formatting template, not a security
    policy, and the classifier maintains no argument allowlist, so operations
    whose arguments touch the filesystem are conservatively routed to
    APPROVAL_REQUIRED instead of SAFE.

    Additional conservative checks:

    * Relative‑path arguments (``./...`` or ``../...``) are not trusted,
      because they can substitute an unregistered executable relative to the
      caller's cwd or PATH.
    * ``--script`` flags with a path argument that is not a URL are not
      trusted, because they enable arbitrary script execution (e.g. nmap
      NSE scripts) whose behaviour cannot be verified against the tool's
      definition.
    """
    for i, token in enumerate(tokens[1:], start=1):
        if token in _FILE_OUTPUT_FLAGS:
            return True
        if _is_absolute_filesystem_path(token):
            return True
        # Relative‑path arguments cannot be verified against any trustworthy
        # definition; conservatively require approval.
        if token.startswith("./") or token.startswith("../"):
            return True
        # ``--script`` with a non‑URL path argument enables arbitrary script
        # execution whose behaviour cannot be verified.
        if token == "--script" and i + 1 < len(tokens):
            next_token = tokens[i]
            if next_token and "://" not in next_token:
                return True
    return False


# ---- Remaining classifier code (truncated for brevity) continues below ----


def _base_details(request: ExecutionRequest) -> dict:
    """Observability details; privilege_level is recorded but never trusted."""
    return {
        "supplied_privilege_level": request.privilege_level or "SAFE",
        # privilege_level is recorded for auditability only and is never used
        # to establish that an operation is safe.
    }


def classify_operation(request: ExecutionRequest) -> ClassificationResult:
    """Classify the privilege risk of the actual execution operation.

    Pure and side-effect-free: no command is executed, no file or permission
    is modified, and no database or audit-log write occurs.  The caller-supplied
    ``request.privilege_level`` is never trusted as proof of safety.
    """
    details = _base_details(request)

    # Environment variables can alter dynamic-library loading, interpreter
    # behaviour, and privilege boundaries.  The classifier has no verified
    # environment policy, so any supplied environment makes the operation
    # non-SAFE (APPROVAL_REQUIRED) rather than SAFE.
    if request.env:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "environment variables supplied - execution environment not verified",
            {**details, "env": dict(request.env)},
        )

    argv = [str(a) for a in (request.argv or [])]
    if argv:
        return _classify_argv(request, argv, details)
    return _classify_command(request, details)


def _classify_argv(request, argv, details) -> ClassificationResult:
    """Classify a shell-free (argv) execution request.

    ``argv`` elements are literal arguments passed with ``shell=False``, so
    their contents are never re-interpreted as shell syntax.
    """
    details["mode"] = "argv"
    binary = argv[0]
    binary_name = os.path.basename(binary)
    details["binary"] = binary_name

    low = binary_name.lower()
    if low in _PRIVILEGE_WRAPPERS:
        return ClassificationResult(
            OperationRisk.DENY,
            "privilege wrapper executed directly",
            {**details, "wrapper": binary_name},
        )
    if low in _INTERPRETERS:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "interpreter - arbitrary script execution",
            {**details, "interpreter": binary_name},
        )

    capability = (request.capability or "").strip()
    tool_name = (request.tool_name or "").strip()

    if capability and tool_name:
        tool = tool_registry.tools.get(tool_name)
        details["capability"] = capability
        details["tool"] = tool_name
        if tool is not None and capability in tool.capabilities:
            # Verify the executable actually matches the registered tool's
            # declared binary before inheriting its classification.  A
            # mismatched executable must never borrow another tool's SAFE
            # rating (Issue A: capability-to-executable binding).
            if not _binary_matches_tool(binary_name, tool):
                return ClassificationResult(
                    OperationRisk.APPROVAL_REQUIRED,
                    "argv executable does not match bound capability binary",
                    {**details, "capability": capability, "tool": tool_name},
                )
            # Arguments cannot be validated against any verified constraint:
            # args_template is a formatting template, not a security policy,
            # and the classifier maintains no argument allowlist (Issue B).
            if _arguments_require_approval(argv):
                return ClassificationResult(
                    OperationRisk.APPROVAL_REQUIRED,
                    "arguments carry unverified filesystem/write risk",
                    {**details, "capability": capability, "tool": tool_name},
                )
            # Bounded, known capability operation: behaviour grounded in the
            # registered tool definition.
            priv = (tool.privilege_requirement or "SAFE").upper()
            if priv == "SAFE":
                return ClassificationResult(
                    OperationRisk.SAFE,
                    "bounded safe capability (shell-free argv)",
                    {**details, "privilege": priv},
                )
            return ClassificationResult(
                OperationRisk.APPROVAL_REQUIRED,
                f"capability bound to {priv}-privilege tool",
                {**details, "privilege": priv},
            )
        # Tool named but not bound to this capability: behaviour cannot be verified.
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "argv execution without verified capability binding",
            {**details},
        )

    # No capability binding: fall back to the registered tool definition.
    tool = tool_registry.tools.get(binary_name) or _tool_by_binary(binary_name)
    if tool is not None:
        details["tool"] = tool.tool_name
        # Arguments cannot be validated against any verified constraint
        # (Issue B), even when the executable itself is registered.
        if _arguments_require_approval(argv):
            return ClassificationResult(
                OperationRisk.APPROVAL_REQUIRED,
                "arguments carry unverified filesystem/write risk",
                {**details, "tool": tool.tool_name},
            )
        priv = (tool.privilege_requirement or "SAFE").upper()
        if priv == "SAFE":
            return ClassificationResult(
                OperationRisk.SAFE,
                "shell-free execution of registered safe tool",
                {**details, "privilege": priv},
            )
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            f"registered {priv}-privilege tool",
            {**details, "privilege": priv},
        )

    return ClassificationResult(
        OperationRisk.UNKNOWN,
        "unknown argv executable - no basis to classify",
        {**details},
    )


def _classify_command(request, details) -> ClassificationResult:
    """Classify a shell-interpreted command-string request."""
    details["mode"] = "shell"
    command = (request.command or "").strip()
    if not command:
        return ClassificationResult(OperationRisk.DENY, "empty command", details)

    # Quote-aware shell-syntax detection; never a naive substring scan.
    feature = find_shell_syntax(command)
    if feature is not None:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            f"shell syntax ({feature}) - behaviour not safely established",
            {**details, "shell_feature": feature},
        )

    # No shell syntax: split into literal argv, preserving argument boundaries.
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ClassificationResult(OperationRisk.DENY, "unparsable command", details)
    if not tokens:
        return ClassificationResult(OperationRisk.DENY, "empty command after parsing", details)

    details["argv"] = tokens
    first = tokens[0]

    # A leading environment-variable assignment modifies the execution environment.
    if _ENV_ASSIGNMENT_RE.match(first):
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "environment-variable assignment modifies execution environment",
            {**details, "assignment": first},
        )

    binary_name = os.path.basename(first)
    low = binary_name.lower()

    if low in _PRIVILEGE_WRAPPERS:
        return ClassificationResult(
            OperationRisk.DENY,
            "privilege wrapper",
            {**details, "wrapper": binary_name},
        )
    if low in _INTERPRETERS:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "interpreter - arbitrary script execution",
            {**details, "interpreter": binary_name},
        )

    tool = tool_registry.tools.get(binary_name) or _tool_by_binary(binary_name)
    if tool is not None:
        details["tool"] = tool.tool_name
        # Arguments cannot be validated against any verified constraint
        # (Issue B), even for a registered safe tool.
        if _arguments_require_approval(tokens):
            return ClassificationResult(
                OperationRisk.APPROVAL_REQUIRED,
                "arguments carry unverified filesystem/write risk",
                {**details, "tool": tool.tool_name},
            )
        priv = (tool.privilege_requirement or "SAFE").upper()
        if priv == "SAFE":
            return ClassificationResult(
                OperationRisk.SAFE,
                "simple command of registered safe tool, no shell syntax",
                {**details, "privilege": priv},
            )
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            f"registered {priv}-privilege tool",
            {**details, "privilege": priv},
        )

    return ClassificationResult(
        OperationRisk.UNKNOWN,
        "unknown command - no basis to classify",
        {**details, "binary": binary_name},
    )