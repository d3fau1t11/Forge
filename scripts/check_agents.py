import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.config import settings
from backend.providers.router import model_router
from backend.providers.quota_manager import quota_manager
from backend.providers.cli.claude_code import AgentRouterClaudeCodeProvider
from backend.providers.cli.codex import AgentRouterCodexProvider
from backend.environment.detector import environment_detector

async def run_check():
    print("=" * 60)
    print("           FORGE AI AGENTS & PROVIDERS STATUS CHECK         ")
    print("=" * 60)

    # 1. Environment & Binaries
    print("\n[1] LOCAL CLI BINARIES:")
    claude_prov = AgentRouterClaudeCodeProvider()
    codex_prov = AgentRouterCodexProvider()

    claude_avail = await claude_prov.is_available()
    claude_exe = claude_prov.find_executable()
    claude_ver = await claude_prov.get_version() if claude_avail else "Not available"
    print(f"  * Claude Code CLI: {'AVAILABLE' if claude_avail else 'NOT FOUND'} (v{claude_ver}) -> {claude_exe}")

    codex_avail = await codex_prov.is_available()
    codex_exe = codex_prov.find_executable()
    codex_ver = await codex_prov.get_version() if codex_avail else "Not available"
    print(f"  * Codex CLI      : {'AVAILABLE' if codex_avail else 'NOT FOUND'} ({codex_ver}) -> {codex_exe}")

    # 2. Configured Providers & Keys
    print("\n[2] PROVIDER API KEYS & REGISTRY:")
    for name, prov in model_router.providers.items():
        avail = await prov.is_available()
        status_str = "READY" if avail else "NO_KEY / DISABLED"
        print(f"  * Provider '{name:24}': {status_str}")

    # 3. AgentRouter Quota Status
    print("\n[3] AGENTROUTER QUOTA STATUS:")
    q_summary = quota_manager.get_quota_status_summary()
    print(f"  * Global Quota Exhausted: {q_summary.get('is_exhausted_globally')}")
    print(f"  * Next Batch Replenish : {q_summary.get('next_batch_time_utc')}")
    print(f"  * Batch Schedule       : {q_summary.get('batch_schedule')}")

    # 4. Live Router Request Dispatch Test
    print("\n[4] LIVE AGENT REASONING TEST:")
    try:
        t0 = time.time()
        res = await model_router.route_request(
            prompt="Analyze this port scan: Port 80/tcp open (nginx), Port 22/tcp open (OpenSSH 8.9). Provide a 1-sentence assessment.",
            capability="web_analysis"
        )
        elapsed = round(time.time() - t0, 2)
        print(f"  * Routed Provider : {res.provider_name}")
        print(f"  * Model Used      : {res.model_name}")
        print(f"  * Latency         : {elapsed}s")
        print(f"  * Is Refusal      : {res.is_refusal}")
        if res.content:
            clean_out = res.content.strip().replace("\n", " ")[:120]
            print(f"  * Output Preview  : {clean_out}...")
        elif res.refusal_reason:
            print(f"  * Refusal Reason  : {res.refusal_reason[:120]}")
    except Exception as e:
        print(f"  * Router Execution Error: {e}")

    print("\n" + "=" * 60)
    print("CHECK COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_check())
