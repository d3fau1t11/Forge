import asyncio
import httpx

API_KEY = "sk-Ro3qvLoHeg8NMVcIaOgvsHEzUmM88Vuw1nS60Or0yxK1JIMO"

urls = [
    "https://agentrouter.org/v1/messages",
    "https://agentrouter.org/v1/chat/completions",
    "https://co.agentrouter.org/v1/messages",
    "https://co.agentrouter.org/v1/chat/completions"
]

async def test_domain(url):
    headers = {
        "x-api-key": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}]
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            print(f"[{url:<50}] -> Status {res.status_code}: {res.text[:140]}")
    except Exception as e:
        print(f"[{url:<50}] -> Error: {str(e)}")

async def main():
    print("Testing AgentRouter Domains...")
    for u in urls:
        await test_domain(u)

if __name__ == "__main__":
    asyncio.run(main())
