"""Supplemental endpoint-level regression tests for Fix 01.

These tests add coverage the pre-existing suite deliberately skips:
* symlink/junction escapes are SIMULATED (via monkeypatched os.path.realpath)
  because this Windows environment cannot create symlinks (WinError 1314);
  the pre-existing suite skips real-symlink tests for that reason.
* error responses must not leak internal exception details or host paths.

Every test points CTF_WORKSPACE_ROOT at an isolated tmp_path directory; the
real ~/Documents/CTF workspace is never touched.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath("."))

from backend.api import routes
from backend.utils import workspace


@pytest.fixture
def ws_root(tmp_path, monkeypatch):
    """Point CTF_WORKSPACE_ROOT at an isolated tmp_path directory."""
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "CTF_WORKSPACE_ROOT", str(root))
    monkeypatch.setattr(routes, "CTF_WORKSPACE_ROOT", str(root))
    return str(root)


def _simulate_symlink_resolving_outside(monkeypatch, link_path, outside="C:\\Windows"):
    """Monkeypatch os.path.realpath so `link_path` resolves to `outside`.

    Real symlinks cannot be created here (WinError 1314), so this simulates
    an existing symlink/junction inside the workspace that points outside it.
    """
    orig_realpath = os.path.realpath

    def fake_realpath(p):
        if os.path.normcase(str(p)) == os.path.normcase(link_path):
            return outside
        return orig_realpath(p)

    monkeypatch.setattr(os.path, "realpath", fake_realpath)


# ---------------------------------------------------------------- symlink escapes

def test_browse_symlink_escape_rejected(ws_root, monkeypatch):
    # A symlink inside the workspace that resolves outside must not be browsable.
    link = os.path.join(ws_root, "link_to_outside")
    _simulate_symlink_resolving_outside(monkeypatch, link)
    with pytest.raises(routes.HTTPException) as exc:
        routes.browse_directory(path=link)
    assert exc.value.status_code == 400


def test_create_symlink_parent_escape_rejected(ws_root, monkeypatch):
    # A symlinked parent must not let a new directory be created outside.
    parent = os.path.join(ws_root, "linked_parent")
    _simulate_symlink_resolving_outside(monkeypatch, parent)
    with pytest.raises(routes.HTTPException):
        routes.create_directory(
            routes.CreateDirRequest(parent_path=parent, dir_name="evil")
        )


# ------------------------------------------------------------- error hygiene

def test_create_error_does_not_leak_details(ws_root):
    # parent_path pointing at a file must fail with a generic message, not the
    # raw OS error or a sensitive host path.
    afile = os.path.join(ws_root, "afile.txt")
    with open(afile, "w") as fh:
        fh.write("x")
    req = routes.CreateDirRequest(parent_path=afile, dir_name="child")
    with pytest.raises(routes.HTTPException) as exc:
        routes.create_directory(req)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Failed to create directory."
