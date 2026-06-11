"""
Market snapshot — cached aggregate of "what's happening right now in
markets" used to ground the AI advisor in current data. Refreshed every
4 hours by a Celery task; old rows pruned after 7 days.

Coverage:
- Major indices (SPX, Nasdaq Composite, Dow, VIX, Russell 2000)
- Asset classes / commodity futures (gold, silver, oil, copper, nat gas,
  10Y treasury yield, dollar index)
- Crypto (BTC, ETH)
- Forex pairs (EUR/USD, GBP/USD, USD/JPY, AUD/USD)
- S&P 500 sectors (from heatmap)
- Top movers (5 gainers, 5 losers from S&P 500)
- Latest market headlines (Yahoo RSS)
- Upcoming macro events (from signals table)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_snapshot import MarketSnapshot
from app.models.signal import Signal, SignalType
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)

REFRESH_TTL = timedelta(hours=4)  # Triggers a lazy refresh if the latest is older.
PRUNE_AFTER = timedelta(days=7)   # Older snapshots get deleted.
TOP_MOVERS_COUNT = 5
MAX_HEADLINES = 10

# (display_name, yfinance_ticker) — kept compact so the LLM preamble stays small.
INDICES: list[tuple[str, str]] = [
    ("S&P 500",       "^GSPC"),
    ("Nasdaq",        "^IXIC"),
    ("Dow Jones",     "^DJI"),
    ("VIX",           "^VIX"),
    ("Russell 2000",  "^RUT"),
]

# Commodity futures + rates + dollar index. "ZN=F" would also work for the
# treasury front-month future but ^TNX gives the more intuitive yield-in-percent
# that humans (and the LLM) think in.
ASSET_CLASSES: list[tuple[str, str]] = [
    ("Gold",           "GC=F"),
    ("Silver",         "SI=F"),
    ("Oil (WTI)",      "CL=F"),
    ("Copper",         "HG=F"),
    ("Natural Gas",    "NG=F"),
    ("10Y Treasury",   "^TNX"),  # value is yield in %, not a dollar price.
    ("Dollar Index",   "DX-Y.NYB"),
]

CRYPTO: list[tuple[str, str]] = [
    ("Bitcoin",   "BTC-USD"),
    ("Ethereum",  "ETH-USD"),
]

FOREX: list[tuple[str, str]] = [
    ("EUR/USD",  "EURUSD=X"),
    ("GBP/USD",  "GBPUSD=X"),
    ("USD/JPY",  "USDJPY=X"),
    ("AUD/USD",  "AUDUSD=X"),
]


class MarketSnapshotService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Read path ────────────────────────────────────────────────────────

    async def get_latest(self) -> dict | None:
        """Return the most recent snapshot payload, or None if the table
        is empty. Does NOT check staleness — callers can decide their own
        TTL policy."""
        result = await self.db.execute(
            select(MarketSnapshot).order_by(MarketSnapshot.captured_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        try:
            payload = json.loads(row.payload_json)
            payload["_captured_at"] = row.captured_at.isoformat()
            return payload
        except json.JSONDecodeError as exc:
            log.warning("Stored market snapshot %s has invalid JSON: %s", row.id, exc)
            return None

    async def get_latest_or_lazy(self) -> dict | None:
        """Same as get_latest, but if no snapshot exists OR the latest is
        older than REFRESH_TTL, trigger a fresh fetch synchronously.
        Used by the AI advisor on cold-start so the first chat after
        deploy isn't grounded on empty data."""
        latest = await self.get_latest()
        now = datetime.now(UTC)
        if latest is not None:
            captured = datetime.fromisoformat(latest["_captured_at"])
            if now - captured < REFRESH_TTL:
                return latest
            log.info(
                "Market snapshot stale (%s old) — refreshing inline",
                now - captured,
            )
        else:
            log.info("No market snapshot yet — refreshing inline")
        try:
            return await self.refresh()
        except Exception as exc:
            log.warning("Inline market snapshot refresh failed: %s", exc)
            return latest  # fall back to whatever we have, even if stale

    # ── Refresh path ─────────────────────────────────────────────────────

    async def refresh(self) -> dict:
        """Build a fresh snapshot, persist it, return the payload."""
        payload = await self._build_payload()
        now = datetime.now(UTC)
        row = MarketSnapshot(
            captured_at=now,
            payload_json=json.dumps(payload),
            created_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.commit()
        payload["_captured_at"] = now.isoformat()
        return payload

    async def prune_old(self) -> int:
        """Delete snapshots older than PRUNE_AFTER. Returns rows removed."""
        cutoff = datetime.now(UTC) - PRUNE_AFTER
        result = await self.db.execute(
            delete(MarketSnapshot).where(MarketSnapshot.captured_at < cutoff)
        )
        await self.db.commit()
        return result.rowcount or 0

    # ── Internals ────────────────────────────────────────────────────────

    async def _build_payload(self) -> dict:
        # All quote fetches batched into one yfinance call set.
        ticker_pairs = INDICES + ASSET_CLASSES + CRYPTO + FOREX
        tickers = [t for _, t in ticker_pairs]
        mds = MarketDataService()
        try:
            quotes = await mds.get_quotes(tickers)
        except Exception as exc:
            log.warning("Snapshot quote fetch failed: %s", exc)
            quotes = []
        quote_by_ticker: dict[str, dict] = {q["ticker"]: q for q in quotes if q.get("ticker")}

        def cell(ticker: str) -> dict[str, Any]:
            q = quote_by_ticker.get(ticker, {})
            return {
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
            }

        indices = {name: cell(t) for name, t in INDICES}
        asset_classes = {name: cell(t) for name, t in ASSET_CLASSES}
        crypto = {name: cell(t) for name, t in CRYPTO}
        forex = {name: cell(t) for name, t in FOREX}

        # Sectors + top movers — reuse heatmap service.
        sectors, top_movers = await self._fetch_sectors_and_movers()

        # Market headlines — reuse briefing service's RSS fetcher.
        headlines = await self._fetch_headlines()

        # Upcoming macro events — from signals table.
        upcoming_macro = await self._fetch_upcoming_macro()

        return {
            "indices": indices,
            "asset_classes": asset_classes,
            "crypto": crypto,
            "forex": forex,
            "sectors": sectors,
            "top_movers": top_movers,
            "headlines": headlines,
            "upcoming_macro": upcoming_macro,
        }

    @staticmethod
    async def _fetch_sectors_and_movers() -> tuple[list[dict], dict[str, list[dict]]]:
        """Return (sector summaries, top movers) from the cached heatmap."""
        try:
            from app.services.heatmap import HeatmapService
            data = await HeatmapService().get_sp500_heatmap()
            sector_blocks = data.get("sectors", [])
        except Exception as exc:
            log.warning("Snapshot sector fetch failed: %s", exc)
            return [], {"gainers": [], "losers": []}

        # Sector-level average change weighted by index weight.
        sectors_out: list[dict] = []
        all_constituents: list[dict] = []
        for block in sector_blocks:
            children = block.get("children", []) or []
            total_w = sum(c.get("weight") or 0 for c in children)
            weighted_sum = sum(
                (c.get("change_pct") or 0) * (c.get("weight") or 0)
                for c in children
                if c.get("change_pct") is not None
            )
            avg = round(weighted_sum / total_w, 2) if total_w > 0 else None
            sectors_out.append({"name": block.get("name"), "change_pct": avg})
            for c in children:
                if c.get("change_pct") is not None:
                    all_constituents.append({
                        "ticker": c.get("ticker"),
                        "name": c.get("name"),
                        "change_pct": c.get("change_pct"),
                    })

        all_constituents.sort(key=lambda x: x["change_pct"] or 0, reverse=True)
        gainers = all_constituents[:TOP_MOVERS_COUNT]
        losers = list(reversed(all_constituents[-TOP_MOVERS_COUNT:]))
        return sectors_out, {"gainers": gainers, "losers": losers}

    @staticmethod
    async def _fetch_headlines() -> list[dict]:
        try:
            from app.services.briefing import BriefingService
            headlines = await BriefingService._fetch_market_headlines()
            return headlines[:MAX_HEADLINES]
        except Exception as exc:
            log.warning("Snapshot headline fetch failed: %s", exc)
            return []

    async def _fetch_upcoming_macro(self) -> list[dict]:
        """Pull non-expired macro_event signals from the signals table.

        The EconomicCalendarProvider populates these when FINNHUB_API_KEY
        is set; if it's not, this returns an empty list and the advisor
        just won't have upcoming-macro context."""
        now = datetime.now(UTC)
        result = await self.db.execute(
            select(Signal)
            .where(
                Signal.signal_type == SignalType.macro_event,
                (Signal.expires_at.is_(None)) | (Signal.expires_at > now),
            )
            .order_by(Signal.expires_at.asc())
            .limit(5)
        )
        out: list[dict] = []
        for s in result.scalars().all():
            out.append({
                "event": s.indicators or s.rationale or "Macro event",
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "strength": s.strength,
            })
        return out
