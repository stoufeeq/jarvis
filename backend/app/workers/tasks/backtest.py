"""Celery task: run a signal-strategy backtest asynchronously.

Backtests iterate the full SignalOutcome table and fetch benchmark
history from yfinance. Running that inline in a FastAPI request holds
a DB connection for the entire simulation and — on a memory-constrained
box — can push the API container past its OOM limit and take user-facing
traffic down with it.

Dispatching to Celery isolates the workload: the API returns a task_id
immediately, the worker does the heavy lifting, and the frontend polls
GET /signals/backtest/{task_id} for the result. If the worker OOMs it
restarts on its own without affecting the API.
"""

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.models.signal import SignalDirection, SignalType
from app.services.backtest import BacktestService
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.backtest.run_backtest_task", bind=True)
def run_backtest_task(
    self,
    signal_type: str | None = None,
    direction: str | None = None,
    min_strength: int = 1,
    hold_period: str = "5d",
    capital_per_trade: float = 1000.0,
    ticker: str | None = None,
) -> dict:
    """Wrapper around BacktestService.simulate. Enum values are passed
    as their string .value so Celery can JSON-serialise them; we rehydrate
    to the Enum types below since the service signature expects them."""
    return asyncio.run(
        _run(
            signal_type=signal_type,
            direction=direction,
            min_strength=min_strength,
            hold_period=hold_period,
            capital_per_trade=capital_per_trade,
            ticker=ticker,
        )
    )


async def _run(
    signal_type: str | None,
    direction: str | None,
    min_strength: int,
    hold_period: str,
    capital_per_trade: float,
    ticker: str | None,
) -> dict:
    st_enum = SignalType(signal_type) if signal_type else None
    dir_enum = SignalDirection(direction) if direction else None

    async with AsyncSessionLocal() as db:
        result = await BacktestService(db).simulate(
            signal_type=st_enum,
            direction=dir_enum,
            min_strength=min_strength,
            hold_period=hold_period,  # type: ignore[arg-type]
            capital_per_trade=capital_per_trade,
            ticker=ticker,
        )
    log.info(
        "Backtest complete: %d trades, %.2f%% return",
        result.get("metrics", {}).get("n_trades", 0),
        result.get("metrics", {}).get("total_return_pct", 0),
    )
    return result
