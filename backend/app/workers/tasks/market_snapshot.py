"""
Celery task: refresh the market_snapshots cache every 4 hours.

The AI advisor reads from this table to ground chat responses in
current market data instead of Gemini's training-cutoff knowledge.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.market_snapshot import MarketSnapshotService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.market_snapshot.refresh_market_snapshot", bind=True)
def refresh_market_snapshot(self):
    asyncio.run(_run())


async def _run() -> None:
    async with AsyncSessionLocal() as db:
        svc = MarketSnapshotService(db)
        try:
            payload = await svc.refresh()
            log.info(
                "Market snapshot refreshed: %d indices, %d asset_classes, "
                "%d crypto, %d forex, %d sectors, %d headlines, %d macro",
                len(payload.get("indices", {})),
                len(payload.get("asset_classes", {})),
                len(payload.get("crypto", {})),
                len(payload.get("forex", {})),
                len(payload.get("sectors", [])),
                len(payload.get("headlines", [])),
                len(payload.get("upcoming_macro", [])),
            )
        except Exception as exc:
            log.warning("Market snapshot refresh failed: %s", exc)

        try:
            removed = await svc.prune_old()
            if removed:
                log.info("Pruned %d old market snapshots", removed)
        except Exception as exc:
            log.warning("Market snapshot prune failed: %s", exc)
