"""
Calendar service — aggregates upcoming events relevant to a user's
watchlist and portfolio.

Sources:
- Earnings + ex-dividend dates → calendar_events table (refreshed daily
  from yfinance Ticker.calendar / Ticker.info)
- Macro events (FOMC, CPI, jobs) → signals table (signal_type=macro_event,
  populated by EconomicCalendarProvider)

Read path is fast: pure DB queries, no live yfinance calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import yfinance as yf
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar_event import CalendarEvent, CalendarEventType
from app.models.portfolio import Portfolio, Position
from app.models.signal import Signal, SignalType
from app.models.watchlist import Watchlist, WatchlistItem

log = logging.getLogger(__name__)


class CalendarService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Read path ─────────────────────────────────────────────────────────

    async def upcoming_events_for_user(
        self,
        user_id: int,
        days_ahead: int = 60,
        portfolio_only: bool = False,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return upcoming events (earnings, ex-dividend, macro) for the
        user's watchlist + portfolio tickers, sorted by date ascending.

        - days_ahead: include events up to this many days from today
        - portfolio_only: if True, ignore watchlist tickers
        - types: filter to ["earnings", "ex_dividend", "macro"] subset
        """
        today = datetime.now(UTC).date()
        end = today + timedelta(days=days_ahead)
        type_filter = set(types) if types else None

        # Collect user's tickers
        port_tickers = await self._user_portfolio_tickers(user_id)
        watch_tickers = set() if portfolio_only else await self._user_watchlist_tickers(user_id)
        relevant_tickers = port_tickers | watch_tickers

        events: list[dict[str, Any]] = []

        # 1. Earnings + ex-dividend from calendar_events
        if relevant_tickers and (not type_filter or type_filter & {"earnings", "ex_dividend"}):
            type_conds = []
            if not type_filter or "earnings" in type_filter:
                type_conds.append(CalendarEvent.event_type == CalendarEventType.earnings)
            if not type_filter or "ex_dividend" in type_filter:
                type_conds.append(CalendarEvent.event_type == CalendarEventType.ex_dividend)

            if type_conds:
                from sqlalchemy import or_
                result = await self.db.execute(
                    select(CalendarEvent).where(
                        and_(
                            CalendarEvent.ticker.in_(relevant_tickers),
                            CalendarEvent.event_date >= today,
                            CalendarEvent.event_date <= end,
                            or_(*type_conds),
                        )
                    ).order_by(CalendarEvent.event_date.asc())
                )
                for e in result.scalars().all():
                    events.append({
                        "type": e.event_type.value,
                        "ticker": e.ticker,
                        "date": e.event_date.isoformat(),
                        "title": e.title,
                        "details": e.details,
                        "in_portfolio": e.ticker in port_tickers,
                    })

        # 2. Macro events from signals table (no per-ticker filter — relevant to all)
        if not type_filter or "macro" in type_filter:
            now = datetime.now(UTC)
            macro_result = await self.db.execute(
                select(Signal).where(
                    Signal.signal_type == SignalType.macro_event,
                    (Signal.expires_at.is_(None)) | (Signal.expires_at > now),
                ).order_by(Signal.created_at.asc())
            )
            for s in macro_result.scalars().all():
                # Macro signals encode the event date in expires_at (rough proxy)
                # or fall back to created_at + a few days
                event_date = (s.expires_at or (s.created_at + timedelta(days=7))).date()
                if event_date < today or event_date > end:
                    continue
                events.append({
                    "type": "macro",
                    "ticker": s.ticker if s.ticker not in (None, "MACRO", "ECON") else None,
                    "date": event_date.isoformat(),
                    "title": s.rationale[:120] if s.rationale else "Macro event",
                    "details": s.indicators,
                    "in_portfolio": False,
                })

        events.sort(key=lambda e: (e["date"], e["type"]))
        return events

    # ── Write path: refresh from yfinance ────────────────────────────────

    async def refresh_for_tickers(self, tickers: list[str]) -> dict[str, int]:
        """Fetch upcoming earnings + ex-dividend dates from yfinance for
        each ticker and upsert into calendar_events.

        Skips crypto tickers (no traditional fundamentals from yfinance).
        Returns counts of {earnings, ex_dividend, errors}."""
        from app.data.crypto import is_crypto

        equity_tickers = [t for t in tickers if not is_crypto(t)]
        counts = {"earnings": 0, "ex_dividend": 0, "errors": 0}
        now = datetime.now(UTC)

        for ticker in equity_tickers:
            try:
                t = yf.Ticker(ticker)

                # Earnings date
                ed = self._extract_earnings_date(t)
                if ed:
                    await self._upsert_event(
                        ticker=ticker,
                        event_type=CalendarEventType.earnings,
                        event_date=ed,
                        title=f"{ticker} Earnings",
                        details=None,
                        fetched_at=now,
                    )
                    counts["earnings"] += 1

                # Ex-dividend date
                xd = self._extract_ex_dividend(t)
                if xd:
                    div_date, div_amount = xd
                    details = f"${div_amount:.2f} per share" if div_amount else None
                    title = f"{ticker} Ex-Dividend"
                    if div_amount:
                        title += f" ${div_amount:.2f}"
                    await self._upsert_event(
                        ticker=ticker,
                        event_type=CalendarEventType.ex_dividend,
                        event_date=div_date,
                        title=title,
                        details=details,
                        fetched_at=now,
                    )
                    counts["ex_dividend"] += 1

            except Exception as exc:
                log.warning("Calendar refresh failed for %s: %s", ticker, exc)
                counts["errors"] += 1

        # Clean up stale events (past + not refreshed in 14d) for these tickers
        cutoff_date = now.date() - timedelta(days=1)
        stale_cutoff = now - timedelta(days=14)
        await self.db.execute(
            delete(CalendarEvent).where(
                and_(
                    CalendarEvent.ticker.in_(equity_tickers),
                    CalendarEvent.event_date < cutoff_date,
                    CalendarEvent.fetched_at < stale_cutoff,
                )
            )
        )

        await self.db.flush()
        return counts

    async def _upsert_event(
        self,
        ticker: str,
        event_type: CalendarEventType,
        event_date: date,
        title: str,
        details: str | None,
        fetched_at: datetime,
    ) -> None:
        """Insert or update a single calendar_events row by unique key."""
        existing = await self.db.execute(
            select(CalendarEvent).where(
                CalendarEvent.ticker == ticker,
                CalendarEvent.event_type == event_type,
                CalendarEvent.event_date == event_date,
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.title = title
            row.details = details
            row.fetched_at = fetched_at
        else:
            self.db.add(CalendarEvent(
                ticker=ticker,
                event_type=event_type,
                event_date=event_date,
                title=title,
                details=details,
                fetched_at=fetched_at,
            ))

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_earnings_date(t: yf.Ticker) -> date | None:
        """Pull the next earnings date from yfinance Ticker.calendar.

        Calendar can be a dict (newer yfinance) or DataFrame (older);
        the date can be a date object, datetime, list of dates, or string."""
        try:
            cal = t.calendar
            if cal is None:
                return None
            ed = None
            if hasattr(cal, "get"):
                ed = cal.get("Earnings Date") or cal.get("earningsDate")
            elif hasattr(cal, "iloc"):
                try:
                    ed = cal.iloc[0, 0]
                except Exception:
                    return None
            if ed is None:
                return None
            if isinstance(ed, list) and ed:
                ed = ed[0]
            if hasattr(ed, "date"):
                return ed.date()
            return datetime.fromisoformat(str(ed)[:10]).date()
        except Exception:
            return None

    @staticmethod
    def _extract_ex_dividend(t: yf.Ticker) -> tuple[date, float | None] | None:
        """Pull the next ex-dividend date from yfinance Ticker.info."""
        try:
            info = t.info
            ts = info.get("exDividendDate")  # Unix epoch seconds
            if not ts:
                return None
            d = datetime.fromtimestamp(int(ts), tz=UTC).date()
            today = datetime.now(UTC).date()
            # Skip past ex-dividend dates
            if d < today:
                return None
            amount = info.get("lastDividendValue") or info.get("dividendRate")
            return d, float(amount) if amount else None
        except Exception:
            return None

    async def _user_portfolio_tickers(self, user_id: int) -> set[str]:
        result = await self.db.execute(
            select(Position.ticker)
            .join(Portfolio, Position.portfolio_id == Portfolio.id)
            .where(Portfolio.user_id == user_id, Portfolio.is_active.is_(True))
            .distinct()
        )
        return {row[0] for row in result.fetchall()}

    async def _user_watchlist_tickers(self, user_id: int) -> set[str]:
        result = await self.db.execute(
            select(WatchlistItem.ticker)
            .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.user_id == user_id)
            .distinct()
        )
        return {row[0] for row in result.fetchall()}
