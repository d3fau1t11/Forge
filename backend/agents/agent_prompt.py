"""
FORGE flexible full-context agent prompt template.

Replaces the previous fixed worker-role / hardcoded-checklist architecture.
Each agent receives the same rich context and decides its own approach based on
the actual challenge — no pre-assigned "recon vs crypto vs exploit" role.

Key design decisions
---------------------
- Single template function build_agent_prompt() covers both the single-agent
  orchestrator loop and N-worker swarm agents.  Callers pass what they know;
  optional fields default to empty strings so partial context is fine.
- The flag-format line is a VALIDATION FILTER, never a construction target.
  The wording is deliberately chosen to make fabrication the obvious wrong move.
- Budget exhaustion must produce a structured "BUDGET_EXHAUSTED" report, not a
  hallucinated flag — the prompt makes this the default non-flag outcome.
- Binary-artifact mode inserts an additional constraint block that forbids
  text-mode tools and requires byte-level access via xxd/hexdump/Python rb.
- Tool-inventory and OS data come directly from environment_detector.detect_environment()
  — the same source already used by orchestrator_loop.py — so no new data
  collection is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from backend.agents.artifact_classifier import ClassificationResult


# ---------------------------------------------------------------------------
# Context bundle (passed by callers; all fields optional except the essentials)
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    # Challenge identity ────────────────────────────────────────────────────
    platform: str = ""
    challenge_name: str = ""
    category: str = ""
    difficulty: str = ""
    description: str = ""
    target_url: str = ""

    # Artifact / file attachment ────────────────────────────────────────────
    # If the challenge ships a downloadable file OR the user uploaded one,
    # both paths land here.  The artifact classifier result is also passed so
    # the prompt can insert binary-specific constraints.
    attached_file_paths: list = field(default_factory=list)   # ["/abs/path/to/artifact"]
    artifact_classification: Optional[ClassificationResult] = None

    # Environment ───────────────────────────────────────────────────────────
    detected_os: str = "Linux"
    tool_inventory: str = ""     # comma-separated list from environment_detector
    python_libs: str = ""        # comma-separated list from environment_detector
    working_directory: str = ""

    # Budget ────────────────────────────────────────────────────────────────
    max_iterations: int = 40
    max_minutes: int = 30

    # Flag validation ───────────────────────────────────────────────────────
    # User-supplied override pattern shown in the form, or the platform default.
    # This is baked into the prompt as a VALIDATION FILTER, never a target.
    flag_pattern: str = "picoCTF{...}|FLAG{...}|flag{...}|HTB{...}|CTF{...}"

    # Prior context (injected by HITL checkpoint routing) ───────────────────
    # Plain-text directive from a human/stronger-model suggestion.  Injected
    # on top of the agent's accumulated history, not as a replacement.
    injected_directive: str = ""

    # Accumulated history summary (last N turns / shared blackboard state) ──
    history_context: str = ""

    # Retrieved FORGE memory (past experiences + reference playbooks). Built ONCE
    # per mission by the memory retriever and shared across all agents (§6, §8).
    # Reference material — never auto-executed commands.
    memory_context: str = ""


# ---------------------------------------------------------------------------
# System instruction (identity + hard rules)
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION_TEMPLATE = """\
You are FORGE, an autonomous CTF security agent operating inside an authorized educational lab environment on {detected_os}.

Your sole objective: find the flag for the challenge described below.

HARD RULES — violation of any of these is a critical failure:
1. Every command you issue will be executed and its exact output returned to you as the next input.
2. You have a budget of {max_iterations} tool-call iterations OR {max_minutes} minutes, whichever comes first.
   When your budget is exhausted: output BUDGET_EXHAUSTED followed by your best findings, current hypothesis,
   and what is blocking you.  Do NOT guess or fabricate a flag.
3. The expected flag format is: {flag_pattern}
   This format is a VALIDATION FILTER only.  A flag must be extracted from real command output you can
   point to.  Never construct a string to fit this pattern.  Never output a plausible-looking flag you
   have not verified against real, observed output.
4. Every flag you report must include: FLAG: <value> — followed by the exact command whose output it
   was extracted from, verbatim.  If you cannot point to the source command output, say so explicitly
   and describe what is missing.
5. Output ONLY one of:
   - A single executable bash/shell command line.
   - A complete Python script inside triple-backtick python blocks (FORGE will save it as solve.py and run it).
   - FLAG: <value>  (only when verified from real observed output — include the source command).
   - BUDGET_EXHAUSTED: <findings summary>
6. Do not output narrative prose, commentary, or explanations alongside a command.  A command is the action; context belongs in the next turn's history."""

# ---------------------------------------------------------------------------
# User prompt (full context + history injected here each turn)
# ---------------------------------------------------------------------------

USER_PROMPT_TEMPLATE = """\
=== CHALLENGE ===
Platform        : {platform}
Name            : {challenge_name}
Category        : {category}
Difficulty      : {difficulty}
Target          : {target_url}
{attached_files_section}
=== DESCRIPTION ===
{description}

=== ENVIRONMENT ===
OS              : {detected_os}
Tools available : {tool_inventory}
Python libs     : {python_libs}
Working dir     : {working_directory}
{binary_constraints_section}
{memory_section}
=== STEP HISTORY (last turns) ===
{history_context}
{injected_directive_section}
=== YOUR NEXT ACTION ===
Analyse the challenge, environment, and history above.  Issue the single next command, Python solver script, or FLAG/BUDGET_EXHAUSTED response."""

# Binary-mode additional constraint block — inserted when the artifact
# classifier fires so the agent cannot accidentally use text-mode tools.
_BINARY_CONSTRAINTS_TEMPLATE = """\
=== BINARY ARTIFACT MODE ===
The target is a binary artifact ({artifact_type}) saved at: {artifact_path}
MANDATORY constraints for this challenge:
- The artifact is already saved byte-for-byte at the path above.  Do NOT re-download it via curl or wget.
- Never pipe raw binary through a tool that decodes output as UTF-8 text (e.g. `cat`, `strings` piped to a Python str).
  Use byte-safe access: `xxd`, `hexdump -C`, or Python `open(path, 'rb')`.
- Begin with: `file {artifact_path}` to confirm format, then use appropriate analysis tools:
  {recommended_tools}
- Reasoning about encoding, transformation, or decompilation must be based on byte-level evidence from the above tools."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_system_instruction(ctx: AgentContext) -> str:
    """Build the system/identity instruction.  Stateless — call once per run start."""
    return SYSTEM_INSTRUCTION_TEMPLATE.format(
        detected_os=ctx.detected_os or "Linux",
        max_iterations=ctx.max_iterations,
        max_minutes=ctx.max_minutes,
        flag_pattern=ctx.flag_pattern or "picoCTF{...}|FLAG{...}|flag{...}|HTB{...}|CTF{...}",
    )


def build_user_prompt(ctx: AgentContext) -> str:
    """Build the per-turn user prompt containing all context and history."""

    # Attached files section
    if ctx.attached_file_paths:
        lines = ["Attached file(s):"]
        for p in ctx.attached_file_paths:
            lines.append(f"  {p}")
        attached_files_section = "\n".join(lines)
    else:
        attached_files_section = ""

    # Binary constraints section
    binary_constraints_section = ""
    if ctx.artifact_classification and ctx.artifact_classification.is_binary:
        clf = ctx.artifact_classification
        artifact_path = clf.safe_file_path or (ctx.attached_file_paths[0] if ctx.attached_file_paths else "<artifact_path>")
        tools_str = ", ".join(clf.recommended_tools) if clf.recommended_tools else "file, xxd, strings, binwalk"
        binary_constraints_section = _BINARY_CONSTRAINTS_TEMPLATE.format(
            artifact_type=clf.artifact_type,
            artifact_path=artifact_path,
            recommended_tools=tools_str,
        )

    # Injected directive section (from HITL checkpoint suggestion routing)
    if ctx.injected_directive and ctx.injected_directive.strip():
        injected_directive_section = (
            "=== OPERATOR DIRECTIVE (HIGH PRIORITY — address this before continuing) ===\n"
            + ctx.injected_directive.strip()
        )
    else:
        injected_directive_section = ""

    # Retrieved FORGE memory section (shared across agents; empty when nothing relevant).
    memory_section = ctx.memory_context.strip() if ctx.memory_context else ""

    history = ctx.history_context.strip() if ctx.history_context else "No commands executed yet."

    return USER_PROMPT_TEMPLATE.format(
        platform=ctx.platform or "Unknown",
        challenge_name=ctx.challenge_name or "Unknown",
        category=ctx.category or "UNKNOWN",
        difficulty=ctx.difficulty or "UNKNOWN",
        target_url=ctx.target_url or "(none)",
        attached_files_section=attached_files_section,
        description=ctx.description.strip() if ctx.description else "(no description provided)",
        detected_os=ctx.detected_os or "Linux",
        tool_inventory=ctx.tool_inventory or "curl, python3, file, strings, xxd",
        python_libs=ctx.python_libs or "requests, cryptography",
        working_directory=ctx.working_directory or ".",
        binary_constraints_section=binary_constraints_section,
        memory_section=memory_section,
        history_context=history,
        injected_directive_section=injected_directive_section,
    )


def build_agent_prompt(ctx: AgentContext) -> tuple[str, str]:
    """Convenience wrapper — returns (system_instruction, user_prompt) tuple."""
    return build_system_instruction(ctx), build_user_prompt(ctx)


def make_context_from_env(
    env_info: dict,
    challenge_name: str,
    platform: str,
    category: str,
    difficulty: str,
    description: str,
    target_url: str,
    working_directory: str,
    max_iterations: int,
    max_minutes: int,
    flag_pattern: str,
    attached_file_paths: list | None = None,
    artifact_classification: ClassificationResult | None = None,
    history_context: str = "",
    injected_directive: str = "",
    memory_context: str = "",
) -> AgentContext:
    """Build an AgentContext from the dict returned by environment_detector.detect_environment().

    This is the standard factory used by both orchestrator_loop.py and
    swarm_orchestrator.py — ensures both code paths produce identical prompts.
    """
    # Tool inventory: only tools confirmed installed on this host
    installed_tools = [
        name for name, meta in env_info.get("installed_tools", {}).items()
        if meta.get("installed")
    ]
    tool_inventory = ", ".join(installed_tools) if installed_tools else "curl, python3, file, strings, xxd"

    # Python lib inventory
    py_libs = [
        lib for lib, active in env_info.get("installed_python_libs", {}).items()
        if active
    ]
    python_libs = ", ".join(py_libs) if py_libs else "requests, cryptography"

    distro = env_info.get("distro") or env_info.get("os", "Linux")

    return AgentContext(
        platform=platform,
        challenge_name=challenge_name,
        category=category,
        difficulty=difficulty,
        description=description,
        target_url=target_url,
        attached_file_paths=attached_file_paths or [],
        artifact_classification=artifact_classification,
        detected_os=distro,
        tool_inventory=tool_inventory,
        python_libs=python_libs,
        working_directory=working_directory,
        max_iterations=max_iterations,
        max_minutes=max_minutes,
        flag_pattern=flag_pattern,
        history_context=history_context,
        injected_directive=injected_directive,
        memory_context=memory_context,
    )
