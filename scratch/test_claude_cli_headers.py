import asyncio
import httpx

API_KEY = "sk-Ro3qvLoHeg8NMVcIaOgvsHEzUmM88Vuw1nS60Or0yxK1JIMO"

user_agents = [
    "claude-code/0.2.29 (x86_64-pc-windows-msvc)",
    "claude-code/0.2.29",
    "claude-code/0.1.0",
    "@anthropic-ai/sdk",
    "Anthropic/TypeScript 0.20.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

models = [
    "claude-3-5-sonnet-20241022",
    "claude-3-7-sonnet-20240219",
    "claude-3-5-haiku-20241022"
]

async def test_combination(ua, model):
    url = "https://agentrouter.org/v1/messages"
    headers = {
        "x-api-key": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",
        "User-Agent": ua,
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hello"}]
    }
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            res = await client.post(url, headers=headers, json=payload)
            print(f"[{ua[:25]:<25}] [{model[:25]:<25}] -> Status {res.status_code}: {res.text[:100]}", flush=True)
            if res.status_code == 200:
                print(f"SUCCESS! Output: {res.text}", flush=True)
    except Exception as e:
        print(f"Error: {str(e)}", flush=True)

async def main():
    print("Testing Claude Code CLI Headers against AgentRouter...", flush=True)
    for ua in user_agents:
        for m in models:
            await test_combination(ua, m)

if __name__ == "__main__":
    asyncio.run(main())
