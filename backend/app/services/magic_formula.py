"""
Greenblatt Magic Formula screen.

Ranks a universe on two metrics independently, then sums the ranks —
lowest combined score wins:

  Return on capital   EBIT / capital employed
  Earnings yield      EBIT / enterprise value

One deviation from the book, stated plainly: Greenblatt used return on
TANGIBLE capital (net working capital + net fixed assets, excluding
goodwill). yfinance does not reliably expose the components for that, so
this uses standard ROCE — EBIT / (total assets − current liabilities).
The practical difference is that acquisitive companies carrying large
goodwill balances score worse here than in Greenblatt's version.

Exclusions follow the book:
  - Financials: EBIT/EV is meaningless when debt is the raw material of
    the business rather than a financing choice.
  - Utilities: regulated returns and heavy leverage distort both legs.
Plus mechanical exclusions where the maths breaks down — non-positive
EBIT, capital employed or enterprise value. Excluded names are RETAINED
with a reason so the UI can explain an absence rather than silently
dropping it.

Compute cost: three yfinance calls per ticker. ~500 names takes 15-25
minutes, so this is a weekly Celery job writing to magic_formula_ranks,
never an on-demand page load.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.sp500 import SP500
from app.models.magic_formula import MagicFormulaRank
from app.models.portfolio import BrokerType, Portfolio, Position
from app.models.watchlist import Watchlist, WatchlistItem

log = logging.getLogger(__name__)

# Yahoo's sector taxonomy, not GICS — these are the strings Ticker.info
# actually returns.
EXCLUDED_SECTORS = {"financial services", "utilities"}

SP500_META = {s["ticker"]: s for s in SP500}

# yfinance row labels drift between versions and filers; try in order.
EBIT_ROWS = ("EBIT", "Operating Income", "Total Operating Income As Reported")
CURRENT_LIABILITY_ROWS = ("Current Liabilities", "Total Current Liabilities")


@dataclass
class Fundamentals:
    ticker: str
    company_name: str | None = None
    sector: str | None = None
    ebit: float | None = None
    capital_employed: float | None = None
    enterprise_value: float | None = None
    error: str | None = None


class MagicFormulaService:
    CONCURRENCY = 6

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Universe ──────────────────────────────────────────────────────

    async def build_universe(self) -> list[str]:
        """S&P 500 constituents plus anything the user actually follows —
        open positions in real portfolios and watchlist entries. Paper
        positions are excluded: they are strategy artefacts, not names
        the user has chosen to track."""
        tickers = {s["ticker"].upper() for s in SP500}

        rows = (await self.db.execute(
            select(Position.ticker).distinct()
            .join(Portfolio, Portfolio.id == Position.portfolio_id)
            .where(Position.quantity > 0, Portfolio.broker != BrokerType.paper)
        )).all()
        tickers.update(r[0].upper() for r in rows if r[0])

        rows = (await self.db.execute(
            select(WatchlistItem.ticker).distinct()
            .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        )).all()
        tickers.update(r[0].upper() for r in rows if r[0])

        return sorted(tickers)

    # ── Fetch ─────────────────────────────────────────────────────────

    @staticmethod
    def _fetch_sync(ticker: str) -> Fundamentals:
        """Blocking pull of the three inputs. Never raises — a ticker
        that fails becomes an excluded row with a reason, so one bad
        symbol cannot abort a 500-name run."""
        import yfinance as yf

        out = Fundamentals(ticker=ticker)
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            out.company_name = info.get("shortName") or info.get("longName")
            out.sector = info.get("sector")
            ev = info.get("enterpriseValue")
            if ev is not None:
                try:
                    ev_f = float(ev)
                    if math.isfinite(ev_f):
                        out.enterprise_value = ev_f
                except (TypeError, ValueError):
                    pass

            fin = t.financials
            if fin is not None and not fin.empty:
                for row in EBIT_ROWS:
                    if row in fin.index:
                        v = fin.loc[row].iloc[0]
                        if v == v:  # not NaN
                            out.ebit = float(v)
                            break

            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                total_assets = None
                current_liabilities = None
                if "Total Assets" in bs.index:
                    v = bs.loc["Total Assets"].iloc[0]
                    if v == v:
                        total_assets = float(v)
                for row in CURRENT_LIABILITY_ROWS:
                    if row in bs.index:
                        v = bs.loc[row].iloc[0]
                        if v == v:
                            current_liabilities = float(v)
                            break
                if total_assets is not None and current_liabilities is not None:
                    out.capital_employed = total_assets - current_liabilities
        except Exception as exc:
            out.error = f"{type(exc).__name__}: {exc}"[:180]
        return out

    async def fetch_all(self, tickers: list[str]) -> list[Fundamentals]:
        sem = asyncio.Semaphore(self.CONCURRENCY)
        done = 0

        async def _one(t: str) -> Fundamentals:
            nonlocal done
            async with sem:
                res = await asyncio.to_thread(self._fetch_sync, t)
                done += 1
                if done % 50 == 0:
                    log.info("Magic formula: fetched %d/%d", done, len(tickers))
                return res

        return list(await asyncio.gather(*(_one(t) for t in tickers)))

    # ── Rank ──────────────────────────────────────────────────────────

    @staticmethod
    def _exclusion_reason(f: Fundamentals) -> str | None:
        """None means the ticker is rankable."""
        if f.error:
            return f"Data fetch failed: {f.error}"
        if f.sector and f.sector.strip().lower() in EXCLUDED_SECTORS:
            return f"Excluded sector: {f.sector}"
        if f.ebit is None:
            return "No EBIT reported"
        if f.ebit <= 0:
            # A loss-making company has a negative yield and negative
            # return on capital; ranking it against profitable peers is
            # not meaningful, and Greenblatt's screen is quality-first.
            return "Non-positive EBIT"
        if f.capital_employed is None:
            return "No capital-employed data"
        if f.capital_employed <= 0:
            # Happens after sustained buybacks (negative equity). ROCE
            # would be negative or explosive, so it can't be ranked.
            return "Non-positive capital employed"
        if f.enterprise_value is None or f.enterprise_value <= 0:
            return "No enterprise value"
        return None

    def rank(self, fundamentals: list[Fundamentals]) -> list[dict]:
        """Rank the eligible names on both legs and sum. Ties share a
        rank (competition ranking), so two equally cheap names both get
        the better position rather than one being arbitrarily demoted."""
        rows: list[dict] = []
        eligible: list[dict] = []

        for f in fundamentals:
            reason = self._exclusion_reason(f)
            base = {
                "ticker": f.ticker,
                "company_name": f.company_name,
                "sector": f.sector,
                "ebit": f.ebit,
                "capital_employed": f.capital_employed,
                "enterprise_value": f.enterprise_value,
                "roce": None,
                "earnings_yield": None,
                "rank_roce": None,
                "rank_yield": None,
                "combined_rank": None,
                "excluded": reason is not None,
                "exclusion_reason": reason,
                "in_sp500": f.ticker in SP500_META,
            }
            if reason is None:
                base["roce"] = f.ebit / f.capital_employed
                base["earnings_yield"] = f.ebit / f.enterprise_value
                eligible.append(base)
            rows.append(base)

        def _assign(key: str, rank_key: str) -> None:
            # Higher metric = better = lower rank number.
            ordered = sorted(eligible, key=lambda r: -r[key])
            prev_val = None
            prev_rank = 0
            for i, r in enumerate(ordered, start=1):
                if prev_val is not None and r[key] == prev_val:
                    r[rank_key] = prev_rank      # tie keeps the better rank
                else:
                    r[rank_key] = i
                    prev_rank = i
                    prev_val = r[key]

        _assign("roce", "rank_roce")
        _assign("earnings_yield", "rank_yield")

        for r in eligible:
            r["combined_rank"] = r["rank_roce"] + r["rank_yield"]

        return rows

    # ── Persist / read ────────────────────────────────────────────────

    async def refresh(self) -> dict:
        tickers = await self.build_universe()
        log.info("Magic formula: fetching %d tickers", len(tickers))
        fundamentals = await self.fetch_all(tickers)
        rows = self.rank(fundamentals)

        now = datetime.now(UTC)
        await self.db.execute(delete(MagicFormulaRank))
        for r in rows:
            self.db.add(MagicFormulaRank(computed_at=now, **r))
        await self.db.flush()

        ranked = sum(1 for r in rows if not r["excluded"])
        log.info(
            "Magic formula: %d tickers, %d ranked, %d excluded",
            len(rows), ranked, len(rows) - ranked,
        )
        return {
            "universe": len(rows),
            "ranked": ranked,
            "excluded": len(rows) - ranked,
            "computed_at": now.isoformat(),
        }

    async def get_screen(self, limit: int = 30) -> dict:
        """Top N by combined rank, plus the exclusion tally so the UI can
        explain what is missing and why."""
        ranked = (await self.db.execute(
            select(MagicFormulaRank)
            .where(MagicFormulaRank.excluded.is_(False))
            .order_by(MagicFormulaRank.combined_rank.asc(), MagicFormulaRank.ticker.asc())
            .limit(limit)
        )).scalars().all()

        all_rows = (await self.db.execute(select(MagicFormulaRank))).scalars().all()
        if not all_rows:
            return {
                "computed_at": None, "universe": 0, "ranked": 0,
                "exclusions": [], "results": [],
            }

        tally: dict[str, int] = {}
        for r in all_rows:
            if r.excluded and r.exclusion_reason:
                # Collapse the per-ticker error detail into one bucket.
                key = r.exclusion_reason.split(":")[0]
                tally[key] = tally.get(key, 0) + 1

        return {
            "computed_at": all_rows[0].computed_at.isoformat(),
            "universe": len(all_rows),
            "ranked": sum(1 for r in all_rows if not r.excluded),
            "exclusions": [
                {"reason": k, "count": v}
                for k, v in sorted(tally.items(), key=lambda kv: -kv[1])
            ],
            "results": [
                {
                    "ticker": r.ticker,
                    "company_name": r.company_name,
                    "sector": r.sector,
                    "roce": float(r.roce) if r.roce is not None else None,
                    "earnings_yield": float(r.earnings_yield) if r.earnings_yield is not None else None,
                    "ev_ebit": (
                        float(r.enterprise_value) / float(r.ebit)
                        if r.ebit and float(r.ebit) > 0 and r.enterprise_value else None
                    ),
                    "rank_roce": r.rank_roce,
                    "rank_yield": r.rank_yield,
                    "combined_rank": r.combined_rank,
                    "in_sp500": r.in_sp500,
                }
                for r in ranked
            ],
        }
