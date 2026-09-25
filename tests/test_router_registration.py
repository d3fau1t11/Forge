import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.providers.router import model_router
from backend.providers.snippet_parser import SnippetParser
# backend.config calls load_dotenv(dotenv_path=".env", override=True) at import,
# which would reset DATABASE_URL to the production value from .env. Import it here so
# that override happens now -- once -- then pin DATABASE_URL at the isolated test
# database. Never point this at forge.db: other modules' tearDowns delete real rows.
import backend.config  # noqa: F401
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"


def test_dynamic_model_registration():
    snippet = """
import requests

invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
stream = True

headers = {
    "Authorization": "Bearer nvapi-EXAMPLEKEY0000000000000000000000000000000000",
    "Accept": "text/event-stream" if stream else "application/json",
}

payload = {
  "messages": [
    {
      "role": "user",
      "content": "ping"
    }
  ],
  "model": "moonshotai/kimi-k3"
}
"""
    parsed = SnippetParser.parse_snippet(snippet)
    assert parsed["success"] is True
    
    # Register in router
    prov = model_router.register_custom_model(
        provider_name=parsed["provider_name"],
        api_key=parsed["api_key"],
        model_id=parsed["model"],
        base_url=parsed["base_url"]
    )
    
    assert "nvidia" in model_router.providers
    assert model_router.MODEL_PROVIDER_MAP["moonshotai/kimi-k3"] == ("nvidia", "moonshotai/kimi-k3")
    print("[OK] Dynamic model registration test passed!")

if __name__ == "__main__":
    test_dynamic_model_registration()
