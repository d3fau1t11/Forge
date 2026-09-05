import logging
from typing import Dict, List, Optional, Any
from backend.config import settings
from backend.providers.base import BaseProvider, ProviderResponse
from backend.providers.mock import MockProvider
from backend.providers.real_providers import GeminiProvider, OpenAISpecProvider, HuggingFaceProvider, CloudflareProvider
from backend.providers.cli.claude_code import AgentRouterClaudeCodeProvider
from backend.providers.cli.codex import AgentRouterCodexProvider
from backend.providers.quota_manager import quota_manager

logger = logging.getLogger("forge.router")

class ModelRouter:
    """Model Router selecting appropriate provider/model based on capability, cost, budget, and CLI routing."""

    DEFAULT_ROUTING_MAP = {
        "recon": ["nvidia", "agentrouter_codex", "agentrouter_claude_code", "openrouter", "gemini", "cloudflare", "mock"],
        "directory_enumeration": ["nvidia", "agentrouter_codex", "agentrouter_claude_code", "openrouter", "gemini", "cloudflare", "mock"],
        "web_analysis": ["nvidia", "agentrouter_claude_code", "agentrouter_codex", "openrouter", "gemini", "cloudflare", "mock"],
        "code_analysis": ["agentrouter_claude_code", "agentrouter_codex", "nvidia", "openrouter", "gemini", "cloudflare", "mock"],
        "reverse_engineering": ["agentrouter_claude_code", "agentrouter_codex", "nvidia", "openrouter", "gemini", "cloudflare", "mock"],
        "fast_reasoning": ["agentrouter_codex", "nvidia", "agentrouter_claude_code", "openrouter", "gemini", "cloudflare", "mock"],
        "general_reasoning": ["agentrouter_claude_code", "agentrouter_codex", "nvidia", "openrouter", "gemini", "cloudflare", "mock"],
        "verification": ["agentrouter_claude_code", "agentrouter_codex", "nvidia", "openrouter", "gemini", "cloudflare", "mock"]
    }

    # Model to Provider/CLI Transport Mapping
    MODEL_PROVIDER_MAP = {
        "claude-opus-5": ("agentrouter_claude_code", "claude_code"),
        "claude-opus-4-8": ("agentrouter_claude_code", "claude_code"),
        "gpt-5.6": ("agentrouter_codex", "codex"),
        "gpt-5.6-sol": ("agentrouter_codex", "codex"),
        "glm-5.3": ("agentrouter_codex", "codex"),
        "deepseek-v4-flash": ("agentrouter_codex", "codex"),
        "deepseek-v4-pro": ("nvidia", "deepseek-ai/deepseek-v4-pro-0813"),
        "nemotron-lightning": ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        "nemotron-ultra": ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        "kimi-k3": ("nvidia", "moonshotai/kimi-k3")
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
                default_model="deepseek-ai/deepseek-v4-pro-0813",
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

    def get_quota_status(self) -> Dict:
        """Get current AgentRouter quota status summary."""
        return quota_manager.get_quota_status_summary()

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
            # Check if this model's quota is currently exhausted
            if quota_manager.is_model_exhausted(target_model):
                fallback_model = quota_manager.get_fallback_model(target_model)
                next_batch = quota_manager.get_next_batch_time_str()
                logger.warning(
                    f"[ModelRouter] AgentRouter quota exhausted for '{target_model}'. "
                    f"Auto-falling back to '{fallback_model}'. Next batch: {next_batch}"
                )
                if fallback_model and fallback_model in self.MODEL_PROVIDER_MAP:
                    target_model = fallback_model

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
                    quota_manager.record_successful_request(target_model)
                    return res

                # Detect AgentRouter 402 quota exhaustion from refusal reason
                refusal = res.refusal_reason or ""
                if quota_manager.detect_quota_error(refusal):
                    quota_manager.record_quota_exhaustion(target_model, refusal)
                    # Attempt auto-fallback to always-available model
                    fallback_model = quota_manager.get_fallback_model(target_model)
                    if fallback_model and fallback_model in self.MODEL_PROVIDER_MAP:
                        logger.info(
                            f"[ModelRouter] Quota 402 detected for '{target_model}'. "
                            f"Retrying with fallback model '{fallback_model}'..."
                        )
                        fb_provider_name, _ = self.MODEL_PROVIDER_MAP[fallback_model]
                        fb_provider = self.providers.get(fb_provider_name)
                        if fb_provider and await fb_provider.is_available():
                            fb_res = await fb_provider.generate_response(
                                prompt=prompt,
                                system_instruction=system_instruction,
                                capability=capability,
                                model=fallback_model,
                                **kwargs
                            )
                            if not fb_res.is_refusal:
                                quota_manager.record_successful_request(fallback_model)
                                return fb_res

                logger.warning(f"CLI Provider '{provider_name}' failed for model '{target_model}': {res.refusal_reason}. Falling back...")

        # 2. Capability Candidates Fallback Chain
        skip_quota_limited = quota_manager.should_skip_quota_limited_models()
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
                        # Check for quota exhaustion in refusal
                        refusal = response.refusal_reason or ""
                        if quota_manager.detect_quota_error(refusal):
                            quota_manager.record_quota_exhaustion(
                                response.model_name, refusal
                            )
                        logger.warning(f"Model refusal from {provider_name}: {response.refusal_reason}. Fallback...")
                        continue

                    if response.estimated_cost_usd > 0:
                        self.current_spent_usd += response.estimated_cost_usd

                    quota_manager.record_successful_request(response.model_name)
                    return response
                except Exception as e:
                    error_str = str(e)
                    # Detect 402 quota errors from exceptions too
                    if quota_manager.detect_quota_error(error_str):
                        quota_manager.record_quota_exhaustion(provider_name, error_str)
                    logger.error(f"Error calling provider {provider_name}: {error_str}. Fallback...")
                    continue

        logger.info("Using offline mock provider fallback.")
        return await self.providers["mock"].generate_response(
            prompt=prompt,
            system_instruction=system_instruction,
            capability=capability
        )

model_router = ModelRouter()
