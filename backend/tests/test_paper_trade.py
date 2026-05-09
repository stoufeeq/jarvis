"""Tests for paper trade execution — validates that paper portfolios stay
isolated from real ones and that cash/position accounting is correct.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio import BrokerType, Portfolio, TradeAction
from app.schemas.portfolio import PortfolioCreate
from app.services.portfolio import PortfolioService


@pytest.fixture
def mock_quote():
    """Patch MarketDataService.get_quotes to return a deterministic price."""
    with patch("app.services.portfolio.MarketDataService") as MDS:
        instance = MDS.return_value
        instance.get_quotes.return_value = [
            {"ticker": "AAPL", "price": 200.0, "previous_close": 198.0},
        ]

        async def async_get_quotes(_):
            return [{"ticker": "AAPL", "price": 200.0, "previous_close": 198.0}]

        instance.get_quotes = async_get_quotes
        yield instance


@pytest.mark.asyncio
async def test_create_paper_portfolio_defaults_initial_cash(db):
    user_id = 1
    p = await PortfolioService(db).create(user_id, PortfolioCreate(
        name="Paper",
        broker=BrokerType.paper,
        currency="USD",
    ))
    await db.commit()
    assert p.broker == BrokerType.paper
    assert float(p.initial_cash) == 100_000.0
    assert float(p.cash_balance) == 100_000.0


@pytest.mark.asyncio
async def test_create_paper_portfolio_custom_initial_cash(db):
    p = await PortfolioService(db).create(1, PortfolioCreate(
        name="Paper",
        broker=BrokerType.paper,
        currency="USD",
        initial_cash=50_000,
    ))
    await db.commit()
    assert float(p.initial_cash) == 50_000.0
    assert float(p.cash_balance) == 50_000.0


@pytest.mark.asyncio
async def test_cannot_create_two_paper_portfolios(db):
    svc = PortfolioService(db)
    await svc.create(1, PortfolioCreate(name="Paper 1", broker=BrokerType.paper))
    await db.commit()
    with pytest.raises(ValueError, match="already exists"):
        await svc.create(1, PortfolioCreate(name="Paper 2", broker=BrokerType.paper))


@pytest.mark.asyncio
async def test_paper_buy_debits_cash_and_creates_position(db, mock_quote):
    svc = PortfolioService(db)
    p = await svc.create(1, PortfolioCreate(name="Paper", broker=BrokerType.paper))
    await db.commit()

    trade = await svc.execute_paper_trade(
        portfolio=p,
        ticker="AAPL",
        action=TradeAction.buy,
        quantity=10,
    )
    await db.commit()

    # Cash debited: 10 × $200 = $2000
    await db.refresh(p)
    assert float(p.cash_balance) == 100_000.0 - 2_000.0
    assert trade.ticker == "AAPL"
    assert float(trade.quantity) == 10
    assert float(trade.price) == 200.0


@pytest.mark.asyncio
async def test_paper_buy_rejects_when_insufficient_cash(db, mock_quote):
    svc = PortfolioService(db)
    p = await svc.create(1, PortfolioCreate(
        name="Paper", broker=BrokerType.paper, initial_cash=1000,
    ))
    await db.commit()

    # Trying to buy 10 × $200 = $2000 with only $1000 cash
    with pytest.raises(ValueError, match="Insufficient cash"):
        await svc.execute_paper_trade(p, "AAPL", TradeAction.buy, quantity=10)


@pytest.mark.asyncio
async def test_paper_trade_rejects_on_non_paper_portfolio(db):
    """A regression guard: paper-trade endpoint should never write to a
    real (manual/ibkr) portfolio even if called with that portfolio ID."""
    svc = PortfolioService(db)
    real = await svc.create(1, PortfolioCreate(
        name="Real IBKR", broker=BrokerType.ibkr, currency="USD",
    ))
    await db.commit()

    with pytest.raises(ValueError, match="paper"):
        await svc.execute_paper_trade(real, "AAPL", TradeAction.buy, quantity=10)
