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


def action_signature(capability: str, target: str = "", params: str = "") -> str:
    """A normalized signature for a concrete ACTION (Phase 5 §8).

    Unlike :func:`task_signature` (role + objective prose), this keys on the
    *capability being exercised*, the *target*, and the *normalized parameters* — so
    three agents each running ``nmap -sV target`` (i.e. the ``port_scan`` capability
    against the same host) collapse to one signature even if their task objectives
    were worded differently. It deliberately does not depend on raw command-string
    equality (§8): ``nmap  -sV  TARGET`` and ``nmap -sv target`` share a signature.

    The ``target`` is normalized to its host/path identity (scheme, default ports and
    a trailing slash are dropped) so ``http://t.ctf:80/`` and ``t.ctf`` match.
    """
    cap = normalize_text(capability)
    tgt = normalize_target(target)
    prm = normalize_command(params)
    return f"{cap}::{tgt}::{prm}"


def normalize_target(target: str) -> str:
    """Collapse a target string to a stable host/path identity for signatures.

    Drops the URL scheme, a default :80/:443 port, and a bare trailing slash so
    superficially different spellings of the same target share a signature. Multi-
    target ``+`` specs keep every component (order-normalized) so they stay distinct.
    """
    if not target:
        return ""
    parts = [normalize_text(p) for p in str(target).split("+")]
    out = []
    for s in parts:
        if not s:
            continue
        s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)   # strip scheme
        s = re.sub(r":(80|443)(/|$)", r"\2", s)          # drop default ports
        if len(s) > 1:
            s = s.rstrip("/")
        out.append(s)
    return "+".join(sorted(out)) if len(out) > 1 else (out[0] if out else "")


def is_duplicate(signature: str, seen: Iterable[str]) -> bool:
    return bool(signature) and signature in set(seen)
