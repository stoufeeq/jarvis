"""Tests for AutoTraderService — the only code in Jarvis that can trade
without a human in the loop.

The safety property that matters most: a misconfigured strategy pointing
at a REAL portfolio must never move real positions. Every write path
checks broker == paper first; these tests pin that in place.

Beyond safety, the exit logic has a specific ordering (stop-loss bypasses
min_hold_days, max_hold beats planned) that is easy to break silently —
a wrong ordering just means positions exit on the wrong day, which looks
like normal strategy behaviour rather than a bug.

Quotes are mocked throughout so fills are deterministic.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio import BrokerType, Portfolio, Position, Trade
from app.models.signal import Signal, SignalDirection, SignalType
from app.models.strategy import (
    AllocationMode,
    Strategy,
    StrategyExitReason,
    StrategyTrade,
    StrategyTradeStatus,
)
from app.services.auto_trader import AutoTraderService

USER_ID = 1


# ── Fixtures ──────────────────────────────────────────────────────────


def _quotes(price: float, ticker: str = "AAPL"):
    """Patch quote fetching so paper fills are deterministic. Patched at
    the module where PortfolioService imports it, plus the local import
    inside _open_position."""

    async def fake_get_quotes(self, tickers):
        return [{"ticker": t, "price": price, "previous_close": price} for t in tickers]

    async def fake_get_quote(self, t):
        return {"ticker": t, "price": price, "previous_close": price}

    return patch.multiple(
        "app.services.market_data.MarketDataService",
        get_quotes=fake_get_quotes,
        get_quote=fake_get_quote,
    )


async def _mk_portfolio(
    db, *, broker: BrokerType = BrokerType.paper, cash: float = 100_000
) -> Portfolio:
    p = Portfolio(
        user_id=USER_ID,
        name="Paper",
        broker=broker,
        currency="USD",
        initial_cash=Decimal(str(cash)),
        cash_balance=Decimal(str(cash)),
    )
    db.add(p)
    await db.flush()
    return p


async def _mk_strategy(db, portfolio: Portfolio, **overrides) -> Strategy:
    defaults = dict(
        user_id=USER_ID,
        portfolio_id=portfolio.id,
        name="Test strategy",
        min_strength=4,
        allocation_mode=AllocationMode.fixed,
        allocation_value=Decimal("2000"),
        max_position_pct=Decimal("10.0"),
        min_cash_reserve=Decimal("5000"),
        min_hold_days=1,
        base_hold_days=5,
        max_hold_days=10,
        exit_on_opposite_signal=True,
        extend_on_continuing_signal=True,
        is_active=True,
    )
    defaults.update(overrides)
    s = Strategy(**defaults)
    db.add(s)
    await db.flush()
    return s


async def _mk_signal(
    db,
    *,
    ticker: str = "AAPL",
    direction: SignalDirection = SignalDirection.bullish,
    strength: int = 5,
    signal_type: SignalType = SignalType.technical,
) -> Signal:
    sig = Signal(
        ticker=ticker,
        signal_type=signal_type,
        direction=direction,
        strength=strength,
        rationale="test signal",
        created_at=datetime.now(UTC),
    )
    db.add(sig)
    await db.flush()
    return sig


async def _mk_open_trade(
    db,
    strategy: Strategy,
    *,
    ticker: str = "AAPL",
    entry_price: float = 100.0,
    quantity: float = 20.0,
    entry_days_ago: int = 0,
    planned_exit_days: int = 5,
    direction: SignalDirection = SignalDirection.bullish,
) -> StrategyTrade:
    """An open StrategyTrade plus the backing Position, so stop-loss
    checks (which read Position.current_price) have something to read."""
    now = datetime.now(UTC)
    entry_at = now - timedelta(days=entry_days_ago)

    buy = Trade(
        portfolio_id=strategy.portfolio_id,
        ticker=ticker,
        action="buy",
        quantity=Decimal(str(quantity)),
        price=Decimal(str(entry_price)),
        fees=Decimal("0"),
        currency="USD",
        traded_at=entry_at,
    )
    db.add(buy)
    await db.flush()

    st = StrategyTrade(
        strategy_id=strategy.id,
        ticker=ticker,
        direction=direction,
        buy_trade_id=buy.id,
        entry_price=Decimal(str(entry_price)),
        quantity=Decimal(str(quantity)),
        entry_at=entry_at,
        planned_exit_at=entry_at + timedelta(days=planned_exit_days),
        status=StrategyTradeStatus.open,
    )
    db.add(st)
    await db.flush()
    return st


async def _mk_position(
    db, portfolio: Portfolio, ticker: str, qty: float, avg_cost: float, current: float
) -> Position:
    pos = Position(
        portfolio_id=portfolio.id,
        ticker=ticker,
        quantity=Decimal(str(qty)),
        avg_cost=Decimal(str(avg_cost)),
        current_price=Decimal(str(current)),
        currency="USD",
        opened_at=datetime.now(UTC),
    )
    db.add(pos)
    await db.flush()
    return pos


async def _reload(db, st: StrategyTrade) -> StrategyTrade:
    await db.refresh(st)
    return st


# ── Safety: real portfolios are untouchable ───────────────────────────
#
# Where this invariant actually lives: PortfolioService.execute_paper_trade
# raises ValueError when portfolio.broker != paper, and EVERY write in this
# service funnels through it. The broker checks inside AutoTraderService are
# early-exit + logging on top of that.
#
# Verified by mutation: removing either AutoTraderService guard leaves the
# tests below green (the inner guard still blocks the write), while removing
# the execute_paper_trade guard turns them red. So that check is the one to
# preserve — it is pinned directly by
# test_execute_paper_trade_rejects_real_portfolio.


@pytest.mark.asyncio
async def test_execute_paper_trade_rejects_real_portfolio(db):
    """The load-bearing guard. Every auto-trader write path goes through
    execute_paper_trade, so this single check is what actually makes real
    portfolios unreachable."""
    from app.models.portfolio import TradeAction
    from app.services.portfolio import PortfolioService

    real = await _mk_portfolio(db, broker=BrokerType.manual, cash=100_000)

    with _quotes(100.0):
        with pytest.raises(ValueError, match="only be executed on paper"):
            await PortfolioService(db).execute_paper_trade(
                portfolio=real, ticker="AAPL",
                action=TradeAction.buy, quantity=10,
            )



@pytest.mark.asyncio
async def test_process_signals_skips_strategy_on_real_portfolio(db):
    """The headline safety guarantee. A strategy misconfigured to point at
    a real (manual/ibkr) portfolio must leave it completely untouched.

    Asserts portfolio STATE rather than the return code: there is
    defence-in-depth here (execute_paper_trade re-validates the broker
    too), so a return of 0 alone would still pass if the outer guard
    were removed. What must hold regardless of which layer catches it
    is that no trade, no position, and no cash movement occurred.
    """
    from sqlalchemy import select

    real = await _mk_portfolio(db, broker=BrokerType.manual, cash=100_000)
    strat = await _mk_strategy(db, real, max_position_pct=Decimal("100"))
    sig = await _mk_signal(db)
    cash_before = float(real.cash_balance)

    with _quotes(100.0):
        result = await AutoTraderService(db).process_new_signals([sig.id])

    assert result == {"opened": 0, "extended": 0, "closed": 0}
    assert (await db.execute(select(Trade))).scalars().all() == []
    assert (await db.execute(select(Position))).scalars().all() == []
    assert (await db.execute(select(StrategyTrade))).scalars().all() == []
    await db.refresh(real)
    assert float(real.cash_balance) == pytest.approx(cash_before)


@pytest.mark.asyncio
async def test_close_position_refuses_on_real_portfolio(db):
    """Even with an open StrategyTrade already on the books, closing must
    not sell out of a real portfolio.

    Like the open path, asserts state: the position must still exist at
    full size and no sell Trade may be written. Checking only the return
    value would pass even with the guard removed, because
    execute_paper_trade rejects it one layer down.
    """
    from sqlalchemy import select

    real = await _mk_portfolio(db, broker=BrokerType.manual, cash=100_000)
    strat = await _mk_strategy(db, real)
    st = await _mk_open_trade(db, strat, quantity=20)
    pos = await _mk_position(db, real, "AAPL", qty=20, avg_cost=100, current=100)
    cash_before = float(real.cash_balance)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._close_position(
            st, strat, StrategyExitReason.manual
        )

    assert ok is False
    assert (await _reload(db, st)).status == StrategyTradeStatus.open
    # The holding must survive intact — no partial or full liquidation.
    await db.refresh(pos)
    assert float(pos.quantity) == pytest.approx(20.0)
    sells = [t for t in (await db.execute(select(Trade))).scalars().all()
             if str(t.action).endswith("sell")]
    assert sells == []
    await db.refresh(real)
    assert float(real.cash_balance) == pytest.approx(cash_before)


@pytest.mark.asyncio
async def test_inactive_strategies_are_ignored(db):
    p = await _mk_portfolio(db)
    await _mk_strategy(db, p, is_active=False)
    sig = await _mk_signal(db)

    with _quotes(100.0):
        result = await AutoTraderService(db).process_new_signals([sig.id])

    assert result["opened"] == 0


@pytest.mark.asyncio
async def test_empty_signal_list_is_a_noop(db):
    assert await AutoTraderService(db).process_new_signals([]) == {
        "opened": 0, "extended": 0, "closed": 0,
    }


# ── Signal filtering ──────────────────────────────────────────────────


def test_signal_matches_filter_null_filters_match_anything():
    """Unset signal_type/direction on a strategy mean 'any'."""
    strat = Strategy(signal_type=None, direction=None, min_strength=1)
    sig = Signal(signal_type=SignalType.insider, direction=SignalDirection.bearish, strength=3)
    assert AutoTraderService._signal_matches_filter(strat, sig) is True


def test_signal_matches_filter_rejects_wrong_type():
    strat = Strategy(signal_type=SignalType.technical, direction=None, min_strength=1)
    sig = Signal(signal_type=SignalType.insider, direction=SignalDirection.bullish, strength=5)
    assert AutoTraderService._signal_matches_filter(strat, sig) is False


def test_signal_matches_filter_rejects_wrong_direction():
    strat = Strategy(signal_type=None, direction=SignalDirection.bullish, min_strength=1)
    sig = Signal(signal_type=SignalType.technical, direction=SignalDirection.bearish, strength=5)
    assert AutoTraderService._signal_matches_filter(strat, sig) is False


def test_signal_matches_filter_rejects_below_min_strength():
    strat = Strategy(signal_type=None, direction=None, min_strength=4)
    sig = Signal(signal_type=SignalType.technical, direction=SignalDirection.bullish, strength=3)
    assert AutoTraderService._signal_matches_filter(strat, sig) is False
    # Boundary: equal to threshold passes
    sig.strength = 4
    assert AutoTraderService._signal_matches_filter(strat, sig) is True


def test_is_opposite_direction():
    fn = AutoTraderService._is_opposite_direction
    assert fn(SignalDirection.bullish, SignalDirection.bearish) is True
    assert fn(SignalDirection.bearish, SignalDirection.bullish) is True
    assert fn(SignalDirection.bullish, SignalDirection.bullish) is False
    # Neutral is not "opposite" to anything — it shouldn't force an exit.
    assert fn(SignalDirection.bullish, SignalDirection.neutral) is False
    assert fn(SignalDirection.neutral, SignalDirection.bearish) is False


# ── Opening positions ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bearish_signal_does_not_open_position(db):
    """Paper trading is long-only — bearish signals must not open a short."""
    p = await _mk_portfolio(db)
    strat = await _mk_strategy(db, p)
    sig = await _mk_signal(db, direction=SignalDirection.bearish)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is False


@pytest.mark.asyncio
async def test_neutral_signal_does_not_open_position(db):
    p = await _mk_portfolio(db)
    strat = await _mk_strategy(db, p)
    sig = await _mk_signal(db, direction=SignalDirection.neutral)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is False


@pytest.mark.asyncio
async def test_open_blocked_when_cash_at_or_below_reserve(db):
    """min_cash_reserve is a hard floor — the strategy must not dip into it."""
    p = await _mk_portfolio(db, cash=5000)
    strat = await _mk_strategy(db, p, min_cash_reserve=Decimal("5000"))
    sig = await _mk_signal(db)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is False


@pytest.mark.asyncio
async def test_open_allocates_fixed_dollar_amount(db):
    """fixed mode: allocation_value is a dollar figure, so qty = value/price."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(
        db, p,
        allocation_mode=AllocationMode.fixed,
        allocation_value=Decimal("2000"),
        max_position_pct=Decimal("100"),
    )
    sig = await _mk_signal(db)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is True
    st = (await db.execute(
        __import__("sqlalchemy").select(StrategyTrade)
    )).scalars().first()
    assert st is not None
    assert float(st.quantity) == pytest.approx(20.0)  # $2000 / $100
    assert float(st.entry_price) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_open_allocates_percent_of_cash(db):
    """percent mode: allocation_value is a % of available cash."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(
        db, p,
        allocation_mode=AllocationMode.percent,
        allocation_value=Decimal("10"),   # 10% of 100k = 10k
        max_position_pct=Decimal("100"),
        min_cash_reserve=Decimal("0"),
    )
    sig = await _mk_signal(db)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is True
    st = (await db.execute(
        __import__("sqlalchemy").select(StrategyTrade)
    )).scalars().first()
    assert float(st.quantity) == pytest.approx(100.0)  # $10,000 / $100


@pytest.mark.asyncio
async def test_open_caps_allocation_at_usable_cash(db):
    """A fixed allocation larger than (cash - reserve) is trimmed to fit
    rather than overdrawing the reserve."""
    p = await _mk_portfolio(db, cash=10_000)
    strat = await _mk_strategy(
        db, p,
        allocation_value=Decimal("50000"),     # way more than we have
        min_cash_reserve=Decimal("4000"),      # usable = 6000
        max_position_pct=Decimal("100"),
    )
    sig = await _mk_signal(db)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is True
    st = (await db.execute(
        __import__("sqlalchemy").select(StrategyTrade)
    )).scalars().first()
    # 6000 usable / 100 = 60 shares, not 500
    assert float(st.quantity) == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_open_blocked_when_already_at_max_position_pct(db):
    """Existing exposure counts against the per-ticker cap, so a strategy
    can't keep pyramiding into one name on repeated signals."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(
        db, p,
        max_position_pct=Decimal("5"),   # 5% cap
        min_cash_reserve=Decimal("0"),
    )
    # Already holding $50k of AAPL — way past 5% of the ~100k book.
    await _mk_position(db, p, "AAPL", qty=500, avg_cost=100, current=100)
    sig = await _mk_signal(db)

    with _quotes(100.0):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is False
    # Nothing may be written — asserting state, not just the return code,
    # because a negative available_room would also be rejected further
    # down by the qty<=0 check.
    from sqlalchemy import select
    assert (await db.execute(select(StrategyTrade))).scalars().all() == []


@pytest.mark.asyncio
async def test_open_returns_false_when_quote_unavailable(db):
    """No price → no fill. Must not open at a fabricated price."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, max_position_pct=Decimal("100"))
    sig = await _mk_signal(db)

    async def no_quotes(self, tickers):
        return []

    with patch("app.services.market_data.MarketDataService.get_quotes", no_quotes):
        ok = await AutoTraderService(db)._open_position(strat, p, sig)

    assert ok is False


@pytest.mark.asyncio
async def test_open_snapshots_trigger_signal_fields(db):
    """Signals get deleted and rewritten every scan, NULLing the FK. The
    snapshot columns are the only durable record of what drove a trade —
    without them, post-hoc analysis is blind."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, max_position_pct=Decimal("100"))
    sig = await _mk_signal(db, strength=5, signal_type=SignalType.insider)

    with _quotes(100.0):
        await AutoTraderService(db)._open_position(strat, p, sig)

    st = (await db.execute(
        __import__("sqlalchemy").select(StrategyTrade)
    )).scalars().first()
    assert st.trigger_signal_type == SignalType.insider
    assert st.trigger_signal_strength == 5
    assert st.trigger_signal_rationale == "test signal"


@pytest.mark.asyncio
async def test_open_sets_planned_exit_from_base_hold_days(db):
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, base_hold_days=7, max_position_pct=Decimal("100"))
    sig = await _mk_signal(db)

    with _quotes(100.0):
        await AutoTraderService(db)._open_position(strat, p, sig)

    st = (await db.execute(
        __import__("sqlalchemy").select(StrategyTrade)
    )).scalars().first()
    delta = st.planned_exit_at - st.entry_at
    assert 6.9 < delta.total_seconds() / 86400 < 7.1


# ── Stop-loss ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_loss_not_configured_never_fires(db):
    p = await _mk_portfolio(db)
    strat = await _mk_strategy(db, p, stop_loss_pct=None)
    st = await _mk_open_trade(db, strat, entry_price=100)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=10)  # -90%

    fired = await AutoTraderService(db)._check_and_stop_loss(st, strat)
    assert fired is False


@pytest.mark.asyncio
async def test_stop_loss_fires_when_breached(db):
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, stop_loss_pct=Decimal("-10"))
    st = await _mk_open_trade(db, strat, entry_price=100, quantity=20)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=85)  # -15%

    with _quotes(85.0):
        fired = await AutoTraderService(db)._check_and_stop_loss(st, strat)

    assert fired is True
    st = await _reload(db, st)
    assert st.status == StrategyTradeStatus.closed
    assert st.exit_reason == StrategyExitReason.stop_loss


@pytest.mark.asyncio
async def test_stop_loss_does_not_fire_above_threshold(db):
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, stop_loss_pct=Decimal("-10"))
    st = await _mk_open_trade(db, strat, entry_price=100, quantity=20)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=95)  # -5%

    with _quotes(95.0):
        fired = await AutoTraderService(db)._check_and_stop_loss(st, strat)

    assert fired is False
    assert (await _reload(db, st)).status == StrategyTradeStatus.open


@pytest.mark.asyncio
async def test_stop_loss_fires_exactly_at_threshold(db):
    """Boundary: the check is <=, so exactly -10% triggers."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, stop_loss_pct=Decimal("-10"))
    st = await _mk_open_trade(db, strat, entry_price=100, quantity=20)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=90)  # exactly -10%

    with _quotes(90.0):
        fired = await AutoTraderService(db)._check_and_stop_loss(st, strat)

    assert fired is True


@pytest.mark.asyncio
async def test_stop_loss_skipped_when_no_cached_price(db):
    """No Position row (or no cached price) → can't evaluate, don't guess."""
    p = await _mk_portfolio(db)
    strat = await _mk_strategy(db, p, stop_loss_pct=Decimal("-10"))
    st = await _mk_open_trade(db, strat, entry_price=100)
    # Deliberately no Position row created.

    assert await AutoTraderService(db)._check_and_stop_loss(st, strat) is False


# ── Exit sweep ordering ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_loss_bypasses_min_hold_days(db):
    """The whole point of a stop is to cap a loss — holding to min_hold
    just to satisfy the minimum would defeat it. Day-0 breach must exit."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(
        db, p, stop_loss_pct=Decimal("-10"), min_hold_days=5,
    )
    st = await _mk_open_trade(db, strat, entry_price=100, quantity=20, entry_days_ago=0)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=80)  # -20%

    with _quotes(80.0):
        result = await AutoTraderService(db).daily_exit_sweep()

    assert result["stop_loss_closed"] == 1
    assert (await _reload(db, st)).exit_reason == StrategyExitReason.stop_loss


@pytest.mark.asyncio
async def test_min_hold_days_blocks_planned_exit(db):
    """A planned exit that comes due before min_hold_days is deferred."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, min_hold_days=5, max_hold_days=30)
    # Entered 2 days ago with a planned exit 1 day after entry (already past).
    st = await _mk_open_trade(db, strat, entry_days_ago=2, planned_exit_days=1)

    with _quotes(100.0):
        result = await AutoTraderService(db).daily_exit_sweep()

    assert result["planned_closed"] == 0
    assert (await _reload(db, st)).status == StrategyTradeStatus.open


@pytest.mark.asyncio
async def test_planned_exit_closes_once_past_min_hold(db):
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, min_hold_days=1, max_hold_days=30)
    st = await _mk_open_trade(db, strat, entry_days_ago=6, planned_exit_days=5)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=110)

    with _quotes(110.0):
        result = await AutoTraderService(db).daily_exit_sweep()

    assert result["planned_closed"] == 1
    st = await _reload(db, st)
    assert st.status == StrategyTradeStatus.closed
    assert st.exit_reason == StrategyExitReason.planned


@pytest.mark.asyncio
async def test_max_hold_days_forces_exit_even_with_future_planned_exit(db):
    """max_hold is a hard ceiling — it must win over a planned exit that
    hasn't come due, otherwise a position could be extended indefinitely."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, min_hold_days=1, max_hold_days=10)
    # 12 days old, but planned exit is still 20 days out (extended repeatedly).
    st = await _mk_open_trade(db, strat, entry_days_ago=12, planned_exit_days=32)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=110)

    with _quotes(110.0):
        result = await AutoTraderService(db).daily_exit_sweep()

    assert result["max_hold_closed"] == 1
    assert (await _reload(db, st)).exit_reason == StrategyExitReason.max_hold


@pytest.mark.asyncio
async def test_sweep_leaves_healthy_position_open(db):
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, min_hold_days=1, max_hold_days=30)
    st = await _mk_open_trade(db, strat, entry_days_ago=2, planned_exit_days=10)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=105)

    with _quotes(105.0):
        result = await AutoTraderService(db).daily_exit_sweep()

    assert result == {
        "stop_loss_closed": 0, "planned_closed": 0,
        "max_hold_closed": 0, "errors": 0,
    }
    assert (await _reload(db, st)).status == StrategyTradeStatus.open


@pytest.mark.asyncio
async def test_stop_loss_sweep_only_checks_stops(db):
    """The frequent sweep must ignore planned/max-hold logic — otherwise
    running it every 15 min would exit positions on the wrong schedule."""
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p, min_hold_days=1, max_hold_days=10,
                               stop_loss_pct=Decimal("-10"))
    # Well past max_hold, but only slightly down — stop not breached.
    st = await _mk_open_trade(db, strat, entry_days_ago=30, planned_exit_days=1)
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=98)

    with _quotes(98.0):
        result = await AutoTraderService(db).stop_loss_sweep()

    assert result == {"stop_loss_closed": 0}
    assert (await _reload(db, st)).status == StrategyTradeStatus.open


# ── Panic close ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_panic_close_all_closes_every_open_position(db):
    p = await _mk_portfolio(db, cash=100_000)
    strat = await _mk_strategy(db, p)
    a = await _mk_open_trade(db, strat, ticker="AAPL")
    b = await _mk_open_trade(db, strat, ticker="MSFT")
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=100)
    await _mk_position(db, p, "MSFT", qty=20, avg_cost=100, current=100)

    with _quotes(100.0):
        closed = await AutoTraderService(db).panic_close_all(strat.id)

    assert closed == 2
    for st in (a, b):
        st = await _reload(db, st)
        assert st.status == StrategyTradeStatus.closed
        assert st.exit_reason == StrategyExitReason.panic_close


@pytest.mark.asyncio
async def test_panic_close_ignores_other_strategies(db):
    p = await _mk_portfolio(db, cash=100_000)
    mine = await _mk_strategy(db, p, name="Mine")
    theirs = await _mk_strategy(db, p, name="Theirs")
    a = await _mk_open_trade(db, mine, ticker="AAPL")
    b = await _mk_open_trade(db, theirs, ticker="MSFT")
    await _mk_position(db, p, "AAPL", qty=20, avg_cost=100, current=100)
    await _mk_position(db, p, "MSFT", qty=20, avg_cost=100, current=100)

    with _quotes(100.0):
        closed = await AutoTraderService(db).panic_close_all(mine.id)

    assert closed == 1
    assert (await _reload(db, b)).status == StrategyTradeStatus.open
