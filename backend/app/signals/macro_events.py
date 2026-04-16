"""
Economic Calendar Signal Provider — powered by Finnhub.

Generates macro_event signals for high-impact economic events (FOMC, CPI,
NFP, GDP) within the next 7 days. These are not ticker-specific — they
affect the whole market — so they are attached to a synthetic ticker "SPY"
when scanned directly, but the briefing service queries them globally.

Strength:
  High impact   → 5
  Medium impact → 3
  Low impact    → 1

Direction is always neutral (macro events cut both ways until released).

Requires FINNHUB_API_KEY. Skips gracefully if absent.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx

from app.config import get_settings
from app.models.signal import Signal, SignalDirection, SignalType
from app.signals.base import BaseSignalProvider

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
LOOKAHEAD_DAYS = 7
CACHE_TTL = 3600

# Keywords that flag high-impact events
HIGH_IMPACT_KEYWORDS = {
    "fomc", "federal reserve", "fed rate", "interest rate decision",
    "nonfarm payroll", "nfp", "cpi", "consumer price index",
    "gdp", "gross domestic product", "pce", "personal consumption",
}
MEDIUM_IMPACT_KEYWORDS = {
    "ppi", "producer price", "retail sales", "unemployment",
    "jobless claims", "ism", "pmi", "housing starts",
    "durable goods", "trade balance",
}

_econ_cache: dict[str, tuple[float, list[dict]]] = {}


def _fetch_economic_calendar_sync(api_key: str) -> list[dict]:
    cache_key = "global"
    cached = _econ_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < CACHE_TTL:
        return cached[1]

    today = datetime.now(UTC).date()
    to_date = today + timedelta(days=LOOKAHEAD_DAYS)

    try:
        resp = httpx.get(
            f"{FINNHUB_BASE}/calendar/economic",
            params={"token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json().get("economicCalendar", [])
        # Filter to lookahead window
        filtered = []
        for e in events:
            date_str = e.get("time", "")[:10]
            try:
                event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if today <= event_date <= to_date:
                    filtered.append(e)
            except ValueError:
                continue
        _econ_cache[cache_key] = (time.monotonic(), filtered)
        return filtered
    except Exception as exc:
        logger.warning("Finnhub economic calendar fetch failed: %s", exc)
        return []


def _classify_impact(event: dict) -> tuple[int, bool]:
    """Return (strength 1-5, is_relevant). Irrelevant = skip."""
    name = (event.get("event") or "").lower()
    impact = (event.get("impact") or "").lower()

    # Finnhub provides impact: "high", "medium", "low"
    if impact == "high" or any(k in name for k in HIGH_IMPACT_KEYWORDS):
        return 5, True
    if impact == "medium" or any(k in name for k in MEDIUM_IMPACT_KEYWORDS):
        return 3, True
    if impact == "low":
        return 1, False  # skip low-impact events
    return 1, False


class EconomicCalendarProvider(BaseSignalProvider):
    """
    Generates macro_event signals attached to SPY.
    Called when scanning SPY, or directly by the briefing service.
    """

    async def scan(self, ticker: str) -> list[Signal]:
        # Only attach macro events when scanning SPY or QQQ as proxies
        # (briefing service calls scan_macro_events() directly for all events)
        if ticker.upper() not in ("SPY", "QQQ", "IWM", "DIA"):
            return []

        return await self.scan_macro_events()

    async def scan_macro_events(self) -> list[Signal]:
        """Fetch all upcoming macro events regardless of ticker."""
        settings = get_settings()
        if not settings.finnhub_api_key:
            return []

        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(
            None, _fetch_economic_calendar_sync, settings.finnhub_api_key
        )

        signals: list[Signal] = []
        today = datetime.now(UTC).date()

        for event in events:
            strength, relevant = _classify_impact(event)
            if not relevant:
                continue

            date_str = (event.get("time") or "")[:10]
            try:
                event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            days_away = (event_date - today).days
            event_name = event.get("event", "Economic Event")
            country = event.get("country", "US").upper()
            estimate = event.get("estimate")
            prev = event.get("prev")

            rationale = (
                f"{country} macro event: {event_name} on {event_date.strftime('%b %d')} "
                f"({days_away} day{'s' if days_away != 1 else ''} away)."
            )
            if estimate is not None:
                rationale += f" Consensus estimate: {estimate}."
            if prev is not None:
                rationale += f" Previous: {prev}."

            indicators = f"EVENT={event_name.replace(' ', '_')[:50]},COUNTRY={country},DAYS={days_away}"

            expires = datetime.now(UTC) + timedelta(days=days_away + 1)

            signals.append(Signal(
                ticker="SPY",
                signal_type=SignalType.macro_event,
                direction=SignalDirection.neutral,
                strength=strength,
                rationale=rationale,
                indicators=indicators,
                timeframe="event",
                expires_at=expires,
            ))

        return signals
