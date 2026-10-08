"""
Heatmap service — batch-fetches S&P 500 quotes and builds a sector tree
suitable for rendering as a Recharts Treemap (heatmap) or ScatterChart
(bubbles) on the frontend.

One data source: Yahoo's batched /v7/finance/quote endpoint, via
app/services/yahoo_quotes.py. Five requests cover all ~452 constituents
and carry price, previous close, change %, volume and 10-day average
volume.

It used to be two, and they were ruinously expensive. yf.download for
the volume series cost ~453 requests (download() threads per ticker; it
does not batch, despite the name) and fast_info for change_pct cost
another ~904 (two requests each). About 1,357 requests every ten
minutes — 8,100/hour from one IP, against a limit the community puts
near 360/hour. That is what got the server throttled, and the 429s then
surfaced as 75 holdings "likely delisted", AAPL and NVDA among them.

The batched endpoint also removed the reason the two sources existed.
fast_info was there because the historical-bar feed occasionally drops a
valid trading day (2026-06-15 made WDC show a Friday-to-Tuesday move),
so previousClose came from one path and the comparison from the other.
/v7/finance/quote returns regularMarketPrice and
regularMarketPreviousClose from the same snapshot, which is the
agreement the reconciliation was approximating.

A cold heatmap now takes ~2s rather than ~10-15s; warm hits return from
Redis instantly.

Cache lives in Redis (not a per-process dict) so the celery-worker's
30-min pre-warm task and the API backend share the same data. Without
this, the backend's own cache stays cold and every dashboard hit paid
the 10-15s fetch cost until the backend itself happened to warm it.
"""

import asyncio
import json
import logging
import math
import time
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings
from app.data.sp500 import SP500
from app.services import yahoo_quotes

log = logging.getLogger(__name__)

CACHE_KEY = "heatmap:sp500"
# Last successful payload, kept far longer than the serving cache. When a
# fetch fails — a throttle, an outage, an exhausted request budget — a
# day-old heatmap labelled stale is strictly better than an empty one:
# empty renders as a uniformly flat market, which is a lie, and it would
# also be cached for ten minutes and hide the recovery.
LAST_GOOD_KEY = "heatmap:sp500:last_good"
LAST_GOOD_TTL = 86_400
# 10 min TTL matches the Celery pre-warm interval + frontend refetchInterval.
# Previously 30 min but 30 min stale × 30 min client staleTime meant worst-case
# 60-min-old data on the dashboard, which felt broken during active trading
# hours. 10 min is still 4x the ~2.5-min fetch time so pre-warm never overlaps.
CACHE_TTL = 600  # 10 minutes

# Lazy-inited shared Redis client per process. aioredis clients have an
# internal connection pool so reusing one across calls is the right
# pattern — creating a fresh one each call would burn TCP handshakes.
_redis_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            get_settings().redis_url, decode_responses=True,
        )
    return _redis_client


async def _cache_get(key: str = CACHE_KEY) -> dict | None:
    """Return a cached heatmap payload, or None on miss / any Redis
    failure. Redis-down is treated as a cache miss — same effect as an
    empty cache today, so nothing regresses if Redis goes offline."""
    try:
        raw = await _redis().get(key)
        if raw:
            return json.loads(raw)
        return None
    except Exception:
        log.warning("Redis GET failed on heatmap cache — treating as miss", exc_info=True)
        return None


async def _cache_set(data: dict) -> None:
    try:
        blob = json.dumps(data)
        await _redis().set(CACHE_KEY, blob, ex=CACHE_TTL)
        # Only a payload with real prices becomes the stale fallback —
        # otherwise a failed fetch would overwrite the good copy it
        # exists to preserve.
        if not data.get("error"):
            await _redis().set(LAST_GOOD_KEY, blob, ex=LAST_GOOD_TTL)
    except Exception:
        log.warning("Redis SET failed on heatmap cache — next call will refetch", exc_info=True)


class HeatmapService:
    async def get_sp500_heatmap(self, force_refresh: bool = False) -> dict:
        if not force_refresh:
            cached = await _cache_get()
            if cached:
                return cached

        data = await _fetch_heatmap()

        # A fetch that came back empty is a throttle or an outage, not a
        # flat market. Caching it would pin an empty heatmap for ten
        # minutes and hide the recovery, so serve stale instead.
        if not data.pop("_any_data", False):
            stale = await _cache_get(LAST_GOOD_KEY)
            if stale:
                log.warning("Heatmap fetch failed; serving stale cache")
                stale["stale"] = True
                stale["error"] = data.get("error") or "fetch returned no quotes"
                return stale
        await _cache_set(data)
        return data


def _reconcile_change(fast_info: float | None, download: float | None) -> float | None:
    """Merge two independently-sourced change_pct readings, preferring the
    more extreme when they disagree by more than a point.

    No longer used by the heatmap: /v7/finance/quote returns price and
    previousClose from one snapshot, so there are no longer two feeds to
    disagree. Kept because the rule it encodes is still correct and still
    tested — a lagging snapshot understates a move, so the larger
    absolute reading is the one that saw the price later. Delete it if a
    second source never reappears."""
    if fast_info is None and download is None:
        return None
    if fast_info is None:
        return download
    if download is None:
        return fast_info
    if abs(fast_info - download) <= 1.0:
        return download
    return download if abs(download) > abs(fast_info) else fast_info


async def _fetch_heatmap() -> dict:
    """Fetch every constituent in five batched requests.

    change_pct comes straight from the quote: price and previousClose
    arrive in one snapshot, so there is nothing to reconcile between two
    feeds (_reconcile_change is kept for the tests that pin its
    behaviour, and for any caller still merging two sources).

    rel_volume is today's volume against the 10-day average. The old code
    used a 20-day average built from a 1mo history fetch; 10-day comes
    free in the quote and the figure is read as a rough "busier than
    usual", not a precise ratio. Both share the same quirk: during the
    session today's volume is partial, so early in the day everything
    looks quiet.
    """
    tickers = [s["ticker"] for s in SP500]

    change_map: dict[str, float | None] = {}
    vol_map: dict[str, float | None] = {}
    error: str | None = None

    try:
        # Background job: wait a good while for budget rather than
        # giving up. A missed warm leaves the dashboard on stale data.
        quotes = await yahoo_quotes.get_many(tickers, budget_timeout=120.0)
    except Exception as exc:
        log.warning("Heatmap quote fetch failed: %s", exc)
        quotes, error = {}, str(exc)

    if not quotes and not error:
        error = "no quotes returned (request budget exhausted or provider throttling)"

    for ticker in tickers:
        q = quotes.get(ticker.upper())
        if not q:
            change_map[ticker] = None
            vol_map[ticker] = None
            continue

        change_map[ticker] = (
            round(q["change_pct"], 2) if q.get("change_pct") is not None else None
        )

        vol = q.get("volume")
        avg = q.get("avg_volume_10d") or q.get("avg_volume_3m")
        if vol and avg and math.isfinite(vol) and math.isfinite(avg) and avg > 0:
            vol_map[ticker] = round(vol / avg, 2)
        else:
            vol_map[ticker] = None

    resolved = sum(1 for v in change_map.values() if v is not None)
    log.info(
        "Heatmap fetched: %d/%d constituents priced in %d request(s)",
        resolved, len(tickers), yahoo_quotes.request_cost(tickers),
    )

    payload: dict[str, Any] = {
        "sectors": _build_sectors(change_map, vol_map),
        "cached_at": time.time(),
        # Internal flag so the caller can tell "partial" from "nothing".
        "_any_data": resolved > 0,
    }
    if error:
        payload["error"] = error
    return payload


def _build_sectors(
    change_map: dict[str, float | None],
    vol_map: dict[str, float | None] | None = None,
) -> list[dict]:
    vol_map = vol_map or {}
    sectors_dict: dict[str, list[dict]] = {}

    for stock in SP500:
        sector = stock["sector"]
        if sector not in sectors_dict:
            sectors_dict[sector] = []
        sectors_dict[sector].append(
            {
                "ticker":     stock["ticker"],
                "name":       stock["name"],
                "weight":     stock["weight"],
                "change_pct": change_map.get(stock["ticker"]),
                "rel_volume": vol_map.get(stock["ticker"]),
            }
        )

    sector_order = [
        "Information Technology",
        "Health Care",
        "Financials",
        "Consumer Discretionary",
        "Communication Services",
        "Industrials",
        "Consumer Staples",
        "Energy",
        "Utilities",
        "Real Estate",
        "Materials",
    ]
    return [
        {"name": s, "children": sectors_dict[s]}
        for s in sector_order
        if s in sectors_dict
    ]
