"""
Celery task: daily auto-trader exit sweep.

Runs once daily at 22:00 UTC (after US market close). Closes any
strategy-owned positions whose planned_exit_at has passed (respecting
min_hold_days) or which have hit the max_hold_days ceiling.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.auto_trader import AutoTraderService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.auto_trader.daily_exit_sweep", bind=True)
def daily_exit_sweep(self):
    asyncio.run(_run())


async def _run():
    async with AsyncSessionLocal() as db:
        counts = await AutoTraderService(db).daily_exit_sweep()
        await db.commit()
        log.info("Auto-trader daily exit sweep: %s", counts)
