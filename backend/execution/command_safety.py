"""
Shell-syntax classification and shell-free command composition.

FORGE executes two structurally different kinds of command, and they must not be
conflated:

* **Agent-authored commands** (the default swarm's ReAct loop) are free-form shell
  strings.  Real CTF work needs pipes, redirection, and ``&&`` chaining, so the
  shell is genuinely required.  Those commands are the agent's own arbitrary
  action and are routed through an explicitly separated shell mechanism
  (``ProcessManager.run``); nothing here pretends a text filter makes arbitrary
  shell execution safe.
* **System-composed commands** (capability templates, extra tool arguments) mix
  *untrusted* values — a challenge target, caller-supplied args — into a command
  line.  Interpolating those into a shell string would let a target value such as
  ``127.0.0.1; rm -rf /`` append arbitrary commands.  Those callers compose an
  executable + **literal argv** with :func:`build_argv` and execute with
  ``shell=False`` (``ProcessManager.run_argv``), so no value is ever reinterpreted
  as shell syntax.

This module only classifies and composes strings; it never executes anything.
"""
from __future__ import annotations

import re
import shlex
from typing import Dict, List, Optional

# A conservative "plain host token": what a hostname/IP/domain looks like.  Used to
# refuse to splice anything containing shell metacharacters or whitespace into a
# command line during target typo-correction.
_PLAIN_HOST_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class UnsupportedShellSyntax(ValueError):
    """Raised when a shell-free execution path is handed shell syntax.

    Carries the detected *feature* (e.g. ``"command_substitution"``) so callers can
    reject it explicitly and observably instead of silently mis-executing.
    """

    def __init__(self, feature: str, command: str = ""):
        self.feature = feature
        self.command = command
        super().__init__(f"unsupported shell syntax '{feature}' in: {command[:120]!r}")


def is_plain_host(value: str) -> bool:
    """True iff *value* is a bare hostname/IP with no shell-significant characters."""
    return bool(value) and bool(_PLAIN_HOST_RE.match(value))


def find_shell_syntax(command: str) -> Optional[str]:
    """Return the name of the first shell construct in *command*, else ``None``.

    Quote-aware: metacharacters inside single quotes are literal and ignored;
    inside double quotes only ``$`` (expansion/substitution) and backticks remain
    active, matching POSIX semantics.  Covers the constructs that let a value
    escape its own command word: pipelines (``|``), chaining (``;``, ``&&``,
    ``||``, newline), redirection (``<``, ``>``), backgrounding (``&``), command
    substitution (backticks, ``$(...)``) and variable expansion (``$VAR`` /
    ``${...}``).
    """
    if not command:
        return None

    n = len(command)
    i = 0
    in_single = False
    in_double = False

    while i < n:
        ch = command[i]

        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            if ch == '"':
                in_double = False
            elif ch == "`":
                return "command_substitution"
            elif ch == "$" and i + 1 < n:
                # ``$`` is live inside double quotes (unlike |, ;, <, >).
                return "command_substitution" if command[i + 1] == "(" else "variable_expansion"
            i += 1
            continue

        # ── unquoted ──
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "`":
            return "command_substitution"
        elif ch == "$" and i + 1 < n:
            return "command_substitution" if command[i + 1] == "(" else "variable_expansion"
        elif ch == "|":
            return "command_chaining" if (i + 1 < n and command[i + 1] == "|") else "pipeline"
        elif ch == "&":
            return "command_chaining" if (i + 1 < n and command[i + 1] == "&") else "background"
        elif ch == ";":
            return "command_chaining"
        elif ch == "<" or ch == ">":
            return "redirection"
        elif ch == "\n" or ch == "\r":
            return "command_chaining"
        i += 1

    return None


def requires_shell(command: str) -> bool:
    """True iff *command* contains syntax that only a shell can interpret."""
    return find_shell_syntax(command) is not None


def split_simple_command(command: str) -> List[str]:
    """Split a *simple* command (no shell syntax) into an argv list.

    Raises :class:`UnsupportedShellSyntax` — never guesses — when the command
    contains shell operators, substitution, or expansion, because silently
    splitting those would change their meaning.
    """
    feature = find_shell_syntax(command)
    if feature is not None:
        raise UnsupportedShellSyntax(feature, command)
    return shlex.split(command.strip())


def parse_extra_args(extra_args: Optional[str]) -> List[str]:
    """Parse caller-supplied extra tool arguments as literal argv tokens.

    Capabilities never require shell operators; if *extra_args* contains any, it is
    rejected explicitly (raising :class:`UnsupportedShellSyntax`) rather than being
    handed to a shell.  Quoted values with spaces are preserved as single tokens.
    """
    if not extra_args or not extra_args.strip():
        return []
    feature = find_shell_syntax(extra_args)
    if feature is not None:
        raise UnsupportedShellSyntax(feature, extra_args)
    return shlex.split(extra_args.strip())


def substitute_template(template: str, values: Dict[str, str]) -> List[str]:
    """Tokenise a trusted *template* and substitute ``{placeholder}`` occurrences.

    Substitution happens **per token** and any substituted value stays inside that
    single token, so a value containing spaces or shell metacharacters still maps
    to exactly ONE argv element and is never re-split or re-interpreted.  A single
    regex pass means a value cannot itself be re-scanned for further placeholders.
    """
    tokens = shlex.split(template)
    if not values:
        return tokens
    pattern = re.compile(r"\{(\w+)\}")

    def _replace(match: "re.Match[str]") -> str:
        return values.get(match.group(0), match.group(0))

    return [pattern.sub(_replace, token) for token in tokens]


def build_argv(
    binary: str,
    template: str,
    *,
    target: Optional[str] = None,
    extra_args: Optional[str] = None,
    substitutions: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Compose ``[binary, *template-tokens, *extra-args]`` with literal values.

    *target* is substituted as a single argv element; *extra_args* is parsed to
    literal tokens (rejecting shell syntax).  The result is safe to pass to
    ``ProcessManager.run_argv`` (``shell=False``).
    """
    values: Dict[str, str] = dict(substitutions or {})
    if target is not None:
        values["{target}"] = str(target)
    argv = [binary] + substitute_template(template, values)
    argv += parse_extra_args(extra_args)
    return argv
