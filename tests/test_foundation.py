import unittest
import asyncio
from backend.environment.detector import environment_detector
from backend.providers.router import model_router
from backend.tools.registry import tool_registry
from backend.tools.manager import tool_manager

class TestFoundation(unittest.TestCase):

    def test_environment_detector(self):
        env = environment_detector.detect_environment()
        self.assertIn("os", env)
        self.assertIn("installed_tools", env)
        self.assertIsInstance(env["cpu_cores"], int)

    def test_tool_registry(self):
        tools = tool_registry.get_tools_for_capability("directory_enumeration")
        self.assertGreater(len(tools), 0)
        self.assertTrue(any(t.tool_name == "ffuf" for t in tools))

    def test_mock_provider_routing(self):
        async def run_async():
            return await model_router.route_request(
                prompt="Analyze web target",
                capability="directory_enumeration"
            )
        response = asyncio.run(run_async())
        self.assertEqual(response.provider_name, "mock")
        self.assertFalse(response.is_refusal)
        self.assertIn("request_capability", response.content)

    def test_tool_manager_missing_tool(self):
        async def run_async():
            return await tool_manager.execute_capability("non_existent_capability", "127.0.0.1")
        res = asyncio.run(run_async())
        self.assertEqual(res.status, "MISSING_TOOL")

if __name__ == "__main__":
    unittest.main()
