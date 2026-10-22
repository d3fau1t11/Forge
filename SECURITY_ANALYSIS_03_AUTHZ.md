# FORGE Security Analysis 03 — API Authentication and Authorization

**Status:** Read-only analysis. No files were modified, staged, or committed. This report prepares a separate, narrowly scoped implementation prompt.

---

## 1. Repository State and Working-Tree Preservation

| Item | Value |
|------|-------|
| Branch | `main` |
| HEAD | `bb30ff4d9e25d6e613588bc331d6e2d750878ed3` |
| HEAD subject | "Confine directory access and gate LAN binding" |
| `git status --short` | empty (working tree clean) |
| `git diff --stat` | empty |
| Untracked files (excl. standard ignores) | none |

**Discrepancy vs. task expectation:** The task anticipated that Fix 01 (workspace containment) and Fix 02 (launcher host selection) would be *uncommitted*. They are not — both are already committed in `bb30ff4`, which touched exactly the expected files (`backend/api/routes.py`, `backend/utils/workspace.py`, `tests/test_workspace_safety.py`, `tests/test_directory_endpoints.py`, `launch_forge.py`, `tests/test_launcher_host_selection.py`). The working tree is clean. All existing changes are preserved; nothing was reset, restored, reverted, staged, or overwritten during this analysis.

**Secret note:** `.env` is git-ignored (`.gitignore:50`) and is **not** in the repository. It exists locally and contains live provider API keys (Gemini, NVIDIA, AgentRouter, OpenRouter, Cloudflare, RapidAPI, Groq, Mistral, XKIRO, CleanAPIs — `.env:4-20`). `.env.example` is tracked and contains no real keys.

---

## 2. Confirmed Findings

### 2.1 No authentication exists anywhere in the request path (Critical)

**Backend middleware** — `backend/main.py:33-39`: the *only* middleware registered is CORS:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
There is no authentication middleware, no session middleware, and no `before_request`/auth hook.

**Route dependencies** — `backend/api/routes.py`: all 35 `Depends(...)` calls are `Depends(get_db)` only (lines 207, 211, 218, 225, 262, 292, 464, 496, 512, 523, 569, 581, 594, 613, 617, 624, 638, 651, 675, 679, 731, 735, 757, 863, 874, 890, 1028, 1032, 1208, 1212, 1227, 1231, 1247, 1251, 1255). **Zero** are auth dependencies. There is no OAuth2, no `HTTPBearer`, no API-key dependency, and no `get_current_user`/`get_current_active_user` anywhere in the backend (grep over `backend/**/*.py` returned no auth-matching symbols).

**Conclusion:** Every HTTP route in `backend/api/routes.py` is reachable with no credentials whatsoever.

### 2.2 No authorization per endpoint or per action (Critical)

There is no role model, no user model, no scope check, and no action-level gate. The privilege layer (`backend/privilege/manager.py:8-39`) decides `SAFE` vs `PRIVILEGED` vs `DANGEROUS` for *agent* tool execution, but the HTTP endpoints that approve/reject those decisions (`/privilege/decision`, `/privilege/approve`, `/privilege/reject`) perform no caller-identity check. `update_system_settings` (`routes.py:1275`) mutates `settings.PAID_MODEL_ALLOWED`, `DAILY_BUDGET_USD`, `AUTO_APPROVE_PRIVILEGED`, etc. with no authorization.

### 2.3 Sensitive endpoints callable without authentication (Critical)

All of the following are reachable unauthenticated, with exact locations in `backend/api/routes.py`:

**Arbitrary / privileged code execution**
- `POST /terminal/execute` (line 913) — runs an arbitrary operator-supplied shell command via `execution_service.run_command`. No `working_directory` containment beyond the caller-supplied string.
- `POST /privilege/approve` (line 1406) — executes a command with `sudo` elevation (`tool_manager.execute_raw_command`), optionally embedding a caller-supplied `sudo_password` into `echo {pw} | sudo -S ...` (line 1418).
- `POST /package/install` (line 1316) — runs `pip install <package_name>` as a subprocess (line 1336).
- `POST /tools/execute` (line 908) — executes a named tool capability against a target.

**Data destruction / run control**
- `DELETE /challenges/{challenge_id}` (line 224) — deletes a challenge, its runs/evidence/findings/reports, and its working directory on disk.
- `DELETE /challenges` (line 261) — deletes **all** challenges and all working directories.
- `POST /challenges/{id}/pause` (496), `/resume` (512), `/continue` (523) — run lifecycle control.
- `POST /killswitch` (line 1295) — activates the emergency kill switch for a run.

**Configuration / credential mutation**
- `POST /system/settings` (line 1275) — mutates `PAID_MODEL_ALLOWED`, `DAILY_BUDGET_USD`, `SESSION_BUDGET_USD`, `AUTO_APPROVE_PRIVILEGED`, `EXECUTION_MODE`, `DEFAULT_STRATEGY`.
- `POST /providers/register-snippet` (line 1123) — accepts and registers a raw `api_key` into `model_router` (returns it masked only in the response).
- `POST /providers/update-key` (line 1183) — overwrites a provider's live `api_key` in memory.
- `POST /privilege/decision` (line 1031) — approves/rejects audit-log privilege decisions by `audit_id`.

**File-system access**
- `POST /challenges/upload` (line 438) — staged file upload into `CTF_WORKSPACE_ROOT/_uploads`.
- `POST /system/create-dir` (line 1546), `GET /system/browse-dir` (line 1486), `POST /system/select-folder-dialog` (line 1580) — workspace-bounded but still unauthenticated directory operations.

**Data exposure (read)**
- `GET /audit-logs` (1255), `GET /tools/executions` (757 — includes `command`, `stdout`, `stderr`, `privilege_level`), `GET /evidence` (1207), `GET /findings` (1226), `GET /reports` (1246), `GET /knowledge` (1250), `GET /privilege/pending` (1028), `GET /system/settings` (1262 — budgets, paid_model_allowed, auto_approve), `GET /providers` (1051), `GET /providers/health` (1084 — budget/spent/quota state), `GET /memory/*` (1835+), `GET /swarm/*` (862+ — shared state, evidence), `GET /health` (185), `GET /environment` (198), `GET /challenges/*`, `GET /targets/*`, `GET /runs`.

### 2.4 WebSocket connections are unauthenticated and unathorized (Critical)

`backend/main.py:93-102`:
```python
@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    ...
```
`backend/websocket/manager.py:10-12` — `connect()` unconditionally `await websocket.accept()` and appends the socket to a list. There is **no** query-param token, no subprotocol check, no `Depends`, and no authorization before `accept()`. Once connected, a client receives **every** broadcast (`ws_manager.broadcast`, manager.py:18-23) — including `CHALLENGE_CREATED`, `LOG_OUTPUT` (terminal command + output), `ROOT_PERMISSION_RESULT`, `PRIVILEGE_DECISION_UPDATED`, `KILL_SWITCH_ACTIVATED`, and `PACKAGE_INSTALL_RESULT` — and can send messages that the endpoint echoes back (main.py:98-100).

### 2.5 Frontend has no reusable session, token, or CSRF mechanism (Critical)

`frontend/src/services/api.ts`:
- `API_BASE_URL = 'http://localhost:8000/api'` (line 3); `WS_BASE_URL = 'ws://localhost:8000/ws/events'` (line 4).
- Every `fetch` is bare — no `Authorization` header, no `credentials`, no CSRF/XSRF token, no custom auth header (confirmed by reading all 691 lines; grep for `localStorage|sessionStorage|token|Authorization|auth|csrf|xsrf|credentials` found no client-side auth storage or header).
- `connectWebSocket` (line 14) opens `new WebSocket(WS_BASE_URL)` with no token.

**There is nothing in the frontend to reuse.** A bearer-token/session scheme would have to be added to both the backend dependency and the frontend client.

### 2.6 Privilege approvals and the kill switch interact with endpoint access by being fully open (Critical)

The privilege manager (`backend/privilege/manager.py`) only governs whether an *agent* may auto-execute `SAFE` tools vs. pend `PRIVILEGED`/`DANGEROUS` ones. The *operator-facing* HTTP gates (`/privilege/approve`, `/privilege/reject`, `/privilege/decision`) and the kill switch (`/killswitch`) carry no caller authentication. An unauthenticated caller can therefore:
- Approve a `sudo` command (`/privilege/approve`, line 1406) and have it executed on the host.
- Flip pending audit decisions (`/privilege/decision`, line 1031).
- Kill any run (`/killswitch`, line 1295).

### 2.7 What is exposed through unauthenticated endpoints (High)

- **Provider credentials:** accepted/mutated via `/providers/register-snippet` and `/providers/update-key`; live keys reside in `.env` (local only, git-ignored) and are loaded into `settings` at startup (`backend/config.py:23-37`). Endpoints return keys masked (`api_key_masked`, routes.py:1177) but accept raw keys.
- **Settings/budgets:** read via `/system/settings`; mutated via `/system/settings` POST — including `PAID_MODEL_ALLOWED`, `DAILY_BUDGET_USD`, `SESSION_BUDGET_USD`, `AUTO_APPROVE_PRIVILEGED`.
- **Logs:** `GET /audit-logs` returns the full audit trail; `GET /tools/executions` returns command stdout/stderr (potentially sensitive command output and credentials accidentally typed on a command line).
- **Uploaded files:** `POST /challenges/upload` accepts artifacts; file paths returned can be attached to challenges.
- **Reports/artifacts/execution artifacts:** `GET /reports`, `GET /evidence`, `GET /findings`, `GET /swarm/*` (shared state + evidence), `GET /memory/*`.

### 2.8 Backend assumes loopback binding / CORS provide security — this is false (High)

**Loopback binding:** `launch_forge.py:14-15` defaults to `127.0.0.1`; `0.0.0.0` requires explicit `--lan` or `FORGE_LAN=1` (`is_lan_opt_in`, lines 18-31; `resolve_host`, lines 34-41). This limits *remote* LAN exposure but does **not** protect against:
- Same-machine attacks (a malicious website the operator visits, or any other process under the same OS account) — loopback is reachable from all of them.
- Accidental LAN exposure if `--lan` is used.

**CORS is not authorization:** `backend/utils/workspace.py:30-37` explicitly documents this:
> "Treat these endpoints as reachable only by a local, trusted frontend: they are currently unauthenticated and CORS is not authorization."

With `allow_origins=["*"], allow_credentials=True`, Starlette/FastAPI **reflects the requesting Origin** and returns `Access-Control-Allow-Credentials: true`. Consequently, *any* website the operator visits can issue cross-origin `fetch` requests to `http://localhost:8000/api/*` (including state-changing POST/DELETE with `Content-Type: application/json`, which triggers a CORS preflight that is also reflected) and **read the responses**. Loopback binding plus wildcard CORS is therefore not a security boundary.

### 2.9 Existing tests assume unauthenticated access and would need updates (Medium)

- `tests/test_workspace_safety.py:211-218` builds a `TestClient(app)` and calls routes directly with no auth.
- `tests/test_directory_endpoints.py` calls `routes.browse_directory(...)`, `routes.create_directory(...)` directly with no auth (lines 56-58, 66-68, 80-81).
- `tests/test_launcher_host_selection.py` tests launcher host selection only (no auth concept).
- README.md:211 states "All 29/29 automated backend tests pass cleanly" against the unauthenticated app.
- The frontend (`api.ts`) makes bare `fetch` calls with no auth header, so the documented local workflow (`README.md:93-125`: launch → open `http://localhost:8000`) depends on zero authentication.

---

## 3. Concrete Attack Scenarios (grounded in implementation)

1. **Arbitrary code execution via a malicious website.** Operator visits an attacker-controlled site while FORGE runs on `localhost:8000`. The site issues `fetch('http://localhost:8000/api/terminal/execute', {method:'POST', body: JSON.stringify({command: '...'})})`. Because CORS reflects `*`+credentials, the browser allows the request and the response is readable. Result: arbitrary commands run as the OS user launching FORGE (potentially elevated if `sudo` is passwordless). Endpoint: `routes.py:913`.

2. **Root/sudo execution via `/privilege/approve`.** Same malicious site posts `{request_id, command: 'rm -rf /...', sudo_password: '...'}` to `/api/privilege/approve`. `routes.py:1418` builds `echo {pw} | sudo -S <cmd>` and executes it via `tool_manager.execute_raw_command`. No caller check. Endpoint: `routes.py:1406`.

3. **Supply-chain / code execution via `/package/install`.** Attacker posts `{package_name: 'malicious-package'}` to `/api/package/install`, triggering `pip install` as a subprocess. Endpoint: `routes.py:1316`.

4. **Data exfiltration.** Attacker reads `GET /api/audit-logs`, `GET /api/tools/executions` (commands + stdout/stderr), `GET /api/system/settings` (budgets/paid-model flags), `GET /api/providers/health` (spent/quota state), and `GET /api/swarm/*` (shared state/evidence). Endpoints: `routes.py:1255, 757, 1262, 1084, 862`.

5. **Data destruction.** Attacker calls `DELETE /api/challenges` (all challenges + working directories deleted) or `DELETE /api/challenges/{id}`. Endpoints: `routes.py:261, 224`.

6. **Unauthorized WebSocket interception.** Attacker opens `ws://localhost:8000/ws/events` and receives every real-time event (terminal output, privilege decisions, kill-switch activations, challenge creation). Endpoint: `main.py:93`, `manager.py:10`.

7. **Accidental LAN exposure.** If launched with `--lan` or `FORGE_LAN=1`, the server binds `0.0.0.0` (`launch_forge.py:81-82`) and every device on the LAN reaches all the above unauthenticated endpoints. CORS wildcard further allows cross-origin access from any origin.

8. **CSRF-style state change.** Because state-changing endpoints require no token and CORS permits cross-origin requests with readable responses, any site can trigger `POST /api/killswitch`, `POST /api/system/settings`, `POST /api/privilege/approve`, etc. from the operator's browser.

---

## 4. Existing Mechanisms That Can Be Reused

| Mechanism | Location | Reusability for auth |
|-----------|----------|----------------------|
| `SECRET_KEY` setting | `backend/config.py:17` (`forge-secret-key-change-in-production`) | Can derive/issue a bearer token or session secret; currently unused for auth. |
| FastAPI `Depends` injection | `backend/api/routes.py` (35× `Depends(get_db)`) | A single `Depends(verify_session)` / `Depends(require_token)` can be added to routes or at router level. |
| `APIRouter` | `backend/api/routes.py:38` | Router-level `dependencies=[Depends(...)]` can cover all routes in one place (excluding health if desired). |
| `ws_manager.connect` | `backend/websocket/manager.py:10-12` | Add a token check (query param or subprotocol) before `websocket.accept()`. |
| Frontend `ApiService` | `frontend/src/services/api.ts` | Single point where all `fetch` calls and the WebSocket are made — add `Authorization` header and WS token here. |
| `.env` / `settings` | `backend/config.py` | Can store a generated session token or expected API key at startup. |

Installed versions (from `backend/requirements.txt`): `fastapi>=0.104.0`, `pydantic-settings>=2.1.0`, `uvicorn[standard]>=0.24.0`. `cryptography>=41.0.0` is available if token signing is desired, but no JWT library (e.g. `python-jose`, `pyjwt`) is declared. A simple opaque bearer token (random string compared via `secrets.compare_digest`) is the smallest viable approach and needs no new dependency.

---

## 5. Recommended First Fix (Bounded)

**Goal:** Smallest practical change that meaningfully reduces unauthorized access — require a single local session/bearer token on all state-changing and data-reading API routes and on the WebSocket, and have the local frontend send it. Do **not** attempt RBAC, user accounts, password login, OAuth2, HTTPS, or rate limiting in this step.

### 5.1 Precise scope

**In scope:**
- Add one authentication secret/token (derived from `SECRET_KEY` or generated at startup) and a FastAPI dependency `require_api_token` / `require_session`.
- Apply it to every route in `backend/api/routes.py` (via router-level `dependencies` or per-route `Depends`), except possibly `GET /health` (keep unauthenticated for startup/load-balancer probes — document this exception).
- Authenticate the WebSocket at `backend/main.py:93` before `ws_manager.accept()` (token via query param or `Sec-WebSocket-Protocol`).
- Update `frontend/src/services/api.ts` to include the token in every `fetch` (`Authorization: Bearer <token>`) and to pass it when opening the WebSocket.
- Obtain the token at frontend startup (e.g., the backend exposes it once via `/health` or the launcher prints it; the frontend reads it from a known local source). **Do not** embed a static token in the frontend bundle.

**Out of scope (defer to follow-ups):**
- Per-user accounts, login UI, password hashing, OAuth2, JWT issuance/rotation.
- Role-based access control (RBAC) — all local operators share the single session token.
- HTTPS/TLS, rate limiting, request signing, CSRF double-submit cookies (bearer tokens sent via explicit header are not auto-sent by browsers, which already mitigates cross-origin form CSRF).
- Workspace, provider, OmniRoute, UI redesign, agent-orchestration, or database refactoring.

### 5.2 Files and functions likely to require changes

- `backend/config.py` — add a session/token setting (e.g., `FORGE_SESSION_TOKEN` or derive from `SECRET_KEY`), loaded from `.env`/generated at startup.
- `backend/main.py` — add the auth dependency to `api_router` registration (`app.include_router(api_router, prefix="/api", dependencies=[...])`) and authenticate `websocket_endpoint` before `ws_manager.connect()`.
- `backend/api/routes.py` — if not using router-level dependencies, add `Depends(require_token)` to each route; ensure `GET /health` exception is intentional.
- `frontend/src/services/api.ts` — add `Authorization: Bearer <token>` header to all `fetch` calls; pass token to `new WebSocket(...)` (e.g., `?token=` or via subprotocol).
- `launch_forge.py` — optionally print/display the session token at startup so the operator can configure the frontend (or the frontend reads it from a local file/env).

### 5.3 Required request and WebSocket behavior

- **HTTP:** every request to `/api/*` (except `/health`) must carry `Authorization: Bearer <token>`; missing/invalid token → `401`.
- **WebSocket:** client must present the token before the server accepts; missing/invalid → close with `401` / do not `accept()`.
- **Frontend:** obtains the token once at startup from a local source and reuses it for all requests and the WebSocket connection.

### 5.4 How sensitive actions and privilege approvals should be protected

- `POST /privilege/approve`, `POST /privilege/reject`, `POST /privilege/decision`, `POST /killswitch`, `POST /package/install`, `POST /system/settings`, `DELETE /challenges*`, `POST /terminal/execute`, `POST /tools/execute`, `POST /providers/*` — all require the same session token (no per-action scope in step 1).
- The existing `PrivilegeManager.evaluate_privilege` (`SAFE` vs `PRIVILEGED`/`DANGEROUS`) remains for agent-level gating; the new token gates *caller access* to the HTTP endpoints. These are orthogonal.

### 5.5 Required tests (positive and negative)

**Positive:** each protected route returns expected data/action when a valid token is supplied; WebSocket connects and receives events when a valid token is presented.
**Negative:** each protected route returns `401` with no token, with an invalid token, and with a wrong token; WebSocket is refused/closed without a valid token; `GET /health` remains reachable without a token (if kept as the exception).
**Tests to update:** `tests/test_workspace_safety.py:211-218` (`TestClient`) and `tests/test_directory_endpoints.py` must supply the token in requests; `tests/test_launcher_host_selection.py` is unaffected (host selection only).

### 5.6 Compatibility risks and migration considerations

- The frontend is served from the same FastAPI instance (`main.py:104-124`), so same-origin `fetch` to `http://localhost:8000/api` continues to work once the header is added.
- Existing automated tests and the README workflow assume no auth; they must be updated to supply the token, otherwise they break.
- `allow_origins=["*"], allow_credentials=True` should be narrowed to the local frontend origin in a follow-up (CORS is not authorization, but the wildcard + credentials combination is actively dangerous). In step 1, the bearer token is the real control; CORS narrowing is a hardening follow-up.
- Any external scripts or tools that call `http://localhost:8000/api/*` directly will break until they include the token — document this as a breaking change for direct API callers.

### 5.7 Issues that must remain separate follow-up fixes

- CORS `allow_origins=["*"]` + `allow_credentials=True` narrowing to `http://localhost:8000` (or the actual frontend origin).
- `.env` containing live API keys on disk (git-ignored but present); endpoints that accept raw keys.
- Per-user RBAC, login, JWT/session rotation, HTTPS, rate limiting.
- WebSocket message authorization (once connected, a client receives all broadcasts; fine-grained subscribe-level auth is out of step 1).

---

## 6. Test Plan and Likely Regressions

**Test plan:**
1. Negative: `GET /api/challenges`, `POST /api/terminal/execute`, `POST /api/privilege/approve`, `DELETE /api/challenges/{id}`, `POST /api/killswitch`, `POST /api/system/settings`, WebSocket connect — all without a token → `401`/refused.
2. Positive: same calls with a valid token → `200`/expected behavior; WebSocket with valid token → connected + receives broadcast.
3. Exception: `GET /api/health` reachable without a token (if kept public).
4. Update `tests/test_workspace_safety.py` and `tests/test_directory_endpoints.py` to include the token in `TestClient` requests.

**Likely regressions:**
- All existing tests that call routes directly or via `TestClient` without a token will receive `401` and fail until updated.
- The frontend `ApiService` will fail all requests until it sends the token; the app will appear offline.
- Any CI or README examples calling `http://localhost:8000/api/*` directly will break.
- `test_fullstack.py` and other tests using the DB directly (not HTTP) are unaffected.

---

## 7. Separate Follow-Up Security Issues

- Narrow CORS `allow_origins` from `*` to the local frontend origin (currently `allow_credentials=True` + `*` reflects any origin).
- Store provider API keys more safely (not plaintext in `.env`; mask/avoid returning them; the `/providers/register-snippet` endpoint accepts raw keys).
- Per-user authentication and RBAC if multiple operators share the machine.
- WebSocket subscribe-level authorization (currently all connected clients receive all broadcasts).
- Rate limiting on sensitive endpoints (`/terminal/execute`, `/privilege/approve`, `/package/install`).
- HTTPS/TLS if the service is ever exposed beyond loopback.
- Audit-log the caller identity for privileged actions (currently `AuditLogModel.agent` is agent-supplied, not caller-authenticated).

---

## 8. Explicit Uncertainties (not resolvable from repository evidence)

1. **Token issuance/transport mechanism:** No design exists for how the frontend first obtains the session token (launcher printout? local file? `/health`?). This must be decided in the implementation prompt.
2. **Whether `GET /health` should remain unauthenticated:** The README and frontend `getHealth()` depend on it; making it authenticated would break startup diagnostics. A decision is needed.
3. **WebSocket token transport:** Query parameter vs. `Sec-WebSocket-Protocol` header vs. first-message auth — not determined; query param is simplest but logs the token in server access logs.
4. **Token lifetime/rotation:** No expiry or rotation mechanism exists; a single long-lived token is the simplest option but has trade-offs.
5. **Whether `.env` live keys should be treated as already-compromised:** They are local and git-ignored, but present on disk; whether key rotation is required is outside this analysis's scope.
6. **Whether any external tooling/scripts call the API directly and would break:** Not observable from the repo; this is a migration risk to confirm with the team.

---

**End of report.** No fix has been implemented. The next step is a separate review and implementation decision based on this report.
