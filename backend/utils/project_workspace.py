"""Allocation only: project directories never use challenge cleanup helpers."""

import os
from uuid import UUID

from backend.utils import workspace


class ProjectWorkspaceError(ValueError):
    pass


def project_workspace_root():
    root = os.path.join(workspace.CTF_WORKSPACE_ROOT, "_projects")
    if not workspace.is_within_workspace(root):
        raise ProjectWorkspaceError("Project root is outside the CTF workspace")
    return os.path.realpath(root)


def _contained(path):
    root = project_workspace_root()
    resolved = os.path.realpath(path)
    try:
        contained = os.path.commonpath([os.path.normcase(root), os.path.normcase(resolved)]) == os.path.normcase(root)
    except ValueError:
        contained = False
    if not contained:
        raise ProjectWorkspaceError("Path is outside the project workspace")
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(resolved):
        raise ProjectWorkspaceError("Redirected project directories are not supported")
    return resolved


def project_path(project_id):
    return _contained(os.path.join(project_workspace_root(), str(UUID(str(project_id)))))


def session_path(project_id, session_id):
    return _contained(os.path.join(project_path(project_id), "sessions", str(UUID(str(session_id)))))


def validate_project_directory(project_id, stored_path):
    expected = project_path(project_id)
    if os.path.normcase(_contained(stored_path)) != os.path.normcase(expected) or not os.path.isdir(expected):
        raise ProjectWorkspaceError("Invalid project directory")
    return expected


def validate_session_directory(project_id, session_id, stored_path):
    expected = session_path(project_id, session_id)
    if os.path.normcase(_contained(stored_path)) != os.path.normcase(expected) or not os.path.isdir(expected):
        raise ProjectWorkspaceError("Invalid session directory")
    return expected


def allocate_project_directory(project_id):
    root = project_workspace_root()
    os.makedirs(_contained(root), exist_ok=True)
    destination = project_path(project_id)
    # exist_ok=False refuses files, dangling links, and unexpected directories.
    os.mkdir(destination)
    for name in ("files", "artifacts", "sessions"):
        os.mkdir(_contained(os.path.join(destination, name)))
    return destination


def allocate_session_directory(project_id, project_directory, session_id):
    validate_project_directory(project_id, project_directory)
    parent = _contained(os.path.join(project_path(project_id), "sessions"))
    if not os.path.isdir(parent):
        raise ProjectWorkspaceError("Invalid sessions directory")
    destination = session_path(project_id, session_id)
    os.mkdir(destination)
    return destination
