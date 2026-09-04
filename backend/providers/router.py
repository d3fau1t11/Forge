import logging
from typing import Dict, List, Optional, Any
from backend.config import settings
from backend.providers.base import BaseProvider, ProviderResponse
from backend.providers.mock import MockProvider
from backend.providers.real_providers import GeminiProvider, OpenAISpecProvider, HuggingFaceProvider, CloudflareProvider
from backend.providers.cli.claude_code import AgentRouterClaudeCodeProvider
from backend.providers.cli.codex import AgentRouterCodexProvider

logger = logging.getLogger("forge.router")

class ModelRouter:
    """Model Router selecting appropriate provider/model based on capability, cost, budget, and CLI routing."""

    DEFAULT_ROUTING_MAP = {
        "recon": ["gemini", "openrouter", "cloudflare", "mock"],
        "directory_enumeration": ["gemini", "openrouter", "cloudflare", "mock"],
        "web_analysis": ["gemini", "openrouter", "cloudflare", "mock"],
        "code_analysis": ["gemini", "openrouter", "cloudflare", "agentrouter_claude_code", "mock"],
        "reverse_engineering": ["gemini", "openrouter", "cloudflare", "agentrouter_claude_code", "mock"],
        "fast_reasoning": ["gemini", "openrouter", "cloudflare", "mock"],
        "general_reasoning": ["gemini", "openrouter", "cloudflare", "mock"],
        "verification": ["gemini", "openrouter", "cloudflare", "mock"]
    }

    # Model to Provider/CLI Transport Mapping
    MODEL_PROVIDER_MAP = {
        "claude-opus-5": ("agentrouter_claude_code", "claude_code"),
        "claude-opus-4-8": ("agentrouter_claude_code", "claude_code"),
        "gpt-5.6": ("agentrouter_codex", "codex"),
        "gpt-5.6-sol": ("agentrouter_codex", "codex"),
        "glm-5.3": ("agentrouter_codex", "codex"),
        "deepseek-v4-flash": ("agentrouter_codex", "codex")
    }

    def __init__(self):
        self.providers: Dict[str, BaseProvider] = {
            "mock": MockProvider(),
            "agentrouter_claude_code": AgentRouterClaudeCodeProvider(),
            "agentrouter_codex": AgentRouterCodexProvider()
        }
        self.paid_allowed = settings.PAID_MODEL_ALLOWED
        self.daily_budget_usd = settings.DAILY_BUDGET_USD
        self.current_spent_usd = 0.0
        self._initialize_env_providers()

    def _initialize_env_providers(self):
        if settings.GEMINI_API_KEY:
            self.register_provider("gemini", GeminiProvider(api_key=settings.GEMINI_API_KEY))
        if settings.NVIDIA_API_KEY:
            self.register_provider("nvidia", OpenAISpecProvider(
                name="nvidia",
                is_paid=True,
                api_key=settings.NVIDIA_API_KEY,
                default_model="deepseek-ai/deepseek-v3",
                base_url="https://integrate.api.nvidia.com/v1"
            ))
        if settings.CEREBRAS_API_KEY:
            self.register_provider("cerebras", OpenAISpecProvider(
                name="cerebras",
                is_paid=False,
                api_key=settings.CEREBRAS_API_KEY,
                default_model="llama3.1-8b",
                base_url="https://api.cerebras.ai/v1"
            ))
        if settings.OPENROUTER_API_KEY:
            self.register_provider("openrouter", OpenAISpecProvider(
                name="openrouter",
                is_paid=True,
                api_key=settings.OPENROUTER_API_KEY,
                default_model="meta-llama/llama-3.1-8b-instruct",
                base_url="https://openrouter.ai/api/v1",
                extra_headers={"HTTP-Referer": "https://forge.local", "X-Title": "FORGE CTF"}
            ))
        if settings.HF_TOKEN:
            self.register_provider("huggingface", HuggingFaceProvider(api_key=settings.HF_TOKEN))
        if settings.CLOUDFLARE_API_TOKEN and settings.CLOUDFLARE_ACCOUNT_ID:
            self.register_provider("cloudflare", CloudflareProvider(
                api_key=settings.CLOUDFLARE_API_TOKEN,
                account_id=settings.CLOUDFLARE_ACCOUNT_ID
            ))
        if settings.AGENTROUTER_API_KEY:
            self.register_provider("agentrouter", OpenAISpecProvider(
                name="agentrouter",
                is_paid=True,
                api_key=settings.AGENTROUTER_API_KEY,
                default_model="deepseek-v4-flash",
                base_url="https://agentrouter.org/v1"
            ))

    def register_provider(self, name: str, provider: BaseProvider):
        self.providers[name.lower()] = provider

    def set_paid_allowed(self, allowed: bool):
        self.paid_allowed = allowed

    async def route_request(
        self,
        prompt: str,
        capability: str = "general_reasoning",
        system_instruction: Optional[str] = None,
        target_model: Optional[str] = None,
        **kwargs
    ) -> ProviderResponse:

        # 1. Direct Model Request (e.g. claude-opus-5, gpt-5.6)
        if target_model and target_model in self.MODEL_PROVIDER_MAP:
            provider_name, cli_type = self.MODEL_PROVIDER_MAP[target_model]
            provider = self.providers.get(provider_name)
            if provider and await provider.is_available():
                logger.info(f"[ModelRouter] Direct routing model '{target_model}' to CLI provider '{provider_name}'")
                res = await provider.generate_response(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    capability=capability,
                    model=target_model,
                    **kwargs
                )
                if not res.is_refusal:
                    return res
                logger.warning(f"CLI Provider '{provider_name}' failed for model '{target_model}': {res.refusal_reason}. Falling back...")

        # 2. Capability Candidates Fallback Chain
        candidates = self.DEFAULT_ROUTING_MAP.get(capability, ["cloudflare", "openrouter", "gemini", "mock"])

        for provider_name in candidates:
            provider = self.providers.get(provider_name)
            if not provider:
                continue

            if provider.is_paid:
                if not self.paid_allowed:
                    logger.warning(f"Provider {provider_name} rejected: Paid models disabled.")
                    continue
                if self.current_spent_usd >= self.daily_budget_usd:
                    logger.warning(f"Provider {provider_name} rejected: Daily budget ${self.daily_budget_usd} exceeded.")
                    continue

            if await provider.is_available():
                try:
                    response = await provider.generate_response(
                        prompt=prompt,
                        system_instruction=system_instruction,
                        capability=capability,
                        **kwargs
                    )
                    
                    if response.is_refusal:
                        logger.warning(f"Model refusal from {provider_name}: {response.refusal_reason}. Fallback...")
                        continue

                    if response.estimated_cost_usd > 0:
                        self.current_spent_usd += response.estimated_cost_usd

                    return response
                except Exception as e:
                    logger.error(f"Error calling provider {provider_name}: {str(e)}. Fallback...")
                    continue

        logger.info("Using offline mock provider fallback.")
        return await self.providers["mock"].generate_response(
            prompt=prompt,
            system_instruction=system_instruction,
            capability=capability
        )

model_router = ModelRouter()
