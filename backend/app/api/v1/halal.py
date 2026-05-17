from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.halal import HalalComplianceRead
from app.services.halal_screener import HalalScreenerService

router = APIRouter(prefix="/halal", tags=["halal"])


@router.get("/{ticker}", response_model=HalalComplianceRead)
async def screen_one(
    ticker: str,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sharia compliance verdict for a single ticker. Uses the 24h cache;
    on miss, runs the screen and persists the result."""
    return await HalalScreenerService(db).screen(ticker)


@router.get("/", response_model=list[HalalComplianceRead])
async def screen_many(
    tickers: list[str] = Query(..., description="One or more tickers"),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch screen — same cache semantics as the single-ticker endpoint."""
    return await HalalScreenerService(db).screen_many(tickers)
