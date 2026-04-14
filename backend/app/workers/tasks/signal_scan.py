"""
Celery tasks: technical signal scanning.
"""

import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.watchlist import WatchlistItem
from app.services.signal_engine import SignalEngine
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.signal_scan.scan_all_watchlist_tickers", bind=True)
def scan_all_watchlist_tickers(self):
    asyncio.run(_scan_all_watchlist_tickers())


async def _scan_all_watchlist_tickers():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WatchlistItem.ticker).distinct())
        tickers = [row[0] for row in result.all()]

        engine = SignalEngine(db)
        for ticker in tickers:
            try:
                await engine.scan_ticker(ticker)
            except Exception:
                pass  # log and continue

        await db.commit()


@celery_app.task(name="app.workers.tasks.signal_scan.scan_ticker")
def scan_ticker(ticker: str):
    asyncio.run(_scan_ticker(ticker))


async def _scan_ticker(ticker: str):
    async with AsyncSessionLocal() as db:
        engine = SignalEngine(db)
        await engine.scan_ticker(ticker.upper())
        await db.commit()
