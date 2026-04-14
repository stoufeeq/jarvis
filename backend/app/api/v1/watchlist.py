from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.user import User
from app.schemas.watchlist import WatchlistCreate, WatchlistItemCreate, WatchlistRead
from app.services.watchlist import WatchlistService

router = APIRouter(prefix="/watchlists", tags=["watchlist"])


@router.get("/", response_model=list[WatchlistRead])
async def list_watchlists(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await WatchlistService(db).list_for_user(user.id)


@router.post("/", response_model=WatchlistRead, status_code=201)
async def create_watchlist(
    payload: WatchlistCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await WatchlistService(db).create(user.id, payload)


@router.post("/{watchlist_id}/items", response_model=WatchlistRead, status_code=201)
async def add_item(
    watchlist_id: int,
    payload: WatchlistItemCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = WatchlistService(db)
    wl = await svc.get(watchlist_id)
    if not wl:
        raise NotFoundError("Watchlist not found")
    if wl.user_id != user.id:
        raise ForbiddenError()
    return await svc.add_item(wl, payload)


@router.delete("/{watchlist_id}/items/{ticker}", status_code=204)
async def remove_item(
    watchlist_id: int,
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = WatchlistService(db)
    wl = await svc.get(watchlist_id)
    if not wl:
        raise NotFoundError("Watchlist not found")
    if wl.user_id != user.id:
        raise ForbiddenError()
    await svc.remove_item(wl, ticker.upper())
