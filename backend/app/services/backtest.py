"""
Strategy backtester — simulates "what if I'd taken every signal matching X
filter, with $N per trade, holding for D days?" over the existing
signal_outcomes data.

Builds on the SignalOutcome table (filled by SignalOutcomeService) so no
extra historical fetches are needed. Each outcome is a complete trade:
entry at entry_price, exit at price_{1d|5d|30d|90d}.

Compares the strategy's equity curve against a SPY buy-and-hold benchmark
over the same date range.
"""

import logging
import math
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import SignalDirection, SignalType
from app.models.signal_outcome import SignalOutcome
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)

HoldPeriod = Literal["1d", "5d", "30d", "90d"]
DEFAULT_BENCHMARK = "SPY"


class BacktestService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def simulate(
        self,
        signal_type: SignalType | None = None,
        direction: SignalDirection | None = None,
        min_strength: int = 1,
        hold_period: HoldPeriod = "5d",
        capital_per_trade: float = 1000.0,
        ticker: str | None = None,
    ) -> dict:
        """Run the backtest. Returns metrics + equity curve + benchmark."""
        outcomes = await self._fetch_filtered_outcomes(
            signal_type, direction, min_strength, hold_period, ticker
        )
        if not outcomes:
            return self._empty_result()

        # Order chronologically so the equity curve plays back in time order
        outcomes.sort(key=lambda o: o.signal_created_at)

        price_attr = f"price_{hold_period}"

        equity_curve: list[dict] = []
        running_pnl = 0.0
        wins = 0
        losses = 0
        trade_pnls: list[float] = []
        peak = 0.0
        max_drawdown = 0.0  # signed negative number

        for o in outcomes:
            entry = float(o.entry_price)
            exit_price = float(getattr(o, price_attr) or 0)
            # NaN guard: Postgres NUMERIC can hold NaN (some pre-fix snapshot
            # rows have it). NaN <= 0 is False, so it slips past a naive
            # check and then poisons every subsequent sum with NaN.
            if not math.isfinite(entry) or not math.isfinite(exit_price):
                continue
            if entry <= 0 or exit_price <= 0:
                continue

            # Long for bullish, short for bearish (neutral signals skipped)
            if o.direction.value == "bullish":
                trade_return_pct = (exit_price - entry) / entry
            elif o.direction.value == "bearish":
                trade_return_pct = (entry - exit_price) / entry
            else:
                continue

            trade_pnl = capital_per_trade * trade_return_pct
            trade_pnls.append(trade_pnl)
            running_pnl += trade_pnl

            if trade_pnl > 0:
                wins += 1
            elif trade_pnl < 0:
                losses += 1

            # Track peak and drawdown
            if running_pnl > peak:
                peak = running_pnl
            drawdown = running_pnl - peak  # negative when below peak
            if drawdown < max_drawdown:
                max_drawdown = drawdown

            equity_curve.append({
                "date": o.signal_created_at.date().isoformat(),
                "ticker": o.ticker,
                "signal_type": o.signal_type.value,
                "direction": o.direction.value,
                "strength": o.strength,
                "trade_pnl": round(trade_pnl, 2),
                "cumulative_pnl": round(running_pnl, 2),
                "trade_return_pct": round(trade_return_pct * 100, 2),
            })

        n_trades = len(trade_pnls)
        if n_trades == 0:
            return self._empty_result()

        total_pnl = round(running_pnl, 2)
        total_invested = capital_per_trade * n_trades
        total_return_pct = round((running_pnl / total_invested) * 100, 2) if total_invested else 0
        avg_trade_pnl = round(sum(trade_pnls) / n_trades, 2)
        hit_rate = round((wins / n_trades) * 100, 1) if n_trades else 0
        max_dd = round(max_drawdown, 2)
        max_dd_pct = round((max_drawdown / total_invested) * 100, 2) if total_invested else 0

        # Benchmark: SPY buy-and-hold from first to last signal date
        first_date = outcomes[0].signal_created_at.date()
        last_outcome_date = self._compute_exit_date(outcomes[-1].signal_created_at, hold_period)
        benchmark = await self._compute_spy_benchmark(first_date, last_outcome_date)

        return {
            "strategy": {
                "signal_type": signal_type.value if signal_type else None,
                "direction": direction.value if direction else None,
                "min_strength": min_strength,
                "hold_period": hold_period,
                "capital_per_trade": capital_per_trade,
                "ticker": ticker,
            },
            "metrics": {
                "n_trades": n_trades,
                "wins": wins,
                "losses": losses,
                "hit_rate_pct": hit_rate,
                "total_pnl": total_pnl,
                "total_return_pct": total_return_pct,
                "avg_trade_pnl": avg_trade_pnl,
                "max_drawdown": max_dd,
                "max_drawdown_pct": max_dd_pct,
                "first_date": first_date.isoformat(),
                "last_exit_date": last_outcome_date.isoformat(),
            },
            "equity_curve": equity_curve,
            "benchmark": benchmark,
        }

    async def _fetch_filtered_outcomes(
        self,
        signal_type: SignalType | None,
        direction: SignalDirection | None,
        min_strength: int,
        hold_period: HoldPeriod,
        ticker: str | None,
    ) -> list[SignalOutcome]:
        from sqlalchemy import literal_column

        price_col = getattr(SignalOutcome, f"price_{hold_period}")
        # NaN guard: Postgres NUMERIC NaN compares GREATER than any finite
        # value, so `< 'NaN'::numeric` is true only for finite numerics.
        nan = literal_column("'NaN'::numeric")
        conditions = [
            SignalOutcome.strength >= min_strength,
            price_col.is_not(None),
            price_col < nan,
            SignalOutcome.entry_price.is_not(None),
            SignalOutcome.entry_price < nan,
            SignalOutcome.entry_price > 0,
        ]
        if signal_type:
            conditions.append(SignalOutcome.signal_type == signal_type)
        if direction:
            conditions.append(SignalOutcome.direction == direction)
        if ticker:
            conditions.append(SignalOutcome.ticker == ticker.upper())

        result = await self.db.execute(select(SignalOutcome).where(and_(*conditions)))
        return list(result.scalars().all())

    @staticmethod
    def _compute_exit_date(entry_dt: datetime, hold_period: HoldPeriod) -> date:
        """Return the calendar date when the position would be closed."""
        from datetime import timedelta
        days_map = {"1d": 1, "5d": 5, "30d": 30, "90d": 90}
        return (entry_dt + timedelta(days=days_map[hold_period])).date()

    @staticmethod
    def _empty_result() -> dict:
        return {
            "strategy": {},
            "metrics": {
                "n_trades": 0, "wins": 0, "losses": 0, "hit_rate_pct": 0,
                "total_pnl": 0, "total_return_pct": 0, "avg_trade_pnl": 0,
                "max_drawdown": 0, "max_drawdown_pct": 0,
                "first_date": None, "last_exit_date": None,
            },
            "equity_curve": [],
            "benchmark": {"return_pct": None, "first_close": None, "last_close": None},
        }

    @staticmethod
    async def _compute_spy_benchmark(start: date, end: date) -> dict:
        """Compute SPY buy-and-hold return over [start, end]. Returns dict
        with first_close, last_close, return_pct. Returns None values if
        history can't be fetched."""
        try:
            mds = MarketDataService()
            # Fetch enough history to cover the range — request "max" ish via period
            # Heuristic: use yfinance period that's just larger than range needed
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).date()
            days_back = (now - start).days
            if days_back <= 30:
                period = "1mo"
            elif days_back <= 90:
                period = "3mo"
            elif days_back <= 180:
                period = "6mo"
            elif days_back <= 365:
                period = "1y"
            else:
                period = "2y"

            history = await mds.get_history(DEFAULT_BENCHMARK, period=period, interval="1d")
            candles = history.get("candles", []) if isinstance(history, dict) else []
            if not candles:
                return {"return_pct": None, "first_close": None, "last_close": None}

            # Find first close on/after start, and last close on/before end
            def parse_date(c):
                t = c.get("time")
                if isinstance(t, int):
                    return datetime.fromtimestamp(t, tz=timezone.utc).date()
                return datetime.fromisoformat(str(t)[:10]).date()

            first_close: float | None = None
            last_close: float | None = None
            for c in candles:
                d = parse_date(c)
                close = c.get("close")
                if close is None:
                    continue
                if first_close is None and d >= start:
                    first_close = float(close)
                if d <= end:
                    last_close = float(close)

            if first_close is None or last_close is None or first_close <= 0:
                return {"return_pct": None, "first_close": first_close, "last_close": last_close}

            return_pct = round(((last_close - first_close) / first_close) * 100, 2)
            return {
                "return_pct": return_pct,
                "first_close": round(first_close, 2),
                "last_close": round(last_close, 2),
            }
        except Exception as exc:
            log.warning("Benchmark fetch failed: %s", exc)
            return {"return_pct": None, "first_close": None, "last_close": None}
