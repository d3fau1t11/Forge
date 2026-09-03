import httpx
import logging
from typing import Optional, Dict, Any
from backend.providers.base import BaseProvider, ProviderResponse

logger = logging.getLogger("forge.providers")

class HTTPBaseProvider(BaseProvider):
    def __init__(self, name: str, is_paid: bool, api_key: str, default_model: str, base_url: str):
        super().__init__(name=name, is_paid=is_paid)
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = base_url

    async def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def _post_json(self, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code == 401 or res.status_code == 403:
                raise PermissionError(f"HTTP {res.status_code} Unauthorized / Forbidden for provider {self.name}")
            elif res.status_code == 429:
                raise RuntimeError(f"HTTP 429 Rate Limit / Quota Exhausted for provider {self.name}")
            elif res.status_code >= 500:
                raise RuntimeError(f"HTTP {res.status_code} Provider Server Error for provider {self.name}")
            res.raise_for_status()
            return res.json()

class GeminiProvider(HTTPBaseProvider):
    def __init__(self, api_key: str = ""):
        super().__init__(
            name="gemini",
            is_paid=True,
            api_key=api_key,
            default_model="gemini-1.5-pro",
            base_url="https://generativelanguage.googleapis.com/v1beta/models"
        )

    async def generate_response(
        self, prompt: str, system_instruction: Optional[str] = None, capability: str = "general_reasoning", model: Optional[str] = None, **kwargs
    ) -> ProviderResponse:
        model_to_use = model or self.default_model
        if not await self.is_available():
            return ProviderResponse(provider_name=self.name, model_name=model_to_use, content="", is_refusal=True, refusal_reason="API Key unconfigured")

        url = f"{self.base_url}/{model_to_use}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        try:
            data = await self._post_json(url, {"Content-Type": "application/json"}, payload)
            candidates = data.get("candidates", [])
            if not candidates:
                return ProviderResponse(provider_name=self.name, model_name=model_to_use, content="", is_refusal=True, refusal_reason="No candidate in response")
            
            text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return ProviderResponse(
                provider_name=self.name,
                model_name=model_to_use,
                content=text_content,
                prompt_tokens=len(prompt) // 4,
                completion_tokens=len(text_content) // 4,
                estimated_cost_usd=0.001
            )
        except Exception as e:
            logger.error(f"Gemini API error: {str(e)}")
            return ProviderResponse(provider_name=self.name, model_name=model_to_use, content="", is_refusal=True, refusal_reason=str(e))

class OpenAISpecProvider(HTTPBaseProvider):
    """Generic Provider for OpenAI-compatible REST APIs (OpenRouter, NVIDIA NIM, Cerebras, AgentRouter, Groq, Mistral)."""
    def __init__(self, name: str, is_paid: bool, api_key: str, default_model: str, base_url: str):
        super().__init__(name=name, is_paid=is_paid, api_key=api_key, default_model=default_model, base_url=base_url)

    async def generate_response(
        self, prompt: str, system_instruction: Optional[str] = None, capability: str = "general_reasoning", model: Optional[str] = None, **kwargs
    ) -> ProviderResponse:
        model_to_use = model or self.default_model
        if not await self.is_available():
            return ProviderResponse(provider_name=self.name, model_name=model_to_use, content="", is_refusal=True, refusal_reason="API Key unconfigured")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model_to_use, "messages": messages}

        try:
            data = await self._post_json(url, headers, payload)
            choices = data.get("choices", [])
            if not choices:
                return ProviderResponse(provider_name=self.name, model_name=model_to_use, content="", is_refusal=True, refusal_reason="Empty choices")

            text_content = choices[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            return ProviderResponse(
                provider_name=self.name,
                model_name=model_to_use,
                content=text_content,
                prompt_tokens=usage.get("prompt_tokens", len(prompt) // 4),
                completion_tokens=usage.get("completion_tokens", len(text_content) // 4),
                estimated_cost_usd=0.001 if self.is_paid else 0.0
            )
        except Exception as e:
            logger.error(f"{self.name} API error: {str(e)}")
            return ProviderResponse(provider_name=self.name, model_name=model_to_use, content="", is_refusal=True, refusal_reason=str(e))
