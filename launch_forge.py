import os
import sys
import subprocess
import uvicorn

from backend.config import settings

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

DEFAULT_HOST = "127.0.0.1"
LAN_HOST = "0.0.0.0"


def is_lan_opt_in(argv=None):
    """Return True only when LAN binding is explicitly opted into.

    Explicit opt-in paths:
      * ``--lan`` command-line flag
      * ``FORGE_LAN=1`` environment variable (exact match only)

    Missing, malformed, or any other value does NOT enable LAN binding.
    """
    if argv is None:
        argv = sys.argv[1:]
    if "--lan" in argv:
        return True
    return os.environ.get("FORGE_LAN") == "1"


def resolve_host(lan_enabled: bool = False) -> str:
    """Resolve the bind host for the Uvicorn server.

    The default is loopback (127.0.0.1). LAN binding (0.0.0.0) requires an
    explicit opt-in; a general HOST configuration never silently bypasses
    the loopback default.
    """
    return LAN_HOST if lan_enabled else DEFAULT_HOST


def free_port(port: int = 8000):
    """Frees specified port if occupied by a previous process."""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                print(f"[!] Port {port} is currently in use. Cleaning up previous server process...")
                if sys.platform == "win32":
                    subprocess.run(
                        f"powershell -Command \"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}\"",
                        shell=True
                    )
                else:
                    subprocess.run(f"fuser -k {port}/tcp", shell=True)
    except Exception:
        pass

def main():
    print("=" * 65)
    print(" FORGE -- Autonomous CTF Intelligence & Exploitation Framework")
    print("   Single System Launcher (FastAPI Backend + React Web Portal)")
    print("=" * 65)

    project_root = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(project_root, "frontend")
    dist_dir = os.path.join(frontend_dir, "dist")

    # Build frontend if dist doesn't exist
    if not os.path.exists(dist_dir):
        print("[+] Building web portal bundle...")
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        subprocess.run([npm_cmd, "run", "build"], cwd=frontend_dir, check=True)
        print("[✓] Web portal bundle ready.")

    port = settings.PORT
    free_port(port)

    lan_enabled = is_lan_opt_in()
    host = resolve_host(lan_enabled)

    print(f"[+] Launching unified FORGE server on http://{host}:{port} ...")
    uvicorn.run("backend.main:app", host=host, port=port, reload=False)

if __name__ == "__main__":
    main()
