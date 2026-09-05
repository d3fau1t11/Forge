import asyncio
import httpx

key = "ad44572166msh97ae7a96445fec2p15ff9ejsn80829c13468b"

endpoints = [
    {
        "name": "gpt-5-5",
        "url": "https://gpt-5-5.p.rapidapi.com/chat/completions",
        "host": "gpt-5-5.p.rapidapi.com",
        "payload": {"model": "GPT-5.5", "messages": [{"role": "user", "content": "hello"}]}
    },
    {
        "name": "gpt-5-4-mini",
        "url": "https://gpt-5-4-mini.p.rapidapi.com/chat/completions",
        "host": "gpt-5-4-mini.p.rapidapi.com",
        "payload": {"model": "gpt-5.4-mini", "messages": [{"role": "user", "content": "hello"}]}
    },
    {
        "name": "deepseek-v31",
        "url": "https://deepseek-v31.p.rapidapi.com/",
        "host": "deepseek-v31.p.rapidapi.com",
        "payload": {"model": "DeepSeek-V3.2", "messages": [{"role": "user", "content": "hello"}]}
    },
    {
        "name": "chatgpt-42",
        "url": "https://chatgpt-42.p.rapidapi.com/conversationgpt4-2",
        "host": "chatgpt-42.p.rapidapi.com",
        "payload": {"messages": [{"role": "user", "content": "hello"}], "web_access": False}
    },
    {
        "name": "gpt-5-nano",
        "url": "https://gpt-5-nano.p.rapidapi.com/chat/completions",
        "host": "gpt-5-nano.p.rapidapi.com",
        "payload": {"model": "GPT-5-nano", "messages": [{"role": "user", "content": "hello"}]}
    }
]

async def test_all():
    print(f"Testing 5 RapidAPI endpoints with key: {key[:8]}...{key[-4:]}\n" + "="*60)
    async with httpx.AsyncClient(timeout=20.0) as client:
        for ep in endpoints:
            headers = {
                "Content-Type": "application/json",
                "x-rapidapi-host": ep["host"],
                "x-rapidapi-key": key
            }
            try:
                r = await client.post(ep["url"], headers=headers, json=ep["payload"])
                print(f"[{ep['name']}] Status: {r.status_code}")
                print(f"Response: {r.text[:300]}\n")
            except Exception as e:
                print(f"[{ep['name']}] Error: {type(e).__name__}: {e}\n")

if __name__ == "__main__":
    asyncio.run(test_all())
