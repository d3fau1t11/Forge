import asyncio
import httpx

API_KEY = "sk-e5539zskVXsS1vh7ZpmRp2ZQpdBk9YMr0OWmgGbzIjVxTdgZ"

user_agents = [
    "claude-code/0.1.0",
    "anthropic-cli/1.0.0",
    "Claude-Code",
    "Cursor/0.40.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Anthropic/Python 0.25.0",
    "claude/1.0.0"
]

async def test_user_agent(ua):
    url = "https://agentrouter.org/v1/messages"
    headers = {
        "x-api-key": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "anthropic-version": "2023-06-01",
        "User-Agent": ua,
        "Content-Type": "application/json"
    }
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}]
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            print(f"User-Agent [{ua:<35}] -> Status {res.status_code}: {res.text[:120]}")
    except Exception as e:
        print(f"User-Agent [{ua:<35}] -> Error: {str(e)}")

async def main():
    print("Testing AgentRouter Client Detection Headers...")
    for ua in user_agents:
        await test_user_agent(ua)

if __name__ == "__main__":
    asyncio.run(main())
