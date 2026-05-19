import csv
import io
import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from decimal import Decimal

from app.models.portfolio import AssetType, BrokerType, Portfolio, Position, Trade, TradeAction
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
        # For paper portfolios: enforce single per user, default cash to $100k
        if payload.broker == BrokerType.paper:
            existing = await self.db.execute(
                select(Portfolio).where(
                    Portfolio.user_id == user_id,
                    Portfolio.broker == BrokerType.paper,
                    Portfolio.is_active.is_(True),
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise ValueError("A paper portfolio already exists for this user")

            initial = payload.initial_cash if payload.initial_cash and payload.initial_cash > 0 else 100_000.0
            p = Portfolio(
                user_id=user_id,
                name=payload.name,
                description=payload.description,
                broker=BrokerType.paper,
                currency=payload.currency,
                initial_cash=Decimal(str(initial)),
                cash_balance=Decimal(str(initial)),
            )
        else:
            data = payload.model_dump(exclude={"initial_cash"})
            p = Portfolio(user_id=user_id, **data)

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
        """Return positions using DB-cached prices only.

        Prices are kept fresh by the Celery worker (every 5 min).  Making a
        live yfinance call here added 500 ms–2 s of latency on every page load
        for no meaningful accuracy gain over a 5-minute cache.
        """
        result = await self.db.execute(
            select(Position).where(Position.portfolio_id == portfolio_id)
        )
        positions = list(result.scalars().all())

        # Sanitize any NaN/Inf values previously written to DB by the Celery worker
        for pos in positions:
            for field in ("current_price", "unrealized_pnl", "unrealized_pnl_pct"):
                v = getattr(pos, field)
                if v is not None and not math.isfinite(float(v)):
                    setattr(pos, field, None)

        return positions

    async def list_trades(self, portfolio_id: int) -> list[Trade]:
        result = await self.db.execute(
            select(Trade)
            .where(Trade.portfolio_id == portfolio_id)
            .order_by(Trade.traded_at.desc())
        )
        return list(result.scalars().all())

    async def add_trade(self, portfolio_id: int, payload: TradeCreate) -> Trade:
        from app.services.trade_cash import TradeCashService

        trade = Trade(portfolio_id=portfolio_id, **payload.model_dump())
        self.db.add(trade)
        await self.db.flush()
        await self._update_position(portfolio_id, trade)

        # Auto-settle cash against the user's accounts for real portfolios.
        # Paper portfolios use their own portfolio.cash_balance and skip this.
        portfolio = await self.get(portfolio_id)
        if portfolio is not None:
            await TradeCashService(self.db).on_trade_created(portfolio, trade)

        await self.db.refresh(trade)
        return trade

    async def execute_paper_trade(
        self,
        portfolio: Portfolio,
        ticker: str,
        action: TradeAction,
        quantity: float,
    ) -> Trade:
        """Execute a paper trade against the user's virtual cash balance.

        - Validates the portfolio is a paper portfolio
        - Fetches the current quote (used as fill price)
        - Validates sufficient cash for buys, sufficient shares for sells
        - Creates a Trade row, updates the Position, updates cash_balance
        """
        if portfolio.broker != BrokerType.paper:
            raise ValueError("Paper trades can only be executed on paper portfolios")
        if action not in (TradeAction.buy, TradeAction.sell):
            raise ValueError("Paper trades support only 'buy' or 'sell'")
        if quantity <= 0:
            raise ValueError("Quantity must be positive")

        ticker = ticker.upper().strip()

        # Fetch fill price
        quotes = await MarketDataService().get_quotes([ticker])
        if not quotes or not quotes[0].get("price"):
            raise ValueError(f"Could not fetch quote for {ticker}")
        price = float(quotes[0]["price"])
        if price <= 0 or not math.isfinite(price):
            raise ValueError(f"Invalid quote price for {ticker}: {price}")

        cost = price * quantity
        cash = float(portfolio.cash_balance or 0)

        if action == TradeAction.buy:
            if cost > cash:
                raise ValueError(
                    f"Insufficient cash: trade requires ${cost:,.2f} but only ${cash:,.2f} available"
                )
            new_cash = cash - cost
        else:  # sell
            # Verify the user holds enough shares
            pos_result = await self.db.execute(
                select(Position).where(
                    Position.portfolio_id == portfolio.id,
                    Position.ticker == ticker,
                )
            )
            existing_pos = pos_result.scalar_one_or_none()
            if existing_pos is None or float(existing_pos.quantity) < quantity:
                held = float(existing_pos.quantity) if existing_pos else 0
                raise ValueError(
                    f"Insufficient shares: trying to sell {quantity} but only {held} held"
                )
            new_cash = cash + cost

        # Detect asset type — crypto vs stock
        from app.data.crypto import is_crypto
        asset_type = AssetType.crypto if is_crypto(ticker) else AssetType.stock

        # Create the Trade row (immutable ledger)
        trade = Trade(
            portfolio_id=portfolio.id,
            ticker=ticker,
            asset_type=asset_type,
            action=action,
            quantity=Decimal(str(quantity)),
            price=Decimal(str(price)),
            fees=Decimal("0"),
            currency="USD" if asset_type == AssetType.crypto else (portfolio.currency or "USD"),
            traded_at=datetime.now(timezone.utc),
            notes="Paper trade",
        )
        self.db.add(trade)
        await self.db.flush()

        # Update or create the position
        await self._update_position(portfolio.id, trade)

        # Update virtual cash balance
        portfolio.cash_balance = Decimal(str(new_cash))
        await self.db.flush()
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
        from app.services.trade_cash import TradeCashService

        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(trade, field, value)
        await self.db.flush()
        await self._recalculate_position(trade.portfolio_id, trade.ticker)

        portfolio = await self.get(trade.portfolio_id)
        if portfolio is not None:
            await TradeCashService(self.db).on_trade_updated(portfolio, trade)

        await self.db.refresh(trade)
        return trade

    async def delete_trade(self, trade: Trade) -> None:
        from app.services.trade_cash import TradeCashService

        # Reverse cash flow first while the trade still exists (FK refs).
        portfolio = await self.get(trade.portfolio_id)
        if portfolio is not None:
            await TradeCashService(self.db).reverse_for_trade(trade)

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
            if p.current_price is not None and math.isfinite(float(p.current_price)):
                price_map[p.ticker] = float(p.current_price)
            else:
                uncached.append(p.ticker)

        if uncached:
            mds = MarketDataService()
            try:
                quotes = await mds.get_quotes(uncached)
                for q in quotes:
                    price = q.get("price")
                    if price is not None and math.isfinite(float(price)):
                        price_map[q["ticker"]] = price
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

        # Today's change — derived from DB-cached current_price and previous_close.
        # Both are written by the Celery worker every 5 min, so this works correctly
        # across API/worker container boundaries (no in-process cache dependency).
        day_change = 0.0
        for pos in positions:
            cp = price_map.get(pos.ticker)
            prev = pos.previous_close
            if cp is not None and prev is not None:
                prev_f = float(prev)
                if math.isfinite(prev_f) and prev_f > 0:
                    change_per_share = cp - prev_f
                    day_change += to_base(float(pos.quantity) * change_per_share, pos.currency or "USD")
        prev_total = total_value - day_change
        day_change_pct = (day_change / prev_total * 100) if prev_total else None

        def _safe(v: float | None) -> float | None:
            if v is None:
                return None
            return None if not math.isfinite(v) else round(v, 2)

        return PortfolioSummary(
            **{c.key: getattr(portfolio, c.key) for c in portfolio.__table__.columns},
            total_value=_safe(total_value) or 0.0,
            total_cost=_safe(total_cost) or 0.0,
            total_pnl=_safe(total_pnl) or 0.0,
            total_pnl_pct=_safe(total_pnl_pct),
            day_change=_safe(day_change) or 0.0,
            day_change_pct=_safe(day_change_pct),
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
