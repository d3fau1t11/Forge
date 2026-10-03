import os
import sys
import shutil
import asyncio
import logging
from typing import Optional
from backend.providers.base import BaseProvider

logger = logging.getLogger("forge.cli_provider")

def redact_secrets(text: str, keys: Optional[list] = None) -> str:
    """Redacts secret keys from log strings and CLI output streams."""
    if not text:
        return ""
    cleaned = text
    if keys:
        for k in keys:
            if k and len(k) > 5:
                cleaned = cleaned.replace(k, "[REDACTED_API_KEY]")
    return cleaned

class BaseCLIProvider(BaseProvider):
    def __init__(self, name: str, cli_binary_default: str, custom_path: str = ""):
        super().__init__(name=name, is_paid=True)
        self.cli_binary_default = cli_binary_default
        self.custom_path = custom_path

    def find_executable(self) -> Optional[str]:
        if self.custom_path and os.path.exists(self.custom_path):
            return self.custom_path
        
        # Discover binary across PATH
        path = shutil.which(self.cli_binary_default)
        if path:
            return path

        # Platform-specific fallback locations
        if sys.platform == "win32":
            # Check npm global AppData on Windows
            npm_appdata = os.path.expanduser("~\\AppData\\Roaming\\npm\\" + self.cli_binary_default + ".cmd")
            if os.path.exists(npm_appdata):
                return npm_appdata
        else:
            local_bin = f"/usr/local/bin/{self.cli_binary_default}"
            if os.path.exists(local_bin):
                return local_bin
        return None

    async def is_available(self) -> bool:
        return self.find_executable() is not None

    async def get_version(self) -> str:
        """Return the CLI tool's version string, or 'unknown' if unavailable."""
        exe = self.find_executable()
        if not exe:
            return "not installed"
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            output = (stdout or stderr).decode(errors="replace").strip()
            # Take first line only
            return output.splitlines()[0] if output else "unknown"
        except Exception:
            return "unknown"
