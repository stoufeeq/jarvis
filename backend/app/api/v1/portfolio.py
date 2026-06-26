from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.portfolio import Trade
from app.models.user import User
from app.schemas.portfolio import (
    PaperTradeRequest,
    PortfolioCreate,
    PortfolioRead,
    PortfolioSummary,
    PortfolioUpdate,
    PositionRead,
    TradeCreate,
    TradeRead,
    TradeUpdate,
)
from app.services.portfolio import PortfolioService

router = APIRouter(prefix="/portfolios", tags=["portfolio"])


def _assert_owner(portfolio, user: User):
    if portfolio.user_id != user.id:
        raise ForbiddenError()


async def _assert_not_auto_managed(svc: "PortfolioService", portfolio_id: int) -> None:
    """Block manual trade mutations on portfolios owned by an auto-trader
    strategy — the strategy tracks its own per-trade quantity and a manual
    trade slipped in would silently desync the StrategyTrade ledger from
    the Position table, eventually stranding strategy_trades in 'open' when
    the auto-close tries to sell shares that no longer exist."""
    if await svc.is_auto_managed(portfolio_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "This portfolio is managed by an auto-trader strategy. "
                "Pause or delete the strategy first if you need to make "
                "manual trades."
            ),
        )


@router.get("/", response_model=list[PortfolioSummary])
async def list_portfolios(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    portfolios = await svc.list_for_user(user.id)
    return [await svc.get_summary(p) for p in portfolios]


@router.post("/", response_model=PortfolioRead, status_code=201)
async def create_portfolio(
    payload: PortfolioCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await PortfolioService(db).create(user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{portfolio_id}/paper-trade", response_model=TradeRead, status_code=201)
async def execute_paper_trade(
    portfolio_id: int,
    payload: PaperTradeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a market-order paper trade (buy or sell) at the current quote.
    Validates cash for buys, shares for sells. Updates the virtual cash balance."""
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    await _assert_not_auto_managed(svc, portfolio_id)
    try:
        return await svc.execute_paper_trade(
            portfolio=p,
            ticker=payload.ticker,
            action=payload.action,
            quantity=payload.quantity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{portfolio_id}", response_model=PortfolioSummary)
async def get_portfolio(
    portfolio_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    return await svc.get_summary(p)


@router.get("/{portfolio_id}/performance")
async def get_portfolio_performance(
    portfolio_id: int,
    period: str = Query("6mo", regex="^(1mo|3mo|6mo|1y|2y|5y|ytd|max)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Daily market value + cost basis series for the portfolio over `period`.

    Returned as `[{date, market_value, cost_basis}, ...]` in the
    portfolio's base currency. Empty list if the portfolio has no trades.
    """
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    curve = await svc.compute_equity_curve(p, period=period)
    return {"portfolio_id": portfolio_id, "currency": p.currency, "period": period, "points": curve}


@router.patch("/{portfolio_id}", response_model=PortfolioRead)
async def update_portfolio(
    portfolio_id: int,
    payload: PortfolioUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    return await svc.update(p, payload)


@router.delete("/{portfolio_id}", status_code=204)
async def delete_portfolio(
    portfolio_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    await svc.delete(p)


# ── Positions ────────────────────────────────────────────────────────────────

@router.get("/{portfolio_id}/positions", response_model=list[PositionRead])
async def list_positions(
    portfolio_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    return await svc.list_positions(portfolio_id)


# ── Trades ───────────────────────────────────────────────────────────────────

@router.get("/{portfolio_id}/trades", response_model=list[TradeRead])
async def list_trades(
    portfolio_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    return await svc.list_trades(portfolio_id)


@router.post("/{portfolio_id}/trades", response_model=TradeRead, status_code=201)
async def add_trade(
    portfolio_id: int,
    payload: TradeCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    await _assert_not_auto_managed(svc, portfolio_id)
    return await svc.add_trade(portfolio_id, payload)


@router.patch("/{portfolio_id}/trades/{trade_id}", response_model=TradeRead)
async def update_trade(
    portfolio_id: int,
    trade_id: int,
    payload: TradeUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    await _assert_not_auto_managed(svc, portfolio_id)
    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.portfolio_id == portfolio_id)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise NotFoundError("Trade not found")
    return await svc.update_trade(trade, payload)


@router.delete("/{portfolio_id}/trades/{trade_id}", status_code=204)
async def delete_trade(
    portfolio_id: int,
    trade_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    await _assert_not_auto_managed(svc, portfolio_id)
    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.portfolio_id == portfolio_id)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise NotFoundError("Trade not found")
    await svc.delete_trade(trade)


@router.post("/{portfolio_id}/import-csv", status_code=202)
async def import_csv(
    portfolio_id: int,
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import trades from an IBKR-format CSV export."""
    svc = PortfolioService(db)
    p = await svc.get(portfolio_id)
    if not p:
        raise NotFoundError("Portfolio not found")
    _assert_owner(p, user)
    await _assert_not_auto_managed(svc, portfolio_id)
    content = await file.read()
    count = await svc.import_from_csv(portfolio_id, content)
    return {"imported": count}
