"""
Celery task: pre-warm the S&P 500 heatmap cache.

Runs every 30 minutes during US market hours (and once at open / close)
so the in-process cache is always fresh. Users never wait for the ~450
yfinance quote fetch on the first dashboard or heatmap visit.
"""

import asyncio
import logging

from app.services.heatmap import HeatmapService
from app.services.market_session import MarketSession
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.heatmap_warm.warm_heatmap_cache", bind=True)
def warm_heatmap_cache(self):
    """Force-refresh the heatmap cache. Skips work outside trading days
    to avoid wasting yfinance calls on weekends/holidays — the existing
    cache from Friday close (or the last trading day) stays valid."""
    session = MarketSession()

    # Always warm on trading days, even outside hours, so pre-market and
    # after-hours visits are still fast. Skip on weekends and holidays —
    # the data won't change anyway.
    if not session.is_trading_day:
        log.info("Skipping heatmap warm — not a trading day (%s)", session.state)
        return {"skipped": True, "reason": session.state}

    asyncio.run(_warm())
    return {"skipped": False}


async def _warm():
    log.info("Warming S&P 500 heatmap cache...")
    data = await HeatmapService().get_sp500_heatmap(force_refresh=True)
    sector_count = len(data.get("sectors", []))
    log.info("Heatmap cache warmed: %d sectors", sector_count)
