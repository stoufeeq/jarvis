"""
Strategy API endpoints — manage user's auto-trader strategies.

All endpoints enforce two safety invariants:
1. Strategy creation/update rejects non-paper portfolios
2. Strategies are user-scoped (you can't see or modify other users')
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.portfolio import BrokerType, Portfolio
from app.models.strategy import Strategy, StrategyTrade, StrategyTradeStatus
from app.models.user import User
from app.schemas.strategy import (
    StrategyCreate,
    StrategyRead,
    StrategyStats,
    StrategyTradeRead,
    StrategyUpdate,
)
from app.services.auto_trader import AutoTraderService

router = APIRouter(prefix="/strategies", tags=["strategies"])


# ── List / create ───────────────────────────────────────────────────────


@router.get("/", response_model=list[StrategyRead])
async def list_strategies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Strategy).where(Strategy.user_id == user.id).order_by(Strategy.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/", response_model=StrategyRead, status_code=201)
async def create_strategy(
    payload: StrategyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Safety: only paper portfolios can host strategies
    portfolio = await db.get(Portfolio, payload.portfolio_id)
    if not portfolio or portfolio.user_id != user.id:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.broker != BrokerType.paper:
        raise HTTPException(
            status_code=400,
            detail="Strategies can only target paper portfolios (broker=paper)",
        )

    # Sanity-check hold periods
    if payload.min_hold_days > payload.base_hold_days:
        raise HTTPException(status_code=400, detail="min_hold_days must be <= base_hold_days")
    if payload.base_hold_days > payload.max_hold_days:
        raise HTTPException(status_code=400, detail="base_hold_days must be <= max_hold_days")

    strategy = Strategy(user_id=user.id, **payload.model_dump())
    db.add(strategy)
    await db.flush()
    await db.refresh(strategy)
    return strategy


# ── Get / update / delete ───────────────────────────────────────────────


@router.get("/{strategy_id}", response_model=StrategyRead)
async def get_strategy(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_owned(db, strategy_id, user.id)
    return s


@router.patch("/{strategy_id}", response_model=StrategyRead)
async def update_strategy(
    strategy_id: int,
    payload: StrategyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_owned(db, strategy_id, user.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(s, field, value)
    await db.flush()
    await db.refresh(s)
    return s


@router.delete("/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    s = await _get_owned(db, strategy_id, user.id)
    await db.delete(s)


# ── Trades + stats per strategy ─────────────────────────────────────────


@router.get("/{strategy_id}/trades", response_model=list[StrategyTradeRead])
async def list_strategy_trades(
    strategy_id: int,
    status: StrategyTradeStatus | None = Query(None),
    limit: int = Query(100, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned(db, strategy_id, user.id)
    query = select(StrategyTrade).where(StrategyTrade.strategy_id == strategy_id)
    if status:
        query = query.where(StrategyTrade.status == status)
    query = query.order_by(StrategyTrade.entry_at.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{strategy_id}/stats", response_model=StrategyStats)
async def get_strategy_stats(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned(db, strategy_id, user.id)
    result = await db.execute(
        select(StrategyTrade).where(StrategyTrade.strategy_id == strategy_id)
    )
    trades = list(result.scalars().all())

    open_count = sum(1 for t in trades if t.status == StrategyTradeStatus.open)
    closed_trades = [t for t in trades if t.status == StrategyTradeStatus.closed]
    closed_count = len(closed_trades)

    total_pnl = 0.0
    wins = losses = 0
    for t in closed_trades:
        if t.exit_price is None:
            continue
        pnl = (float(t.exit_price) - float(t.entry_price)) * float(t.quantity)
        if t.direction.value == "bearish":
            pnl = -pnl
        total_pnl += pnl
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1

    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else None

    return StrategyStats(
        open_count=open_count,
        closed_count=closed_count,
        total_pnl=round(total_pnl, 2),
        win_count=wins,
        loss_count=losses,
        win_rate_pct=round(win_rate, 1) if win_rate is not None else None,
    )


# ── Panic close ─────────────────────────────────────────────────────────


@router.post("/{strategy_id}/panic-close", status_code=200)
async def panic_close(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Immediately market-sell every open position in this strategy."""
    await _get_owned(db, strategy_id, user.id)
    closed = await AutoTraderService(db).panic_close_all(strategy_id)
    await db.commit()
    return {"closed": closed}


# ── Helper ──────────────────────────────────────────────────────────────


async def _get_owned(db: AsyncSession, strategy_id: int, user_id: int) -> Strategy:
    result = await db.execute(
        select(Strategy).where(
            and_(Strategy.id == strategy_id, Strategy.user_id == user_id)
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return s
