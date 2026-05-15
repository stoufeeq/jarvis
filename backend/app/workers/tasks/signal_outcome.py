"""
Celery tasks: signal outcome snapshots and backfill.

snapshot_signal_outcomes — periodic task. Fills 1d/5d/30d/90d snapshots
using historical close prices (correct even when many days behind).

backfill_signal_outcomes — one-shot helper, populates outcomes for any
existing signals that don't have a matching outcome row yet.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.signal_outcome import SignalOutcomeService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.signal_outcome.snapshot_signal_outcomes", bind=True)
def snapshot_signal_outcomes(self, force_refresh: bool = False):
    """Periodic snapshot fill. Pass force_refresh=True to also overwrite
    snapshots already populated (used once to scrub the corrupted values
    that the old current-quote-based job wrote)."""
    asyncio.run(_run_snapshots(force_refresh=force_refresh))


async def _run_snapshots(force_refresh: bool = False):
    async with AsyncSessionLocal() as db:
        svc = SignalOutcomeService(db)
        counts = await svc.refresh_snapshots_from_history(force_refresh=force_refresh)
        # refresh_snapshots_from_history already commits per ticker.
        log.info("Signal outcome snapshots taken (force=%s): %s", force_refresh, counts)


@celery_app.task(name="app.workers.tasks.signal_outcome.backfill_signal_outcomes", bind=True)
def backfill_signal_outcomes(self, limit: int | None = None):
    asyncio.run(_run_backfill(limit))


async def _run_backfill(limit: int | None):
    async with AsyncSessionLocal() as db:
        svc = SignalOutcomeService(db)
        count = await svc.backfill_from_existing_signals(limit=limit)
        await db.commit()
        log.info("Backfilled %d signal outcomes", count)
