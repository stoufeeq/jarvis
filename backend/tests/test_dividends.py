"""Tests for dividend income tracking.

The subtle part is entitlement: you receive a dividend only if you held
shares BEFORE the ex-date. Buying on the ex-date does not qualify, and
selling before it forfeits the payment. Getting that boundary wrong
produces income figures that look plausible but are quietly wrong —
exactly the failure mode tests exist for.

Shares-at-ex-date are reconstructed from the trade ledger rather than
stored, so back-dated imports and trade edits stay correct. Those
scenarios are covered here too.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.dividend import Dividend
from app.models.portfolio import AssetType, BrokerType, Portfolio, Position, Trade, TradeAction
from app.services.dividend import DividendService

USER_ID = 1


# ── Fixtures ──────────────────────────────────────────────────────────


async def _portfolio(db, currency: str = "USD") -> Portfolio:
    p = Portfolio(user_id=USER_ID, name="P", broker=BrokerType.manual, currency=currency)
    db.add(p)
    await db.flush()
    return p


async def _trade(
    db, portfolio, action: TradeAction, qty: float, on: date,
    *, ticker: str = "AAPL", price: float = 100, currency: str = "USD",
) -> Trade:
    t = Trade(
        portfolio_id=portfolio.id,
        ticker=ticker,
        asset_type=AssetType.stock,
        action=action,
        quantity=Decimal(str(qty)),
        price=Decimal(str(price)),
        fees=Decimal("0"),
        currency=currency,
        traded_at=datetime(on.year, on.month, on.day, tzinfo=UTC),
    )
    db.add(t)
    await db.flush()
    return t


async def _dividend(
    db, ticker: str, ex: date, per_share: float, *, currency: str = "USD",
) -> Dividend:
    d = Dividend(
        ticker=ticker,
        ex_date=ex,
        amount_per_share=Decimal(str(per_share)),
        currency=currency,
    )
    db.add(d)
    await db.flush()
    return d


async def _position(db, portfolio, ticker: str, qty: float, avg_cost: float,
                    currency: str = "USD") -> Position:
    pos = Position(
        portfolio_id=portfolio.id,
        ticker=ticker,
        quantity=Decimal(str(qty)),
        avg_cost=Decimal(str(avg_cost)),
        currency=currency,
        opened_at=datetime.now(UTC),
    )
    db.add(pos)
    await db.flush()
    return pos


def _no_forward():
    """Suppress the forward-estimate yfinance call. Tests that care about
    the forward figure patch it explicitly instead."""
    async def _stub(self, portfolio, base, fx):
        return {"annual": 0.0, "yield_on_cost_pct": None}

    return patch.object(DividendService, "_forward_estimate", _stub)


def _fx(rates: dict[str, float]):
    async def fake(self, currencies, base="USD"):
        return {c: rates[c] for c in currencies if c in rates}

    return patch("app.services.market_data.MarketDataService.get_fx_rates", fake)


# ── Entitlement boundary ──────────────────────────────────────────────


def _shares(trades, ticker, on):
    return DividendService._shares_at(trades, ticker, on)


@pytest.mark.asyncio
async def test_shares_at_counts_buys_before_the_date(db):
    p = await _portfolio(db)
    t1 = await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    assert _shares([t1], "AAPL", date(2026, 2, 1)) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_buy_on_the_ex_date_does_not_qualify(db):
    """You must hold at the close of the day BEFORE the ex-date. Buying
    on the ex-date itself means you bought ex-dividend — the seller
    keeps the payment."""
    p = await _portfolio(db)
    t1 = await _trade(db, p, TradeAction.buy, 100, date(2026, 2, 1))
    assert _shares([t1], "AAPL", date(2026, 2, 1)) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_buy_one_day_before_ex_date_qualifies(db):
    p = await _portfolio(db)
    t1 = await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 31))
    assert _shares([t1], "AAPL", date(2026, 2, 1)) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_sell_before_ex_date_forfeits(db):
    p = await _portfolio(db)
    t1 = await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    t2 = await _trade(db, p, TradeAction.sell, 100, date(2026, 1, 20))
    assert _shares([t1, t2], "AAPL", date(2026, 2, 1)) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_partial_sell_reduces_entitlement(db):
    p = await _portfolio(db)
    t1 = await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    t2 = await _trade(db, p, TradeAction.sell, 40, date(2026, 1, 20))
    assert _shares([t1, t2], "AAPL", date(2026, 2, 1)) == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_shares_at_never_goes_negative(db):
    """Bad data (a sell with no matching buy) must clamp at zero rather
    than producing negative income."""
    p = await _portfolio(db)
    t1 = await _trade(db, p, TradeAction.sell, 50, date(2026, 1, 10))
    assert _shares([t1], "AAPL", date(2026, 2, 1)) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_shares_at_isolates_tickers(db):
    p = await _portfolio(db)
    a = await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10), ticker="AAPL")
    m = await _trade(db, p, TradeAction.buy, 999, date(2026, 1, 10), ticker="MSFT")
    assert _shares([a, m], "AAPL", date(2026, 2, 1)) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_shares_at_ignores_short_and_cover(db):
    """Long-only convention, matching realised P&L. A short position
    owes the dividend rather than receiving it, which this v1 does not
    model — so it must contribute zero, not a wrong-signed amount."""
    p = await _portfolio(db)
    s = await _trade(db, p, TradeAction.short, 100, date(2026, 1, 10))
    c = await _trade(db, p, TradeAction.cover, 100, date(2026, 1, 15))
    assert _shares([s, c], "AAPL", date(2026, 2, 1)) == pytest.approx(0.0)


# ── Income computation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_income_zero_with_no_trades(db):
    p = await _portfolio(db)
    with _no_forward():
        out = await DividendService(db).compute_income(p)
    assert out["ytd"] == 0.0
    assert out["received"] == []


@pytest.mark.asyncio
async def test_income_zero_when_no_dividends_recorded(db):
    p = await _portfolio(db)
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    with _no_forward():
        out = await DividendService(db).compute_income(p)
    assert out["total_all_time"] == 0.0


@pytest.mark.asyncio
async def test_income_single_payment(db):
    p = await _portfolio(db)
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    await _dividend(db, "AAPL", date(2026, 2, 1), 0.26)

    with _no_forward():
        out = await DividendService(db).compute_income(p)

    assert out["total_all_time"] == pytest.approx(26.0)
    assert len(out["received"]) == 1
    r = out["received"][0]
    assert r["ticker"] == "AAPL"
    assert r["shares"] == pytest.approx(100.0)
    assert r["amount"] == pytest.approx(26.0)


@pytest.mark.asyncio
async def test_income_uses_shares_at_each_ex_date_not_today(db):
    """The whole point of walking the ledger: a position that grew over
    time earns different amounts per payment. Using today's holding for
    all of them would overstate the early ones."""
    p = await _portfolio(db)
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    await _dividend(db, "AAPL", date(2026, 2, 1), 1.00)   # 100 shares → 100
    await _trade(db, p, TradeAction.buy, 100, date(2026, 3, 1))
    await _dividend(db, "AAPL", date(2026, 5, 1), 1.00)   # 200 shares → 200

    with _no_forward():
        out = await DividendService(db).compute_income(p)

    assert out["total_all_time"] == pytest.approx(300.0)
    by_ex = {r["ex_date"]: r["amount"] for r in out["received"]}
    assert by_ex["2026-02-01"] == pytest.approx(100.0)
    assert by_ex["2026-05-01"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_income_excludes_payments_before_purchase(db):
    """A dividend that predates ownership must not be credited — the
    obvious bug when a ticker's full history is synced."""
    p = await _portfolio(db)
    await _dividend(db, "AAPL", date(2025, 1, 1), 5.00)   # before we owned it
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    await _dividend(db, "AAPL", date(2026, 2, 1), 0.26)

    with _no_forward():
        out = await DividendService(db).compute_income(p)

    assert out["total_all_time"] == pytest.approx(26.0)
    assert [r["ex_date"] for r in out["received"]] == ["2026-02-01"]


@pytest.mark.asyncio
async def test_income_after_full_exit_keeps_history(db):
    """Selling out doesn't erase income already earned — the history is
    what makes the total-return figure honest."""
    p = await _portfolio(db)
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10))
    await _dividend(db, "AAPL", date(2026, 2, 1), 0.26)
    await _trade(db, p, TradeAction.sell, 100, date(2026, 3, 1))

    with _no_forward():
        out = await DividendService(db).compute_income(p)

    assert out["total_all_time"] == pytest.approx(26.0)


@pytest.mark.asyncio
async def test_ytd_and_ttm_windows(db):
    """YTD counts from Jan 1 of the current year; TTM from 365 days ago.
    Anchored to today so the test doesn't rot."""
    today = datetime.now(UTC).date()
    p = await _portfolio(db)
    await _trade(db, p, TradeAction.buy, 100, today - timedelta(days=1000))

    await _dividend(db, "AAPL", today - timedelta(days=10), 1.00)    # YTD + TTM
    await _dividend(db, "AAPL", today - timedelta(days=800), 1.00)   # neither
    # 200 days back is inside TTM; inside YTD only if we haven't crossed
    # Jan 1 in between, so assert TTM and total rather than YTD here.
    await _dividend(db, "AAPL", today - timedelta(days=200), 1.00)

    with _no_forward():
        out = await DividendService(db).compute_income(p)

    assert out["total_all_time"] == pytest.approx(300.0)
    assert out["trailing_12m"] == pytest.approx(200.0)
    assert out["ytd"] >= 100.0
    assert out["ytd"] <= out["trailing_12m"]


@pytest.mark.asyncio
async def test_income_converts_foreign_currency(db):
    p = await _portfolio(db, currency="USD")
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10),
                 ticker="SHEL", currency="GBP")
    await _dividend(db, "SHEL", date(2026, 2, 1), 0.50, currency="GBP")

    with _no_forward(), _fx({"GBP": 1.25}):
        out = await DividendService(db).compute_income(p)

    # 100 × 0.50 GBP = 50 GBP → 62.50 USD
    assert out["total_all_time"] == pytest.approx(62.5)


@pytest.mark.asyncio
async def test_income_falls_back_to_1to1_when_fx_down(db):
    """An FX outage must degrade to an approximate figure rather than
    zeroing out income or raising."""
    p = await _portfolio(db, currency="USD")
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10),
                 ticker="SHEL", currency="GBP")
    await _dividend(db, "SHEL", date(2026, 2, 1), 0.50, currency="GBP")

    async def boom(self, currencies, base="USD"):
        raise RuntimeError("FX down")

    with _no_forward(), patch(
        "app.services.market_data.MarketDataService.get_fx_rates", boom
    ):
        out = await DividendService(db).compute_income(p)

    assert out["total_all_time"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_by_ticker_breakdown_sorted_descending(db):
    p = await _portfolio(db)
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10), ticker="AAPL")
    await _trade(db, p, TradeAction.buy, 100, date(2026, 1, 10), ticker="MSFT")
    await _dividend(db, "AAPL", date(2026, 2, 1), 0.10)   # 10
    await _dividend(db, "MSFT", date(2026, 2, 1), 0.80)   # 80

    with _no_forward():
        out = await DividendService(db).compute_income(p)

    assert [b["ticker"] for b in out["by_ticker"]] == ["MSFT", "AAPL"]
    assert out["by_ticker"][0]["amount"] == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_backdated_trade_changes_income_retroactively(db):
    """An IBKR import that lands older trades must be reflected without
    any re-sync — the reason income is derived rather than stored."""
    p = await _portfolio(db)
    await _dividend(db, "AAPL", date(2026, 2, 1), 1.00)

    with _no_forward():
        before = await DividendService(db).compute_income(p)
    assert before["total_all_time"] == pytest.approx(0.0)

    # Import drops in a trade predating the ex-date.
    await _trade(db, p, TradeAction.buy, 50, date(2026, 1, 5))

    with _no_forward():
        after = await DividendService(db).compute_income(p)
    assert after["total_all_time"] == pytest.approx(50.0)


# ── Upcoming ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upcoming_uses_current_holdings(db):
    """Entitlement isn't settled for a future ex-date, so today's
    position is the right basis."""
    today = datetime.now(UTC).date()
    p = await _portfolio(db)
    await _position(db, p, "AAPL", qty=150, avg_cost=100)
    await _dividend(db, "AAPL", today + timedelta(days=5), 0.26)

    out = await DividendService(db).upcoming(p)

    assert len(out) == 1
    assert out[0]["shares"] == pytest.approx(150.0)
    assert out[0]["amount"] == pytest.approx(39.0)


@pytest.mark.asyncio
async def test_upcoming_excludes_past_ex_dates(db):
    today = datetime.now(UTC).date()
    p = await _portfolio(db)
    await _position(db, p, "AAPL", qty=150, avg_cost=100)
    await _dividend(db, "AAPL", today - timedelta(days=5), 0.26)

    assert await DividendService(db).upcoming(p) == []


@pytest.mark.asyncio
async def test_upcoming_respects_horizon(db):
    today = datetime.now(UTC).date()
    p = await _portfolio(db)
    await _position(db, p, "AAPL", qty=150, avg_cost=100)
    await _dividend(db, "AAPL", today + timedelta(days=90), 0.26)

    assert await DividendService(db).upcoming(p, days_ahead=30) == []
    assert len(await DividendService(db).upcoming(p, days_ahead=120)) == 1


@pytest.mark.asyncio
async def test_upcoming_ignores_tickers_not_held(db):
    today = datetime.now(UTC).date()
    p = await _portfolio(db)
    await _position(db, p, "AAPL", qty=150, avg_cost=100)
    await _dividend(db, "MSFT", today + timedelta(days=5), 0.80)

    assert await DividendService(db).upcoming(p) == []


# ── Forward estimate ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forward_estimate_runs_real_arithmetic(db):
    """Exercises _forward_estimate itself with the yfinance rate lookup
    stubbed: 100 shares x $1.08/yr = $108 against a $5,000 cost basis
    = 2.16% yield on cost."""
    p = await _portfolio(db)
    await _position(db, p, "AAPL", qty=100, avg_cost=50)

    async def fake_to_thread(fn, *args):
        return {"AAPL": 1.08}

    with patch("asyncio.to_thread", fake_to_thread):
        out = await DividendService(db)._forward_estimate(p, "USD", {})

    assert out["annual"] == pytest.approx(108.0)
    assert out["yield_on_cost_pct"] == pytest.approx(2.16)


@pytest.mark.asyncio
async def test_forward_estimate_zero_with_no_positions(db):
    p = await _portfolio(db)
    out = await DividendService(db)._forward_estimate(p, "USD", {})
    assert out["annual"] == 0.0
    assert out["yield_on_cost_pct"] is None


@pytest.mark.asyncio
async def test_forward_estimate_survives_rate_lookup_failure(db):
    """A yfinance outage must not blank the whole dividends page."""
    p = await _portfolio(db)
    await _position(db, p, "AAPL", qty=100, avg_cost=50)

    async def boom(fn, *args):
        raise RuntimeError("yfinance down")

    with patch("asyncio.to_thread", boom):
        out = await DividendService(db)._forward_estimate(p, "USD", {})

    assert out["annual"] == 0.0
