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

Look-aside cache in halal_compliance table; 24h TTL. yfinance fetches
run in thread pool so concurrent screening doesn't block the event loop.
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

CACHE_TTL = timedelta(hours=24)
DEBT_RATIO_MAX = 0.33      # AAOIFI 33% threshold
CASH_RATIO_MAX = 0.33

_WHITELIST_PATH = Path(__file__).parent.parent / "data" / "halal_whitelist.json"


def _load_whitelist() -> dict[str, Any]:
    with _WHITELIST_PATH.open() as f:
        return json.load(f)


# Loaded once at import time; whitelist is small + rarely changes.
_WHITELIST = _load_whitelist()
_COMPLIANT_ETFS: dict[str, str] = _WHITELIST.get("etfs_compliant", {})
_BANNED_INDUSTRIES = {s.lower() for s in _WHITELIST.get("banned_industries", [])}
_BANNED_SECTORS = {s.lower() for s in _WHITELIST.get("banned_sectors", [])}


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

    async def screen(self, ticker: str) -> HalalCompliance:
        """Return cached verdict if fresh; otherwise recompute, persist, return."""
        ticker = ticker.upper()
        row = await self.db.get(HalalCompliance, ticker)
        if row and self._is_fresh(row):
            return row
        return await self._compute_and_store(ticker, existing=row)

    async def screen_many(self, tickers: list[str]) -> list[HalalCompliance]:
        """Batch — concurrent yfinance fetches when cache misses."""
        tickers = [t.upper() for t in tickers]
        if not tickers:
            return []

        result = await self.db.execute(
            select(HalalCompliance).where(HalalCompliance.ticker.in_(tickers))
        )
        cached = {r.ticker: r for r in result.scalars().all()}

        out: list[HalalCompliance] = []
        misses: list[tuple[str, HalalCompliance | None]] = []
        for t in tickers:
            row = cached.get(t)
            if row and self._is_fresh(row):
                out.append(row)
            else:
                misses.append((t, row))

        if misses:
            # Limit concurrency — yfinance throttles aggressively above ~10 parallel.
            sem = asyncio.Semaphore(8)

            async def one(t: str, existing: HalalCompliance | None) -> HalalCompliance:
                async with sem:
                    return await self._compute_and_store(t, existing=existing)

            fresh = await asyncio.gather(*[one(t, e) for t, e in misses])
            out.extend(fresh)

        # Preserve input ticker order
        by_t = {r.ticker: r for r in out}
        return [by_t[t] for t in tickers if t in by_t]

    # ── Compute path ───────────────────────────────────────────────────────

    @staticmethod
    def _is_fresh(row: HalalCompliance) -> bool:
        age = datetime.now(UTC) - row.computed_at
        return age < CACHE_TTL

    async def _compute_and_store(
        self, ticker: str, existing: HalalCompliance | None
    ) -> HalalCompliance:
        verdict = await self._compute(ticker)
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

        await self.db.commit()
        await self.db.refresh(row)
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
        if industry and industry.lower() in _BANNED_INDUSTRIES:
            return {
                "status": HalalStatus.non_compliant,
                "reason": f"Industry: {industry}",
                "quote_type": "EQUITY",
                "sector": sector,
                "industry": industry,
            }
        if (not industry) and sector and sector.lower() in _BANNED_SECTORS:
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
