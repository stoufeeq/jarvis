"""
Celery tasks: market data refresh.
"""

import asyncio
import math

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.portfolio import Position
from app.services.market_data import MarketDataService
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.market_data.refresh_all_positions", bind=True)
def refresh_all_positions(self):
    asyncio.run(_refresh_all_positions())


async def _refresh_all_positions():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Position))
        positions = result.scalars().all()

        if not positions:
            return

        tickers = list({p.ticker for p in positions})
        mds = MarketDataService()
        quotes = await mds.get_quotes(tickers)
        price_map = {
            q["ticker"]: q["price"]
            for q in quotes
            if q.get("price") is not None and math.isfinite(float(q["price"]))
        }

        for pos in positions:
            cp = price_map.get(pos.ticker)
            if cp:
                avg_cost = float(pos.avg_cost)
                qty = float(pos.quantity)
                pos.current_price = cp
                pos.unrealized_pnl = round((cp - avg_cost) * qty, 4)
                pos.unrealized_pnl_pct = round((cp - avg_cost) / avg_cost * 100, 2) if avg_cost else 0

        await db.commit()
