"""LLM routing + client-side rate limiting for the chat clients.

Two task tiers are routed to different model pools:

* ``tools``     — the agentic tool-calling loop (fast/cheap models).
* ``reasoning`` — the final synthesis / answer pass (stronger models).

Each model has its own token bucket (requests-per-minute + tokens-per-minute)
so we throttle *before* the provider returns HTTP 429. When a model has no
headroom (or a 429 slips through anyway) the router fails over immediately to
the next model in the tier, then to the global fallback. A request is never
silently dropped: if every candidate is exhausted the router waits for the
soonest bucket to refill and retries.
"""

from .rate_limit import ModelLimiter, RateLimit, TokenBucket
from .router import LLMResponse, LLMRouter, RateLimitExceeded, estimate_tokens

__all__ = [
    "TokenBucket",
    "RateLimit",
    "ModelLimiter",
    "LLMRouter",
    "LLMResponse",
    "RateLimitExceeded",
    "estimate_tokens",
]
