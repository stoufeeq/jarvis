"""
Celery task: refresh upcoming earnings + ex-dividend dates from yfinance
into the calendar_events table.

Runs once daily at 3:00 AM UTC. Refreshes for every distinct ticker that
appears in any user's watchlist or portfolio.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.portfolio import Portfolio, Position
from app.models.watchlist import Watchlist, WatchlistItem
from app.services.calendar import CalendarService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.calendar_refresh.refresh_calendar_events", bind=True)
def refresh_calendar_events(self):
    asyncio.run(_run())


async def _run():
    async with AsyncSessionLocal() as db:
        tickers = await _all_user_tickers(db)
        if not tickers:
            log.info("Calendar refresh: no tickers across users — skipping")
            return
        log.info("Calendar refresh: %d unique tickers", len(tickers))
        counts = await CalendarService(db).refresh_for_tickers(tickers)
        await db.commit()
        log.info("Calendar refresh complete: %s", counts)


async def _all_user_tickers(db: AsyncSession) -> list[str]:
    """Distinct tickers across all active portfolios + all watchlists."""
    result = await db.execute(
        select(Position.ticker)
        .join(Portfolio, Position.portfolio_id == Portfolio.id)
        .where(Portfolio.is_active.is_(True))
        .distinct()
    )
    port_tickers = {row[0] for row in result.fetchall()}

    result = await db.execute(
        select(WatchlistItem.ticker).join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id).distinct()
    )
    watch_tickers = {row[0] for row in result.fetchall()}

    return sorted(port_tickers | watch_tickers)


@celery_app.task(name="app.workers.tasks.calendar_refresh.refresh_calendar_for_tickers", bind=True)
def refresh_calendar_for_tickers(self, tickers: list[str]):
    """One-shot refresh for a specific ticker list — used for backfill /
    manual trigger from the API."""
    asyncio.run(_run_for(tickers))


async def _run_for(tickers: list[str]):
    async with AsyncSessionLocal() as db:
        counts = await CalendarService(db).refresh_for_tickers(tickers)
        await db.commit()
        log.info("Calendar refresh for %d tickers: %s", len(tickers), counts)
