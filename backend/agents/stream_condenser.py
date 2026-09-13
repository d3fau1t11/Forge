import re
import logging
from typing import List

logger = logging.getLogger("forge.stream_condenser")

class StreamCondenser:
    """Output distillation middleware between tool execution and LLM context injection."""

    INTERESTING_STATUSES = ["200", "301", "302", "403", "500"]
    INTERESTING_EXTENSIONS = [".php", ".bak", ".txt", ".sql", ".env", ".git", ".flag", ".py", ".sh", ".json", ".db", ".key", ".conf"]
    FLAG_PATTERNS = [r"(?:picoCTF|FLAG|CTF|HTB|THM)\{[^\}\s]+\}"]

    @staticmethod
    def _is_structured_ascii(lines: List[str]) -> bool:
        """Detect whether lines form a structured ASCII art, grid, binary matrix, or layout block."""
        if not lines:
            return False
        grid_lines = 0
        symbols = set("#*+|-_[]01\\/█░▒▓:.=")
        for line in lines:
            s = line.strip()
            if len(s) >= 4 and sum(1 for c in s if c in symbols) / max(len(s), 1) >= 0.45:
                grid_lines += 1
        return grid_lines >= 3

    @classmethod
    def condense_output(cls, tool_name: str, raw_output, max_lines: int = 25):
        """Distill verbose command output into high-value signal lines capped at max_lines,
        preserving whitespace/column layout for structured ASCII/matrix data."""
        if raw_output is None:
            return None
        if not raw_output or len(raw_output.splitlines()) <= max_lines:
            return raw_output

        lines = raw_output.splitlines()
        original_line_count = len(lines)
        tool_lower = tool_name.lower()

        # If the output is a structured ASCII block / binary matrix, preserve contiguous block with formatting
        if cls._is_structured_ascii(lines):
            # Preserve head of structured grid / layout without stripping leading indentation
            preserved = [l.rstrip() for l in lines[:max_lines] if l.strip()]
            summary_header = f"[STREAM CONDENSER: Preserved {len(preserved)} structured layout lines from {original_line_count} raw lines]"
            return f"{summary_header}\n" + "\n".join(preserved)

        extracted_lines: List[str] = []

        # 1. ALWAYS extract any flag matches first
        for line in lines:
            for pat in cls.FLAG_PATTERNS:
                if re.search(pat, line, re.IGNORECASE):
                    if line.rstrip() not in extracted_lines:
                        extracted_lines.append(f"🚩 FLAG FOUND: {line.strip()}")

        # 2. Tool-specific intelligent filtering
        if any(w in tool_lower for w in ["ffuf", "gobuster", "dirsearch", "dirb", "feroxbuster", "wfuzz"]):
            for line in lines:
                # Keep lines with Status: 200, 301, 403 or interesting files
                if any(f"status: {s}" in line.lower() or f"[{s}]" in line.lower() or f" {s} " in line for s in cls.INTERESTING_STATUSES):
                    if line.rstrip() not in extracted_lines:
                        extracted_lines.append(line.rstrip())
                elif any(ext in line.lower() for ext in cls.INTERESTING_EXTENSIONS):
                    if line.rstrip() not in extracted_lines:
                        extracted_lines.append(line.rstrip())

        elif any(w in tool_lower for w in ["nmap", "rustscan", "masscan"]):
            for line in lines:
                # Keep open ports, service versions, and OS fingerprints
                if "open" in line.lower() or "service" in line.lower() or "os details" in line.lower() or "running:" in line.lower():
                    if line.rstrip() not in extracted_lines:
                        extracted_lines.append(line.rstrip())

        elif any(w in tool_lower for w in ["binwalk", "foremost", "strings", "readelf"]):
            for line in lines:
                if any(kw in line.lower() for kw in ["elf", "gzip", "zip", "pk", "certificate", "private key", "flag", "password", "root", "secret"]):
                    if line.rstrip() not in extracted_lines:
                        extracted_lines.append(line.rstrip())

        elif any(w in tool_lower for w in ["checksec", "ropgadget", "radare2", "ghidra"]):
            for line in lines:
                if any(kw in line.lower() for kw in ["canary", "pie", "nx", "relro", "ret", "pop rdi", "system", "win", "/bin/sh"]):
                    if line.rstrip() not in extracted_lines:
                        extracted_lines.append(line.rstrip())

        # 3. Web / HTML / Script Output Distillation
        for line in lines:
            l_lower = line.lower()
            if any(kw in l_lower for kw in [
                "<!--", "-->", "//", "/*", "*/", "rot13", "base64", "bypass", "header", "cookie",
                "x-", "auth", "token", "jwt", "secret", "dev", "debug", "<form", "<input", "fetch(",
                "post", "get", "api", "endpoint", "admin", "password", "email", "username", "flag"
            ]):
                if line.rstrip() not in extracted_lines:
                    extracted_lines.append(line.rstrip())

        # 4. Fallback generic distillation if tool-specific matched too few lines
        if len(extracted_lines) < 3:
            for line in lines:
                l_lower = line.lower()
                if any(kw in l_lower for kw in ["found", "success", "error", "vulnerable", "warning", "endpoint", "url", "200 ok"]):
                    if line.rstrip() not in extracted_lines:
                        extracted_lines.append(line.rstrip())

        # If still nothing specific, grab the first 12 lines + last 12 lines
        if not extracted_lines:
            extracted_lines = [l.rstrip() for l in lines[:12] + lines[-12:] if l.strip()]

        # Deduplicate and cap to max_lines
        final_lines = list(dict.fromkeys(extracted_lines))[:max_lines]

        summary_header = f"[STREAM CONDENSER: Distilled {original_line_count} raw lines -> {len(final_lines)} high-value lines]"
        return f"{summary_header}\n" + "\n".join(final_lines)

stream_condenser = StreamCondenser()
