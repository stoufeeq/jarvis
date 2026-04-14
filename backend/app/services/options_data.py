"""
Options data service.

Primary source : yfinance option_chain()  — free, ~15-min delayed.
Enhancement    : Unusual Whales API        — real-time flow, requires paid key
                 (set UNUSUAL_WHALES_API_KEY in .env to activate).

Public interface
----------------
  svc = OptionsDataService()
  summary = await svc.get_chain_summary("AAPL")

Summary shape
-------------
{
  "ticker": "AAPL",
  "as_of": "2026-04-15T14:30:00+00:00",
  "current_price": 195.50,
  "expirations_used": ["2026-04-17", "2026-04-24"],
  "call_volume": 12500,
  "put_volume": 5600,
  "pc_ratio": 0.45,
  "net_call_premium": 1_250_000,
  "net_put_premium": 480_000,
  "unusual_calls": [
    {
      "strike": 200.0, "expiry": "2026-04-17",
      "volume": 1200, "open_interest": 350,
      "vol_oi_ratio": 3.4, "premium": 85_200,
      "last_price": 0.71, "itm": false
    }
  ],
  "unusual_puts": [ ... ],
  "uw_flow": [            # only present when UW key is set
    {
      "type": "call", "strike": 200.0, "expiry": "2026-04-17",
      "premium": 85_000, "volume": 1200, "open_interest": 350,
      "is_sweep": true, "is_block": false, "sentiment": "bullish",
      "executed_at": "2026-04-15T14:28:00Z"
    }
  ]
}
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

# Minimum DTE to be considered "near-term" for flow analysis
MAX_DTE = 45
# Minimum near-term expiries to analyse
MIN_EXPIRIES = 1
MAX_EXPIRIES = 3

# Unusual volume thresholds
VOL_OI_THRESHOLD = 3.0    # volume/OI ratio to flag as unusual
MIN_VOLUME = 200           # minimum contract volume to consider
MIN_PREMIUM = 10_000       # minimum total premium ($) to surface


class OptionsDataService:
    async def get_chain_summary(self, ticker: str) -> dict:
        """
        Build a full options flow summary for the given ticker.
        Raises ValueError if the ticker has no listed options.
        """
        base = await self._yfinance_summary(ticker)

        # Overlay Unusual Whales real-time flow if key is available
        if settings.unusual_whales_api_key:
            try:
                uw = await self._uw_flow(ticker)
                if uw:
                    base["uw_flow"] = uw
            except Exception as exc:
                log.warning("Unusual Whales fetch failed for %s: %s", ticker, exc)

        return base

    # ── yfinance ──────────────────────────────────────────────────────────────

    async def _yfinance_summary(self, ticker: str) -> dict:
        import yfinance as yf

        def _fetch():
            t = yf.Ticker(ticker)
            expirations = t.options  # tuple of "YYYY-MM-DD" strings
            if not expirations:
                raise ValueError(f"{ticker} has no listed options")

            # Pick near-term expiries (within MAX_DTE days)
            today = datetime.now(UTC).date()
            near = [
                exp for exp in expirations
                if 0 <= (datetime.strptime(exp, "%Y-%m-%d").date() - today).days <= MAX_DTE
            ][:MAX_EXPIRIES]

            if not near:
                # Fall back to the first available expiry
                near = [expirations[0]]

            # Fetch chains for each expiry
            all_calls, all_puts = [], []
            for exp in near:
                try:
                    chain = t.option_chain(exp)
                    calls = chain.calls.copy()
                    puts  = chain.puts.copy()
                    calls["expiry"] = exp
                    puts["expiry"]  = exp
                    all_calls.append(calls)
                    all_puts.append(puts)
                except Exception as exc:
                    log.warning("option_chain(%s, %s) failed: %s", ticker, exp, exc)

            if not all_calls:
                raise ValueError(f"Could not fetch option chain for {ticker}")

            import pandas as pd
            calls_df = pd.concat(all_calls, ignore_index=True)
            puts_df  = pd.concat(all_puts,  ignore_index=True)

            # Fill NaN volumes/OI with 0
            for df in (calls_df, puts_df):
                df["volume"]       = df["volume"].fillna(0).astype(int)
                df["openInterest"] = df["openInterest"].fillna(0).astype(int)
                df["lastPrice"]    = df["lastPrice"].fillna(0.0)

            # Get current price for OTM determination
            try:
                price = float(t.fast_info.last_price or 0)
            except Exception:
                price = 0.0

            import math as _math
            call_vol = int(calls_df["volume"].sum())
            put_vol  = int(puts_df["volume"].sum())
            _raw_pc  = (put_vol / call_vol) if call_vol else None
            pc_ratio = None if (_raw_pc is None or _math.isnan(_raw_pc) or _math.isinf(_raw_pc)) else round(_raw_pc, 3)

            # Net premium = sum(volume * lastPrice * 100); fillna(0) guards NaN prices
            def _net_premium(df):
                import math
                val = (df["volume"] * df["lastPrice"].fillna(0) * 100).sum()
                return 0 if (math.isnan(val) or math.isinf(val)) else int(val)

            net_call_premium = _net_premium(calls_df)
            net_put_premium  = _net_premium(puts_df)

            # Unusual contracts
            unusual_calls = _unusual(calls_df, price, side="call")
            unusual_puts  = _unusual(puts_df,  price, side="put")

            return {
                "ticker": ticker.upper(),
                "as_of": datetime.now(UTC).isoformat(),
                "current_price": round(price, 4) if price else None,
                "expirations_used": near,
                "call_volume": call_vol,
                "put_volume": put_vol,
                "pc_ratio": pc_ratio,
                "net_call_premium": net_call_premium,
                "net_put_premium": net_put_premium,
                "unusual_calls": unusual_calls,
                "unusual_puts": unusual_puts,
            }

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(_fetch))

    # ── Unusual Whales ────────────────────────────────────────────────────────

    async def _uw_flow(self, ticker: str) -> list[dict]:
        """Fetch recent real-time options flow from Unusual Whales API."""
        url = f"https://api.unusualwhales.com/api/option/flow/ticker/{ticker.upper()}"
        headers = {
            "Authorization": f"Bearer {settings.unusual_whales_api_key}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params={"limit": 50, "order": "premium"})

        if resp.status_code == 401:
            log.warning("Unusual Whales API key invalid or expired")
            return []
        if resp.status_code != 200:
            log.warning("Unusual Whales returned %d for %s", resp.status_code, ticker)
            return []

        raw = resp.json()
        items = raw if isinstance(raw, list) else raw.get("data", [])

        result = []
        for item in items:
            try:
                result.append({
                    "type":         item.get("type") or item.get("option_type"),
                    "strike":       float(item.get("strike", 0)),
                    "expiry":       item.get("expiry") or item.get("expires_at", "")[:10],
                    "premium":      int(float(item.get("premium", 0))),
                    "volume":       int(item.get("volume", 0)),
                    "open_interest": int(item.get("open_interest", 0)),
                    "is_sweep":     bool(item.get("is_sweep", False)),
                    "is_block":     bool(item.get("is_block", False)),
                    "sentiment":    item.get("sentiment", ""),
                    "executed_at":  item.get("executed_at") or item.get("timestamp", ""),
                })
            except Exception:
                continue
        return result


def _safe_float(val, fallback: float = 0.0) -> float:
    """Convert to float, replacing inf/nan with fallback (JSON-safe)."""
    import math
    try:
        f = float(val)
        return fallback if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return fallback


def _unusual(df, current_price: float, side: str) -> list[dict]:
    """Return contracts with unusual volume relative to open interest."""
    rows = []
    for _, row in df.iterrows():
        vol = int(row["volume"])
        oi  = int(row["openInterest"])
        if vol < MIN_VOLUME:
            continue
        # Use 999.0 as the capped sentinel when OI is zero — JSON-safe, still sortable
        vol_oi = round(vol / oi, 2) if oi > 0 else 999.0
        if vol_oi < VOL_OI_THRESHOLD and oi > 0:
            continue
        last = _safe_float(row["lastPrice"])
        premium = int(vol * last * 100)
        if premium < MIN_PREMIUM:
            continue
        strike = _safe_float(row["strike"])
        itm = (strike < current_price) if side == "call" else (strike > current_price)
        rows.append({
            "strike":        strike,
            "expiry":        str(row.get("expiry", "")),
            "volume":        vol,
            "open_interest": oi,
            "vol_oi_ratio":  vol_oi,
            "premium":       premium,
            "last_price":    round(last, 4),
            "itm":           itm,
        })
    # Sort by premium descending
    return sorted(rows, key=lambda x: x["premium"], reverse=True)[:10]
