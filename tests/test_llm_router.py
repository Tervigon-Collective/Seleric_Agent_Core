"""Tests for client-side rate limiting + task-tier routing (src/seleric_mcp/llm)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from seleric_mcp.llm import LLMRouter, RateLimit, RateLimitExceeded, estimate_tokens
from seleric_mcp.llm.rate_limit import ModelLimiter, TokenBucket


# --- TokenBucket ------------------------------------------------------------


def test_bucket_consumes_until_empty_then_refills():
    now = [0.0]
    b = TokenBucket(capacity=10, refill_per_sec=1.0)
    # Monkeypatch the clock by driving monotonic via adjust math is awkward;
    # instead exercise the public contract: full bucket allows capacity, then
    # denies, and time_until reports the wait.
    assert b.try_consume(10) is True
    assert b.try_consume(1) is False
    # 3 tokens needed at 1/sec -> ~3s (bucket empty)
    assert b.time_until(3) == pytest.approx(3.0, abs=0.2)


def test_bucket_time_until_clamps_oversized_request():
    b = TokenBucket(capacity=100, refill_per_sec=10.0)
    b.try_consume(100)  # empty it
    # asking for more than capacity waits at most a full refill, never forever
    assert b.time_until(1000) == pytest.approx(10.0, abs=0.5)


def test_bucket_zero_refill_is_infinite_when_empty():
    b = TokenBucket(capacity=5, refill_per_sec=0.0)
    b.try_consume(5)
    assert b.time_until(1) == float("inf")


# --- ModelLimiter -----------------------------------------------------------


def test_limiter_acquire_is_all_or_nothing():
    lim = ModelLimiter("m", RateLimit(requests_per_minute=100, tokens_per_minute=10))
    # first acquire uses the whole token budget
    assert lim.try_acquire(10) is True
    # second fails on tokens; must NOT have consumed a request slot
    before = lim.requests.available
    assert lim.try_acquire(10) is False
    assert lim.requests.available == pytest.approx(before, abs=0.01)


def test_limiter_reconcile_charges_the_difference():
    lim = ModelLimiter("m", RateLimit(requests_per_minute=100, tokens_per_minute=1000))
    lim.try_acquire(100)  # estimate 100
    after_est = lim.tokens.available
    lim.reconcile(est_tokens=100, actual_tokens=250)  # real usage higher
    # extra 150 tokens should have been drawn down
    assert lim.tokens.available == pytest.approx(after_est - 150, abs=1.0)


# --- estimate_tokens --------------------------------------------------------


def test_estimate_includes_reserve():
    msgs = [{"role": "user", "content": "hello world"}]
    est = estimate_tokens(msgs, tools=None, completion_reserve=500)
    assert est > 500  # reserve plus some prompt tokens


# --- Router -----------------------------------------------------------------


class FakeRateLimit(Exception):
    status_code = 429


def _resp(text: str = "ok", total_tokens: int = 50) -> Any:
    msg = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=msg)
    usage = SimpleNamespace(total_tokens=total_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


class FakeClient:
    """Records which models were called and can be scripted to raise 429s."""

    def __init__(self, script: dict[str, Any] | None = None) -> None:
        self.calls: list[str] = []
        self.script = script or {}
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, *, model: str, **kwargs: Any) -> Any:
        self.calls.append(model)
        behavior = self.script.get(model)
        if behavior == "429":
            raise FakeRateLimit("Too Many Requests")
        return _resp(total_tokens=50)


def _router(client: FakeClient, **overrides: Any) -> LLMRouter:
    return LLMRouter(
        client,
        tiers={"tools": ["A", "B"], "reasoning": ["R1", "R2"]},
        fallback="K",
        default_limit=RateLimit(requests_per_minute=20, tokens_per_minute=20000),
        **overrides,
    )


def test_router_picks_first_tier_model():
    client = FakeClient()
    r = _router(client)
    out = r.complete(tier="tools", messages=[{"role": "user", "content": "hi"}])
    assert out.model == "A"
    assert client.calls == ["A"]


def test_router_fails_over_on_429_then_uses_fallback():
    # A and B both 429 -> should land on fallback K
    client = FakeClient(script={"A": "429", "B": "429"})
    r = _router(client)
    out = r.complete(tier="tools", messages=[{"role": "user", "content": "hi"}])
    assert out.model == "K"
    assert client.calls == ["A", "B", "K"]


def test_router_reasoning_tier_uses_its_own_pool():
    client = FakeClient()
    r = _router(client)
    out = r.complete(tier="reasoning", messages=[{"role": "user", "content": "hi"}])
    assert out.model == "R1"


def test_router_skips_model_without_headroom():
    client = FakeClient()
    r = _router(client)
    # drain model A's request bucket so it has no headroom -> route to B
    for _ in range(20):
        r.limiter("A").requests.try_consume(1)
    out = r.complete(tier="tools", messages=[{"role": "user", "content": "hi"}])
    assert out.model == "B"
    assert client.calls == ["B"]


def test_router_waits_when_all_exhausted_rather_than_dropping():
    client = FakeClient()
    clock = [0.0]
    slept: list[float] = []

    def fake_sleep(s: float) -> None:
        slept.append(s)
        clock[0] += s  # advance the shared clock so buckets refill

    r = _router(client, sleep=fake_sleep, time_func=lambda: clock[0])
    # exhaust request buckets on every candidate in the tools tier + fallback
    for name in ("A", "B", "K"):
        for _ in range(20):
            r.limiter(name).requests.try_consume(1)
    out = r.complete(tier="tools", messages=[{"role": "user", "content": "hi"}])
    # it must have waited (not raised) and then succeeded on some model
    assert slept, "expected the router to wait for a bucket to refill"
    assert out.model in {"A", "B", "K"}


def test_router_raises_when_request_can_never_fit():
    client = FakeClient()
    # zero refill + drained buckets => infinite wait => explicit error, no hang
    r = LLMRouter(
        client,
        tiers={"tools": ["A"]},
        fallback=None,
        default_limit=RateLimit(requests_per_minute=1, tokens_per_minute=1),
        sleep=lambda s: None,
    )
    r.limiter("A").requests.refill_per_sec = 0.0
    r.limiter("A").tokens.refill_per_sec = 0.0
    r.limiter("A").requests.try_consume(1)
    with pytest.raises(RateLimitExceeded):
        r.complete(tier="tools", messages=[{"role": "user", "content": "hi"}])


def test_router_non_rate_limit_error_propagates():
    class Boom(Exception):
        pass

    client = FakeClient()

    def _boom(*, model: str, **kwargs: Any):
        raise Boom("kaboom")

    client.chat.completions.create = _boom
    r = _router(client)
    with pytest.raises(Boom):
        r.complete(tier="tools", messages=[{"role": "user", "content": "hi"}])
