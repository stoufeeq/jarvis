"""Market regime classifier + persistence.

Regime is a 2×2 classification of overall market state:

    bull_low_vol    SPX > 200-SMA  and  VIX <  20
    bull_high_vol   SPX > 200-SMA  and  VIX >= 20
    bear_low_vol    SPX < 200-SMA  and  VIX <  20
    bear_high_vol   SPX < 200-SMA  and  VIX >= 20

Rationale for these two axes specifically:
  - SPX-vs-200SMA is the single most durable trend proxy — decades of
    research on ma-crossover regimes agree it separates "environments
    to buy dips" from "environments to sell rips".
  - VIX cuts across the trend axis: even a bull can be dangerous when
    vol is >20 (bigger drawdowns; tighter stops needed). Splitting on
    it captures the "expected volatility" dimension cheaply.

Both inputs come from yfinance daily bars (SPY as SPX proxy — better
data quality than ^GSPC in yfinance; ^VIX for vol).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.market_regime import MarketRegime
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)

VIX_THRESHOLD = 20.0
SMA_WINDOW = 200
# yfinance ticker for the S&P 500 index. SPY (the ETF) tracks it 1:1
# and has cleaner intraday data than ^GSPC.
SPX_TICKER = "SPY"
VIX_TICKER = "^VIX"


REGIMES = (
    "bull_low_vol",
    "bull_high_vol",
    "bear_low_vol",
    "bear_high_vol",
)


def _normalize_index(s: pd.Series) -> pd.Series:
    """Strip timezone and normalise to midnight so two yfinance series
    (which may be tz-aware in different zones) align by calendar date
    rather than by wall-clock nanosecond. Without this, DataFrame joins
    silently fill with NaN for every row and dropna nukes everything."""
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    s = s.copy()
    s.index = idx.normalize()
    return s


def classify(spx_close: float, spx_sma200: float, vix_close: float) -> str:
    """Deterministic 2×2 classification. Pure function — no I/O."""
    is_bull = spx_close > spx_sma200
    is_low_vol = vix_close < VIX_THRESHOLD
    if is_bull and is_low_vol:
        return "bull_low_vol"
    if is_bull and not is_low_vol:
        return "bull_high_vol"
    if not is_bull and is_low_vol:
        return "bear_low_vol"
    return "bear_high_vol"


class RegimeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_current(self) -> MarketRegime | None:
        """Return today's regime row, or the most recent one if today
        hasn't been classified yet (weekends, before the daily refresh
        has run). Callers should treat "None" as "no gate applied"."""
        result = await self.db.execute(
            select(MarketRegime).order_by(MarketRegime.date.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_for_date(self, d: date) -> MarketRegime | None:
        """Look up the classified regime for a specific historical
        date. Used by the analysis script to bucket closed trades."""
        result = await self.db.execute(
            select(MarketRegime).where(MarketRegime.date == d)
        )
        return result.scalar_one_or_none()

    async def refresh_current(self) -> MarketRegime:
        """Fetch fresh SPX + VIX, classify, upsert. Idempotent — run
        as often as you like without duplicating rows.

        Uses the last complete daily bar (yesterday if today's hasn't
        closed yet). On weekends / holidays this yields Friday's data,
        which is exactly what you want ("what regime is in effect right
        now?" — the last observed one).
        """
        mds = MarketDataService()
        # 300d gives comfortable headroom over the 200-day SMA window
        # and lets us re-classify recent history if needed.
        spx_df = await mds.get_ohlcv_dataframe(SPX_TICKER, period="1y", interval="1d")
        vix_df = await mds.get_ohlcv_dataframe(VIX_TICKER, period="1y", interval="1d")

        if spx_df is None or vix_df is None or spx_df.empty or vix_df.empty:
            raise RuntimeError("Regime refresh: SPX or VIX bars unavailable")

        spx_close_series = spx_df["Close"].dropna()
        if len(spx_close_series) < SMA_WINDOW:
            raise RuntimeError(
                f"Regime refresh: need {SMA_WINDOW} SPX bars for SMA200, "
                f"got {len(spx_close_series)}"
            )

        spx_sma200 = spx_close_series.rolling(SMA_WINDOW).mean().iloc[-1]
        spx_close = spx_close_series.iloc[-1]
        vix_close = vix_df["Close"].dropna().iloc[-1]

        # Date = last SPX bar's date. Normalise to naive date.
        last_ts: pd.Timestamp = spx_close_series.index[-1]
        d = last_ts.date() if hasattr(last_ts, "date") else datetime.now(UTC).date()

        regime = classify(float(spx_close), float(spx_sma200), float(vix_close))
        return await self._upsert(
            d=d,
            regime=regime,
            spx_close=float(spx_close),
            spx_sma200=float(spx_sma200),
            vix_close=float(vix_close),
        )

    async def backfill(self, lookback_days: int = 365) -> int:
        """Backfill regime rows for the last `lookback_days` (skipping
        any dates already present). Run once when the feature is first
        deployed so the analysis script can join historical trades.

        Returns the number of rows inserted.
        """
        mds = MarketDataService()
        # Pull enough history so we can compute 200-SMA at every point
        # in the lookback window. 365d + 200d = ~2y ⇒ use "2y".
        spx_df = await mds.get_ohlcv_dataframe(SPX_TICKER, period="2y", interval="1d")
        vix_df = await mds.get_ohlcv_dataframe(VIX_TICKER, period="2y", interval="1d")

        if spx_df is None or vix_df is None or spx_df.empty or vix_df.empty:
            log.warning("Regime backfill: SPX or VIX unavailable — skipped")
            return 0

        spx_close = _normalize_index(spx_df["Close"].dropna())
        vix_close = _normalize_index(vix_df["Close"].dropna())
        sma200 = spx_close.rolling(SMA_WINDOW).mean()

        # Align on shared index (trading days present in both). The
        # _normalize_index above strips timezone + normalises to midnight
        # so SPY (tz-aware in America/New_York) and ^VIX align exactly
        # on the same date value. Without this the DataFrame constructor
        # would treat 2025-07-24 00:00-04:00 and 2025-07-24 00:00+00:00
        # as different index positions → NaN → dropna wipes everything.
        joined = pd.DataFrame({
            "spx": spx_close,
            "sma200": sma200,
            "vix": vix_close,
        }).dropna()
        log.info(
            "Regime backfill: fetched SPY=%d bars, VIX=%d bars, aligned=%d bars",
            len(spx_close), len(vix_close), len(joined),
        )

        # Cutoff: only classify dates within the requested lookback.
        cutoff = datetime.now(UTC).date() - timedelta(days=lookback_days)
        joined = joined[joined.index.date >= cutoff]  # type: ignore[union-attr]
        log.info("Regime backfill: %d bars after %s cutoff", len(joined), cutoff)

        # Skip dates already in the table so a re-run is cheap.
        existing_dates_result = await self.db.execute(select(MarketRegime.date))
        existing_dates = {row[0] for row in existing_dates_result.all()}

        inserted = 0
        for ts, row in joined.iterrows():
            d = ts.date() if hasattr(ts, "date") else ts  # type: ignore[union-attr]
            if d in existing_dates:
                continue
            regime = classify(float(row["spx"]), float(row["sma200"]), float(row["vix"]))
            await self._upsert(
                d=d,
                regime=regime,
                spx_close=float(row["spx"]),
                spx_sma200=float(row["sma200"]),
                vix_close=float(row["vix"]),
            )
            inserted += 1
        return inserted

    async def _upsert(
        self,
        *,
        d: date,
        regime: str,
        spx_close: float,
        spx_sma200: float,
        vix_close: float,
    ) -> MarketRegime:
        """Postgres INSERT ... ON CONFLICT (date) DO UPDATE. SQLite
        tests use a delete+insert fallback since the dialect doesn't
        support the same syntax."""
        dialect = self.db.bind.dialect.name if self.db.bind else "postgresql"
        if dialect == "postgresql":
            stmt = pg_insert(MarketRegime).values(
                date=d,
                regime=regime,
                spx_close=spx_close,
                spx_sma200=spx_sma200,
                vix_close=vix_close,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["date"],
                set_={
                    "regime": regime,
                    "spx_close": spx_close,
                    "spx_sma200": spx_sma200,
                    "vix_close": vix_close,
                },
            )
            await self.db.execute(stmt)
        else:
            existing = await self.get_for_date(d)
            if existing:
                existing.regime = regime
                existing.spx_close = spx_close
                existing.spx_sma200 = spx_sma200
                existing.vix_close = vix_close
            else:
                self.db.add(MarketRegime(
                    date=d, regime=regime,
                    spx_close=spx_close,
                    spx_sma200=spx_sma200,
                    vix_close=vix_close,
                ))
        await self.db.flush()
        # Return the row for callers that want it.
        result = await self.get_for_date(d)
        assert result is not None
        return result
