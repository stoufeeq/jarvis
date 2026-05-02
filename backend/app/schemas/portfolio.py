from datetime import datetime

from pydantic import BaseModel

from app.models.portfolio import AssetType, BrokerType, TradeAction


class PortfolioCreate(BaseModel):
    name: str
    description: str | None = None
    broker: BrokerType = BrokerType.manual
    currency: str = "USD"
    # Required when broker = 'paper'. Defaults to $100k if not provided.
    initial_cash: float | None = None


class PortfolioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    currency: str | None = None
    is_active: bool | None = None


class PortfolioRead(BaseModel):
    id: int
    user_id: int
    name: str
    description: str | None
    broker: BrokerType
    currency: str
    is_active: bool
    created_at: datetime
    initial_cash: float | None = None
    cash_balance: float | None = None

    model_config = {"from_attributes": True}


class PortfolioSummary(PortfolioRead):
    """PortfolioRead + computed P&L totals."""
    total_value: float | None = None
    total_cost: float | None = None
    total_pnl: float | None = None
    total_pnl_pct: float | None = None
    day_change: float | None = None      # today's $ change across all positions
    day_change_pct: float | None = None  # today's % change vs yesterday's close
    position_count: int = 0


class PaperTradeRequest(BaseModel):
    """Request body for executing a paper trade."""
    ticker: str
    action: TradeAction  # buy or sell
    quantity: float


class PositionRead(BaseModel):
    id: int
    portfolio_id: int
    ticker: str
    asset_type: AssetType
    quantity: float
    avg_cost: float
    currency: str
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    opened_at: datetime

    model_config = {"from_attributes": True}


class TradeCreate(BaseModel):
    ticker: str
    asset_type: AssetType = AssetType.stock
    action: TradeAction
    quantity: float
    price: float
    fees: float = 0.0
    currency: str = "USD"
    notes: str | None = None
    traded_at: datetime


class TradeUpdate(BaseModel):
    action: TradeAction | None = None
    asset_type: AssetType | None = None
    quantity: float | None = None
    price: float | None = None
    fees: float | None = None
    currency: str | None = None
    notes: str | None = None
    traded_at: datetime | None = None


class TradeRead(BaseModel):
    id: int
    portfolio_id: int
    ticker: str
    asset_type: AssetType
    action: TradeAction
    quantity: float
    price: float
    fees: float
    currency: str
    notes: str | None
    traded_at: datetime
    external_id: str | None

    model_config = {"from_attributes": True}
