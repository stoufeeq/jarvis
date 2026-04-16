"""
Heatmap service — batch-fetches S&P 500 quotes and builds a sector tree
suitable for rendering as a Recharts Treemap on the frontend.

Results are cached in-process for CACHE_TTL seconds to avoid hammering
Yahoo Finance on every page load. The cache is shared across all requests
within the same API worker process.
"""

import asyncio
import math
import time
from typing import Any

from app.data.sp500 import SP500

CACHE_TTL = 120  # seconds — cached result served for 2 minutes

_cache: dict[str, Any] = {}


class HeatmapService:
    async def get_sp500_heatmap(self) -> dict:
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
    try:
        df = yf.download(
            tickers,
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        closes = df["Close"] if "Close" in df.columns else df.get("close")
        if closes is None:
            raise ValueError("No Close column in download result")

        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    series = closes.dropna()
                else:
                    series = closes[ticker].dropna()
                if len(series) >= 2:
                    prev = float(series.iloc[-2])
                    curr = float(series.iloc[-1])
                    if prev and math.isfinite(prev) and math.isfinite(curr):
                        change_map[ticker] = round((curr - prev) / prev * 100, 2)
                    else:
                        change_map[ticker] = None
                elif len(series) == 1:
                    change_map[ticker] = 0.0
                else:
                    change_map[ticker] = None
            except (KeyError, IndexError, TypeError, ValueError):
                change_map[ticker] = None

    except Exception as exc:
        # Return structure with no change data rather than failing the endpoint
        return {
            "sectors": _build_sectors(change_map),
            "cached_at": time.time(),
            "error": str(exc),
        }

    return {
        "sectors": _build_sectors(change_map),
        "cached_at": time.time(),
    }


def _build_sectors(change_map: dict[str, float | None]) -> list[dict]:
    sectors_dict: dict[str, list[dict]] = {}
    for stock in SP500:
        sector = stock["sector"]
        if sector not in sectors_dict:
            sectors_dict[sector] = []
        sectors_dict[sector].append(
            {
                "ticker": stock["ticker"],
                "name": stock["name"],
                "weight": stock["weight"],
                "change_pct": change_map.get(stock["ticker"]),
            }
        )

    # Preserve a consistent sector order
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
