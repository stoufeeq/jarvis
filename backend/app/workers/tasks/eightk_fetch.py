"""
Celery task: fetch SEC 8-K material event filings for all watchlist tickers.
Runs once daily at 8:00 AM UTC (after markets open, before US market hours).
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.eightk_fetcher import EightKFetcher
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.eightk_fetch.fetch_all_8k_filings", bind=True)
def fetch_all_8k_filings(self):
    asyncio.run(_fetch_all())


async def _fetch_all():
    async with AsyncSessionLocal() as db:
        fetcher = EightKFetcher(db)
        totals = await fetcher.fetch_for_all_watchlist_tickers()
        await db.commit()

    total_new = sum(totals.values())
    if total_new:
        log.info("8-K fetch complete: %d new filings across %d tickers", total_new, len(totals))
    else:
        log.info("8-K fetch complete: no new filings")
