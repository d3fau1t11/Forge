import asyncio
import httpx

API_KEY = "sk-e5539zskVXsS1vh7ZpmRp2ZQpdBk9YMr0OWmgGbzIjVxTdgZ"

base_urls = [
    "https://agentrouter.org/v1",
    "https://api.agentrouter.org/v1",
    "https://agentrouter.ai/v1",
    "https://api.agentrouter.ai/v1"
]

models = ["deepseek-v4-flash", "claude-opus-4.8", "glm-5.3", "gpt-4o"]

async def test_endpoint(url, model):
    full_url = f"{url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}]
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(full_url, headers=headers, json=payload)
            print(f"[{url}] ({model}) -> Status {res.status_code}: {res.text[:150]}")
    except Exception as e:
        print(f"[{url}] ({model}) -> Error: {str(e)}")

async def main():
    print("Testing AgentRouter URLs & Models...")
    for url in base_urls:
        await test_endpoint(url, "deepseek-v4-flash")

if __name__ == "__main__":
    asyncio.run(main())
