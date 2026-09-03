import asyncio
import httpx

API_KEY = "sk-kJLKAzICcqq9xzoxeR75kHcAfWnFVcW5Q7B13IapJofgAYeY"

async def test_anthropic_spec():
    url = "https://agentrouter.org/v1/messages"
    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}]
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(url, headers=headers, json=payload)
        print(f"[Anthropic Format /v1/messages] Status {res.status_code}: {res.text[:200]}")

async def test_anthropic_bearer():
    url = "https://agentrouter.org/v1/messages"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}]
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(url, headers=headers, json=payload)
        print(f"[Anthropic Bearer /v1/messages] Status {res.status_code}: {res.text[:200]}")

async def main():
    print("Testing AgentRouter Anthropic Spec Endpoints...")
    await test_anthropic_spec()
    await test_anthropic_bearer()

if __name__ == "__main__":
    asyncio.run(main())
