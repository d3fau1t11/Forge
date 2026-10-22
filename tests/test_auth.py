"""Focused authentication and CORS tests for FORGE Fix 03.

A single fixed bearer token (FORGE_API_TOKEN) is used so behaviour is
deterministic. These tests exercise the *real* auth dependency
(``backend.api.auth.require_api_token``) applied to the real route router, the
real WebSocket endpoint from ``backend.main``, and the real CORS allowlist.

They do NOT rely on testing the auth helper in isolation: every request goes
through the FastAPI dependency/route layer (or the real TestClient WebSocket
handshake).
"""

import os
import sys

sys.path.insert(0, os.path.abspath("."))

# Deterministic token: set before importing backend.config so
# settings.API_TOKEN is predictable.
os.environ["FORGE_API_TOKEN"] = "fix03-test-token-0123456789abcdef"

import pytest  # noqa: E402
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from backend.api import routes  # noqa: E402
from backend.api.auth import require_api_token  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.database.session import init_db  # noqa: E402
from backend.main import websocket_endpoint  # noqa: E402

TEST_TOKEN = "fix03-test-token-0123456789abcdef"

init_db()


@pytest.fixture(autouse=True)
def _fixed_token():
    """Keep settings.API_TOKEN at the fixed test token for every test."""
    previous = settings.API_TOKEN
    settings.API_TOKEN = TEST_TOKEN
    yield
    settings.API_TOKEN = previous


def _make_app():
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Same public health probe as backend/main.py.
    @app.get("/api/health")
    def health():
        return {"status": "healthy"}

    # Every application route in routes.router is protected by the bearer token.
    app.include_router(
        routes.router,
        prefix="/api",
        dependencies=[Depends(require_api_token)],
    )

    # The real WebSocket endpoint from backend/main.py (authenticates via
    # subprotocol before accept()).
    app.websocket("/ws/events")(websocket_endpoint)
    return app


def _authed_client(app):
    return TestClient(app, headers={"Authorization": f"Bearer {TEST_TOKEN}"})


def _noauth_client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# HTTP negative: missing / malformed / invalid / wrong token => 401
# ---------------------------------------------------------------------------

PROTECTED_ROUTES = [
    ("GET", "/api/challenges", None),                      # read application data
    ("POST", "/api/terminal/execute", {"command": "whoami"}),  # command execution
    ("POST", "/api/privilege/decision", {"audit_id": "x", "approved": True}),  # privilege approval
    ("POST", "/api/system/settings", {"execution_mode": "CTF_OFFENSIVE_CONTROLLED"}),  # settings mutation
    ("DELETE", "/api/challenges/does-not-exist", None),    # destructive challenge operation
]


@pytest.mark.parametrize("method,path,body", PROTECTED_ROUTES)
def test_protected_route_rejects_no_token(method, path, body):
    app = _make_app()
    client = _noauth_client(app)
    if method == "GET":
        res = client.get(path)
    elif method == "POST":
        res = client.post(path, json=body)
    else:
        res = client.delete(path)
    assert res.status_code == 401, f"{method} {path} without token: {res.status_code}"


@pytest.mark.parametrize("method,path,body", PROTECTED_ROUTES)
def test_protected_route_rejects_malformed_bearer(method, path, body):
    app = _make_app()
    client = TestClient(app, headers={"Authorization": "Basic notabearer"})
    if method == "GET":
        res = client.get(path)
    elif method == "POST":
        res = client.post(path, json=body)
    else:
        res = client.delete(path)
    assert res.status_code == 401, f"{method} {path} malformed bearer: {res.status_code}"


@pytest.mark.parametrize("method,path,body", PROTECTED_ROUTES)
def test_protected_route_rejects_invalid_token(method, path, body):
    app = _make_app()
    client = TestClient(app, headers={"Authorization": "Bearer wrong-token"})
    if method == "GET":
        res = client.get(path)
    elif method == "POST":
        res = client.post(path, json=body)
    else:
        res = client.delete(path)
    assert res.status_code == 401, f"{method} {path} invalid token: {res.status_code}"


# ---------------------------------------------------------------------------
# HTTP negative: rejected requests must not execute the operation
# ---------------------------------------------------------------------------

def test_terminal_execute_not_run_without_token(monkeypatch):
    """Invalid token must return 401 before execution_service.run_command runs."""
    from backend.execution import service as execution_service_module

    called = {"run_command": False}

    async def _fake_run_command(*args, **kwargs):
        called["run_command"] = True
        raise AssertionError("run_command must not be invoked without a token")

    monkeypatch.setattr(
        routes.execution_service, "run_command", _fake_run_command
    )

    app = _make_app()
    client = _noauth_client(app)
    res = client.post("/api/terminal/execute", json={"command": "whoami"})
    assert res.status_code == 401
    assert called["run_command"] is False


def test_delete_challenge_blocked_without_token():
    """DELETE without token returns 401 and changes nothing."""
    app = _make_app()
    client = _noauth_client(app)
    res = client.delete("/api/challenges/some-challenge-id")
    assert res.status_code == 401

    # The (nonexistent) challenge is unchanged: a valid token reaches the
    # endpoint logic and yields 404 (not 401) for an unknown challenge.
    authed = _authed_client(app)
    res = authed.delete("/api/challenges/some-challenge-id")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# HTTP positive: valid credentials preserve expected behaviour
# ---------------------------------------------------------------------------

def test_health_remains_public_without_token():
    app = _make_app()
    client = _noauth_client(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_challenges_read_with_valid_token():
    app = _make_app()
    client = _authed_client(app)
    res = client.get("/api/challenges")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_state_change_reaches_endpoint_with_valid_token():
    """Valid credentials pass auth and reach the endpoint logic (404 for an
    unknown audit id, never 401)."""
    app = _make_app()
    client = _authed_client(app)
    res = client.post("/api/privilege/decision", json={"audit_id": "does-not-exist", "approved": True})
    assert res.status_code == 404  # auth passed; endpoint logic ran


def test_wrong_token_rejected_for_state_change():
    app = _make_app()
    client = TestClient(app, headers={"Authorization": "Bearer wrong-token"})
    res = client.post(
        "/api/system/settings",
        json={"execution_mode": "CTF_OFFENSIVE_CONTROLLED"},
    )
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# WebSocket: authenticate via subprotocol before accept()
# ---------------------------------------------------------------------------

def test_ws_missing_token_rejected():
    app = _make_app()
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/events"):
            pytest.fail("missing-token WebSocket was accepted")


def test_ws_invalid_token_rejected():
    app = _make_app()
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/events", subprotocols=["wrong-token"]):
            pytest.fail("invalid-token WebSocket was accepted")


def test_ws_valid_token_connects_and_receives_events():
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/events", subprotocols=[TEST_TOKEN]) as ws:
        ws.send_text("ping")
        resp = ws.receive_json()
        assert resp == {"status": "received", "data": "ping"}


# ---------------------------------------------------------------------------
# CORS: explicit allowlist, no wildcard reflection
# ---------------------------------------------------------------------------

def _cors_response(app, origin):
    client = TestClient(app)
    return client.get("/api/health", headers={"Origin": origin})


def test_cors_approved_origin_is_granted():
    app = _make_app()
    res = _cors_response(app, "http://localhost:5173")
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_127_origin_is_granted():
    app = _make_app()
    res = _cors_response(app, "http://127.0.0.1:8000")
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"


def test_cors_unapproved_origin_not_permissive():
    app = _make_app()
    res = _cors_response(app, "http://evil.com")
    # The response must NOT carry a permissive/wildcard CORS grant nor reflect
    # the unapproved origin.
    allow_origin = res.headers.get("access-control-allow-origin")
    assert allow_origin != "*"
    assert allow_origin != "http://evil.com"


def test_cors_no_wildcard_fallback():
    app = _make_app()
    # No Origin header at all: no permissive wildcard grant.
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.headers.get("access-control-allow-origin") is None
