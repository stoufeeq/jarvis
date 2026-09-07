"""Tests for paper-trade currency handling.

Regression suite for a bug that made every non-USD paper position wrong.
execute_paper_trade stamped each trade with the PORTFOLIO's currency
regardless of where the instrument actually trades, so a EUR-quoted line
like MBG.DE recorded a €45 fill as "$45" and debited $45 of virtual
cash. Paper P&L on any foreign name was off by the FX rate — roughly 8%
for EUR — and it compounded silently across every trade a strategy made.

The fix records the trade at its NATIVE price and currency, matching how
real trades are stored, and converts only for the single-scalar cash
ledger. These tests pin both halves: the trade must stay native, and the
cash must move in portfolio currency.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio import AssetType, BrokerType, Portfolio, Position, TradeAction
from app.schemas.portfolio import PortfolioCreate
from app.services.portfolio import PortfolioService

USER_ID = 1


def _market(price: float, currency: str, fx: dict[str, float] | None = None):
    """Stub quotes, the currency lookup, and FX in one context manager."""
    async def fake_quotes(self, tickers):
        return [{"ticker": t, "price": price, "previous_close": price} for t in tickers]

    async def fake_currency(self, ticker):
        return {"ticker": ticker, "currency": currency}

    async def fake_fx(self, currencies, base="USD"):
        return {c: (fx or {}).get(c) for c in currencies if (fx or {}).get(c)}

    return patch.multiple(
        "app.services.market_data.MarketDataService",
        get_quotes=fake_quotes,
        get_currency=fake_currency,
        get_fx_rates=fake_fx,
    )


async def _paper(db, cash: float = 100_000, currency: str = "USD") -> Portfolio:
    return await PortfolioService(db).create(USER_ID, PortfolioCreate(
        name="Paper", broker=BrokerType.paper, currency=currency, initial_cash=cash,
    ))


# ── Same-currency path (unchanged behaviour) ──────────────────────────


@pytest.mark.asyncio
async def test_usd_instrument_in_usd_account_is_unconverted(db):
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=200.0, currency="USD"):
        t = await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="AAPL", action=TradeAction.buy, quantity=10,
        )

    assert t.currency == "USD"
    assert float(t.price) == pytest.approx(200.0)
    # 10 × $200 = $2,000 out of $100,000
    assert float(p.cash_balance) == pytest.approx(98_000.0)
    assert "FX" not in (t.notes or "")


# ── Foreign instrument: the actual bug ────────────────────────────────


@pytest.mark.asyncio
async def test_eur_instrument_records_native_price_and_currency(db):
    """The trade row must reflect what actually happened on Xetra: a €45
    fill, in EUR. Stamping it USD was the original defect."""
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=45.0, currency="EUR", fx={"EUR": 1.10}):
        t = await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
        )

    assert t.currency == "EUR"
    assert float(t.price) == pytest.approx(45.0)


@pytest.mark.asyncio
async def test_eur_instrument_debits_cash_in_portfolio_currency(db):
    """Cash is a single USD scalar, so the €450 notional must leave the
    account as $495 at a 1.10 rate — not $450."""
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=45.0, currency="EUR", fx={"EUR": 1.10}):
        await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
        )

    # 10 × €45 = €450 → $495
    assert float(p.cash_balance) == pytest.approx(100_000 - 495.0)


@pytest.mark.asyncio
async def test_the_old_bug_would_have_debited_the_unconverted_amount(db):
    """Explicitly pins the difference the fix makes: the pre-fix code
    would have taken $450 rather than $495."""
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=45.0, currency="EUR", fx={"EUR": 1.10}):
        await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
        )

    spent = 100_000 - float(p.cash_balance)
    assert spent == pytest.approx(495.0)
    assert spent != pytest.approx(450.0)   # the old, wrong figure


@pytest.mark.asyncio
async def test_fx_rate_recorded_in_the_note(db):
    """The rate used is part of the audit trail — without it a later
    reader cannot reconcile the native price against the cash movement."""
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=45.0, currency="EUR", fx={"EUR": 1.10}):
        t = await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
        )

    assert "FX" in (t.notes or "")
    assert "EUR/USD" in (t.notes or "")
    assert "1.10" in (t.notes or "")


@pytest.mark.asyncio
async def test_sell_credits_converted_proceeds(db):
    p = await _paper(db, cash=100_000, currency="USD")
    svc = PortfolioService(db)

    with _market(price=45.0, currency="EUR", fx={"EUR": 1.10}):
        await svc.execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
        )
        cash_after_buy = float(p.cash_balance)
        await svc.execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.sell, quantity=10,
        )

    # Round trip at an unchanged price returns exactly what it cost.
    assert float(p.cash_balance) == pytest.approx(cash_after_buy + 495.0)
    assert float(p.cash_balance) == pytest.approx(100_000.0)


@pytest.mark.asyncio
async def test_position_carries_the_native_currency(db):
    """Downstream FX conversion in the portfolio summary depends on the
    position knowing its real currency."""
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=45.0, currency="EUR", fx={"EUR": 1.10}):
        await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
        )

    from sqlalchemy import select
    pos = (await db.execute(select(Position))).scalars().one()
    assert pos.currency == "EUR"
    assert float(pos.avg_cost) == pytest.approx(45.0)


# ── Non-USD account ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usd_instrument_in_eur_account_converts(db):
    """Conversion has to work in both directions, not just into USD."""
    p = await _paper(db, cash=100_000, currency="EUR")

    with _market(price=200.0, currency="USD", fx={"USD": 0.91}):
        t = await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="AAPL", action=TradeAction.buy, quantity=10,
        )

    assert t.currency == "USD"
    assert float(t.price) == pytest.approx(200.0)
    # 10 × $200 = $2,000 → €1,820
    assert float(p.cash_balance) == pytest.approx(100_000 - 1_820.0)


@pytest.mark.asyncio
async def test_matching_foreign_currencies_need_no_conversion(db):
    """A EUR instrument in a EUR account must not touch FX at all."""
    p = await _paper(db, cash=100_000, currency="EUR")

    with _market(price=45.0, currency="EUR"):
        t = await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
        )

    assert t.currency == "EUR"
    assert float(p.cash_balance) == pytest.approx(100_000 - 450.0)
    assert "FX" not in (t.notes or "")


# ── Cash sufficiency uses the converted figure ────────────────────────


@pytest.mark.asyncio
async def test_insufficient_cash_measured_after_conversion(db):
    """A €450 buy costs $495. An account holding $470 can afford the
    unconverted figure but not the real one, and must be rejected."""
    p = await _paper(db, cash=470, currency="USD")

    with _market(price=45.0, currency="EUR", fx={"EUR": 1.10}):
        with pytest.raises(ValueError, match="Insufficient cash"):
            await PortfolioService(db).execute_paper_trade(
                portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
            )

    assert float(p.cash_balance) == pytest.approx(470.0)


# ── Failure handling ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_fx_rate_rejects_rather_than_guessing(db):
    """Silently assuming 1:1 is what produced the original bug. With no
    rate available the trade must fail loudly."""
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=45.0, currency="EUR", fx={}):     # no EUR rate
        with pytest.raises(ValueError, match="no FX rate available"):
            await PortfolioService(db).execute_paper_trade(
                portfolio=p, ticker="MBG.DE", action=TradeAction.buy, quantity=10,
            )

    assert float(p.cash_balance) == pytest.approx(100_000.0)


@pytest.mark.asyncio
async def test_currency_lookup_failure_falls_back_to_portfolio_currency(db):
    """A provider hiccup on the currency lookup shouldn't block trading —
    degrade to the old assumption and log, rather than failing."""
    p = await _paper(db, cash=100_000, currency="USD")

    async def fake_quotes(self, tickers):
        return [{"ticker": t, "price": 200.0, "previous_close": 200.0} for t in tickers]

    async def boom(self, ticker):
        raise RuntimeError("provider down")

    with patch.multiple(
        "app.services.market_data.MarketDataService",
        get_quotes=fake_quotes, get_currency=boom,
    ):
        t = await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="AAPL", action=TradeAction.buy, quantity=10,
        )

    assert t.currency == "USD"
    assert float(p.cash_balance) == pytest.approx(98_000.0)


@pytest.mark.asyncio
async def test_crypto_is_always_usd_without_a_lookup(db):
    """CoinGecko quotes crypto in USD, so no provider call is needed."""
    p = await _paper(db, cash=100_000, currency="USD")

    with _market(price=60_000.0, currency="EUR"):   # would be ignored for crypto
        t = await PortfolioService(db).execute_paper_trade(
            portfolio=p, ticker="BTC", action=TradeAction.buy, quantity=1,
        )

    assert t.currency == "USD"
    assert t.asset_type == AssetType.crypto
    assert float(p.cash_balance) == pytest.approx(40_000.0)
