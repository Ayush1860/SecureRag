"""Per-principal rate limiting (moving window, in-process).

Uses the ``limits`` library (the engine behind slowapi) directly as a FastAPI dependency, keyed
by the authenticated principal instead of the client IP. The in-memory storage is per worker;
for multi-worker deployments point ``RATE_LIMIT_STORAGE`` at Redis (``redis://host:6379``).
"""
from __future__ import annotations

from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter


class RateLimiter:
    def __init__(self, rule: str = "60/minute", storage_uri: str = "memory://"):
        self.rule = parse(rule)
        self._limiter = MovingWindowRateLimiter(storage_from_string(storage_uri))

    def hit(self, key: str) -> bool:
        """Record one request for ``key``; False when the limit is exceeded."""
        return self._limiter.hit(self.rule, "securerag", key)
