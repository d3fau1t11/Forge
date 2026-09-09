"""
FORGE Memory Retriever.

The single "retrieve relevant memory" phase of the intelligence loop (§6). Given
the current evidence, it pulls a SMALL set of high-quality memories from BOTH
knowledge sources and renders them into a compact prompt section:

    Current evidence → MemoryRetriever → top-K memories → shared agent context

Ranking blends relevance with provenance so FORGE-verified experience outranks
unproven external knowledge, and repeated success outranks a single solve (§7).
The retriever returns at most a handful of memories (default 3-10) and formats
them as *reference*, never as commands to auto-execute (§8, §20, §22).

Performance (§17): one retrieval per mission, shared across all agents; uses the
indexed FTS search in ExperienceMemory and the Playbook Vault; no LLM calls.
"""

from __future__ import annotations

import math
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.knowledge.memory_models import RetrievedMemory, SOURCE_WEIGHTS

logger = logging.getLogger("forge.memory_retriever")

_CATEGORY_ALIASES = {
    "rev": "reverse", "reversing": "reverse", "re": "reverse",
    "binary": "pwn", "binexp": "pwn", "web3": "web", "misc": "web",
}


class MemoryRetriever:
    """Unified retrieval + ranking across FORGE experience and the Playbook Vault (§10)."""

    def __init__(self, memory: Any = None, vault: Any = None):
        if memory is None:
            from backend.knowledge.experience_memory import experience_memory
            memory = experience_memory
        if vault is None:
            from backend.knowledge.playbook_vault import playbook_vault
            vault = playbook_vault
        self.memory = memory
        self.vault = vault

    @staticmethod
    def _norm_category(category: Optional[str]) -> Optional[str]:
        if not category:
            return None
        c = category.strip().lower()
        return _CATEGORY_ALIASES.get(c, c)

    # ------------------------------------------------------------------ #

    def retrieve(
        self,
        evidence: str = "",
        category: Optional[str] = None,
        technologies: Optional[List[str]] = None,
        query: str = "",
        top_k: int = 6,
        include_failures: bool = True,
    ) -> List[RetrievedMemory]:
        """Return up to *top_k* ranked memories relevant to the current evidence.

        FORGE experience is favoured over external playbooks (roughly 60/40) but the
        split back-fills from whichever source has material, so a strong playbook is
        never starved when there is no experience yet, and vice-versa.
        """
        top_k = max(1, min(int(top_k or 6), 12))
        cat = self._norm_category(category)
        tech = [t for t in (technologies or []) if t]
        search_query = " ".join(filter(None, [query, cat or "", " ".join(tech)]))

        exp_budget = math.ceil(top_k * 0.6)
        pb_budget = top_k - exp_budget

        experiences = self._retrieve_experiences(search_query, cat, evidence, tech, exp_budget + pb_budget, include_failures)
        playbooks = self._retrieve_playbooks(search_query, cat, evidence, tech, pb_budget + exp_budget)

        chosen: List[RetrievedMemory] = []
        chosen.extend(experiences[:exp_budget])
        chosen.extend(playbooks[:pb_budget])

        # Back-fill unused budget from the other source.
        if len(chosen) < top_k:
            remaining = top_k - len(chosen)
            extra_pb = [p for p in playbooks[pb_budget:]]
            extra_exp = [e for e in experiences[exp_budget:]]
            chosen.extend((extra_exp + extra_pb)[:remaining])

        chosen.sort(key=lambda m: m.score, reverse=True)
        return chosen[:top_k]

    def _retrieve_experiences(self, query, category, evidence, tech, k, include_failures) -> List[RetrievedMemory]:
        try:
            rows = self.memory.search(query=query, category=category, evidence=evidence,
                                      tags=tech, top_k=k, include_failures=include_failures)
        except Exception as e:
            logger.warning(f"[MemoryRetriever] experience search failed: {e}")
            return []
        out = []
        for r in rows:
            out.append(self._experience_to_memory(r))
        return out

    def _retrieve_playbooks(self, query, category, evidence, tech, k) -> List[RetrievedMemory]:
        try:
            pbs = self.vault.search_playbooks(
                query=query or (category or "methodology"),
                category=category,
                recon_artifacts=evidence,
                candidate_tags=tech,
                top_k=k,
                include_unpromoted=False,
            )
        except Exception as e:
            logger.warning(f"[MemoryRetriever] playbook search failed: {e}")
            return []
        return [self._playbook_to_memory(pb) for pb in pbs]

    # ------------------------------------------------------------------ #

    @staticmethod
    def _experience_to_memory(r: Dict[str, Any]) -> RetrievedMemory:
        outcome = r.get("outcome", "success")
        n_succ = int(r.get("times_successful", 0) or 0)
        if outcome == "success" and n_succ >= 2:
            source = "forge_repeated_success"
        elif outcome == "success":
            source = "forge_success"
        else:
            source = "forge_failure"
        base = float(r.get("_score", 0.0)) or (
            (0.5 + 0.5 * float(r.get("confidence", 0.0)))
            * (0.4 + 0.6 * float(r.get("success_rate", 0.0)))
            * SOURCE_WEIGHTS.get(source, 1.0)
        )
        failed = [f.get("approach", "") for f in (r.get("failed_techniques") or []) if f.get("approach")]
        return RetrievedMemory(
            kind="experience",
            id=r.get("id", ""),
            technique=r.get("technique", ""),
            category=r.get("category", ""),
            applicable_conditions=r.get("applicable_conditions", ""),
            strategy=r.get("generalized_strategy", ""),
            outcome=outcome,
            success_indicators=[str(s) for s in (r.get("success_indicators") or [])][:5],
            failed_approaches=failed[:5],
            confidence=float(r.get("confidence", 0.0)),
            success_rate=float(r.get("success_rate", 0.0)),
            source=source,
            provenance={
                "source_run_id": r.get("source_run_id"),
                "source_challenge_id": r.get("source_challenge_id"),
                "challenge_name": r.get("challenge_name"),
                "times_successful": n_succ,
            },
            score=round(base, 4),
        )

    @staticmethod
    def _playbook_to_memory(pb: Any) -> RetrievedMemory:
        conf = float(getattr(pb, "confidence_score", 0.5) or 0.5)
        succ = float(getattr(pb, "success_rate", 1.0) or 1.0)
        src_type = getattr(pb, "source_type", "curated")
        weight = SOURCE_WEIGHTS["external_writeup"] * (1.1 if src_type == "curated" else 1.0)
        score = (0.5 + 0.5 * conf) * (0.5 + 0.5 * succ) * weight
        tags = list(getattr(pb, "tags", []) or [])
        return RetrievedMemory(
            kind="playbook",
            id=getattr(pb, "id", ""),
            technique=(tags[0] if tags else getattr(pb, "category", "playbook")),
            category=getattr(pb, "category", ""),
            applicable_conditions=", ".join(getattr(pb, "trigger_signatures", []) or [])[:400],
            strategy=(getattr(pb, "exploit_template", "") or "")[:400],
            outcome="reference",
            success_indicators=[str(s) for s in (getattr(pb, "expected_outcome_signatures", []) or [])][:5],
            failed_approaches=[],
            confidence=conf,
            success_rate=succ,
            source=f"playbook:{src_type}",
            provenance={"playbook_id": getattr(pb, "id", "")},
            score=round(score, 4),
        )

    # ------------------------------------------------------------------ #
    # Prompt formatting (§8)
    # ------------------------------------------------------------------ #

    @staticmethod
    def format_for_prompt(memories: List[RetrievedMemory]) -> str:
        if not memories:
            return ""
        lines = [
            "## RELEVANT FORGE MEMORY (PAST EXPERIENCE / REFERENCE)",
            "These are prior experiences and reference playbooks, NOT commands to run. "
            "Evaluate whether each actually applies to THIS target before acting; the flag "
            "must still be verified from real output.",
            "",
        ]
        for i, m in enumerate(memories, 1):
            if m.kind == "experience":
                tag = f"FORGE EXPERIENCE · {m.outcome.upper()}"
                stats = f"confidence {m.confidence:.2f} · success rate {int(round(m.success_rate*100))}%"
                if m.provenance.get("times_successful"):
                    stats += f" · {m.provenance['times_successful']}× successful"
                header = f"Memory {i} [{tag} · {stats}]"
            else:
                header = f"Memory {i} [REFERENCE PLAYBOOK · {m.source}]"
            lines.append(header)
            if m.technique:
                lines.append(f"  Technique: {m.technique}")
            if m.applicable_conditions:
                lines.append(f"  Applicable conditions: {m.applicable_conditions[:300]}")
            if m.strategy:
                lines.append(f"  Strategy: {m.strategy[:300]}")
            if m.success_indicators:
                lines.append(f"  Success indicators: {', '.join(m.success_indicators)}")
            if m.failed_approaches:
                lines.append(f"  Previously FAILED here (do not blindly repeat): {'; '.join(m.failed_approaches)}")
            lines.append("")
        return "\n".join(lines).strip()

    def retrieve_and_format(
        self,
        evidence: str = "",
        category: Optional[str] = None,
        technologies: Optional[List[str]] = None,
        query: str = "",
        top_k: int = 6,
        include_failures: bool = True,
    ) -> Tuple[str, List[RetrievedMemory]]:
        """Convenience: retrieve + render. Returns (prompt_section, memories)."""
        memories = self.retrieve(evidence=evidence, category=category, technologies=technologies,
                                 query=query, top_k=top_k, include_failures=include_failures)
        return self.format_for_prompt(memories), memories


memory_retriever = MemoryRetriever()
