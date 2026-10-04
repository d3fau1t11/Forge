import os
import re
import shlex
from typing import Optional

from backend.tools.registry import tool_registry

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*;\s*:\s*\}", re.IGNORECASE),  # fork bomb
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"curl[^|]*\|\s*(sh|bash)\b", re.IGNORECASE),
    re.compile(r"wget[^|]*\|\s*(sh|bash)\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r">\s*/dev/sd", re.IGNORECASE),
    re.compile(r"\biptables\b", re.IGNORECASE),
    re.compile(r"\buserdel\b|\bpasswd\b", re.IGNORECASE),
]

# Interpreters that run an operator/agent-authored SCRIPT.  `python3 exploit.py` is
# FORGE's ordinary automation path, not a privileged side effect, so these are treated
# exactly like an already-registered SAFE tool instead of falling through to the
# PRIVILEGED fail-closed default (which still covers genuinely unknown binaries:
# sqlmap, hydra, nc, socat, and anything else unregistered).
#
# SECURITY — ordering is load-bearing: this allowlist is consulted AFTER the
# DANGEROUS_PATTERNS scan above it in classify_command_privilege(), so it can never
# launder a destructive command.  `python3 -c "import os; os.system('rm -rf /')"`
# still classifies DANGEROUS, because the pattern scan reads the ENTIRE command string
# including text inside a `-c` argument.  Do not move the check above the scan.
AUTOMATION_SAFE_BINARIES = {"python", "python3", "bash", "sh", "node", "perl", "ruby", "php"}

# Creation/copy binaries that MAY be auto-approved when — and ONLY when — every path
# argument is provably confined to the caller-supplied workspace root.  This is a
# NARROW carve-out for routine agent workspace operations (e.g. `mkdir -p recon`) that
# would otherwise fall through to the PRIVILEGED fail-closed default and stall a manual
# approval gate.  It is consulted AFTER the DANGEROUS_PATTERNS scan, so `rm -rf ...`,
# shell chaining, and path traversal all still classify as DANGEROUS/PRIVILEGED.
WORKSPACE_CONFINED_BINARIES = {"mkdir", "touch", "cp", "mv"}

# Shell metacharacters that can chain, redirect to, or expand into a SECOND command or
# an out-of-workspace path.  A flat reject is deliberate: there is no "safe" use worth
# reasoning about here, and per this module's fail-closed philosophy an unprovable
# command must stay PRIVILEGED.  `$` subsumes the `$(` command-substitution form.
_SHELL_METACHARS = (";", "|", "&", "`", ">", "<", "\n", "$", "~")


def _is_workspace_confined(cmd: str, workspace_root: str) -> bool:
    """Return True only when ``cmd`` is a creation/copy command whose every path
    argument resolves inside ``workspace_root``.

    SECURITY — this predicate authorizes AUTO-APPROVAL, so every uncertain branch is a
    flat False.  It MUST be called only after the DANGEROUS_PATTERNS scan in
    :func:`classify_command_privilege`, so it can never launder a destructive command.

    The real guard is the realpath/commonpath check in step (d), which defeats symlinks
    and ``..`` components.  The string checks in steps (b)/(c) are cheap early rejects,
    not substitutes for it.
    """
    # (a) No root to confine to -> refuse.   `workspace_root` is a caller-supplied
    #     mission context; None/"" means we cannot prove containment.
    if not workspace_root:
        return False

    # (b) Any chaining/redirect/expansion metacharacter -> refuse outright.
    if any(meta in cmd for meta in _SHELL_METACHARS):
        return False

    # (c) Cheap first filter for traversal.  Kept in ADDITION to the realpath check
    #     below (never instead of it): the string check catches the obvious case, and
    #     the realpath check is what actually defeats resolved symlinks and odd forms.
    if ".." in cmd:
        return False

    # (d) Tokenize, confirm the binary, then prove EVERY path argument is contained.
    #     POSIX shells treat backslash as an escape; Windows create_subprocess_shell
    #     runs cmd.exe, where a backslash is a literal path separator.  Tokenize to
    #     match the platform so a Windows path cannot be mis-read as a relative token
    #     (which would falsely look confined).
    try:
        tokens = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        # Unbalanced quotes etc. -> cannot reason about it -> fail closed.
        return False
    if not tokens:
        return False
    if os.path.basename(tokens[0]) not in WORKSPACE_CONFINED_BINARIES:
        return False

    root_real = os.path.realpath(workspace_root)
    for token in tokens[1:]:
        # Non-posix tokenization retains surrounding quotes; strip a matching pair.
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
            token = token[1:-1]
        if not token:
            continue
        if token.startswith("-"):
            # A bare boolean flag (-p, -r, -rf) carries no path.  A flag carrying a
            # value (--target-directory=/x, -t/x, --opt=val) can name a destination
            # this scan would otherwise skip, so refuse rather than guess.  Over-
            # rejecting only keeps a command PRIVILEGED (fail-closed).
            if "=" in token or "/" in token or "\\" in token:
                return False
            continue
        # Windows drive-relative paths (`C:foo`) are not absolute but still resolve
        # against a per-drive current directory, so they can escape; refuse them.
        if os.name == "nt":
            drive, _ = os.path.splitdrive(token)
            if drive and not os.path.isabs(token):
                return False
        candidate = token if os.path.isabs(token) else os.path.join(root_real, token)
        resolved = os.path.realpath(candidate)
        try:
            if os.path.commonpath([resolved, root_real]) != root_real:
                return False
        except ValueError:
            # Different drives / otherwise incomparable paths -> not confined.
            return False
    return True


def classify_command_privilege(
    cmd: str, bin_name: str, workspace_root: Optional[str] = None
) -> str:
    """Classify the privilege level required for a command.

    Priority order:
    1. Check cmd against dangerous regex patterns -> 'DANGEROUS'.  This runs FIRST,
       before any allowlist or registry lookup, so nothing can hide a destructive
       command behind a trusted name: `python3 -c "import os; os.system('rm -rf /')"`
       is DANGEROUS even though `python3` is a registered SAFE tool.
    2. If bin_name is in tool_registry.tools, return its privilege_requirement.
    3. Else, if bin_name is a common script interpreter -> 'SAFE' (automation).
    4. Else, if bin_name is a creation/copy binary AND every path argument is confined
       to `workspace_root` -> 'SAFE'.  The confinement scan runs AFTER the DANGEROUS
       scan above (ordering is load-bearing; see AUTOMATION_SAFE_BINARIES).
    5. Otherwise, return 'PRIVILEGED' (fail-closed).
    """
    if any(pattern.search(cmd) for pattern in DANGEROUS_PATTERNS):
        return "DANGEROUS"

    if bin_name and bin_name in tool_registry.tools:
        return tool_registry.tools[bin_name].privilege_requirement

    if bin_name and bin_name in AUTOMATION_SAFE_BINARIES:
        return "SAFE"

    # Workspace-confined creation/copy.  Gated on a NON-None workspace_root and a
    # proven-confinement predicate; when either is missing this falls through to the
    # PRIVILEGED default exactly as before.
    if (
        bin_name
        and bin_name in WORKSPACE_CONFINED_BINARIES
        and workspace_root
        and _is_workspace_confined(cmd, workspace_root)
    ):
        return "SAFE"

    return "PRIVILEGED"
