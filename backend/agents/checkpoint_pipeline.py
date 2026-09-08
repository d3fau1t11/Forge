"""
FORGE HITL checkpoint pipeline — consolidated report assembly + suggestion parser.

Two distinct concerns (per spec), kept separate even though the orchestrator may
drive them in one cycle:

1. REPORT GENERATION (build_consolidated_report)
   Produces ONE consolidated, plain-text report covering ALL active agents
   together — so a stronger external model can see cross-agent connections
   (agent A found a credential, agent B needs it). The factual fields
   (Evidence / Tried / Flag candidate) are assembled DETERMINISTICALLY and
   verbatim from blackboard state — never from an LLM — so nothing can be
   fabricated or have its confidence upgraded during summarization. Only the
   narrative fields (Hypothesis / Blocked on) come from a STRICTLY EXTRACTIVE
   Gemini pass, and even that is optional: if the summarizer is absent or fails,
   deterministic placeholders are used and the report is still emitted.

2. SUGGESTION PARSING (parse_suggestions)
   Splits the operator's pasted response on `--- suggestion: {agent_id} ---`
   markers using plain string parsing (no LLM). Each block is routed to the
   agent named in ITS OWN label (supporting cross-agent routing). Unparseable
   input is never silently dropped or misrouted — it is flagged and returned as
   an all-agents fallback directive.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("forge.checkpoint")

# Delimiter the external model is required to emit, one per routed directive.
_SUGGESTION_DELIMITER_RE = re.compile(
    r"^[ \t]*-{2,}[ \t]*suggestion[ \t]*:[ \t]*(?P<agent>[^\s-][^\n\r]*?)[ \t]*-{2,}[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Strictly-extractive prompt for the (optional) Gemini narrative pass. The
# factual fields are already fixed by the deterministic layer; this only fills
# Hypothesis / Blocked-on, and is forbidden from inferring or inventing flags.
_EXTRACTIVE_REPORT_PROMPT = """\
You are a strictly EXTRACTIVE transcript reader. You will be given the raw execution transcript
of one autonomous CTF agent. Output ONLY these two lines, extracted from the transcript:

HYPOTHESIS: <the agent's current best theory for the next step, taken only from what the transcript explicitly shows; if none is present, write: none stated>
BLOCKED: <what is explicitly missing or unresolved in the transcript — a tool, information, or an unclear result; if nothing is blocking, write: none stated>

HARD RULES:
- Extract only what is explicitly present in the transcript. Do NOT infer intent or add reasoning the transcript does not show.
- Do NOT smooth over uncertainty. Do NOT mention or invent any flag. Do NOT upgrade the confidence of anything.
- Output EXACTLY two lines, starting with "HYPOTHESIS:" and "BLOCKED:". No preamble, no extra text.

TRANSCRIPT:
{transcript}
"""


@dataclass
class AgentCheckpointRecord:
    """Deterministic, verbatim facts for one agent at checkpoint time."""
    agent_id: str
    evidence: List[str] = field(default_factory=list)   # verbatim strings/headers/output
    tried: List[str] = field(default_factory=list)      # exact commands + short results
    transcript: str = ""                                # raw text for the extractive pass
    flag_candidate: Optional[str] = None                # from blackboard.flag_candidates
    flag_source: Optional[str] = None                   # e.g. "tool_output", "llm_decoded"
    # Filled by the extractive pass (or deterministic fallback).
    hypothesis: str = "none stated"
    blocked_on: str = "none stated"


@dataclass
class ParsedSuggestions:
    parsed: bool                                        # True if ≥1 delimiter matched
    directives: Dict[str, str] = field(default_factory=dict)   # agent_id -> directive text
    fallback_text: str = ""                             # set when parsing failed
    fallback: bool = False                              # True → apply fallback_text to all agents
    unknown_labels: List[str] = field(default_factory=list)    # labels not in known agent set
    note: str = ""


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + " …[truncated]"


def build_response_instructions(agent_ids: List[str]) -> str:
    """Closing block appended to every consolidated report telling the external model
    EXACTLY how to format its reply so parse_suggestions() can split it deterministically.

    Without this block the report ended after the last agent's 'Blocked on' line and the
    operator's pasted reply had no delimiters — the confirmed live failure
    ('UNPARSEABLE paste — No "--- suggestion: {agent} ---" delimiters found'). The example
    stanzas use the run's ACTUAL agent ids so the labels match what the parser expects.
    """
    ids = [a for a in (agent_ids or []) if a] or ["agent_1"]
    example = "\n\n".join(
        f"--- suggestion: {aid} ---\n[specific next action for {aid}]" for aid in ids
    )
    return (
        "=== INSTRUCTIONS FOR YOUR RESPONSE ===\n"
        "Reply with ONE suggestion block per agent listed above, using EXACTLY this "
        "delimiter format (the parser splits on these lines literally):\n\n"
        f"{example}\n\n"
        "Rules:\n"
        "- Use the exact agent ids shown above as the labels.\n"
        "- If a suggestion for one agent depends on another agent's findings, still label "
        "it under the agent it is FOR, not the agent it came from.\n"
        "- Do not include any text outside these labeled blocks."
    )


async def build_consolidated_report(
    *,
    challenge_name: str,
    category: str,
    difficulty: str,
    target: str,
    cycle_n: int,
    start_time: str,
    end_time: str,
    records: List[AgentCheckpointRecord],
    summarizer: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
) -> str:
    """Assemble the strict plain-text consolidated checkpoint report.

    `summarizer` is an async callable taking a prompt and returning the model's
    text (or None on failure). It is used ONLY for the Hypothesis/Blocked-on
    narrative lines. When None or failing, deterministic placeholders are used.
    """
    # --- Narrative pass (optional, strictly extractive, one call per agent) ----
    if summarizer is not None:
        for rec in records:
            if not rec.transcript.strip():
                continue
            try:
                raw = await summarizer(_EXTRACTIVE_REPORT_PROMPT.format(
                    transcript=_truncate(rec.transcript, 6000)
                ))
            except Exception as exc:                    # noqa: BLE001 — narrative is best-effort
                logger.debug(f"[checkpoint] summarizer failed for {rec.agent_id}: {exc}")
                raw = None
            if raw:
                hyp = re.search(r"HYPOTHESIS:\s*(.+)", raw, re.IGNORECASE)
                blk = re.search(r"BLOCKED:\s*(.+)", raw, re.IGNORECASE)
                if hyp:
                    rec.hypothesis = hyp.group(1).strip() or "none stated"
                if blk:
                    rec.blocked_on = blk.group(1).strip() or "none stated"

    # --- Deterministic assembly (verbatim facts) -------------------------------
    lines: List[str] = []
    lines.append(f"=== FORGE CHECKPOINT — Cycle {cycle_n} ({start_time}–{end_time}) ===")
    lines.append(f"Challenge: {challenge_name} | {category} | {difficulty}")
    lines.append(f"Target: {target}")
    lines.append("")

    if not records:
        lines.append("(no active agents produced any activity this cycle)")

    for rec in records:
        lines.append(f"--- agent: {rec.agent_id} ---")
        if rec.evidence:
            ev = " | ".join(_truncate(e, 400) for e in rec.evidence)
        else:
            ev = "none observed"
        lines.append(f"Evidence: {ev}")

        if rec.tried:
            tried = " || ".join(_truncate(t, 300) for t in rec.tried)
        else:
            tried = "nothing executed yet"
        lines.append(f"Tried: {tried}")

        lines.append(f"Hypothesis: {rec.hypothesis or 'none stated'}")

        if rec.flag_candidate:
            src = rec.flag_source or "unknown source"
            lines.append(f"Flag candidate: {rec.flag_candidate} (source: {src}; UNVERIFIED)")
        else:
            lines.append("Flag candidate: NONE")

        lines.append(f"Blocked on: {rec.blocked_on or 'none stated'}")
        lines.append("")

    # Closing block: tell the external model exactly how to format its reply so the
    # operator's paste-back parses deterministically (see build_response_instructions).
    lines.append(build_response_instructions([r.agent_id for r in records]))

    return "\n".join(lines).rstrip() + "\n"


def parse_suggestions(text: str, known_agent_ids: Optional[List[str]] = None) -> ParsedSuggestions:
    """Deterministically split a pasted external response into per-agent directives.

    Splits on `--- suggestion: {agent_id} ---` markers. Routes each block to the
    agent named in its OWN label. If no marker is found (or the text is empty),
    returns a flagged all-agents fallback rather than silently dropping input.
    """
    known = set(known_agent_ids or [])
    text = text or ""

    matches = list(_SUGGESTION_DELIMITER_RE.finditer(text))
    if not matches:
        return ParsedSuggestions(
            parsed=False,
            fallback_text=text.strip(),
            fallback=bool(text.strip()),
            note="No '--- suggestion: {agent} ---' delimiters found; treating pasted text "
                 "as general guidance for all active agents.",
        )

    directives: Dict[str, str] = {}
    unknown: List[str] = []
    for i, m in enumerate(matches):
        agent_id = m.group("agent").strip()
        block_start = m.end()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        directive = text[block_start:block_end].strip()
        if not directive:
            continue
        # Route by the suggestion's OWN label (supports cross-agent routing).
        if known and agent_id not in known:
            unknown.append(agent_id)
        # Merge if the same agent appears twice (append, never overwrite).
        if agent_id in directives:
            directives[agent_id] = directives[agent_id] + "\n\n" + directive
        else:
            directives[agent_id] = directive

    note = ""
    if unknown:
        note = (f"Delimiters parsed, but these labels match no active agent and were still "
                f"recorded as-labelled: {', '.join(sorted(set(unknown)))}.")

    return ParsedSuggestions(
        parsed=bool(directives),
        directives=directives,
        unknown_labels=sorted(set(unknown)),
        note=note,
        fallback=not directives,
        fallback_text=text.strip() if not directives else "",
    )
