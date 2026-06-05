"""Utilities for cross-run tool result caching and simple rate limiting.

This file implements a small in-memory LRU-TTL cache and a simple token-bucket rate
limiter per key. The cache is used by tools like `vector_search` to reduce repeated
calls to external services and avoid duplicated cost.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

DEFAULT_TTL = int(os.getenv("TOOL_CACHE_TTL_SECONDS", "60"))
DEFAULT_MAX_ITEMS = int(os.getenv("TOOL_CACHE_MAX_ITEMS", "1024"))


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    """A simple fixed-size LRU cache with TTLs.

    This is not a full-featured cache but is good enough for small deployments to
    de-duplicate identical tool calls for a window of time.
    """

    def __init__(self, max_items: int = DEFAULT_MAX_ITEMS, ttl_seconds: int = DEFAULT_TTL):
        self._max_items = max_items
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = Lock()

    def _purge_expired(self) -> None:
        now = time.time()
        keys_to_delete = [k for k, v in self._cache.items() if v.expires_at <= now]
        for k in keys_to_delete:
            self._cache.pop(k, None)

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry.expires_at <= time.time():
                # expired
                self._cache.pop(key, None)
                return None
            # Move to end (recently used)
            self._cache.move_to_end(key)
            return entry.value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._purge_expired()
            if key in self._cache:
                self._cache.pop(key)
            while len(self._cache) >= self._max_items:
                # pop oldest
                self._cache.popitem(last=False)
            self._cache[key] = CacheEntry(value=value, expires_at=time.time() + self._ttl)


class RateLimiter:
    """Simple per-key token bucket rate limiter.

    Usage: create per-key RateLimiter with capacity and refill rate (tokens / sec). Call
    `allow()` to check permission; `allow()` will refill tokens based on elapsed time.
    """

    def __init__(self, capacity: int = 10, refill_rate: float = 1.0):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = capacity
        self._last_time = time.time()
        self._lock = Lock()

    def allow(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.time()
            elapsed = now - self._last_time
            refill = elapsed * self.refill_rate
            if refill > 0:
                self._tokens = min(self.capacity, self._tokens + refill)
                self._last_time = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


# Module-level caches and map of rate limiters keyed by collection and tool
default_cache = TTLCache()
_rate_limiters: dict[str, RateLimiter] = {}
_rate_limiters_lock = Lock()


def get_rate_limiter(
    key: str, capacity: int | None = None, refill_rate: float | None = None
) -> RateLimiter:
    with _rate_limiters_lock:
        if key in _rate_limiters:
            return _rate_limiters[key]
        cap = int(os.getenv("TOOL_RATE_LIMIT_BURST", "16")) if capacity is None else capacity
        refill = (
            float(os.getenv("TOOL_RATE_LIMIT_PER_SEC", "8")) if refill_rate is None else refill_rate
        )
        limiter = RateLimiter(capacity=cap, refill_rate=refill)
        _rate_limiters[key] = limiter
        return limiter


__all__ = ["RateLimiter", "TTLCache", "default_cache", "get_rate_limiter"]
