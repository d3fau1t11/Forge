"""Phase 6 §9 — contextual success statistics for learned techniques.

FORGE already tracks *global* success counters on every experience
(``times_successful`` / ``times_failed`` / EWMA ``success_rate``). This module adds
the missing **contextual** view the Phase-6 spec calls for:

    Technique X  overall success:                     62%
    Technique X  against Python web applications:     81%
    Technique X  against Windows targets:             18%

It is deliberately NOT a machine-learning model (Part 9 / Part 14): it is a pure,
deterministic aggregation over the experience rows that ``ExperienceMemory`` already
stores. No second database, no new table — it reads ``experiences`` and groups.

The numbers it returns are *advisory inputs* to Phase-5 scoring, never authority:
``CandidateGenerator`` blends a contextual success rate into a candidate's
``success_probability`` (and records it as observability provenance), but the
candidate still competes on current evidence and passes every capability / privilege
/ target gate before it can run.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from backend.database.session import SessionLocal
from backend.database.models import ExperienceModel

logger = logging.getLogger("forge.knowledge.technique_stats")

# Tokens too generic to establish that two techniques are "the same family".
_STOP = {
    "the", "and", "for", "with", "via", "from", "into", "using", "use", "run",
    "flag", "ctf", "challenge", "target", "solve", "methodology", "generic",
    "attack", "exploit", "technique", "test", "analysis", "based",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    """Significant lowercase tokens (len ≥ 3, minus stopwords)."""
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 3 and t not in _STOP}


def _significant(tokens: set) -> set:
    """The subset of tokens strong enough to anchor a family match (len ≥ 4)."""
    return {t for t in tokens if len(t) >= 4}


def _norm(text: str) -> str:
    return " ".join(sorted(_tokens(text)))


class TechniqueStats:
    """Deterministic contextual statistics over stored experiences (§9).

    Injectable for tests; production uses the module-level :data:`technique_stats`
    singleton. Every method is non-fatal — any DB error yields empty stats so a
    statistics failure can never break reasoning or a mission.
    """

    #: A contextual/global bucket needs at least this many distinct experiences before
    #: its success rate is trusted enough to influence scoring.
    DEFAULT_MIN_SAMPLES = 2

    # ------------------------------------------------------------------ #
    # Aggregation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _aggregate(rows: Sequence[Any]) -> Dict[str, Any]:
        """Fold a set of experience rows into one statistics bucket.

        Reads the row counters directly: ``store()`` seeds ``times_successful`` /
        ``times_failed`` from the initial outcome and ``record_feedback()`` increments
        them on each reuse, so ``times_successful + times_failed`` IS the total attempt
        count (initial solve + every reinforced reuse). A never-fed legacy row with
        zeroed counters contributes a single attempt derived from its stored outcome.
        This makes a technique that has been reused and kept working score higher than
        a one-off, with no learning model (Part 9 / Part 14).
        """
        sample = len(rows)
        attempts = successes = failures = 0
        last_used: Optional[datetime] = None
        last_success: Optional[datetime] = None
        for r in rows:
            succ = int(getattr(r, "times_successful", 0) or 0)
            fail = int(getattr(r, "times_failed", 0) or 0)
            if succ + fail == 0:
                # Legacy/unfed row — derive one attempt from the recorded outcome.
                if getattr(r, "outcome", "success") == "success":
                    succ = 1
                else:
                    fail = 1
            attempts += succ + fail
            successes += succ
            failures += fail
            lu = getattr(r, "last_used", None)
            if lu and (last_used is None or lu > last_used):
                last_used = lu
            created = getattr(r, "created_at", None)
            if succ and created and (last_success is None or created > last_success):
                last_success = created
        rate = round(successes / attempts, 3) if attempts else 0.0
        return {
            "sample_size": sample,
            "attempt_count": attempts,
            "success_count": successes,
            "failure_count": failures,
            "success_rate": rate,
            "last_used": last_used.isoformat() if last_used else None,
            "last_success": last_success.isoformat() if last_success else None,
        }

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_families(row: Any) -> set:
        """Tokens describing what technique family a row belongs to."""
        toks = _tokens(getattr(row, "technique", "") or "")
        for t in (getattr(row, "tags", None) or []):
            toks |= _tokens(str(t))
        for st in (getattr(row, "successful_techniques", None) or []):
            toks |= _tokens(str(st))
        return toks

    @classmethod
    def _matches_technique(cls, row: Any, q_tokens: set, q_sig: set) -> bool:
        row_toks = cls._row_families(row)
        if not row_toks or not q_tokens:
            return False
        # Same family iff they share a significant (len≥4) token, or the query's
        # significant tokens are a subset of the row's (a specific technique matching a
        # broader stored one). Purely lexical + deterministic.
        row_sig = _significant(row_toks)
        if q_sig and (q_sig & row_sig):
            return True
        if q_sig and q_sig <= row_toks:
            return True
        return False

    @staticmethod
    def _row_technologies(row: Any) -> set:
        techs = set(str(t).lower() for t in (getattr(row, "technologies", None) or []))
        tc = getattr(row, "target_characteristics", None) or {}
        if isinstance(tc, dict):
            techs |= set(str(t).lower() for t in (tc.get("technologies") or []))
        return techs

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def lookup(
        self,
        technique: str,
        *,
        category: Optional[str] = None,
        target_type: Optional[str] = None,
        technologies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return global + contextual statistics for *technique*.

        ``contextual`` is restricted to experiences in the same ``category`` and — when
        ``technologies`` are supplied — sharing at least one technology, which is the
        grain that distinguishes "technique X against Python web apps" from "against
        Windows targets" (§9). The result is JSON-serialisable for the observability
        API / reasoning snapshot.
        """
        q_tokens = _tokens(technique)
        q_sig = _significant(q_tokens)
        cat = (category or "").strip().lower()
        techs = set(t.lower() for t in (technologies or []) if t)

        result = {
            "technique": technique,
            "normalized": _norm(technique),
            "context": {"category": cat or None, "target_type": target_type or None,
                        "technologies": sorted(techs) or None},
            "global": self._aggregate([]),
            "contextual": self._aggregate([]),
        }
        if not q_tokens:
            return result

        db = SessionLocal()
        try:
            rows = db.query(ExperienceModel).all()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"[TechniqueStats] lookup skipped: {e}")
            db.close()
            return result
        try:
            matches = [r for r in rows if self._matches_technique(r, q_tokens, q_sig)]
            result["global"] = self._aggregate(matches)

            if cat or techs:
                ctx_rows = []
                for r in matches:
                    if cat and (getattr(r, "category", "") or "").lower() != cat:
                        continue
                    if techs and not (self._row_technologies(r) & techs):
                        continue
                    ctx_rows.append(r)
                result["contextual"] = self._aggregate(ctx_rows)
            else:
                # No context supplied → contextual == global.
                result["contextual"] = dict(result["global"])
            return result
        finally:
            db.close()

    def contextual_success_rate(
        self,
        technique: str,
        *,
        category: Optional[str] = None,
        target_type: Optional[str] = None,
        technologies: Optional[List[str]] = None,
        min_samples: Optional[int] = None,
    ) -> Any:
        """Return ``(rate | None, stats_dict)``.

        Prefers the contextual rate when there is enough contextual evidence, else the
        global rate when there is enough global evidence, else ``None`` (the caller
        should then fall back to the memory's own confidence — history is one input,
        never the sole authority; Part 7). Deterministic and non-fatal.
        """
        floor = self.DEFAULT_MIN_SAMPLES if min_samples is None else int(min_samples)
        try:
            stats = self.lookup(technique, category=category, target_type=target_type,
                                 technologies=technologies)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"[TechniqueStats] rate skipped: {e}")
            return None, {"technique": technique, "global": self._aggregate([]),
                          "contextual": self._aggregate([])}
        ctx = stats.get("contextual", {})
        glob = stats.get("global", {})
        if int(ctx.get("sample_size", 0)) >= floor:
            return ctx.get("success_rate"), stats
        if int(glob.get("sample_size", 0)) >= floor:
            return glob.get("success_rate"), stats
        return None, stats


# Module-level singleton used in production. Tests may construct their own instance or
# inject a double into the CandidateGenerator.
technique_stats = TechniqueStats()
