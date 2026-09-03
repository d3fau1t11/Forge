import logging
from typing import Dict, List, Optional, Any
from backend.config import settings
from backend.providers.base import BaseProvider, ProviderResponse
from backend.providers.mock import MockProvider

logger = logging.getLogger("forge.router")

class ModelRouter:
    """Model Router selecting appropriate provider/model based on capability, cost, & budget."""

    # Default capability mappings
    DEFAULT_ROUTING_MAP = {
        "recon": ["cerebras", "gemini", "mock"],
        "directory_enumeration": ["cerebras", "gemini", "mock"],
        "web_analysis": ["gemini", "openrouter", "mock"],
        "code_analysis": ["nvidia", "openrouter", "mock"],
        "reverse_engineering": ["nvidia", "agentrouter", "mock"],
        "fast_reasoning": ["cerebras", "groq", "mock"],
        "general_reasoning": ["gemini", "openrouter", "mock"],
        "verification": ["nvidia", "gemini", "mock"]
    }

    def __init__(self):
        self.providers: Dict[str, BaseProvider] = {
            "mock": MockProvider()
        }
        self.paid_allowed = settings.PAID_MODEL_ALLOWED
        self.daily_budget_usd = settings.DAILY_BUDGET_USD
        self.current_spent_usd = 0.0

    def register_provider(self, name: str, provider: BaseProvider):
        self.providers[name.lower()] = provider

    def set_paid_allowed(self, allowed: bool):
        self.paid_allowed = allowed

    async def route_request(
        self,
        prompt: str,
        capability: str = "general_reasoning",
        system_instruction: Optional[str] = None,
        **kwargs
    ) -> ProviderResponse:
        candidates = self.DEFAULT_ROUTING_MAP.get(capability, ["gemini", "openrouter", "mock"])

        for provider_name in candidates:
            provider = self.providers.get(provider_name)
            if not provider:
                continue

            # Paid model budget check
            if provider.is_paid:
                if not self.paid_allowed:
                    logger.warning(f"Provider {provider_name} rejected: Paid models currently disabled.")
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
                        logger.warning(f"Model refusal from {provider_name}: {response.refusal_reason}. Trying fallback.")
                        continue

                    # Track cost
                    if response.estimated_cost_usd > 0:
                        self.current_spent_usd += response.estimated_cost_usd

                    return response
                except Exception as e:
                    logger.error(f"Error calling provider {provider_name}: {str(e)}. Falling back...")
                    continue

        # Ultimate fallback to mock provider
        logger.info("Using offline mock provider fallback.")
        return await self.providers["mock"].generate_response(
            prompt=prompt,
            system_instruction=system_instruction,
            capability=capability
        )

model_router = ModelRouter()
