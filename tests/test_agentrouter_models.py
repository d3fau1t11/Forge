import unittest
import asyncio
from backend.config import settings
from backend.providers.real_providers import OpenAISpecProvider

class TestAgentRouterModels(unittest.TestCase):

    def test_agentrouter_models(self):
        if not settings.AGENTROUTER_API_KEY:
            self.skipTest("No AGENTROUTER_API_KEY configured")

        models_to_test = [
            "claude-opus-4.8",
            "claude-opus-5",
            "deepseek-v4-flash",
            "glm-5.3",
            "gpt-5.6-sol"
        ]

        async def run_tests():
            results = {}
            for model in models_to_test:
                provider = OpenAISpecProvider(
                    name="agentrouter",
                    is_paid=True,
                    api_key=settings.AGENTROUTER_API_KEY,
                    default_model=model,
                    base_url="https://agentrouter.org/v1"
                )
                try:
                    res = await provider.generate_response("Say hello", model=model)
                    if res.is_refusal:
                        results[model] = f"REFUSAL: {res.refusal_reason}"
                    else:
                        results[model] = f"SUCCESS: {res.content[:50]}"
                except Exception as e:
                    results[model] = f"ERROR: {str(e)}"
            return results

        res_dict = asyncio.run(run_tests())
        print("\n--- AGENTROUTER SPECIFIC MODEL TEST RESULTS ---")
        for m, status in res_dict.items():
            print(f"Model [{m}]: {status}")
        print("-----------------------------------------------\n")

if __name__ == "__main__":
    unittest.main()
