import sys
import os
import asyncio
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.providers.cli.claude_code import AgentRouterClaudeCodeProvider
from backend.providers.cli.codex import AgentRouterCodexProvider
from backend.providers.cli.base import redact_secrets

async def run_diagnostics():
    print("==================================================")
    print("       AgentRouter CLI Provider Diagnostic        ")
    print("==================================================\n")

    claude_prov = AgentRouterClaudeCodeProvider()
    codex_prov = AgentRouterCodexProvider()

    # 1. Claude Code CLI Diagnostic
    claude_installed = await claude_prov.is_available()
    claude_exe = claude_prov.find_executable() or "NONE"
    claude_ver = await claude_prov.get_version() if claude_installed else "N/A"
    claude_key = claude_prov.resolve_api_key("claude-opus-5")

    print("Claude Code")
    print("------------")
    print(f"Installed                   : {'YES' if claude_installed else 'NO'}")
    print(f"Version                     : {claude_ver}")
    print(f"Executable                  : {claude_exe}")
    print(f"Configuration               : {'VALID' if claude_key else 'INVALID (No key in env)'}")
    print(f"AgentRouter Authentication  : {'CONFIGURED' if claude_key else 'MISSING'}")

    claude_status = "UNKNOWN"
    if claude_installed and claude_key:
        print("\nTesting Inference via Claude Code Subprocess...")
        start_t = time.time()
        res = await claude_prov.generate_response(
            prompt="Reply with the text 'AGENTROUTER_CLI_OK' if you can read this.",
            model="claude-opus-5",
            timeout_seconds=30
        )
        latency = round(time.time() - start_t, 2)
        if not res.is_refusal:
            print(f"Inference                   : PASS")
            print(f"Latency                     : {latency}s")
            clean_print_content = res.content[:60].replace("\n", " ").encode("ascii", "replace").decode("ascii")
            print(f"Output                      : {clean_print_content}")
            claude_status = "PASS"
        else:
            print(f"Inference                   : FAIL ({res.refusal_reason[:80]}...)")
            claude_status = "DEGRADED / REFUSAL"
    else:
        claude_status = "CLI_NOT_INSTALLED" if not claude_installed else "AUTH_FAILED"

    print(f"Status                      : {claude_status}\n")

    # 2. Codex CLI Diagnostic
    codex_installed = await codex_prov.is_available()
    codex_exe = codex_prov.find_executable() or "NONE"
    codex_ver = await codex_prov.get_version() if codex_installed else "N/A"
    codex_key = codex_prov.resolve_api_key("deepseek-v4-flash")

    print("Codex")
    print("------------")
    print(f"Installed                   : {'YES' if codex_installed else 'NO'}")
    print(f"Version                     : {codex_ver}")
    print(f"Executable                  : {codex_exe}")
    print(f"Configuration               : {'VALID' if codex_key else 'INVALID (No key in env)'}")
    print(f"AgentRouter Authentication  : {'CONFIGURED' if codex_key else 'MISSING'}")
    codex_status = "CLI_NOT_INSTALLED" if not codex_installed else ("PASS" if codex_key else "AUTH_FAILED")
    print(f"Status                      : {codex_status}\n")

    # 3. Overall Diagnostic Result
    print("--------------------------------------------------")
    if claude_status == "PASS" or codex_status == "PASS":
        print("Overall Status              : PASS")
    elif claude_installed:
        print("Overall Status              : PARTIAL (CLI Installed, API Fallback Active)")
    else:
        print("Overall Status              : FAIL")
    print("--------------------------------------------------\n")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "agentrouter-cli":
        asyncio.run(run_diagnostics())
    else:
        asyncio.run(run_diagnostics())
