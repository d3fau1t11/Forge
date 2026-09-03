import sys
import shutil
import platform
try:
    import psutil
except ImportError:
    psutil = None
from typing import Dict, List, Any

class EnvironmentDetector:
    """Discovers host operating system, hardware resources, and installed security tools."""

    @staticmethod
    def detect_environment() -> Dict[str, Any]:
        system_os = platform.system().lower() # 'linux', 'windows', 'darwin'
        
        distro_name = "Unknown"
        if system_os == "linux":
            try:
                import os
                if os.path.exists("/etc/os-release"):
                    with open("/etc/os-release") as f:
                        lines = f.readlines()
                        for line in lines:
                            if line.startswith("PRETTY_NAME="):
                                distro_name = line.split("=")[1].strip().strip('"')
            except Exception:
                pass
        elif system_os == "windows":
            distro_name = f"Windows {platform.release()} ({platform.version()})"

        # Check installed CLI security tools
        common_tools = ["nmap", "ffuf", "gobuster", "feroxbuster", "dirsearch", "curl", "httpx", "strings", "binwalk", "7z", "unzip", "gdb", "radare2", "ghidra", "python3"]
        installed_tools = {}
        for tool in common_tools:
            path = shutil.which(tool)
            installed_tools[tool] = {
                "installed": path is not None,
                "path": path or ""
            }

        cpu_count = psutil.cpu_count(logical=True) if psutil else 1
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 2) if psutil else 0.0

        return {
            "os": system_os,
            "distro": distro_name,
            "architecture": platform.machine(),
            "python_version": sys.version.split()[0],
            "cpu_cores": cpu_count,
            "ram_gb": ram_gb,
            "installed_tools": installed_tools,
            "is_parrot_os": "parrot" in distro_name.lower(),
            "is_kali_linux": "kali" in distro_name.lower()
        }

environment_detector = EnvironmentDetector()
