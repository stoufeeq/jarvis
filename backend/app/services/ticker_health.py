"""
Ticker resolution monitoring.

Answers one question: is any symbol the user references something the
market-data provider has never heard of?

This exists because MBGD and SYTA 404'd on every price refresh, signal
scan, insider fetch, dividend sync and backtest for weeks. They were
loud in the logs and completely invisible in the UI — the affected
holdings just silently contributed nothing everywhere, and a Mercedes
position ended up split across two symbols as a result.

The design problem is false positives. yfinance 404s transiently even
for large, liquid, unambiguously-listed names: CTRA, BK, MMC, HOLX and
EXAS have all failed mid-heatmap while being perfectly real S&P
constituents. Flagging on a single failure would produce a permanently
red banner that the user learns to ignore, which is worse than no
banner. Hence FAILURE_THRESHOLD: a ticker must fail on several
consecutive daily checks before it is called broken, and one success
resets the counter to zero.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import BrokerType, Portfolio, Position
from app.models.ticker_health import TickerHealth
from app.models.watchlist import Watchlist, WatchlistItem

log = logging.getLogger(__name__)


class TickerHealthService:
    # Consecutive daily failures before a ticker is reported as broken.
    # Three days is long enough to ride out provider flakiness and short
    # enough that a genuine typo surfaces within the week.
    FAILURE_THRESHOLD = 3

    # Concurrency for the provider probes. Deliberately modest — this
    # runs daily and being slow is fine; being rate-limited is not, since
    # a 429 would look exactly like a bad symbol.
    CONCURRENCY = 4

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Which tickers matter ──────────────────────────────────────────

    async def tracked_tickers(self, user_id: int | None = None) -> list[str]:
        """Symbols the user actually references: open positions in real
        portfolios, plus watchlist entries. Paper portfolios are included
        too — a typo there breaks the paper strategy just as thoroughly."""
        tickers: set[str] = set()

        pos_q = (
            select(Position.ticker)
            .join(Portfolio, Portfolio.id == Position.portfolio_id)
            .where(Position.quantity > 0)
        )
        wl_q = (
            select(WatchlistItem.ticker)
            .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        )
        if user_id is not None:
            pos_q = pos_q.where(Portfolio.user_id == user_id)
            wl_q = wl_q.where(Watchlist.user_id == user_id)

        for q in (pos_q, wl_q):
            for row in (await self.db.execute(q.distinct())).all():
                if row[0]:
                    tickers.add(row[0].upper())
        return sorted(tickers)

    # ── Probing ───────────────────────────────────────────────────────

    @staticmethod
    def _probe_sync(ticker: str) -> tuple[bool, str | None]:
        """Blocking provider probe. Returns (resolved, error).

        Tries a live price first, then falls back to recent history —
        some valid symbols (thin ADRs, certain foreign listings) have no
        fast_info price but do have bars. Only when BOTH come back empty
        do we call it unresolved.
        """
        import yfinance as yf

        try:
            t = yf.Ticker(ticker)
            try:
                price = t.fast_info.get("lastPrice")
                if price and float(price) > 0:
                    return True, None
            except Exception:
                pass

            hist = t.history(period="5d", interval="1d")
            if hist is not None and not hist.empty and len(hist["Close"].dropna()) > 0:
                return True, None
            return False, "no price and no recent history"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:400]

    async def check_all(self, user_id: int | None = None) -> dict:
        """Probe every tracked ticker and update its health row.

        Success resets consecutive_failures to zero and stamps
        last_ok_at. Failure increments, and only once the count reaches
        FAILURE_THRESHOLD is the ticker marked as not resolving.
        """
        tickers = await self.tracked_tickers(user_id)
        if not tickers:
            return {"checked": 0, "broken": 0, "recovered": 0}

        sem = asyncio.Semaphore(self.CONCURRENCY)

        async def _one(t: str) -> tuple[str, bool, str | None]:
            async with sem:
                ok, err = await asyncio.to_thread(self._probe_sync, t)
                return t, ok, err

        results = await asyncio.gather(*(_one(t) for t in tickers))

        existing = {
            r.ticker: r
            for r in (await self.db.execute(
                select(TickerHealth).where(TickerHealth.ticker.in_(tickers))
            )).scalars().all()
        }

        now = datetime.now(UTC)
        broken = recovered = 0

        for ticker, ok, err in results:
            row = existing.get(ticker)
            if row is None:
                row = TickerHealth(ticker=ticker, resolves=True, consecutive_failures=0)
                self.db.add(row)

            row.last_checked_at = now
            if ok:
                if not row.resolves:
                    recovered += 1
                    log.info("Ticker health: %s resolves again", ticker)
                row.resolves = True
                row.consecutive_failures = 0
                row.last_ok_at = now
                row.last_error = None
            else:
                row.consecutive_failures += 1
                row.last_error = err
                if row.consecutive_failures >= self.FAILURE_THRESHOLD and row.resolves:
                    row.resolves = False
                    broken += 1
                    log.warning(
                        "Ticker health: %s marked unresolvable after %d consecutive "
                        "failures (%s)", ticker, row.consecutive_failures, err,
                    )

        await self.db.flush()
        return {"checked": len(tickers), "broken": broken, "recovered": recovered}

    # ── Read side ─────────────────────────────────────────────────────

    async def unresolvable_for_user(self, user_id: int) -> list[dict]:
        """Broken tickers this user references, newest problem first.

        `never_resolved` distinguishes a typo (never worked) from a
        delisting or ticker change (worked until recently) — the fixes
        are different, so the UI says which it is.
        """
        tickers = await self.tracked_tickers(user_id)
        if not tickers:
            return []

        rows = (await self.db.execute(
            select(TickerHealth).where(
                TickerHealth.ticker.in_(tickers),
                TickerHealth.resolves.is_(False),
            ).order_by(TickerHealth.consecutive_failures.desc())
        )).scalars().all()

        return [
            {
                "ticker": r.ticker,
                "consecutive_failures": r.consecutive_failures,
                "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
                "last_ok_at": r.last_ok_at.isoformat() if r.last_ok_at else None,
                "never_resolved": r.last_ok_at is None,
                "last_error": r.last_error,
            }
            for r in rows
        ]
