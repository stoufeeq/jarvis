"""Tests for trade ↔ account cash wiring.

This module settles every real-portfolio trade against the user's cash
accounts. Bugs here silently corrupt balances — and one already did in
production (IBKR trades draining the SRS SGD account when USD ran short,
fixed by Portfolio.allowed_account_ids). These tests lock in the rules.

FX is mocked throughout. Tests that exercise the FX paths patch
MarketDataService.get_fx_rates with fixed rates so assertions are exact;
single-currency tests avoid FX entirely (rate is hard-coded 1.0 in the
service when currencies match).
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.account import Account, AccountBalance, AccountTransaction, TransactionType
from app.models.portfolio import AssetType, BrokerType, Portfolio, Trade, TradeAction
from app.services.trade_cash import (
    TradeCashService,
    _cash_impact,
    _parse_allowed_account_ids,
)

# _currency_priority is a staticmethod on the service, not module-level.
_currency_priority = TradeCashService._currency_priority

USER_ID = 1


# ── Fixtures ──────────────────────────────────────────────────────────


async def _mk_account(
    db,
    name: str,
    balances: dict[str, float],
    *,
    primary_currency: str = "USD",
    created_at: datetime | None = None,
) -> Account:
    """Create an active account with the given per-currency balances.

    `created_at` matters: the drain order is oldest-first, so tests that
    assert which of several same-currency accounts gets hit need to pin
    creation timestamps explicitly.
    """
    acct = Account(
        user_id=USER_ID,
        name=name,
        primary_currency=primary_currency,
        is_active=True,
    )
    if created_at is not None:
        acct.created_at = created_at
        acct.updated_at = created_at
    db.add(acct)
    await db.flush()
    for ccy, amount in balances.items():
        db.add(AccountBalance(account_id=acct.id, currency=ccy, balance=amount))
    await db.flush()
    await db.refresh(acct, ["balances"])
    return acct


async def _mk_portfolio(
    db,
    *,
    broker: BrokerType = BrokerType.manual,
    allowed_account_ids: str | None = None,
) -> Portfolio:
    p = Portfolio(
        user_id=USER_ID,
        name="Test",
        broker=broker,
        currency="USD",
        allowed_account_ids=allowed_account_ids,
    )
    db.add(p)
    await db.flush()
    return p


async def _mk_trade(
    db,
    portfolio: Portfolio,
    *,
    action: TradeAction = TradeAction.buy,
    quantity: float = 10,
    price: float = 100,
    fees: float = 0,
    currency: str = "USD",
    account_id: int | None = None,
) -> Trade:
    t = Trade(
        portfolio_id=portfolio.id,
        ticker="AAPL",
        asset_type=AssetType.stock,
        action=action,
        quantity=Decimal(str(quantity)),
        price=Decimal(str(price)),
        fees=Decimal(str(fees)),
        currency=currency,
        traded_at=datetime.now(UTC),
        account_id=account_id,
    )
    db.add(t)
    await db.flush()
    return t


async def _balance_of(db, account_id: int, currency: str) -> float:
    from sqlalchemy import select

    row = await db.execute(
        select(AccountBalance).where(
            AccountBalance.account_id == account_id,
            AccountBalance.currency == currency,
        )
    )
    bal = row.scalar_one_or_none()
    return float(bal.balance) if bal else 0.0


async def _txns_for_trade(db, trade_id: int) -> list[AccountTransaction]:
    from sqlalchemy import select

    rows = await db.execute(
        select(AccountTransaction).where(AccountTransaction.trade_id == trade_id)
    )
    return list(rows.scalars().all())


def _fx(rates: dict[str, float]):
    """Patch FX lookups with fixed rates. `rates` maps CCY → units of base
    per 1 unit of CCY (matching _fx_rates_to's contract)."""

    async def fake_get_fx_rates(self, currencies, base="USD"):
        return {c: rates[c] for c in currencies if c in rates}

    return patch(
        "app.services.market_data.MarketDataService.get_fx_rates",
        fake_get_fx_rates,
    )


# ── Pure helpers ──────────────────────────────────────────────────────


def test_cash_impact_buy_adds_fees():
    """A buy costs notional PLUS fees — the broker takes commission on top."""
    t = Trade(
        action=TradeAction.buy,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fees=Decimal("1.50"),
    )
    amount, is_debit = _cash_impact(t)
    assert is_debit is True
    assert amount == pytest.approx(1001.50)


def test_cash_impact_sell_subtracts_fees():
    """A sell nets notional MINUS fees — commission comes out of proceeds."""
    t = Trade(
        action=TradeAction.sell,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fees=Decimal("1.50"),
    )
    amount, is_debit = _cash_impact(t)
    assert is_debit is False
    assert amount == pytest.approx(998.50)


def test_cash_impact_cover_is_debit_short_is_credit():
    """cover behaves like buy (cash out); short like sell (cash in)."""
    cover = Trade(action=TradeAction.cover, quantity=Decimal("5"),
                  price=Decimal("20"), fees=Decimal("0"))
    short = Trade(action=TradeAction.short, quantity=Decimal("5"),
                  price=Decimal("20"), fees=Decimal("0"))
    assert _cash_impact(cover) == (100.0, True)
    assert _cash_impact(short) == (100.0, False)


@pytest.mark.parametrize(
    "csv,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("1,2,3", {1, 2, 3}),
        ("1, 2 , 3 ", {1, 2, 3}),
        ("1,,2", {1, 2}),
        # Malformed entries are dropped, not fatal — a data glitch must not
        # break every trade attempt on the portfolio.
        ("1,abc,3", {1, 3}),
        # All-malformed degrades to "unrestricted" rather than "nothing allowed",
        # which would lock the user out of trading entirely.
        ("abc,def", None),
    ],
)
def test_parse_allowed_account_ids(csv, expected):
    assert _parse_allowed_account_ids(csv) == expected


def test_currency_priority_puts_trade_currency_first():
    assert _currency_priority("SGD") == ["SGD", "USD", "EUR"]
    assert _currency_priority("USD") == ["USD", "SGD", "EUR"]
    # Off-chain currency still tries the chain afterwards (via FX)
    assert _currency_priority("HKD") == ["HKD", "USD", "SGD", "EUR"]


# ── Buy / debit path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buy_debits_matching_currency_account(db):
    acct = await _mk_account(db, "USD Cash", {"USD": 5000})
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, action=TradeAction.buy, quantity=10, price=100, fees=1)

    await TradeCashService(db).on_trade_created(p, trade)

    assert await _balance_of(db, acct.id, "USD") == pytest.approx(3999.0)
    txns = await _txns_for_trade(db, trade.id)
    assert len(txns) == 1
    assert txns[0].transaction_type == TransactionType.withdrawal
    assert float(txns[0].amount) == pytest.approx(1001.0)


@pytest.mark.asyncio
async def test_sell_credits_account(db):
    acct = await _mk_account(db, "USD Cash", {"USD": 1000})
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, action=TradeAction.sell, quantity=10, price=100, fees=1)

    await TradeCashService(db).on_trade_created(p, trade)

    # 10 * 100 - 1 fee = 999 credited
    assert await _balance_of(db, acct.id, "USD") == pytest.approx(1999.0)
    txns = await _txns_for_trade(db, trade.id)
    assert len(txns) == 1
    assert txns[0].transaction_type == TransactionType.deposit


@pytest.mark.asyncio
async def test_buy_drains_oldest_account_first(db):
    """Same-currency accounts drain oldest-first so the user's primary
    account is used before newer/secondary ones."""
    old = await _mk_account(db, "Old USD", {"USD": 600},
                            created_at=datetime(2020, 1, 1, tzinfo=UTC))
    new = await _mk_account(db, "New USD", {"USD": 5000},
                            created_at=datetime(2024, 1, 1, tzinfo=UTC))
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100)  # needs 1000

    await TradeCashService(db).on_trade_created(p, trade)

    # Older account fully drained, remainder from the newer one.
    assert await _balance_of(db, old.id, "USD") == pytest.approx(0.0)
    assert await _balance_of(db, new.id, "USD") == pytest.approx(4600.0)
    assert len(await _txns_for_trade(db, trade.id)) == 2


@pytest.mark.asyncio
async def test_buy_insufficient_funds_raises_400(db):
    await _mk_account(db, "USD Cash", {"USD": 50})
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100)  # needs 1000

    with pytest.raises(HTTPException) as exc:
        await TradeCashService(db).on_trade_created(p, trade)
    assert exc.value.status_code == 400
    assert "Insufficient funds" in exc.value.detail


@pytest.mark.asyncio
async def test_no_accounts_is_soft_skip_not_error(db):
    """Users who haven't set up cash accounts can still record trades —
    the wiring logs and skips rather than blocking trade entry."""
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100)

    await TradeCashService(db).on_trade_created(p, trade)  # must not raise

    assert await _txns_for_trade(db, trade.id) == []


@pytest.mark.asyncio
async def test_paper_portfolio_skips_cash_wiring(db):
    """Paper portfolios track cash on the Portfolio row, not via accounts."""
    acct = await _mk_account(db, "USD Cash", {"USD": 5000})
    p = await _mk_portfolio(db, broker=BrokerType.paper)
    trade = await _mk_trade(db, p, quantity=10, price=100)

    await TradeCashService(db).on_trade_created(p, trade)

    assert await _balance_of(db, acct.id, "USD") == pytest.approx(5000.0)
    assert await _txns_for_trade(db, trade.id) == []


# ── allowed_account_ids guardrail ─────────────────────────────────────
# This is the regression suite for the production bug where IBKR trades
# quietly drained an SRS SGD account after the USD account ran short.


@pytest.mark.asyncio
async def test_allowed_list_blocks_non_whitelisted_account_in_fallback_chain(db):
    """The fallback chain must only consider whitelisted accounts. Without
    this, a short USD balance silently reaches into unrelated accounts."""
    usd = await _mk_account(db, "USD Cash", {"USD": 200},
                            created_at=datetime(2020, 1, 1, tzinfo=UTC))
    srs = await _mk_account(db, "SRS SGD", {"SGD": 100_000},
                            created_at=datetime(2021, 1, 1, tzinfo=UTC))
    # Only the USD account is allowed to fund this portfolio.
    p = await _mk_portfolio(db, allowed_account_ids=str(usd.id))
    trade = await _mk_trade(db, p, quantity=10, price=100)  # needs 1000, only 200 available

    with pytest.raises(HTTPException) as exc:
        await TradeCashService(db).on_trade_created(p, trade)
    assert exc.value.status_code == 400

    # Critically: the SRS account must be untouched.
    assert await _balance_of(db, srs.id, "SGD") == pytest.approx(100_000.0)


@pytest.mark.asyncio
async def test_allowed_list_rejects_explicit_account_not_on_list(db):
    """Explicitly picking a non-whitelisted account in the Edit UI is
    rejected with a message naming the allowed set."""
    usd = await _mk_account(db, "USD Cash", {"USD": 5000})
    srs = await _mk_account(db, "SRS SGD", {"SGD": 100_000})
    p = await _mk_portfolio(db, allowed_account_ids=str(usd.id))
    trade = await _mk_trade(db, p, quantity=1, price=10, account_id=srs.id)

    with pytest.raises(HTTPException) as exc:
        await TradeCashService(db).on_trade_created(p, trade)
    assert exc.value.status_code == 400
    assert "not on this portfolio's allowed list" in exc.value.detail
    assert await _balance_of(db, srs.id, "SGD") == pytest.approx(100_000.0)


@pytest.mark.asyncio
async def test_empty_allowed_list_means_unrestricted(db):
    """Legacy portfolios (allowed_account_ids NULL) keep the old
    unrestricted fallback behaviour."""
    usd = await _mk_account(db, "USD Cash", {"USD": 5000})
    p = await _mk_portfolio(db, allowed_account_ids=None)
    trade = await _mk_trade(db, p, quantity=10, price=100)

    await TradeCashService(db).on_trade_created(p, trade)

    assert await _balance_of(db, usd.id, "USD") == pytest.approx(4000.0)


# ── Explicit account selection ────────────────────────────────────────


@pytest.mark.asyncio
async def test_explicit_account_bypasses_fallback_chain(db):
    """When the user names an account, we drain that one only — we never
    reach into other accounts even if it can't cover the trade."""
    chosen = await _mk_account(db, "Chosen", {"USD": 5000},
                               created_at=datetime(2021, 1, 1, tzinfo=UTC))
    other = await _mk_account(db, "Other", {"USD": 99_000},
                              created_at=datetime(2020, 1, 1, tzinfo=UTC))
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100, account_id=chosen.id)

    await TradeCashService(db).on_trade_created(p, trade)

    assert await _balance_of(db, chosen.id, "USD") == pytest.approx(4000.0)
    # The older, richer account must be untouched despite being first in
    # drain order — explicit selection wins.
    assert await _balance_of(db, other.id, "USD") == pytest.approx(99_000.0)


@pytest.mark.asyncio
async def test_explicit_account_from_other_user_rejected(db):
    """Account ownership is checked — you can't fund from someone else's account."""
    foreign = Account(user_id=999, name="Not yours", primary_currency="USD", is_active=True)
    db.add(foreign)
    await db.flush()
    db.add(AccountBalance(account_id=foreign.id, currency="USD", balance=10_000))
    await db.flush()

    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=1, price=10, account_id=foreign.id)

    with pytest.raises(HTTPException) as exc:
        await TradeCashService(db).on_trade_created(p, trade)
    assert exc.value.status_code == 400
    assert "not found or not yours" in exc.value.detail


@pytest.mark.asyncio
async def test_explicit_account_insufficient_does_not_touch_others(db):
    """Shortfall on an explicitly-chosen account raises rather than
    spilling over to other accounts."""
    chosen = await _mk_account(db, "Chosen", {"USD": 100})
    other = await _mk_account(db, "Other", {"USD": 99_000})
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100, account_id=chosen.id)

    with pytest.raises(HTTPException) as exc:
        await TradeCashService(db).on_trade_created(p, trade)
    assert exc.value.status_code == 400
    assert "Insufficient funds in" in exc.value.detail
    assert await _balance_of(db, other.id, "USD") == pytest.approx(99_000.0)


@pytest.mark.asyncio
async def test_explicit_account_sell_credits_primary_currency_with_fx(db):
    """A USD sell into an SGD-primary account converts the proceeds.
    Mirrors the StashAway case in the service docstring."""
    acct = await _mk_account(db, "StashAway", {"SGD": 1000}, primary_currency="SGD")
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, action=TradeAction.sell, quantity=10,
                            price=100, currency="USD", account_id=acct.id)

    # 1 USD = 1.35 SGD
    with _fx({"USD": 1.35}):
        await TradeCashService(db).on_trade_created(p, trade)

    # 1000 USD proceeds → 1350 SGD, added to the existing 1000
    assert await _balance_of(db, acct.id, "SGD") == pytest.approx(2350.0)
    txns = await _txns_for_trade(db, trade.id)
    assert len(txns) == 1
    assert txns[0].currency == "SGD"
    assert txns[0].notes is not None and "FX:" in txns[0].notes


# ── Reversal (edit / delete) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_reverse_restores_balance_and_deletes_txns(db):
    acct = await _mk_account(db, "USD Cash", {"USD": 5000})
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100, fees=1)
    svc = TradeCashService(db)

    await svc.on_trade_created(p, trade)
    assert await _balance_of(db, acct.id, "USD") == pytest.approx(3999.0)

    await svc.reverse_for_trade(trade)

    assert await _balance_of(db, acct.id, "USD") == pytest.approx(5000.0)
    assert await _txns_for_trade(db, trade.id) == []


@pytest.mark.asyncio
async def test_reverse_replays_exact_amounts_not_current_fx(db):
    """Reversal replays each linked txn's stored amount — so FX drift
    between trade time and reversal time can't leak money."""
    acct = await _mk_account(db, "StashAway", {"SGD": 5000}, primary_currency="SGD")
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, action=TradeAction.sell, quantity=10,
                            price=100, currency="USD", account_id=acct.id)
    svc = TradeCashService(db)

    with _fx({"USD": 1.35}):
        await svc.on_trade_created(p, trade)
    assert await _balance_of(db, acct.id, "SGD") == pytest.approx(6350.0)

    # Rate moves a long way before the user edits the trade. Reversal must
    # still undo exactly 1350 SGD, not 1500.
    with _fx({"USD": 1.50}):
        await svc.reverse_for_trade(trade)

    assert await _balance_of(db, acct.id, "SGD") == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_reverse_that_would_go_negative_raises_400(db):
    """If sell proceeds have since been spent, reversing the sell would
    push the balance negative — reject rather than silently allowing it."""
    acct = await _mk_account(db, "USD Cash", {"USD": 0})
    p = await _mk_portfolio(db)
    sell = await _mk_trade(db, p, action=TradeAction.sell, quantity=10, price=100)
    svc = TradeCashService(db)

    await svc.on_trade_created(p, sell)
    assert await _balance_of(db, acct.id, "USD") == pytest.approx(1000.0)

    # Simulate the user spending the proceeds elsewhere.
    from sqlalchemy import select

    bal = (await db.execute(
        select(AccountBalance).where(AccountBalance.account_id == acct.id)
    )).scalar_one()
    bal.balance = 200.0
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await svc.reverse_for_trade(sell)
    assert exc.value.status_code == 400
    assert "would go negative" in exc.value.detail


@pytest.mark.asyncio
async def test_update_reverses_then_reapplies(db):
    """on_trade_updated must leave the balance reflecting only the NEW
    trade values, with exactly one set of linked txns."""
    acct = await _mk_account(db, "USD Cash", {"USD": 5000})
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100)
    svc = TradeCashService(db)

    await svc.on_trade_created(p, trade)
    assert await _balance_of(db, acct.id, "USD") == pytest.approx(4000.0)

    # User corrects the quantity from 10 to 4.
    trade.quantity = 4.0
    await svc.on_trade_updated(p, trade)

    assert await _balance_of(db, acct.id, "USD") == pytest.approx(4600.0)
    assert len(await _txns_for_trade(db, trade.id)) == 1


@pytest.mark.asyncio
async def test_update_with_no_net_change_is_idempotent(db):
    """Re-saving a trade without changing anything must not double-charge."""
    acct = await _mk_account(db, "USD Cash", {"USD": 5000})
    p = await _mk_portfolio(db)
    trade = await _mk_trade(db, p, quantity=10, price=100)
    svc = TradeCashService(db)

    await svc.on_trade_created(p, trade)
    await svc.on_trade_updated(p, trade)
    await svc.on_trade_updated(p, trade)

    assert await _balance_of(db, acct.id, "USD") == pytest.approx(4000.0)
    assert len(await _txns_for_trade(db, trade.id)) == 1
