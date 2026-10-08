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

    async def check_all(self, user_id: int | None = None) -> dict:
        """Probe every tracked ticker and update its health row.

        Success resets consecutive_failures to zero and stamps
        last_ok_at. Failure increments, and only once the count reaches
        FAILURE_THRESHOLD is the ticker marked as not resolving.
        """
        tracked = await self.tracked_tickers(user_id)

        # Crypto resolves through CoinGecko, not Yahoo. Probing "BTC"
        # against a Yahoo quote endpoint fails every time — Yahoo spells
        # it BTC-USD — so including them produced permanent false
        # positives. BTC and ETH were both in the 75-ticker false alarm.
        # A broken crypto symbol needs its own probe against the provider
        # that actually serves it; until that exists, silence beats a
        # guaranteed-wrong verdict.
        tickers = [t for t in tracked if not is_crypto(t)]
        skipped_crypto = len(tracked) - len(tickers)
        if skipped_crypto:
            log.debug(
                "Ticker health: skipped %d crypto symbol(s) — served by CoinGecko",
                skipped_crypto,
            )

        if not tickers:
            return {"checked": 0, "broken": 0, "recovered": 0, "outage": False}

        results = await self._probe_batch(tickers)

        failures = [(t, e) for t, ok, e in results if not ok]
        ratio = len(failures) / len(results)
        outage = (
            len(results) >= self.OUTAGE_MIN_SAMPLE
            and ratio >= self.OUTAGE_FAILURE_RATIO
        )
        if outage:
            # Record nothing. Incrementing here is how a provider outage
            # turns into seventy-five "likely delisted" warnings, which
            # is worse than no warning at all: it buries the one symbol
            # that really is wrong and trains the user to ignore the
            # banner. last_checked_at is left alone too, so the run reads
            # as "did not happen" rather than "happened and was fine".
            sample = ", ".join(t for t, _ in failures[:5])
            log.error(
                "Ticker health: %d/%d probes failed (%.0f%%) — treating as a "
                "provider outage, not %d bad symbols. No health rows updated. "
                "Sample: %s. First error: %s",
                len(failures), len(results), ratio * 100, len(failures),
                sample, failures[0][1] if failures else None,
            )
            return {
                "checked": len(results),
                "broken": 0,
                "recovered": 0,
                "skipped": len(results),
                "outage": True,
                "failure_ratio": round(ratio, 3),
                "sample_error": failures[0][1] if failures else None,
            }

        existing = {
            r.ticker: r
            for r in (await self.db.execute(
                select(TickerHealth).where(TickerHealth.ticker.in_(tickers))
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
        if throttled:
            log.warning(
                "Ticker health: %d probe(s) failed on transport errors and were "
                "not counted against the symbol", throttled,
            )
        return {
            "checked": len(tickers),
            "broken": broken,
            "recovered": recovered,
            "throttled": throttled,
            "outage": False,
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
