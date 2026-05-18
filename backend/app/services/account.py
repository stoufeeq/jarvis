"""
Account service — manages cash accounts with multi-currency balances.
"""

import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account, AccountBalance, AccountTransaction, TransactionType
from app.schemas.account import (
    AccountCreate,
    AccountTransactionCreate,
    AccountTransactionUpdate,
    AccountUpdate,
)
from app.services.market_data import MarketDataService


def _txn_delta(t: TransactionType, amount: float) -> float:
    """How much this transaction adds (positive) or removes (negative)
    from the corresponding currency balance."""
    return amount if t == TransactionType.deposit else -amount


class AccountService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Queries ───────────────────────────────────────────────────────────────

    async def list_for_user(self, user_id: int) -> list[Account]:
        result = await self.db.execute(
            select(Account)
            .where(Account.user_id == user_id, Account.is_active == True)  # noqa: E712
            .options(selectinload(Account.balances))
            .order_by(Account.created_at)
        )
        return list(result.scalars().all())

    async def get(self, account_id: int) -> Account | None:
        result = await self.db.execute(
            select(Account)
            .where(Account.id == account_id)
            .options(
                selectinload(Account.balances),
                selectinload(Account.transactions),
            )
        )
        return result.scalar_one_or_none()

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def create(self, user_id: int, payload: AccountCreate) -> Account:
        account = Account(
            user_id=user_id,
            name=payload.name,
            description=payload.description,
        )
        self.db.add(account)
        await self.db.flush()
        return account

    async def update(self, account: Account, payload: AccountUpdate) -> Account:
        for field, value in payload.model_dump(exclude_none=True).items():
            setattr(account, field, value)
        return account

    async def delete(self, account: Account) -> None:
        account.is_active = False

    async def deposit(self, account: Account, payload: AccountTransactionCreate) -> AccountTransaction:
        currency = payload.currency.upper()
        at = payload.transacted_at or datetime.now(timezone.utc)

        # Update or create balance row
        balance = await self._get_or_create_balance(account.id, currency)
        balance.balance = float(balance.balance) + payload.amount

        txn = AccountTransaction(
            account_id=account.id,
            transaction_type=TransactionType.deposit,
            amount=payload.amount,
            currency=currency,
            notes=payload.notes,
            transacted_at=at,
        )
        self.db.add(txn)
        await self.db.flush()
        return txn

    async def withdraw(self, account: Account, payload: AccountTransactionCreate) -> AccountTransaction:
        currency = payload.currency.upper()
        at = payload.transacted_at or datetime.now(timezone.utc)

        balance = await self._get_or_create_balance(account.id, currency)
        new_balance = float(balance.balance) - payload.amount
        if new_balance < 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Insufficient {currency} balance")
        balance.balance = new_balance

        txn = AccountTransaction(
            account_id=account.id,
            transaction_type=TransactionType.withdrawal,
            amount=payload.amount,
            currency=currency,
            notes=payload.notes,
            transacted_at=at,
        )
        self.db.add(txn)
        await self.db.flush()
        return txn

    async def update_transaction(
        self,
        txn: AccountTransaction,
        payload: AccountTransactionUpdate,
    ) -> AccountTransaction:
        """Reverse the existing txn's effect on balances, apply the new
        values, then re-apply. Rejects with 400 if any affected currency
        balance would go negative."""
        from fastapi import HTTPException

        old_currency = txn.currency
        old_amount = float(txn.amount)
        old_type = txn.transaction_type

        # 1. Reverse original effect
        await self._adjust_balance(txn.account_id, old_currency, -_txn_delta(old_type, old_amount))

        # 2. Apply new values onto the txn row
        new_type = payload.transaction_type if payload.transaction_type is not None else old_type
        new_amount = payload.amount if payload.amount is not None else old_amount
        new_currency = (payload.currency or old_currency).upper()

        txn.transaction_type = new_type
        txn.amount = new_amount
        txn.currency = new_currency
        if payload.notes is not None:
            txn.notes = payload.notes
        if payload.transacted_at is not None:
            txn.transacted_at = payload.transacted_at

        # 3. Apply new effect
        await self._adjust_balance(txn.account_id, new_currency, _txn_delta(new_type, new_amount))

        # 4. Validate — both currencies if changed; otherwise just the one.
        for c in {old_currency, new_currency}:
            balance = await self._get_or_create_balance(txn.account_id, c)
            if float(balance.balance) < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Update would push {c} balance negative",
                )

        await self.db.flush()
        return txn

    async def delete_transaction(self, txn: AccountTransaction) -> None:
        """Reverse the txn's effect on the balance, then delete it.
        Rejects with 400 if removing a deposit would push the balance
        negative (i.e. the funds were already spent in a later withdrawal)."""
        from fastapi import HTTPException

        await self._adjust_balance(
            txn.account_id, txn.currency, -_txn_delta(txn.transaction_type, float(txn.amount))
        )
        balance = await self._get_or_create_balance(txn.account_id, txn.currency)
        if float(balance.balance) < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Deleting this would push {txn.currency} balance negative",
            )

        await self.db.delete(txn)
        await self.db.flush()

    # ── Liquidity aggregation ─────────────────────────────────────────────────

    async def get_liquidity(self, user_id: int) -> dict:
        """Return per-currency totals and a USD-equivalent grand total."""
        accounts = await self.list_for_user(user_id)

        # Sum balances across all accounts per currency
        totals: dict[str, float] = {}
        for acct in accounts:
            for bal in acct.balances:
                c = bal.currency.upper()
                totals[c] = totals.get(c, 0.0) + float(bal.balance)

        # Remove zero balances
        totals = {c: v for c, v in totals.items() if v != 0.0}

        # Convert everything to USD
        foreign = [c for c in totals if c != "USD"]
        rates: dict[str, float] = {}
        if foreign:
            mds = MarketDataService()
            rates = await mds.get_fx_rates(foreign, base="USD")

        total_usd = 0.0
        for currency, amount in totals.items():
            if currency == "USD":
                total_usd += amount
            else:
                rate = rates.get(currency)
                if rate and math.isfinite(rate):
                    total_usd += amount * rate

        return {"balances": totals, "total_usd": round(total_usd, 2)}

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _adjust_balance(self, account_id: int, currency: str, delta: float) -> None:
        """Add `delta` (can be negative) to (account, currency) balance.
        Caller is responsible for validating the result is non-negative."""
        balance = await self._get_or_create_balance(account_id, currency)
        balance.balance = float(balance.balance) + delta

    async def _get_or_create_balance(self, account_id: int, currency: str) -> AccountBalance:
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
