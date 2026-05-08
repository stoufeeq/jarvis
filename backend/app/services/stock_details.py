"""
Stock details service — fetches comprehensive valuation/growth/technical/IV
data on demand for the Stock Browser feature. No DB writes.

Used by GET /market/details/{ticker}. Caches results per ticker for 5
minutes so repeated visits to the same ticker are instant.

All slow yfinance calls (Ticker.info, option_chain, OHLCV history) are
parallelised with asyncio.gather so the endpoint response time = max of
the slowest call (~2-3s), not the sum.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator

from app.data.crypto import is_crypto
from app.services.crypto_market_data import CryptoMarketDataService
from app.services.market_data import MarketDataService
from app.services.options_analytics import OptionsAnalyticsService

log = logging.getLogger(__name__)

# 5-minute in-process cache per ticker
_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 5 * 60  # 5 minutes


class StockDetailsService:
    @staticmethod
    async def get_details(ticker: str) -> dict[str, Any]:
        """Return comprehensive details for `ticker`. 5-min cached."""
        ticker = ticker.upper().strip()

        cached = _CACHE.get(ticker)
        if cached and (time.monotonic() - cached[0]) < CACHE_TTL:
            return cached[1]

        # Crypto path: no fundamentals/options/analyst, just price + technicals
        if is_crypto(ticker):
            details = await _build_crypto_details(ticker)
        else:
            details = await _build_equity_details(ticker)

        _CACHE[ticker] = (time.monotonic(), details)
        return details


# ── Equity path ─────────────────────────────────────────────────────────────


async def _build_equity_details(ticker: str) -> dict[str, Any]:
    mds = MarketDataService()
    loop = asyncio.get_event_loop()

    # Fan out: quote, info, history, IV summary in parallel
    results = await asyncio.gather(
        mds.get_quote(ticker),
        loop.run_in_executor(None, _fetch_info, ticker),
        loop.run_in_executor(None, _fetch_history, ticker),
        OptionsAnalyticsService.get_iv_summary(ticker),
        return_exceptions=True,
    )
    quote = results[0] if not isinstance(results[0], Exception) else {}
    info = results[1] if not isinstance(results[1], Exception) else {}
    history_df = results[2] if not isinstance(results[2], Exception) else None
    iv = results[3] if not isinstance(results[3], Exception) else None

    technicals = _compute_technicals(history_df, info) if history_df is not None else _empty_technicals()
    options_listed = bool(info.get("_options_listed", False))

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or info.get("displayName") or ticker,
        "asset_type": "crypto" if is_crypto(ticker) else (info.get("quoteType") or "stock").lower(),
        "exchange": info.get("exchange") or info.get("fullExchangeName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": (info.get("currency") or quote.get("currency") or "USD").upper(),
        "quote": _build_quote_section(quote, info),
        "valuation": _build_valuation(info),
        "growth": _build_growth(info),
        "technicals": technicals,
        "iv_analytics": iv if options_listed else None,
        "analyst": _build_analyst(info, quote.get("price")),
        "options_listed": options_listed,
    }


def _fetch_info(ticker: str) -> dict[str, Any]:
    """Fetch yfinance Ticker.info synchronously. Slow (~2s). Don't call in async."""
    try:
        t = yf.Ticker(ticker)
        info = dict(t.info or {})
        # Sniff for options availability cheaply (a tuple lookup)
        try:
            options_listed = bool(t.options)
        except Exception:
            options_listed = False
        info["_options_listed"] = options_listed
        return info
    except Exception as exc:
        log.warning("Ticker.info failed for %s: %s", ticker, exc)
        return {}


def _fetch_history(ticker: str):
    """Fetch ~10 months of daily OHLCV for technical computations."""
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="10mo", interval="1d")
        if df is None or df.empty:
            return None
        return df
    except Exception as exc:
        log.warning("history fetch failed for %s: %s", ticker, exc)
        return None


def _build_quote_section(quote: dict, info: dict) -> dict:
    """Header data — price, ranges, volume, market cap."""
    return {
        "price": _f(quote.get("price")),
        "previous_close": _f(quote.get("previous_close")),
        "change": _f(quote.get("change")),
        "change_pct": _f(quote.get("change_pct")),
        "day_low": _f(info.get("dayLow") or info.get("regularMarketDayLow")),
        "day_high": _f(info.get("dayHigh") or info.get("regularMarketDayHigh")),
        "volume": int(info.get("regularMarketVolume") or info.get("volume") or 0),
        "avg_volume": int(info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0),
        "market_cap": _i(info.get("marketCap")),
        "fifty_two_week_high": _f(quote.get("fifty_two_week_high")),
        "fifty_two_week_low": _f(quote.get("fifty_two_week_low")),
    }


def _build_valuation(info: dict) -> dict:
    return {
        "pe_trailing": _f(info.get("trailingPE")),
        "pe_forward": _f(info.get("forwardPE")),
        "pb_ratio": _f(info.get("priceToBook")),
        "peg_ratio": _f(info.get("pegRatio") or info.get("trailingPegRatio")),
        "ev_ebitda": _f(info.get("enterpriseToEbitda")),
        "eps_trailing": _f(info.get("trailingEps")),
        "eps_forward": _f(info.get("forwardEps")),
        # yfinance has two dividend yield fields with different units:
        # - trailingAnnualDividendYield: decimal fraction (0.0036 = 0.36%) — preferred, predictable
        # - dividendYield: already a percentage in newer yfinance (0.38 = 0.38%) — fallback only
        "dividend_yield_pct": _dividend_yield_pct(info),
        "payout_ratio_pct": _pct(info.get("payoutRatio")),
    }


def _build_growth(info: dict) -> dict:
    return {
        "revenue_ttm": _i(info.get("totalRevenue")),
        "revenue_growth_pct": _pct(info.get("revenueGrowth")),
        "earnings_growth_pct": _pct(info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")),
        "net_margin_pct": _pct(info.get("profitMargins")),
        "operating_margin_pct": _pct(info.get("operatingMargins")),
        "roe_pct": _pct(info.get("returnOnEquity")),
        "free_cash_flow": _i(info.get("freeCashflow")),
        "debt_to_equity": _f(info.get("debtToEquity")),
    }


def _empty_technicals() -> dict:
    return {
        "rsi14": None,
        "macd_signal": None,
        "sma50": None,
        "sma200": None,
        "above_sma50": None,
        "above_sma200": None,
        "beta": None,
    }


def _compute_technicals(df, info: dict) -> dict:
    """RSI, MACD signal direction, SMA crossover info."""
    out: dict[str, Any] = _empty_technicals()
    out["beta"] = _f(info.get("beta"))

    if df is None or df.empty:
        return out

    closes = df["Close"]
    if len(closes) < 15:
        return out

    last_close = float(closes.iloc[-1])

    # RSI 14
    try:
        rsi = RSIIndicator(closes, window=14).rsi()
        last_rsi = float(rsi.iloc[-1])
        if math.isfinite(last_rsi):
            out["rsi14"] = round(last_rsi, 2)
    except Exception:
        pass

    # MACD signal direction (bullish/bearish/neutral based on histogram)
    try:
        macd = MACD(closes, window_fast=12, window_slow=26, window_sign=9)
        hist = macd.macd_diff()
        if len(hist) >= 2 and math.isfinite(float(hist.iloc[-1])) and math.isfinite(float(hist.iloc[-2])):
            curr, prev = float(hist.iloc[-1]), float(hist.iloc[-2])
            if curr > 0 and curr > prev:
                out["macd_signal"] = "bullish"
            elif curr < 0 and curr < prev:
                out["macd_signal"] = "bearish"
            else:
                out["macd_signal"] = "neutral"
    except Exception:
        pass

    # SMA 50 / 200
    if len(closes) >= 50:
        try:
            sma50 = float(SMAIndicator(closes, window=50).sma_indicator().iloc[-1])
            if math.isfinite(sma50):
                out["sma50"] = round(sma50, 2)
                out["above_sma50"] = last_close > sma50
        except Exception:
            pass
    if len(closes) >= 200:
        try:
            sma200 = float(SMAIndicator(closes, window=200).sma_indicator().iloc[-1])
            if math.isfinite(sma200):
                out["sma200"] = round(sma200, 2)
                out["above_sma200"] = last_close > sma200
        except Exception:
            pass

    return out


def _build_analyst(info: dict, current_price: float | None) -> dict:
    target_mean = _f(info.get("targetMeanPrice"))
    upside_pct = None
    if target_mean and current_price and current_price > 0:
        upside_pct = round((target_mean - current_price) / current_price * 100, 2)
    return {
        "recommendation_key": info.get("recommendationKey"),  # 'buy', 'hold', etc.
        "recommendation_mean": _f(info.get("recommendationMean")),  # 1.0–5.0 (1=Strong Buy)
        "n_analysts": _i(info.get("numberOfAnalystOpinions")),
        "target_mean": target_mean,
        "target_high": _f(info.get("targetHighPrice")),
        "target_low": _f(info.get("targetLowPrice")),
        "upside_pct": upside_pct,
    }


# ── Crypto path ─────────────────────────────────────────────────────────────


async def _build_crypto_details(ticker: str) -> dict[str, Any]:
    """Crypto has no fundamentals, options, or analyst data — just price
    and technicals from CoinGecko OHLC."""
    mds = MarketDataService()
    quote = await mds.get_quote(ticker)
    df = await CryptoMarketDataService.get_ohlcv_dataframe(ticker, period="6mo", interval="1d")
    technicals = _compute_technicals(df, {}) if df is not None and not df.empty else _empty_technicals()

    from app.data.crypto import get_crypto_name
    name = get_crypto_name(ticker) or ticker

    return {
        "ticker": ticker,
        "name": name,
        "asset_type": "crypto",
        "exchange": "Crypto",
        "sector": None,
        "industry": None,
        "currency": "USD",
        "quote": {
            "price": quote.get("price"),
            "previous_close": quote.get("previous_close"),
            "change": quote.get("change"),
            "change_pct": quote.get("change_pct"),
            "day_low": None,
            "day_high": None,
            "volume": int(quote.get("volume") or 0),
            "avg_volume": 0,
            "market_cap": quote.get("market_cap"),
            "fifty_two_week_high": quote.get("fifty_two_week_high"),
            "fifty_two_week_low": quote.get("fifty_two_week_low"),
        },
        "valuation": None,  # not applicable for crypto
        "growth": None,
        "technicals": technicals,
        "iv_analytics": None,
        "analyst": None,
        "options_listed": False,
    }


# ── Helpers ─────────────────────────────────────────────────────────────────


def _f(val) -> float | None:
    """Float-cast, returning None for NaN/Inf/None."""
    try:
        if val is None:
            return None
        f = float(val)
        return round(f, 4) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _i(val) -> int | None:
    """Int-cast, returning None for NaN/None."""
    try:
        if val is None:
            return None
        f = float(val)
        return int(f) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _pct(val) -> float | None:
    """Convert a 0–1 ratio to a percent. Returns None for missing/invalid."""
    f = _f(val)
    return round(f * 100, 2) if f is not None else None


def _dividend_yield_pct(info: dict) -> float | None:
    """Resolve dividend yield to a percentage, handling yfinance's mixed
    conventions: trailingAnnualDividendYield is a fraction, dividendYield
    is already a percentage in newer yfinance versions."""
    trailing = _f(info.get("trailingAnnualDividendYield"))
    if trailing is not None and 0 <= trailing <= 1:
        return round(trailing * 100, 2)
    fwd = _f(info.get("dividendYield"))
    if fwd is not None and 0 <= fwd <= 100:
        return round(fwd, 2)
    return None
