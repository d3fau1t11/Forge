"""
Privilege-risk classification of the *actual* execution operation.

This module is intentionally side-effect-free and is NOT wired into
ExecutionService, ProcessManager, LocalBackend, or any execution path.  It is
a pure classifier used to evaluate how risky an operation is based on what is
actually about to run (command / argv, execution mode, capability binding),
never on a caller-supplied ``privilege_level`` label.

Classification is conservative:

* SAFE        - operations that pass this classifier's limited checks: a
                registered tool whose
                ``privilege_requirement`` is ``SAFE`` and ``risk_level`` is
                ``SAFE`` or ``LOW``, executed shell-free via ``argv`` or as a
                supported simple POSIX command, with an absolute executable token
                currently resolving to the same file as an absolute registered
                binary path, no supplied env/cwd/stdin, and no argument risks or
                ambiguous Curl option boundaries recognized by the checks below.
* APPROVAL_REQUIRED - operations that may need elevation or whose behaviour
                cannot be safely established: shell syntax (chaining, pipelines,
                redirection, substitution, expansion), privileged tools,
                interpreters, environment-variable assignments, unverified
                executable/capability bindings, supplied environment variables,
                cwd or stdin, or recognized argument risks/unsupported Curl options.
                Windows shell strings are not parsed as POSIX commands and can
                never be SAFE; use argv for a verifiable shell-free operation.
* DENY        - recognized outer privilege wrappers (sudo/su/doas/...),
                empty or unparsable commands.
* UNKNOWN     - no basis to classify (unknown executable); never SAFE.

Both DENY and UNKNOWN are conservative outcomes: neither is ever treated as
SAFE, and a caller cannot make a dangerous operation SAFE by supplying a
``privilege_level="SAFE"`` label.

SAFE is not comprehensive command safety. The argument checks are limited, not
a complete per-tool security policy; capability templates do not constrain tool
behaviour. Supported POSIX strings use literal words and balanced quotes without
active shell constructs; unsupported grammar remains non-SAFE. Raw head
recognition only detects risks, never authorizes execution. File identity is
checked now, not atomically at launch (TOCTOU remains), and executable contents,
dependencies, inherited environment and ambient filesystem trust are not verified.

Nothing here executes a command, changes permissions, modifies the host, or
performs database or audit-log writes.  Registry lookups are read-only.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backend.execution.base import ExecutionRequest
from backend.execution.command_safety import find_shell_syntax
from backend.tools.registry import tool_registry


class OperationRisk(str, Enum):
    SAFE = "SAFE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DENY = "DENY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of :func:`classify_operation` (observability only, no side effects)."""

    risk: OperationRisk
    reason: str
    details: dict


# Executables that explicitly request privilege elevation.  Never SAFE.
_PRIVILEGE_WRAPPERS = frozenset(
    {"sudo", "su", "doas", "runas", "gsudo", "pkexec", "kdesu", "gksu", "super"}
)


# Arbitrary-code interpreters: executing one admits unrestricted script
# execution, so these are never SAFE -- even when bound to a registered SAFE
# capability.  The set is deliberately small and explicitly listed.
_INTERPRETERS = frozenset(
    {
        # Shell interpreters.
        "bash",
        "sh",
        "zsh",
        "dash",
        "ksh",
        "csh",
        "tcsh",
        "fish",
        "pwsh",
        "powershell",
        "powershell.exe",
        "cmd",
        "cmd.exe",
        "command",
        "command.com",
        # Scripting / bytecode interpreters that run arbitrary code.
        "python",
        "python2",
        "python3",
        "perl",
        "ruby",
        "node",
        "php",
        "lua",
        "r",
        "rscript",
        # POSIX names are case-sensitive; Windows normalization uses the entries above.
        "R",
        "Rscript",
    }
)

# A leading shell environment assignment, e.g. ``VAR=value cmd``.
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_PLATFORM = sys.platform
_IS_WINDOWS = _PLATFORM == "win32"

# The shell-feature detector is not a full parser. Exclude additional expansion,
# comments, grouping and escapes from the classifier's SAFE subset, even when
# quoted. This is deliberately conservative, not a new shell grammar.
_UNVERIFIED_POSIX_SYNTAX_RE = re.compile(r"[\\*?\[\]~#(){}]")

# Retain raw quoting and token positions for leading-command recognition only.
# This is not a shell parser: expansions, here-documents and compound commands
# are not traversed, and these tokens never authorize a SAFE operation.
# Keep backtick contents opaque, including escaped nested backticks and quotes.
# An unclosed substitution consumes the remaining text rather than exposing its
# fragments as outer command words. Shell-feature checks still reject it.
# Matches must be contiguous complete words: never search past unmatched quotes.
# Active $() is unsupported: stop before its boundary, including inside double
# quotes. Single-quoted/escaped text and opaque backtick contents retain their
# existing treatment; never interpret nested $() quotes as outer word boundaries.
# POSIX removes backslash-LF pairs between '$' and '(' before token recognition.
# Recognize that boundary directly in the original text; never rewrite commands.
_POSIX_DOLLAR_SUBSTITUTION_RE = re.compile(r"\$(?:\\\n)*\(")
_POSIX_BACKTICK_WORD = r"`(?:[^`\\]|\\[\s\S])*(?:`|$)"
_POSIX_RAW_TOKEN_RE = re.compile(
    rf"""(?:(?!{_POSIX_DOLLAR_SUBSTITUTION_RE.pattern})[^ \t\n'"\\`;&|<>()]|\\[\s\S]|'[^']*'|{_POSIX_BACKTICK_WORD}"""
    rf"""|"(?:(?!{_POSIX_DOLLAR_SUBSTITUTION_RE.pattern})[^"\\`]|\\[\s\S]|{_POSIX_BACKTICK_WORD})*")+"""
    r"|<<-?|>>|<>|>\||[<>]&|&&|\|\||[;&|<>()\n]"
)
_POSIX_REDIRECTIONS = frozenset({"<", ">", ">>", "<>", ">|", "<&", ">&"})

# Recognition only, never an assertion that Windows shell syntax was parsed.
_WINDOWS_HEAD_RE = re.compile(r'^(?:"([^"\r\n]+)"|([^\s"\'&|<>^()%!]+))(?=\s|[&|<>]|$)')
_WINDOWS_PLAIN_COMMAND_RE = re.compile(r"[A-Za-z0-9_.:/\\ \t-]+")


def _is_absolute_executable_path(path: str) -> bool:
    """Check if *path* is fully qualified on the classified platform.

    Handles Unix absolute paths (leading ``/``), Windows absolute paths
    (``C:\\...`` or ``\\\\...``), and rejects relative names.
    """
    if not path:
        return False
    if _IS_WINDOWS:
        drive, tail = ntpath.splitdrive(path)
        # Drive-relative and root-relative paths depend on cwd/current drive.
        return bool(drive) and (drive.startswith("\\\\") or tail.startswith(("/", "\\")))
    return posixpath.isabs(path)


def _normalize_binary_basename(name: str) -> str:
    """Candidate/risk lookup only: a basename is never executable identity."""
    if not _IS_WINDOWS:
        return posixpath.basename(name)
    base = ntpath.basename(name).lower()
    stem, ext = ntpath.splitext(base)
    if ext in (".exe", ".com", ".bat", ".cmd"):
        base = stem
    return base


def _tool_by_binary(binary_name: str):
    """Find a candidate definition; the caller must still verify file identity."""
    for tool in tool_registry.tools.values():
        if tool.binary and _normalize_binary_basename(tool.binary) == binary_name:
            return tool
    return None


def _binary_matches_tool(binary: str, tool) -> bool:
    """Compare the resolved file with an authoritative absolute registry path.

    A name-only entry cannot supply a trusted comparison target: resolving both
    names through the same PATH would also accept a shadow executable. Keep the
    original absolute token, require successful resolution and samefile, and
    never fall back to basename or normalized-path-string equality. This verifies current
    file identity, not content integrity or the executable at a future launch.
    """
    if not tool or not tool.binary:
        return False
    if not _is_absolute_executable_path(tool.binary):
        return False
    if not _is_absolute_executable_path(binary):
        # A bare name leaves PATH searching to the launcher, which may continue
        # after a candidate that which() found fails to execute. Relative paths
        # likewise have no authoritative cwd policy. Neither binds the launch.
        return False
    if _IS_WINDOWS and ntpath.splitext(binary)[1].lower() not in (".exe", ".com"):
        # CreateProcess search order is not shutil.which's PATH/PATHEXT lookup.
        # Require an explicit native file, not an inferred suffix/search result.
        return False
    try:
        resolved = shutil.which(binary)
        if not resolved or not _is_absolute_executable_path(resolved):
            return False
        # Windows may run batch files through a shell even with shell=False.
        if _IS_WINDOWS and ntpath.splitext(resolved)[1].lower() not in (".exe", ".com"):
            return False
        for path in (tool.binary, resolved):
            name = _normalize_binary_basename(path)
            if name in _PRIVILEGE_WRAPPERS or name in _INTERPRETERS:
                return False
        if _IS_WINDOWS and not os.path.samefile(binary, resolved):
            return False
        return os.path.samefile(resolved, tool.binary)
    except (OSError, ValueError):
        return False


# File-output / write flags that direct a tool to write to the filesystem.
# Kept small and tied to the confirmed false-SAFE paths; not a comprehensive
# per-tool argument policy.
_FILE_OUTPUT_FLAGS = frozenset(
    {
        "-o",
        "--output",
        "-O",
        "--remote-name",
        "--output-file",
        "--output-dir",
        "-oN",
        "-oX",
        "-oS",
        "-oG",
    }
)

# Minimal Curl arity metadata, not a comprehensive parser or safety allowlist.
# Established curl(1) semantics: --user-agent/-A <name>, --request/-X <method>,
# --header/-H <header/@file>, and --data-binary <data> each consume one value,
# even when it begins with '-'. Long options accept '=value'; short options
# accept an immediately attached value (including a literal leading '=').
# Header and binary-data values beginning with '@' can read files/stdin.
# Verified offline in the bundled curl(1) 8.19.0 manual's OPTIONS and named
# option entries; long-option '=' syntax is documented there since 8.16.0.
_CURL_VALUE_OPTIONS = frozenset(
    {"--user-agent", "-A", "--request", "-X", "--header", "-H", "--data-binary"}
)
# Only the existing simple probes/template flags and their curl(1) aliases.
_CURL_NO_VALUE_OPTIONS = frozenset(
    {"--version", "-V", "--include", "-i", "--silent", "-s"}
)


def _is_absolute_filesystem_path(token: str) -> bool:
    """True iff *token* is an absolute filesystem path rather than a URL.

    Tokens with a scheme (``://``) are network locations, not filesystem
    paths, and are not flagged here.
    """
    if not token:
        return False
    if "://" in token:
        return False
    if token.startswith("/") or token.startswith("\\"):
        return True
    if len(token) >= 2 and token[1] == ":" and token[0].isalpha():
        return True  # Windows drive letter, e.g. ``C:\\``
    return False


def _arguments_require_approval(tokens: list, tool_name: str) -> bool:
    """True iff *tokens* (full argv; ``tokens[0]`` is the binary) carry
    filesystem-write or sensitive-filesystem-access risk that the classifier
    cannot validate against any verified constraint.

    The registry's ``args_template`` is a formatting template, not a security
    policy, and the classifier maintains no comprehensive argument allowlist.
    The known output, config, file-backed data and path forms below require
    approval; absence of a match is not a proof of arbitrary argument safety.
    Only Curl has literal-value exemptions, using explicit arity metadata.
    Unknown Curl options (including short clusters/long abbreviations) require
    approval rather than guessing what they consume. Other tools retain token
    risk checks with no literal-payload exemption.

    Additional conservative checks:

    * Relative‑path arguments (``./...`` or ``../...``) are not trusted,
      because they can substitute an unregistered executable relative to the
      caller's cwd or PATH.
    * ``--script`` selection is not trusted: neither a URL substring nor a
      Boolean expression establishes safe script execution (e.g. nmap NSE).
    """
    i = 1
    while i < len(tokens):
        token = tokens[i]
        option, separator, attached_value = token.partition("=")
        if (token in _FILE_OUTPUT_FLAGS
                or (separator and option in _FILE_OUTPUT_FLAGS)
                or token.startswith("-o")):
            # The existing -o/-oN/-oX/... output flags also accept attached paths.
            return True
        if option == "--config":
            # Loading options from a file is unverified, including a missing value.
            return True
        if option == "--script":
            # No registered constraint verifies script selection, even for a
            # URL-looking expression, an attached value, or a missing value.
            return True
        if tool_name == "curl" and not token.startswith("--") and token[:2] in _CURL_VALUE_OPTIONS:
            option = token[:2]
            separator = token[2:]
            attached_value = token[2:]  # '-H=x' consumes '=x', not 'x'.
        if tool_name == "curl" and option in _CURL_VALUE_OPTIONS:
            value = attached_value if separator else (
                tokens[i + 1] if i + 1 < len(tokens) else None)
            if value is None:
                return True  # Missing option values cannot establish SAFE.
            if option in ("--data-binary", "--header", "-H") and value.startswith("@"):
                return True
            if option != "--data-binary" and (
                    _is_absolute_filesystem_path(value) or value.startswith(("./", "../"))):
                # Retain conservative path checks outside binary-data payloads.
                return True
            # A consumed value is never rescanned as an independent option.
            # Advance only over this option/value, not the next genuine option.
            i += 1 if separator else 2
            continue
        if option == "--data-binary":
            return True  # Curl's payload semantics cannot be borrowed by other tools.
        if tool_name == "curl" and token.startswith("-") and token not in _CURL_NO_VALUE_OPTIONS:
            return True  # Unknown arity: no safe inference about subsequent tokens.
        if _is_absolute_filesystem_path(token):
            return True
        # Relative‑path arguments cannot be verified against any trustworthy
        # definition; conservatively require approval.
        if token.startswith("./") or token.startswith("../"):
            return True
        i += 1
    return False


def _posix_command_head(command: str) -> tuple[str, Optional[str]]:
    """Recognize the first simple-command word after assignments/redirections.

    Inspect raw words so quoting the name or '=' prevents assignment recognition,
    while quoting an assignment's value is permitted. Only an unquoted adjacent
    numeric token is an IO descriptor. Backtick contents remain opaque. Stop at
    unsupported operators or comments; never search later commands or mistake a
    redirection's operand or substitution contents for an outer command.
    shlex validates ordinary quotes, not quoting inside shell substitutions.
    Match contiguously and reject incomplete words; never resume after a gap.
    Active $() stops recognition without traversing its unsupported contents.
    A recognized head before unsupported trailing text still establishes an
    outer wrapper, but fragments of an unsupported leading word never do.
    """
    raw_tokens = []
    position = 0
    while position < len(command):
        if command[position] in " \t":
            position += 1
            continue
        match = _POSIX_RAW_TOKEN_RE.match(command, position)
        if match is None:
            break
        if (match.group()[0] not in ";&|<>()\n" and match.end() < len(command)
                and command[match.end()] not in " \t\n;&|<>()"):
            # A prefix such as FOO= or su before an unmatched quote is not a
            # complete shell word. Do not keep it or search inside the quote.
            break
        raw_tokens.append(match)
        position = match.end()
    index = 0
    assignment = None
    while index < len(raw_tokens):
        raw = raw_tokens[index].group()
        if raw.startswith("#"):
            return "", assignment
        if _ENV_ASSIGNMENT_RE.match(raw):
            if assignment is None:
                assignment = raw
            index += 1
            continue
        if (re.fullmatch(r"[0-9]+", raw) and index + 1 < len(raw_tokens)
                and raw_tokens[index].end() == raw_tokens[index + 1].start()
                and raw_tokens[index + 1].group() in _POSIX_REDIRECTIONS):
            index += 1
            raw = raw_tokens[index].group()
        if raw in _POSIX_REDIRECTIONS:
            index += 1
            if index >= len(raw_tokens):
                return "", assignment
            operand = raw_tokens[index].group()
            if operand.startswith("#") or operand[0] in ";&|<>()\n":
                return "", assignment
            index += 1
            continue
        if raw[0] in ";&|<>()\n":
            return "", assignment
        # This is already one raw shell word. Decode its quotes/escapes without
        # letting shlex's default whitespace discard literal carriage returns.
        word = shlex.shlex(raw, posix=True)
        word.whitespace = ""
        word.whitespace_split = True
        word.commenters = ""
        return next(word, ""), assignment
    return "", assignment


def _posix_shell_feature(command: str) -> Optional[str]:
    """Refine only a continued first dollar feature, without rewriting input.

    The shared detector reports '$' followed by a backslash as variable expansion.
    Mirror its quote state only to locate that first feature, not to parse $().
    Keep earlier features and conservative escaped-dollar diagnostics unchanged.
    """
    feature = find_shell_syntax(command)
    if feature != "variable_expansion" or "\\\n" not in command:
        return feature
    in_single = False
    in_double = False
    for position, ch in enumerate(command):
        if in_single:
            if ch == "'":
                in_single = False
        elif ch == '"':
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = True
        elif ch == "$" and position + 1 < len(command):
            escapes = 0
            while position > escapes and command[position - escapes - 1] == "\\":
                escapes += 1
            if escapes % 2 == 0 and _POSIX_DOLLAR_SUBSTITUTION_RE.match(command, position):
                return "command_substitution"
            return feature
    return feature


def _base_details(request: ExecutionRequest) -> dict:
    """Observability details; privilege_level is recorded but never trusted."""
    return {
        "supplied_privilege_level": request.privilege_level or "SAFE",
        # privilege_level is recorded for auditability only and is never used
        # to establish that an operation is safe.
    }


def classify_operation(request: ExecutionRequest) -> ClassificationResult:
    """Classify the privilege risk of the actual execution operation.

    Pure and side-effect-free: no command is executed, no file or permission
    is modified, and no database or audit-log write occurs.  The caller-supplied
    ``request.privilege_level`` is never trusted as proof of safety.
    """
    details = _base_details(request)

    # Match execution-mode selection: an empty argv falls back to command mode.
    # Do not coerce malformed executable/argument values into legitimate strings.
    if request.argv:
        if not isinstance(request.argv, list) or any(
            not isinstance(a, str) or "\x00" in a for a in request.argv
        ) or not request.argv[0]:
            return ClassificationResult(OperationRisk.DENY, "invalid argv", details)
        return _classify_argv(request, request.argv, details)
    return _classify_command(request, details)


def _classify_argv(request, argv, details) -> ClassificationResult:
    """Classify a shell-free (argv) execution request.

    ``argv`` elements are literal arguments passed with ``shell=False``, so
    their contents are never re-interpreted as shell syntax.
    """
    details["mode"] = "argv"
    binary = argv[0]
    binary_name = _normalize_binary_basename(binary)
    details["binary"] = binary

    if binary_name in _PRIVILEGE_WRAPPERS:
        return ClassificationResult(
            OperationRisk.DENY,
            "privilege wrapper executed directly",
            {**details, "wrapper": binary_name},
        )
    if binary_name in _INTERPRETERS:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "interpreter - arbitrary script execution",
            {**details, "interpreter": binary_name},
        )

    return _classify_registered_operation(request, argv, details)


def _classify_command(request, details) -> ClassificationResult:
    """Classify a shell-interpreted command-string request."""
    details["mode"] = "shell"
    if request.command is not None and not isinstance(request.command, str):
        return ClassificationResult(OperationRisk.DENY, "invalid command", details)
    # Trim only shell blanks; str.strip() would also remove literal Unicode
    # whitespace or carriage returns and could change the executable identity.
    command = (request.command or "").strip(" \t")
    if not command.strip():
        return ClassificationResult(OperationRisk.DENY, "empty command", details)
    if "\x00" in command:
        return ClassificationResult(OperationRisk.DENY, "invalid command (NUL)", details)
    if _IS_WINDOWS:
        return _classify_windows_command(request, command, details)

    # shlex checks ordinary quoting; raw recognition also refuses incomplete
    # words in unsupported substitution contexts before wrapper detection.
    try:
        tokens = shlex.split(command)
        first, assignment = _posix_command_head(command)
    except ValueError:
        return ClassificationResult(OperationRisk.DENY, "unparsable command", details)
    if not tokens or not tokens[0]:
        return ClassificationResult(OperationRisk.DENY, "empty command after parsing", details)

    details["argv"] = tokens
    binary_name = _normalize_binary_basename(first)
    if binary_name in _PRIVILEGE_WRAPPERS:
        return ClassificationResult(
            OperationRisk.DENY, "privilege wrapper", {**details, "wrapper": binary_name},
        )
    feature = _posix_shell_feature(command)
    if feature is not None:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            f"shell syntax ({feature}) - behaviour not safely established",
            {**details, "shell_feature": feature},
        )
    if not first:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "POSIX outer command boundary not established", details,
        )
    if _UNVERIFIED_POSIX_SYNTAX_RE.search(command):
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "POSIX expansion, escaping or grouping context not verified",
            details,
        )
    if assignment is not None:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "environment-variable assignment modifies execution environment",
            {**details, "assignment": assignment},
        )
    if binary_name in _INTERPRETERS:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "interpreter - arbitrary script execution",
            {**details, "interpreter": binary_name},
        )

    # LocalBackend._normalise_command() applies unrestricted str.strip() before
    # launch. We already removed ordinary shell blanks, but any extra trimming
    # could change a literal executable or argument. Reject, never rewrite it.
    # Bare Python rewrites cannot reach SAFE (interpreter/absolute-path checks).
    if command != command.strip():
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "backend command normalization could change verified command identity",
            details,
        )

    return _classify_registered_operation(request, tokens, details)


def _classify_windows_command(request, command, details) -> ClassificationResult:
    """No POSIX parsing of Windows shell strings; recognize only obvious risks."""
    # cmd.exe's unquoted leading @ suppresses echo; it is not part of the
    # executable. Do not strip @ inside a quoted word or from argv mode.
    head_command = command[1:].lstrip(" \t") if command.startswith("@") else command
    head = _WINDOWS_HEAD_RE.match(head_command)
    first = (head.group(1) or head.group(2)) if head else ""
    name = _normalize_binary_basename(first)
    if name in _PRIVILEGE_WRAPPERS:
        return ClassificationResult(
            OperationRisk.DENY, "privilege wrapper", {**details, "wrapper": name},
        )
    # Preserve UNKNOWN for a plainly unknown executable with no shell features
    # or supplied context. Quoting, expansion, operators and registered commands
    # remain unverified, irrespective of what shlex would do with the string.
    if (head and _WINDOWS_PLAIN_COMMAND_RE.fullmatch(command)
            and name not in _INTERPRETERS
            and not (tool_registry.tools.get(name) or _tool_by_binary(name))
            and not (request.env or request.cwd or request.stdin is not None
                     or request.capability or request.tool_name)):
        return ClassificationResult(
            OperationRisk.UNKNOWN, "unknown command - no basis to classify",
            {**details, "binary": first},
        )
    return ClassificationResult(
        OperationRisk.APPROVAL_REQUIRED,
        "Windows shell parsing/context not verified - use shell-free argv",
        details,
    )


def _classify_registered_operation(request, tokens, details) -> ClassificationResult:
    """Only SAFE route: metadata, current file identity and limited risk checks.

    This does not verify executable contents, inherited context or launch-time
    identity, nor does it prove comprehensive argument/command safety.
    """
    if request.env:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "environment variables supplied - execution environment not verified",
            {**details, "env": dict(request.env)},
        )
    if request.cwd or request.stdin is not None:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "supplied working directory or stdin context not verified", details,
        )

    binary = tokens[0]  # Preserve the full executable token, not its basename.
    details["binary"] = binary
    capability = (request.capability or "").strip()
    tool_name = (request.tool_name or "").strip()
    if capability or tool_name:
        tool = tool_registry.tools.get(tool_name)
        details.update(capability=capability, tool=tool_name)
        if not capability or tool is None or capability not in tool.capabilities:
            return ClassificationResult(
                OperationRisk.APPROVAL_REQUIRED,
                "execution without verified capability binding", details,
            )
    else:
        name = _normalize_binary_basename(binary)
        tool = tool_registry.tools.get(name) or _tool_by_binary(name)
    if tool is None:
        return ClassificationResult(
            OperationRisk.UNKNOWN, "unknown executable - no basis to classify", details,
        )

    priv = (tool.privilege_requirement or "").upper()
    risk = (tool.risk_level or "").upper()
    details.update(tool=tool.tool_name, privilege=priv, risk_level=risk)
    if priv != "SAFE":
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            f"registered privilege requirement not SAFE ({priv or 'unspecified'})", details,
        )
    # SAFE and LOW are the existing low-risk metadata values, not new levels.
    if risk not in ("SAFE", "LOW"):
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            f"registered tool risk not verified as low ({risk or 'unspecified'})", details,
        )
    platform = "windows" if _IS_WINDOWS else _PLATFORM
    if platform not in tool.os_compatibility:
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "registered tool is not verified for this platform", details,
        )
    if not _is_absolute_executable_path(binary):
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            f"{details['mode']} executable resolution not verified - require an absolute executable", details,
        )
    if not _binary_matches_tool(binary, tool):
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "executable identity not verified against an absolute registered binary", details,
        )
    if _arguments_require_approval(tokens, tool.tool_name):
        return ClassificationResult(
            OperationRisk.APPROVAL_REQUIRED,
            "arguments carry unverified filesystem/write risk or option boundaries", details,
        )

    return ClassificationResult(
        OperationRisk.SAFE,
        f"verified registered low-risk tool ({details['mode']})", details,
    )
