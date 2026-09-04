import unittest
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"
from backend.config import settings
from backend.providers.router import model_router

class TestLiveAPIKeys(unittest.TestCase):

    def test_providers_registered_from_env(self):
        registered = list(model_router.providers.keys())
        print(f"\n[FORGE] Registered Providers in Router: {registered}")
        self.assertIn("mock", registered)
        if settings.GEMINI_API_KEY:
            self.assertIn("gemini", registered)
        if settings.NVIDIA_API_KEY:
            self.assertIn("nvidia", registered)
        if settings.CEREBRAS_API_KEY:
            self.assertIn("cerebras", registered)
        if settings.OPENROUTER_API_KEY:
            self.assertIn("openrouter", registered)

    def test_live_inference_calls(self):
        async def run_live_tests():
            results = {}
            for name, provider in model_router.providers.items():
                if name == "mock":
                    continue
                available = await provider.is_available()
                if not available:
                    results[name] = "SKIPPED (No Key)"
                    continue

                try:
                    res = await provider.generate_response(
                        prompt="Write 'FORGE OK' if you can read this.",
                        capability="general_reasoning"
                    )
                    if res.is_refusal:
                        results[name] = f"REFUSAL: {res.refusal_reason}"
                    else:
                        results[name] = f"SUCCESS ({res.model_name}): {res.content[:60]}..."
                except Exception as e:
                    results[name] = f"ERROR: {str(e)}"
            return results

        results = asyncio.run(run_live_tests())
        print("\n--- LIVE API KEY TEST RESULTS ---")
        for p_name, status in results.items():
            clean_status = status.replace("\n", " ").encode("ascii", "replace").decode("ascii")
            print(f"Provider [{p_name.upper()}]: {clean_status}")
        print("---------------------------------\n")

if __name__ == "__main__":
    unittest.main()
