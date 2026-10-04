"""Durable provider-call telemetry (ProviderUsageModel).

Before this, a provider attempt lived only as a Python log line and (for fallbacks) a
transient WebSocket banner, so a call that succeeded quietly — or failed without
triggering a visible fallback — left no queryable history. These tests drive the real
``ModelRouter.route_request`` public entry point against fake providers and assert that
every attempt writes exactly one ``provider_usage`` row with the right outcome, and that
a logging-DB failure can never change the result handed back to the caller.

Network is never touched and no real quota is spent (fake providers, isolated DB).
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# Pinned above the first backend import on purpose. Never point this at forge.db.
os.environ["DATABASE_URL"] = "sqlite:///./test_forge.db"

from backend.database.session import init_db, SessionLocal
from backend.database.models import ProviderUsageModel
from backend.providers.base import ProviderResponse
from backend.providers.router import model_router
from backend.providers.quota_manager import quota_manager


_CAP = "provider_usage_test_capability"
_PROVIDERS = (
    "pu_test_ok",
    "pu_test_rl",
    "pu_test_x",
    "pu_test_y",
    "pu_test_direct",
    "pu_test_db",
)
_DIRECT_MODEL = "pu-test-direct-model"


class _FakeProvider:
    """Minimal provider whose outcome (response or raised exception) is scripted."""

    def __init__(self, name, response=None, exc=None, default_model="pu-fake-model"):
        self.name = name
        self.is_paid = False
        self.speed_tier = "fast"
        self.default_model = default_model
        self.base_url = ""
        self._response = response
        self._exc = exc
        self.calls = 0

    async def is_available(self):
        return True

    async def generate_response(self, prompt, system_instruction=None,
                                capability="general_reasoning", model=None, **kwargs):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._response


def _response(provider, model, content="ok", is_refusal=False, reason=None,
              prompt_tokens=0, completion_tokens=0, cost=0.0):
    return ProviderResponse(
        provider_name=provider,
        model_name=model,
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=cost,
        is_refusal=is_refusal,
        refusal_reason=reason,
    )


async def _route_and_settle(**kwargs):
    """Run a route_request, then yield once so scheduled fallback notices run."""
    result = await model_router.route_request(**kwargs)
    await asyncio.sleep(0)
    return result


class TestProviderUsagePersistence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    # -- helpers ----------------------------------------------------------- #

    def _cleanup(self):
        for name in _PROVIDERS:
            quota_manager._session_blacklisted.pop(name, None)
            quota_manager._consecutive_429_counts.pop(name, None)
        db = SessionLocal()
        try:
            (db.query(ProviderUsageModel)
               .filter(ProviderUsageModel.provider_name.in_(_PROVIDERS))
               .delete(synchronize_session=False))
            db.commit()
        finally:
            db.close()

    def _rows(self, provider_name):
        db = SessionLocal()
        try:
            return (db.query(ProviderUsageModel)
                      .filter(ProviderUsageModel.provider_name == provider_name)
                      .order_by(ProviderUsageModel.timestamp.asc())
                      .all())
        finally:
            db.close()

    def _run_capability(self, providers, names):
        with patch.dict(model_router.DEFAULT_ROUTING_MAP, {_CAP: names}), \
             patch.object(model_router, "providers", providers), \
             patch("backend.providers.router._notify_fallback", new=AsyncMock()):
            return asyncio.run(_route_and_settle(
                prompt="trace the target", capability=_CAP))

    # -- tests ------------------------------------------------------------- #

    def test_successful_call_writes_success_row(self):
        prov = _FakeProvider(
            "pu_test_ok",
            response=_response("pu_test_ok", "pu-model-1", content="done",
                               prompt_tokens=11, completion_tokens=22, cost=0.003),
        )
        result = self._run_capability({"pu_test_ok": prov}, ["pu_test_ok"])

        self.assertFalse(result.is_refusal)
        rows = self._rows("pu_test_ok")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].success)
        self.assertEqual(rows[0].model_name, "pu-model-1")
        self.assertEqual(rows[0].prompt_tokens, 11)
        self.assertEqual(rows[0].completion_tokens, 22)
        self.assertAlmostEqual(rows[0].cost_usd, 0.003)
        self.assertGreaterEqual(rows[0].latency_ms, 0.0)

    def test_direct_model_success_writes_success_row(self):
        prov = _FakeProvider(
            "pu_test_direct",
            response=_response("pu_test_direct", "pu-direct-model", content="direct"),
        )
        with patch.dict(model_router.MODEL_PROVIDER_MAP,
                        {_DIRECT_MODEL: ("pu_test_direct", "pu-direct-model")}), \
             patch.object(model_router, "providers", {"pu_test_direct": prov}), \
             patch("backend.providers.router._notify_fallback", new=AsyncMock()):
            result = asyncio.run(_route_and_settle(
                prompt="hi", capability="general_reasoning", target_model=_DIRECT_MODEL))

        self.assertFalse(result.is_refusal)
        rows = self._rows("pu_test_direct")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].success)
        self.assertEqual(rows[0].model_name, "pu-direct-model")

    def test_rate_limit_writes_failure_and_keeps_fallback_behavior(self):
        prov = _FakeProvider(
            "pu_test_rl",
            response=_response("pu_test_rl", "pu-rl-model", content="",
                               is_refusal=True, reason="HTTP 429 Rate Limit for pu_test_rl"),
        )
        notify = AsyncMock()
        with patch.dict(model_router.DEFAULT_ROUTING_MAP, {_CAP: ["pu_test_rl"]}), \
             patch.object(model_router, "providers", {"pu_test_rl": prov}), \
             patch("backend.providers.router._notify_fallback", new=notify):
            result = asyncio.run(_route_and_settle(
                prompt="trace the target", capability=_CAP))

        self.assertTrue(result.is_refusal)
        rows = self._rows("pu_test_rl")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].success)

        # Existing fallback behaviour is untouched: the WS notice still fires and the
        # 429 is still counted by the quota manager.
        notify.assert_called()
        self.assertEqual(quota_manager._consecutive_429_counts.get("pu_test_rl"), 1)

    def test_total_exhaustion_writes_one_failure_row_per_provider(self):
        providers = {
            "pu_test_x": _FakeProvider(
                "pu_test_x",
                response=_response("pu_test_x", "x-model", content="",
                                   is_refusal=True, reason="generic failure on x")),
            "pu_test_y": _FakeProvider(
                "pu_test_y",
                response=_response("pu_test_y", "y-model", content="",
                                   is_refusal=True, reason="generic failure on y")),
        }
        result = self._run_capability(providers, ["pu_test_x", "pu_test_y"])

        self.assertTrue(result.is_refusal)
        rows = self._rows("pu_test_x") + self._rows("pu_test_y")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not r.success for r in rows))
        self.assertEqual({r.provider_name for r in rows}, {"pu_test_x", "pu_test_y"})

    def test_db_write_failure_does_not_affect_returned_result(self):
        prov = _FakeProvider(
            "pu_test_db",
            response=_response("pu_test_db", "db-model", content="unaffected"),
        )
        with patch.dict(model_router.DEFAULT_ROUTING_MAP, {_CAP: ["pu_test_db"]}), \
             patch.object(model_router, "providers", {"pu_test_db": prov}), \
             patch("backend.database.session.SessionLocal", side_effect=RuntimeError("db down")), \
             patch("backend.providers.router._notify_fallback", new=AsyncMock()):
            result = asyncio.run(_route_and_settle(
                prompt="trace the target", capability=_CAP))

        # The provider result is returned exactly as if telemetry did not exist.
        self.assertFalse(result.is_refusal)
        self.assertEqual(result.content, "unaffected")
        self.assertEqual(self._rows("pu_test_db"), [])


if __name__ == "__main__":
    unittest.main()
