"""
Signal outcome service — captures entry prices when signals are created,
periodically snapshots prices at +1d/+5d/+30d/+90d intervals, and
computes performance statistics.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import Signal
from app.models.signal_outcome import SignalOutcome
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)

# Days after signal_created_at when each snapshot is due
SNAPSHOT_INTERVALS = {
    "1d":  1,
    "5d":  5,
    "30d": 30,
    "90d": 90,
}


class SignalOutcomeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Capture entry on signal creation ──────────────────────────────────────

    async def record_entry(self, signal: Signal, entry_price: float | None = None) -> SignalOutcome | None:
        """Create an outcome row for a freshly-created signal.

        If `entry_price` is not provided, fetches the current quote.
        Returns None if no price is available (signal won't be tracked).
        """
        if entry_price is None:
            entry_price = await self._fetch_current_price(signal.ticker)
        if entry_price is None or entry_price <= 0:
            log.debug("Skipping outcome row for %s — no entry price", signal.ticker)
            return None

        outcome = SignalOutcome(
            signal_id=signal.id,
            ticker=signal.ticker,
            signal_type=signal.signal_type,
            direction=signal.direction,
            strength=signal.strength,
            rationale=signal.rationale,
            entry_price=entry_price,
            signal_created_at=signal.created_at or datetime.now(UTC),
        )
        self.db.add(outcome)
        return outcome

    # ── Periodic snapshot job ──────────────────────────────────────────────────

    async def snapshot_due_outcomes(self) -> dict[str, int]:
        """Find outcomes whose next snapshot is due and fill in the price.

        Returns counts of snapshots taken per interval.
        """
        now = datetime.now(UTC)
        counts = {"1d": 0, "5d": 0, "30d": 0, "90d": 0}

        # For each interval, find rows where:
        #   signal_created_at + N days <= now
        #   AND price_Nd IS NULL
        for label, days in SNAPSHOT_INTERVALS.items():
            cutoff = now - timedelta(days=days)
            price_col = getattr(SignalOutcome, f"price_{label}")

            result = await self.db.execute(
                select(SignalOutcome).where(
                    and_(
                        SignalOutcome.signal_created_at <= cutoff,
                        price_col.is_(None),
                    )
                ).limit(500)  # cap per run to avoid long-running tasks
            )
            outcomes = list(result.scalars().all())
            if not outcomes:
                continue

            # Batch by ticker to avoid duplicate quote fetches
            unique_tickers = list({o.ticker for o in outcomes})
            quotes = await MarketDataService().get_quotes(unique_tickers)
            price_map = {q["ticker"]: q.get("price") for q in quotes if q.get("price")}

            for o in outcomes:
                price = price_map.get(o.ticker)
                if price is None or price <= 0:
                    continue
                setattr(o, f"price_{label}", Decimal(str(price)))
                setattr(o, f"snapshot_{label}_at", now)
                counts[label] += 1

            await self.db.flush()

        return counts

    # ── Backfill from historical data ─────────────────────────────────────────

    async def backfill_from_existing_signals(self, limit: int | None = None) -> int:
        """Create outcome rows for existing signals that don't have one yet.

        Uses yfinance historical data to compute entry_price (close on
        signal date) and any past snapshots that are now due.

        Returns the number of outcome rows created.
        """
        # Find signals with no outcome row
        existing_outcome_ids = select(SignalOutcome.signal_id).where(
            SignalOutcome.signal_id.is_not(None)
        )
        query = (
            select(Signal)
            .where(Signal.id.not_in(existing_outcome_ids))
            .order_by(Signal.created_at.desc())
        )
        if limit:
            query = query.limit(limit)

        signals = list((await self.db.execute(query)).scalars().all())
        if not signals:
            return 0

        log.info("Backfilling outcomes for %d existing signals", len(signals))
        created = 0
        now = datetime.now(UTC)

        # Group by ticker to fetch one history per ticker
        by_ticker: dict[str, list[Signal]] = {}
        for s in signals:
            by_ticker.setdefault(s.ticker, []).append(s)

        mds = MarketDataService()
        for ticker, tsignals in by_ticker.items():
            try:
                # Fetch 1 year of history — enough to backfill 90d snapshots
                resp = await mds.get_history(ticker, period="1y", interval="1d")
                candles = resp.get("candles", []) if isinstance(resp, dict) else []
            except Exception as exc:
                log.warning("Backfill: failed to fetch history for %s: %s", ticker, exc)
                continue

            if not candles:
                continue

            # Index by date string for fast lookup
            by_date = {self._candle_date(c): c for c in candles}

            for sig in tsignals:
                created_dt = sig.created_at or now
                entry_date = created_dt.date().isoformat()
                entry_candle = by_date.get(entry_date) or self._closest_after(entry_date, by_date)
                if not entry_candle:
                    continue
                entry_price = entry_candle.get("close")
                if not entry_price or entry_price <= 0:
                    continue

                outcome = SignalOutcome(
                    signal_id=sig.id,
                    ticker=sig.ticker,
                    signal_type=sig.signal_type,
                    direction=sig.direction,
                    strength=sig.strength,
                    rationale=sig.rationale,
                    entry_price=Decimal(str(entry_price)),
                    signal_created_at=created_dt,
                )

                # Backfill historical snapshots if enough time has elapsed
                for label, days in SNAPSHOT_INTERVALS.items():
                    target_dt = created_dt + timedelta(days=days)
                    if target_dt > now:
                        continue
                    target_date = target_dt.date().isoformat()
                    candle = by_date.get(target_date) or self._closest_after(target_date, by_date)
                    if candle and candle.get("close") and candle["close"] > 0:
                        setattr(outcome, f"price_{label}", Decimal(str(candle["close"])))
                        setattr(outcome, f"snapshot_{label}_at", target_dt)

                self.db.add(outcome)
                created += 1

            await self.db.flush()

        return created

    # ── Stats / Aggregation ────────────────────────────────────────────────────

    async def get_performance_stats(self) -> dict:
        """Aggregate outcome data into hit-rate and avg-gain stats.

        Returns dict with:
          - by_signal_type: {type: {timeframe: {hit_rate, avg_gain_pct, sample_size}}}
          - by_direction:   {direction: {timeframe: {...}}}
          - by_strength:    {strength: {timeframe: {...}}}
          - overall:        {timeframe: {...}}
        """
        result = await self.db.execute(select(SignalOutcome))
        outcomes = list(result.scalars().all())

        by_type: dict[str, dict[str, list[float]]]      = {}
        by_direction: dict[str, dict[str, list[float]]] = {}
        by_strength: dict[int, dict[str, list[float]]]  = {}
        overall: dict[str, list[float]]                 = {tf: [] for tf in SNAPSHOT_INTERVALS}

        for o in outcomes:
            entry = float(o.entry_price)
            if entry <= 0:
                continue
            for tf in SNAPSHOT_INTERVALS:
                price = getattr(o, f"price_{tf}")
                if price is None:
                    continue
                price_f = float(price)
                pct = (price_f - entry) / entry * 100
                # For bearish, invert: a price drop is a "hit"
                if o.direction.value == "bearish":
                    pct = -pct
                # neutral signals have no direction — skip from aggregates
                if o.direction.value == "neutral":
                    continue

                overall[tf].append(pct)
                by_type.setdefault(o.signal_type.value, {}).setdefault(tf, []).append(pct)
                by_direction.setdefault(o.direction.value, {}).setdefault(tf, []).append(pct)
                by_strength.setdefault(o.strength, {}).setdefault(tf, []).append(pct)

        def summarize(values: list[float]) -> dict:
            n = len(values)
            if n == 0:
                return {"hit_rate": None, "avg_gain_pct": None, "sample_size": 0}
            hits = sum(1 for v in values if v > 0)
            return {
                "hit_rate": round(hits / n * 100, 1),
                "avg_gain_pct": round(sum(values) / n, 2),
                "sample_size": n,
            }

        def summarize_dict(d: dict) -> dict:
            return {tf: summarize(values) for tf, values in d.items()}

        return {
            "overall":      {tf: summarize(values) for tf, values in overall.items()},
            "by_signal_type":  {k: summarize_dict(v) for k, v in by_type.items()},
            "by_direction":    {k: summarize_dict(v) for k, v in by_direction.items()},
            "by_strength":     {str(k): summarize_dict(v) for k, v in by_strength.items()},
            "total_outcomes":  len(outcomes),
        }

    async def list_recent(self, limit: int = 50) -> list[SignalOutcome]:
        """List recent outcomes for display."""
        result = await self.db.execute(
            select(SignalOutcome)
            .order_by(SignalOutcome.signal_created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    async def _fetch_current_price(ticker: str) -> float | None:
        try:
            quotes = await MarketDataService().get_quotes([ticker])
            if quotes and quotes[0].get("price"):
                return float(quotes[0]["price"])
        except Exception as exc:
            log.warning("Failed to fetch entry price for %s: %s", ticker, exc)
        return None

    @staticmethod
    def _candle_date(candle: dict) -> str:
        """Extract YYYY-MM-DD from a candle dict (handles unix int and string)."""
        t = candle.get("time")
        if isinstance(t, int):
            return datetime.fromtimestamp(t, tz=UTC).date().isoformat()
        return str(t)[:10]

    @staticmethod
    def _closest_after(target_date: str, by_date: dict) -> dict | None:
        """Find the first candle on or after target_date (handles weekends/holidays)."""
        for offset in range(7):
            d = (datetime.fromisoformat(target_date).date() + timedelta(days=offset)).isoformat()
            if d in by_date:
                return by_date[d]
        return None
