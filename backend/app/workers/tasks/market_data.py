"""
Celery tasks: market data refresh.
"""

import asyncio
import math
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.portfolio import Position
from app.models.watchlist import WatchlistItem
from app.services.market_data import MarketDataService
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.market_data.refresh_all_positions", bind=True)
def refresh_all_positions(self):
    asyncio.run(_refresh_all_prices())


def _safe_price(value) -> float | None:
    """Return value as float if finite, else None."""
    if value is None:
        return None
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


async def _refresh_all_prices():
    async with AsyncSessionLocal() as db:
        positions = list((await db.execute(select(Position))).scalars().all())
        watchlist_items = list((await db.execute(select(WatchlistItem))).scalars().all())

        # Collect all unique tickers across positions and watchlist
        all_tickers = list({p.ticker for p in positions} | {w.ticker for w in watchlist_items})
        if not all_tickers:
            return

        mds = MarketDataService()
        raw_quotes = await mds.get_quotes(all_tickers)
        # Build a full quote map (not just price) for watchlist caching
        quote_map = {q["ticker"]: q for q in raw_quotes}
        now = datetime.now(timezone.utc)

        # Update position prices
        for pos in positions:
            q = quote_map.get(pos.ticker)
            if not q:
                continue
            cp = _safe_price(q.get("price"))
            if cp:
                avg_cost = float(pos.avg_cost)
                qty = float(pos.quantity)
                pos.current_price = cp
                pos.unrealized_pnl = round((cp - avg_cost) * qty, 4)
                pos.unrealized_pnl_pct = round((cp - avg_cost) / avg_cost * 100, 2) if avg_cost else 0

        # Update watchlist item price cache
        for item in watchlist_items:
            q = quote_map.get(item.ticker)
            if not q:
                continue
            item.last_price = _safe_price(q.get("price"))
            item.last_change = _safe_price(q.get("change"))
            item.last_change_pct = _safe_price(q.get("change_pct"))
            item.previous_close = _safe_price(q.get("previous_close"))
            item.fifty_two_week_high = _safe_price(q.get("fifty_two_week_high"))
            item.fifty_two_week_low = _safe_price(q.get("fifty_two_week_low"))
            item.price_updated_at = now

        await db.commit()
