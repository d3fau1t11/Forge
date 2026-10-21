"""Host-selection tests for the FORGE launcher (Fix 02).

Verifies that the documented launcher binds to loopback by default and that
LAN binding requires an explicit opt-in. No real network interface is bound
and uvicorn is never actually started.
"""

import sys

import pytest

import launch_forge


# ---------------------------------------------------------------------------
# resolve_host
# ---------------------------------------------------------------------------

def test_resolve_host_default_is_loopback():
    assert launch_forge.resolve_host() == "127.0.0.1"


def test_resolve_host_explicit_false_is_loopback():
    assert launch_forge.resolve_host(lan_enabled=False) == "127.0.0.1"


def test_resolve_host_lan_opt_in_is_all_interfaces():
    assert launch_forge.resolve_host(lan_enabled=True) == "0.0.0.0"


# ---------------------------------------------------------------------------
# is_lan_opt_in
# ---------------------------------------------------------------------------

def test_is_lan_opt_in_default_false(monkeypatch):
    monkeypatch.delenv("FORGE_LAN", raising=False)
    assert launch_forge.is_lan_opt_in(argv=[]) is False


def test_is_lan_opt_in_lan_flag_true():
    assert launch_forge.is_lan_opt_in(argv=["--lan"]) is True


def test_is_lan_opt_in_env_one_true(monkeypatch):
    monkeypatch.setenv("FORGE_LAN", "1")
    assert launch_forge.is_lan_opt_in(argv=[]) is True


@pytest.mark.parametrize("value", ["true", "yes", "on", "0", "2", "true1", "1 ", " 1", ""])
def test_is_lan_opt_in_malformed_env_false(monkeypatch, value):
    monkeypatch.setenv("FORGE_LAN", value)
    assert launch_forge.is_lan_opt_in(argv=[]) is False, f"FORGE_LAN={value!r} must not enable LAN"


def test_is_lan_opt_in_flag_takes_precedence_over_bad_env(monkeypatch):
    monkeypatch.setenv("FORGE_LAN", "garbage")
    assert launch_forge.is_lan_opt_in(argv=["--lan"]) is True


# ---------------------------------------------------------------------------
# Import does not start the server
# ---------------------------------------------------------------------------

def test_import_does_not_start_uvicorn(monkeypatch):
    started = []

    def fake_run(*args, **kwargs):
        started.append((args, kwargs))

    monkeypatch.setattr(launch_forge.uvicorn, "run", fake_run)
    assert started == [], "importing launch_forge must not start uvicorn"


# ---------------------------------------------------------------------------
# main() binds loopback by default, all-interfaces only on explicit opt-in
# ---------------------------------------------------------------------------

def _patch_main(monkeypatch):
    calls = []

    def fake_run(app, host, port, reload):
        calls.append((host, port, reload))

    monkeypatch.setattr(launch_forge.uvicorn, "run", fake_run)
    monkeypatch.setattr(launch_forge.os.path, "exists", lambda p: True)  # skip frontend build
    monkeypatch.setattr(launch_forge, "free_port", lambda port=8000: None)
    return calls


def test_main_defaults_to_loopback(monkeypatch):
    calls = _patch_main(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["launch_forge.py"])
    monkeypatch.delenv("FORGE_LAN", raising=False)

    launch_forge.main()

    assert calls == [("127.0.0.1", 8000, False)]


def test_main_lan_flag_binds_all_interfaces(monkeypatch):
    calls = _patch_main(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["launch_forge.py", "--lan"])
    monkeypatch.delenv("FORGE_LAN", raising=False)

    launch_forge.main()

    assert calls == [("0.0.0.0", 8000, False)]


def test_main_lan_env_one_binds_all_interfaces(monkeypatch):
    calls = _patch_main(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["launch_forge.py"])
    monkeypatch.setenv("FORGE_LAN", "1")

    launch_forge.main()

    assert calls == [("0.0.0.0", 8000, False)]


def test_main_malformed_env_does_not_bind_lan(monkeypatch):
    calls = _patch_main(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["launch_forge.py"])
    monkeypatch.setenv("FORGE_LAN", "true")

    launch_forge.main()

    assert calls == [("127.0.0.1", 8000, False)]
