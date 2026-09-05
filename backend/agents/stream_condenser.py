import re
import logging
from typing import List

logger = logging.getLogger("forge.stream_condenser")

class StreamCondenser:
    """Output distillation middleware between tool execution and LLM context injection."""

    INTERESTING_STATUSES = ["200", "301", "302", "403", "500"]
    INTERESTING_EXTENSIONS = [".php", ".bak", ".txt", ".sql", ".env", ".git", ".flag", ".py", ".sh", ".json", ".db", ".key", ".conf"]
    FLAG_PATTERNS = [r"(?:picoCTF|FLAG|CTF|HTB|THM)\{[^\}\s]+\}"]

    @classmethod
    def condense_output(cls, tool_name: str, raw_output, max_lines: int = 25):
        """Distill verbose command output into high-value signal lines capped at max_lines."""
        if raw_output is None:
            return None
        if not raw_output or len(raw_output.splitlines()) <= max_lines:
            return raw_output

        lines = raw_output.splitlines()
        original_line_count = len(lines)
        tool_lower = tool_name.lower()

        extracted_lines: List[str] = []

        # 1. ALWAYS extract any flag matches first
        for line in lines:
            for pat in cls.FLAG_PATTERNS:
                if re.search(pat, line, re.IGNORECASE):
                    if line.strip() not in extracted_lines:
                        extracted_lines.append(f"🚩 FLAG FOUND: {line.strip()}")

        # 2. Tool-specific intelligent filtering
        if any(w in tool_lower for w in ["ffuf", "gobuster", "dirsearch", "dirb", "feroxbuster", "wfuzz"]):
            for line in lines:
                # Keep lines with Status: 200, 301, 403 or interesting files
                if any(f"status: {s}" in line.lower() or f"[{s}]" in line.lower() or f" {s} " in line for s in cls.INTERESTING_STATUSES):
                    if line.strip() not in extracted_lines:
                        extracted_lines.append(line.strip())
                elif any(ext in line.lower() for ext in cls.INTERESTING_EXTENSIONS):
                    if line.strip() not in extracted_lines:
                        extracted_lines.append(line.strip())

        elif any(w in tool_lower for w in ["nmap", "rustscan", "masscan"]):
            for line in lines:
                # Keep open ports, service versions, and OS fingerprints
                if "open" in line.lower() or "service" in line.lower() or "os details" in line.lower() or "running:" in line.lower():
                    if line.strip() not in extracted_lines:
                        extracted_lines.append(line.strip())

        elif any(w in tool_lower for w in ["binwalk", "foremost", "strings", "readelf"]):
            for line in lines:
                if any(kw in line.lower() for kw in ["elf", "gzip", "zip", "pk", "certificate", "private key", "flag", "password", "root", "secret"]):
                    if line.strip() not in extracted_lines:
                        extracted_lines.append(line.strip())

        elif any(w in tool_lower for w in ["checksec", "ropgadget", "radare2", "ghidra"]):
            for line in lines:
                if any(kw in line.lower() for kw in ["canary", "pie", "nx", "relro", "ret", "pop rdi", "system", "win", "/bin/sh"]):
                    if line.strip() not in extracted_lines:
                        extracted_lines.append(line.strip())

        # 3. Fallback generic distillation if tool-specific matched too few lines
        if len(extracted_lines) < 3:
            for line in lines:
                l_lower = line.lower()
                if any(kw in l_lower for kw in ["found", "success", "error", "vulnerable", "warning", "endpoint", "url", "200 ok"]):
                    if line.strip() not in extracted_lines:
                        extracted_lines.append(line.strip())

        # If still nothing specific, grab the first 10 lines + last 10 lines
        if not extracted_lines:
            extracted_lines = [l.strip() for l in lines[:10] + lines[-10:] if l.strip()]

        # Deduplicate and cap to max_lines
        final_lines = list(dict.fromkeys(extracted_lines))[:max_lines]

        summary_header = f"[STREAM CONDENSER: Distilled {original_line_count} raw lines -> {len(final_lines)} high-value lines]"
        return f"{summary_header}\n" + "\n".join(final_lines)

stream_condenser = StreamCondenser()
