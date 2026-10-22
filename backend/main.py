import os
import logging
import secrets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, APIRouter, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.config import settings
from backend.api.auth import require_api_token
from backend.database.session import init_db
from backend.api.routes import router as api_router
from backend.websocket.manager import ws_manager
from backend.environment.detector import environment_detector
from backend.providers.router import model_router

log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "forge.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file_path, encoding="utf-8")
    ]
)
logger = logging.getLogger("forge.main")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Autonomous CTF Intelligence & Exploitation Framework"
)

# CORS allowlist: explicit origins actually used by the frontend.
#   * http://localhost:5173  — Vite development server
#   * http://127.0.0.1:8000  — production: FastAPI serves the compiled portal
#
# `localhost` and `127.0.0.1` are NOT the same browser origin, so both are listed
# explicitly when they are used. Credentials are enabled because the frontend
# sends `Authorization: Bearer <token>` (an explicit credential) on protected
# requests; the allowlist is explicit (never a wildcard) so arbitrary origins are
# never reflected. CORS narrowing complements — it does not replace — auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _mark_stale_runs_interrupted():
    """On boot, mark runs/challenges left RUNNING by a previous session as INTERRUPTED.

    Swarms are in-memory only, so any RUNNING row at startup is a zombie from a
    crashed/stopped server. Without this sweep the Command Center keeps showing
    dead challenges as live operations (fake uptime, "RUNNING" forever).
    """
    from backend.database.session import SessionLocal
    from backend.database.models import RunModel, ChallengeModel
    db = SessionLocal()
    try:
        runs = db.query(RunModel).filter(RunModel.status == "RUNNING").all()
        challenges = db.query(ChallengeModel).filter(ChallengeModel.status == "RUNNING").all()
        for r in runs:
            r.status = "INTERRUPTED"
        for c in challenges:
            c.status = "INTERRUPTED"
        if runs or challenges:
            db.commit()
            logger.info(f"[StartupRecovery] Marked {len(runs)} stale run(s) and {len(challenges)} challenge(s) as INTERRUPTED.")
    except Exception as e:
        logger.warning(f"[StartupRecovery] Stale-run sweep failed: {e}")
    finally:
        db.close()

@app.on_event("startup")
def on_startup():
    logger.info("Initializing database tables...")
    init_db()
    _mark_stale_runs_interrupted()
    logger.info(f"{settings.PROJECT_NAME} initialized and ready.")
    # Display the active bearer token once in the local terminal so the operator
    # can enter it into the frontend. Use `print()` (stdout) rather than the
    # application logger so the token is NOT written to forge.log or any log file.
    # It is printed once per backend process, not on every request.
    print("")
    print("=" * 65)
    print(f" FORGE API bearer token: {settings.API_TOKEN}")
    print(" Enter this token in the frontend connection dialog.")
    print(" Valid for this backend process only (never stored permanently).")
    print("=" * 65)
    print("")

@app.on_event("shutdown")
async def on_shutdown():
    """Clean-process-lifecycle rule: release every FORGE-tracked interactive process
    so none survives a controlled shutdown (Phase 4.x hardening §7).

    Interactive sessions own long-lived OS processes; without this hook they would
    outlive the server and hold file locks on forge.db / logs. close_all() kills each
    tracked process tree, reaps it, and unregisters its PID — idempotent and guarded
    so a cleanup error can never block shutdown.
    """
    try:
        from backend.execution.interactive import interactive_manager
        closed = await interactive_manager.close_all(reason="app_shutdown")
        if closed:
            logger.info(f"[Shutdown] Closed {closed} tracked interactive session(s).")
    except Exception as e:
        logger.warning(f"[Shutdown] Interactive session cleanup failed: {e}")

# Public, unauthenticated readiness probe. /health exposes no application data
# (only OS/tool/budget summary) and is intentionally separated from the
# authentication-protected /api router so startup/load-balancer probes work
# before the operator has entered the backend token.
public_router = APIRouter()


@public_router.get("/health")
def health_check():
    env = environment_detector.detect_environment()
    return {
        "status": "healthy",
        "system": env["os"],
        "distro": env["distro"],
        "installed_tools_count": sum(1 for t in env["installed_tools"].values() if t["installed"]),
        "paid_models_allowed": model_router.paid_allowed,
        "daily_budget_usd": model_router.daily_budget_usd,
        "current_spent_usd": model_router.current_spent_usd
    }


app.include_router(public_router, prefix="/api")

# All application-data and state-changing /api routes require the bearer token.
# The dependency runs before any endpoint operation. /health (above) is the sole
# public exception and is deliberately not part of this dependency list.
app.include_router(
    api_router,
    prefix="/api",
    dependencies=[Depends(require_api_token)],
)

@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    # Authenticate before accept(): the client presents the bearer token as the
    # WebSocket subprotocol (Sec-WebSocket-Protocol), keeping the secret out of
    # the URL/query string and out of server access logs.
    offered_protocol = websocket.headers.get("sec-websocket-protocol", "")
    if not secrets.compare_digest(offered_protocol, settings.API_TOKEN):
        # Reject without registering the socket: it can never receive broadcasts.
        await websocket.close(code=1008, reason="Unauthorized: invalid or missing token")
        return
    await ws_manager.connect(websocket, subprotocol=offered_protocol)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle incoming client ping/messages
            await websocket.send_json({"status": "received", "data": data})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# Single System Mount: Serve compiled React Web Portal frontend directly from FastAPI
frontend_dist_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")

if os.path.exists(frontend_dist_dir):
    assets_dir = os.path.join(frontend_dist_dir, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="static_assets")

    @app.get("/")
    async def serve_root():
        return FileResponse(os.path.join(frontend_dist_dir, "index.html"))

    @app.get("/{full_path:path}")
    async def serve_frontend_spa(full_path: str):
        if full_path.startswith("api") or full_path.startswith("ws"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="API Route Not Found")
        file_path = os.path.join(frontend_dist_dir, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist_dir, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.HOST, port=settings.PORT, reload=True)
