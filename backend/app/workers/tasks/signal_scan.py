"""
Celery tasks: technical signal scanning.
"""

import asyncio
import logging
import time

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.watchlist import WatchlistItem
from app.services.signal_engine import SignalEngine
from app.workers.celery_app import celery_app

log = logging.getLogger("jarvis")

# Concurrency cap for the fan-out scan. Each concurrent scan makes 3–4
# outbound HTTP requests (yfinance, SEC, Finnhub) in providers plus a DB
# session; 8 is a sane balance between throughput and rate-limit risk.
# Every increase past ~12 starts triggering yfinance/Yahoo 429s and
# multiple Postgres connections per worker process.
SCAN_CONCURRENCY = 8


@celery_app.task(name="app.workers.tasks.signal_scan.scan_all_watchlist_tickers", bind=True)
def scan_all_watchlist_tickers(self):
    asyncio.run(_scan_all_watchlist_tickers())


async def _scan_all_watchlist_tickers():
    # Read the ticker list in one short-lived session, then release it
    # so the fan-out below can each grab their own connection cleanly.
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WatchlistItem.ticker).distinct())
        tickers = [row[0] for row in result.all()]

    if not tickers:
        return

    started = time.monotonic()
    log.info("Signal scan: starting %d tickers (concurrency=%d)", len(tickers), SCAN_CONCURRENCY)

    # Fan out with a semaphore. Each concurrent scan uses its own DB
    # session — async SQLAlchemy sessions are NOT task-safe, so sharing
    # one session across gather'd coroutines would race on the underlying
    # connection. Independent sessions keep each provider's writes
    # committed atomically per ticker.
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def _scan_one(ticker: str) -> list[int]:
        async with sem:
            async with AsyncSessionLocal() as ticker_db:
                try:
                    engine = SignalEngine(ticker_db)
                    signals = await engine.scan_ticker(ticker)
                    await ticker_db.commit()
                    return [s.id for s in signals if s.id]
                except Exception:
                    # Log & swallow so one bad ticker doesn't fail the batch.
                    log.warning("Signal scan: %s failed", ticker, exc_info=True)
                    return []

    per_ticker_ids = await asyncio.gather(*[_scan_one(t) for t in tickers])
    all_new_signal_ids: list[int] = [sid for sublist in per_ticker_ids for sid in sublist]

    elapsed = time.monotonic() - started
    log.info(
        "Signal scan: %d tickers done in %.1fs → %d new signals",
        len(tickers), elapsed, len(all_new_signal_ids),
    )

    # Auto-trader still runs on a single session — it evaluates every
    # new signal against every active strategy, which reads shared state
    # (Positions, Portfolio.cash_balance) that must stay consistent.
    if all_new_signal_ids:
        async with AsyncSessionLocal() as db:
            from app.services.auto_trader import AutoTraderService
            try:
                counts = await AutoTraderService(db).process_new_signals(all_new_signal_ids)
                if any(counts.values()):
                    log.info("Auto-trader: %s", counts)
                await db.commit()
            except Exception:
                # Auto-trader failures must never break signal scanning.
                log.exception("Auto-trader failed")


@celery_app.task(name="app.workers.tasks.signal_scan.scan_ticker")
def scan_ticker(ticker: str):
    asyncio.run(_scan_ticker(ticker))


async def _scan_ticker(ticker: str):
    async with AsyncSessionLocal() as db:
        engine = SignalEngine(db)
        signals = await engine.scan_ticker(ticker.upper())
        await db.commit()
        # Same auto-trader hook for on-demand scans
        if signals:
            from app.services.auto_trader import AutoTraderService
            try:
                await AutoTraderService(db).process_new_signals([s.id for s in signals if s.id])
                await db.commit()
            except Exception:
                import logging
                logging.getLogger("jarvis").exception("Auto-trader failed")
