"""
Ticker resolution monitoring.

Answers one question: is any symbol the user references something the
market-data provider has never heard of?

This exists because MBGD and SYTA 404'd on every price refresh, signal
scan, insider fetch, dividend sync and backtest for weeks. They were
loud in the logs and completely invisible in the UI — the affected
holdings just silently contributed nothing everywhere, and a Mercedes
position ended up split across two symbols as a result.

The design problem is false positives. yfinance 404s transiently even
for large, liquid, unambiguously-listed names: CTRA, BK, MMC, HOLX and
EXAS have all failed mid-heatmap while being perfectly real S&P
constituents. Flagging on a single failure would produce a permanently
red banner that the user learns to ignore, which is worse than no
banner. Hence FAILURE_THRESHOLD: a ticker must fail on several
consecutive daily checks before it is called broken, and one success
resets the counter to zero.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import BrokerType, Portfolio, Position
from app.data.crypto import is_crypto
from app.models.ticker_health import TickerHealth
from app.models.watchlist import Watchlist, WatchlistItem

log = logging.getLogger(__name__)


class TickerHealthService:
    # Consecutive daily failures before a ticker is reported as broken.
    # Three days is long enough to ride out provider flakiness and short
    # enough that a genuine typo surfaces within the week.
    FAILURE_THRESHOLD = 3

    # Concurrency for the provider probes. Deliberately modest — this
    # runs daily and being slow is fine; being rate-limited is not, since
    # a 429 would look exactly like a bad symbol.
    CONCURRENCY = 4

    # Mass-failure guard. If this share of a run fails, the cause is the
    # provider — rate limiting, an outage, a DNS or egress problem — not
    # the symbols. Companies do not delist in batches of seventy-five,
    # and AAPL failing alongside MSFT and NVDA is proof of the opposite
    # of what a per-ticker verdict would claim. On a run like that
    # nothing is counted: no increments, no new flags.
    #
    # The number is high on purpose. A user whose watchlist is genuinely
    # half typos should still get warned, so this must only trip on a
    # failure rate no plausible set of real symbols produces.
    OUTAGE_FAILURE_RATIO = 0.6

    # Below this, the ratio is noise — three tickers where two fail is
    # 67% and tells you nothing. Small sets fall through to the normal
    # per-ticker path.
    OUTAGE_MIN_SAMPLE = 8

    # Substrings that identify throttling rather than a bad symbol. A
    # rate-limited probe is never evidence about the ticker, so it is
    # discounted whatever the overall failure rate.
    RATE_LIMIT_MARKERS = (
        "429", "too many requests", "rate limit", "ratelimit",
        "timeout", "timed out", "connection", "temporarily unavailable",
        "503", "502", "504", "curl", "ssl", "max retries",
        "budget exhausted", "returned nothing",
    )

    @classmethod
    def _is_transport_error(cls, err: str | None) -> bool:
        if not err:
            return False
        low = err.lower()
        return any(m in low for m in cls.RATE_LIMIT_MARKERS)

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Which tickers matter ──────────────────────────────────────────

    async def tracked_tickers(self, user_id: int | None = None) -> list[str]:
        """Symbols the user actually references: open positions in real
        portfolios, plus watchlist entries. Paper portfolios are included
        too — a typo there breaks the paper strategy just as thoroughly."""
        tickers: set[str] = set()

        pos_q = (
            select(Position.ticker)
            .join(Portfolio, Portfolio.id == Position.portfolio_id)
            .where(Position.quantity > 0)
        )
        wl_q = (
            select(WatchlistItem.ticker)
            .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        )
        if user_id is not None:
            pos_q = pos_q.where(Portfolio.user_id == user_id)
            wl_q = wl_q.where(Watchlist.user_id == user_id)

        for q in (pos_q, wl_q):
            for row in (await self.db.execute(q.distinct())).all():
                if row[0]:
                    tickers.add(row[0].upper())
        return sorted(tickers)

    # ── Probing ───────────────────────────────────────────────────────

    @staticmethod
    def _probe_sync(ticker: str) -> tuple[bool, str | None]:
        """Blocking single-ticker probe. Returns (resolved, error).

        Costs three provider requests, so it is NOT the path check_all
        uses — see _probe_batch. Kept for one-off checks and because the
        test suite stubs it.

        Tries a live price first, then falls back to recent history —
        some valid symbols (thin ADRs, certain foreign listings) have no
        fast_info price but do have bars. Only when BOTH come back empty
        do we call it unresolved.
        """
        import yfinance as yf

        try:
            t = yf.Ticker(ticker)
            try:
                price = t.fast_info.get("lastPrice")
                if price and float(price) > 0:
                    return True, None
            except Exception:
                pass

            hist = t.history(period="5d", interval="1d")
            if hist is not None and not hist.empty and len(hist["Close"].dropna()) > 0:
                return True, None
            return False, "no price and no recent history"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:400]

    @staticmethod
    async def _probe_batch(tickers: list[str]) -> list[tuple[str, bool, str | None]]:
        """Probe every ticker in one or two batched requests.

        Yahoo's batched quote endpoint omits symbols it cannot resolve,
        which is a far better signal than the per-ticker path gave: that
        one spent three requests each (~270 for a 90-ticker book, daily)
        and could not distinguish a bad symbol from a throttled request,
        because both arrive as an exception or an empty frame.

        Here the distinction is structural. A transport failure takes the
        whole batch down, so every ticker fails together and check_all's
        outage guard catches it. A symbol missing from an otherwise
        healthy response is genuinely unknown to the provider.
        """
        from app.services import yahoo_quotes

        try:
            quotes = await yahoo_quotes.get_many(tickers, budget_timeout=60.0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:400]
            return [(t, False, err) for t in tickers]

        if not quotes:
            # Budget exhausted or every chunk failed. Marked as a
            # transport error so it is discounted even if the ratio
            # somehow lands under the outage threshold.
            err = "batched quote request returned nothing (throttled or budget exhausted)"
            return [(t, False, err) for t in tickers]

        out: list[tuple[str, bool, str | None]] = []
        for t in tickers:
            q = quotes.get(t.upper())
            if q and q.get("price") is not None:
                out.append((t, True, None))
            else:
                out.append((t, False, "not returned by the provider's quote endpoint"))
        return out

    @staticmethod
    async def _probe_crypto(tickers: list[str]) -> list[tuple[str, bool, str | None]]:
        """Probe crypto symbols against CoinGecko, which is what actually
        serves them.

        They were previously probed against Yahoo, where "BTC" does not
        exist — Yahoo spells it BTC-USD — so BTC and ETH failed every
        single check and sat permanently in the warning banner.

        The first fix was to skip crypto entirely, which was worse: a
        symbol that is never probed is never marked healthy either, so
        the stale false flag became permanent instead of merely
        recurring. Probing the right provider is the only version that
        both avoids the false positive and still catches a genuinely bad
        crypto symbol.
        """
        from app.services.crypto_market_data import CryptoMarketDataService

        try:
            quotes = await CryptoMarketDataService.get_quotes(tickers)
        except Exception as exc:
            # One batched call, so a failure is the provider, not the
            # symbols. Phrased to match the transport markers so it is
            # discounted even when the set is too small for the outage
            # ratio to apply.
            err = f"CoinGecko connection failed: {type(exc).__name__}: {exc}"[:400]
            return [(t, False, err) for t in tickers]

        priced = {
            str(q.get("ticker", "")).upper()
            for q in quotes
            if q.get("price") is not None
        }
        if not priced and tickers:
            err = "CoinGecko returned nothing (temporarily unavailable)"
            return [(t, False, err) for t in tickers]

        return [
            (t, t.upper() in priced,
             None if t.upper() in priced else "not priced by CoinGecko")
            for t in tickers
        ]

    async def check_all(self, user_id: int | None = None) -> dict:
        """Probe every tracked ticker and update its health row.

        Success resets consecutive_failures to zero and stamps
        last_ok_at. Failure increments, and only once the count reaches
        FAILURE_THRESHOLD is the ticker marked as not resolving.
        """
        tracked = await self.tracked_tickers(user_id)
        if not tracked:
            return {"checked": 0, "broken": 0, "recovered": 0, "outage": False}

        # Each symbol is probed against the provider that serves it.
        # Guarded separately too: a Yahoo throttle must not suppress
        # verdicts about CoinGecko symbols, and vice versa. Pooling them
        # would let one healthy provider dilute the other's failure rate
        # below the outage threshold and flag its symbols as dead.
        batches = [
            ("Yahoo", [t for t in tracked if not is_crypto(t)], self._probe_batch),
            ("CoinGecko", [t for t in tracked if is_crypto(t)], self._probe_crypto),
        ]

        results: list[tuple[str, bool, str | None]] = []
        outages: list[str] = []
        skipped = 0
        first_error: str | None = None

        for name, group, probe in batches:
            if not group:
                continue
            group_results = await probe(group)
            group_failures = [(t, e) for t, ok, e in group_results if not ok]
            group_ratio = len(group_failures) / len(group_results)
            if (
                len(group_results) >= self.OUTAGE_MIN_SAMPLE
                and group_ratio >= self.OUTAGE_FAILURE_RATIO
            ):
                outages.append(name)
                skipped += len(group_results)
                if first_error is None and group_failures:
                    first_error = group_failures[0][1]
                log.error(
                    "Ticker health: %d/%d %s probes failed (%.0f%%) — treating as a "
                    "provider outage, not bad symbols. No health rows updated for "
                    "this group. First error: %s",
                    len(group_failures), len(group_results), name,
                    group_ratio * 100, group_failures[0][1] if group_failures else None,
                )
                continue
            results.extend(group_results)

        if not results:
            return {
                "checked": 0,
                "broken": 0,
                "recovered": 0,
                "skipped": skipped,
                "outage": True,
                "outage_providers": outages,
                "sample_error": first_error,
            }

        probed = [t for t, _, _ in results]
        existing = {
            r.ticker: r
            for r in (await self.db.execute(
                select(TickerHealth).where(TickerHealth.ticker.in_(probed))
            )).scalars().all()
        }

        now = datetime.now(UTC)
        broken = recovered = throttled = 0

        for ticker, ok, err in results:
            row = existing.get(ticker)
            if row is None:
                row = TickerHealth(ticker=ticker, resolves=True, consecutive_failures=0)
                self.db.add(row)

            row.last_checked_at = now
            if ok:
                if not row.resolves:
                    recovered += 1
                    log.info("Ticker health: %s resolves again", ticker)
                row.resolves = True
                row.consecutive_failures = 0
                row.last_ok_at = now
                row.last_error = None
            elif self._is_transport_error(err):
                # A throttled or timed-out probe says nothing about the
                # symbol. Record the error for diagnosis but leave the
                # counter alone, so a trickle of 429s across many days
                # can't accumulate into a false verdict.
                throttled += 1
                row.last_error = err
            else:
                row.consecutive_failures += 1
                row.last_error = err
                if row.consecutive_failures >= self.FAILURE_THRESHOLD and row.resolves:
                    row.resolves = False
                    broken += 1
                    log.warning(
                        "Ticker health: %s marked unresolvable after %d consecutive "
                        "failures (%s)", ticker, row.consecutive_failures, err,
                    )

        await self.db.flush()
        if outages:
            log.warning(
                "Ticker health: skipped %d symbol(s) — %s unavailable",
                skipped, " and ".join(outages),
            )
        if throttled:
            log.warning(
                "Ticker health: %d probe(s) failed on transport errors and were "
                "not counted against the symbol", throttled,
            )
        return {
            "checked": len(results),
            "broken": broken,
            "recovered": recovered,
            "throttled": throttled,
            "skipped": skipped,
            "outage": bool(outages),
            "outage_providers": outages,
        }

    # ── Read side ─────────────────────────────────────────────────────

    async def unresolvable_for_user(self, user_id: int) -> list[dict]:
        """Broken tickers this user references, newest problem first.

        `never_resolved` distinguishes a typo (never worked) from a
        delisting or ticker change (worked until recently) — the fixes
        are different, so the UI says which it is.
        """
        tickers = await self.tracked_tickers(user_id)
        if not tickers:
            return []

        rows = (await self.db.execute(
            select(TickerHealth).where(
                TickerHealth.ticker.in_(tickers),
                TickerHealth.resolves.is_(False),
            ).order_by(TickerHealth.consecutive_failures.desc())
        )).scalars().all()

        return [
            {
                "ticker": r.ticker,
                "consecutive_failures": r.consecutive_failures,
                "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
                "last_ok_at": r.last_ok_at.isoformat() if r.last_ok_at else None,
                "never_resolved": r.last_ok_at is None,
                "last_error": r.last_error,
            }
            for r in rows
        ]
