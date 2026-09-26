"""
Reference-data lookup for tickers: sector, industry, country, quote type.

Cached in the ticker_profiles table with a long TTL — a company's sector
is stable for years, so the only reason to refetch is a symbol that
previously had no data.

Concurrency shape mirrors the lesson from the halal screener: provider
fetches run in parallel, DB writes do not. Committing from inside
asyncio.gather raises IllegalStateChangeError on a shared AsyncSession.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticker_profile import TickerProfile

log = logging.getLogger(__name__)

# Sectors don't change. This TTL exists so a symbol that resolved to
# nothing (new listing, provider hiccup) is eventually retried, not so
# that sectors are kept current.
CACHE_TTL = timedelta(days=90)

# Shorter retry for rows we failed to classify, so a ticker added the day
# the provider was flaky doesn't sit sector-less for three months.
UNKNOWN_RETRY_TTL = timedelta(days=3)

CONCURRENCY = 8


class TickerProfileService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_many(self, tickers: list[str]) -> dict[str, TickerProfile]:
        """Profiles keyed by uppercase ticker. Fetches only what's missing
        or stale; never raises on a provider failure (a ticker simply comes
        back without a sector)."""
        unique = list(dict.fromkeys(t.upper() for t in tickers if t))
        if not unique:
            return {}

        rows = (
            await self.db.execute(
                select(TickerProfile).where(TickerProfile.ticker.in_(unique))
            )
        ).scalars().all()
        cached = {r.ticker: r for r in rows}

        misses = [t for t in unique if not self._is_fresh(cached.get(t))]

        if misses:
            sem = asyncio.Semaphore(CONCURRENCY)

            async def one(t: str) -> tuple[str, dict | None]:
                async with sem:
                    return t, await asyncio.to_thread(self._fetch, t)

            fetched = await asyncio.gather(*(one(t) for t in misses))

            now = datetime.now(UTC)
            for ticker, info in fetched:
                row = cached.get(ticker)
                if row is None:
                    row = TickerProfile(ticker=ticker, fetched_at=now)
                    self.db.add(row)
                    cached[ticker] = row
                # A failed fetch stamps fetched_at without clobbering a
                # sector we already had — better a stale sector than none.
                if info is not None:
                    row.company_name = info.get("company_name") or row.company_name
                    row.sector = info.get("sector") or row.sector
                    row.industry = info.get("industry") or row.industry
                    row.country = info.get("country") or row.country
                    row.quote_type = info.get("quote_type") or row.quote_type
                row.fetched_at = now
            await self.db.commit()

        return cached

    @staticmethod
    def _is_fresh(row: TickerProfile | None) -> bool:
        if row is None:
            return False
        fetched = row.fetched_at
        if fetched is None:
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        ttl = CACHE_TTL if row.sector else UNKNOWN_RETRY_TTL
        return datetime.now(UTC) - fetched < ttl

    @staticmethod
    def _fetch(ticker: str) -> dict | None:
        """Blocking provider call. Returns None on any failure — the caller
        treats that as 'no data', never as an error."""
        import yfinance as yf

        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as exc:
            log.warning("Ticker profile: fetch failed for %s: %s", ticker, exc)
            return None
        if not info:
            return None
        return {
            "company_name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "quote_type": (info.get("quoteType") or "").upper() or None,
        }
