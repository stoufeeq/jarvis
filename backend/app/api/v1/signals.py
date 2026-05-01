from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.signal import SignalDirection, SignalType
from app.models.user import User
from app.schemas.signal import SignalOutcomeRead, SignalRead
from app.schemas.insider_trade import InsiderTradeRead
from app.services.signal_engine import SignalEngine
from app.services.signal_outcome import SignalOutcomeService

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/", response_model=list[SignalRead])
async def get_signals(
    ticker: str | None = Query(None),
    signal_type: SignalType | None = Query(None),
    direction: SignalDirection | None = Query(None),
    limit: int = Query(50, le=200),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve recent signals, optionally filtered."""
    return await SignalEngine(db).get_signals(
        ticker=ticker, signal_type=signal_type, direction=direction, limit=limit
    )


@router.post("/scan/{ticker}", response_model=list[SignalRead])
async def scan_ticker(
    ticker: str,
    include_ai: bool = Query(False, description="Also run AI News and Cross-Impact providers (uses Gemini)"),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run signal scan for a ticker. Set include_ai=true to also run Gemini-powered providers."""
    engine = SignalEngine(db)
    signals = await engine.scan_ticker(ticker.upper(), include_ai=include_ai)
    await db.commit()
    return signals


@router.get("/insider", response_model=list[InsiderTradeRead])
async def get_insider_trades(
    ticker: str | None = Query(None),
    limit: int = Query(50, le=200),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recent insider trades, optionally filtered by ticker."""
    return await SignalEngine(db).get_insider_trades(ticker=ticker, limit=limit)


@router.get("/performance")
async def get_signal_performance(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated signal performance stats — hit rate and avg gain by
    signal type, direction, and strength across 1d/5d/30d/90d timeframes."""
    return await SignalOutcomeService(db).get_performance_stats()


@router.get("/outcomes", response_model=list[SignalOutcomeRead])
async def get_recent_outcomes(
    limit: int = Query(50, le=200),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Recent tracked signal outcomes (raw rows, newest first)."""
    return await SignalOutcomeService(db).list_recent(limit=limit)


@router.post("/outcomes/backfill", status_code=202)
async def backfill_outcomes(
    limit: int | None = Query(None, description="Max signals to process (omit for all)"),
    _: User = Depends(get_current_user),
):
    """Dispatch a background Celery task to populate signal_outcomes for
    existing signals using historical yfinance prices. Returns immediately;
    backfill runs asynchronously and may take minutes for large signal sets.
    Safe to re-run — only processes signals that don't already have an
    outcome row."""
    from app.workers.tasks.signal_outcome import backfill_signal_outcomes
    task = backfill_signal_outcomes.delay(limit)
    return {"task_id": task.id, "status": "dispatched"}
