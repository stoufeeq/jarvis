"""
Trade ↔ Account cash wiring.

For real (non-paper) portfolios, every trade is auto-settled against
the user's cash accounts:

  - buy / cover  → cash OUT  (quantity * price + fees)
  - sell / short → cash IN   (quantity * price - fees)

Source-of-funds priority (debits):
  1. The trade's currency, if the user has an account holding it.
  2. Fallback chain in order: USD → SGD → EUR (skipping any tried in 1).
  3. Anything not USD/SGD/EUR is *not* tapped — reject if needed.

Multiple accounts in the same currency are drained oldest-first
(Account.created_at ASC).

Credits land in the trade's currency if any account holds it; else USD.

Each AccountTransaction created here is linked back to the trade via
`trade_id`, so update/delete can locate and reverse them deterministically.
Reversal replays each linked txn's exact amount + currency, so FX-drift
between trade time and reversal time is irrelevant.

This module is called from PortfolioService.add_trade / update_trade /
delete_trade. Paper portfolios and CSV-imported trades skip the wiring.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import (
    Account,
    AccountBalance,
    AccountTransaction,
    TransactionType,
)
from app.models.portfolio import BrokerType, Portfolio, Trade, TradeAction
from app.services.account import _txn_delta
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)

# Search order for debits, prefer-matching-currency-first per the user spec.
FALLBACK_CHAIN = ["USD", "SGD", "EUR"]


def _cash_impact(trade: Trade) -> tuple[float, bool]:
    """Return (gross_cash_amount, is_debit) for this trade.

    Debit (cash OUT) for buy/cover; credit (cash IN) for sell/short.
    Fees are added to debits, subtracted from credits.
    """
    qty = float(trade.quantity)
    price = float(trade.price)
    fees = float(trade.fees or 0)
    notional = qty * price
    if trade.action in (TradeAction.buy, TradeAction.cover):
        return (notional + fees, True)
    return (notional - fees, False)


def _label(trade: Trade) -> str:
    """Human-readable note shown in the account-transactions list."""
    return (
        f"Trade #{trade.id}: {trade.action.value} {trade.quantity} "
        f"{trade.ticker} @ {trade.price} {trade.currency}"
    )


class TradeCashService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Public lifecycle hooks ────────────────────────────────────────────

    async def on_trade_created(self, portfolio: Portfolio, trade: Trade) -> None:
        """Settle cash for a newly-created trade. No-op on paper portfolios."""
        if portfolio.broker == BrokerType.paper:
            return
        amount, is_debit = _cash_impact(trade)
        if amount <= 0:
            return
        currency = trade.currency.upper()

        # Explicit account selection bypasses the fallback chain entirely.
        if trade.account_id is not None:
            account = await self.db.get(Account, trade.account_id)
            if account is None or account.user_id != portfolio.user_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Funding account #{trade.account_id} not found or not yours",
                )
            if is_debit:
                await self._debit_chosen_account(account, trade, amount, currency)
            else:
                await self._credit_chosen_account(account, trade, amount, currency)
            return

        # Fall back to the legacy USD → SGD → EUR priority chain when no
        # account is specified (backward compatible with pre-feature trades).
        if is_debit:
            await self._debit(portfolio.user_id, trade, amount, currency)
        else:
            await self._credit(portfolio.user_id, trade, amount, currency)

    async def on_trade_updated(self, portfolio: Portfolio, trade: Trade) -> None:
        """Reverse prior cash effects of this trade, then re-apply."""
        if portfolio.broker == BrokerType.paper:
            return
        await self.reverse_for_trade(trade)
        await self.on_trade_created(portfolio, trade)

    async def reverse_for_trade(self, trade: Trade) -> None:
        """Find linked txns, replay each in reverse to undo the cash
        movement, then delete them. Raises HTTP 400 if a reversal would
        push a balance negative (i.e. credited funds have since been spent)."""
        rows = (
            await self.db.execute(
                select(AccountTransaction).where(AccountTransaction.trade_id == trade.id)
            )
        ).scalars().all()
        for txn in rows:
            await self._adjust_balance(
                txn.account_id,
                txn.currency,
                -_txn_delta(txn.transaction_type, float(txn.amount)),
            )
            balance = await self._balance_row(txn.account_id, txn.currency)
            if float(balance.balance) < 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot reverse trade cash flow: "
                        f"{txn.currency} balance on account #{txn.account_id} would go negative. "
                        f"Adjust the account before editing/deleting this trade."
                    ),
                )
            await self.db.delete(txn)
        await self.db.flush()

    # ── Internals ─────────────────────────────────────────────────────────

    async def _debit(self, user_id: int, trade: Trade, amount: float, trade_ccy: str) -> None:
        """Pull `amount` (in trade_ccy) from accounts using the priority chain.
        Creates one AccountTransaction per source-account drained."""
        order = self._currency_priority(trade_ccy)

        # Build the list of (account, currency, balance) tuples in drain order.
        accounts = await self._user_accounts(user_id)
        if not accounts:
            # Soft-skip: legacy behaviour preserved when user hasn't set up
            # any cash accounts at all. Logged but no error.
            log.info("Trade-cash: no accounts for user %s, skipping settle", user_id)
            return

        # FX rates: get one rate per non-trade-currency we might touch.
        needed_rates = {c for c in order if c != trade_ccy}
        rates = await self._fx_rates_to(trade_ccy, list(needed_rates)) if needed_rates else {}

        remaining = amount  # always tracked in trade_ccy

        # For each currency in priority order, find accounts with that
        # currency, drain oldest first.
        for ccy in order:
            if remaining <= 1e-6:
                break
            # rate = how much of trade_ccy you get per 1 unit of ccy
            rate = 1.0 if ccy == trade_ccy else rates.get(ccy)
            if rate is None or not math.isfinite(rate) or rate <= 0:
                continue

            for acct in accounts:
                if remaining <= 1e-6:
                    break
                bal = self._find_balance(acct, ccy)
                if bal is None or float(bal.balance) <= 0:
                    continue
                avail_native = float(bal.balance)
                avail_in_trade_ccy = avail_native * rate
                if avail_in_trade_ccy >= remaining:
                    # Drain just enough — round to 4 decimals to match column scale.
                    take_native = round(remaining / rate, 4)
                    take_trade = remaining
                else:
                    take_native = avail_native
                    take_trade = avail_in_trade_ccy

                await self._adjust_balance(acct.id, ccy, -take_native)
                self.db.add(AccountTransaction(
                    account_id=acct.id,
                    transaction_type=TransactionType.withdrawal,
                    amount=Decimal(str(take_native)),
                    currency=ccy,
                    notes=_label(trade) + (
                        "" if ccy == trade_ccy else f" (FX: {take_native} {ccy} ≈ {round(take_trade, 4)} {trade_ccy})"
                    ),
                    transacted_at=trade.traded_at or datetime.now(UTC),
                    trade_id=trade.id,
                ))
                remaining -= take_trade

        if remaining > 1e-2:  # tolerate sub-cent rounding
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Insufficient funds to settle this trade. "
                    f"Need {round(amount, 2)} {trade_ccy}; still short "
                    f"{round(remaining, 2)} {trade_ccy} after draining "
                    f"USD/SGD/EUR accounts (in that priority)."
                ),
            )

        await self.db.flush()

    async def _credit(self, user_id: int, trade: Trade, amount: float, trade_ccy: str) -> None:
        """Deposit `amount` into an account in trade_ccy if one exists,
        else into the oldest USD account, creating one if needed."""
        accounts = await self._user_accounts(user_id)
        if not accounts:
            log.info("Trade-cash: no accounts for user %s, skipping credit", user_id)
            return

        # Pick destination: oldest account holding trade_ccy, else oldest with USD,
        # else oldest account (and we'll create a USD balance on it).
        target = self._pick_credit_target(accounts, trade_ccy)
        target_ccy = trade_ccy
        if target is None:
            # No account holds trade_ccy; fall back to USD.
            target = self._pick_credit_target(accounts, "USD")
            target_ccy = "USD"
            if target is None:
                # No USD account either — credit to oldest account in USD anyway.
                target = accounts[0]

        # FX if we had to fall back from trade_ccy to USD.
        deposit_amount = amount
        note_extra = ""
        if target_ccy != trade_ccy:
            rates = await self._fx_rates_to(target_ccy, [trade_ccy])
            rate = rates.get(trade_ccy)
            if rate is None or not math.isfinite(rate) or rate <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not FX-convert {trade_ccy} proceeds to {target_ccy}",
                )
            deposit_amount = round(amount * rate, 4)
            note_extra = f" (FX: {round(amount, 4)} {trade_ccy} ≈ {deposit_amount} {target_ccy})"

        await self._adjust_balance(target.id, target_ccy, deposit_amount)
        self.db.add(AccountTransaction(
            account_id=target.id,
            transaction_type=TransactionType.deposit,
            amount=Decimal(str(deposit_amount)),
            currency=target_ccy,
            notes=_label(trade) + note_extra,
            transacted_at=trade.traded_at or datetime.now(UTC),
            trade_id=trade.id,
        ))
        await self.db.flush()

    # ── Chosen-account path (no cross-account chain; FX within account) ───

    async def _debit_chosen_account(
        self, account: Account, trade: Trade, amount: float, trade_ccy: str
    ) -> None:
        """Drain `amount` (in trade_ccy) from the user-chosen account.

        Tries the trade currency first; if short, FX-converts from the
        other currencies the SAME account holds, largest balance first.
        Rejects with HTTP 400 only if the account can't cover the trade
        even after exhausting every currency it holds. No cross-account
        fallback — the user picked this account.
        """
        await self.db.refresh(account, ["balances"])

        # Step 1: drain trade currency directly (no FX, 1.0 rate).
        remaining = amount
        bal_row = self._find_balance(account, trade_ccy)
        if bal_row and float(bal_row.balance) > 0:
            take = min(remaining, float(bal_row.balance))
            await self._adjust_balance(account.id, trade_ccy, -take)
            self.db.add(AccountTransaction(
                account_id=account.id,
                transaction_type=TransactionType.withdrawal,
                amount=Decimal(str(round(take, 4))),
                currency=trade_ccy,
                notes=_label(trade),
                transacted_at=trade.traded_at or datetime.now(UTC),
                trade_id=trade.id,
            ))
            remaining -= take

        if remaining <= 1e-6:
            await self.db.flush()
            return

        # Step 2: cover the shortfall via FX from the other currencies in
        # this same account, largest balance first.
        other_balances = sorted(
            (b for b in account.balances if b.currency != trade_ccy and float(b.balance) > 0),
            key=lambda b: float(b.balance),
            reverse=True,
        )
        if other_balances:
            needed_rates = {b.currency for b in other_balances}
            rates = await self._fx_rates_to(trade_ccy, list(needed_rates))

            for b in other_balances:
                if remaining <= 1e-6:
                    break
                rate = rates.get(b.currency)
                if rate is None or not math.isfinite(rate) or rate <= 0:
                    continue
                avail_native = float(b.balance)
                avail_in_trade = avail_native * rate
                if avail_in_trade >= remaining:
                    take_native = round(remaining / rate, 4)
                    take_trade = remaining
                else:
                    take_native = avail_native
                    take_trade = avail_in_trade

                await self._adjust_balance(account.id, b.currency, -take_native)
                self.db.add(AccountTransaction(
                    account_id=account.id,
                    transaction_type=TransactionType.withdrawal,
                    amount=Decimal(str(take_native)),
                    currency=b.currency,
                    notes=_label(trade) + (
                        f" (FX: {take_native} {b.currency} ≈ {round(take_trade, 4)} {trade_ccy})"
                    ),
                    transacted_at=trade.traded_at or datetime.now(UTC),
                    trade_id=trade.id,
                ))
                remaining -= take_trade

        if remaining > 1e-2:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Insufficient funds in {account.name!r}: "
                    f"need {round(amount, 2)} {trade_ccy}; still short "
                    f"{round(remaining, 2)} {trade_ccy} after draining "
                    f"every currency in this account (FX applied)."
                ),
            )

        await self.db.flush()

    async def _credit_chosen_account(
        self, account: Account, trade: Trade, amount: float, trade_ccy: str
    ) -> None:
        """Deposit sell proceeds into the chosen account's primary currency.

        If primary_currency == trade_ccy: straight deposit.
        Else: FX-convert trade_ccy → primary_currency and deposit there.
        Rejects with HTTP 400 if FX rate isn't available.
        """
        primary = (account.primary_currency or "USD").upper()
        if primary == trade_ccy:
            deposit_amount = amount
            note_extra = ""
        else:
            rates = await self._fx_rates_to(primary, [trade_ccy])
            rate = rates.get(trade_ccy)
            if rate is None or not math.isfinite(rate) or rate <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot credit proceeds: no FX rate available "
                        f"for {trade_ccy} → {primary}."
                    ),
                )
            deposit_amount = round(amount * rate, 4)
            note_extra = f" (FX: {round(amount, 4)} {trade_ccy} ≈ {deposit_amount} {primary})"

        await self._adjust_balance(account.id, primary, deposit_amount)
        self.db.add(AccountTransaction(
            account_id=account.id,
            transaction_type=TransactionType.deposit,
            amount=Decimal(str(round(deposit_amount, 4))),
            currency=primary,
            notes=_label(trade) + note_extra,
            transacted_at=trade.traded_at or datetime.now(UTC),
            trade_id=trade.id,
        ))
        await self.db.flush()

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _currency_priority(trade_ccy: str) -> list[str]:
        """Trade currency first (if it's in the chain), then the rest in
        canonical order. Non-chain trade currencies still try USD/SGD/EUR
        afterwards via FX."""
        trade_ccy = trade_ccy.upper()
        if trade_ccy in FALLBACK_CHAIN:
            return [trade_ccy] + [c for c in FALLBACK_CHAIN if c != trade_ccy]
        return [trade_ccy, *FALLBACK_CHAIN]

    async def _user_accounts(self, user_id: int) -> list[Account]:
        """Active accounts owned by the user, oldest first, with balances eagerly loaded."""
        from sqlalchemy.orm import selectinload

        result = await self.db.execute(
            select(Account)
            .where(Account.user_id == user_id, Account.is_active == True)  # noqa: E712
            .options(selectinload(Account.balances))
            .order_by(Account.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    def _find_balance(account: Account, currency: str) -> AccountBalance | None:
        for bal in account.balances:
            if bal.currency == currency:
                return bal
        return None

    @staticmethod
    def _pick_credit_target(accounts: list[Account], currency: str) -> Account | None:
        """Oldest account that already has a balance row for this currency."""
        for acct in accounts:
            if TradeCashService._find_balance(acct, currency) is not None:
                return acct
        return None

    async def _fx_rates_to(self, base: str, currencies: list[str]) -> dict[str, float]:
        """Return rate per currency such that `1 unit of CCY = rate units of base`."""
        if not currencies:
            return {}
        mds = MarketDataService()
        rates = await mds.get_fx_rates(currencies, base=base.upper())
        return {c: float(r) for c, r in rates.items() if r and math.isfinite(r)}

    async def _balance_row(self, account_id: int, currency: str) -> AccountBalance:
        result = await self.db.execute(
            select(AccountBalance).where(
                AccountBalance.account_id == account_id,
                AccountBalance.currency == currency,
            )
        )
        balance = result.scalar_one_or_none()
        if balance is None:
            balance = AccountBalance(account_id=account_id, currency=currency, balance=0.0)
            self.db.add(balance)
            await self.db.flush()
        return balance

    async def _adjust_balance(self, account_id: int, currency: str, delta: float) -> None:
        balance = await self._balance_row(account_id, currency)
        balance.balance = float(balance.balance) + delta
