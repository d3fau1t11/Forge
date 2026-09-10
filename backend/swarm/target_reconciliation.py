"""
Phase 7 — Authoritative target reconciliation on resume (STEP 2 / STEP 5 / STEP 10).

A CTF instance frequently gets a NEW address between sessions — HackTheBox /
TryHackMe respawns, a docker restart, a re-issued VPN lease, an operator editing the
target. When a mission is *resumed*, the target the operator supplies NOW must win
over whatever was frozen into the checkpoint. FORGE must never execute against an old
target merely because it exists in a checkpoint.

This module is pure and deterministic — no I/O, no network, no DB. It only compares
two target specifications and reports whether the authoritative (current) target has
changed and which host tokens from the previous target are now stale. Callers apply
the result to their own state (``SharedMissionState.adopt_authoritative_target`` for
the coordinated engine, ``SwarmBlackboard.reconcile_target`` for the default engine).

Detection reuses the existing conservative :class:`TargetDetector` and
``dedup.normalize_target`` so a superficially different spelling of the *same* target
(``http://h:80/`` vs ``h``) is correctly treated as unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

from backend.swarm.dedup import normalize_target

try:  # execution layer is optional at import time; degrade to normalize-only matching
    from backend.execution.targets import target_detector as _target_detector
except Exception:  # pragma: no cover - defensive
    _target_detector = None


def _split(spec: str) -> List[str]:
    """Split a possibly multi-target ('+'-joined) spec into trimmed components."""
    return [t.strip() for t in (spec or "").split("+") if t.strip()]


def _normalized_set(spec: str) -> Set[str]:
    """Set of normalized identities for each component of a spec (scheme/port/slash
    insensitive), used for the changed/unchanged decision."""
    out: Set[str] = set()
    for part in _split(spec):
        n = normalize_target(part)
        if n:
            out.add(n)
    return out


def hosts_of(spec: str) -> Set[str]:
    """Best-effort set of host tokens for a target spec.

    Includes the normalized identity, the host:port and bare-host slices of it, and
    (when available) the :class:`TargetDetector`'s parsed host. Always non-guessing —
    an empty/opaque spec simply yields fewer tokens.
    """
    hosts: Set[str] = set()
    for part in _split(spec):
        n = normalize_target(part)
        if n:
            hosts.add(n)
            hostport = n.split("/", 1)[0]      # strip any path
            hosts.add(hostport)
            hosts.add(hostport.split(":", 1)[0])  # strip any port
        if _target_detector is not None:
            try:
                t = _target_detector.detect(part)
                if getattr(t, "host", ""):
                    hosts.add(t.host)
            except Exception:
                pass
    return {h for h in hosts if h}


@dataclass
class TargetReconciliation:
    """The outcome of comparing a checkpoint target against the current one."""
    changed: bool
    authoritative: str
    previous: str = ""
    stale_hosts: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "changed": self.changed,
            "authoritative": self.authoritative,
            "previous": self.previous,
            "stale_hosts": list(self.stale_hosts),
            "reason": self.reason,
        }


def reconcile_target(current: str, persisted: str) -> TargetReconciliation:
    """Decide whether the authoritative target changed between a checkpoint and now.

    * ``current``  — the target supplied by the operator/API for THIS resumed run.
    * ``persisted`` — the target frozen into the checkpoint / mission state.

    When ``current`` is empty we cannot make it authoritative, so the persisted target
    is kept unchanged (a resume with no fresh target is a legitimate continuation of
    the same engagement). Otherwise the *current* target is always authoritative;
    ``changed`` is True only when it differs (after normalization) from ``persisted``.
    ``stale_hosts`` are host tokens present in the previous target but not the current
    one — the callers use them to invalidate host-specific state.
    """
    cur = (current or "").strip()
    prev = (persisted or "").strip()
    if not cur:
        return TargetReconciliation(
            changed=False, authoritative=prev, previous=prev,
            reason="no current target supplied; persisted target kept")
    if not prev or _normalized_set(cur) == _normalized_set(prev):
        return TargetReconciliation(
            changed=False, authoritative=cur, previous=prev, reason="target unchanged")
    stale = sorted(hosts_of(prev) - hosts_of(cur))
    return TargetReconciliation(
        changed=True, authoritative=cur, previous=prev, stale_hosts=stale,
        reason=f"authoritative target changed from '{prev}' to '{cur}'")


def references_stale_host(text: str, stale_hosts: List[str]) -> bool:
    """True if *text* references any stale host token (case-insensitive substring).

    Deliberately conservative: only state that literally names an old host is treated
    as stale. Relative paths (``/login``), techniques, and non-host facts (``nginx``)
    contain no stale host and are therefore preserved across a target change.
    """
    if not text or not stale_hosts:
        return False
    low = str(text).lower()
    return any(h and h.lower() in low for h in stale_hosts)
