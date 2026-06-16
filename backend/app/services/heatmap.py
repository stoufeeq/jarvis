"""
Heatmap service — batch-fetches S&P 500 quotes and builds a sector tree
suitable for rendering as a Recharts Treemap (heatmap) or ScatterChart
(bubbles) on the frontend.

Uses period="1mo" so we have enough rows to compute a 20-day average volume
for the relative-volume axis on the bubbles view.

Results are cached in-process for CACHE_TTL seconds.
"""

import asyncio
import logging
import math
import time
from typing import Any

from app.data.sp500 import SP500

log = logging.getLogger(__name__)

CACHE_TTL = 1800  # 30 minutes — aligned with frontend staleTime and Celery pre-warm interval

# Defensive clamp on yfinance daily-bar data. Yahoo occasionally publishes
# bad prints (a 2026-06-16 example showed WDC at +20%+ which wasn't real),
# and a single rogue value distorts the whole sector visualisation. Real
# single-day moves >15% happen but are rare; on the S&P 500 universe,
# anything bigger is almost always a yfinance quote glitch or a missed
# corporate-action adjustment. Mark those as None so the heatmap renders
# the cell as "no data" rather than amplifying the noise.
MAX_SANE_DAILY_CHANGE_PCT = 15.0

_cache: dict[str, Any] = {}


class HeatmapService:
    async def get_sp500_heatmap(self, force_refresh: bool = False) -> dict:
        if not force_refresh:
            cached = _cache.get("sp500")
            if cached and (time.monotonic() - cached["ts"]) < CACHE_TTL:
                return cached["data"]

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch_heatmap_sync)

        _cache["sp500"] = {"ts": time.monotonic(), "data": data}
        return data


def _fetch_heatmap_sync() -> dict:
    import yfinance as yf

    tickers = [s["ticker"] for s in SP500]

    change_map: dict[str, float | None] = {}
    vol_map: dict[str, float | None] = {}

    try:
        df = yf.download(
            tickers,
            period="1mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        closes  = df.get("Close")
        volumes = df.get("Volume")

        if closes is None:
            raise ValueError("No Close column in download result")

        for ticker in tickers:
            # ── change % ──────────────────────────────────────────────────────
            try:
                series = (closes if len(tickers) == 1 else closes[ticker]).dropna()
                if len(series) >= 2:
                    prev = float(series.iloc[-2])
                    curr = float(series.iloc[-1])
                    if prev and math.isfinite(prev) and math.isfinite(curr):
                        pct = round((curr - prev) / prev * 100, 2)
                        if abs(pct) > MAX_SANE_DAILY_CHANGE_PCT:
                            log.warning(
                                "Heatmap: dropping implausible change for %s "
                                "(prev=%.2f, curr=%.2f, pct=%+.2f%%) — likely a "
                                "yfinance bad print or missed corporate action.",
                                ticker, prev, curr, pct,
                            )
                            change_map[ticker] = None
                        else:
                            change_map[ticker] = pct
                    else:
                        change_map[ticker] = None
                elif len(series) == 1:
                    change_map[ticker] = 0.0
                else:
                    change_map[ticker] = None
            except (KeyError, IndexError, TypeError, ValueError):
                change_map[ticker] = None

            # ── relative volume ───────────────────────────────────────────────
            try:
                if volumes is None:
                    vol_map[ticker] = None
                    continue
                vseries = (volumes if len(tickers) == 1 else volumes[ticker]).dropna()
                if len(vseries) >= 2:
                    today_vol = float(vseries.iloc[-1])
                    avg_vol   = float(vseries.iloc[:-1].mean())
                    if avg_vol and math.isfinite(avg_vol) and math.isfinite(today_vol):
                        vol_map[ticker] = round(today_vol / avg_vol, 2)
                    else:
                        vol_map[ticker] = None
                else:
                    vol_map[ticker] = None
            except (KeyError, IndexError, TypeError, ValueError):
                vol_map[ticker] = None

    except Exception as exc:
        return {
            "sectors": _build_sectors(change_map, vol_map),
            "cached_at": time.time(),
            "error": str(exc),
        }

    return {
        "sectors": _build_sectors(change_map, vol_map),
        "cached_at": time.time(),
    }


def _build_sectors(
    change_map: dict[str, float | None],
    vol_map: dict[str, float | None] | None = None,
) -> list[dict]:
    vol_map = vol_map or {}
    sectors_dict: dict[str, list[dict]] = {}

    for stock in SP500:
        sector = stock["sector"]
        if sector not in sectors_dict:
            sectors_dict[sector] = []
        sectors_dict[sector].append(
            {
                "ticker":     stock["ticker"],
                "name":       stock["name"],
                "weight":     stock["weight"],
                "change_pct": change_map.get(stock["ticker"]),
                "rel_volume": vol_map.get(stock["ticker"]),
            }
        )

    sector_order = [
        "Information Technology",
        "Health Care",
        "Financials",
        "Consumer Discretionary",
        "Communication Services",
        "Industrials",
        "Consumer Staples",
        "Energy",
        "Utilities",
        "Real Estate",
        "Materials",
    ]
    return [
        {"name": s, "children": sectors_dict[s]}
        for s in sector_order
        if s in sectors_dict
    ]
