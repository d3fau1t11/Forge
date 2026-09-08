"""
Passive provider rate-limit awareness for FORGE.

Reads the rate-limit headers providers already return on real responses (NO probe calls)
and normalizes them into a RateLimitSnapshot. The critical job is getting the SCOPE right:
several providers (Groq notably) report a per-DAY request allowance in the same
`x-ratelimit-limit-requests` header other providers use for a per-minute limit. The only
reliable scope signal is the RESET window (`x-ratelimit-reset-requests`) — a reset measured
in hours/days is a daily bucket, not per-minute. Trusting a friendly "per-minute" label
here is exactly what made FORGE believe it had ~1,000 RPM of Groq headroom when the real
ceiling was a daily cap it had already hit once.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Compound duration tokens like "1h30m", "2m59.56s", "7.66s", "882ms", "1d".
_DUR_RE = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>ms|d|h|m|s)", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def parse_duration_seconds(text: Any) -> Optional[float]:
    """Parse a provider reset-duration string into seconds.

    Handles compound forms ('1h30m', '2m59.56s', '882ms', '1d') and bare numeric seconds
    ('60', '0.5'). Returns None when nothing parseable is present.
    """
    if text is None:
        return None
    t = str(text).strip().lower()
    if not t:
        return None
    try:
        return float(t)                         # bare seconds count
    except ValueError:
        pass
    total, matched = 0.0, False
    for m in _DUR_RE.finditer(t):
        matched = True
        total += float(m.group("val")) * _UNIT_SECONDS[m.group("unit").lower()]
    return total if matched else None


def classify_scope(reset_seconds: Optional[float]) -> str:
    """Infer the bucket scope from how far away the reset is:
    <=90s -> 'minute'; <=2h -> 'hour'; otherwise 'day'. 'unknown' when no reset is present."""
    if reset_seconds is None:
        return "unknown"
    if reset_seconds <= 90:
        return "minute"
    if reset_seconds <= 2 * 3600:
        return "hour"
    return "day"


@dataclass
class RateLimitSnapshot:
    provider: str
    limit_requests: Optional[float] = None
    remaining_requests: Optional[float] = None
    reset_requests_seconds: Optional[float] = None
    scope_requests: str = "unknown"
    limit_tokens: Optional[float] = None
    remaining_tokens: Optional[float] = None
    reset_tokens_seconds: Optional[float] = None
    scope_tokens: str = "unknown"
    retry_after_seconds: Optional[float] = None
    observed_at: float = 0.0
    raw: Dict[str, str] = field(default_factory=dict)

    def request_headroom_fraction(self) -> Optional[float]:
        if self.limit_requests and self.limit_requests > 0 and self.remaining_requests is not None:
            return max(0.0, min(1.0, self.remaining_requests / self.limit_requests))
        return None

    def token_headroom_fraction(self) -> Optional[float]:
        if self.limit_tokens and self.limit_tokens > 0 and self.remaining_tokens is not None:
            return max(0.0, min(1.0, self.remaining_tokens / self.limit_tokens))
        return None

    def summary(self) -> str:
        parts = []
        if self.limit_requests is not None:
            parts.append(f"req {self.remaining_requests:.0f}/{self.limit_requests:.0f} per-{self.scope_requests}")
        if self.limit_tokens is not None:
            parts.append(f"tok {self.remaining_tokens:.0f}/{self.limit_tokens:.0f} per-{self.scope_tokens}")
        if self.retry_after_seconds is not None:
            parts.append(f"retry-after {self.retry_after_seconds:.0f}s")
        return f"{self.provider}: " + (", ".join(parts) if parts else "no rate-limit headers")


def _to_float(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


# Header-name variants across OpenAI-compatible providers (compared lowercased).
_H_LIMIT_REQ = ("x-ratelimit-limit-requests", "x-ratelimit-limit-request", "ratelimit-limit")
_H_REMAIN_REQ = ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-request", "ratelimit-remaining")
_H_RESET_REQ = ("x-ratelimit-reset-requests", "x-ratelimit-reset-request", "ratelimit-reset")
_H_LIMIT_TOK = ("x-ratelimit-limit-tokens", "x-ratelimit-limit-token")
_H_REMAIN_TOK = ("x-ratelimit-remaining-tokens", "x-ratelimit-remaining-token")
_H_RESET_TOK = ("x-ratelimit-reset-tokens", "x-ratelimit-reset-token")


def _first(low: Dict[str, str], names) -> Optional[str]:
    for n in names:
        if n in low:
            return low[n]
    return None


def parse_ratelimit_headers(provider: str, headers: Any) -> Optional[RateLimitSnapshot]:
    """Build a RateLimitSnapshot from a response's headers (dict or httpx.Headers).
    Returns None when no recognizable rate-limit headers are present."""
    if not headers:
        return None
    try:
        items = list(headers.items())
    except AttributeError:
        return None
    low = {str(k).lower(): str(v) for k, v in items}

    limit_req = _to_float(_first(low, _H_LIMIT_REQ))
    remain_req = _to_float(_first(low, _H_REMAIN_REQ))
    reset_req = parse_duration_seconds(_first(low, _H_RESET_REQ))
    limit_tok = _to_float(_first(low, _H_LIMIT_TOK))
    remain_tok = _to_float(_first(low, _H_REMAIN_TOK))
    reset_tok = parse_duration_seconds(_first(low, _H_RESET_TOK))
    retry_after = parse_duration_seconds(low.get("retry-after"))

    if all(v is None for v in (limit_req, remain_req, reset_req, limit_tok, remain_tok, retry_after)):
        return None

    kept = {k: v for k, v in low.items() if "ratelimit" in k or k == "retry-after"}
    return RateLimitSnapshot(
        provider=provider,
        limit_requests=limit_req,
        remaining_requests=remain_req,
        reset_requests_seconds=reset_req,
        scope_requests=classify_scope(reset_req),
        limit_tokens=limit_tok,
        remaining_tokens=remain_tok,
        reset_tokens_seconds=reset_tok,
        scope_tokens=classify_scope(reset_tok),
        retry_after_seconds=retry_after,
        observed_at=time.time(),
        raw=kept,
    )
