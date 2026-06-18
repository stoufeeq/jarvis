import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class TransactionType(str, enum.Enum):
    deposit = "deposit"
    withdrawal = "withdrawal"


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    # The currency this account "thinks in". Sell-proceeds credited via
    # the chosen-account trade-cash path land here (FX-converted from the
    # trade currency when they differ). e.g. StashAway accounts hold SGD
    # → set primary_currency=SGD so US-stock sells auto-convert back.
    primary_currency: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="USD",
    )

    balances: Mapped[list["AccountBalance"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["AccountTransaction"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", order_by="AccountTransaction.transacted_at.desc()"
    )


class AccountBalance(Base):
    """Running balance per currency per account."""
    __tablename__ = "account_balances"
    __table_args__ = (UniqueConstraint("account_id", "currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0.0)

    account: Mapped["Account"] = relationship(back_populates="balances")


class AccountTransaction(TimestampMixin, Base):
    """Immutable ledger of deposits and withdrawals.

    When `trade_id` is set, this row was auto-created by the trade-cash
    wiring (debit on buy, credit on sell). The trade-cash service owns
    these rows: they must not be edited/deleted directly via the
    account-transaction API — edit/delete the trade instead and the
    wiring will reverse + recreate as needed.
    """
    __tablename__ = "account_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="transaction_type"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    transacted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Link back to the originating trade, if this txn was created by the
    # auto-settle wiring. SET NULL so a hard-deleted trade leaves the
    # txn as a normal historical record rather than vanishing it.
    trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("trades.id", ondelete="SET NULL"), nullable=True, index=True
    )

    account: Mapped["Account"] = relationship(back_populates="transactions")
