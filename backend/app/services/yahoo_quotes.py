"""
Batched Yahoo quotes — one request per 100 symbols.

The problem this replaces
------------------------
yfinance's per-ticker helpers are far more expensive than they look.
Measured against yfinance 1.2.2:

    fast_info                 2 requests per ticker
    Ticker.history            1 request per ticker
    download([... 40 ...])   41 requests  — download() threads per
                                            ticker, it does NOT batch

That last one matters most, because the name suggests otherwise and the
codebase relied on it. The S&P 500 heatmap was costing one batched-looking
download (~453 requests) plus 452 fast_info calls (~904 requests) — about
1,357 requests every ten minutes, or 8,100/hour, from a single IP against
a limit the community puts near 360/hour.

Yahoo's /v7/finance/quote endpoint genuinely batches: one request returns
a full quote for up to ~250 symbols. The same heatmap costs 5 requests
through it. Verified to carry everything the app's Quote shape needs —
price, previous close, change %, volume, market cap, 52-week range,
currency — for US equities, foreign listings (MBG.DE in EUR), crypto
(BTC-USD), other exchanges (0700.HK), indices (^VIX) and FX (EURUSD=X).

Authentication rides on yfinance's own session, so the cookie/crumb
handshake stays yfinance's problem rather than something reimplemented
here.

Unknown symbols are simply absent from the response. That is a cleaner
signal than yfinance's per-ticker behaviour, which raises for some
symbols and returns an empty frame for others, and it is what lets
ticker health tell a bad symbol from a throttled request.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from app.services.rate_limit import spend

log = logging.getLogger(__name__)

QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"

# Symbols per request. The endpoint serves 250 but 100 is the documented
# community figure and leaves room for long foreign symbols inside URL
# length limits. Five requests for the whole S&P 500 is already cheap
# enough that squeezing it further buys nothing.
CHUNK = 100

# Per-request network timeout handed to yfinance. Its default is 30s,
# which is too patient for a job on a schedule.
HTTP_TIMEOUT = 15

# Hard ceiling per chunk, enforced outside yfinance. Needed because
# yfinance retries its cookie/crumb handshake internally when Yahoo is
# throttling, so the HTTP timeout above bounds one attempt but not the
# call. Without this a throttled provider can wedge a Celery worker — the
# task never returns, beat queues the next one behind it, and the queue
# backs up behind a request that is never coming.
CHUNK_DEADLINE = 25.0

# Ceiling for a whole get_many across all its chunks, so a 5-chunk
# heatmap fetch cannot stack five deadlines into two minutes.
TOTAL_DEADLINE = 60.0


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _normalise(q: dict) -> dict:
    """Map Yahoo's field names onto the app's Quote shape."""
    price = _f(q.get("regularMarketPrice"))
    prev = _f(q.get("regularMarketPreviousClose"))

    change = _f(q.get("regularMarketChange"))
    change_pct = _f(q.get("regularMarketChangePercent"))
    # Derive rather than trust: the endpoint occasionally omits the
    # change fields for thin names while still carrying both prices.
    if change is None and price is not None and prev is not None:
        change = price - prev
    if change_pct is None and price is not None and prev not in (None, 0):
        change_pct = (price - prev) / prev * 100.0

    ccy = (q.get("currency") or "USD").upper()
    # Yahoo quotes some LSE listings in pence under the code "GBp";
    # MarketDataService normalises the same way, so quotes agree
    # whichever path produced them.
    if ccy == "GBP" and q.get("currency") == "GBp":
        ccy = "GBP"

    return {
        "ticker": q.get("symbol"),
        "price": price,
        "previous_close": prev,
        "change": change,
        "change_pct": change_pct,
        "volume": _f(q.get("regularMarketVolume")),
        # Lets the heatmap compute relative volume without a second
        # historical fetch — that fetch was ~453 requests on its own.
        "avg_volume_10d": _f(q.get("averageDailyVolume10Day")),
        "avg_volume_3m": _f(q.get("averageDailyVolume3Month")),
        "market_state": q.get("marketState"),
        "market_cap": _f(q.get("marketCap")),
        "fifty_two_week_high": _f(q.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": _f(q.get("fiftyTwoWeekLow")),
        "currency": ccy,
    }


def _fetch_chunk_sync(symbols: list[str]) -> list[dict]:
    """Blocking single-request fetch for up to CHUNK symbols."""
    from yfinance.data import YfData

    raw = YfData().get_raw_json(
        QUOTE_URL, params={"symbols": ",".join(symbols)}, timeout=HTTP_TIMEOUT
    )
    body = raw.get("quoteResponse") or {}
    if body.get("error"):
        raise RuntimeError(f"Yahoo quote error: {body['error']}")
    return body.get("result") or []


def chunks(symbols: list[str], size: int = CHUNK) -> list[list[str]]:
    return [symbols[i : i + size] for i in range(0, len(symbols), size)]


def request_cost(symbols: list[str]) -> int:
    """Requests a `get_many` for these symbols will spend. Lets callers
    reserve the whole job's budget up front rather than discovering
    halfway through that they can't finish."""
    unique = list(dict.fromkeys(s.upper() for s in symbols if s))
    return len(chunks(unique))


async def get_many(
    symbols: list[str],
    *,
    budget_timeout: float | None = None,
    reserve: bool = True,
) -> dict[str, dict]:
    """Quotes keyed by uppercase symbol. Missing symbols are absent.

    Never raises for a bad symbol; raises only if every chunk fails,
    since silently returning {} would read as "all your holdings are
    unresolvable" to a caller that can't tell the difference.
    """
    unique = list(dict.fromkeys(s.upper() for s in symbols if s))
    if not unique:
        return {}

    groups = chunks(unique)
    if reserve and not await spend(len(groups), timeout=budget_timeout):
        log.warning(
            "Yahoo budget: skipping quote fetch for %d symbol(s) — %d request(s) "
            "unavailable within timeout", len(unique), len(groups),
        )
        return {}

    out: dict[str, dict] = {}
    failures = 0
    started = time.monotonic()

    for group in groups:
        remaining = TOTAL_DEADLINE - (time.monotonic() - started)
        if remaining <= 0:
            failures += 1
            log.warning(
                "Yahoo quote fetch hit its %.0fs total deadline with %d chunk(s) "
                "unfetched", TOTAL_DEADLINE, len(groups) - len(out) // CHUNK,
            )
            break
        try:
            # wait_for abandons the await; the worker thread finishes on
            # its own and its result is discarded. That leaks a thread
            # briefly, which is the acceptable cost of never blocking the
            # caller indefinitely.
            rows = await asyncio.wait_for(
                asyncio.to_thread(_fetch_chunk_sync, group),
                timeout=min(CHUNK_DEADLINE, remaining),
            )
            for q in rows:
                row = _normalise(q)
                if row["ticker"]:
                    out[str(row["ticker"]).upper()] = row
        except TimeoutError:
            failures += 1
            log.warning(
                "Yahoo quote chunk of %d timed out after %.0fs — provider is "
                "likely throttling", len(group), min(CHUNK_DEADLINE, remaining),
            )
        except Exception as exc:
            failures += 1
            log.warning(
                "Yahoo quote chunk of %d failed: %s: %s",
                len(group), type(exc).__name__, exc,
            )

    if failures and failures == len(groups):
        raise RuntimeError(
            f"All {failures} Yahoo quote request(s) failed for {len(unique)} symbols"
        )
    return out


async def get_one(symbol: str, *, budget_timeout: float | None = None) -> dict | None:
    got = await get_many([symbol], budget_timeout=budget_timeout)
    return got.get(symbol.upper())
