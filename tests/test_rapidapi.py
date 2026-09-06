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

async def run_rapidapi_tests():
    print(f"Testing 5 RapidAPI endpoints with key: {key[:8]}...{key[-4:]}\n" + "="*60)
    for ep in endpoints:
        headers = {
            "x-rapidapi-key": key,
            "x-rapidapi-host": ep["host"],
            "Content-Type": "application/json"
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.post(ep["url"], headers=headers, json=ep["payload"])
                print(f"[{ep['name']:15s}] Status: {res.status_code}")
                if res.status_code == 200:
                    text = str(res.json())[:100]
                    print(f"  OK -> {text}")
                else:
                    print(f"  FAIL -> {res.text[:100]}")
        except Exception as e:
            print(f"[{ep['name']:15s}] ERR: {e}")
        await asyncio.sleep(0.5)

def test_all():
    asyncio.run(run_rapidapi_tests())

if __name__ == "__main__":
    test_all()
