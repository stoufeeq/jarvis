"""
Earnings Signal Provider — powered by Finnhub.

Signals fired:
  - earnings_upcoming (bullish/bearish/neutral) — company reports earnings
    within the next 7 days. Strength based on days-to-earnings:
      1–2 days  → strength 5
      3–4 days  → strength 4
      5–7 days  → strength 3

Direction is neutral unless we have an EPS estimate — positive EPS estimate
→ bullish, negative → bearish.

Requires FINNHUB_API_KEY in settings. Skips gracefully if key is absent.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx

from app.config import get_settings
from app.models.signal import Signal, SignalDirection, SignalType
from app.signals.base import BaseSignalProvider

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
LOOKAHEAD_DAYS = 7
CACHE_TTL = 3600  # 1 hour — earnings dates don't change intraday

_earnings_cache: dict[str, tuple[float, list[dict]]] = {}


def _fetch_earnings_sync(ticker: str, api_key: str) -> list[dict]:
    """Fetch upcoming earnings for ticker from Finnhub (sync, runs in executor)."""
    import time

    cached = _earnings_cache.get(ticker)
    if cached and (time.monotonic() - cached[0]) < CACHE_TTL:
        return cached[1]

    today = datetime.now(UTC).date()
    to_date = today + timedelta(days=LOOKAHEAD_DAYS)

    try:
        resp = httpx.get(
            f"{FINNHUB_BASE}/calendar/earnings",
            params={
                "from": today.isoformat(),
                "to": to_date.isoformat(),
                "symbol": ticker,
                "token": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("earningsCalendar", [])
        _earnings_cache[ticker] = (time.monotonic(), data)
        return data
    except Exception as exc:
        logger.warning("Finnhub earnings fetch failed for %s: %s", ticker, exc)
        return []


class EarningsSignalProvider(BaseSignalProvider):
    """Generates earnings_upcoming signals using Finnhub calendar."""

    async def scan(self, ticker: str) -> list[Signal]:
        settings = get_settings()
        if not settings.finnhub_api_key:
            return []

        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(
            None, _fetch_earnings_sync, ticker, settings.finnhub_api_key
        )

        signals: list[Signal] = []
        today = datetime.now(UTC).date()

        for event in events:
            if event.get("symbol", "").upper() != ticker.upper():
                continue

            date_str = event.get("date")
            if not date_str:
                continue

            try:
                report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            days_away = (report_date - today).days
            if days_away < 0 or days_away > LOOKAHEAD_DAYS:
                continue

            # The 3-4 day window (former strength=4 bucket) was dropped on
            # 2026-06-03 — backtest showed it as a net loser (-1.43% per
            # trade, 39% hit rate, n=3.5k). Likely cause: that's the IV
            # crush sweet spot where premium starts to deflate ahead of the
            # event but the move itself hasn't happened yet. Entering 5-7
            # days out (str=3) and 1-2 days out (str=5) both work; the
            # middle is a trap.
            if days_away <= 2:
                strength = 5
            elif days_away <= 4:
                continue  # skip the IV-crush bucket entirely
            else:
                strength = 3

            # Direction from EPS estimate
            eps_estimate = event.get("epsEstimate")
            if eps_estimate is not None:
                direction = SignalDirection.bullish if float(eps_estimate) >= 0 else SignalDirection.bearish
            else:
                direction = SignalDirection.neutral

            # Time qualifier
            hour = event.get("hour", "")  # "bmo" = before market open, "amc" = after market close
            time_label = " (before open)" if hour == "bmo" else " (after close)" if hour == "amc" else ""

            rationale = (
                f"{ticker} reports earnings on {report_date.strftime('%b %d')}{time_label} "
                f"— {days_away} day{'s' if days_away != 1 else ''} away."
            )
            if eps_estimate is not None:
                rationale += f" Consensus EPS estimate: {float(eps_estimate):+.2f}."

            revenue_estimate = event.get("revenueEstimate")
            if revenue_estimate:
                rationale += f" Revenue estimate: ${float(revenue_estimate)/1e9:.1f}B." if float(revenue_estimate) > 1e9 else f" Revenue estimate: ${float(revenue_estimate)/1e6:.0f}M."

            expires = datetime.now(UTC) + timedelta(days=days_away + 1)

            signals.append(Signal(
                ticker=ticker,
                signal_type=SignalType.earnings_upcoming,
                direction=direction,
                strength=strength,
                rationale=rationale,
                indicators=f"DAYS_TO_EARNINGS={days_away},REPORT_DATE={date_str}",
                timeframe="event",
                expires_at=expires,
            ))

        return signals
