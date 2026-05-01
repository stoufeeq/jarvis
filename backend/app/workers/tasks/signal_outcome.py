"""
Celery tasks: signal outcome snapshots and backfill.

snapshot_signal_outcomes — runs daily, finds outcomes whose +1d/+5d/+30d/+90d
snapshots are due and fills in the current price.

backfill_signal_outcomes — one-shot helper, populates outcomes for any
existing signals that don't have a matching outcome row yet, using
historical yfinance data.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.signal_outcome import SignalOutcomeService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.signal_outcome.snapshot_signal_outcomes", bind=True)
def snapshot_signal_outcomes(self):
    asyncio.run(_run_snapshots())


async def _run_snapshots():
    async with AsyncSessionLocal() as db:
        svc = SignalOutcomeService(db)
        counts = await svc.snapshot_due_outcomes()
        await db.commit()
        log.info("Signal outcome snapshots taken: %s", counts)


@celery_app.task(name="app.workers.tasks.signal_outcome.backfill_signal_outcomes", bind=True)
def backfill_signal_outcomes(self, limit: int | None = None):
    asyncio.run(_run_backfill(limit))


async def _run_backfill(limit: int | None):
    async with AsyncSessionLocal() as db:
        svc = SignalOutcomeService(db)
        count = await svc.backfill_from_existing_signals(limit=limit)
        await db.commit()
        log.info("Backfilled %d signal outcomes", count)
