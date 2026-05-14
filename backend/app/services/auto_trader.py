"""
AutoTraderService — runs paper trades on behalf of active strategies.

Two entry points:
- process_new_signals(signal_ids): called after a signal scan; matches each
  new signal against all active strategies, opens or extends/closes positions
  accordingly. Safe to re-call: dedupes via open-position check.

- daily_exit_sweep(): runs once daily; closes positions whose
  planned_exit_at has passed (within min/max bounds), or which have hit
  the max_hold_days ceiling.

Hard safety guarantee: every write path validates
portfolio.broker == 'paper' BEFORE any state change. Real portfolios
cannot be modified by this service even if a strategy is misconfigured.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import BrokerType, Portfolio, Position, TradeAction
from app.models.signal import Signal, SignalDirection
from app.models.strategy import (
    AllocationMode,
    Strategy,
    StrategyExitReason,
    StrategyTrade,
    StrategyTradeStatus,
)
from app.services.portfolio import PortfolioService

log = logging.getLogger(__name__)


class AutoTraderService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Public entry points ─────────────────────────────────────────────

    async def process_new_signals(self, signal_ids: list[int]) -> dict:
        """Match each new signal against active strategies.

        Returns counts of {opened, extended, closed} actions taken.
        """
        if not signal_ids:
            return {"opened": 0, "extended": 0, "closed": 0}

        # Load signals
        result = await self.db.execute(
            select(Signal).where(Signal.id.in_(signal_ids))
        )
        signals = list(result.scalars().all())
        if not signals:
            return {"opened": 0, "extended": 0, "closed": 0}

        # Load active strategies (with their paper portfolios)
        sresult = await self.db.execute(
            select(Strategy).where(Strategy.is_active.is_(True))
        )
        active_strategies = list(sresult.scalars().all())
        if not active_strategies:
            return {"opened": 0, "extended": 0, "closed": 0}

        opened = extended = closed = 0
        for strategy in active_strategies:
            # Safety: skip strategies pointing at non-paper portfolios
            portfolio = await self.db.get(Portfolio, strategy.portfolio_id)
            if not portfolio or portfolio.broker != BrokerType.paper:
                log.warning(
                    "Strategy %s targets non-paper portfolio %s — skipping",
                    strategy.id, strategy.portfolio_id,
                )
                continue

            for signal in signals:
                action = await self._handle_signal_for_strategy(strategy, portfolio, signal)
                if action == "opened":
                    opened += 1
                elif action == "extended":
                    extended += 1
                elif action == "closed":
                    closed += 1

        return {"opened": opened, "extended": extended, "closed": closed}

    async def daily_exit_sweep(self) -> dict:
        """Close positions whose planned_exit_at has passed (respecting
        min_hold_days) OR whose age >= max_hold_days."""
        now = datetime.now(UTC)
        result = await self.db.execute(
            select(StrategyTrade, Strategy).join(Strategy, StrategyTrade.strategy_id == Strategy.id)
            .where(StrategyTrade.status == StrategyTradeStatus.open)
        )
        rows = result.all()

        planned_closed = max_hold_closed = errors = 0
        for st, strat in rows:
            age_days = (now - st.entry_at).days

            # Respect minimum hold — even if planned_exit is past, wait
            if age_days < strat.min_hold_days:
                continue

            # Hard ceiling: max_hold_days
            if age_days >= strat.max_hold_days:
                ok = await self._close_position(st, strat, StrategyExitReason.max_hold)
                if ok: max_hold_closed += 1
                else: errors += 1
                continue

            # Planned exit reached
            if st.planned_exit_at <= now:
                ok = await self._close_position(st, strat, StrategyExitReason.planned)
                if ok: planned_closed += 1
                else: errors += 1

        return {"planned_closed": planned_closed, "max_hold_closed": max_hold_closed, "errors": errors}

    async def panic_close_all(self, strategy_id: int) -> int:
        """User-triggered: close every open position in a strategy at market.
        Returns count of positions closed."""
        result = await self.db.execute(
            select(StrategyTrade, Strategy).join(Strategy, StrategyTrade.strategy_id == Strategy.id)
            .where(
                StrategyTrade.strategy_id == strategy_id,
                StrategyTrade.status == StrategyTradeStatus.open,
            )
        )
        rows = result.all()
        closed = 0
        for st, strat in rows:
            ok = await self._close_position(st, strat, StrategyExitReason.panic_close)
            if ok: closed += 1
        return closed

    # ── Per-signal dispatch ─────────────────────────────────────────────

    async def _handle_signal_for_strategy(
        self,
        strategy: Strategy,
        portfolio: Portfolio,
        signal: Signal,
    ) -> str | None:
        """Decide what to do with one (strategy, signal) pair. Returns
        action label or None if no action taken."""
        # Ticker filter
        if strategy.tickers:
            allowed = {t.strip().upper() for t in strategy.tickers.split(",") if t.strip()}
            if signal.ticker.upper() not in allowed:
                return None

        # Find existing open position in this strategy on this ticker
        open_pos = await self._get_open_position(strategy.id, signal.ticker)

        # Case 1: signal matches strategy filter AND direction → maybe BUY or EXTEND
        if self._signal_matches_filter(strategy, signal):
            if open_pos is None:
                # New entry
                opened = await self._open_position(strategy, portfolio, signal)
                return "opened" if opened else None
            else:
                # Already in this direction — extend hold if enabled
                if strategy.extend_on_continuing_signal and open_pos.direction == signal.direction:
                    self._extend_hold(strategy, open_pos)
                    return "extended"
                return None

        # Case 2: opposing-direction signal on a held position → maybe CLOSE early
        if (
            open_pos is not None
            and strategy.exit_on_opposite_signal
            and self._is_opposite_direction(open_pos.direction, signal.direction)
            and self._meets_strength_threshold(strategy, signal)
        ):
            age = (datetime.now(UTC) - open_pos.entry_at).days
            if age >= strategy.min_hold_days:
                ok = await self._close_position(open_pos, strategy, StrategyExitReason.opposite_signal)
                return "closed" if ok else None

        return None

    # ── Action: open ────────────────────────────────────────────────────

    async def _open_position(
        self,
        strategy: Strategy,
        portfolio: Portfolio,
        signal: Signal,
    ) -> bool:
        """Open a new paper position triggered by a matching signal.
        Returns True on success.

        Direction handling: bullish → buy; bearish would require shorting
        which paper trading doesn't currently support, so bearish signals
        are skipped for opens (still trigger exits via Case 2 above).
        Neutral signals are skipped entirely.
        """
        if signal.direction != SignalDirection.bullish:
            # Paper trading is long-only for now
            return False

        # Re-load portfolio to get fresh cash balance
        await self.db.refresh(portfolio)

        cash = float(portfolio.cash_balance or 0)
        if cash <= float(strategy.min_cash_reserve):
            log.info("Strategy %s: cash %.2f below reserve %.2f — skipping %s",
                     strategy.id, cash, float(strategy.min_cash_reserve), signal.ticker)
            return False

        # Compute allocation
        if strategy.allocation_mode == AllocationMode.fixed:
            target_dollars = float(strategy.allocation_value)
        else:  # percent
            target_dollars = cash * float(strategy.allocation_value) / 100.0

        usable_cash = cash - float(strategy.min_cash_reserve)
        target_dollars = min(target_dollars, usable_cash)
        if target_dollars <= 0:
            return False

        # Max-position-pct cap: total portfolio value × max_pct
        summary = await PortfolioService(self.db).get_summary(portfolio)
        total_value = float(summary.total_value or 0) + cash
        max_position_dollars = total_value * float(strategy.max_position_pct) / 100.0

        # Current exposure on this ticker
        pos_result = await self.db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio.id,
                Position.ticker == signal.ticker,
            )
        )
        existing = pos_result.scalar_one_or_none()
        current_exposure = 0.0
        if existing and existing.current_price:
            current_exposure = float(existing.current_price) * float(existing.quantity)

        available_room = max_position_dollars - current_exposure
        if available_room <= 0:
            log.info("Strategy %s: %s already at/over max-position-pct — skipping",
                     strategy.id, signal.ticker)
            return False
        target_dollars = min(target_dollars, available_room)

        # Fetch current quote to compute share qty
        from app.services.market_data import MarketDataService
        try:
            quotes = await MarketDataService().get_quotes([signal.ticker])
            if not quotes or not quotes[0].get("price"):
                log.warning("Strategy %s: no quote for %s", strategy.id, signal.ticker)
                return False
            price = float(quotes[0]["price"])
        except Exception as exc:
            log.warning("Strategy %s: quote fetch failed for %s: %s",
                        strategy.id, signal.ticker, exc)
            return False

        qty = round(target_dollars / price, 4)
        if qty <= 0:
            return False

        # Execute the paper trade — this also re-validates broker=paper
        try:
            buy_trade = await PortfolioService(self.db).execute_paper_trade(
                portfolio=portfolio,
                ticker=signal.ticker,
                action=TradeAction.buy,
                quantity=qty,
            )
        except ValueError as exc:
            log.warning("Strategy %s: paper buy failed for %s: %s",
                        strategy.id, signal.ticker, exc)
            return False

        now = datetime.now(UTC)
        planned_exit = now + timedelta(days=strategy.base_hold_days)

        strategy_trade = StrategyTrade(
            strategy_id=strategy.id,
            ticker=signal.ticker,
            direction=signal.direction,
            buy_trade_id=buy_trade.id,
            trigger_signal_id=signal.id,
            entry_price=Decimal(str(price)),
            quantity=Decimal(str(qty)),
            entry_at=now,
            planned_exit_at=planned_exit,
            status=StrategyTradeStatus.open,
        )
        self.db.add(strategy_trade)
        await self.db.flush()

        log.info(
            "Strategy %s opened: BUY %s qty=%.4f price=$%.2f, planned exit %s",
            strategy.id, signal.ticker, qty, price, planned_exit.date().isoformat(),
        )
        return True

    # ── Action: extend hold ─────────────────────────────────────────────

    @staticmethod
    def _extend_hold(strategy: Strategy, st: StrategyTrade) -> None:
        """Bump planned_exit by base_hold_days more, capped at max_hold."""
        now = datetime.now(UTC)
        new_planned = now + timedelta(days=strategy.base_hold_days)
        # Don't blow past max_hold_days
        hard_cap = st.entry_at + timedelta(days=strategy.max_hold_days)
        st.planned_exit_at = min(new_planned, hard_cap)
        log.info(
            "Strategy %s extended hold for %s — new planned exit %s",
            strategy.id, st.ticker, st.planned_exit_at.date().isoformat(),
        )

    # ── Action: close ───────────────────────────────────────────────────

    async def _close_position(
        self,
        st: StrategyTrade,
        strategy: Strategy,
        reason: StrategyExitReason,
    ) -> bool:
        """Market-sell a strategy position. Updates StrategyTrade row."""
        portfolio = await self.db.get(Portfolio, strategy.portfolio_id)
        if not portfolio or portfolio.broker != BrokerType.paper:
            log.error("Cannot close: strategy %s portfolio %s is not paper",
                      strategy.id, strategy.portfolio_id)
            return False

        try:
            sell_trade = await PortfolioService(self.db).execute_paper_trade(
                portfolio=portfolio,
                ticker=st.ticker,
                action=TradeAction.sell,
                quantity=float(st.quantity),
            )
        except ValueError as exc:
            log.warning(
                "Strategy %s: sell failed for trade %s (%s): %s",
                strategy.id, st.id, st.ticker, exc,
            )
            return False

        now = datetime.now(UTC)
        st.sell_trade_id = sell_trade.id
        st.exit_price = sell_trade.price
        st.exited_at = now
        st.status = StrategyTradeStatus.closed
        st.exit_reason = reason
        await self.db.flush()

        log.info(
            "Strategy %s closed: SELL %s qty=%.4f at $%s, reason=%s",
            strategy.id, st.ticker, float(st.quantity), sell_trade.price, reason.value,
        )
        return True

    # ── Helpers ─────────────────────────────────────────────────────────

    async def _get_open_position(self, strategy_id: int, ticker: str) -> StrategyTrade | None:
        result = await self.db.execute(
            select(StrategyTrade).where(
                and_(
                    StrategyTrade.strategy_id == strategy_id,
                    StrategyTrade.ticker == ticker,
                    StrategyTrade.status == StrategyTradeStatus.open,
                )
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _signal_matches_filter(strategy: Strategy, signal: Signal) -> bool:
        """All filters AND together. Null filter values match anything."""
        if strategy.signal_type and strategy.signal_type != signal.signal_type:
            return False
        if strategy.direction and strategy.direction != signal.direction:
            return False
        if signal.strength < strategy.min_strength:
            return False
        return True

    @staticmethod
    def _meets_strength_threshold(strategy: Strategy, signal: Signal) -> bool:
        return signal.strength >= strategy.min_strength

    @staticmethod
    def _is_opposite_direction(held: SignalDirection, incoming: SignalDirection) -> bool:
        return (
            (held == SignalDirection.bullish and incoming == SignalDirection.bearish)
            or (held == SignalDirection.bearish and incoming == SignalDirection.bullish)
        )
