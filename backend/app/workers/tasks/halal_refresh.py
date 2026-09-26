"""
Celery task: re-screen Sharia compliance for every referenced ticker.

Monthly, because the inputs are balance-sheet figures — total debt, cash
and short-term investments — that only move when a company files. The
service cache TTL (35 days) is set longer than this interval on purpose:
this task is what renews a verdict, so a page load never waits on a
yfinance fetch for a symbol that has been screened before.

Market cap is the one input that moves daily, and it sits in the
denominator of both AAOIFI ratios. A company parked very near the 33%
line can therefore hold a month-old verdict that spot figures would
flip. That is an accepted trade — a screen is a starting point for a
decision, not a live signal — and `force=True` on the service (or
re-running this task) gets a fresh answer on demand.

Ticker set is the same one TickerHealthService tracks: open positions
plus watchlist entries, which is exactly where the badges render.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.halal_screener import HalalScreenerService
from app.services.ticker_health import TickerHealthService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.halal_refresh.refresh_halal_compliance", bind=True)
def refresh_halal_compliance(self):
    return asyncio.run(_refresh())


async def _refresh() -> dict:
    async with AsyncSessionLocal() as db:
        tickers = await TickerHealthService(db).tracked_tickers()
        if not tickers:
            log.info("Halal refresh: no tracked tickers")
            return {"screened": 0}

        tally = await HalalScreenerService(db).refresh_all(tickers)
        log.info(
            "Halal refresh: screened %d — compliant %d, non-compliant %d, unknown %d",
            tally.get("screened", 0),
            tally.get("compliant", 0),
            tally.get("non_compliant", 0),
            tally.get("unknown", 0),
        )
        return tally
