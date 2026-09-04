"""
Celery task: refresh dividend events for every real portfolio.

Runs nightly. Dividend schedules change slowly (quarterly for most US
names), so a daily pull is generous — the point is that a newly-declared
dividend shows up in the "upcoming" list before its ex-date rather than
after the user has already missed it.

Paper portfolios are skipped: their positions are synthetic and dividend
income there would be misleading.
"""

import asyncio
import logging

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.portfolio import BrokerType, Portfolio
from app.services.dividend import DividendService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.dividend_sync.sync_all_dividends", bind=True)
def sync_all_dividends(self):
    return asyncio.run(_sync())


async def _sync() -> dict:
    async with AsyncSessionLocal() as db:
        portfolios = list((await db.execute(
            select(Portfolio).where(
                Portfolio.is_active == True,  # noqa: E712
                Portfolio.broker != BrokerType.paper,
            )
        )).scalars().all())

        if not portfolios:
            log.info("Dividend sync: no active real portfolios")
            return {"portfolios": 0, "tickers": 0, "rows_written": 0}

        svc = DividendService(db)
        # Dedupe across portfolios — the same ticker held in two
        # portfolios only needs fetching once.
        seen: set[str] = set()
        total_written = 0
        for p in portfolios:
            tickers = await svc._all_traded_tickers(p.id)
            for t in tickers:
                if t in seen:
                    continue
                seen.add(t)
                total_written += await svc.sync_ticker(t)

        await db.commit()
        log.info(
            "Dividend sync: %d portfolios, %d unique tickers, %d rows written",
            len(portfolios), len(seen), total_written,
        )
        return {
            "portfolios": len(portfolios),
            "tickers": len(seen),
            "rows_written": total_written,
        }
