import os
import sys
import subprocess
import uvicorn

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

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

    free_port(8000)

    print("[+] Launching unified FORGE server on http://localhost:8000 ...")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()
