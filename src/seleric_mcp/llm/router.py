"""Task-tier model router with client-side rate limiting and failover.

Usage::

    router = LLMRouter(client, tiers={...}, fallback="Kimi-K2.6", default_limit=...)
    resp = router.complete(tier="tools", messages=msgs, tools=tools)
    message = resp.message          # provider choices[0].message
    used    = resp.model            # which model actually answered

Routing per :meth:`complete` call:

1. Build the candidate list = ``tiers[tier]`` then the global ``fallback``.
2. Estimate the request's token cost and, in order, take the first model whose
   per-model buckets have headroom (:meth:`ModelLimiter.try_acquire`).
3. Call it. On a 429 (or other rate-limit error) drain that model's buckets and
   fail over immediately to the next candidate — no waiting.
4. Only if *every* candidate is exhausted do we wait for the soonest bucket to
   refill, then retry. That safety net keeps a request from ever being dropped.
5. After each successful call, reconcile the token estimate against the
   provider's reported ``usage`` so the bucket tracks reality.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .rate_limit import ModelLimiter, RateLimit

_log = logging.getLogger(__name__)

# ~4 chars per token is the usual English rule of thumb; good enough as a
# pre-call estimate because we reconcile against real usage afterward.
_CHARS_PER_TOKEN = 4.0
# Headroom reserved for the completion we haven't generated yet.
DEFAULT_COMPLETION_RESERVE = 1024
# Default wait-loop bounds. These can be overridden via config.yaml llm.max_wait_rounds
# and llm.max_sleep_seconds. Set max_wait_rounds to 0 for unlimited waiting.
DEFAULT_MAX_WAIT_ROUNDS = 120  # ~2 hours with 60s sleeps (was 8)
DEFAULT_MAX_SLEEP_SECONDS = 60.0


class RateLimitExceeded(RuntimeError):
    """Raised only when no candidate model can ever satisfy the request (e.g.
    all buckets misconfigured with zero refill), or the wait budget is spent."""


@dataclass
class LLMResponse:
    model: str
    message: Any  # provider response.choices[0].message
    usage_tokens: int
    attempts: list[str] = field(default_factory=list)


# --- token estimation -------------------------------------------------------

try:  # optional: better estimates when available, never required
    import tiktoken  # type: ignore

    _ENC = tiktoken.get_encoding("o200k_base")

    def _count_text(text: str) -> int:
        return len(_ENC.encode(text))

except Exception:  # pragma: no cover - fallback path
    _ENC = None

    def _count_text(text: str) -> int:
        return int(len(text) / _CHARS_PER_TOKEN) + 1


def estimate_tokens(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None = None,
    completion_reserve: int = DEFAULT_COMPLETION_RESERVE,
) -> int:
    """Best-effort prompt-token estimate + a reserve for the completion.

    Overestimating slightly is the safe direction — it makes us throttle a hair
    early rather than trip a 429 — and the post-call reconciliation corrects any
    drift against the provider's real usage numbers.
    """
    total = 0
    for m in messages:
        total += 4  # per-message framing overhead
        content = m.get("content")
        if isinstance(content, str):
            total += _count_text(content)
        elif content is not None:
            total += _count_text(json.dumps(content, default=str))
        for tc in m.get("tool_calls") or []:
            total += _count_text(json.dumps(tc, default=str))
    if tools:
        total += _count_text(json.dumps(list(tools), default=str))
    return total + max(0, completion_reserve)


# --- rate-limit error detection --------------------------------------------

try:  # narrow, explicit match when the openai SDK is present
    from openai import RateLimitError as _OpenAIRateLimitError  # type: ignore
except Exception:  # pragma: no cover
    _OpenAIRateLimitError = ()  # type: ignore


def _default_is_rate_limit(exc: BaseException) -> bool:
    if _OpenAIRateLimitError and isinstance(exc, _OpenAIRateLimitError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _extract_retry_after(exc: BaseException) -> float | None:
    """Best-effort read of a provider ``Retry-After`` header off a rate-limit
    error. Returns None when absent/unparseable so callers fall back to
    bucket-refill math."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        headers = getattr(exc, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usage_total(resp: Any) -> int:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return 0
    return int(getattr(usage, "total_tokens", 0) or 0)


class LLMRouter:
    def __init__(
        self,
        client: Any,
        *,
        tiers: Mapping[str, Iterable[str]],
        fallback: str | None = None,
        default_limit: RateLimit,
        model_limits: Mapping[str, RateLimit] | None = None,
        completion_reserve: int = DEFAULT_COMPLETION_RESERVE,
        max_wait_rounds: int = DEFAULT_MAX_WAIT_ROUNDS,
        max_sleep_seconds: float = DEFAULT_MAX_SLEEP_SECONDS,
        token_estimator: Callable[..., int] | None = None,
        is_rate_limit_error: Callable[[BaseException], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._tiers = {k: list(v) for k, v in tiers.items()}
        self._fallback = fallback
        self._completion_reserve = completion_reserve
        self._max_wait_rounds = max_wait_rounds  # 0 = unlimited
        self._max_sleep_seconds = max_sleep_seconds
        self._estimate = token_estimator or estimate_tokens
        self._is_rate_limit = is_rate_limit_error or _default_is_rate_limit
        self._sleep = sleep
        model_limits = model_limits or {}
        self._limiters: dict[str, ModelLimiter] = {}
        for name in self._all_model_names():
            self._limiters[name] = ModelLimiter(
                name, model_limits.get(name, default_limit), time_func
            )

    def _all_model_names(self) -> list[str]:
        names: list[str] = []
        for models in self._tiers.values():
            names.extend(models)
        if self._fallback:
            names.append(self._fallback)
        # de-dup, preserve order
        seen: set[str] = set()
        return [n for n in names if not (n in seen or seen.add(n))]

    def candidates(self, tier: str) -> list[str]:
        models = list(self._tiers.get(tier) or [])
        if not models and self._fallback:
            models = [self._fallback]
        if self._fallback and self._fallback not in models:
            models.append(self._fallback)
        if not models:
            raise RateLimitExceeded(f"no models configured for tier {tier!r}")
        return models

    def limiter(self, model: str) -> ModelLimiter:
        return self._limiters[model]

    def complete(
        self,
        *,
        tier: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> LLMResponse:
        cands = self.candidates(tier)
        est = self._estimate(messages, tools, self._completion_reserve)
        attempts: list[str] = []
        exhausted: set[str] = set()
        wait_rounds = 0

        while True:
            # Pass 1: take the first candidate with live headroom.
            for name in cands:
                if name in exhausted:
                    continue
                if not self._limiters[name].try_acquire(est):
                    continue
                attempts.append(name)
                try:
                    resp = self._invoke(name, messages, tools, tool_choice, **kwargs)
                except Exception as exc:  # noqa: BLE001 - reraise non-rate-limit below
                    if self._is_rate_limit(exc):
                        self._limiters[name].penalize(_extract_retry_after(exc))
                        exhausted.add(name)
                        continue
                    raise
                self._limiters[name].reconcile(est, _usage_total(resp))
                return LLMResponse(
                    model=name,
                    message=resp.choices[0].message,
                    usage_tokens=_usage_total(resp),
                    attempts=attempts,
                )

            # Everyone is throttled right now. If everyone also hard-429'd this
            # turn, clear the penalty set and wait it out rather than drop.
            live = [n for n in cands if n not in exhausted]
            if not live:
                live = cands
                exhausted = set()

            wait_rounds += 1
            # max_wait_rounds=0 means unlimited waiting
            if self._max_wait_rounds > 0 and wait_rounds > self._max_wait_rounds:
                raise RateLimitExceeded(
                    f"rate-limit wait budget exhausted for tier {tier!r} "
                    f"after trying {attempts} ({wait_rounds} rounds)"
                )
            wait = min(self._limiters[n].time_until(est) for n in live)
            if wait == float("inf"):
                raise RateLimitExceeded(
                    f"no model in tier {tier!r} can satisfy a {est}-token request"
                )
            actual_wait = min(wait, self._max_sleep_seconds)
            # Log that we're waiting (helps users understand the agent isn't stuck)
            _log.info(
                f"Rate limit: waiting {actual_wait:.1f}s for tier '{tier}' "
                f"(round {wait_rounds}, tried: {attempts})"
            )
            self._sleep(actual_wait)

    def _invoke(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None,
        tool_choice: str,
        **kwargs: Any,
    ) -> Any:
        params: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        params.update(kwargs)
        return self._client.chat.completions.create(**params)
