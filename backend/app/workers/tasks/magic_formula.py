"""
Celery task: recompute the Magic Formula screen.

Weekly. The inputs are annual-report figures that change quarterly at
most, so the cadence is generous — the job exists because the compute is
slow (three yfinance calls per ticker across ~500 names, 15-25 minutes),
not because the data moves fast.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.magic_formula import MagicFormulaService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.tasks.magic_formula.refresh_magic_formula",
    bind=True,
    # Long-running by nature; give it room rather than letting a default
    # timeout kill it three-quarters of the way through.
    time_limit=3600,
    soft_time_limit=3300,
)
def refresh_magic_formula(self):
    return asyncio.run(_refresh())


async def _refresh() -> dict:
    async with AsyncSessionLocal() as db:
        result = await MagicFormulaService(db).refresh()
        await db.commit()
        log.info("Magic formula refreshed: %s", result)
        return result
