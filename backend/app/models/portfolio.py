import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class BrokerType(str, enum.Enum):
    manual = "manual"
    ibkr = "ibkr"
    paper = "paper"  # System-managed paper trading portfolio (virtual cash)


class AssetType(str, enum.Enum):
    stock = "stock"
    etf = "etf"
    option = "option"
    crypto = "crypto"
    forex = "forex"
    futures = "futures"


class TradeAction(str, enum.Enum):
    buy = "buy"
    sell = "sell"
    short = "short"
    cover = "cover"


class Portfolio(TimestampMixin, Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    broker: Mapped[BrokerType] = mapped_column(
        Enum(BrokerType, name="broker_type"), default=BrokerType.manual
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_active: Mapped[bool] = mapped_column(default=True)

    # Paper trading: virtual cash. Both null for manual/ibkr portfolios.
    initial_cash: Mapped[float | None] = mapped_column(Numeric(18, 4))
    cash_balance: Mapped[float | None] = mapped_column(Numeric(18, 4))

    user: Mapped["User"] = relationship(back_populates="portfolios")  # noqa: F821
    positions: Mapped[list["Position"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    trades: Mapped[list["Trade"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class Position(TimestampMixin, Base):
    """Current open positions — updated as trades are recorded."""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    asset_type: Mapped[AssetType] = mapped_column(
        Enum(AssetType, name="asset_type"), default=AssetType.stock
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    avg_cost: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="USD")
    # Cached values — updated by background job
    current_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    previous_close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(18, 4))
    unrealized_pnl_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    portfolio: Mapped["Portfolio"] = relationship(back_populates="positions")


class Trade(TimestampMixin, Base):
    """Immutable log of every executed trade."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    asset_type: Mapped[AssetType] = mapped_column(
        Enum(AssetType, name="asset_type"), default=AssetType.stock
    )
    action: Mapped[TradeAction] = mapped_column(
        Enum(TradeAction, name="trade_action"), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    fees: Mapped[float] = mapped_column(Numeric(10, 4), default=0.0)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="USD")
    notes: Mapped[str | None] = mapped_column(Text)
    traded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # For IBKR synced trades
    external_id: Mapped[str | None] = mapped_column(String(100), unique=True)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="trades")
