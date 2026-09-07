"""End-to-end verification of the FORGE fix.

Boots the real uvicorn server, creates a challenge against an unreachable target,
and verifies:
  1. The swarm records tool executions to ToolExecutionModel (terminal history)
  2. mission_plan + progress persist to the challenge row (reload-safe todo)
  3. /api/agents returns live worker state + AGENT_UPDATE WS events flow
  4. The swarm stalls and terminates visibly (FAILED) instead of hanging at 90%
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

PORT = 8018


def wait_for_server(url, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.status
        except Exception:
            time.sleep(0.5)
    return None


async def main():
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        if wait_for_server(f"http://127.0.0.1:{PORT}/api/health") is None:
            print("SERVER FAILED TO START")
            print(proc.stdout.read().decode(errors="replace")[-3000:])
            return

        import httpx
        import websockets

        events = []

        async def listen():
            async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/events") as ws:
                print("WS CONNECTED")
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        events.append(json.loads(msg))
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        print("WS listener end:", type(e).__name__, e)
                        return

        listener = asyncio.create_task(listen())
        await asyncio.sleep(1.0)

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=120.0) as client:
            resp = await client.post("/api/challenges", json={
                "name": "VerifyFix",
                "category": "web",
                "difficulty": "EASY",
                "description": "http://127.0.0.1:9/",
                "target_address": "http://127.0.0.1:9/",
                "platform_name": "PicoCTF",
            })
            body = resp.json()
            ch_id = body.get("id")
            print("challenge:", ch_id, "| create status:", resp.status_code)

            # 1. After ~35s: check executions persisted + plan/progress + agents + events
            await asyncio.sleep(35)

            execs = await client.get(f"/api/tools/executions?challenge_id={ch_id}&limit=50")
            exec_rows = execs.json()
            print("=== PERSISTED TOOL EXECUTIONS ===")
            print("count:", len(exec_rows))
            for e in exec_rows[:5]:
                print(f"  {e['agent']} | {e['status']} | exit={e['exit_code']} | {e['command'][:70]!r} | challenge_id={e['challenge_id']}")

            ch = (await client.get("/api/challenges")).json()
            my_ch = next((c for c in ch if c["id"] == ch_id), None)
            plan = my_ch.get("mission_plan") or {}
            print("=== CHALLENGE ROW (persisted) ===")
            print("status:", my_ch.get("status"), "| progress:", my_ch.get("progress"))
            print("plan tasks:", len(plan.get("tasks", [])))
            for t in plan.get("tasks", [])[:6]:
                print(f"  [{t['status']}] {t.get('title', '')[:70]}")

            agents = (await client.get("/api/agents")).json()
            print("=== LIVE AGENTS ===")
            print("count:", len(agents))
            for a in agents[:4]:
                print(f"  {a.get('worker_id')} | {a.get('status')} | task={str(a.get('current_task'))[:50]!r} | cmds={a.get('commands_run')} | model={a.get('selected_model')}")

            # 2. Wait for the swarm to stall and terminate visibly (refills + stall)
            print("=== WAITING FOR STALL TERMINATION (up to ~4 min) ===")
            t0 = time.time()
            final_status = None
            while time.time() - t0 < 240:
                await asyncio.sleep(15)
                ch = (await client.get("/api/challenges")).json()
                my_ch = next((c for c in ch if c["id"] == ch_id), None)
                final_status = my_ch.get("status") if my_ch else None
                plan_status = (my_ch.get("mission_plan") or {}).get("status")
                print(f"  t={int(time.time()-t0)}s challenge={final_status} plan={plan_status} progress={my_ch.get('progress')}")
                if final_status in ("FAILED", "SOLVED", "COMPLETED"):
                    break

            # 3. Event inventory
            from collections import Counter
            kinds = Counter(e.get("event", e.get("type", "?")) for e in events)
            print("=== WS EVENTS ===")
            print(dict(kinds))
            print("AGENT_UPDATE count:", kinds.get("AGENT_UPDATE", 0))
            print("RUN_STALLED count:", kinds.get("RUN_STALLED", 0))

            # Cleanup
            await client.delete(f"/api/challenges/{ch_id}")
            print("cleanup delete sent")

        listener.cancel()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())