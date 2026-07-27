"""Thread-safe token-bucket rate limiting, one bucket pair per model.

The chat clients call the provider from worker threads (chat_web uses
``asyncio.to_thread``; chat_client is synchronous), so every bucket guards its
state with a plain ``threading.Lock``. No asyncio primitives are used, which
keeps the limiter usable from both sync and async call sites.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


class TokenBucket:
    """Classic token bucket.

    Starts full at ``capacity`` and refills continuously at
    ``refill_per_sec``. ``try_consume`` is non-blocking; ``time_until`` reports
    how long until an amount is available so callers can decide whether to wait
    or fail over. ``time_func`` is injectable so tests can drive a fake clock.
    """

    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._time = time_func
        self._tokens = float(capacity)
        self._last = time_func()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = self._time()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(
                self.capacity, self._tokens + elapsed * self.refill_per_sec
            )
            self._last = now

    def try_consume(self, amount: float) -> bool:
        """Reserve ``amount`` tokens if available. Returns False without
        consuming when there isn't enough headroom."""
        with self._lock:
            self._refill_locked()
            if self._tokens >= amount:
                self._tokens -= amount
                return True
            return False

    def adjust(self, amount: float) -> None:
        """Unconditionally subtract ``amount`` (may drive the balance
        negative). Used to reconcile an estimate against real usage, and to
        drain the bucket as a back-off penalty. A negative ``amount`` refunds.
        """
        with self._lock:
            self._refill_locked()
            self._tokens = min(self.capacity, self._tokens - amount)

    def drain(self) -> None:
        """Empty the bucket (back-off penalty after an observed 429)."""
        with self._lock:
            self._refill_locked()
            self._tokens = 0.0

    def time_until(self, amount: float) -> float:
        """Seconds until ``amount`` tokens are available (0.0 if now).

        ``amount`` is clamped to ``capacity``: a single request larger than the
        whole bucket can never be fully covered, so we wait at most for a full
        refill and let the provider (and the post-call reconciliation) sort out
        the overshoot rather than blocking forever.
        """
        amount = min(amount, self.capacity)
        with self._lock:
            self._refill_locked()
            if self._tokens >= amount:
                return 0.0
            if self.refill_per_sec <= 0:
                return float("inf")
            return (amount - self._tokens) / self.refill_per_sec

    @property
    def available(self) -> float:
        with self._lock:
            self._refill_locked()
            return self._tokens


@dataclass(frozen=True)
class RateLimit:
    """Per-model limit. Both dimensions are enforced independently."""

    requests_per_minute: float
    tokens_per_minute: float


class ModelLimiter:
    """Couples a request bucket and a token bucket for one model.

    ``try_acquire`` is all-or-nothing across both dimensions so we never burn a
    request slot while the token budget is empty (or vice-versa).
    """

    def __init__(
        self,
        name: str,
        limit: RateLimit,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.limit = limit
        self.requests = TokenBucket(
            limit.requests_per_minute, limit.requests_per_minute / 60.0, time_func
        )
        self.tokens = TokenBucket(
            limit.tokens_per_minute, limit.tokens_per_minute / 60.0, time_func
        )

    def _cap(self, est_tokens: float) -> float:
        return max(0.0, min(float(est_tokens), self.tokens.capacity))

    def try_acquire(self, est_tokens: float) -> bool:
        """Reserve one request plus ``est_tokens``. Refunds the request slot if
        the token reservation fails, so partial holds never happen."""
        est = self._cap(est_tokens)
        if not self.requests.try_consume(1):
            return False
        if not self.tokens.try_consume(est):
            self.requests.adjust(-1)  # refund the request slot
            return False
        return True

    def time_until(self, est_tokens: float) -> float:
        est = self._cap(est_tokens)
        return max(self.requests.time_until(1), self.tokens.time_until(est))

    def reconcile(self, est_tokens: float, actual_tokens: float) -> None:
        """Correct the token bucket for the gap between the pre-call estimate
        and the provider's reported usage. Positive delta consumes more,
        negative refunds."""
        delta = self._cap(actual_tokens) - self._cap(est_tokens)
        if delta:
            self.tokens.adjust(delta)

    def penalize(self) -> None:
        """Drain both buckets after an observed 429 so we stop routing to this
        model until its window refills."""
        self.requests.drain()
        self.tokens.drain()
