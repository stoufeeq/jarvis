"""
Celery task: fetch SEC Form 4 insider trades for all watchlist tickers.
"""

import asyncio
import logging

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.watchlist import WatchlistItem
from app.services.insider_fetcher import InsiderTradeFetcher
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.insider_fetch.fetch_all_insider_trades", bind=True)
def fetch_all_insider_trades(self):
    asyncio.run(_fetch_all())


async def _fetch_all():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WatchlistItem.ticker).distinct())
        tickers = [row[0] for row in result.all()]

    if not tickers:
        return

    fetcher = InsiderTradeFetcher()
    try:
        for ticker in tickers:
            try:
                async with AsyncSessionLocal() as db:
                    count = await fetcher.fetch_for_ticker(ticker, db, days=90)
                    await db.commit()
                    if count:
                        log.info("Insider fetch %s: +%d rows", ticker, count)
            except Exception as exc:
                log.warning("Insider fetch failed for %s: %s", ticker, exc)
    finally:
        await fetcher.close()
