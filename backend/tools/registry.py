from typing import Dict, List, Any, Optional
from pydantic import BaseModel

class ToolMetadata(BaseModel):
    tool_name: str
    capabilities: List[str]
    binary: str
    version_command: str
    installation_recipe: str
    os_compatibility: List[str] # ["linux", "windows", "darwin"]
    privilege_requirement: str # SAFE, PRIVILEGED, DANGEROUS
    risk_level: str
    timeout_seconds: int = 60
    args_template: str

class ToolRegistry:
    """Capability-aware Tool Registry holding pre-approved cybersecurity tools."""

    def __init__(self):
        self.tools: Dict[str, ToolMetadata] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        # Recon / Directory Enumeration
        self.register_tool(ToolMetadata(
            tool_name="nmap",
            capabilities=["network_scanning", "recon"],
            binary="nmap",
            version_command="nmap --version",
            installation_recipe="sudo apt-get install -y nmap",
            os_compatibility=["linux", "windows"],
            privilege_requirement="SAFE",
            risk_level="LOW",
            timeout_seconds=120,
            args_template="{target} -sV -F"
        ))

        self.register_tool(ToolMetadata(
            tool_name="ffuf",
            capabilities=["directory_enumeration", "web_testing"],
            binary="ffuf",
            version_command="ffuf -V",
            installation_recipe="sudo apt-get install -y ffuf",
            os_compatibility=["linux", "windows"],
            privilege_requirement="SAFE",
            risk_level="LOW",
            timeout_seconds=90,
            args_template="-u {target}/FUZZ -w /usr/share/seclists/Discovery/Web-Content/common.txt"
        ))

        self.register_tool(ToolMetadata(
            tool_name="curl",
            capabilities=["web_testing", "recon"],
            binary="curl",
            version_command="curl --version",
            installation_recipe="sudo apt-get install -y curl",
            os_compatibility=["linux", "windows"],
            privilege_requirement="SAFE",
            risk_level="SAFE",
            timeout_seconds=30,
            args_template="-i -s {target}"
        ))

        # Binary / File Analysis
        self.register_tool(ToolMetadata(
            tool_name="strings",
            capabilities=["file_analysis", "reverse_engineering"],
            binary="strings",
            version_command="strings --version",
            installation_recipe="sudo apt-get install -y binutils",
            os_compatibility=["linux", "windows"],
            privilege_requirement="SAFE",
            risk_level="SAFE",
            timeout_seconds=30,
            args_template="-n 8 {target}"
        ))

        self.register_tool(ToolMetadata(
            tool_name="binwalk",
            capabilities=["file_analysis", "forensics"],
            binary="binwalk",
            version_command="binwalk --help",
            installation_recipe="sudo apt-get install -y binwalk",
            os_compatibility=["linux"],
            privilege_requirement="SAFE",
            risk_level="SAFE",
            timeout_seconds=60,
            args_template="-e {target}"
        ))

    def register_tool(self, tool: ToolMetadata):
        self.tools[tool.tool_name] = tool

    def get_tools_for_capability(self, capability: str) -> List[ToolMetadata]:
        return [t for t in self.tools.values() if capability in t.capabilities]

tool_registry = ToolRegistry()
