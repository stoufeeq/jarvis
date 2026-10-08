"""
Shared Yahoo request budget.

Why this exists
---------------
Jarvis ran at roughly 11,000 Yahoo requests/hour against a limit the
community puts near 360/hour, and the result was an IP-level throttle
whose 429s are indistinguishable from delisted symbols — 75 holdings,
AAPL and NVDA among them, were reported as "likely delisted" when the
real cause was request volume.

Two things were wrong. The volume itself (fixed by batching — see
yahoo_quotes.py) and the absence of any ceiling: nothing in the codebase
knew how many requests had already been spent, so every new feature
silently added to a total no one was tracking. This module is that
ceiling.

Why Redis rather than a process-local counter
---------------------------------------------
The budget belongs to the IP, and the IP is shared by the api, worker
and beat containers. A per-process limiter would permit three times the
intended rate while reporting that it was holding the line — the exact
failure mode that makes a limiter worse than none, because it creates
false confidence.

The algorithm
-------------
A sliding-window counter: one Redis sorted set keyed to the window,
holding a timestamp per spent request. Chosen over a token bucket
because the question being answered is literally "how many requests in
the last hour", which is what the published figure constrains. A bucket
would permit a full-budget burst in one second, which is the pattern
most likely to trip a server-side limiter even when the hourly average
is fine.

Acquire is atomic via a Lua script — a read-then-write would let two
containers both see 299 and both proceed.

Failure policy
--------------
If Redis is unreachable the limiter allows the call. Degrading to
"unlimited" is the lesser evil: blocking would take the whole app down
over a cache outage, and the batching work means normal operation is
already far inside budget. The event is logged at WARNING so it is not
silent.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis

from app.config import get_settings

log = logging.getLogger(__name__)

WINDOW_SECONDS = 3600
KEY = "jarvis:yf:window"

# Atomic acquire. Trims entries older than the window, counts what's
# left, and only then records the new request — so the count a caller
# acts on cannot have changed underneath it.
_ACQUIRE_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local budget = tonumber(ARGV[3])
local cost   = tonumber(ARGV[4])
local member = ARGV[5]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local used = redis.call('ZCARD', key)

if used + cost > budget then
  -- Report when the oldest entry leaves the window, so the caller can
  -- sleep exactly long enough instead of polling.
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest and oldest[2] then
    retry = (tonumber(oldest[2]) + window) - now
  end
  return {0, used, retry}
end

for i = 1, cost do
  redis.call('ZADD', key, now, member .. ':' .. i)
end
redis.call('EXPIRE', key, window + 60)
return {1, used + cost, 0}
"""


class YahooRateLimiter:
    """Budget gate for Yahoo requests. Use `acquire` before issuing them."""

    def __init__(self, redis_url: str | None = None, budget: int | None = None):
        s = get_settings()
        self._url = redis_url or s.redis_url
        self._budget = budget if budget is not None else s.yf_requests_per_hour
        self._enabled = s.yf_rate_limit_enabled
        self._client: aioredis.Redis | None = None
        self._script = None

    async def _conn(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self._url, decode_responses=True)
            self._script = self._client.register_script(_ACQUIRE_LUA)
        return self._client

    async def try_acquire(self, cost: int = 1) -> tuple[bool, int, float]:
        """One atomic attempt. Returns (granted, used_after, retry_after)."""
        if not self._enabled:
            return True, 0, 0.0
        try:
            await self._conn()
            granted, used, retry = await self._script(
                keys=[KEY],
                args=[time.time(), WINDOW_SECONDS, self._budget, cost, uuid.uuid4().hex],
            )
            return bool(int(granted)), int(used), float(retry)
        except Exception as exc:
            # See "Failure policy" in the module docstring: allow, loudly.
            log.warning(
                "Yahoo rate limiter unavailable (%s) — allowing %d request(s) "
                "unthrottled", exc, cost,
            )
            return True, 0, 0.0

    async def acquire(self, cost: int = 1, timeout: float | None = None) -> bool:
        """Wait for budget. Returns False if it didn't arrive within timeout.

        A caller that gets False should skip its work, not proceed anyway:
        proceeding is what deepens a throttle instead of riding it out.
        """
        if not self._enabled:
            return True
        deadline = time.monotonic() + (
            timeout if timeout is not None else get_settings().yf_rate_limit_wait_seconds
        )
        attempts = 0
        while True:
            granted, used, retry = await self.try_acquire(cost)
            if granted:
                if attempts:
                    log.info(
                        "Yahoo budget: granted %d request(s) after waiting "
                        "(%d/%d used this hour)", cost, used, self._budget,
                    )
                return True
            attempts += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning(
                    "Yahoo budget exhausted: %d/%d used this hour, need %d, "
                    "next slot in %.0fs — giving up after waiting %.0fs",
                    used, self._budget, cost, retry,
                    (timeout if timeout is not None else 0),
                )
                return False
            # Sleep until a slot frees, capped so a long retry doesn't
            # overshoot the deadline and so we re-check periodically.
            await asyncio.sleep(max(0.5, min(retry, remaining, 30.0)))

    async def usage(self) -> dict:
        """Current window usage — for the diagnostic script and logs."""
        if not self._enabled:
            return {"enabled": False, "used": 0, "budget": self._budget}
        try:
            client = await self._conn()
            now = time.time()
            await client.zremrangebyscore(KEY, "-inf", now - WINDOW_SECONDS)
            used = await client.zcard(KEY)
            return {
                "enabled": True,
                "used": int(used),
                "budget": self._budget,
                "remaining": max(0, self._budget - int(used)),
            }
        except Exception as exc:
            return {"enabled": True, "error": str(exc), "budget": self._budget}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Process-wide instance. Cheap to share: the Redis client is lazy and
# connection-pooled, and the budget lives in Redis, not here.
_limiter: YahooRateLimiter | None = None


def get_limiter() -> YahooRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = YahooRateLimiter()
    return _limiter


async def spend(cost: int = 1, timeout: float | None = None) -> bool:
    """Convenience wrapper: reserve `cost` requests from the hourly budget."""
    return await get_limiter().acquire(cost=cost, timeout=timeout)
