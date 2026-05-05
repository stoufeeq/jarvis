"""
CoinGecko-based market data for crypto tickers.

Free API tier — no key required, generous rate limits (~10–50 calls/min).
Same shape as MarketDataService methods so the two can be combined
transparently in MarketDataService.

Endpoints used:
- /coins/markets               — batch quotes (1 call for many tickers)
- /coins/{id}/ohlc?days=N      — OHLC candles for technical signals
- /coins/{id}/market_chart     — finer-grained price history if needed
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from app.data.crypto import (
    CRYPTO_MAPPING,
    get_coingecko_id,
    get_crypto_name,
    is_crypto,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"
TIMEOUT = 15.0
HEADERS = {"User-Agent": "Jarvis/1.0", "Accept": "application/json"}

# In-process quote cache (60s) to stay well under rate limits and amortise
# bursts when the dashboard pulls quotes for many tickers at once.
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
QUOTE_CACHE_TTL = 60.0

# OHLC cache (15 min) — historical OHLC doesn't change second-to-second.
_OHLC_CACHE: dict[tuple[str, int], tuple[float, list]] = {}
OHLC_CACHE_TTL = 15 * 60.0


# ── Filter helpers ───────────────────────────────────────────────────────────

def filter_crypto(tickers: list[str]) -> list[str]:
    """Return the subset of tickers that are known crypto symbols."""
    return [t for t in tickers if is_crypto(t)]


def filter_non_crypto(tickers: list[str]) -> list[str]:
    """Return the subset of tickers that are NOT known crypto symbols."""
    return [t for t in tickers if not is_crypto(t)]


# ── Service ─────────────────────────────────────────────────────────────────

class CryptoMarketDataService:
    """Stateless wrapper around CoinGecko REST API.

    Methods mirror MarketDataService's shape so both can be merged.
    """

    @staticmethod
    async def get_quote(ticker: str) -> dict | None:
        """Single-ticker quote. Equivalent shape to MarketDataService.get_quote."""
        results = await CryptoMarketDataService.get_quotes([ticker])
        return results[0] if results else None

    @staticmethod
    async def get_quotes(tickers: list[str]) -> list[dict]:
        """Batch quotes — single CoinGecko call regardless of ticker count."""
        crypto_tickers = filter_crypto(tickers)
        if not crypto_tickers:
            return []

        # Serve from cache where possible
        now = time.monotonic()
        cached: list[dict] = []
        to_fetch: list[str] = []
        for t in crypto_tickers:
            entry = _QUOTE_CACHE.get(t.upper())
            if entry and (now - entry[0]) < QUOTE_CACHE_TTL:
                cached.append(entry[1])
            else:
                to_fetch.append(t)

        fetched: list[dict] = []
        if to_fetch:
            ids = [get_coingecko_id(t) for t in to_fetch]
            ids = [i for i in ids if i]
            if ids:
                fetched = await CryptoMarketDataService._fetch_markets(ids)
                # Update cache
                for q in fetched:
                    _QUOTE_CACHE[q["ticker"].upper()] = (now, q)

        return cached + fetched

    @staticmethod
    async def _fetch_markets(coingecko_ids: list[str]) -> list[dict]:
        """Hit /coins/markets for the given CoinGecko IDs."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
                r = await client.get(
                    f"{BASE_URL}/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "ids": ",".join(coingecko_ids),
                        "price_change_percentage": "24h",
                        "per_page": len(coingecko_ids),
                    },
                )
                r.raise_for_status()
                data = r.json() or []
        except Exception as exc:
            log.warning("CoinGecko markets fetch failed: %s", exc)
            return []

        # Map CoinGecko id → our ticker symbol
        id_to_ticker = {v["id"]: k for k, v in CRYPTO_MAPPING.items()}

        out: list[dict] = []
        for d in data:
            ticker = id_to_ticker.get(d.get("id"))
            if not ticker:
                continue
            price = d.get("current_price")
            if price is None or not math.isfinite(float(price)):
                continue

            change_pct_24h = d.get("price_change_percentage_24h")
            change_24h = d.get("price_change_24h")

            # CoinGecko's "previous_close" equivalent: price 24h ago.
            # Used by the rest of the app for "Today's Change" calculation.
            if change_pct_24h is not None and change_pct_24h != -100:
                previous_close = float(price) / (1 + float(change_pct_24h) / 100.0)
            else:
                previous_close = float(price)

            out.append({
                "ticker": ticker,
                "price": float(price),
                "previous_close": round(previous_close, 8),
                "change": float(change_24h) if change_24h is not None else 0.0,
                "change_pct": float(change_pct_24h) if change_pct_24h is not None else 0.0,
                "volume": int(d.get("total_volume") or 0),
                "market_cap": d.get("market_cap"),
                "fifty_two_week_high": float(d.get("ath") or 0.0),
                "fifty_two_week_low":  float(d.get("atl") or 0.0),
            })
        return out

    # ── History (OHLC candles) ────────────────────────────────────────────────

    @staticmethod
    async def get_history(ticker: str, period: str = "3mo", interval: str = "1d") -> dict:
        """OHLC candles. Returns same shape as MarketDataService.get_history.

        CoinGecko's /coins/{id}/ohlc supports days={1,7,14,30,90,180,365}.
        We pick the closest day count to the requested period.
        """
        cg_id = get_coingecko_id(ticker)
        if not cg_id:
            return {"ticker": ticker, "period": period, "interval": interval, "candles": []}

        days = _period_to_days(period)

        # Cache lookup
        cache_key = (cg_id, days)
        now = time.monotonic()
        cached = _OHLC_CACHE.get(cache_key)
        if cached and (now - cached[0]) < OHLC_CACHE_TTL:
            return {
                "ticker": ticker,
                "period": period,
                "interval": interval,
                "candles": cached[1],
            }

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
                r = await client.get(
                    f"{BASE_URL}/coins/{cg_id}/ohlc",
                    params={"vs_currency": "usd", "days": days},
                )
                r.raise_for_status()
                raw = r.json() or []
        except Exception as exc:
            log.warning("CoinGecko OHLC fetch failed for %s: %s", ticker, exc)
            return {"ticker": ticker, "period": period, "interval": interval, "candles": []}

        # CoinGecko OHLC doesn't include volume — fetch market_chart for volume.
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
                r2 = await client.get(
                    f"{BASE_URL}/coins/{cg_id}/market_chart",
                    params={"vs_currency": "usd", "days": days, "interval": "daily" if days >= 90 else None},
                )
                r2.raise_for_status()
                vol_data = r2.json() or {}
        except Exception:
            vol_data = {}

        volumes = vol_data.get("total_volumes") or []
        # Build a lookup: date string → volume (sum of any same-day rows)
        vol_map: dict[str, float] = {}
        for ts_ms, v in volumes:
            d = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date().isoformat()
            vol_map[d] = vol_map.get(d, 0.0) + float(v or 0)

        # Convert OHLC rows: [timestamp_ms, open, high, low, close]
        candles: list[dict] = []
        seen: set[str] = set()
        for row in raw:
            if len(row) < 5:
                continue
            ts_ms, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
            d = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date().isoformat()
            if d in seen:
                # Keep the latest value for the day
                candles[-1] = {
                    "time": d,
                    "open":  round(float(o), 4),
                    "high":  round(float(h), 4),
                    "low":   round(float(l), 4),
                    "close": round(float(c), 4),
                    "volume": int(vol_map.get(d, 0)),
                }
                continue
            seen.add(d)
            candles.append({
                "time": d,
                "open":  round(float(o), 4),
                "high":  round(float(h), 4),
                "low":   round(float(l), 4),
                "close": round(float(c), 4),
                "volume": int(vol_map.get(d, 0)),
            })

        _OHLC_CACHE[cache_key] = (now, candles)
        return {"ticker": ticker, "period": period, "interval": interval, "candles": candles}

    @staticmethod
    async def get_ohlcv_dataframe(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """Pandas DataFrame for the technical signal engine — matches the shape
        that yfinance returns (columns: Open, High, Low, Close, Volume; DatetimeIndex)."""
        history = await CryptoMarketDataService.get_history(ticker, period=period, interval=interval)
        candles = history.get("candles", [])
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)
        df["Date"] = pd.to_datetime(df["time"])
        df = df.set_index("Date")
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        return df[["Open", "High", "Low", "Close", "Volume"]]

    # ── Search / metadata ──────────────────────────────────────────────────────

    @staticmethod
    def search(query: str) -> list[dict]:
        """Search the curated crypto list for matches on symbol or name."""
        q = query.strip().upper()
        if not q:
            return []
        out: list[dict] = []
        for symbol, info in CRYPTO_MAPPING.items():
            if q in symbol or q.lower() in info["name"].lower():
                out.append({
                    "ticker": symbol,
                    "name": info["name"],
                    "exchange": "Crypto",
                    "type": "crypto",
                })
        return out

    @staticmethod
    def get_currency(_ticker: str) -> str:
        """All crypto quotes are in USD via CoinGecko."""
        return "USD"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _period_to_days(period: str) -> int:
    """Map yfinance-style period strings to CoinGecko 'days' parameter.
    CoinGecko free accepts: 1, 7, 14, 30, 90, 180, 365."""
    p = period.lower().strip()
    mapping = {
        "1d":  1, "5d":  7, "1mo": 30, "3mo": 90, "6mo": 180,
        "1y":  365, "2y": 365, "ytd": 180, "max": 365,
    }
    return mapping.get(p, 90)
