"""
Portfolio allocation — how holdings distribute across sectors, asset
types and individual names.

Answers a composition question, deliberately kept apart from
PortfolioService.compute_risk_metrics, which answers a behaviour
question. Risk needs 90 days of price history for every holding and takes
seconds; allocation needs cached positions plus a sector lookup and is
effectively instant. Putting them behind one call would make a pie chart
wait on a correlation matrix.

Values are market value converted to one base currency, because
comparing a EUR position against a USD one un-normalised gives a
meaningless split. Cost basis is deliberately NOT used: exposure is what
a holding is worth now, not what was paid for it.

Paper portfolios are excluded by default — auto-trader positions turn
over constantly and would swamp the real picture.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.sp500 import SP500
from app.models.portfolio import BrokerType, Portfolio, Position
from app.services.market_data import MarketDataService
from app.services.ticker_profile import TickerProfileService

log = logging.getLogger(__name__)

# yfinance's sector vocabulary differs from the GICS names in
# app/data/sp500.py, so the two must be reconciled before a holding's
# sector can be compared with the index's weight in it. Without this map
# "Technology" and "Information Technology" look like different sectors
# and every benchmark comparison silently reads 0%.
SECTOR_TO_GICS = {
    "technology": "Information Technology",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "financial services": "Financials",
    "financial": "Financials",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "communication services": "Communication Services",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
    "basic materials": "Materials",
    "materials": "Materials",
}

# Label for holdings with no sector — ETFs, crypto, and anything the
# provider couldn't classify. Shown as its own slice rather than dropped,
# so the percentages always sum to 100 and an unclassified chunk is
# visible instead of quietly distorting every other share.
UNCLASSIFIED = "Unclassified"


def _gics(sector: str | None) -> str:
    if not sector:
        return UNCLASSIFIED
    return SECTOR_TO_GICS.get(sector.strip().lower(), sector.strip())


def _sp500_sector_weights() -> dict[str, float]:
    """Approximate S&P 500 weight per GICS sector, normalised to 100%.

    The per-name weights in app/data/sp500.py are hand-maintained and
    currently sum to ~126%, so raw values are not usable as shares.
    Normalising makes them comparable; they stay approximate, and the API
    marks them as such so the UI can say so.
    """
    totals: dict[str, float] = defaultdict(float)
    for row in SP500:
        totals[row["sector"]] += float(row.get("weight") or 0.0)
    grand = sum(totals.values())
    if grand <= 0:
        return {}
    return {k: v / grand * 100.0 for k, v in totals.items()}


class AllocationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def compute(
        self,
        user_id: int,
        portfolio_id: int | None = None,
        include_paper: bool = False,
        base_ccy: str = "USD",
    ) -> dict:
        """Allocation for one portfolio, or across all of the user's
        portfolios when portfolio_id is None."""
        base_ccy = (base_ccy or "USD").upper()

        pq = select(Portfolio).where(
            Portfolio.user_id == user_id, Portfolio.is_active.is_(True)
        )
        if portfolio_id is not None:
            pq = pq.where(Portfolio.id == portfolio_id)
        elif not include_paper:
            pq = pq.where(Portfolio.broker != BrokerType.paper)
        portfolios = (await self.db.execute(pq)).scalars().all()

        if not portfolios:
            return self._empty(base_ccy, [])

        pids = [p.id for p in portfolios]
        names = {p.id: p.name for p in portfolios}

        positions = (
            await self.db.execute(
                select(Position).where(
                    Position.portfolio_id.in_(pids), Position.quantity > 0
                )
            )
        ).scalars().all()
        if not positions:
            return self._empty(base_ccy, [names[i] for i in pids])

        fx = await self._fx_rates(positions, base_ccy)
        profiles = await TickerProfileService(self.db).get_many(
            [p.ticker for p in positions]
        )

        # One holding may appear in several portfolios; exposure is the sum.
        by_ticker: dict[str, dict] = {}
        priced = 0
        unpriced: list[str] = []

        for pos in positions:
            value = self._market_value(pos, fx, base_ccy)
            if value is None:
                unpriced.append(pos.ticker.upper())
                continue
            priced += 1
            key = pos.ticker.upper()
            entry = by_ticker.setdefault(
                key,
                {
                    "ticker": key,
                    "value": 0.0,
                    "portfolios": set(),
                    "asset_type": pos.asset_type.value if pos.asset_type else "stock",
                },
            )
            entry["value"] += value
            entry["portfolios"].add(names.get(pos.portfolio_id, ""))

        total = sum(e["value"] for e in by_ticker.values())
        if total <= 0:
            return self._empty(base_ccy, [names[i] for i in pids])

        bench = _sp500_sector_weights()

        sector_groups: dict[str, dict] = {}
        type_groups: dict[str, dict] = {}

        for key, entry in by_ticker.items():
            prof = profiles.get(key)
            sector = _gics(prof.sector if prof else None)
            g = sector_groups.setdefault(
                sector, {"name": sector, "value": 0.0, "tickers": []}
            )
            g["value"] += entry["value"]
            g["tickers"].append(key)

            at = entry["asset_type"]
            t = type_groups.setdefault(at, {"name": at, "value": 0.0, "tickers": []})
            t["value"] += entry["value"]
            t["tickers"].append(key)

        def _as_groups(groups: dict[str, dict], with_bench: bool) -> list[dict]:
            out = []
            for g in groups.values():
                pct = g["value"] / total * 100.0
                row = {
                    "name": g["name"],
                    "value": round(g["value"], 2),
                    "pct": round(pct, 2),
                    "count": len(g["tickers"]),
                    "tickers": sorted(g["tickers"]),
                }
                if with_bench:
                    bw = bench.get(g["name"])
                    row["benchmark_pct"] = round(bw, 2) if bw is not None else None
                    # Percentage-point gap, not a ratio: a ratio explodes
                    # for a sector the index barely holds and reads as a
                    # precision the underlying weights don't have.
                    row["vs_benchmark_pp"] = (
                        round(pct - bw, 2) if bw is not None else None
                    )
                out.append(row)
            # Unclassified last regardless of size — it is a data-quality
            # note, not a sector, and shouldn't head the list.
            return sorted(
                out, key=lambda r: (r["name"] == UNCLASSIFIED, -r["pct"])
            )

        holdings = sorted(
            (
                {
                    "ticker": e["ticker"],
                    "name": (profiles.get(e["ticker"]).company_name
                             if profiles.get(e["ticker"]) else None),
                    "sector": _gics(profiles.get(e["ticker"]).sector
                                    if profiles.get(e["ticker"]) else None),
                    "value": round(e["value"], 2),
                    "pct": round(e["value"] / total * 100.0, 2),
                    "portfolios": sorted(x for x in e["portfolios"] if x),
                }
                for e in by_ticker.values()
            ),
            key=lambda r: -r["pct"],
        )

        return {
            "base_currency": base_ccy,
            "total_value": round(total, 2),
            "portfolio_names": [names[i] for i in pids],
            "holdings_count": len(by_ticker),
            "by_sector": _as_groups(sector_groups, with_bench=True),
            "by_asset_type": _as_groups(type_groups, with_bench=False),
            "holdings": holdings,
            "concentration": self._concentration(holdings),
            "unpriced_tickers": sorted(set(unpriced)),
            "benchmark_note": (
                "S&P 500 sector weights are approximate (hand-maintained "
                "constituent list, normalised to 100%)."
            ),
        }

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _market_value(pos: Position, fx: dict[str, float], base: str) -> float | None:
        """Current market value in base currency, or None when the position
        has no cached price yet.

        Returning None rather than falling back to cost keeps a stale
        position from being silently valued at what was paid for it, which
        would misstate every percentage on the page.
        """
        price = pos.current_price
        if price is None:
            return None
        try:
            value = float(price) * float(pos.quantity)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        ccy = (pos.currency or base).upper()
        if ccy == base:
            return value
        rate = fx.get(ccy)
        # No rate means we cannot express this holding in the base
        # currency. Treating it as 1:1 would silently misweight it, so
        # it's reported as unpriced instead.
        if not rate or not math.isfinite(rate) or rate <= 0:
            return None
        return value * rate

    async def _fx_rates(
        self, positions: list[Position], base: str
    ) -> dict[str, float]:
        foreign = {
            (p.currency or base).upper()
            for p in positions
            if (p.currency or base).upper() != base
        }
        if not foreign:
            return {}
        try:
            return await MarketDataService().get_fx_rates(sorted(foreign), base=base)
        except Exception as exc:
            log.warning("Allocation: FX fetch failed (%s); foreign holdings "
                        "will be reported as unpriced", exc)
            return {}

    @staticmethod
    def _concentration(holdings: list[dict]) -> dict:
        """Single-name concentration summary.

        HHI is the sum of squared percentage shares — the standard
        concentration measure. Its reciprocal is the 'effective number of
        holdings': 20 equal positions give 20, while 20 positions where
        one is 80% give about 1.5. That reads more honestly than a count
        of rows.
        """
        pcts = [h["pct"] for h in holdings]
        hhi = sum(p * p for p in pcts)
        effective = (10_000.0 / hhi) if hhi > 0 else 0.0
        return {
            "top_1_pct": round(pcts[0], 2) if pcts else 0.0,
            "top_3_pct": round(sum(pcts[:3]), 2),
            "top_5_pct": round(sum(pcts[:5]), 2),
            "top_10_pct": round(sum(pcts[:10]), 2),
            "hhi": round(hhi, 1),
            "effective_holdings": round(effective, 1),
        }

    @staticmethod
    def _empty(base_ccy: str, portfolio_names: list[str]) -> dict:
        return {
            "base_currency": base_ccy,
            "total_value": 0.0,
            "portfolio_names": portfolio_names,
            "holdings_count": 0,
            "by_sector": [],
            "by_asset_type": [],
            "holdings": [],
            "concentration": {
                "top_1_pct": 0.0, "top_3_pct": 0.0, "top_5_pct": 0.0,
                "top_10_pct": 0.0, "hhi": 0.0, "effective_holdings": 0.0,
            },
            "unpriced_tickers": [],
            "benchmark_note": "",
        }
