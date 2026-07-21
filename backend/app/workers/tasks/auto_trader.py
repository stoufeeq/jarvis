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


@celery_app.task(name="app.workers.tasks.auto_trader.stop_loss_sweep", bind=True)
def stop_loss_sweep(self):
    """Short-cadence stop-loss check. Bypasses min_hold_days by design —
    a fast adverse move should be cut immediately, not held to satisfy
    a minimum hold ceremony. Uses cached Position.current_price which
    is refreshed by market_data.refresh_all_positions every 5 min."""
    asyncio.run(_run_stop_loss())


async def _run_stop_loss():
    async with AsyncSessionLocal() as db:
        counts = await AutoTraderService(db).stop_loss_sweep()
        await db.commit()
        if counts.get("stop_loss_closed", 0) > 0:
            log.info("Auto-trader stop-loss sweep: %s", counts)
