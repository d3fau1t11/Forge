"""Deterministic duplicate-work prevention via normalized signatures (Phase 4 §8).

A first, deliberately simple implementation: normalize a command or a task into a
stable signature string, so trivially-different reruns (extra whitespace, casing,
surrounding quotes) collapse to the same signature. The coordinator refuses to
dispatch a task whose signature it has already seen, and seeds each agent with the
set of already-attempted command signatures so a specialist does not repeat work
another specialist has already done.

This is intentionally NOT perfect or clever — determinism and testability win over
recall. The per-session ``RepetitionDetector`` in the agent runtime handles the
finer-grained "same agent looping" case; this handles the cross-agent case.
"""
from __future__ import annotations

import re
from typing import Iterable

_WS = re.compile(r"\s+")
_TRIM = " \t\r\n'\"`.;,"


def normalize_command(command: str) -> str:
    """Collapse a shell command to a stable signature.

    Lowercases, strips surrounding quotes/punctuation, and collapses internal
    whitespace so ``nmap  -sV   TARGET`` and ``nmap -sv target`` share a signature.
    """
    if not command:
        return ""
    s = command.strip().strip(_TRIM).lower()
    s = _WS.sub(" ", s)
    return s


def normalize_text(text: str) -> str:
    """Normalize free text (a task objective) to a stable comparison key."""
    if not text:
        return ""
    s = text.strip().lower()
    s = _WS.sub(" ", s)
    return s


def task_signature(role: str, objective: str) -> str:
    """A signature for a task: role + normalized objective.

    Follow-up tasks the supervisor generates from identical leads use templated
    objectives, so an identical lead yields an identical signature and is deduped.
    """
    return f"{str(role).strip().lower()}::{normalize_text(objective)}"


def is_duplicate(signature: str, seen: Iterable[str]) -> bool:
    return bool(signature) and signature in set(seen)
