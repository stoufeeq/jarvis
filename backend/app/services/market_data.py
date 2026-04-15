"""
Market data service — wraps yfinance for MVP.
Swap out the provider by replacing the methods below with Polygon.io calls.
"""

import asyncio
import math
from functools import partial

import yfinance as yf

from app.config import get_settings

settings = get_settings()

# Simple in-process quote cache — avoids hammering Yahoo Finance on every watchlist reload.
# Keys: ticker (str).  Values: (timestamp_float, quote_dict).
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
_QUOTE_TTL = 60  # seconds

# FX rate cache — longer TTL since rates don't move second-to-second.
# Keys: "FROM/TO" (str).  Values: (timestamp_float, rate_float).
_FX_CACHE: dict[str, tuple[float, float]] = {}
_FX_TTL = 300  # 5 minutes


class MarketDataService:
    async def _run_sync(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def get_quote(self, ticker: str) -> dict:
        import time as _time
        cached = _QUOTE_CACHE.get(ticker)
        if cached and (_time.monotonic() - cached[0]) < _QUOTE_TTL:
            return cached[1]

        def _fetch():
            t = yf.Ticker(ticker)
            # history() is more reliable than fast_info across weekends/holidays
            df = t.history(period="5d", interval="1d")
            if df.empty:
                raise ValueError(f"No data for {ticker}")

            def _f(v):
                """Float-cast and return None for NaN/Inf."""
                try:
                    f = float(v)
                    return f if math.isfinite(f) else None
                except (TypeError, ValueError):
                    return None

            info = t.fast_info
            current = _f(df["Close"].iloc[-1])
            if current is None:
                raise ValueError(f"No finite price for {ticker}")
            prev = _f(df["Close"].iloc[-2]) if len(df) >= 2 else current
            if prev is None:
                prev = current
            change = round(current - prev, 4)
            change_pct = round((change / prev) * 100, 2) if prev else 0.0

            return {
                "ticker": ticker,
                "price": current,
                "previous_close": prev,
                "change": change,
                "change_pct": change_pct,
                "volume": int(df["Volume"].iloc[-1]),
                "market_cap": _f(getattr(info, "market_cap", None)),
                "fifty_two_week_high": _f(getattr(info, "year_high", None)),
                "fifty_two_week_low": _f(getattr(info, "year_low", None)),
            }

        result = await self._run_sync(_fetch)
        _QUOTE_CACHE[ticker] = (_time.monotonic(), result)
        return result

    async def get_quotes(self, tickers: list[str]) -> list[dict]:
        tasks = [self.get_quote(t) for t in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception)]

    async def get_history(self, ticker: str, period: str, interval: str) -> dict:
        INTRADAY = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}

        def _fetch():
            t = yf.Ticker(ticker)
            df = t.history(period=period, interval=interval)
            seen = set()
            candles = []
            for ts, row in df.iterrows():
                # Intraday: Unix timestamp (seconds). Daily+: plain date string.
                if interval in INTRADAY:
                    key = int(ts.timestamp())
                else:
                    key = str(ts)[:10]
                if key in seen:
                    continue
                seen.add(key)
                candles.append({
                    "time": key,
                    "open": round(float(row["Open"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                    "close": round(float(row["Close"]), 4),
                    "volume": int(row["Volume"]),
                })
            return {"ticker": ticker, "period": period, "interval": interval, "candles": candles}

        return await self._run_sync(_fetch)

    async def search(self, query: str) -> list[dict]:
        def _fetch():
            results = yf.Search(query, max_results=10)
            quotes = results.quotes if hasattr(results, "quotes") else []
            return [
                {
                    "ticker": q.get("symbol"),
                    "name": q.get("longname") or q.get("shortname"),
                    "exchange": q.get("exchange"),
                    "type": q.get("quoteType"),
                }
                for q in quotes
                if q.get("symbol")
            ]

        return await self._run_sync(_fetch)

    async def get_fx_rates(self, currencies: list[str], base: str = "USD") -> dict[str, float]:
        """Return {currency: rate_to_base} for each foreign currency.
        E.g. {"EUR": 1.08, "GBP": 1.27} means 1 EUR = 1.08 USD.

        Results are cached for 5 minutes.  On fetch failure the last cached
        value is returned so callers never silently receive a 1:1 default.
        """
        import time as _time

        base_u = base.upper()
        foreign = [c.upper() for c in set(currencies) if c.upper() != base_u]
        if not foreign:
            return {}

        now = _time.monotonic()
        result: dict[str, float] = {}
        to_fetch: list[str] = []

        for ccy in foreign:
            cache_key = f"{ccy}/{base_u}"
            cached = _FX_CACHE.get(cache_key)
            if cached and (now - cached[0]) < _FX_TTL:
                result[ccy] = cached[1]
            else:
                to_fetch.append(ccy)

        if not to_fetch:
            return result

        def _fetch():
            fresh: dict[str, float] = {}
            for ccy in to_fetch:
                pair = f"{ccy}{base_u}=X"
                try:
                    df = yf.Ticker(pair).history(period="1d")
                    if not df.empty:
                        rate = float(df["Close"].iloc[-1])
                        if math.isfinite(rate) and rate > 0:
                            fresh[ccy] = rate
                except Exception:
                    pass
            return fresh

        fetched = await self._run_sync(_fetch)

        for ccy in to_fetch:
            if ccy in fetched:
                # Fresh rate — update cache and result
                _FX_CACHE[f"{ccy}/{base_u}"] = (now, fetched[ccy])
                result[ccy] = fetched[ccy]
            else:
                # Fetch failed — use stale cached value if available (beats 1:1 default)
                cache_key = f"{ccy}/{base_u}"
                stale = _FX_CACHE.get(cache_key)
                if stale:
                    result[ccy] = stale[1]
                # If never cached, omit the key so callers know rate is unavailable

        return result

    async def get_currency(self, ticker: str) -> dict:
        def _fetch():
            info = yf.Ticker(ticker).fast_info
            raw = getattr(info, "currency", "USD") or "USD"
            # LSE quotes in pence (GBp) — normalise to GBP
            currency = "GBP" if raw == "GBp" else raw.upper()
            return {"ticker": ticker, "currency": currency}

        return await self._run_sync(_fetch)

    def get_cached_quote(self, ticker: str) -> dict | None:
        """Return the last cached quote dict without making any HTTP call.
        Returns None if the ticker hasn't been fetched yet this session."""
        cached = _QUOTE_CACHE.get(ticker)
        return cached[1] if cached else None

    async def get_ohlcv_dataframe(self, ticker: str, period: str = "6mo", interval: str = "1d"):
        """Returns a pandas DataFrame — used internally by the signal engine."""
        def _fetch():
            return yf.Ticker(ticker).history(period=period, interval=interval)

        return await self._run_sync(_fetch)
