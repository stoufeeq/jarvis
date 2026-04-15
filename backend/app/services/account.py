"""
Account service — manages cash accounts with multi-currency balances.
"""

import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account, AccountBalance, AccountTransaction, TransactionType
from app.schemas.account import AccountCreate, AccountTransactionCreate, AccountUpdate
from app.services.market_data import MarketDataService


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
