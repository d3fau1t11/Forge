import re
from backend.tools.registry import tool_registry

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*;\s*:\s*\}", re.IGNORECASE),  # fork bomb
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"curl[^|]*\|\s*(sh|bash)\b", re.IGNORECASE),
    re.compile(r"wget[^|]*\|\s*(sh|bash)\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r">\s*/dev/sd", re.IGNORECASE),
    re.compile(r"\biptables\b", re.IGNORECASE),
    re.compile(r"\buserdel\b|\bpasswd\b", re.IGNORECASE),
]


def classify_command_privilege(cmd: str, bin_name: str) -> str:
    """Classify the privilege level required for a command.

    Priority order:
    1. If bin_name is in tool_registry.tools, return its privilege_requirement.
    2. Else, check cmd against dangerous regex patterns -> 'DANGEROUS'.
    3. Otherwise, return 'PRIVILEGED' (fail-closed).
    """
    if bin_name and bin_name in tool_registry.tools:
        return tool_registry.tools[bin_name].privilege_requirement

    if any(pattern.search(cmd) for pattern in DANGEROUS_PATTERNS):
        return "DANGEROUS"

    return "PRIVILEGED"
