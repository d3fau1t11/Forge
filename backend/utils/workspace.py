"""Challenge workspace path safety.

Single source of truth for where challenge working directories may live and
which paths are safe to delete. Both the API layer (backend.api.routes) and the
run layer (backend.api.runner) import from here so the rules never drift apart.

Background: challenges created before the ~/Documents/CTF hierarchy existed
stored working_directory="." which os.path.abspath() resolves to the FORGE
project root (wherever launch_forge.py was started). Deleting such a challenge
ran shutil.rmtree() on the project root and wiped the whole installation. These
helpers make that impossible via a strict allowlist.

Containment model (Fix 01 — directory browse / create / select-folder-dialog):

  * CTF_WORKSPACE_ROOT is the allowlist root for the three directory endpoints.
  * Containment is decided with os.path.commonpath on realpath()-resolved,
    case-normalized paths, never with string-prefix checks. This defeats
    sibling-prefix tricks (e.g. .../CTF-evil vs .../CTF) and makes realpath()
    follow symlinks / junctions so a link that resolves outside the root cannot
    be used to escape.
  * Non-existent destinations are validated by resolving their longest existing
    ancestor and checking the combined (resolved ancestor + intended remainder)
    against the root.
  * The workspace root itself counts as "inside" (browsing the root is
    permitted). Deletion keeps the stricter is_deletable_working_dir rule, under
    which the root itself is not deletable.
  * Fail closed: any path that cannot be provably contained is rejected. No
    valid input is ever silently remapped to a different location.

Residual limitation:

  * realpath() is checked at request time, but the filesystem can change
    afterward (a directory swapped for a symlink/junction between the check and
    scandir()/makedirs()). A fully race-free guarantee would require re-checking
    via open file descriptors at every step — a larger redesign outside this
    fix. Treat these endpoints as reachable only by a local, trusted frontend:
    they are currently unauthenticated and CORS is not authorization.
"""

import os

# Canonical root that all challenge working directories must live under.
# Computed at import time; the application never creates this directory itself,
# so callers must tolerate a missing root (browse reports it as empty).
CTF_WORKSPACE_ROOT = os.path.abspath(os.path.expanduser(os.path.join("~", "Documents", "CTF")))

# The FORGE project directory itself (…/backend/utils/workspace.py -> project root),
# so we can explicitly refuse to delete it or any of its ancestors.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _realpath_root() -> str:
    """Real, case-normalized workspace root used as the containment boundary."""
    return os.path.normcase(os.path.realpath(CTF_WORKSPACE_ROOT))


def is_within_workspace(path: str) -> bool:
    """Return True iff `path` resolves to CTF_WORKSPACE_ROOT or strictly beneath it.

    Symlinks and junctions are followed (realpath) so a link that resolves
    outside the root cannot be used to escape. Non-existent destinations are
    validated by resolving their longest existing ancestor; the remaining
    non-existent components are appended and the combined result is checked.
    The workspace root itself counts as inside (suitable for browsing).

    Fail closed: unparseable, cross-drive, UNC-vs-local, and otherwise
    incomparable paths return False. No valid input is silently remapped to a
    different location.
    """
    if not path or not isinstance(path, str):
        return False
    root = _realpath_root()
    try:
        resolved = os.path.normcase(os.path.realpath(path.strip()))
    except (OSError, ValueError, TypeError):
        return False
    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:
        # Different drives on Windows, UNC vs local, or otherwise incomparable.
        return False


def is_valid_directory_name(name: str) -> bool:
    """Return True iff `name` is a single, safe directory name (not a path).

    Rejects empty names, '.', '..', any path separator, traversal, null bytes,
    control characters, surrounding whitespace, trailing periods, and
    platform-invalid / reserved names. The caller is responsible for combining
    the name with a validated parent and re-checking containment of the result.
    """
    if not isinstance(name, str):
        return False
    if '\x00' in name or any(ord(c) < 0x20 for c in name):
        return False
    if '/' in name or '\\' in name or os.sep in name:
        return False
    stripped = name.strip()
    if not stripped or stripped != name:
        return False  # empty or surrounding whitespace
    if stripped in ('.', '..'):
        return False
    if stripped.endswith('.'):
        return False
    if os.name == 'nt':
        if any(c in '<>:"|?*' for c in stripped):
            return False
        base = stripped.split('.', 1)[0].upper()
        reserved = {
            'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
            'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
        }
        if base in reserved:
            return False
    return True


def is_deletable_working_dir(clean_path: str) -> bool:
    """Return True only if clean_path is a real subdirectory strictly inside CTF_WORKSPACE_ROOT.

    Refuses: the workspace root itself, the project root, the project root's
    ancestors, and (transitively) the user home and system roots, since none of
    those live strictly under CTF_WORKSPACE_ROOT. Uses commonpath containment so
    sibling-prefix tricks (e.g. /a/CTF-evil vs /a/CTF) cannot slip through.

    clean_path must already be absolute (os.path.abspath).
    """
    root = CTF_WORKSPACE_ROOT
    if clean_path == root:
        return False
    try:
        if os.path.commonpath([clean_path, root]) != root:
            return False
    except ValueError:
        # Different drives on Windows, or otherwise incomparable paths.
        return False
    # Never delete the project directory or any ancestor of it, even if somehow
    # nested under the workspace root via a symlink or misconfiguration.
    if clean_path == PROJECT_ROOT or PROJECT_ROOT.startswith(clean_path + os.sep):
        return False
    return True


def safe_working_dir_for(challenge_id: str, category: str = "", name: str = "") -> str:
    """Return a guaranteed-safe absolute working directory for a challenge.

    Used as the fallback when a challenge's stored working_directory is missing,
    empty, ".", or otherwise not safely inside CTF_WORKSPACE_ROOT. The path is
    deterministic per challenge so repeated runs reuse the same directory.
    """
    safe_cat = "".join(c for c in (category or "misc") if c.isalnum() or c in ("-", "_")) or "misc"
    safe_id = "".join(c for c in (challenge_id or "unknown") if c.isalnum() or c in ("-", "_")) or "unknown"
    return os.path.join(CTF_WORKSPACE_ROOT, "_workspaces", safe_cat, safe_id)


def resolve_safe_working_dir(stored: str, challenge_id: str, category: str = "", name: str = "") -> str:
    """Resolve a challenge's working directory to a safe absolute path.

    If `stored` resolves to a path strictly inside CTF_WORKSPACE_ROOT it is used
    as-is; otherwise a safe per-challenge fallback is returned. Never returns the
    project root, home, or a system root.
    """
    if stored and isinstance(stored, str) and stored.strip():
        candidate = os.path.abspath(stored.strip())
        if is_deletable_working_dir(candidate):
            return candidate
    return safe_working_dir_for(challenge_id, category, name)
