from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.calendar import CalendarService

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/")
async def get_calendar(
    days_ahead: int = Query(60, ge=1, le=365),
    portfolio_only: bool = Query(False),
    types: list[str] | None = Query(None, description="Subset: earnings, ex_dividend, macro"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upcoming earnings, ex-dividend, and macro events for the user's
    watchlist + portfolio tickers, sorted by date."""
    return await CalendarService(db).upcoming_events_for_user(
        user_id=user.id,
        days_ahead=days_ahead,
        portfolio_only=portfolio_only,
        types=types,
    )


@router.post("/refresh", status_code=202)
async def refresh_calendar(
    user: User = Depends(get_current_user),
):
    """Manually dispatch a Celery task to refresh calendar events for all
    user tickers (one-shot — same task runs daily automatically)."""
    from app.workers.tasks.calendar_refresh import refresh_calendar_events
    task = refresh_calendar_events.delay()
    return {"task_id": task.id, "status": "dispatched"}
