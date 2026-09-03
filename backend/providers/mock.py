import time
import json
from typing import Optional, Dict, Any
from backend.providers.base import BaseProvider, ProviderResponse

class MockProvider(BaseProvider):
    """Local offline mock provider for zero-cost testing and agent workflow simulation."""
    
    def __init__(self):
        super().__init__(name="mock", is_paid=False)

    async def is_available(self) -> bool:
        return True

    async def generate_response(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        capability: str = "general_reasoning",
        model: Optional[str] = None,
        **kwargs
    ) -> ProviderResponse:
        start_time = time.time()
        
        # Capability-aware deterministic mock responses
        if capability in ["recon", "directory_enumeration"]:
            content = json.dumps({
                "agent": "recon",
                "action": "request_capability",
                "capability": "directory_enumeration",
                "reason": "Target web service discovered on port 80. Initiating web directory scan."
            })
        elif capability in ["web_analysis"]:
            content = json.dumps({
                "agent": "web",
                "action": "request_capability",
                "capability": "web_testing",
                "reason": "Inspecting HTML source and headers for hidden comment endpoints or flags."
            })
        elif capability in ["code_analysis", "reverse_engineering"]:
            content = json.dumps({
                "agent": "rev",
                "action": "request_capability",
                "capability": "file_analysis",
                "reason": "Extracting embedded ELF binary sections using binwalk and strings."
            })
        else:
            content = f"[MOCK FORGE REASONING] Processed requirement for capability '{capability}'. Prompt length: {len(prompt)} chars."

        elapsed_ms = (time.time() - start_time) * 1000

        return ProviderResponse(
            provider_name="mock",
            model_name="mock-forge-v1",
            content=content,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(content) // 4,
            estimated_cost_usd=0.0,
            latency_ms=elapsed_ms,
            is_refusal=False
        )
