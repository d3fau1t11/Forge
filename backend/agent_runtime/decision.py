"""
FORGE Agent Runtime — decision engine + provider gateway.

* :class:`Decision`         — the agent's structured next decision.
* :class:`ProviderGateway`  — the ONLY thing the runtime knows about "the model".
                              It asks for a *capability*, *urgency* and *reasoning
                              depth* and gets text back. The concrete
                              :class:`RouterProviderGateway` delegates to FORGE's
                              existing ``model_router`` (Step 8), so the runtime is
                              indifferent to whether the answer came from Groq,
                              Gemini, OpenRouter, AgentRouter, or a future local
                              model — and a provider failure never touches session state.
* :class:`DecisionEngine`   — calls the gateway and parses the reply into a
                              (Decision, Action) pair using FORGE's established output
                              contract (single command / python block / FLAG: / BUDGET_EXHAUSTED).

Tests inject a scripted gateway implementing the same tiny interface, so no API key
is required to exercise the loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

from backend.agent_runtime.action import Action, ActionType


@dataclass
class Decision:
    """The agent's next decision (mirrors the Step 2 schema)."""
    action_type: str = "command"          # command | python_script | tool_call | complete
    command: str = ""
    reason: str = ""
    objective: str = ""
    expected_information: str = ""
    strategy: str = ""
    confidence: float = 0.0

    def to_dict(self):
        return {
            "action_type": self.action_type, "command": self.command, "reason": self.reason,
            "objective": self.objective, "expected_information": self.expected_information,
            "strategy": self.strategy, "confidence": round(self.confidence, 3),
        }


@dataclass
class ProviderCompletion:
    """Normalised model response — provider-agnostic."""
    content: str = ""
    provider_name: str = ""
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    is_refusal: bool = False
    refusal_reason: Optional[str] = None


@runtime_checkable
class ProviderGateway(Protocol):
    async def complete(
        self, *, prompt: str, system_instruction: str = "",
        capability: str = "general_reasoning", urgency: str = "normal",
        reasoning_depth: str = "fast",
    ) -> ProviderCompletion:
        ...


class RouterProviderGateway:
    """Adapter over FORGE's model_router. Requests capability/urgency/reasoning depth."""

    # reasoning_depth → provider speed tier + capability preference.
    _DEPTH_CAPABILITY = {
        "fast": "general_reasoning",
        "deep": "code_analysis",
        "planning": "general_reasoning",
    }

    def __init__(self, router=None, default_model: Optional[str] = None):
        if router is None:
            from backend.providers.router import model_router
            router = model_router
        self.router = router
        self.default_model = default_model

    async def complete(self, *, prompt, system_instruction="", capability="general_reasoning",
                       urgency="normal", reasoning_depth="fast") -> ProviderCompletion:
        speed_tier = "deep" if reasoning_depth in ("deep", "planning") else "fast"
        cap = capability or self._DEPTH_CAPABILITY.get(reasoning_depth, "general_reasoning")
        try:
            resp = await self.router.route_request(
                prompt=prompt,
                capability=cap,
                system_instruction=system_instruction or None,
                target_model=self.default_model,
                speed_tier=speed_tier,
            )
        except Exception as e:
            return ProviderCompletion(is_refusal=True, refusal_reason=f"router exception: {e}")
        return ProviderCompletion(
            content=getattr(resp, "content", "") or "",
            provider_name=getattr(resp, "provider_name", "") or "",
            model_name=getattr(resp, "model_name", "") or "",
            prompt_tokens=int(getattr(resp, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(resp, "completion_tokens", 0) or 0),
            is_refusal=bool(getattr(resp, "is_refusal", False)),
            refusal_reason=getattr(resp, "refusal_reason", None),
        )


# ── Model-output parsing (FORGE's established single-action contract) ─────────
_PY_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FLAG_LINE_RE = re.compile(r"FLAG:\s*(\S.+)", re.IGNORECASE)
_BUDGET_RE = re.compile(r"BUDGET_EXHAUSTED\s*:?\s*(.*)", re.IGNORECASE | re.DOTALL)
_STRATEGY_RE = re.compile(r"(?:strategy|approach)\s*[:=]\s*(.+)", re.IGNORECASE)
_OBJECTIVE_RE = re.compile(r"(?:objective|goal)\s*[:=]\s*(.+)", re.IGNORECASE)
# Lines that are clearly commentary, not a command.
_PROSE_PREFIX = re.compile(r"^\s*(?:#|//|\d+[.)]\s|[-*]\s|>|Note:|First|Let me|I will|I'll|Next|Now|Then|Okay|OK)", re.IGNORECASE)


@dataclass
class DecisionResult:
    decision: Decision
    action: Optional[Action]
    completion: ProviderCompletion
    malformed: bool = False
    provider_failed: bool = False
    reasons: List[str] = field(default_factory=list)


class DecisionEngine:
    """Turns a prompt into a validated (Decision, Action) via the provider gateway."""

    def __init__(self, gateway: ProviderGateway):
        self.gateway = gateway

    async def decide(self, *, system_instruction: str, user_prompt: str,
                     urgency: str = "normal", reasoning_depth: str = "fast",
                     capability: str = "general_reasoning") -> DecisionResult:
        completion = await self.gateway.complete(
            prompt=user_prompt, system_instruction=system_instruction,
            capability=capability, urgency=urgency, reasoning_depth=reasoning_depth,
        )
        if completion.is_refusal or not completion.content.strip():
            return DecisionResult(
                Decision(action_type="none", reason=completion.refusal_reason or "empty response"),
                None, completion, provider_failed=True,
                reasons=[completion.refusal_reason or "Provider returned no content."],
            )
        decision, action, malformed = self.parse(completion.content)
        return DecisionResult(decision, action, completion, malformed=malformed)

    # Pure parsing — separated so tests can validate it without a gateway.
    def parse(self, content: str):
        text = content.strip()
        strategy = self._first(_STRATEGY_RE, text)
        objective = self._first(_OBJECTIVE_RE, text)

        # 1) Verified-flag assertion.
        fm = _FLAG_LINE_RE.search(text)
        if fm:
            flag = fm.group(1).strip().split()[0]
            d = Decision(action_type="complete", command=flag, reason="Agent reports a flag.",
                         objective=objective, strategy=strategy, confidence=0.9)
            return d, Action(ActionType.COMPLETE, command=flag, reason="flag_report", raw=text), False

        # 2) Budget exhausted.
        if _BUDGET_RE.search(text) and "budget_exhausted" in text.lower():
            d = Decision(action_type="complete", reason="Budget exhausted.", objective=objective,
                         strategy=strategy, confidence=0.2)
            return d, Action(ActionType.COMPLETE, reason="budget_exhausted", raw=text), False

        # 3) Python solver block.
        pm = _PY_BLOCK_RE.search(text)
        if pm and pm.group(1).strip():
            script = pm.group(1).strip()
            d = Decision(action_type="python_script", command="python solve.py",
                         reason=self._reason_near(text, pm.start()), objective=objective,
                         strategy=strategy, confidence=0.7)
            return d, Action(ActionType.PYTHON_SCRIPT, script=script, reason=d.reason, raw=text), False

        # 4) A single shell command line — first line that looks like a command, not prose.
        cmd = self._first_command_line(text)
        if cmd:
            d = Decision(action_type="command", command=cmd, reason=self._reason_for(text, cmd),
                         objective=objective, strategy=strategy, confidence=0.6)
            return d, Action(ActionType.COMMAND, command=cmd, reason=d.reason, raw=text), False

        # 5) Nothing parseable → malformed.
        return Decision(action_type="none", reason="unparseable", strategy=strategy), None, True

    # ------------------------------------------------------------------ #

    @staticmethod
    def _first(rx: re.Pattern, text: str) -> str:
        m = rx.search(text)
        return m.group(1).strip().splitlines()[0].strip() if m else ""

    @staticmethod
    def _first_command_line(text: str) -> str:
        known = (
            "curl", "wget", "python", "python3", "nmap", "ffuf", "gobuster", "dirb", "feroxbuster",
            "nc", "ncat", "netcat", "ls", "cat", "file", "strings", "xxd", "hexdump", "sqlmap",
            "hydra", "john", "hashcat", "binwalk", "grep", "echo", "bash", "sh", "ssh", "openssl",
            "base64", "tar", "unzip", "steghide", "exiftool", "tshark", "objdump", "readelf", "gdb",
            "pip", "pip3", "git", "awk", "sed", "tr", "cut", "sort", "uniq", "head", "tail", "find",
            "vision_read", "interactive_open", "interactive_send", "interactive_read",
            "interactive_send_and_read", "interactive_close",
        )
        for line in text.splitlines():
            s = line.strip().strip("`").strip()
            if not s or _PROSE_PREFIX.match(s):
                continue
            first = s.split()[0].rsplit("/", 1)[-1].lower()
            # Strongly accept a known tool invocation.
            if first in known:
                return s
            # Otherwise accept a bare-word-led token that is clearly not an English sentence.
            if re.match(r"^[A-Za-z_][\w.\-]*(\s.+)?$", s) and not s.endswith(":") and not re.search(r"[.!?](\s|$)", s):
                return s
        return ""

    @staticmethod
    def _reason_for(text: str, cmd: str) -> str:
        # The line immediately before the command line, if it reads like a reason.
        lines = [ln.strip() for ln in text.splitlines()]
        try:
            idx = next(i for i, ln in enumerate(lines) if cmd in ln)
        except StopIteration:
            return ""
        for j in range(idx - 1, -1, -1):
            if lines[j]:
                return lines[j][:200]
        return ""

    @staticmethod
    def _reason_near(text: str, pos: int) -> str:
        head = text[:pos].strip().splitlines()
        return head[-1][:200] if head else ""
