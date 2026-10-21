"""Regression tests for challenge working-directory deletion safety and the
Fix 01 workspace-containment controls for the directory browse,
create-directory, and select-folder-dialog endpoints.

Guards against the historic bug where a challenge whose working_directory was
"." (resolving to the FORGE project root) caused DELETE /challenges to run
shutil.rmtree() on the entire project. See backend.utils.workspace.

All tests run against an isolated temporary directory (never the real user
Documents directory, the repository root, or system directories) with
CTF_WORKSPACE_ROOT patched to that temporary directory.
"""

import os
import sys
import types
import pytest

sys.path.insert(0, os.path.abspath("."))

from backend.utils.workspace import (
    CTF_WORKSPACE_ROOT,
    PROJECT_ROOT,
    is_deletable_working_dir,
    is_valid_directory_name,
    is_within_workspace,
    resolve_safe_working_dir,
)


def test_project_root_is_not_deletable():
    assert is_deletable_working_dir(PROJECT_ROOT) is False


def test_dot_resolves_to_project_root_and_is_refused():
    # "." is exactly what poisoned the old rows: abspath(".") == project root.
    assert is_deletable_working_dir(os.path.abspath(".")) is False


def test_home_and_system_roots_not_deletable():
    assert is_deletable_working_dir(os.path.abspath(os.path.expanduser("~"))) is False
    assert is_deletable_working_dir(os.path.abspath(os.sep)) is False


def test_workspace_root_itself_not_deletable():
    assert is_deletable_working_dir(CTF_WORKSPACE_ROOT) is False


def test_sibling_prefix_trick_not_deletable():
    # A directory that merely shares a name prefix with the workspace root
    # must not be treated as inside it.
    assert is_deletable_working_dir(CTF_WORKSPACE_ROOT + "-evil") is False


def test_legit_challenge_dir_is_deletable():
    legit = os.path.join(CTF_WORKSPACE_ROOT, "PicoCTF", "WEB", "EASY", "some_challenge")
    assert is_deletable_working_dir(legit) is True


def test_resolve_safe_working_dir_rejects_dot():
    resolved = resolve_safe_working_dir(".", "abc123", "WEB", "Demo")
    assert resolved != os.path.abspath(".")
    assert is_deletable_working_dir(resolved) is True


def test_resolve_safe_working_dir_keeps_valid_path():
    legit = os.path.join(CTF_WORKSPACE_ROOT, "PicoCTF", "WEB", "EASY", "keepme")
    resolved = resolve_safe_working_dir(legit, "abc123", "WEB", "keepme")
    assert resolved == os.path.abspath(legit)


def test_safe_delete_refuses_project_root(tmp_path):
    # End-to-end: the routes helper must not remove a sentinel placed at the
    # project root when handed ".".
    from backend.api import routes

    sentinel = os.path.join(PROJECT_ROOT, ".delete_guard_sentinel")
    with open(sentinel, "w") as f:
        f.write("do not delete")
    try:
        routes._safe_delete_working_dir(".")
        routes._safe_delete_working_dir(PROJECT_ROOT)
        assert os.path.exists(sentinel), "project root sentinel was deleted!"
    finally:
        if os.path.exists(sentinel):
            os.remove(sentinel)


# ---------------------------------------------------------------------------
# Fix 01: workspace-containment helpers (unit level)
# ---------------------------------------------------------------------------


def test_is_within_workspace_allows_root_and_children(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        assert is_within_workspace(str(root)) is True
        assert is_within_workspace(os.path.join(str(root), "sub", "dir")) is True


def test_is_within_workspace_rejects_outside(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    outside = tmp_path / "outside"
    with _patch_workspace(str(root)):
        assert is_within_workspace(str(outside)) is False


def test_is_within_workspace_rejects_traversal(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        assert is_within_workspace(os.path.join(str(root), "sub", "..", "..", "escape")) is False


def test_is_within_workspace_rejects_sibling_prefix(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        assert is_within_workspace(str(root) + "-evil") is False


def test_is_within_workspace_validates_nonexistent_destination(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    # A destination beneath the root whose parents do not exist yet must still
    # validate as inside (its existing ancestors are within the root).
    with _patch_workspace(str(root)):
        assert is_within_workspace(os.path.join(str(root), "brand_new", "nested", "leaf")) is True


def test_is_within_workspace_rejects_cross_drive_and_unc(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    root_drive = os.path.splitdrive(str(root))[0]
    with _patch_workspace(str(root)):
        # Cross-drive (different drive letter than the root).
        other = "Z:" + os.sep + "outside" if os.name == "nt" else "/outside_definitely_not_root"
        assert is_within_workspace(other) is False
        # UNC path.
        assert is_within_workspace("\\\\server\\share\\x") is False


@pytest.mark.skipif(os.name != "nt", reason="Windows drive/UNC containment behavior")
def test_is_within_workspace_windows_cross_drive_and_unc(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    root_drive = os.path.splitdrive(str(root))[0].upper()
    # Pick a drive letter that is not the root's drive.
    other_letter = "Z:" if root_drive != "Z:" else "Y:"
    with _patch_workspace(str(root)):
        assert is_within_workspace(other_letter + "\\outside") is False
        assert is_within_workspace("\\\\server\\share\\x") is False


def test_is_valid_directory_name_rejects_escapes_and_invalid(tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        assert is_valid_directory_name("new_folder") is True
        assert is_valid_directory_name("web.challenge_1") is True
        # Path separators and traversal are not a single directory name.
        assert is_valid_directory_name("a/b") is False
        assert is_valid_directory_name("a\\b") is False
        assert is_valid_directory_name("..") is False
        assert is_valid_directory_name(".") is False
        assert is_valid_directory_name("") is False
        assert is_valid_directory_name("   ") is False
        assert is_valid_directory_name(" abs ") is False  # surrounding whitespace
        assert is_valid_directory_name("trailing.") is False
        # Null byte / control characters.
        assert is_valid_directory_name("bad\x00name") is False
        if os.name == "nt":
            assert is_valid_directory_name("con") is False
            assert is_valid_directory_name("COM1") is False
            assert is_valid_directory_name("lpt9.txt") is False
            assert is_valid_directory_name("bad|name") is False


# ---------------------------------------------------------------------------
# Fix 01: endpoint tests (via TestClient, isolated temporary workspace)
# ---------------------------------------------------------------------------


def _patch_workspace(root: str):
    """Context manager patching CTF_WORKSPACE_ROOT in both workspace and routes.

    routes imports CTF_WORKSPACE_ROOT by value, so the routes binding must be
    patched too; is_within_workspace reads the workspace-module global at call
    time, so patching workspace.CTF_WORKSPACE_ROOT covers containment checks.
    """
    import backend.api.routes as routes
    import backend.utils.workspace as workspace

    class _Ctx:
        def __enter__(self):
            self._m_ws = pytest.MonkeyPatch()
            self._m_rt = pytest.MonkeyPatch()
            self._m_ws.setattr(workspace, "CTF_WORKSPACE_ROOT", root)
            self._m_rt.setattr(routes, "CTF_WORKSPACE_ROOT", root)
            return root

        def __exit__(self, *exc):
            self._m_ws.undo()
            self._m_rt.undo()

    return _Ctx()


def _make_client(root: str):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _mock_dialog(monkeypatch, path_or_none):
    """Mock tkinter so the native folder dialog returns a chosen path (or None
    for cancellation) without opening a real GUI."""
    class FakeTk:
        def withdraw(self):
            pass

        def attributes(self, *a, **k):
            pass

        def destroy(self):
            pass

    class FakeFD:
        @staticmethod
        def askdirectory(title=""):
            return path_or_none

    fake_tk = types.SimpleNamespace(Tk=lambda: FakeTk(), filedialog=FakeFD)
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)


def test_browse_workspace_root_succeeds(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.get("/system/browse-dir")
        assert res.status_code == 200
        body = res.json()
        assert body["current_path"] == str(root)
        assert body["parent_path"] == str(root)  # no navigation outside the root
        assert body["drives"] == []  # no host drive enumeration
        assert body["directories"] == []


def test_browse_permitted_subdir_succeeds(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    sub = root / "PicoCTF" / "WEB" / "EASY"
    sub.mkdir(parents=True)
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.get(f"/system/browse-dir?path={sub}")
        assert res.status_code == 200
        assert res.json()["current_path"] == str(sub)


def test_default_browse_is_workspace_root_not_cwd(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.get("/system/browse-dir")
        assert res.status_code == 200
        assert res.json()["current_path"] == str(root)
        assert res.json()["current_path"] != os.getcwd()


def test_browse_outside_absolute_rejected(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    outside = tmp_path / "outside"
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.get(f"/system/browse-dir?path={outside}")
        assert res.status_code == 400


def test_browse_traversal_outside_rejected(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        traversal = os.path.join(str(root), "sub", "..", "..", "escape")
        res = client.get(f"/system/browse-dir?path={traversal}")
        assert res.status_code == 400


def test_browse_sibling_prefix_rejected(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.get(f"/system/browse-dir?path={str(root)}-evil")
        assert res.status_code == 400


def test_browse_nonexistent_path_not_silently_fallback(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        nonexistent = os.path.join(str(root), "does_not_exist")
        res = client.get(f"/system/browse-dir?path={nonexistent}")
        # Rejected (404) rather than silently falling back to os.getcwd().
        assert res.status_code == 404


def test_create_dir_inside_workspace_succeeds(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    parent = root / "PicoCTF"
    parent.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.post(
            "/system/create-dir",
            json={"parent_path": str(parent), "dir_name": "new_folder"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "SUCCESS"
        assert os.path.isdir(os.path.join(str(parent), "new_folder"))


def test_create_dir_rejects_bad_names(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        for bad in ["a/b", "a\\b", "C:\\x", "", "..", ".", "   ", "con"]:
            res = client.post(
                "/system/create-dir",
                json={"parent_path": str(root), "dir_name": bad},
            )
            assert res.status_code == 400, f"dir_name={bad!r}"


def test_create_dir_traversal_cannot_escape(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.post(
            "/system/create-dir",
            json={
                "parent_path": os.path.join(str(root), "..", "outside"),
                "dir_name": "x",
            },
        )
        assert res.status_code == 400
        res = client.post(
            "/system/create-dir",
            json={"parent_path": str(root), "dir_name": ".."},
        )
        assert res.status_code == 400


def test_create_dir_nonexistent_parent_validated_safely(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        new_parent = os.path.join(str(root), "brand_new", "nested")
        res = client.post(
            "/system/create-dir",
            json={"parent_path": new_parent, "dir_name": "leaf"},
        )
        assert res.status_code == 200
        assert os.path.isdir(os.path.join(new_parent, "leaf"))


def test_browse_symlink_outside_rejected(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    target = tmp_path / "outside_target"
    target.mkdir()
    link = root / "link_out"
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not supported in this environment")
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        res = client.get(f"/system/browse-dir?path={link}")
        assert res.status_code == 400
        res = client.post(
            "/system/create-dir",
            json={"parent_path": str(link), "dir_name": "child"},
        )
        assert res.status_code == 400


def test_select_folder_outside_rejected(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        _mock_dialog(monkeypatch, str(outside))
        res = client.post("/system/select-folder-dialog")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "REJECTED"
        assert body["selected_path"] == ""


def test_select_folder_inside_succeeds(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        _mock_dialog(monkeypatch, str(root))
        res = client.post("/system/select-folder-dialog")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "SUCCESS"
        assert body["selected_path"] == str(root)


def test_select_folder_cancelled(monkeypatch, tmp_path):
    root = tmp_path / "CTF"
    root.mkdir()
    with _patch_workspace(str(root)):
        client = _make_client(str(root))
        _mock_dialog(monkeypatch, None)
        res = client.post("/system/select-folder-dialog")
        assert res.status_code == 200
        assert res.json()["status"] == "CANCELLED"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
