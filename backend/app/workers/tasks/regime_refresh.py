"""Celery task: refresh today's market regime classification.

Runs once daily after US market close (22:30 UTC), plus a mid-session
refresh at 15:00 UTC to catch intraday regime flips (e.g. VIX spiking
above 20 during the trading day changes bull_low_vol → bull_high_vol
and the auto_trader should react before end of day).

The task is idempotent — it upserts by date, so multiple firings on
the same day just overwrite with the latest read.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.services.regime import RegimeService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.regime_refresh.refresh_regime", bind=True)
def refresh_regime(self):
    asyncio.run(_run())


async def _run() -> None:
    async with AsyncSessionLocal() as db:
        try:
            row = await RegimeService(db).refresh_current()
            await db.commit()
            log.info(
                "Regime refreshed: %s on %s (SPX=%.2f, SMA200=%.2f, VIX=%.2f)",
                row.regime, row.date,
                float(row.spx_close) if row.spx_close else 0.0,
                float(row.spx_sma200) if row.spx_sma200 else 0.0,
                float(row.vix_close) if row.vix_close else 0.0,
            )
        except Exception as exc:
            log.warning("Regime refresh failed: %s", exc)


@celery_app.task(name="app.workers.tasks.regime_refresh.backfill_regimes", bind=True)
def backfill_regimes(self, lookback_days: int = 365, force: bool = False):
    """One-shot backfill so historical strategy_trades can be joined
    to the regime that was in effect on their entry_at date. Dispatched
    manually via Flower or the shell — not scheduled.

    `force=True` re-classifies existing rows too — needed after
    changing the classifier (e.g. adding the crisis regime tier).
    """
    return asyncio.run(_run_backfill(lookback_days, force))


async def _run_backfill(lookback_days: int, force: bool) -> dict:
    async with AsyncSessionLocal() as db:
        n = await RegimeService(db).backfill(lookback_days=lookback_days, force=force)
        await db.commit()
        log.info(
            "Regime backfill: wrote %d rows (lookback %dd, force=%s)",
            n, lookback_days, force,
        )
        return {"written": n, "lookback_days": lookback_days, "force": force}
