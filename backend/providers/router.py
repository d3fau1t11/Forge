import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from backend.config import settings
from backend.providers.base import BaseProvider, ProviderResponse

from backend.providers.real_providers import GeminiProvider, OpenAISpecProvider, HuggingFaceProvider, CloudflareProvider
from backend.providers.cli.claude_code import AgentRouterClaudeCodeProvider
from backend.providers.cli.codex import AgentRouterCodexProvider
from backend.providers.quota_manager import quota_manager

logger = logging.getLogger("forge.router")

async def _notify_fallback(failed_provider: str, reason: str, next_candidate: Optional[str] = None):
    """Broadcast real-time WebSocket notification when a provider fails/exhausts quota and triggers cascade."""
    try:
        from backend.websocket.manager import ws_manager
        await ws_manager.broadcast({
            "type": "PROVIDER_FALLBACK_TRIGGERED",
            "data": {
                "failed_provider": failed_provider,
                "reason": str(reason)[:160],
                "next_provider": next_candidate or "Next available candidate",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        })
    except Exception as e:
        logger.debug(f"[ModelRouter] WS notification skip: {e}")

class ModelRouter:
    """Model Router selecting appropriate provider/model based on capability, cost, budget, and CLI routing."""

    DEFAULT_ROUTING_MAP = {
        # Live-tested 2026-09-06: GLM 5.3 Flash (OpenRouter) & RapidAPI DeepSeek & Groq Qwen = best CTF solvers.
        # RapidAPI DeepSeek first, then OpenRouter GLM, then Groq Qwen (Free/fast), then Cloudflare.
        "recon": ["rapidapi_deepseek_v32", "groq", "openrouter", "rapidapi_gpt54_mini", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"],
        "directory_enumeration": ["rapidapi_deepseek_v32", "groq", "openrouter", "rapidapi_gpt54_mini", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"],
        "web_analysis": ["rapidapi_deepseek_v32", "groq", "openrouter", "rapidapi_gpt54_mini", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"],
        "web_testing": ["rapidapi_deepseek_v32", "groq", "openrouter", "rapidapi_gpt54_mini", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"],
        "code_analysis": ["rapidapi_deepseek_v32", "groq", "openrouter", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"],
        "reverse_engineering": ["rapidapi_deepseek_v32", "groq", "openrouter", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"],
        "fast_reasoning": ["groq", "rapidapi_deepseek_v32", "openrouter", "cloudflare", "rapidapi_gpt54_mini", "nvidia", "agentrouter_claude_code", "gemini"],
        "general_reasoning": ["rapidapi_deepseek_v32", "groq", "openrouter", "rapidapi_gpt54_mini", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"],
        "verification": ["rapidapi_deepseek_v32", "groq", "openrouter", "cloudflare", "nvidia", "agentrouter_claude_code", "gemini"]
    }

    # Model to Provider/Transport Mapping
    MODEL_PROVIDER_MAP = {
        # Groq (Curated fast models)
        "qwen-3.8-27b": ("groq", "qwen/qwen3.8-27b"),
        "gpt-oss-120b": ("groq", "openai/gpt-oss-120b"),
        "groq-compound": ("groq", "groq/compound"),
        # Claude (AgentRouter Claude Code CLI)
        "claude-opus-5": ("agentrouter_claude_code", "claude_code"),
        "claude-opus-4-8": ("agentrouter_claude_code", "claude_code"),
        # Codex (AgentRouter Codex CLI)
        "gpt-5.6": ("agentrouter_codex", "codex"),
        "gpt-5.6-sol": ("agentrouter_codex", "codex"),
        # RapidAPI (High reliability & speed)
        "gpt-5.4-mini": ("rapidapi_gpt54_mini", "gpt-5.4-mini"),
        "deepseek-v3.2": ("rapidapi_deepseek_v32", "DeepSeek-V3.2"),
        "gpt-5-nano": ("rapidapi_gpt5_nano", "GPT-5-nano"),
        # OpenRouter (Low cost / accurate GLM)
        "glm-5.3-flash": ("openrouter", "z-ai/glm-5.3-flash"),
        "z-ai/glm-5.3-flash": ("openrouter", "z-ai/glm-5.3-flash"),
        "glm-5.3": ("openrouter", "z-ai/glm-5.3"),
        "z-ai/glm-5.3": ("openrouter", "z-ai/glm-5.3"),
        "deepseek-v4-flash": ("openrouter", "deepseek/deepseek-chat"),
        "deepseek/deepseek-chat": ("openrouter", "deepseek/deepseek-chat"),
        "deepseek-chat": ("openrouter", "deepseek/deepseek-chat"),
        # Cloudflare Workers AI
        "cloudflare-llama": ("cloudflare", "@cf/meta/llama-3.1-8b-instruct"),
        # Gemini (Fallback with 18-key pool)
        "gemini-3.6-flash": ("gemini", "gemini-3.6-flash"),
        "gemini-1.5-pro": ("gemini", "gemini-1.5-pro"),
        # NVIDIA NIM
        "deepseek-v4-pro": ("nvidia", "deepseek-ai/deepseek-v4-pro-0813"),
        "nemotron-lightning": ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        "nemotron-ultra": ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        "kimi-k3": ("nvidia", "moonshotai/kimi-k3")
    }

    def __init__(self):
        self.providers: Dict[str, BaseProvider] = {
            "agentrouter_claude_code": AgentRouterClaudeCodeProvider(),
            "agentrouter_codex": AgentRouterCodexProvider()
        }
        self.paid_allowed = settings.PAID_MODEL_ALLOWED
        self.daily_budget_usd = settings.DAILY_BUDGET_USD
        self.current_spent_usd = 0.0
        self._initialize_env_providers()

    def _initialize_env_providers(self):
        # 1. RapidAPI Verified Working Models
        rapidapi_key = (settings.RAPIDAPI_KEY or os.getenv("RAPIDAPI_KEY", "")).strip()
        if rapidapi_key:
            self.register_provider("rapidapi_gpt54_mini", OpenAISpecProvider(
                name="rapidapi_gpt54_mini",
                is_paid=True,
                api_key=rapidapi_key,
                default_model="gpt-5.4-mini",
                base_url="https://gpt-5-4-mini.p.rapidapi.com",
                extra_headers={"x-rapidapi-host": "gpt-5-4-mini.p.rapidapi.com", "x-rapidapi-key": rapidapi_key, "User-Agent": "Mozilla/5.0"},
                speed_tier="fast"
            ))
            self.register_provider("rapidapi_deepseek_v32", OpenAISpecProvider(
                name="rapidapi_deepseek_v32",
                is_paid=True,
                api_key=rapidapi_key,
                default_model="DeepSeek-V3.2",
                base_url="https://deepseek-v31.p.rapidapi.com/",
                extra_headers={"x-rapidapi-host": "deepseek-v31.p.rapidapi.com", "x-rapidapi-key": rapidapi_key, "User-Agent": "Mozilla/5.0"},
                speed_tier="fast"
            ))
            self.register_provider("rapidapi_gpt5_nano", OpenAISpecProvider(
                name="rapidapi_gpt5_nano",
                is_paid=True,
                api_key=rapidapi_key,
                default_model="GPT-5-nano",
                base_url="https://gpt-5-nano.p.rapidapi.com",
                extra_headers={"x-rapidapi-host": "gpt-5-nano.p.rapidapi.com", "x-rapidapi-key": rapidapi_key, "User-Agent": "Mozilla/5.0"},
                speed_tier="fast"
            ))
        # 2. Groq Provider with Multi-Key Rotation Pool
        groq_keys = [k.strip() for k in (getattr(settings, "GROQ_API_KEYS", "") or "").split(",") if k.strip()]
        groq_single_key = (settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")).strip()
        if groq_single_key or groq_keys:
            self.register_provider("groq", OpenAISpecProvider(
                name="groq",
                is_paid=False,
                api_key=groq_single_key,
                api_keys=groq_keys,
                default_model="qwen/qwen3.8-27b",
                base_url="https://api.groq.com/openai/v1",
                speed_tier="fast"
            ))
        # 3. Gemini Provider with 18-Key Rotation Pool
        if settings.GEMINI_API_KEY or settings.GEMINI_API_KEYS:
            gem_keys = [k.strip() for k in (settings.GEMINI_API_KEYS or "").split(",") if k.strip()]
            self.register_provider("gemini", GeminiProvider(
                api_key=settings.GEMINI_API_KEY,
                api_keys=gem_keys
            ))
        # 4. OpenRouter Provider
        if settings.OPENROUTER_API_KEY:
            self.register_provider("openrouter", OpenAISpecProvider(
                name="openrouter",
                is_paid=True,
                api_key=settings.OPENROUTER_API_KEY,
                default_model="z-ai/glm-5.3-flash",
                base_url="https://openrouter.ai/api/v1",
                extra_headers={"HTTP-Referer": "https://forge.local", "X-Title": "FORGE CTF"}
            ))
        # 5. Cloudflare Workers AI Provider
        if settings.CLOUDFLARE_API_TOKEN and settings.CLOUDFLARE_ACCOUNT_ID:
            self.register_provider("cloudflare", CloudflareProvider(
                api_key=settings.CLOUDFLARE_API_TOKEN,
                account_id=settings.CLOUDFLARE_ACCOUNT_ID
            ))
        # 6. NVIDIA NIM Provider
        if settings.NVIDIA_API_KEY:
            self.register_provider("nvidia", OpenAISpecProvider(
                name="nvidia",
                is_paid=True,
                api_key=settings.NVIDIA_API_KEY,
                default_model="deepseek-ai/deepseek-v4-pro-0813",
                base_url="https://integrate.api.nvidia.com/v1"
            ))
        # Note: Direct HTTP REST calls to agentrouter.org/v1 return 401 Unauthorized Client.
        # AgentRouter access is strictly mediated via terminal CLI tools (agentrouter_claude_code & agentrouter_codex).

    def register_provider(self, name: str, provider: BaseProvider):
        self.providers[name.lower()] = provider

    def register_custom_model(self, provider_name: str, api_key: str, model_id: str, base_url: str = "https://integrate.api.nvidia.com/v1", is_paid: bool = True) -> OpenAISpecProvider:
        """Dynamically register or update a model provider from snippet or UI configuration."""
        key = provider_name.lower()
        provider = OpenAISpecProvider(
            name=key,
            is_paid=is_paid,
            api_key=api_key,
            default_model=model_id,
            base_url=base_url
        )
        self.register_provider(key, provider)
        # Register in model to provider map
        self.MODEL_PROVIDER_MAP[model_id] = (key, model_id)
        
        # Prepend to routing maps for high priority
        for capability in self.DEFAULT_ROUTING_MAP:
            if key not in self.DEFAULT_ROUTING_MAP[capability]:
                self.DEFAULT_ROUTING_MAP[capability].insert(0, key)
            else:
                # Move to front
                self.DEFAULT_ROUTING_MAP[capability].remove(key)
                self.DEFAULT_ROUTING_MAP[capability].insert(0, key)

        logger.info(f"[ModelRouter] Successfully registered custom model '{model_id}' under provider '{key}' (base_url: {base_url})")
        return provider

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
        speed_tier: Optional[str] = None,
        **kwargs
    ) -> ProviderResponse:

        # 1. Direct Model Request (e.g. claude-opus-5, deepseek-v4-flash, glm-5.3)
        if target_model and target_model in self.MODEL_PROVIDER_MAP:
            # Check if this model is quota-limited and should be skipped (outside 3h window or exhausted)
            if quota_manager.is_quota_limited_model(target_model) and quota_manager.should_skip_quota_limited_models():
                fallback_model = quota_manager.get_fallback_model(target_model) or "deepseek/deepseek-chat"
                logger.info(
                    f"[ModelRouter] Model '{target_model}' skipped (outside 3h post-reset window or quota exhausted). "
                    f"Directing to always-available model '{fallback_model}' via OpenRouter."
                )
                target_model = fallback_model

            provider_name, target_model_id = self.MODEL_PROVIDER_MAP[target_model]
            provider = self.providers.get(provider_name)
            if provider and await provider.is_available():
                logger.info(f"[ModelRouter] Direct routing model '{target_model}' to provider '{provider_name}' (target: {target_model_id})")
                res = await provider.generate_response(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    capability=capability,
                    model=target_model_id,
                    **kwargs
                )
                if not res.is_refusal:
                    quota_manager.record_successful_request(target_model)
                    return res

                # Detect AgentRouter 402 quota exhaustion from refusal reason
                refusal = res.refusal_reason or ""
                if quota_manager.detect_quota_error(refusal) or "402" in refusal or "budget" in refusal.lower():
                    quota_manager.record_quota_exhaustion(target_model, refusal)
                    # Instant auto-fallback to OpenRouter always-available model
                    fallback_model = quota_manager.get_fallback_model(target_model) or "deepseek/deepseek-chat"
                    logger.info(
                        f"[ModelRouter] Quota 402 detected for '{target_model}'. "
                        f"Instantly failing over to OpenRouter model '{fallback_model}'..."
                    )
                    fb_provider_name, fb_model_id = self.MODEL_PROVIDER_MAP.get(fallback_model, ("openrouter", "deepseek/deepseek-chat"))
                    fb_provider = self.providers.get(fb_provider_name)
                    if fb_provider and await fb_provider.is_available():
                        fb_res = await fb_provider.generate_response(
                            prompt=prompt,
                            system_instruction=system_instruction,
                            capability=capability,
                            model=fb_model_id,
                            **kwargs
                        )
                        if not fb_res.is_refusal:
                            quota_manager.record_successful_request(fallback_model)
                            return fb_res

                logger.warning(f"Provider '{provider_name}' failed for model '{target_model}': {res.refusal_reason}. Falling back to capability chain...")

        # 2. Capability Candidates Fallback Chain
        skip_quota_limited = quota_manager.should_skip_quota_limited_models()
        candidates = list(self.DEFAULT_ROUTING_MAP.get(capability, ["openrouter", "gemini", "cloudflare"]))

        if speed_tier:
            # Sort providers matching requested speed_tier first
            candidates.sort(key=lambda p_name: 0 if getattr(self.providers.get(p_name), "speed_tier", "fast") == speed_tier else 1)

        for provider_name in candidates:
            if skip_quota_limited and provider_name == "agentrouter_claude_code":
                # Outside 3-hour batch window or exhausted: skip Claude Code immediately
                continue

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
                        asyncio.create_task(_notify_fallback(provider_name, refusal or "Model Refusal / Quota Limit"))
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
                    asyncio.create_task(_notify_fallback(provider_name, error_str))
                    continue

        logger.error("[ModelRouter] ALL providers exhausted. No valid response obtained.")
        return ProviderResponse(
            provider_name="none",
            model_name="none",
            content="[FORGE ERROR] All providers exhausted for this request. Check provider keys and quota status.",
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost_usd=0.0,
            latency_ms=0.0,
            is_refusal=True,
            refusal_reason="All providers exhausted — no live provider could fulfill this request."
        )

model_router = ModelRouter()
