"""
Halal (Sharia) compliance screener.

Tier 1 design — hand-curated whitelist + AAOIFI financial ratios.

Verdict flow per ticker:
  1. ETF / mutual fund → look up in whitelist; on miss → unknown.
  2. Equity → run business-activity screen (industry / sector banned list).
                If non-compliant, return.
              Then ratio screen:
                debt / market_cap < 33%
                cash + ST investments / market_cap < 33%
  3. Any other quote type (crypto, currency, …) → unknown.

Look-aside cache in the halal_compliance table. The TTL is deliberately
longer than the monthly refresh job's period, so the scheduled task is
what renews a verdict and a page load never pays for a yfinance fetch
of a ticker that has been screened before. Pass force=True (or run the
Celery task) to refresh regardless of age.

yfinance fetches run in a thread pool so concurrent screening doesn't
block the event loop. Those fetches are the ONLY concurrent part:
verdicts are computed in parallel but persisted sequentially on one
session. Committing from inside asyncio.gather raises
IllegalStateChangeError — SQLAlchemy's AsyncSession is not safe for
concurrent use — which used to 500 the batch endpoint on any cold cache
and, because the badge renders nothing without data, failed invisibly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.halal_compliance import HalalCompliance, HalalStatus

log = logging.getLogger(__name__)

# Longer than the monthly refresh interval (see the halal_refresh Celery
# task) so a cached verdict is renewed by that job rather than lazily on
# a user's page load. The inputs are balance-sheet figures that move
# quarterly at most.
CACHE_TTL = timedelta(days=35)
DEBT_RATIO_MAX = 0.33      # AAOIFI 33% threshold
CASH_RATIO_MAX = 0.33

_WHITELIST_PATH = Path(__file__).parent.parent / "data" / "halal_whitelist.json"


def _load_whitelist() -> dict[str, Any]:
    with _WHITELIST_PATH.open() as f:
        return json.load(f)


def _normalise_label(value: str) -> str:
    """Canonicalise a sector/industry label for set membership.

    yfinance is inconsistent about the separator in compound industry
    names — the same industry arrives as "Banks—Diversified" (em-dash),
    "Banks-Diversified" (hyphen), or "Banks - Diversified" (spaced
    hyphen) depending on the version and the endpoint that served it.
    The whitelist stores one spelling, so an exact lowercase match
    silently lets the other spellings through: a bank would screen as
    COMPLIANT purely because of a dash character. Normalising both
    sides removes that class of false pass.
    """
    out = value.lower().strip()
    for dash in ("\u2014", "\u2013", "\u2212"):  # em, en, minus
        out = out.replace(dash, "-")
    # Collapse spacing around the separator and any repeated whitespace.
    out = out.replace(" - ", "-").replace(" -", "-").replace("- ", "-")
    return " ".join(out.split())


# Loaded once at import time; whitelist is small + rarely changes.
_WHITELIST = _load_whitelist()
_COMPLIANT_ETFS: dict[str, str] = _WHITELIST.get("etfs_compliant", {})
_BANNED_INDUSTRIES = {_normalise_label(s) for s in _WHITELIST.get("banned_industries", [])}
_BANNED_SECTORS = {_normalise_label(s) for s in _WHITELIST.get("banned_sectors", [])}


def _finite(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


class HalalScreenerService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Public API ─────────────────────────────────────────────────────────

    async def screen(self, ticker: str, force: bool = False) -> HalalCompliance:
        """Return cached verdict if fresh; otherwise recompute, persist, return."""
        ticker = ticker.upper()
        row = await self.db.get(HalalCompliance, ticker)
        if row and not force and self._is_fresh(row):
            return row
        verdict = await self._compute(ticker)
        row = self._persist(ticker, verdict, existing=row)
        await self.db.commit()
        return row

    async def screen_many(
        self, tickers: list[str], force: bool = False
    ) -> list[HalalCompliance]:
        """Batch screen. Provider fetches run concurrently; DB writes do not.

        Deduplicates the input — a portfolio and a watchlist commonly
        overlap, and screening the same symbol twice in one batch would
        both waste a provider call and race two writes to one primary key.
        """
        wanted = [t.upper() for t in tickers]
        unique = list(dict.fromkeys(wanted))
        if not unique:
            return []

        result = await self.db.execute(
            select(HalalCompliance).where(HalalCompliance.ticker.in_(unique))
        )
        cached = {r.ticker: r for r in result.scalars().all()}

        out: dict[str, HalalCompliance] = {}
        misses: list[str] = []
        for t in unique:
            row = cached.get(t)
            if row is not None and not force and self._is_fresh(row):
                out[t] = row
            else:
                misses.append(t)

        if misses:
            # Limit concurrency — yfinance throttles aggressively above ~10
            # parallel. Nothing in here touches the DB session.
            sem = asyncio.Semaphore(8)

            async def one(t: str) -> tuple[str, dict[str, Any]]:
                async with sem:
                    return t, await self._compute(t)

            verdicts = await asyncio.gather(*[one(t) for t in misses])

            # Persist sequentially on the single session, then commit once.
            for t, verdict in verdicts:
                out[t] = self._persist(t, verdict, existing=cached.get(t))
            await self.db.commit()

        # Preserve input order; a ticker requested twice is returned once.
        return [out[t] for t in unique if t in out]

    async def refresh_all(self, tickers: list[str]) -> dict[str, int]:
        """Re-screen every given ticker ignoring the cache. Used by the
        monthly Celery task; returns a verdict tally for the log."""
        rows = await self.screen_many(tickers, force=True)
        tally: dict[str, int] = {"screened": len(rows)}
        for r in rows:
            key = r.status.value if hasattr(r.status, "value") else str(r.status)
            tally[key] = tally.get(key, 0) + 1
        return tally

    # ── Compute path ───────────────────────────────────────────────────────

    @staticmethod
    def _is_fresh(row: HalalCompliance) -> bool:
        computed = row.computed_at
        if computed is None:
            return False
        # The column is DateTime(timezone=True), so Postgres hands back an
        # aware value — but SQLite (tests) and any row written by a path
        # that lost the tzinfo hand back a naive one, and subtracting
        # those raises TypeError. A crash here would look exactly like the
        # bug this file guards against: no verdict, no badge, no message.
        if computed.tzinfo is None:
            computed = computed.replace(tzinfo=UTC)
        return datetime.now(UTC) - computed < CACHE_TTL

    def _persist(
        self, ticker: str, verdict: dict[str, Any], existing: HalalCompliance | None
    ) -> HalalCompliance:
        """Write the verdict to the cache row. Deliberately synchronous and
        commit-free: the caller decides the transaction boundary, which is
        what keeps batch screening off concurrent commits."""
        now = datetime.now(UTC)

        if existing is None:
            row = HalalCompliance(
                ticker=ticker,
                status=verdict["status"],
                reason=verdict.get("reason"),
                quote_type=verdict.get("quote_type"),
                sector=verdict.get("sector"),
                industry=verdict.get("industry"),
                debt_pct=verdict.get("debt_pct"),
                cash_pct=verdict.get("cash_pct"),
                computed_at=now,
            )
            self.db.add(row)
        else:
            row = existing
            row.status = verdict["status"]
            row.reason = verdict.get("reason")
            row.quote_type = verdict.get("quote_type")
            row.sector = verdict.get("sector")
            row.industry = verdict.get("industry")
            row.debt_pct = verdict.get("debt_pct")
            row.cash_pct = verdict.get("cash_pct")
            row.computed_at = now

        return row

    async def _compute(self, ticker: str) -> dict[str, Any]:
        # Fast paths that don't require yfinance ─────────────────────────────
        if ticker in _COMPLIANT_ETFS:
            return {
                "status": HalalStatus.compliant,
                "reason": f"ETF whitelist: {_COMPLIANT_ETFS[ticker]}",
                "quote_type": "ETF",
            }

        # Pull yfinance Ticker.info in a thread so blocking I/O doesn't stall loop
        try:
            info = await asyncio.to_thread(self._fetch_info, ticker)
        except Exception as exc:
            log.warning("Halal screener: yfinance fetch failed for %s: %s", ticker, exc)
            return {"status": HalalStatus.unknown, "reason": "Data fetch failed"}

        if not info:
            return {"status": HalalStatus.unknown, "reason": "No data available"}

        quote_type = (info.get("quoteType") or "").upper()
        sector = info.get("sector")
        industry = info.get("industry")

        # Unknown ETFs / mutual funds — we don't do constituent screening in Tier 1.
        if quote_type in {"ETF", "MUTUALFUND"}:
            return {
                "status": HalalStatus.unknown,
                "reason": "Not in halal-ETF whitelist; constituent screening not implemented",
                "quote_type": quote_type,
            }

        if quote_type and quote_type != "EQUITY":
            return {
                "status": HalalStatus.unknown,
                "reason": f"Unsupported quote type: {quote_type}",
                "quote_type": quote_type,
            }

        # Equity screen ──────────────────────────────────────────────────────
        # 1. Business activity
        if industry and _normalise_label(industry) in _BANNED_INDUSTRIES:
            return {
                "status": HalalStatus.non_compliant,
                "reason": f"Industry: {industry}",
                "quote_type": "EQUITY",
                "sector": sector,
                "industry": industry,
            }
        if (not industry) and sector and _normalise_label(sector) in _BANNED_SECTORS:
            return {
                "status": HalalStatus.non_compliant,
                "reason": f"Sector: {sector}",
                "quote_type": "EQUITY",
                "sector": sector,
            }

        # 2. Financial ratios
        market_cap = _finite(info.get("marketCap"))
        total_debt = _finite(info.get("totalDebt"))
        total_cash = _finite(info.get("totalCash"))

        if market_cap is None or market_cap <= 0:
            return {
                "status": HalalStatus.unknown,
                "reason": "Missing market cap",
                "quote_type": "EQUITY",
                "sector": sector,
                "industry": industry,
            }

        debt_pct = (total_debt / market_cap) if total_debt is not None else None
        cash_pct = (total_cash / market_cap) if total_cash is not None else None

        if debt_pct is None or cash_pct is None:
            return {
                "status": HalalStatus.unknown,
                "reason": "Missing financials (debt or cash)",
                "quote_type": "EQUITY",
                "sector": sector,
                "industry": industry,
                "debt_pct": debt_pct,
                "cash_pct": cash_pct,
            }

        if debt_pct >= DEBT_RATIO_MAX:
            return {
                "status": HalalStatus.non_compliant,
                "reason": f"Debt {debt_pct * 100:.1f}% ≥ 33%",
                "quote_type": "EQUITY",
                "sector": sector,
                "industry": industry,
                "debt_pct": debt_pct,
                "cash_pct": cash_pct,
            }
        if cash_pct >= CASH_RATIO_MAX:
            return {
                "status": HalalStatus.non_compliant,
                "reason": f"Cash + ST securities {cash_pct * 100:.1f}% ≥ 33%",
                "quote_type": "EQUITY",
                "sector": sector,
                "industry": industry,
                "debt_pct": debt_pct,
                "cash_pct": cash_pct,
            }

        return {
            "status": HalalStatus.compliant,
            "reason": "Passes activity + AAOIFI 33% ratios",
            "quote_type": "EQUITY",
            "sector": sector,
            "industry": industry,
            "debt_pct": debt_pct,
            "cash_pct": cash_pct,
        }

    @staticmethod
    def _fetch_info(ticker: str) -> dict | None:
        t = yf.Ticker(ticker)
        info = t.info
        return info if info else None
