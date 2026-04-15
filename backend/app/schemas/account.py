from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.account import TransactionType


class AccountCreate(BaseModel):
    name: str
    description: str | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class AccountBalanceRead(BaseModel):
    id: int
    currency: str
    balance: float

    model_config = {"from_attributes": True}


class AccountTransactionCreate(BaseModel):
    amount: float
    currency: str = "USD"
    notes: str | None = None
    transacted_at: datetime | None = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.upper()


class AccountTransactionRead(BaseModel):
    id: int
    account_id: int
    transaction_type: TransactionType
    amount: float
    currency: str
    notes: str | None
    transacted_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class AccountRead(BaseModel):
    id: int
    user_id: int
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    balances: list[AccountBalanceRead] = []

    model_config = {"from_attributes": True}


class AccountDetail(AccountRead):
    transactions: list[AccountTransactionRead] = []


class LiquidityResponse(BaseModel):
    """Per-currency balances + total converted to USD."""
    balances: dict[str, float]   # { "USD": 5000, "GBP": 2000 }
    total_usd: float
