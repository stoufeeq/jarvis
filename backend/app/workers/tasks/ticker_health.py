"""
Celery task: probe every referenced ticker against the data provider.

Runs daily. Combined with TickerHealthService.FAILURE_THRESHOLD of 3,
that means a genuinely bad symbol surfaces in the UI within three days
while transient provider flakiness — which is common even for large
liquid names — never trips the warning.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.ticker_health import TickerHealthService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.ticker_health.check_ticker_health", bind=True)
def check_ticker_health(self):
    return asyncio.run(_check())


async def _check() -> dict:
    async with AsyncSessionLocal() as db:
        result = await TickerHealthService(db).check_all()
        await db.commit()
        log.info(
            "Ticker health: checked %d, newly broken %d, recovered %d",
            result["checked"], result["broken"], result["recovered"],
        )
        return result
