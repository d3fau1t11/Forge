"""
AgentRouter Quota Manager
==========================
Handles the AgentRouter daily batch quota system for Claude / GPT models.

Key Policy:
- Claude and GPT models have limited daily quotas, released in 2 batches:
  - Batch 1: Beijing 07:00 (UTC 23:00)
  - Batch 2: Beijing 19:00 (UTC 11:00)
- When quota is exhausted, API returns HTTP 402: "Budget pool quota has been exhausted."
- DeepSeek and GLM models are NOT quota-limited and remain always available.
- The system should auto-fallback to DeepSeek/GLM when Claude/GPT quota is exhausted.
"""

import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple, Set

logger = logging.getLogger("forge.quota_manager")

# Models that are subject to AgentRouter's batch quota system
QUOTA_LIMITED_MODELS = {
    "claude-opus-5",
    "claude-opus-4-8",
    "gpt-5.6",
    "gpt-5.6-sol",
}

# Models that are always available (no quota limit, routed via OpenRouter)
ALWAYS_AVAILABLE_MODELS = {
    "deepseek/deepseek-chat",
    "deepseek-v4-flash",
    "z-ai/glm-5.3-flash",
    "z-ai/glm-5.3",
    "glm-5.3",
    "glm-5.3-flash",
}

# Recommended fallback mapping: quota-limited model → always-available OpenRouter / Gemini alternative
QUOTA_FALLBACK_MAP: Dict[str, str] = {
    "claude-opus-5": "deepseek/deepseek-chat",
    "claude-opus-4-8": "deepseek/deepseek-chat",
    "gpt-5.6": "deepseek/deepseek-chat",
    "gpt-5.6-sol": "z-ai/glm-5.3-flash",
}

# Batch replenishment schedule in UTC hours
BATCH_REPLENISH_UTC_HOURS = [23, 11]  # Beijing 07:00 and 19:00


class AgentRouterQuotaManager:
    """Tracks AgentRouter quota exhaustion and provides intelligent fallback routing."""

    def __init__(self):
        # Tracks when each model family was last observed as exhausted
        self._exhaustion_timestamps: Dict[str, float] = {}
        self._consecutive_402_counts: Dict[str, int] = {}
        self._quota_exhausted_globally = False
        self._last_global_exhaustion_ts: float = 0.0
        # Session-Wide Circuit Breaker: Models/providers temporarily blacklisted
        # Maps identifier -> (expiry_timestamp, reason)
        self._session_blacklisted: Dict[str, Tuple[float, str]] = {}

    def blacklist_for_session(self, identifier: str, reason: str = "Quota/RateLimit Exceeded"):
        """Temporarily blacklists a provider/model with a time-limited cooldown.
        - 429/rate-limit errors: 5-minute cooldown (transient, will recover)
        - 402/quota errors: 30-minute cooldown (likely won't recover soon, but not permanent)
        - 401/auth errors: 60-minute cooldown (likely config issue)
        """
        key = identifier.lower().strip()
        reason_lower = reason.lower()
        
        # Determine cooldown duration based on error type
        if "429" in reason or "rate" in reason_lower:
            cooldown_seconds = 300  # 5 minutes for rate limits
            error_type = "rate-limit"
        elif "402" in reason or "quota" in reason_lower or "budget" in reason_lower:
            cooldown_seconds = 1800  # 30 minutes for quota exhaustion
            error_type = "quota"
        elif "401" in reason or "unauthorized" in reason_lower:
            cooldown_seconds = 3600  # 60 minutes for auth errors
            error_type = "auth"
        else:
            cooldown_seconds = 300  # 5 minutes default
            error_type = "unknown"
        
        expiry = time.time() + cooldown_seconds
        self._session_blacklisted[key] = (expiry, reason)
        logger.warning(
            f"[CircuitBreaker] ⛔ Provider '{identifier}' blacklisted for {cooldown_seconds}s ({error_type}). Reason: {reason[:120]}"
        )

    def is_blacklisted_for_session(self, identifier: str) -> bool:
        """Returns True if model/provider is currently blacklisted (not expired)."""
        key = identifier.lower().strip()
        if key not in self._session_blacklisted:
            return False
        expiry, reason = self._session_blacklisted[key]
        if time.time() >= expiry:
            # Blacklist expired — provider can be retried
            del self._session_blacklisted[key]
            logger.info(f"[CircuitBreaker] ✅ Provider '{identifier}' blacklist expired, re-enabled.")
            return False
        return True

    def reset_session_blacklists(self):
        """Clears all session blacklists."""
        self._session_blacklisted.clear()
        logger.info("[CircuitBreaker] Session blacklists reset.")

    def is_quota_limited_model(self, model: str) -> bool:
        """Check if a model is subject to AgentRouter batch quota limits."""
        return model in QUOTA_LIMITED_MODELS

    def is_always_available_model(self, model: str) -> bool:
        """Check if a model is always available regardless of quota."""
        return model in ALWAYS_AVAILABLE_MODELS

    def record_quota_exhaustion(self, model: str, error_message: str = ""):
        """Record a 402 quota exhaustion event for a model."""
        now = time.time()
        self._exhaustion_timestamps[model] = now
        self._consecutive_402_counts[model] = self._consecutive_402_counts.get(model, 0) + 1

        # If it's a quota-limited model, set global exhaustion flag
        if self.is_quota_limited_model(model):
            self._quota_exhausted_globally = True
            self._last_global_exhaustion_ts = now

        next_batch = self.get_next_batch_time_str()
        logger.warning(
            f"[QuotaManager] AgentRouter quota EXHAUSTED for model '{model}' "
            f"(consecutive 402s: {self._consecutive_402_counts[model]}). "
            f"Next batch replenishment: {next_batch}. "
            f"Error: {error_message[:150]}"
        )

    def record_successful_request(self, model: str):
        """Record a successful (non-402) request, clearing exhaustion state for the model."""
        if model in self._exhaustion_timestamps:
            del self._exhaustion_timestamps[model]
        if model in self._consecutive_402_counts:
            del self._consecutive_402_counts[model]

        # Clear global flag if no more quota-limited models are exhausted
        if not any(m in QUOTA_LIMITED_MODELS for m in self._exhaustion_timestamps):
            self._quota_exhausted_globally = False

    def is_model_exhausted(self, model: str) -> bool:
        """
        Check if a model is currently considered quota-exhausted.

        A model is considered exhausted if:
        - It received a 402 error during the current batch period
        - AND no scheduled batch replenishment (UTC 23:00 or 11:00) has occurred since
        """
        if model not in self._exhaustion_timestamps:
            return False

        last_402_ts = self._exhaustion_timestamps[model]

        # Check if a batch replenishment (UTC 23:00 or 11:00) has happened since the 402
        if self._has_batch_replenished_since(last_402_ts):
            # Quota refilled in new batch — clear the exhaustion record
            self.record_successful_request(model)
            return False

        return True

    def is_in_claude_allowed_window(self) -> bool:
        """
        Claude Code and GPT batch models are only permitted during the 3-hour window
        immediately following the trial/batch quota reset intervals (Beijing 07:00-10:00 and 19:00-22:00).
        In UTC: 23:00-02:00 (hours 23, 0, 1) and 11:00-14:00 (hours 11, 12, 13).
        Outside this 3-hour post-reset window, Claude and GPT models are strictly skipped in favor of Codex (DeepSeek/GLM).
        """
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        return current_hour in [23, 0, 1, 11, 12, 13]

    def should_skip_quota_limited_models(self) -> bool:
        """
        Returns True if Claude/GPT models should NOT be used:
        - If we are outside the 3-hour window following batch reset.
        - OR if batch quota exhaustion (402) was observed and next batch has not arrived.
        """
        if not self.is_in_claude_allowed_window():
            return True

        if not self._quota_exhausted_globally:
            return False

        if self._has_batch_replenished_since(self._last_global_exhaustion_ts):
            # Quota refilled
            self._quota_exhausted_globally = False
            return False

        return True

    def get_fallback_model(self, exhausted_model: str) -> Optional[str]:
        """Get the recommended always-available fallback for a quota-exhausted model."""
        return QUOTA_FALLBACK_MAP.get(exhausted_model)

    def get_next_batch_time_str(self) -> str:
        """Get a human-readable string of when the next quota batch will be released."""
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour

        # Find the next replenishment hour
        next_hour = None
        for h in sorted(BATCH_REPLENISH_UTC_HOURS):
            if h > current_hour:
                next_hour = h
                break

        if next_hour is None:
            # Next batch is tomorrow at the earliest hour
            next_hour = sorted(BATCH_REPLENISH_UTC_HOURS)[0]
            next_time = now_utc.replace(hour=next_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            next_time = now_utc.replace(hour=next_hour, minute=0, second=0, microsecond=0)

        # If we're past the minute mark of the current hour batch
        if next_time <= now_utc:
            # Move to next batch
            idx = BATCH_REPLENISH_UTC_HOURS.index(next_hour)
            if idx + 1 < len(BATCH_REPLENISH_UTC_HOURS):
                next_hour = BATCH_REPLENISH_UTC_HOURS[idx + 1]
                next_time = now_utc.replace(hour=next_hour, minute=0, second=0, microsecond=0)
            else:
                next_hour = BATCH_REPLENISH_UTC_HOURS[0]
                next_time = now_utc.replace(hour=next_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)

        delta = next_time - now_utc
        hours_left = int(delta.total_seconds() // 3600)
        mins_left = int((delta.total_seconds() % 3600) // 60)

        # Beijing time conversion
        beijing_tz = timezone(timedelta(hours=8))
        next_time_beijing = next_time.astimezone(beijing_tz)

        return (
            f"{next_time.strftime('%H:%M UTC')} / "
            f"{next_time_beijing.strftime('%H:%M Beijing')} "
            f"(~{hours_left}h {mins_left}m from now)"
        )

    def get_quota_status_summary(self) -> Dict:
        """Get a summary of current quota status for all AgentRouter models."""
        now = time.time()
        summary = {
            "quota_exhausted_globally": self._quota_exhausted_globally,
            "next_batch_replenishment": self.get_next_batch_time_str(),
            "batch_schedule_utc": ["23:00", "11:00"],
            "batch_schedule_beijing": ["07:00", "19:00"],
            "models": {}
        }

        for model in QUOTA_LIMITED_MODELS:
            is_exhausted = self.is_model_exhausted(model)
            last_402 = self._exhaustion_timestamps.get(model)
            summary["models"][model] = {
                "quota_limited": True,
                "currently_exhausted": is_exhausted,
                "last_402_ago_seconds": round(now - last_402, 1) if last_402 else None,
                "consecutive_402s": self._consecutive_402_counts.get(model, 0),
                "fallback_model": QUOTA_FALLBACK_MAP.get(model),
            }

        for model in ALWAYS_AVAILABLE_MODELS:
            summary["models"][model] = {
                "quota_limited": False,
                "currently_exhausted": False,
                "last_402_ago_seconds": None,
                "consecutive_402s": 0,
                "fallback_model": None,
            }

        return summary

    def _has_batch_replenished_since(self, since_timestamp: float) -> bool:
        """Check if any batch replenishment event has occurred since a given timestamp."""
        since_dt = datetime.fromtimestamp(since_timestamp, tz=timezone.utc)
        now_utc = datetime.now(timezone.utc)

        # Check each replenishment hour between since_dt and now
        check_date = since_dt.date()
        end_date = now_utc.date() + timedelta(days=1)

        while check_date <= end_date:
            for hour in BATCH_REPLENISH_UTC_HOURS:
                batch_time = datetime(
                    check_date.year, check_date.month, check_date.day,
                    hour, 0, 0, tzinfo=timezone.utc
                )
                if since_dt < batch_time <= now_utc:
                    return True
            check_date += timedelta(days=1)

        return False

    def detect_quota_error(self, error_text: str) -> bool:
        """Detect if an error string indicates quota exhaustion, rate limit, or budget depletion."""
        if not error_text:
            return False
        lower = error_text.lower()
        return (
            "402" in error_text
            or "budget pool quota has been exhausted" in lower
            or "quota exhausted" in lower
            or "quota has been exhausted" in lower
            or "exceeded the daily quota" in lower
            or "exceeded the quota" in lower
            or "exceeded your current quota" in lower
            or "daily quota" in lower
            or "insufficient_quota" in lower
            or "rate limit" in lower
            or "too many requests" in lower
            or ("quota" in lower and any(w in lower for w in ("exceeded", "limit", "zero", "depleted", "exhausted")))
        )


# Global singleton
quota_manager = AgentRouterQuotaManager()
