"""Challenge workspace path safety.

Single source of truth for where challenge working directories may live and
which paths are safe to delete. Both the API layer (backend.api.routes) and the
run layer (backend.api.runner) import from here so the rules never drift apart.

Background: challenges created before the ~/Documents/CTF hierarchy existed
stored working_directory="." which os.path.abspath() resolves to the FORGE
project root (wherever launch_forge.py was started). Deleting such a challenge
ran shutil.rmtree() on the project root and wiped the whole installation. These
helpers make that impossible via a strict allowlist.
"""

import os

# Canonical root that all challenge working directories must live under.
CTF_WORKSPACE_ROOT = os.path.abspath(os.path.expanduser(os.path.join("~", "Documents", "CTF")))

# The FORGE project directory itself (…/backend/utils/workspace.py -> project root),
# so we can explicitly refuse to delete it or any of its ancestors.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


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
