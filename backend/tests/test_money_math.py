"""Tests for the money-math paths: realised P&L, signal-outcome snapshot
date resolution, and heatmap price reconciliation.

These are the functions whose bugs are *silent* — a wrong realised-P&L
number looks plausible, a mis-dated outcome snapshot quietly poisons
every backtest conclusion, and a stale heatmap price just looks like a
slow market. None of them throw. Hence tests.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio import AssetType, BrokerType, Portfolio, Trade, TradeAction
from app.services.heatmap import _reconcile_change
from app.services.portfolio import PortfolioService
from app.services.signal_outcome import SignalOutcomeService, _finite_or_none

USER_ID = 1


# ── Helpers ───────────────────────────────────────────────────────────


async def _mk_portfolio(db, currency: str = "USD") -> Portfolio:
    p = Portfolio(user_id=USER_ID, name="Test", broker=BrokerType.manual, currency=currency)
    db.add(p)
    await db.flush()
    return p


async def _add_trade(
    db,
    portfolio: Portfolio,
    action: TradeAction,
    qty: float,
    price: float,
    *,
    fees: float = 0,
    ticker: str = "AAPL",
    currency: str = "USD",
    days_ago: int = 0,
) -> Trade:
    t = Trade(
        portfolio_id=portfolio.id,
        ticker=ticker,
        asset_type=AssetType.stock,
        action=action,
        quantity=Decimal(str(qty)),
        price=Decimal(str(price)),
        fees=Decimal(str(fees)),
        currency=currency,
        traded_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(t)
    await db.flush()
    return t


def _fx(rates: dict[str, float]):
    async def fake_get_fx_rates(self, currencies, base="USD"):
        return {c: rates[c] for c in currencies if c in rates}

    return patch("app.services.market_data.MarketDataService.get_fx_rates", fake_get_fx_rates)


# ── Realised P&L (moving-average cost) ────────────────────────────────


@pytest.mark.asyncio
async def test_realised_pnl_zero_with_no_trades(db):
    p = await _mk_portfolio(db)
    assert await PortfolioService(db).compute_realised_pnl(p) == 0.0


@pytest.mark.asyncio
async def test_realised_pnl_zero_when_only_buys(db):
    """Unrealised gains don't count — only closed lots produce realised P&L."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 10, 100, days_ago=5)
    await _add_trade(db, p, TradeAction.buy, 10, 150, days_ago=3)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_realised_pnl_simple_round_trip(db):
    """Buy 10 @ 100, sell 10 @ 120 → 200 profit."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 10, 100, days_ago=5)
    await _add_trade(db, p, TradeAction.sell, 10, 120, days_ago=1)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_realised_pnl_includes_fees_on_both_sides(db):
    """Buy fees raise the cost basis; sell fees reduce the proceeds.
    Buy 10 @ 100 + 5 fee → avg 100.5. Sell 10 @ 120 - 5 fee → 190."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 10, 100, fees=5, days_ago=5)
    await _add_trade(db, p, TradeAction.sell, 10, 120, fees=5, days_ago=1)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(190.0)


@pytest.mark.asyncio
async def test_realised_pnl_partial_sell_uses_running_average(db):
    """Two buys at different prices then a partial sell. Avg cost of
    (10@100 + 10@200) = 150. Selling 10 @ 180 realises (180-150)*10 = 300."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 10, 100, days_ago=10)
    await _add_trade(db, p, TradeAction.buy, 10, 200, days_ago=8)
    await _add_trade(db, p, TradeAction.sell, 10, 180, days_ago=1)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_realised_pnl_average_unchanged_after_partial_sell(db):
    """Selling doesn't move the average — the second sell realises at the
    same basis as the first."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 20, 100, days_ago=10)
    await _add_trade(db, p, TradeAction.sell, 10, 120, days_ago=5)   # +200
    await _add_trade(db, p, TradeAction.sell, 10, 120, days_ago=1)   # +200
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(400.0)


@pytest.mark.asyncio
async def test_realised_pnl_records_losses(db):
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 10, 100, days_ago=5)
    await _add_trade(db, p, TradeAction.sell, 10, 80, days_ago=1)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(-200.0)


@pytest.mark.asyncio
async def test_realised_pnl_oversell_clamps_to_held_quantity(db):
    """Selling more than the book holds (bad data / missing buy) realises
    only against the quantity actually on the books — it must not
    fabricate profit on phantom shares."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 5, 100, days_ago=5)
    await _add_trade(db, p, TradeAction.sell, 100, 120, days_ago=1)
    # Only 5 shares were held → (120-100)*5 = 100, not (120-100)*100
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_realised_pnl_sell_with_no_position_is_ignored(db):
    """A sell with nothing on the books contributes zero rather than
    treating cost basis as 0 and booking the full proceeds as profit."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.sell, 10, 120, days_ago=1)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_realised_pnl_tracks_tickers_independently(db):
    """Each ticker keeps its own running average — a loss on one must not
    net against the cost basis of another."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.buy, 10, 100, ticker="AAPL", days_ago=10)
    await _add_trade(db, p, TradeAction.buy, 10, 50, ticker="MSFT", days_ago=10)
    await _add_trade(db, p, TradeAction.sell, 10, 120, ticker="AAPL", days_ago=1)  # +200
    await _add_trade(db, p, TradeAction.sell, 10, 40, ticker="MSFT", days_ago=1)   # -100
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_realised_pnl_short_and_cover_are_skipped(db):
    """Long-only realised tracking for now (documented in the service).
    Short/cover must contribute nothing rather than a wrong-signed number."""
    p = await _mk_portfolio(db)
    await _add_trade(db, p, TradeAction.short, 10, 100, days_ago=5)
    await _add_trade(db, p, TradeAction.cover, 10, 80, days_ago=1)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_realised_pnl_converts_foreign_currency_to_base(db):
    """A GBP round-trip in a USD portfolio is converted at the FX rate."""
    p = await _mk_portfolio(db, currency="USD")
    await _add_trade(db, p, TradeAction.buy, 10, 100, currency="GBP", days_ago=5)
    await _add_trade(db, p, TradeAction.sell, 10, 120, currency="GBP", days_ago=1)

    # 200 GBP profit at 1 GBP = 1.25 USD → 250 USD
    with _fx({"GBP": 1.25}):
        pnl = await PortfolioService(db).compute_realised_pnl(p)
    assert pnl == pytest.approx(250.0)


@pytest.mark.asyncio
async def test_realised_pnl_falls_back_to_1to1_when_fx_unavailable(db):
    """FX outage must not zero out or crash the realised number — we
    degrade to 1:1 and still report something usable."""
    p = await _mk_portfolio(db, currency="USD")
    await _add_trade(db, p, TradeAction.buy, 10, 100, currency="GBP", days_ago=5)
    await _add_trade(db, p, TradeAction.sell, 10, 120, currency="GBP", days_ago=1)

    async def boom(self, currencies, base="USD"):
        raise RuntimeError("FX provider down")

    with patch("app.services.market_data.MarketDataService.get_fx_rates", boom):
        pnl = await PortfolioService(db).compute_realised_pnl(p)
    assert pnl == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_realised_pnl_respects_chronological_order_not_insert_order(db):
    """Trades are walked by traded_at, so a back-dated buy inserted after
    a sell still establishes the cost basis before that sell."""
    p = await _mk_portfolio(db)
    # Inserted sell-first, but dated later than the buy.
    await _add_trade(db, p, TradeAction.sell, 10, 120, days_ago=1)
    await _add_trade(db, p, TradeAction.buy, 10, 100, days_ago=5)
    assert await PortfolioService(db).compute_realised_pnl(p) == pytest.approx(200.0)


# ── Signal outcome snapshot dating ────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (100.5, 100.5),
        (1, 1.0),
        (None, None),
        ("abc", None),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
        # Non-positive prices are data errors, not valid quotes.
        (0, None),
        (-5, None),
    ],
)
def test_finite_or_none_rejects_bad_prices(value, expected):
    assert _finite_or_none(value) == expected


def test_candle_date_handles_unix_and_string():
    """Candles arrive as unix ints (intraday) or YYYY-MM-DD strings (daily)."""
    fn = SignalOutcomeService._candle_date
    assert fn({"time": "2026-08-28"}) == "2026-08-28"
    assert fn({"time": "2026-08-28T14:30:00Z"}) == "2026-08-28"
    # 2026-08-28 00:00:00 UTC
    unix = int(datetime(2026, 8, 28, tzinfo=UTC).timestamp())
    assert fn({"time": unix}) == "2026-08-28"


def test_closest_after_exact_match():
    by_date = {"2026-08-28": {"close": 100}}
    got = SignalOutcomeService._closest_after("2026-08-28", by_date)
    assert got == {"close": 100}


def test_closest_after_skips_weekend_to_next_trading_day():
    """A snapshot due on a Saturday resolves to Monday's candle — without
    this, weekend-dated snapshots silently go missing and the backtest
    sample shrinks in a biased way."""
    by_date = {"2026-08-31": {"close": 105}}  # Monday
    got = SignalOutcomeService._closest_after("2026-08-29", by_date)  # Saturday
    assert got == {"close": 105}


def test_closest_after_gives_up_past_a_week():
    """A gap longer than a week means the ticker stopped trading (halt,
    delisting) — return None rather than reaching arbitrarily far forward
    and comparing against an unrelated price."""
    by_date = {"2026-09-15": {"close": 105}}
    assert SignalOutcomeService._closest_after("2026-08-28", by_date) is None


def test_closest_after_empty_history():
    assert SignalOutcomeService._closest_after("2026-08-28", {}) is None


# ── Heatmap price reconciliation ──────────────────────────────────────
# Regression suite for the Aug-28-2026 bug: yfinance fast_info reported
# CRM at +7.7% while the stock had actually moved +21%. Both data paths
# are consulted and the more extreme (i.e. later-captured) reading wins.


def test_reconcile_returns_none_when_both_missing():
    assert _reconcile_change(None, None) is None


def test_reconcile_uses_whichever_side_is_available():
    assert _reconcile_change(7.34, None) == 7.34
    assert _reconcile_change(None, 21.12) == 21.12


def test_reconcile_prefers_download_when_they_agree():
    """Within 1 pp the two paths are telling the same story; prefer the
    batched download since it's the more consistent feed."""
    assert _reconcile_change(2.0, 2.3) == 2.3
    assert _reconcile_change(2.3, 2.0) == 2.0


def test_reconcile_takes_more_extreme_on_disagreement():
    """The CRM case: fast_info stale at +7.34%, download fresh at +21.12%."""
    assert _reconcile_change(7.34, 21.12) == 21.12
    # And the mirror — whichever side is stale, the fresher one wins.
    assert _reconcile_change(21.12, 7.34) == 21.12


def test_reconcile_handles_bearish_extremes():
    """Magnitude, not sign, decides — a -15% crash beats a stale -2%."""
    assert _reconcile_change(-2.0, -15.0) == -15.0
    assert _reconcile_change(-15.0, -2.0) == -15.0


def test_reconcile_mixed_signs_takes_larger_magnitude():
    """Sign disagreement means one feed is badly stale; take the bigger move."""
    assert _reconcile_change(-3.0, 10.0) == 10.0
    assert _reconcile_change(10.0, -3.0) == 10.0


def test_reconcile_zero_change_is_not_treated_as_missing():
    """A genuine flat day (0.0) must not be confused with 'no data'."""
    assert _reconcile_change(0.0, None) == 0.0
    assert _reconcile_change(None, 0.0) == 0.0
    # 0.0 vs a real move: the move is more extreme, so it wins.
    assert _reconcile_change(0.0, 5.0) == 5.0
