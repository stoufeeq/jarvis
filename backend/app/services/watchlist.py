from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.watchlist import WatchlistCreate, WatchlistItemCreate


class WatchlistService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, watchlist_id: int) -> Watchlist | None:
        result = await self.db.execute(
            select(Watchlist)
            .where(Watchlist.id == watchlist_id)
            .options(selectinload(Watchlist.items))
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[Watchlist]:
        result = await self.db.execute(
            select(Watchlist)
            .where(Watchlist.user_id == user_id)
            .options(selectinload(Watchlist.items))
        )
        return list(result.scalars().all())

    async def _reload(self, watchlist_id: int) -> Watchlist:
        """Re-fetch a watchlist with items eagerly loaded."""
        result = await self.db.execute(
            select(Watchlist)
            .where(Watchlist.id == watchlist_id)
            .options(selectinload(Watchlist.items))
        )
        return result.scalar_one()

    async def create(self, user_id: int, payload: WatchlistCreate) -> Watchlist:
        wl = Watchlist(user_id=user_id, name=payload.name)
        self.db.add(wl)
        await self.db.flush()
        return await self._reload(wl.id)

    async def add_item(self, watchlist: Watchlist, payload: WatchlistItemCreate) -> Watchlist:
        item = WatchlistItem(
            watchlist_id=watchlist.id,
            ticker=payload.ticker.upper(),
            notes=payload.notes,
        )
        self.db.add(item)
        await self.db.flush()
        return await self._reload(watchlist.id)

    async def remove_item(self, watchlist: Watchlist, ticker: str) -> None:
        result = await self.db.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist.id,
                WatchlistItem.ticker == ticker,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            await self.db.delete(item)
            await self.db.flush()
