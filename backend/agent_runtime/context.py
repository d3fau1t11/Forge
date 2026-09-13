"""
FORGE Agent Runtime — layered context builder.

The model is stateless; FORGE owns the state and constructs a *bounded, layered*
context every turn (Step 2). It deliberately does NOT dump the whole session into
the prompt. Layer order:

    1. mission objective
    2. current state (compact)
    3. latest observation
    4. recent trajectory (last N turns)
    5. relevant previous failed approaches
    6. relevant FORGE experiences   ── via memory_retriever
    7. relevant playbooks           ──/
    8. current strategy / recovery directive
    9. tool capabilities

Layers 1–5, 8 are assembled into the `history_context` and objective fields of the
existing :class:`~backend.agents.agent_prompt.AgentContext`; layers 6–7 go into its
`memory_context`; layer 9 into `tool_inventory`. Reusing that template means the
runtime produces prompts consistent with the swarm/orchestrator — no divergent prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from backend.agents.agent_prompt import AgentContext, build_agent_prompt

# Bounded-window sizes.
_RECENT_TURNS = 8
_MAX_FAILED = 6
_MAX_HYPOTHESES = 4
_MAX_ENDPOINTS = 12
_MAX_STATE_LIST = 10


@dataclass
class BuiltContext:
    system_instruction: str
    user_prompt: str
    memory_ids: List[str]


class ContextBuilder:
    """Assembles the bounded per-turn prompt from FORGE-owned state + memory."""

    def __init__(self, memory_retriever: Any = None, capabilities: Any = None,
                 trajectory_search: Any = None):
        # Lazy default so importing the runtime never forces the knowledge stack to load.
        self._retriever = memory_retriever
        self._retriever_resolved = memory_retriever is not None
        self._capabilities = capabilities
        self._traj_search = trajectory_search
        self._traj_search_resolved = trajectory_search is not None

    def _retriever_or_none(self):
        if self._retriever_resolved:
            return self._retriever
        try:
            from backend.knowledge.memory_retriever import memory_retriever
            self._retriever = memory_retriever
        except Exception:
            self._retriever = None
        self._retriever_resolved = True
        return self._retriever

    def _traj_search_or_none(self):
        if self._traj_search_resolved:
            return self._traj_search
        try:
            from backend.agent_runtime.trajectory import trajectory_search
            self._traj_search = trajectory_search
        except Exception:
            self._traj_search = None
        self._traj_search_resolved = True
        return self._traj_search

    # ------------------------------------------------------------------ #

    def retrieve_memory(self, state: Any) -> Tuple[str, List[str]]:
        """Layers 6–7: relevant FORGE experiences + playbooks (best-effort, non-fatal)."""
        retriever = self._retriever_or_none()
        if retriever is None:
            return "", []
        try:
            evidence_parts: List[str] = [state.description or ""]
            evidence_parts.extend((state.known_endpoints or [])[:_MAX_ENDPOINTS])
            evidence_parts.extend(f"{k}: {v}" for k, v in list((state.headers or {}).items())[:8])
            evidence_parts.extend(state.vulnerabilities or [])
            evidence = "\n".join(p for p in evidence_parts if p)
            text, memories = retriever.retrieve_and_format(
                evidence=evidence, category=(state.category or None),
                technologies=state.technologies or [], query=state.current_objective or "",
                top_k=5, capabilities=self._capabilities,
            )
            ids = [getattr(m, "id", None) or getattr(m, "experience_id", None) for m in (memories or [])]
            return text or "", [i for i in ids if i]
        except Exception:
            return "", []

    def recall_cross_session_failures(self, state: Any, *, exclude_session: str = "",
                                      top_k: int = 4) -> str:
        """Layer 5b (§5, §9): surface prior FAILED/REPLAN approaches from OTHER sessions
        whose trajectory matches the current evidence, so FORGE does not rediscover the
        same dead-end. Advisory only — evidence, never a command. Non-fatal.
        """
        search = self._traj_search_or_none()
        if search is None:
            return ""
        try:
            query = " ".join(filter(None, [
                state.category or "", " ".join((state.technologies or [])[:6]),
                " ".join((state.vulnerabilities or [])[:4]),
                state.current_objective or "",
            ])).strip()
            if not query:
                return ""
            hits = search.search(query, exclude_session=exclude_session or None, top_k=top_k * 3)
            seen, lines = set(), []
            for h in hits:
                if (h.get("event_type") or "") not in ("REPLAN", "RECOVERY"):
                    continue
                cmd = (h.get("command") or "").strip()
                reason = (h.get("decision") or "").strip()
                key = (cmd[:80], reason[:60])
                if not (cmd or reason) or key in seen:
                    continue
                seen.add(key)
                entry = f"  ✗ {cmd[:100]}" if cmd else "  ✗"
                if reason:
                    entry += f" — {reason[:120]}"
                lines.append(entry)
                if len(lines) >= top_k:
                    break
            if not lines:
                return ""
            return ("CROSS-SESSION FAILED APPROACHES (other missions with similar conditions — "
                    "do not blindly repeat; verify whether they still apply):\n" + "\n".join(lines))
        except Exception:
            return ""

    def build(
        self,
        *,
        state: Any,
        latest_observation: Optional[Any] = None,
        recent_events: Optional[List[Any]] = None,
        recovery_directive: str = "",
        memory_context: str = "",
        tool_inventory: str = "",
        python_libs: str = "",
        detected_os: str = "Linux",
        cross_session_failures: str = "",
    ) -> Tuple[str, str]:
        """Return (system_instruction, user_prompt) with bounded, layered context."""
        history = self._compose_history(state, latest_observation, recent_events,
                                        recovery_directive, cross_session_failures)

        ctx = AgentContext(
            platform=state.platform or "",
            challenge_name=state.challenge_name or "",
            category=state.category or "",
            difficulty=state.difficulty or "",
            description=self._objective_block(state),
            target_url=state.target or "",
            detected_os=detected_os or "Linux",
            tool_inventory=tool_inventory or "curl, python3, file, strings, xxd, nmap, ffuf",
            python_libs=python_libs or "requests, cryptography, pwntools",
            working_directory="",
            flag_pattern=state.flag_format or "picoCTF{...}|FLAG{...}|flag{...}|HTB{...}|CTF{...}",
            history_context=history,
            memory_context=memory_context or "",
        )
        return build_agent_prompt(ctx)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _objective_block(state: Any) -> str:
        # Layer 1: the mission objective, prepended to the challenge description.
        obj = state.current_objective or "Find and extract the challenge flag."
        base = state.description or ""
        return f"CURRENT OBJECTIVE: {obj}\n\n{base}".strip()

    def _compose_history(self, state, latest_observation, recent_events, recovery_directive,
                         cross_session_failures: str = "") -> str:
        blocks: List[str] = []

        # Layer 2: compact current state.
        blocks.append(self._state_block(state))

        # Layer 3: latest observation.
        if latest_observation is not None:
            blocks.append(self._observation_block(latest_observation))

        # Layer 4: recent trajectory (bounded).
        traj = self._trajectory_block(recent_events or [])
        if traj:
            blocks.append(traj)

        # Layer 5: relevant previous failed approaches (this session).
        failed = (state.failed_techniques or [])[-_MAX_FAILED:]
        if failed:
            blocks.append("PREVIOUS FAILED APPROACHES (do not repeat these):\n" +
                          "\n".join(f"  ✗ {f}" for f in failed))

        # Layer 5b: cross-session failed approaches (other missions, §5/§9).
        if cross_session_failures:
            blocks.append(cross_session_failures)

        # Layer 8: current strategy / recovery directive (highest priority).
        if recovery_directive:
            blocks.append("⚠ RECOVERY DIRECTIVE (address before anything else):\n" + recovery_directive)

        return "\n\n".join(b for b in blocks if b).strip()

    @staticmethod
    def _state_block(state: Any) -> str:
        def _fmt(label, items, n=_MAX_STATE_LIST):
            items = list(items or [])
            if not items:
                return None
            shown = items[:n]
            more = f" (+{len(items) - n} more)" if len(items) > n else ""
            return f"  {label}: {', '.join(str(x) for x in shown)}{more}"

        lines = [f"MISSION STATE (phase={state.phase}, progress={state.progress}%):"]
        file_prov = getattr(state, "file_provenance", {}) or {}
        all_files = list(getattr(state, "known_files", []) or [])
        local_files = [f for f in all_files if file_prov.get(f) == "LOCAL_FILE"]
        remote_or_src_files = [f for f in all_files if file_prov.get(f) in ("SOURCE_CODE_REFERENCE", "REMOTE_FILE", "REMOTE_PROCESS_STATE")]
        other_files = [f for f in all_files if f not in local_files and f not in remote_or_src_files]

        for label, items in [
            ("Endpoints", state.known_endpoints), ("Services", state.known_services),
            ("Technologies", state.technologies), ("Vulnerabilities", state.vulnerabilities),
            ("Credentials", state.credentials),
            ("Local files (workspace)", local_files),
            ("Remote / source-referenced files (NOT in local workspace)", remote_or_src_files),
            ("Files", other_files),
            ("Active interactive sessions", getattr(state, "interactive_sessions", [])),
            ("Flag candidates", state.flag_candidates),
        ]:
            row = _fmt(label, items)
            if row:
                lines.append(row)
        if state.current_hypotheses:
            lines.append("  Hypotheses: " + "; ".join(state.current_hypotheses[:_MAX_HYPOTHESES]))
        if len(lines) == 1:
            lines.append("  (nothing discovered yet — begin reconnaissance)")
        return "\n".join(lines)

    @staticmethod
    def _observation_block(obs: Any) -> str:
        summary = getattr(obs, "summary", "") or "no summary"
        important = (getattr(obs, "important_output", "") or "").strip()
        block = f"LATEST OBSERVATION: {summary}"
        if important:
            block += f"\n--- output ---\n{important[:1200]}"
        return block

    @staticmethod
    def _trajectory_block(recent_events: List[Any]) -> str:
        events = recent_events[-_RECENT_TURNS:]
        if not events:
            return ""
        lines = ["RECENT TURNS:"]
        for ev in events:
            seq = getattr(ev, "sequence", "?")
            cmd = (getattr(ev, "command", "") or getattr(ev, "action_type", "") or "").strip()
            result = getattr(ev, "result", "") or ""
            summ = getattr(ev, "decision_summary", "") or ""
            line = f"  [{seq}] {cmd[:120]}"
            if result:
                line += f" → {result}"
            if summ:
                line += f" ({summ[:80]})"
            lines.append(line)
        return "\n".join(lines)
