"""Challenge workspace allocation, ownership, execution resolution, and cleanup.

PF02 — replaces the previous policy in which

  * the challenge working directory was derived from USER-SUPPLIED metadata
    (platform/category/difficulty/name) with ``os.makedirs(exist_ok=True)``,
    so a crafted or merely duplicated name ADOPTED an existing directory —
    including project workspaces under ``CTF_WORKSPACE_ROOT/_projects/…`` — and
  * deletion removed ANY non-root directory under the CTF workspace root whose
    path was recorded on the challenge row (deletion authorization used the
    same containment predicate that served as execution-path validation).

The three decisions are now separate and independent:

* Allocation — a challenge workspace lives at the dedicated server-controlled
  location ``CTF_WORKSPACE_ROOT/_challenges/<challenge UUID>/``. The UUID is the
  ONLY identity input; user metadata never influences the path. Allocation is
  collision-refusing (``os.mkdir``, never ``exist_ok``) so an existing directory
  is never adopted. See :func:`allocate_challenge_directory`.
* Ownership — a challenge may only operate on the workspace recorded in ITS OWN
  row when that workspace validates as exactly its canonical slot, or on a
  narrowly validated LEGACY path kept working until migration completes
  (:func:`validate_challenge_workspace`, :func:`is_owned_identity`).
  Filesystem containment alone is NOT ownership: directories under the reserved
  namespaces ``_projects``, ``_challenges`` (other slots), ``_uploads`` and
  ``_workspaces``, the CTF root itself, the repository root, redirected/symlinked
  paths, and anything outside the CTF workspace root are never challenge-owned.
  Project and challenge workspace lifecycles remain independent — this module
  never deletes or modifies anything under ``_projects``.
* Cleanup — challenge deletion may remove only the single directory owned by
  that challenge (its legacy/recorded path when authorized, plus its own
  canonical slot). Cleanup failures are raised, never swallowed, and the
  filesystem-first/Database-second ordering keeps deletion recoverable
  (:func:`cleanup_targets`, :func:`delete_challenge_workspace`).

The low-level containment primitives live in backend.utils.workspace and are
used unchanged (realpath-based commonpath containment); this module adds the
challenge-specific ownership policy on top.
"""

import os
import shutil
from uuid import UUID

from backend.utils import workspace

# Reserved first-level directory names directly under CTF_WORKSPACE_ROOT.
# Directories inside (or named by) these namespaces are NEVER challenge workspaces:
#   _projects    — project workspace namespace (independent lifecycle)
#   _challenges  — challenge workspace namespace (canonical slots only)
#   _uploads     — upload staging area
#   _workspaces  — pre-PF02 deterministic fallback layout of legacy records
RESERVED_NAMESPACE_NAMES = ("_projects", "_challenges", "_uploads", "_workspaces")

CHALLENGE_NAMESPACE_NAME = "_challenges"


class ChallengeWorkspaceError(ValueError):
    """Refusal with a machine-readable code for API error mapping."""
    code = "workspace_error"


class WorkspaceCollisionError(ChallengeWorkspaceError):
    code = "workspace_collision"


class WorkspaceCleanupError(ChallengeWorkspaceError):
    code = "workspace_cleanup_failed"


def challenge_workspace_root() -> str:
    """Real directory of the challenge workspace namespace (created lazily by allocation)."""
    root = os.path.join(workspace.CTF_WORKSPACE_ROOT, CHALLENGE_NAMESPACE_NAME)
    if not workspace.is_within_workspace(root):
        raise ChallengeWorkspaceError("Challenge namespace is outside the CTF workspace")
    if os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.realpath(root)):
        raise ChallengeWorkspaceError("Redirected challenge namespace is not supported")
    return root


def challenge_directory(challenge_id, strict: bool = False) -> str:
    """Canonical expected workspace path for a challenge id (directory may not exist).

    Allocation calls this with strict=True (UUID identity only). Resolution and
    cleanup use the non-strict form, where the id merely has to be a single safe
    directory-name component, so a legacy record with a non-UUID id still maps
    into its own dedicated slot instead of failing a run.
    """
    cid = str(challenge_id)
    if strict:
        try:
            cid = str(UUID(cid))
        except (ValueError, AttributeError, TypeError):
            raise ChallengeWorkspaceError("Challenge id is not a UUID")
    elif not workspace.is_valid_directory_name(cid):
        raise ChallengeWorkspaceError("Challenge id is not a safe directory name")
    return os.path.join(challenge_workspace_root(), cid)


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

def allocate_challenge_directory(challenge_id) -> str:
    """Create AND claim the canonical workspace for a challenge UUID.

    The challenge UUID must already exist as the challenge record identity —
    allocation never derives identity from challenge metadata. An existing
    directory, file, or dangling link at the destination is refused
    (FileExistsError -> WorkspaceCollisionError); nothing is adopted.
    """
    challenge_directory(challenge_id, strict=True)  # validates identity BEFORE any creation
    namespace = challenge_workspace_root()  # validates the namespace itself
    os.makedirs(namespace, exist_ok=True)
    destination = challenge_directory(challenge_id)
    try:
        os.mkdir(destination)
    except FileExistsError:
        raise WorkspaceCollisionError(
            "A workspace already exists for this challenge; refusing to adopt it")
    except OSError as exc:
        raise ChallengeWorkspaceError(f"Challenge workspace allocation failed: {exc}")
    return destination


def ensure_challenge_directory(challenge_id) -> str:
    """Return the canonical workspace, creating it only inside the challenge's own slot.

    Used as the missing/unsafe workspace fallback at EXECUTION time. Reusing the
    slot derived from the challenge's own UUID is deterministic and inherently
    owned; no user path input can steer creation.
    """
    destination = challenge_directory(challenge_id)
    os.makedirs(destination, exist_ok=True)
    return destination


# ---------------------------------------------------------------------------
# Ownership / validation
# ---------------------------------------------------------------------------

def is_reserved_namespace_path(root: str, path: str) -> bool:
    """True iff `path` (assumed inside `root`) sits inside a reserved namespace."""
    try:
        relative = os.path.relpath(path, root)
    except (ValueError, TypeError):
        return True
    if relative in ("", "."):
        return False  # the CTF root itself is handled by containment checks
    return os.path.normpath(relative).split(os.sep)[0] in RESERVED_NAMESPACE_NAMES


def is_owned_workspace(challenge_id, stored_path) -> bool:
    """True iff stored_path IS the canonical workspace of this exact challenge.

    Realpath-resolved equality against the canonical slot for the record's own
    UUID. The namespace itself must not be redirected (checked in
    challenge_workspace_root), so a symlinked or junctioned stored path that
    lands elsewhere is rejected.
    """
    if not isinstance(stored_path, str) or not stored_path.strip():
        return False
    try:
        expected = challenge_directory(challenge_id)
    except ChallengeWorkspaceError:
        return False
    try:
        resolved = os.path.realpath(stored_path.strip())
    except (OSError, ValueError, TypeError):
        return False
    if os.path.normcase(resolved) != os.path.normcase(os.path.abspath(expected)):
        return False
    return os.path.isdir(expected)


def is_owned_identity(challenge_id, stored_path) -> bool:
    """Ownership by identity, WITHOUT requiring the directory to exist.

    Used by deletion authorization so a challenge whose (already cleaned up)
    workspace directory is missing — e.g. a retry after an interrupted
    filesystem-era deletion — still passes the gate and the record deletion
    completes. The strict containment/redirect checks still apply.
    """
    if not isinstance(stored_path, str) or not stored_path.strip():
        return False
    try:
        expected = challenge_directory(challenge_id)
    except ChallengeWorkspaceError:
        return False
    try:
        resolved = os.path.realpath(stored_path.strip())
    except (OSError, ValueError, TypeError):
        return False
    return os.path.normcase(resolved) == os.path.normcase(os.path.abspath(expected))


def is_legacy_compatible_path(path: str) -> bool:
    """Narrowly validate a PRE-PF02 recorded workspace path.

    A legacy path is admissible (for execution AND cleanup) only when ALL hold:
      * it is a real directory strictly inside CTF_WORKSPACE_ROOT (never the
        root itself, never the repository root or its ancestors — the Fix 01
        deletion-predicate containment);
      * realpath resolution equals the plain absolute path — no symlink or
        junction anywhere along it redirects the location;
      * its first component under the root is not a reserved namespace, so
        project workspaces, other challenge slots, upload staging and the old
        fallback layout are all refused.
    """
    if not isinstance(path, str) or not path.strip():
        return False
    candidate = os.path.abspath(path.strip())
    if not os.path.isdir(candidate):
        return False
    if not workspace.is_deletable_working_dir(candidate):
        return False
    try:
        if os.path.normcase(os.path.realpath(candidate)) != os.path.normcase(candidate):
            return False  # redirected (symlink / junction / subst)
    except (OSError, ValueError, TypeError):
        return False
    if is_reserved_namespace_path(workspace.CTF_WORKSPACE_ROOT, candidate):
        return False
    return True


def validate_challenge_workspace(challenge_id, stored_path) -> str:
    """Execution-path validation, independent of deletion authorization.

    Resolution order:
      1. challenge's own canonical slot when the stored path IS that slot;
      2. narrowly validated legacy layout path (pre-PF02 records);
      3. otherwise the challenge's own canonical slot, created if missing.
    The result is always an absolute path belonging to this challenge or to a
    validated legacy record; unrelated, redirected, project, reserved-namespace
    and outside-root paths are never returned.
    """
    if not isinstance(stored_path, str) or not stored_path.strip():
        return ensure_challenge_directory(challenge_id)
    candidate = os.path.abspath(stored_path.strip())
    if is_owned_workspace(challenge_id, candidate):
        return candidate
    if is_legacy_compatible_path(candidate):
        return candidate
    return ensure_challenge_directory(challenge_id)


# ---------------------------------------------------------------------------
# Cleanup lifecycle
# ---------------------------------------------------------------------------

def is_missing_workspace_path(path: str) -> bool:
    """True only for a missing path, not a dangling link or an access error.

    lstat observes the link itself on both Windows and POSIX. Unlike exists
    (which follows links) or lexists (which suppresses all OSError values),
    only FileNotFoundError proves absence here; other errors retain refusal.
    This permits database-only deletion, never filesystem ownership.
    """
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return False


def cleanup_targets(challenge_id, stored_path) -> list:
    """Directories a challenge deletion MAY remove, in deletion order.

    Returns at most two absolute targets:
      * the challenge's OWN canonical slot (always owned by construction);
      * the recorded legacy path when narrowly validated as legacy-compatible.
    The caller (API layer) adds the cross-record ambiguity guard: legacy paths
    referenced by another challenge record still being kept are never cleaned.
    An absent recorded path is never a target; an existing owned canonical
    fallback slot can still be cleaned independently of that recorded path.
    """
    targets = []
    try:
        canonical = challenge_directory(challenge_id)
    except ChallengeWorkspaceError:
        return targets
    if os.path.isdir(canonical):
        targets.append(canonical)
    if (isinstance(stored_path, str) and stored_path.strip()
            and is_missing_workspace_path(stored_path.strip())):
        return targets
    if (isinstance(stored_path, str) and stored_path.strip()
            and not is_owned_workspace(challenge_id, stored_path)
            and is_legacy_compatible_path(stored_path)):
        target = os.path.abspath(stored_path.strip())
        if target not in targets:
            targets.append(target)
    return targets


def delete_challenge_workspace(path: str) -> str:
    """Delete ONE validated directory. Failures are raised, never swallowed.

    Returns the removed path. Callers must have already authorized the target
    via cleanup_targets + ownership/ambiguity checks; authorization lives in
    the API layer because it needs cross-record database knowledge.
    """
    try:
        if os.path.exists(path) and os.path.isdir(path):
            shutil.rmtree(path)
            return path
        if os.path.lexists(path):
            # A file/symlink where a directory is expected is never touched.
            raise WorkspaceCleanupError(
                "Refusing to clean up a non-directory workspace path")
        return ""
    except WorkspaceCleanupError:
        raise
    except OSError as exc:
        raise WorkspaceCleanupError(f"Workspace cleanup failed: {exc}")


__all__ = [
    "CHALLENGE_NAMESPACE_NAME",
    "ChallengeWorkspaceError",
    "RESERVED_NAMESPACE_NAMES",
    "WorkspaceCleanupError",
    "WorkspaceCollisionError",
    "allocate_challenge_directory",
    "challenge_directory",
    "challenge_workspace_root",
    "cleanup_targets",
    "delete_challenge_workspace",
    "ensure_challenge_directory",
    "is_legacy_compatible_path",
    "is_missing_workspace_path",
    "is_owned_identity",
    "is_owned_workspace",
    "is_reserved_namespace_path",
    "validate_challenge_workspace",
]
