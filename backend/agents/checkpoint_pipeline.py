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
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional, Sequence

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


# ---------------------------------------------------------------------------
# Suggestion evaluation gate (Problem 2)
# ---------------------------------------------------------------------------

class SuggestionDecision(str, Enum):
    """Outcome of evaluating a single operator/external-model suggestion."""
    ACCEPT = "ACCEPT"       # Actionable and consistent with evidence
    MODIFY = "MODIFY"       # Directionally useful but needs qualification
    REJECT = "REJECT"       # Vague, contradicted, or repeats exhausted strategy


@dataclass
class SuggestionEvaluation:
    """Result of deterministic evaluation of one suggestion directive."""
    decision: SuggestionDecision
    reason: str                         # Human-readable one-liner explaining the decision
    evidence: List[str] = field(default_factory=list)  # State facts that support the decision
    original_suggestion: str = ""       # Verbatim input text
    suggested_action: str = ""          # Normalized actionable text (empty if REJECT)


# Patterns that signal a vague / non-actionable suggestion.
_VAGUE_RE = re.compile(
    r"^(?:try (?:something |a )(?:else|different|new|other)|do better|keep trying|just"
    r"|you should|have you tried|maybe try|focus more|be more creative|think harder"
    r"|work on it|investigate further|look (?:harder|more carefully)|explore more"
    r"|try again|i don.t know|no idea|unclear|not sure|good luck|try harder"
    r"|[a-z ]{0,30})$",
    re.IGNORECASE,
)

# Patterns that look like a direct flag assertion from the operator.
_FLAG_ASSERTION_RE = re.compile(
    r"(?:flag\s+is|answer\s+is|the\s+flag\s+is|submit|flag\s*[:=])\s*"
    r"(?P<flag>[A-Za-z0-9_{}\-!@#$%^&*()]{4,120})",
    re.IGNORECASE,
)


def evaluate_suggestion(
    suggestion: str,
    *,
    exhausted_strategies: Optional[Sequence[str]] = None,
    failed_techniques: Optional[Sequence[str]] = None,
    known_endpoints: Optional[Sequence[str]] = None,
    known_files: Optional[Sequence[str]] = None,
    flag_candidates: Optional[Sequence[str]] = None,
    mission_state: Optional[object] = None,
) -> SuggestionEvaluation:
    """Deterministically evaluate one suggestion directive against the current mission state.

    Checks (in order):
    1. Empty / vague text → REJECT.
    2. Unverified flag assertion → MODIFY (redirect to verify from real output).
    3. Repeats an exhausted strategy or dead-end technique → REJECT or MODIFY if novel.
    4. Consistency with evidence (endpoints, files, credentials) → ACCEPT with evidence.
    5. Default → ACCEPT with a note that it is unverified-but-actionable.
    """
    text = (suggestion or "").strip()

    # -- Pull additional fields from mission_state if provided ----------------
    ms = mission_state
    exh = set(s.lower() for s in (exhausted_strategies or getattr(ms, "exhausted_strategies", []) or []))
    fail = set(t.lower() for t in (failed_techniques or getattr(ms, "failed_techniques", []) or []))
    endpoints = list(known_endpoints or getattr(ms, "endpoints", []) or getattr(ms, "known_endpoints", []) or [])
    files = list(known_files or getattr(ms, "known_files", []) or [])
    artifacts = list(getattr(ms, "artifacts", []) if ms else [])
    flags = list(flag_candidates or getattr(ms, "flag_candidates", []) or [])

    # 1. Empty / vague --------------------------------------------------------
    if not text:
        return SuggestionEvaluation(
            decision=SuggestionDecision.REJECT,
            reason="Empty suggestion — nothing actionable.",
            original_suggestion=suggestion or "",
        )
    if len(text) < 10 or _VAGUE_RE.match(text.rstrip(".")):
        return SuggestionEvaluation(
            decision=SuggestionDecision.REJECT,
            reason=f"Vague suggestion ('{text[:60]}') — does not specify a concrete next action.",
            original_suggestion=text,
        )

    # 2. Unverified flag assertion --------------------------------------------
    flag_m = _FLAG_ASSERTION_RE.search(text)
    if flag_m:
        asserted = flag_m.group("flag")
        if asserted not in flags:  # flag not already in verified/candidate list
            modified = (
                f"[OPERATOR ASSERTED FLAG — VERIFY BEFORE SUBMITTING] "
                f"Operator suggested the flag may be '{asserted}'. "
                f"Do NOT submit this as the flag unless you can verify it from real tool output. "
                f"Re-run the appropriate command and confirm the flag value from its actual output."
            )
            return SuggestionEvaluation(
                decision=SuggestionDecision.MODIFY,
                reason=f"Suggestion asserts an unverified flag ('{asserted}'); redirected to verification step.",
                original_suggestion=text,
                suggested_action=modified,
            )

    # 3. Exhausted strategy / dead-end check ----------------------------------
    low = text.lower()
    matched_exh = [s for s in exh if s and s in low]
    matched_fail = [f for f in fail if f and f in low]
    if matched_exh or matched_fail:
        # Check if there is a NOVEL element beyond the exhausted strategy (e.g. a new path/param).
        has_novel_target = bool(re.search(r"[/\\?=&]{1}[A-Za-z0-9._\-]{2,}", text))
        if has_novel_target and not matched_exh:
            # Only dead-end techniques but a different target — MODIFY to qualify the target.
            modified = (
                f"[MODIFIED — FOCUS ON NEW TARGET ONLY] {text} "
                f"(Note: the general approach '{', '.join(matched_fail)}' previously failed; "
                f"apply this suggestion only to the new specific target/path.)"
            )
            ev = [f"previously failed: {', '.join(matched_fail)}"] if matched_fail else []
            return SuggestionEvaluation(
                decision=SuggestionDecision.MODIFY,
                reason=f"Technique '{', '.join(matched_fail or matched_exh)}' previously failed; qualified to new target.",
                evidence=ev,
                original_suggestion=text,
                suggested_action=modified,
            )
        exh_str = ', '.join(sorted(matched_exh or matched_fail))
        return SuggestionEvaluation(
            decision=SuggestionDecision.REJECT,
            reason=f"Suggestion repeats exhausted/failed strategy ('{exh_str}'); no novel target identified.",
            original_suggestion=text,
        )

    # 4. Evidence consistency check (support) ---------------------------------
    ev_support: List[str] = []
    all_known = list(endpoints) + list(files) + list(artifacts)
    for known in all_known:
        known_low = known.lower()
        if known_low and known_low in low:
            ev_support.append(f"known item '{known}' mentioned")
    if ev_support:
        return SuggestionEvaluation(
            decision=SuggestionDecision.ACCEPT,
            reason="Suggestion targets a known artifact/endpoint from the current evidence base.",
            evidence=ev_support[:3],
            original_suggestion=text,
            suggested_action=text,
        )

    # 5. Default — actionable but not yet confirmed by evidence ---------------
    return SuggestionEvaluation(
        decision=SuggestionDecision.ACCEPT,
        reason="Suggestion is actionable and does not contradict known state; accepted for agent guidance.",
        original_suggestion=text,
        suggested_action=text,
    )


def evaluate_suggestions(
    parsed: "ParsedSuggestions",
    *,
    mission_state: Optional[object] = None,
    exhausted_strategies: Optional[Sequence[str]] = None,
    failed_techniques: Optional[Sequence[str]] = None,
    known_endpoints: Optional[Sequence[str]] = None,
    known_files: Optional[Sequence[str]] = None,
    flag_candidates: Optional[Sequence[str]] = None,
) -> Dict[str, SuggestionEvaluation]:
    """Evaluate all parsed directives. Returns mapping of agent_id -> SuggestionEvaluation."""
    results: Dict[str, SuggestionEvaluation] = {}
    all_directives: Dict[str, str] = {}
    if parsed.parsed:
        all_directives = parsed.directives
    elif parsed.fallback and parsed.fallback_text:
        # Use the fallback text as a special "all" key so callers can handle it.
        all_directives = {"__fallback__": parsed.fallback_text}

    for agent_id, directive in all_directives.items():
        results[agent_id] = evaluate_suggestion(
            directive,
            mission_state=mission_state,
            exhausted_strategies=exhausted_strategies,
            failed_techniques=failed_techniques,
            known_endpoints=known_endpoints,
            known_files=known_files,
            flag_candidates=flag_candidates,
        )
    return results


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
