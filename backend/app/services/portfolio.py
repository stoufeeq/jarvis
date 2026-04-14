import csv
import io
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import AssetType, Portfolio, Position, Trade, TradeAction
from app.schemas.portfolio import (
    PortfolioCreate,
    PortfolioSummary,
    PortfolioUpdate,
    TradeCreate,
    TradeUpdate,
)
from app.services.market_data import MarketDataService


class PortfolioService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, portfolio_id: int) -> Portfolio | None:
        result = await self.db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[Portfolio]:
        result = await self.db.execute(
            select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def create(self, user_id: int, payload: PortfolioCreate) -> Portfolio:
        p = Portfolio(user_id=user_id, **payload.model_dump())
        self.db.add(p)
        await self.db.flush()
        await self.db.refresh(p)
        return p

    async def update(self, portfolio: Portfolio, payload: PortfolioUpdate) -> Portfolio:
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(portfolio, field, value)
        await self.db.flush()
        await self.db.refresh(portfolio)
        return portfolio

    async def delete(self, portfolio: Portfolio) -> None:
        portfolio.is_active = False
        await self.db.flush()

    async def list_positions(self, portfolio_id: int) -> list[Position]:
        result = await self.db.execute(
            select(Position).where(Position.portfolio_id == portfolio_id)
        )
        positions = list(result.scalars().all())

        tickers = [p.ticker for p in positions]
        if tickers:
            try:
                mds = MarketDataService()
                quotes = await mds.get_quotes(tickers)
                price_map = {q["ticker"]: q["price"] for q in quotes}
                for pos in positions:
                    cp = price_map.get(pos.ticker)
                    if cp:
                        avg_cost = float(pos.avg_cost)
                        qty = float(pos.quantity)
                        pos.current_price = cp
                        pos.unrealized_pnl = round((cp - avg_cost) * qty, 4)
                        pos.unrealized_pnl_pct = round((cp - avg_cost) / avg_cost * 100, 2)
            except Exception:
                pass  # return positions without live prices rather than failing

        return positions

    async def list_trades(self, portfolio_id: int) -> list[Trade]:
        result = await self.db.execute(
            select(Trade)
            .where(Trade.portfolio_id == portfolio_id)
            .order_by(Trade.traded_at.desc())
        )
        return list(result.scalars().all())

    async def add_trade(self, portfolio_id: int, payload: TradeCreate) -> Trade:
        trade = Trade(portfolio_id=portfolio_id, **payload.model_dump())
        self.db.add(trade)
        await self.db.flush()
        await self._update_position(portfolio_id, trade)
        await self.db.refresh(trade)
        return trade

    async def _recalculate_position(self, portfolio_id: int, ticker: str) -> None:
        """Delete and rebuild a position by replaying all trades for that ticker."""
        # Remove existing position
        result = await self.db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.ticker == ticker,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            await self.db.delete(existing)
            await self.db.flush()

        # Replay trades in chronological order
        result = await self.db.execute(
            select(Trade)
            .where(Trade.portfolio_id == portfolio_id, Trade.ticker == ticker)
            .order_by(Trade.traded_at.asc())
        )
        for trade in result.scalars().all():
            await self._update_position(portfolio_id, trade)

    async def update_trade(self, trade: Trade, payload: TradeUpdate) -> Trade:
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(trade, field, value)
        await self.db.flush()
        await self._recalculate_position(trade.portfolio_id, trade.ticker)
        await self.db.refresh(trade)
        return trade

    async def delete_trade(self, trade: Trade) -> None:
        portfolio_id, ticker = trade.portfolio_id, trade.ticker
        await self.db.delete(trade)
        await self.db.flush()
        await self._recalculate_position(portfolio_id, ticker)

    async def _update_position(self, portfolio_id: int, trade: Trade) -> None:
        result = await self.db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.ticker == trade.ticker,
            )
        )
        position = result.scalar_one_or_none()

        # Cast everything to float — position fields come from DB as Decimal,
        # trade fields may be float or Decimal depending on flush state.
        t_qty = float(trade.quantity)
        t_price = float(trade.price)

        if trade.action in (TradeAction.buy, TradeAction.short):
            if position is None:
                position = Position(
                    portfolio_id=portfolio_id,
                    ticker=trade.ticker,
                    asset_type=trade.asset_type,
                    quantity=t_qty,
                    avg_cost=t_price,
                    currency=trade.currency,
                    opened_at=trade.traded_at,
                )
                self.db.add(position)
            else:
                p_qty = float(position.quantity)
                p_cost = float(position.avg_cost)
                total_qty = p_qty + t_qty
                position.avg_cost = ((p_cost * p_qty) + (t_price * t_qty)) / total_qty
                position.quantity = total_qty
        elif trade.action in (TradeAction.sell, TradeAction.cover) and position:
            position.quantity = float(position.quantity) - t_qty
            if position.quantity <= 0:
                await self.db.delete(position)

        await self.db.flush()

    async def get_summary(self, portfolio: Portfolio) -> PortfolioSummary:
        positions = await self.list_positions(portfolio.id)
        base_ccy = (portfolio.currency or "USD").upper()

        # Use DB-cached prices (updated every 5 min by Celery).
        # Only call yfinance for tickers that have no cached price yet.
        price_map: dict[str, float] = {}
        uncached = []
        for p in positions:
            if p.current_price is not None:
                price_map[p.ticker] = float(p.current_price)
            else:
                uncached.append(p.ticker)

        if uncached:
            mds = MarketDataService()
            try:
                quotes = await mds.get_quotes(uncached)
                for q in quotes:
                    price_map[q["ticker"]] = q["price"]
            except Exception:
                pass

        # Fetch FX rates for any non-base currencies used in positions
        foreign_ccys = list({
            (p.currency or "USD").upper()
            for p in positions
            if (p.currency or "USD").upper() != base_ccy
        })
        fx_rates: dict[str, float] = {}
        if foreign_ccys:
            try:
                fx_rates = await MarketDataService().get_fx_rates(foreign_ccys, base=base_ccy)
            except Exception:
                pass  # fall back to 1:1 if FX fetch fails

        def to_base(amount: float, ccy: str) -> float:
            ccy = (ccy or "USD").upper()
            if ccy == base_ccy:
                return amount
            rate = fx_rates.get(ccy, 1.0)
            return amount * rate

        total_cost = sum(
            to_base(float(p.avg_cost) * float(p.quantity), p.currency or "USD")
            for p in positions
        )
        total_value = 0.0
        for pos in positions:
            cp = price_map.get(pos.ticker)
            if cp:
                avg_cost = float(pos.avg_cost)
                qty = float(pos.quantity)
                pos.current_price = cp
                pos.unrealized_pnl = round((cp - avg_cost) * qty, 4)
                pos.unrealized_pnl_pct = round((cp - avg_cost) / avg_cost * 100, 2)
                total_value += to_base(cp * qty, pos.currency or "USD")

        total_pnl = total_value - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else None

        # Today's change — read from the in-process quote cache (no extra HTTP calls)
        mds_cache = MarketDataService()
        day_change = 0.0
        for pos in positions:
            q = mds_cache.get_cached_quote(pos.ticker)
            if q:
                change_per_share = q.get("change", 0.0) or 0.0
                day_change += to_base(float(pos.quantity) * change_per_share, pos.currency or "USD")
        prev_total = total_value - day_change
        day_change_pct = (day_change / prev_total * 100) if prev_total else None

        return PortfolioSummary(
            **{c.key: getattr(portfolio, c.key) for c in portfolio.__table__.columns},
            total_value=round(total_value, 2),
            total_cost=round(total_cost, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 2) if total_pnl_pct else None,
            day_change=round(day_change, 2),
            day_change_pct=round(day_change_pct, 2) if day_change_pct is not None else None,
            position_count=len(positions),
        )

    async def get_context_for_ai(self, portfolio: Portfolio) -> dict:
        """Build a serialisable dict passed to the AI advisor as context."""
        positions = await self.list_positions(portfolio.id)
        summary = await self.get_summary(portfolio)
        return {
            "portfolio_name": portfolio.name,
            "currency": portfolio.currency,
            "total_value": summary.total_value,
            "total_pnl": summary.total_pnl,
            "total_pnl_pct": summary.total_pnl_pct,
            "positions": [
                {
                    "ticker": p.ticker,
                    "quantity": p.quantity,
                    "avg_cost": p.avg_cost,
                    "current_price": p.current_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct,
                }
                for p in positions
            ],
        }

    async def import_from_csv(self, portfolio_id: int, content: bytes) -> int:
        """
        Import trades from an IBKR Activity Statement CSV.
        Handles the 'Trades' section of the IBKR flex report.
        Returns number of trades imported.
        """
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
        count = 0
        for row in reader:
            # IBKR CSV field names — adapt to your export format
            try:
                action_str = row.get("Buy/Sell", "").strip().upper()
                action = TradeAction.buy if action_str == "BUY" else TradeAction.sell
                trade = Trade(
                    portfolio_id=portfolio_id,
                    ticker=row["Symbol"].strip(),
                    asset_type=AssetType.stock,
                    action=action,
                    quantity=float(row["Quantity"].replace(",", "")),
                    price=float(row["T. Price"].replace(",", "")),
                    fees=abs(float(row.get("Comm/Fee", "0").replace(",", ""))),
                    traded_at=datetime.strptime(
                        row["Date/Time"].strip(), "%Y-%m-%d, %H:%M:%S"
                    ).replace(tzinfo=timezone.utc),
                    external_id=row.get("Order ID") or None,
                )
                self.db.add(trade)
                await self.db.flush()
                await self._update_position(portfolio_id, trade)
                count += 1
            except (KeyError, ValueError):
                continue  # skip malformed rows

        return count
