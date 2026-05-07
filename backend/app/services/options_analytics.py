"""
Options analytics — implied volatility analysis built on yfinance option chains.

Computes:
- ATM implied volatility (avg of ATM call + put IV)
- Historical volatility (20-day annualized stdev of log returns)
- IV/HV ratio (vol expensiveness)
- Implied move (from ATM straddle pricing)
- Vol skew (put IV vs call IV at equivalent OTM strikes)
- Days to next earnings (for IV crush warning)

This is Phase 1 of the Black-Scholes IV roadmap. Phase 2 (signal provider)
consumes these analytics. py_vollib not used — yfinance already returns
pre-computed IV per contract, and HV is straightforward numpy.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import yfinance as yf

log = logging.getLogger(__name__)

# Caching — IV/HV change slowly intraday; daily-grain computations are fine
_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 15 * 60  # 15 minutes

# How far around ATM to consider for "ATM strike" averaging
ATM_STRIKE_BAND = 0.025  # ±2.5%

# Skew comparison: compare ~10% OTM put vs ~10% OTM call IV
SKEW_OTM_DISTANCE = 0.10


class OptionsAnalyticsService:
    """Stateless service for IV/HV/skew analytics on a ticker."""

    @staticmethod
    async def get_iv_summary(ticker: str) -> dict[str, Any] | None:
        """Compute a snapshot of IV-related analytics for `ticker`.

        Returns None if the ticker has no listed options or insufficient
        data to compute. Otherwise returns a dict with:
          - atm_iv        : float (annualized, e.g. 0.32 = 32%)
          - hv_20         : float (annualized, e.g. 0.28 = 28%)
          - iv_hv_ratio   : float
          - implied_move_pct  : float (% expected move by next monthly expiry)
          - skew          : float (put_iv - call_iv at ~10% OTM, in vol points)
          - days_to_earnings  : int | None
          - expiry_used   : str (date string)
          - current_price : float
        """
        ticker = ticker.upper()
        cached = _CACHE.get(ticker)
        if cached and (time.monotonic() - cached[0]) < CACHE_TTL:
            return cached[1]

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _compute_sync, ticker)
        if result is not None:
            _CACHE[ticker] = (time.monotonic(), result)
        return result


# ── Sync computation (off-loaded to thread pool) ─────────────────────────────


def _compute_sync(ticker: str) -> dict[str, Any] | None:
    try:
        t = yf.Ticker(ticker)

        # 1. Get current price
        try:
            spot = float(t.fast_info.last_price or 0)
        except Exception:
            spot = 0.0
        if spot <= 0:
            log.debug("No price for %s — skipping IV analytics", ticker)
            return None

        # 2. Pick the nearest monthly-style expiry (>=14 days, <=45 days)
        expirations = list(t.options or [])
        if not expirations:
            return None  # No listed options

        today = datetime.now(timezone.utc).date()
        suitable = [
            exp for exp in expirations
            if 14 <= (datetime.strptime(exp, "%Y-%m-%d").date() - today).days <= 45
        ]
        chosen_expiry = suitable[0] if suitable else expirations[0]
        days_to_expiry = (datetime.strptime(chosen_expiry, "%Y-%m-%d").date() - today).days
        if days_to_expiry <= 0:
            return None

        # 3. Fetch the option chain for that expiry
        try:
            chain = t.option_chain(chosen_expiry)
            calls = chain.calls
            puts = chain.puts
        except Exception as exc:
            log.warning("option_chain(%s, %s) failed: %s", ticker, chosen_expiry, exc)
            return None

        if calls.empty or puts.empty:
            return None

        # 4. ATM IV: average IV of contracts within ATM_STRIKE_BAND of spot
        atm_low = spot * (1 - ATM_STRIKE_BAND)
        atm_high = spot * (1 + ATM_STRIKE_BAND)

        atm_calls = calls[(calls["strike"] >= atm_low) & (calls["strike"] <= atm_high)]
        atm_puts = puts[(puts["strike"] >= atm_low) & (puts["strike"] <= atm_high)]

        atm_call_iv = _mean_finite(atm_calls["impliedVolatility"])
        atm_put_iv = _mean_finite(atm_puts["impliedVolatility"])

        atm_ivs = [v for v in (atm_call_iv, atm_put_iv) if v and 0 < v < 5]
        if not atm_ivs:
            return None
        atm_iv = sum(atm_ivs) / len(atm_ivs)

        # 5. Historical volatility (20-day annualized)
        hv_20 = _historical_volatility(t, days=20)

        iv_hv_ratio = (atm_iv / hv_20) if hv_20 and hv_20 > 0 else None

        # 6. Implied move from ATM straddle: (call_mid + put_mid) / spot
        implied_move_pct = _implied_move(atm_calls, atm_puts, spot)

        # 7. Skew: put IV at ~10% OTM vs call IV at ~10% OTM
        otm_put_strike = spot * (1 - SKEW_OTM_DISTANCE)
        otm_call_strike = spot * (1 + SKEW_OTM_DISTANCE)
        skew_put_iv = _closest_iv(puts, otm_put_strike)
        skew_call_iv = _closest_iv(calls, otm_call_strike)
        skew = (skew_put_iv - skew_call_iv) if (skew_put_iv and skew_call_iv) else None

        # 8. Days to next earnings (for IV crush warning)
        days_to_earnings = _days_to_earnings(t, today)

        return {
            "atm_iv": round(atm_iv, 4),
            "hv_20": round(hv_20, 4) if hv_20 else None,
            "iv_hv_ratio": round(iv_hv_ratio, 3) if iv_hv_ratio else None,
            "implied_move_pct": round(implied_move_pct, 3) if implied_move_pct else None,
            "skew": round(skew, 4) if skew else None,
            "days_to_earnings": days_to_earnings,
            "expiry_used": chosen_expiry,
            "days_to_expiry": days_to_expiry,
            "current_price": round(spot, 4),
        }

    except Exception as exc:
        log.warning("IV analytics failed for %s: %s", ticker, exc)
        return None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mean_finite(series) -> float | None:
    """Mean of a series, ignoring NaN/Inf and returning None if empty."""
    if series is None or len(series) == 0:
        return None
    arr = np.array(series.dropna().astype(float), dtype=float)
    arr = arr[np.isfinite(arr)]
    arr = arr[(arr > 0) & (arr < 5)]  # filter out absurd IVs
    return float(arr.mean()) if len(arr) > 0 else None


def _historical_volatility(t: yf.Ticker, days: int = 20) -> float | None:
    """Annualized historical volatility from N+1 daily closes."""
    try:
        # Need ~N+5 days to have N log returns after weekend/holiday gaps
        df = t.history(period=f"{days * 2}d", interval="1d")
        if df is None or df.empty or len(df) < days:
            return None
        closes = df["Close"].tail(days + 1).values
        log_returns = np.diff(np.log(closes))
        log_returns = log_returns[np.isfinite(log_returns)]
        if len(log_returns) < 5:
            return None
        daily_vol = float(np.std(log_returns, ddof=1))
        return daily_vol * math.sqrt(252)  # annualize
    except Exception:
        return None


def _implied_move(atm_calls, atm_puts, spot: float) -> float | None:
    """Implied move % from ATM straddle: (call_mid + put_mid) / spot."""
    if atm_calls.empty or atm_puts.empty:
        return None
    # Use the closest-to-ATM strike from each side
    closest_call = atm_calls.iloc[(atm_calls["strike"] - spot).abs().argsort().iloc[:1]]
    closest_put = atm_puts.iloc[(atm_puts["strike"] - spot).abs().argsort().iloc[:1]]
    if closest_call.empty or closest_put.empty:
        return None

    def _mid(row) -> float | None:
        try:
            bid = float(row.get("bid", 0) or 0)
            ask = float(row.get("ask", 0) or 0)
            last = float(row.get("lastPrice", 0) or 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            if last > 0:
                return last
        except Exception:
            pass
        return None

    call_mid = _mid(closest_call.iloc[0])
    put_mid = _mid(closest_put.iloc[0])
    if not call_mid or not put_mid or spot <= 0:
        return None
    return (call_mid + put_mid) / spot * 100  # as percentage


def _closest_iv(df, target_strike: float) -> float | None:
    """IV of the contract whose strike is closest to target_strike."""
    if df.empty:
        return None
    idx = (df["strike"] - target_strike).abs().argsort().iloc[:1]
    row = df.iloc[idx]
    if row.empty:
        return None
    iv = row["impliedVolatility"].iloc[0]
    try:
        iv_f = float(iv)
        if 0 < iv_f < 5:
            return iv_f
    except (TypeError, ValueError):
        pass
    return None


def _days_to_earnings(t: yf.Ticker, today) -> int | None:
    """Days until next earnings announcement, or None if unknown / past."""
    try:
        cal = t.calendar
        if cal is None:
            return None
        # Calendar can be a DataFrame or dict depending on yfinance version
        ed = None
        if hasattr(cal, "get"):
            ed = cal.get("Earnings Date") or cal.get("earningsDate")
        elif hasattr(cal, "iloc"):
            try:
                ed = cal.iloc[0, 0]
            except Exception:
                ed = None
        if ed is None:
            return None
        # ed can be a list with start/end dates
        if isinstance(ed, list) and ed:
            ed = ed[0]
        # Convert to a date
        if hasattr(ed, "date"):
            d = ed.date()
        else:
            d = datetime.fromisoformat(str(ed)[:10]).date()
        delta = (d - today).days
        return delta if delta >= 0 else None
    except Exception:
        return None
