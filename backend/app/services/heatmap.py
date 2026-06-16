"""
Heatmap service — batch-fetches S&P 500 quotes and builds a sector tree
suitable for rendering as a Recharts Treemap (heatmap) or ScatterChart
(bubbles) on the frontend.

Two data sources, used independently:
- yf.download(period="1mo", interval="1d") — used ONLY for the 20-day
  volume series (for the relative-volume bubble-chart axis).
- yfinance Ticker.fast_info — used for change_pct via (lastPrice -
  previousClose) / previousClose. The historical-bar feed occasionally
  drops valid trading days (a 2026-06-15 example caused WDC to show
  Friday-to-Tuesday move instead of Monday-to-Tuesday), but fast_info's
  previousClose is reliable.

fast_info is per-ticker (no batch endpoint), so we parallelise with a
ThreadPoolExecutor. Combined with the volume fetch, a cold heatmap takes
~10-15s; warm hits return from the 30-min in-process cache instantly.
"""

import asyncio
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.data.sp500 import SP500

log = logging.getLogger(__name__)

CACHE_TTL = 1800  # 30 minutes — aligned with frontend staleTime and Celery pre-warm interval

# fast_info per-ticker is sequential by default. yfinance's underlying
# requests session is thread-safe enough for ~10 concurrent calls without
# rate-limit issues in practice.
FAST_INFO_WORKERS = 10

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


def _change_pct_via_fast_info(ticker: str) -> float | None:
    """Return today's change_pct using yfinance fast_info, or None on error.

    fast_info exposes previousClose (yesterday's settled close, even when
    the historical-bar download() is missing that day) and lastPrice
    (current intraday quote). Both are needed for an accurate same-day
    change calculation.
    """
    import yfinance as yf

    try:
        fi = yf.Ticker(ticker).fast_info
        # fast_info is dict-like; keys can vary across yfinance versions.
        prev = fi.get("previousClose") or fi.get("regularMarketPreviousClose")
        last = fi.get("lastPrice") or fi.get("regularMarketPrice") or fi.get("last_price")
        if prev is None or last is None:
            return None
        prev_f = float(prev)
        last_f = float(last)
        if prev_f <= 0 or not math.isfinite(prev_f) or not math.isfinite(last_f):
            return None
        return round((last_f - prev_f) / prev_f * 100, 2)
    except Exception:
        # yfinance throws all kinds of things (HTTP errors, JSON decode,
        # delisted tickers). Caller falls back to download-derived value.
        return None


def _fetch_heatmap_sync() -> dict:
    import yfinance as yf

    tickers = [s["ticker"] for s in SP500]

    change_map: dict[str, float | None] = {}
    vol_map: dict[str, float | None] = {}
    download_change_fallback: dict[str, float | None] = {}

    # ── Volume series (download) ──────────────────────────────────────────
    # 1mo of daily bars for the 20-day volume average. Also kept as a
    # fallback source of change_pct in case fast_info fails for a ticker.
    download_error: str | None = None
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
            # ── change % fallback ─────────────────────────────────────────
            try:
                series = (closes if len(tickers) == 1 else closes[ticker]).dropna()
                if len(series) >= 2:
                    prev = float(series.iloc[-2])
                    curr = float(series.iloc[-1])
                    if prev and math.isfinite(prev) and math.isfinite(curr):
                        download_change_fallback[ticker] = round((curr - prev) / prev * 100, 2)
                    else:
                        download_change_fallback[ticker] = None
                elif len(series) == 1:
                    download_change_fallback[ticker] = 0.0
                else:
                    download_change_fallback[ticker] = None
            except (KeyError, IndexError, TypeError, ValueError):
                download_change_fallback[ticker] = None

            # ── relative volume ───────────────────────────────────────────
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
        log.warning("Heatmap volume/fallback download failed: %s", exc)
        download_error = str(exc)

    # ── change_pct via fast_info (parallel) ───────────────────────────────
    # Per-ticker call but threadpool keeps it ~10s total. fast_info has
    # previousClose / lastPrice that the historical-bar feed sometimes
    # misses (e.g. 2026-06-15 was missing from download but present here).
    fast_results: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=FAST_INFO_WORKERS) as exe:
        fast_results = dict(zip(tickers, exe.map(_change_pct_via_fast_info, tickers)))

    # Prefer fast_info; fall back to download-derived value when missing.
    fast_info_hits = 0
    for ticker in tickers:
        from_fast = fast_results.get(ticker)
        if from_fast is not None:
            change_map[ticker] = from_fast
            fast_info_hits += 1
        else:
            change_map[ticker] = download_change_fallback.get(ticker)

    log.info(
        "Heatmap fetched: %d/%d via fast_info, rest from download fallback",
        fast_info_hits, len(tickers),
    )

    payload: dict[str, Any] = {
        "sectors": _build_sectors(change_map, vol_map),
        "cached_at": time.time(),
    }
    if download_error:
        payload["error"] = download_error
    return payload


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
