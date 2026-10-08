"""
Market data service — wraps yfinance for equity tickers, routes crypto
tickers (BTC, ETH, etc.) to CoinGecko via CryptoMarketDataService.

The two providers are merged transparently: callers don't need to know
whether a ticker is equity or crypto. Routing decisions happen here.
"""

import asyncio
import math
from functools import partial

import yfinance as yf

from app.config import get_settings
from app.data.crypto import is_crypto
from app.services import yahoo_quotes
from app.services.rate_limit import spend
from app.services.crypto_market_data import (
    CryptoMarketDataService,
    filter_crypto,
    filter_non_crypto,
)

settings = get_settings()

# Simple in-process quote cache — avoids hammering Yahoo Finance on every watchlist reload.
# Keys: ticker (str).  Values: (timestamp_float, quote_dict).
import logging

log = logging.getLogger(__name__)

_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
_QUOTE_TTL = 60  # seconds

# How many symbols the batch endpoint may miss before we stop retrying
# them individually. A few misses are normal (a genuinely bad symbol, a
# listing Yahoo has no quote for). Many misses mean the provider is
# refusing, and fanning out 90 single requests in response is how a
# throttle becomes a longer throttle.
MAX_SINGLE_QUOTE_FALLBACKS = 5

# FX rate cache — longer TTL since rates don't move second-to-second.
# Keys: "FROM/TO" (str).  Values: (timestamp_float, rate_float).
_FX_CACHE: dict[str, tuple[float, float]] = {}
_FX_TTL = 300  # 5 minutes


class MarketDataService:
    async def _run_sync(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def get_quote(self, ticker: str) -> dict:
        """Single quote. Costs three provider requests (5-day history plus
        fast_info), so prefer get_quotes for anything plural — see the
        note there."""
        # Route crypto tickers (BTC, ETH, etc.) to CoinGecko
        if is_crypto(ticker):
            quote = await CryptoMarketDataService.get_quote(ticker)
            if quote is None:
                raise ValueError(f"No data for {ticker}")
            return quote

        import time as _time
        cached = _QUOTE_CACHE.get(ticker)
        if cached and (_time.monotonic() - cached[0]) < _QUOTE_TTL:
            return cached[1]

        def _fetch():
            t = yf.Ticker(ticker)
            info = t.fast_info

            def _f(v):
                """Float-cast and return None for NaN/Inf."""
                try:
                    f = float(v)
                    return f if math.isfinite(f) else None
                except (TypeError, ValueError):
                    return None

            # Primary: history(). It's usually correct across weekends/
            # holidays. But yfinance occasionally publishes NaN closes for
            # actively-traded tickers (a 2026-06-19 example: SMH).
            # Fall back to fast_info, which had real values for the same
            # tickers in those cases.
            df = t.history(period="5d", interval="1d")
            current = None
            prev = None
            volume = None
            if not df.empty:
                current = _f(df["Close"].iloc[-1])
                prev = _f(df["Close"].iloc[-2]) if len(df) >= 2 else None
                volume = _f(df["Volume"].iloc[-1])

            if current is None:
                # fast_info exposes both camelCase keys and snake_case
                # attributes depending on yfinance version.
                current = _f(
                    info.get("lastPrice")
                    or info.get("regularMarketPrice")
                    or info.get("last_price")
                )
            if prev is None:
                prev = _f(
                    info.get("previousClose")
                    or info.get("regularMarketPreviousClose")
                    or info.get("previous_close")
                )
            if volume is None:
                volume = _f(info.get("lastVolume") or info.get("last_volume"))

            if current is None:
                raise ValueError(f"No finite price for {ticker}")
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
                "volume": int(volume) if volume is not None else 0,
                "market_cap": _f(getattr(info, "market_cap", None)),
                "fifty_two_week_high": _f(getattr(info, "year_high", None)),
                "fifty_two_week_low": _f(getattr(info, "year_low", None)),
            }

        result = await self._run_sync(_fetch)
        _QUOTE_CACHE[ticker] = (_time.monotonic(), result)
        return result

    async def get_quotes(self, tickers: list[str]) -> list[dict]:
        """Quotes for many tickers in as few provider requests as possible.

        Equities go through Yahoo's batched quote endpoint: one request
        per 100 symbols, so a 90-ticker refresh costs 1 request instead
        of 270. It used to fan out to get_quote per ticker, and each of
        those spends three requests (a 5-day history plus two for
        fast_info) — on the 5-minute position refresh that alone was
        ~3,000 requests/hour, well past what the IP is allowed.

        Crypto still goes to CoinGecko in its own batched call.

        Falls back to the per-ticker path only for symbols the batch
        didn't return, and only for a handful of them: a large miss means
        the provider is unhappy, and retrying 90 symbols one at a time is
        precisely the behaviour that turns a throttle into a ban.
        """
        crypto_tickers = filter_crypto(tickers)
        equity_tickers = filter_non_crypto(tickers)

        out: list[dict] = []
        tasks: list = []

        if equity_tickers:
            fresh: list[str] = []
            import time as _time
            for t in equity_tickers:
                cached = _QUOTE_CACHE.get(t)
                if cached and (_time.monotonic() - cached[0]) < _QUOTE_TTL:
                    out.append(cached[1])
                else:
                    fresh.append(t)

            if fresh:
                try:
                    batch = await yahoo_quotes.get_many(fresh)
                except Exception as exc:
                    log.warning("Batched quote fetch failed: %s", exc)
                    batch = {}

                missing: list[str] = []
                for t in fresh:
                    row = batch.get(t.upper())
                    if row and row.get("price") is not None:
                        quote = {
                            "ticker": t,
                            "price": row["price"],
                            "previous_close": row["previous_close"] if row["previous_close"] is not None else row["price"],
                            "change": round(row["change"], 4) if row.get("change") is not None else 0.0,
                            "change_pct": round(row["change_pct"], 2) if row.get("change_pct") is not None else 0.0,
                            "volume": int(row["volume"]) if row.get("volume") is not None else 0,
                            "market_cap": row.get("market_cap"),
                            "fifty_two_week_high": row.get("fifty_two_week_high"),
                            "fifty_two_week_low": row.get("fifty_two_week_low"),
                        }
                        _QUOTE_CACHE[t] = (_time.monotonic(), quote)
                        out.append(quote)
                    else:
                        missing.append(t)

                if missing:
                    log.info(
                        "Batched quotes missed %d/%d symbol(s): %s",
                        len(missing), len(fresh), ", ".join(missing[:10]),
                    )
                    if len(missing) <= MAX_SINGLE_QUOTE_FALLBACKS:
                        tasks.extend(self.get_quote(t) for t in missing)
                    else:
                        log.warning(
                            "Skipping per-ticker fallback for %d symbols — too many "
                            "misses to retry individually without risking a throttle",
                            len(missing),
                        )

        if crypto_tickers:
            tasks.append(CryptoMarketDataService.get_quotes(crypto_tickers))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    continue
                if isinstance(r, list):
                    out.extend(r)
                else:
                    out.append(r)

        # Preserve the caller's ordering — callers zip this against their
        # own ticker list in places.
        order = {t.upper(): i for i, t in enumerate(tickers)}
        out.sort(key=lambda q: order.get(str(q.get("ticker", "")).upper(), 10_000))
        return out

    async def get_history(self, ticker: str, period: str, interval: str) -> dict:
        # Route crypto tickers to CoinGecko OHLC
        if is_crypto(ticker):
            return await CryptoMarketDataService.get_history(ticker, period, interval)

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

        equity_results = await self._run_sync(_fetch)
        # Always also surface matching crypto from the curated list (cheap, sync)
        crypto_results = CryptoMarketDataService.search(query)
        # Show crypto matches first when user is clearly typing a crypto symbol
        return crypto_results + equity_results

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
        # Crypto is always quoted in USD by CoinGecko
        if is_crypto(ticker):
            return {"ticker": ticker, "currency": "USD"}

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

    async def get_ohlcv_dataframe(
        self,
        ticker: str,
        period: str = "6mo",
        interval: str = "1d",
        budget_timeout: float | None = None,
    ):
        """Returns a pandas DataFrame — used internally by the signal engine.

        One provider request per call, and there is no batch equivalent
        for history, so this is the chokepoint every per-ticker fetch
        passes through: the signal scan (every watchlist ticker, every 15
        minutes), momentum scores, and the backtest scripts. Gating it
        here covers all of them at once.

        Returns an empty frame when the budget is unavailable rather than
        raising. Callers already treat an empty frame as "no data" — the
        signal providers skip the ticker, which is the correct response
        to "we are out of requests this hour".
        """
        if is_crypto(ticker):
            return await CryptoMarketDataService.get_ohlcv_dataframe(ticker, period, interval)

        if not await spend(1, timeout=budget_timeout):
            import pandas as pd

            log.warning(
                "Yahoo budget: skipping history for %s (%s/%s)", ticker, period, interval
            )
            return pd.DataFrame()

        def _fetch():
            return yf.Ticker(ticker).history(period=period, interval=interval)

        return await self._run_sync(_fetch)
