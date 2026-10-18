"""
FORGE Agent Runtime — flag verifier.

Detection and verification are deliberately separate concerns (Step 2):

* A regex match is only ever a **FLAG_CANDIDATE**.
* Only :class:`FlagVerifier` may promote a candidate to **FLAG_VERIFIED**, and only
  the runtime acts on FLAG_VERIFIED to complete the mission.

Verification weighs: the *source* of the value (real command output vs. model
prose), whether the producing action actually succeeded, the format, whether the
value was merely echoed/constructed by the agent (present in the command itself
or in agent-authored code such as a generated solver script), placeholder shapes,
and target relevance.

Crucially, a successful command and a regex match are **not** sufficient proof:
:class:`EvidenceOrigin` records *how* the value was recovered (a target response
or a decoded artifact — versus agent-authored code or an unclassified local
command). Only a trustworthy origin can reach VERIFIED; ``action_succeeded=True``
alone is a candidate. A caller that provides no provenance at all
(``origin=UNKNOWN``) fails closed too — there is no implicit "trust any tool
output" fallback.

A caller-supplied trusted origin is also never taken on faith. It must be
substantiated by the execution evidence the verifier can itself observe:
agent-authored code is always agent-controlled regardless of a claimed
``TARGET_RESPONSE``/``DECODED_ARTIFACT`` origin, and a claimed target/decoded
origin must be backed by a command that genuinely reached the target. An
unsubstantiated claim is downgraded and the candidate fails closed.

The regexes here are the canonical set for the runtime and intentionally mirror the
strict patterns already used by ``swarm_orchestrator`` (known CTF prefixes only, a
minimum body length, and braces excluded from the body so an echoed template like
``picoCTF{{{flag}}}`` can never match as a real flag). They are defined here (rather
than imported from the swarm) so the runtime is a self-contained subsystem and does
not drag the swarm's heavy import chain in just for two patterns.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional
from urllib.parse import urlparse

# Conservative, quote-aware shell-syntax classifier (shared with the execution
# layer). Provenance must never be decided by substring checks: a command that a
# shell can compose is handled here, and anything with shell operators fails
# closed. See ``backend/execution/command_safety.py``.
from backend.execution.command_safety import find_shell_syntax

# Strict flag regex — known CTF platform prefixes only, ≥4 chars, braces excluded
# from the body (see module docstring). No generic catch-all → no CSS/LaTeX/JSON FPs.
FLAG_REGEX = re.compile(
    r"(?:picoCTF\{[^{}]{4,}\}|FLAG\{[^{}]{4,}\}|flag\{[^{}]{4,}\}|HTB\{[^{}]{4,}\}|CTF\{[^{}]{4,}\}|"
    r"DUCTF\{[^{}]{4,}\}|corctf\{[^{}]{4,}\}|TFCCTF\{[^{}]{4,}\}|pwn\.college\{[^{}]{4,}\}|THM\{[^{}]{4,}\})",
    re.IGNORECASE,
)

# Shapes that look like a flag but are placeholders/examples from model prose.
FALSE_FLAG_PATTERNS = re.compile(
    r"(?:picoCTF\{\.\.\.\}|FLAG\{\.\.\.\}|HTB\{\.\.\.\}|CTF\{\.\.\.\}|"
    r"\{[a-z_]+_here\}|\{example[^}]*\}|\{your[^}]*\}|\{placeholder[^}]*\}|"
    r"\{some[^}]*\}|\{flag[^}]*format[^}]*\}|\{insert[^}]*\}|\{\.\.\.\})",
    re.IGNORECASE,
)

# Semantic-path (judge_relevance / extract_candidate_blind) matches return a fixed
# 0.75 confidence. The verbatim-substring double-check inside judge_relevance (and the
# echo / agent-source / provenance / action-success gates in assess()) already IS the
# authenticity test, so a semantic-path confirmation promotes directly to VERIFIED —
# there is no confidence-tier auto-complete threshold and no operator-review hold.
# (Checkpoint logic and CHECKPOINT_INTERVAL_SECONDS are gone; this comment only
# documents why no threshold constant remains.)


class FlagStatus(str, Enum):
    VERIFIED = "FLAG_VERIFIED"
    CANDIDATE = "FLAG_CANDIDATE"
    REJECTED = "REJECTED"


class FlagSource(str, Enum):
    TOOL_OUTPUT = "tool_output"      # extracted from a real command's stdout/stderr
    LLM_PROSE = "llm_prose"          # asserted by the model, not seen in tool output
    UNKNOWN = "unknown"


class EvidenceOrigin(str, Enum):
    """How a candidate was *recovered* — the decisive provenance signal.

    Detection only *finds* a value; provenance records where it actually came
    from so verification can require evidence beyond ``action_succeeded``. Only a
    value recovered from target output, or deterministically decoded from
    target/artifact data, is trustworthy proof. A value emitted by agent-authored
    code, or observed by an unclassified local command, is retained as an
    unverified candidate.

    ``MULTI_STEP_CHAIN`` is **reserved and untrusted**: there is no production
    producer that carries validated chain evidence today, so supplying the enum
    value alone never verifies (it would otherwise be a dormant trusted route an
    arbitrary caller could use to fabricate a VERIFIED verdict). It is reintroduced
    as a trusted origin only alongside a real, evidence-backed producer.
    """

    TARGET_RESPONSE = "target_response"      # observed in output of an action that reached the target
    DECODED_ARTIFACT = "decoded_artifact"    # recovered by deterministic decode of target/artifact data
    MULTI_STEP_CHAIN = "multi_step_chain"    # RESERVED — no trusted producer yet (never verifies)
    AGENT_CODE = "agent_code"                # emitted by agent-authored code — NOT trustworthy
    LOCAL_OBSERVATION = "local_observation"  # observed locally with no target provenance — NOT trustworthy
    UNKNOWN = "unknown"                      # caller has not classified provenance (legacy policy)


# Only these origins may reach VERIFIED when a caller classifies provenance.
# MULTI_STEP_CHAIN is deliberately absent: it has no evidence-backed production
# producer yet, so the enum value alone must never unlock VERIFIED.
_TRUSTED_ORIGINS = frozenset({
    EvidenceOrigin.TARGET_RESPONSE,
    EvidenceOrigin.DECODED_ARTIFACT,
})

# Broad flag *shape*, used only to admit a candidate whose platform prefix is not
# in the hardcoded list but IS declared by the challenge's expected format.
# Deliberately generic (never decisive on its own — provenance and the
# expected-format prefix check do the gating).
GENERIC_FLAG_REGEX = re.compile(r"[A-Za-z0-9_]{2,}\{[^{}]{4,}\}")


def expected_flag_prefixes(expected_format: str) -> List[str]:
    """Platform prefixes a challenge declares, e.g. ``picoCTF{...}|FORGE{...}``
    → ``["picoctf", "forge"]``. Empty when no concrete prefix is declared."""
    if not expected_format:
        return []
    return [m.group(1).lower() for m in re.finditer(r"([A-Za-z0-9_]+)\s*\{", expected_format)]


def _matches_expected_format(candidate: str, expected_format: str) -> bool:
    """True when ``candidate`` matches a prefix the challenge itself declared."""
    prefixes = expected_flag_prefixes(expected_format)
    if not prefixes or not candidate:
        return False
    if not GENERIC_FLAG_REGEX.fullmatch(candidate):
        return False
    return candidate.split("{", 1)[0].lower() in prefixes


# ── Provenance classification (shared by every engine) ──
#
# A regex match, a successful exit code, or the target hostname appearing
# *somewhere* in a command/source is never enough to call a value captured. The
# helpers below decide where output actually came from:
#
#   * agent-authored code (a generated ``.py`` solver, or an inline ``-c``/``-e``
#     one-liner) → its stdout is agent-controlled and fails closed. A hostname in a
#     comment, a string literal, or an unrelated URL inside that code proves nothing.
#   * a *simple*, unredirected command that genuinely invokes a remote client
#     against the target host (the host is the authority of every URL operand)
#     → a real target response.
#   * a compound command (pipe/``;``/``&&``/redirection/substitution) or a request
#     that may follow a redirect → fails closed: local code can construct/replace
#     the output, or the final destination is unknown.
#   * anything else → a local observation with no target provenance.
#
# This is deliberately conservative: when provenance cannot be established we keep
# the candidate and do not verify it.

_LOCAL_PY_INVOKE_RE = re.compile(r"^\s*(?:python[0-9.]*|py)\b", re.IGNORECASE)
_LOCAL_PY_FILE_RE = re.compile(r"""["']?([^"'\s]+\.py)["']?""", re.IGNORECASE)
_LOCAL_PY_INLINE_RE = re.compile(r"\s(?:-c|-m)(?:\s|$)", re.IGNORECASE)

# Inline interpreter invocations ("python -c ...", "perl -e ...", "bash -c ...")
# are agent-authored *code*: the same one-liner can construct the value at runtime
# (e.g. ``print('fl'+'ag{x}')``), so substring echo matching alone is not enough.
_INLINE_CODE_RE = re.compile(
    r"^\s*(?:python[0-9.]*|py|perl|ruby|node|php|bash|sh|zsh)\b"
    r"(?:[^\n]*?)(?:\s(?:-c|-e|-r|--eval)\b|<<)",
    re.IGNORECASE,
)

# Programs that, when invoked, actually contact a remote host. The target host
# must be the authority of a URL *operand* of one of these — a hostname appearing
# anywhere else (a comment, an echo, a grep pattern, a local file, generated
# source, or an unrelated URL passed alongside) is not evidence.
_REMOTE_CLIENTS = frozenset({
    "curl", "curl.exe", "wget", "wget.exe", "wget2", "http", "https", "httpie",
    "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "ftp", "smbclient",
    "rpcclient", "ldapsearch", "dig", "host", "nslookup", "openssl",
})

# HTTP clients and their redirect behaviour. ``curl`` does NOT follow redirects
# unless asked (``-L`` / ``--location``); ``wget`` DOES follow them by default.
# A response that may have followed a redirect to an untrusted destination cannot
# be anchored to the target, so it fails closed unless redirects are provably off.
_CURL_CLIENTS = frozenset({"curl", "curl.exe"})
_WGET_CLIENTS = frozenset({"wget", "wget.exe", "wget2"})
_WGET_NO_REDIRECT = "--max-redirect=0"

# A URL *operand* is a single argv token that is itself an absolute URL. The
# scheme prefix and absence of whitespace are checked here; fragments of a quoted
# header/comment (which contain spaces) are therefore never operands.
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def local_script_path(command: str) -> str:
    """Return the local ``.py`` script a command runs, or "" if none."""
    if not command or not _LOCAL_PY_INVOKE_RE.match(command):
        return ""
    if _LOCAL_PY_INLINE_RE.search(command):
        return ""
    m = _LOCAL_PY_FILE_RE.search(command)
    return m.group(1) if m else ""


def is_agent_code_command(command: str) -> bool:
    """True if a command runs agent-authored code (script file OR inline one-liner)."""
    if not command:
        return False
    return bool(local_script_path(command)) or bool(_INLINE_CODE_RE.match(command))


def target_host(target_scope: str) -> str:
    """Extract the bare host from a target scope ("http://h:80/x" -> "h")."""
    if not target_scope:
        return ""
    s = target_scope.strip()
    if "://" in s:
        try:
            return (urlparse(s).hostname or "").lower()
        except Exception:
            return ""
    return s.split("/", 1)[0].split(":", 1)[0].lower()


def _basename(token: str) -> str:
    """Final path component of a program token (handles ``/`` and Windows ``\\``)."""
    return token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()


def _program_name(tokens: List[str]) -> str:
    """The program a *simple* command invokes, lower-cased.

    Skips leading ``VAR=value`` assignments and a privilege wrapper (``sudo`` etc.)
    including the wrapper's own options, so ``sudo curl ...`` is seen as ``curl``.
    """
    i, n = 0, len(tokens)
    while i < n and "=" in tokens[i] and not tokens[i].startswith("-"):
        i += 1
    while i < n and _basename(tokens[i]) in {"sudo", "doas", "command", "env"}:
        i += 1
        while i < n and tokens[i].startswith("-"):
            i += 2 if tokens[i] in ("-u", "-g", "-U", "-p") else 1
    return _basename(tokens[i]) if i < n else ""


def _is_url_operand(token: str) -> bool:
    """True when a single argv token is itself an absolute URL with a host."""
    if not token or any(ch.isspace() for ch in token):
        return False
    if not _URL_SCHEME_RE.match(token):
        return False
    try:
        return bool(urlparse(token).hostname)
    except Exception:
        return False


def _host_of(token: str) -> str:
    try:
        return (urlparse(token).hostname or "").lower()
    except Exception:
        return ""


def _may_follow_redirects(program: str, tokens: List[str]) -> bool:
    """True when the invoked client can follow a redirect off the target.

    ``wget`` follows redirects unless explicitly disabled (``--max-redirect=0``);
    ``curl`` follows only with an explicit location flag (``-L`` / ``-sL`` /
    ``--location`` / ``--location-trusted``, including unambiguous abbreviations).
    """
    if program in _WGET_CLIENTS:
        return _WGET_NO_REDIRECT not in tokens
    if program not in _CURL_CLIENTS:
        return False
    for tok in tokens:
        if tok == "-" or not tok.startswith("-"):
            continue
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            # Location-following options (incl. unambiguous abbreviations) and the
            # opaque ``--config`` file (which can inject ``--location``).
            if name.startswith("--loc") or name.startswith("--fol") or name == "--config":
                return True
        elif "L" in tok[1:]:              # bundled short flags, e.g. -sL / -Ls
            return True
        elif "K" in tok[1:]:              # ``-K`` = --config (opaque)
            return True
    return False


def command_reaches_target(command: str, target_scope: str) -> bool:
    """True only when a *simple, unredirected* command genuinely contacts the target.

    The command must invoke a remote client as its first-stage program and the
    target host must be the authority of a URL operand passed to it — and of *every*
    URL operand passed to it (so a request cannot be paired with an unrelated URL).

    This deliberately fails closed on anything a shell can compose. A pipeline,
    ``;`` / ``&&`` / ``||`` chain, redirection, command substitution, backgrounding,
    or variable expansion lets local code construct or replace the reported output
    (``curl http://target.ctf/x; python3 -c "print(flag)"`` or
    ``curl http://target.ctf/x | base64 -d``), and a client that can follow a
    redirect to an untrusted destination is likewise not target-anchored. None of
    those are target responses, so provenance is not established. A hostname in a
    comment, a header value, or an unrelated URL proves nothing.
    """
    host = target_host(target_scope)
    if not command or not host or len(host) < 3:
        return False

    # Compound commands: the shell (or a downstream stage) can build/replace the
    # output, so the value cannot be attributed to a target response. Fail closed
    # rather than guess from substrings.
    if find_shell_syntax(command) is not None:
        return False

    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False

    program = _program_name(tokens)
    if program not in _REMOTE_CLIENTS:
        return False
    if _may_follow_redirects(program, tokens):
        return False

    operands = [tok for tok in tokens if _is_url_operand(tok)]
    if not operands:
        return False
    # The request must go to the target and to nothing else.
    return {_host_of(tok) for tok in operands} == {host}


def classify_origin(command: str, target_scope: str, *, agent_source: str = "",
                    agent_code: Optional[bool] = None) -> EvidenceOrigin:
    """Classify where a value came from — the decisive provenance signal.

    ``agent_source`` is accepted for signature compatibility and to make the
    "hostname is not provenance" rule explicit, but it is deliberately **not**
    consulted: source text the agent wrote can contain the target hostname in a
    comment or string while printing a fabricated value.
    """
    if agent_code is None:
        agent_code = is_agent_code_command(command)
    if agent_code:
        return EvidenceOrigin.AGENT_CODE
    if command_reaches_target(command, target_scope):
        return EvidenceOrigin.TARGET_RESPONSE
    return EvidenceOrigin.LOCAL_OBSERVATION


@dataclass
class FlagVerdict:
    status: FlagStatus
    confidence: float
    candidate: str
    reasons: List[str] = field(default_factory=list)

    @property
    def is_verified(self) -> bool:
        return self.status == FlagStatus.VERIFIED


@dataclass
class AnswerSpec:
    """What the challenge expects as its answer, derived solely from the
    description. Feeds the semantic relevance fallback (judge_relevance)."""
    expected_shape: str          # "bracketed_token" | "integer" | "string" | "hash" | "free_text"
    description_summary: str     # one sentence: what the challenge is asking for
    known_prefix: str | None = None  # e.g. "academy", only when clearly implied


@dataclass
class RelevanceVerdict:
    is_answer: bool
    extracted_value: str         # empty string when is_answer is False
    justification: str


_VALID_SHAPES = frozenset({"bracketed_token", "integer", "string", "hash", "free_text"})


def _llm_response(llm_call: Callable, prompt: str) -> str | None:
    """Call an LLM callable (sync or async) and return its stripped text, or None on any failure.

    Never raises: a provider failure degrades to None so the caller fails closed
    instead of crashing the mission.
    """
    try:
        response = llm_call(prompt)
    except Exception:
        return None
    if inspect.isawaitable(response):
        # llm_call may be async (e.g. the checkpoint summarizer). This helper is
        # synchronous, so resolve the coroutine with asyncio.run() on a throwaway
        # loop inside a worker thread: never conflicts with (or blocks) a running
        # event loop, and works when no loop is running at all.
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                response = ex.submit(lambda: asyncio.run(response)).result()
        except Exception:
            return None
    if response is None:
        return None
    return str(response).strip()


def derive_answer_spec(description: str, flag_pattern_hint: str, llm_call: Callable) -> AnswerSpec:
    """Derive the expected answer shape from the challenge description only.

    The LLM is instructed to restate what the description already says — never to
    guess a flag format that is not stated or implied. On any malformed or failing
    response, degrades to the permissive free_text default instead of raising.
    """
    fallback = AnswerSpec(
        expected_shape="free_text",
        description_summary=(description or "")[:200],
        known_prefix=None,
    )
    try:
        prompt = (
            "You are analyzing a CTF challenge description. Your job is to ONLY "
            "restate what the description already says about what the answer "
            "looks like — do NOT guess, invent, or assume anything that is not "
            "stated or implied.\n\n"
            f"Challenge description:\n{description}\n\n"
            f"Flag pattern hint (declared expected format, if any):\n{flag_pattern_hint}\n\n"
            "Respond in exactly this structure:\n"
            "expected_shape: one of bracketed_token, integer, string, hash, free_text\n"
            "description_summary: one sentence stating what the challenge is asking for\n"
            'known_prefix: a flag prefix only if the description/target clearly implies one '
            '(e.g. "academy"), otherwise "none"\n\n'
            "Rules:\n"
            "- If the description gives no hint about the answer's shape, use \"free_text\".\n"
            '- Use "none" for known_prefix unless a prefix is clearly implied.\n'
            "- Do not invent a flag format that is not stated or implied.\n"
        )
        response = _llm_response(llm_call, prompt)
        if not response:
            return fallback
        shape_m = re.search(r"^expected_shape\s*:\s*(\S+)", response, re.IGNORECASE | re.MULTILINE)
        summary_m = re.search(r"^description_summary\s*:\s*(.*)$", response, re.IGNORECASE | re.MULTILINE)
        prefix_m = re.search(r"^known_prefix\s*:\s*(.*)$", response, re.IGNORECASE | re.MULTILINE)
        shape = (shape_m.group(1).strip().lower() if shape_m else "")
        if shape not in _VALID_SHAPES:
            shape = "free_text"
        summary = (summary_m.group(1).strip() if summary_m else "") or (description or "")[:200]
        prefix = (prefix_m.group(1).strip() if prefix_m else "")
        if not prefix or prefix.lower() in {"none", "null", "nil", "n/a", "-"}:
            prefix = None
        return AnswerSpec(
            expected_shape=shape,
            description_summary=summary[:200],
            known_prefix=prefix,
        )
    except Exception:
        return fallback


def judge_relevance(candidate: str, raw_trusted_output: str, answer_spec: AnswerSpec,
                    llm_call: Callable) -> RelevanceVerdict:
    """Judge whether ``candidate`` is the answer to the challenge, using only
    verbatim evidence from ``raw_trusted_output``.

    (a) The candidate must be a literal substring of the trusted output — checked
        before any LLM call; otherwise reject immediately without calling the LLM.
    (b) Otherwise the LLM may only confirm with an exact verbatim substring
        (or NO_ANSWER).
    (c) NO_ANSWER (any case) or empty → not relevant.
    (d) Mandatory verbatim re-check of the LLM's returned string — this is the
        actual hallucination guard, not the prompt wording.
    (e) Only a string that passed both substring checks is accepted.
    Never raises.
    """
    # (a) Precondition: verbatim substring of trusted output, case-sensitive.
    if not candidate or candidate not in raw_trusted_output:
        return RelevanceVerdict(
            is_answer=False, extracted_value="",
            justification="not verbatim in trusted output")

    prompt = _extraction_prompt(answer_spec, raw_trusted_output, candidate=candidate)

    response = _llm_response(llm_call, prompt)

    # (c) NO_ANSWER (any case) or empty → not relevant.
    if not response or response.upper() == "NO_ANSWER":
        return RelevanceVerdict(
            is_answer=False, extracted_value="",
            justification="judged not relevant to description")

    # (d) Mandatory verbatim re-check — the hallucination guard.
    if response not in raw_trusted_output:
        return RelevanceVerdict(
            is_answer=False, extracted_value="",
            justification="LLM response failed verbatim re-check — treated as hallucination")

    # (e) Passed both substring checks.
    return RelevanceVerdict(
        is_answer=True, extracted_value=response,
        justification=f"Candidate '{candidate}' confirmed verbatim in trusted output "
                      f"for: {answer_spec.description_summary}")


def _extraction_prompt(answer_spec: AnswerSpec, raw_trusted_output: str,
                       candidate: str | None = None) -> str:
    """Single source of truth for how we ask the model to extract/confirm an
    answer from trusted output.

    Used by ``judge_relevance`` (confirm a pre-identified candidate) and by
    ``extract_candidate_blind`` (extract blind, guided only by the challenge
    description). The wording is intentionally identical at both call sites so
    there is never more than one place defining "how we ask the model to
    extract an answer from trusted output."
    """
    parts = [
        "You are verifying a candidate answer. You must NOT invent, paraphrase, "
        "or construct an answer. You may only confirm or deny using the text "
        "provided below. If the answer is not clearly present, you must say NO_ANSWER.\n\n"
        f"Question being answered: {answer_spec.description_summary}\n\n",
    ]
    if candidate is not None:
        parts.append(f"Candidate answer to verify: {candidate}\n\n")
    parts.append(
        "Full tool output to check (verbatim source of truth):\n"
        f"{raw_trusted_output}\n\n"
        "Respond with either the exact substring from the provided output that "
        "answers the question, copied character-for-character, or the literal "
        "string NO_ANSWER. Do not add commentary."
    )
    return "".join(parts)


def extract_candidate_blind(raw_trusted_output: str, answer_spec: AnswerSpec,
                            llm_call: Callable) -> str | None:
    """Blindly extract a plausible answer from trusted output.

    There is no pre-identified candidate to confirm — the model is guided only
    by ``answer_spec.description_summary`` and the raw trusted output. Validity
    (format, provenance, echo, agent-source) is deliberately NOT decided here;
    that is ``assess()``'s job, which re-runs against whatever this returns.

    Returns the verbatim substring when the model produces one, else ``None``.
    ``None`` is returned (not logged as a rejection) for NO_ANSWER / empty /
    non-substring responses — the equivalent of "no candidates found."
    Never raises.
    """
    if not raw_trusted_output or answer_spec is None:
        return None

    prompt = _extraction_prompt(answer_spec, raw_trusted_output, candidate=None)
    response = _llm_response(llm_call, prompt)

    # NO_ANSWER (any case) or empty → not an answer; not a rejection worth logging.
    if not response or response.upper() == "NO_ANSWER":
        return None

    # Hallucination guard: the model may only return a verbatim substring.
    if response not in raw_trusted_output:
        return None

    return response


class FlagVerifier:
    """Transitions a candidate through REJECTED / CANDIDATE / VERIFIED — nothing else does."""

    def looks_like_flag(self, text: str) -> bool:
        return bool(text) and bool(FLAG_REGEX.search(text)) and not FALSE_FLAG_PATTERNS.search(text)

    def assess(
        self,
        candidate: str,
        *,
        source: FlagSource = FlagSource.UNKNOWN,
        command: str = "",
        action_succeeded: bool = True,
        target_scope: str = "",
        expected_format: str = "",
        agent_source: str = "",
        agent_code: bool = False,
        origin: EvidenceOrigin = EvidenceOrigin.UNKNOWN,
        raw_trusted_output: str = "",
        answer_spec: AnswerSpec | None = None,
        llm_call: Callable | None = None,
    ) -> FlagVerdict:
        candidate = (candidate or "").strip()
        reasons: List[str] = []

        # ── Format gate ──
        # The hardcoded prefix list is a discovery aid, not the authenticity test:
        # a candidate whose prefix the challenge declares in its expected format is
        # accepted here too, so an unfamiliar-but-legitimate prefix is not rejected
        # solely for being absent from the hardcoded list.
        matched = FLAG_REGEX.search(candidate)
        if matched:
            # Normalise to the matched substring (drops surrounding noise).
            candidate = matched.group(0)
        elif _matches_expected_format(candidate, expected_format):
            candidate = GENERIC_FLAG_REGEX.fullmatch(candidate).group(0)
        elif origin in _TRUSTED_ORIGINS and answer_spec is not None and llm_call is not None:
            # NEW: semantic fallback — judge whether this non-format candidate is
            # the answer the challenge asks for, verbatim in trusted output.
            # Strictly additive: runs only when a caller supplies an answer spec
            # and an LLM callable alongside a trusted origin.
            verdict = judge_relevance(candidate, raw_trusted_output, answer_spec, llm_call)
            if verdict.is_answer:
                # The verbatim-substring double-check already IS the authenticity gate:
                # judge_relevance only returns is_answer=True when the candidate is a
                # verbatim substring of trusted output and the LLM re-confirmed it
                # verbatim. So a semantic-path confirmation is proof — promote directly
                # to VERIFIED, same as the regex fast path. No confidence tier, no gating.
                return FlagVerdict(FlagStatus.VERIFIED, 0.75, verdict.extracted_value,
                                   [verdict.justification])
            return FlagVerdict(FlagStatus.REJECTED, 0.0, candidate, [verdict.justification])
        else:
            return FlagVerdict(FlagStatus.REJECTED, 0.0, candidate,
                               ["Does not match any known CTF flag format."])

        if FALSE_FLAG_PATTERNS.search(candidate):
            return FlagVerdict(FlagStatus.REJECTED, 0.0, candidate,
                               ["Matches a placeholder/example shape, not a real flag."])

        # ── Echoed / constructed gate ──
        # If the exact value already appears in the command the agent issued, it
        # generated/echoed it — it is NOT evidence of a captured flag.
        if command and candidate in command:
            return FlagVerdict(FlagStatus.REJECTED, 0.1, candidate,
                               ["Value appears in the issued command itself (echoed/constructed, not captured)."])

        # ── Agent-authored source gate ──
        # If the value literally appears in code the agent wrote and then ran
        # (e.g. a generated solver script), it was constructed, not observed in a
        # genuine target response. This is the same class of false positive as an
        # echoed command, but for multi-line scripts that are written to a file.
        if agent_source and candidate in agent_source:
            return FlagVerdict(FlagStatus.REJECTED, 0.05, candidate,
                               ["Value appears in agent-authored source (constructed, not captured from the target)."])

        # ── Optional expected-format corroboration (soft) ──
        if expected_format:
            prefix = candidate.split("{", 1)[0].lower()
            if prefix and prefix not in expected_flag_prefixes(expected_format) and "{...}" not in expected_format:
                reasons.append(f"Prefix '{prefix}' differs from expected format '{expected_format}'.")

        # ── Agent-authored local code with no inspectable source ──
        # The value came from a local script the agent generated, but we could not
        # read that script to check whether the value was hard-coded. Provenance is
        # therefore not established → fail closed (candidate, never VERIFIED).
        if agent_code and not agent_source:
            reasons.append("Produced by agent-authored local code whose source could not "
                           "be inspected — provenance not established; unverified.")
            return FlagVerdict(FlagStatus.CANDIDATE, 0.4, candidate, reasons)

        # ── Substantiate a caller-supplied origin ──
        # A caller may classify provenance (e.g. a deterministically validated,
        # redirect-free fetch, or the decode of such a response). The verifier never
        # lets that claim exceed what the execution evidence supports: agent-authored
        # code is always agent-controlled, and a trusted target/decoded origin must be
        # backed by a command that genuinely reached the target. Supplying the trusted
        # enum value alone — or over untrusted evidence — is not proof, so the claimed
        # origin is downgraded and the candidate fails closed.
        if origin in _TRUSTED_ORIGINS:
            if agent_code or is_agent_code_command(command):
                reasons.append("Claimed target/decoded provenance is not evidence: the value "
                               "was produced by agent-authored code, so it is agent-controlled.")
                origin = EvidenceOrigin.AGENT_CODE
            elif not command_reaches_target(command, target_scope):
                reasons.append("Claimed target/decoded provenance is not evidence: the producing "
                               "command did not demonstrably reach the target.")
                origin = EvidenceOrigin.LOCAL_OBSERVATION

        # ── Explicit provenance decision (evidence beyond action success) ──
        # No caller-supplied provenance (UNKNOWN) fails closed: a successful command
        # and a flag-shaped value are not proof. There is no implicit "trust any tool
        # output" fallback for callers that did not classify where the value came from.
        if origin == EvidenceOrigin.UNKNOWN:
            reasons.append("No trustworthy provenance was supplied; a successful command "
                           "and a flag-shaped value are not proof of capture — unverified.")
            return FlagVerdict(FlagStatus.CANDIDATE, 0.4, candidate, reasons)

        return self._decide_by_origin(candidate, origin, reasons, action_succeeded, source)

    @staticmethod
    def _decide_by_origin(candidate: str, origin: EvidenceOrigin, reasons: List[str],
                          action_succeeded: bool, source: FlagSource) -> FlagVerdict:
        """Apply the evidence rule for a caller-classified provenance.

        A trustworthy origin still requires a *successful* action (a value scraped
        from a traceback or an unrelated response never verifies). Everything else —
        agent-authored code, an unclassified local command — fails closed.
        """
        if origin in _TRUSTED_ORIGINS:
            if source != FlagSource.TOOL_OUTPUT:
                reasons.append(f"Origin '{origin.value}' but the value did not come from real "
                               "command output — unverified candidate.")
                return FlagVerdict(FlagStatus.CANDIDATE, 0.4, candidate, reasons)
            if not action_succeeded:
                reasons.append(f"Recovered via {origin.value}, but the producing action did not "
                               "report success — needs corroboration.")
                return FlagVerdict(FlagStatus.CANDIDATE, 0.55, candidate, reasons)
            reasons.append(f"Recovered from {origin.value}: target/artifact evidence, not agent-constructed.")
            conf = 0.95 if not any(r.startswith("Prefix") for r in reasons) else 0.8
            return FlagVerdict(FlagStatus.VERIFIED, conf, candidate, reasons)
        reasons.append(f"Provenance '{origin.value}' is agent-controlled or lacks target/artifact "
                       "evidence — unverified candidate.")
        return FlagVerdict(FlagStatus.CANDIDATE, 0.4, candidate, reasons)

    def assess_observation(self, obs: Any, *, command: str = "", action_succeeded: bool = True,
                           target_scope: str = "", expected_format: str = "",
                           origin: EvidenceOrigin = EvidenceOrigin.UNKNOWN,
                           agent_source: str = "", agent_code: bool = False) -> Optional[FlagVerdict]:
        """Verify the first flag candidate found on a structured Observation (tool output)."""
        candidates = getattr(obs, "flag_candidates", None) or []
        if not candidates:
            return None
        return self.assess(candidates[0], source=FlagSource.TOOL_OUTPUT, command=command,
                           action_succeeded=action_succeeded, target_scope=target_scope,
                           expected_format=expected_format, origin=origin,
                           agent_source=agent_source, agent_code=agent_code)
