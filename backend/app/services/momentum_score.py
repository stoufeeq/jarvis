"""9/20/50 EMA + VWAP momentum score.

The classic intraday day-trading setup:
  - Price above/below VWAP tells you where institutional buyers stand
    on average for the session
  - 9 EMA / 20 EMA / 50 EMA stack tells you fast/medium/slow trend
    alignment
  - Recent bounce off VWAP or one of the EMAs is the entry trigger
    (the setup rarely fires on a stale trend — you want the recent
    interaction with support/resistance for edge)

VWAP is deliberately session-based (resets at market open). On intraday
bars this is meaningful — it's what institutional traders benchmark
against. On daily bars VWAP degenerates to close-price and adds no
information, so this service only accepts intraday intervals.

Returns a Verdict enum (Strong Bull / Bull / Neutral / Bear / Strong Bear)
+ per-component breakdown + one-line rationale + numeric score for
consumers that want raw data.

Redis cache: each (ticker, interval) result cached for CACHE_TTL_SEC.
Watchlist/portfolio badges and the dashboard 'Top Setups' tile all
batch-fetch scores for many tickers at once, and paying the ~10s
yfinance fetch per ticker per view would be brutal — the cache means
the first pageview computes fresh and subsequent hits (within TTL)
return instantly. yfinance intraday data itself is ~15-min delayed
so a 5-min cache doesn't cost freshness.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

import numpy as np
import pandas as pd
import redis.asyncio as aioredis

from app.config import get_settings
from app.services.market_data import MarketDataService

log = logging.getLogger(__name__)

# Redis cache: key = momentum:{ticker}:{interval}. TTL matches roughly
# one intraday bar refresh — enough to absorb concurrent badge fetches
# on a page load without going stale.
CACHE_KEY_TEMPLATE = "momentum:{ticker}:{interval}"
CACHE_TTL_SEC = 300  # 5 min

# Shared aioredis client per process. Same pattern as heatmap service.
_redis_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_client


# Only these are supported — the setup is intraday by design. 1d is
# explicitly excluded (see module docstring for reason).
SupportedInterval = Literal["5m", "15m", "1h"]
ALLOWED_INTERVALS: tuple[str, ...] = ("5m", "15m", "1h")

# yfinance's intraday history limits: 5m/15m up to ~60d, 1h up to ~730d.
# We only need enough bars to compute 50 EMA (~100 bars ideal), plus
# recent history for the bounce-trigger check.
PERIOD_FOR_INTERVAL: dict[str, str] = {
    "5m": "5d",
    "15m": "10d",
    "1h": "1mo",
}

Verdict = Literal["strong_bull", "bull", "neutral", "bear", "strong_bear"]
Direction = Literal["bullish", "bearish", "neutral"]

# Trigger lookback: how recently a bounce/cross has to have happened
# to count as a fresh entry signal. Expressed in bars.
TRIGGER_LOOKBACK = 6

# "Bounce" definition: within one bar the low touched at/below the
# level and the close finished above it (with a small tolerance).
BOUNCE_TOLERANCE_PCT = 0.005  # 0.5% — support/resistance is fuzzy


@dataclass
class Component:
    """One of the four score components — carries its own signed
    contribution (+1 bull, -1 bear, 0 neutral) plus a display label
    the UI renders verbatim."""
    key: str
    label: str
    detail: str
    direction: Direction

    @property
    def contribution(self) -> int:
        return {"bullish": 1, "bearish": -1, "neutral": 0}[self.direction]


@dataclass
class MomentumScore:
    ticker: str
    interval: str
    verdict: Verdict
    score: int              # signed −4..+4 (sum of component contributions)
    score_abs: int          # 0..4 magnitude, for UI progress bars
    direction: Direction    # bullish/bearish/neutral overall
    components: list[Component]
    rationale: str
    price: float | None
    vwap: float | None
    ema9: float | None
    ema20: float | None
    ema50: float | None
    updated_at: str         # ISO string; UI shows "updated at X" freshness


class MomentumScoreError(RuntimeError):
    """Raised on data unavailability. Caller renders a friendly message."""


class MomentumScoreService:
    def __init__(self):
        self._mds = MarketDataService()

    # ── Cache helpers ─────────────────────────────────────────────
    # Redis stores the serialised MomentumScore as JSON. Cache misses
    # and Redis failures both degrade to a fresh compute — never crash
    # a request over a cache issue.

    @staticmethod
    def _cache_key(ticker: str, interval: str) -> str:
        return CACHE_KEY_TEMPLATE.format(ticker=ticker.upper(), interval=interval)

    async def _cache_get(self, ticker: str, interval: str) -> MomentumScore | None:
        try:
            raw = await _redis().get(self._cache_key(ticker, interval))
            if not raw:
                return None
            data = json.loads(raw)
            # Rehydrate — Component is a dataclass too, need to reconstruct
            comps = [Component(**c) for c in data.pop("components", [])]
            return MomentumScore(components=comps, **data)
        except Exception:
            log.debug("Momentum cache GET failed for %s@%s", ticker, interval, exc_info=True)
            return None

    async def _cache_set(self, score: MomentumScore) -> None:
        try:
            await _redis().set(
                self._cache_key(score.ticker, score.interval),
                json.dumps(asdict(score)),
                ex=CACHE_TTL_SEC,
            )
        except Exception:
            log.debug("Momentum cache SET failed for %s@%s", score.ticker, score.interval, exc_info=True)

    # ── Compute ───────────────────────────────────────────────────

    async def compute(
        self,
        ticker: str,
        interval: SupportedInterval = "15m",
        *,
        use_cache: bool = True,
    ) -> MomentumScore:
        if interval not in ALLOWED_INTERVALS:
            raise MomentumScoreError(
                f"Interval {interval!r} not supported. Use one of {ALLOWED_INTERVALS}."
            )

        if use_cache:
            cached = await self._cache_get(ticker, interval)
            if cached is not None:
                return cached

        df = await self._mds.get_ohlcv_dataframe(
            ticker, period=PERIOD_FOR_INTERVAL[interval], interval=interval,
        )
        if df is None or df.empty or len(df) < 50:
            raise MomentumScoreError(
                f"Not enough intraday data for {ticker} at {interval} — need 50+ bars."
            )

        df = df.dropna(subset=["Close", "Open", "High", "Low", "Volume"]).copy()
        if len(df) < 50:
            raise MomentumScoreError(f"Not enough clean bars for {ticker} at {interval}.")

        # ── Indicators ────────────────────────────────────────────────
        df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()
        df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["vwap"] = _session_vwap(df)

        last = df.iloc[-1]
        price = float(last["Close"])
        ema9 = float(last["ema9"])
        ema20 = float(last["ema20"])
        ema50 = float(last["ema50"])
        vwap = float(last["vwap"]) if not pd.isna(last["vwap"]) else float("nan")

        # ── Components ────────────────────────────────────────────────
        components: list[Component] = [
            _vwap_component(price, vwap),
            _stack_component(ema9, ema20, ema50),
            _price_vs_emas_component(price, ema9, ema20, ema50),
            _trigger_component(df),
        ]

        score = sum(c.contribution for c in components)
        score_abs = abs(score)
        direction: Direction = (
            "bullish" if score > 0
            else "bearish" if score < 0
            else "neutral"
        )
        verdict = _verdict(score)
        rationale = _rationale(components, direction)

        result = MomentumScore(
            ticker=ticker.upper(),
            interval=interval,
            verdict=verdict,
            score=score,
            score_abs=score_abs,
            direction=direction,
            components=components,
            rationale=rationale,
            price=price,
            vwap=vwap if not np.isnan(vwap) else None,
            ema9=ema9,
            ema20=ema20,
            ema50=ema50,
            updated_at=datetime.now(UTC).isoformat(),
        )
        await self._cache_set(result)
        return result

    async def compute_many(
        self,
        tickers: list[str],
        interval: SupportedInterval = "15m",
        *,
        concurrency: int = 8,
    ) -> dict[str, MomentumScore | None]:
        """Batch-compute scores for many tickers. Result is a dict
        keyed by uppercase ticker; a value of None means the score
        couldn't be computed for that ticker (delisted, thin history,
        transport error). Never raises — batching should always return
        a partial answer so callers can render whatever succeeded.

        Concurrency is capped both for yfinance politeness and to keep
        the backend event loop responsive under many-ticker requests
        (e.g. the dashboard top-setups tile pulling 70+ names)."""
        sem = asyncio.Semaphore(concurrency)

        async def _one(t: str) -> tuple[str, MomentumScore | None]:
            async with sem:
                try:
                    return t.upper(), await self.compute(t, interval=interval)
                except MomentumScoreError:
                    # Expected: not enough data for this ticker
                    return t.upper(), None
                except Exception as exc:
                    log.warning("Momentum compute_many: %s failed — %s", t, exc)
                    return t.upper(), None

        # Deduplicate to avoid double-work if caller passes duplicates.
        unique = sorted({t.upper() for t in tickers if t})
        results = await asyncio.gather(*(_one(t) for t in unique))
        return dict(results)


# ────────────────────────────────────────────────────────────────────
# Indicator + component helpers
# ────────────────────────────────────────────────────────────────────


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP. Resets at each new trading-day boundary
    in the intraday index — cumulative typical-price × volume divided
    by cumulative volume, restarting when the calendar date changes.

    Uses the DataFrame's tz-aware DatetimeIndex date attribute to
    partition sessions. yfinance returns America/New_York-localized
    timestamps for US tickers; grouping by date on that index gives
    the correct 09:30 → 16:00 session boundary."""
    idx = pd.to_datetime(df.index)
    # normalize to session date; works whether index is tz-aware or naive
    if getattr(idx, "tz", None) is not None:
        session = idx.tz_convert("America/New_York").normalize()
    else:
        session = idx.normalize()
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    tpv = typical * df["Volume"]
    # groupby session, then cumulative on each group; align back to df index
    grouped = pd.DataFrame({"tpv": tpv, "vol": df["Volume"]}).groupby(session)
    cum_tpv = grouped["tpv"].cumsum()
    cum_vol = grouped["vol"].cumsum()
    return cum_tpv / cum_vol.replace(0, np.nan)


def _vwap_component(price: float, vwap: float) -> Component:
    if np.isnan(vwap) or vwap <= 0:
        return Component(
            key="vwap",
            label="VWAP",
            detail="not available",
            direction="neutral",
        )
    diff_pct = (price - vwap) / vwap * 100
    if diff_pct > 0.15:
        return Component(
            key="vwap",
            label="Above VWAP",
            detail=f"+{diff_pct:.2f}%",
            direction="bullish",
        )
    if diff_pct < -0.15:
        return Component(
            key="vwap",
            label="Below VWAP",
            detail=f"{diff_pct:.2f}%",
            direction="bearish",
        )
    return Component(
        key="vwap",
        label="At VWAP",
        detail=f"{diff_pct:+.2f}%",
        direction="neutral",
    )


def _stack_component(ema9: float, ema20: float, ema50: float) -> Component:
    if ema9 > ema20 > ema50:
        return Component(
            key="stack",
            label="EMA stack bullish",
            detail="9 > 20 > 50",
            direction="bullish",
        )
    if ema9 < ema20 < ema50:
        return Component(
            key="stack",
            label="EMA stack bearish",
            detail="9 < 20 < 50",
            direction="bearish",
        )
    return Component(
        key="stack",
        label="EMA stack mixed",
        detail=f"9={ema9:.2f} 20={ema20:.2f} 50={ema50:.2f}",
        direction="neutral",
    )


def _price_vs_emas_component(price: float, ema9: float, ema20: float, ema50: float) -> Component:
    above = sum(1 for e in (ema9, ema20, ema50) if price > e)
    if above == 3:
        return Component(
            key="price_vs_emas",
            label="Price above all 3",
            detail="9EMA / 20EMA / 50EMA",
            direction="bullish",
        )
    if above == 0:
        return Component(
            key="price_vs_emas",
            label="Price below all 3",
            detail="9EMA / 20EMA / 50EMA",
            direction="bearish",
        )
    return Component(
        key="price_vs_emas",
        label=f"Above {above}/3 EMAs",
        detail="mixed",
        direction="neutral",
    )


def _trigger_component(df: pd.DataFrame) -> Component:
    """Detect a recent bounce off VWAP or 9/20/50 EMA. Bounce = the bar's
    Low touched at/below the level and the Close finished above it (with
    a small tolerance for noise). Also flags fresh 9-EMA crosses of the
    20-EMA in the same lookback window — a valid trigger for continuation
    entries."""
    recent = df.iloc[-TRIGGER_LOOKBACK:]
    for offset_from_end, (_, row) in enumerate(reversed(list(recent.iterrows()))):
        # offset_from_end = 0 for the most recent bar, 1 for one back, etc.
        for level_key, level_val in (
            ("VWAP", row.get("vwap")),
            ("9 EMA", row["ema9"]),
            ("20 EMA", row["ema20"]),
            ("50 EMA", row["ema50"]),
        ):
            if level_val is None or pd.isna(level_val):
                continue
            tolerance = level_val * BOUNCE_TOLERANCE_PCT
            # Bull bounce
            if row["Low"] <= level_val + tolerance and row["Close"] > level_val:
                bars_ago = offset_from_end
                return Component(
                    key="trigger",
                    label="Bull bounce trigger",
                    detail=f"off {level_key} · {bars_ago} bar{'s' if bars_ago != 1 else ''} ago",
                    direction="bullish",
                )
            # Bear rejection
            if row["High"] >= level_val - tolerance and row["Close"] < level_val:
                bars_ago = offset_from_end
                return Component(
                    key="trigger",
                    label="Bear rejection trigger",
                    detail=f"off {level_key} · {bars_ago} bar{'s' if bars_ago != 1 else ''} ago",
                    direction="bearish",
                )

    # Fresh 9-EMA/20-EMA cross in the window
    for offset_from_end, (_, row) in enumerate(reversed(list(recent.iterrows()))):
        pos = len(df) - 1 - offset_from_end
        if pos < 1:
            continue
        prev = df.iloc[pos - 1]
        if prev["ema9"] < prev["ema20"] and row["ema9"] > row["ema20"]:
            return Component(
                key="trigger",
                label="9/20 EMA bull cross",
                detail=f"{offset_from_end} bar{'s' if offset_from_end != 1 else ''} ago",
                direction="bullish",
            )
        if prev["ema9"] > prev["ema20"] and row["ema9"] < row["ema20"]:
            return Component(
                key="trigger",
                label="9/20 EMA bear cross",
                detail=f"{offset_from_end} bar{'s' if offset_from_end != 1 else ''} ago",
                direction="bearish",
            )

    return Component(
        key="trigger",
        label="No fresh trigger",
        detail=f"no bounce/cross in last {TRIGGER_LOOKBACK} bars",
        direction="neutral",
    )


def _verdict(score: int) -> Verdict:
    if score >= 4:
        return "strong_bull"
    if score >= 2:
        return "bull"
    if score <= -4:
        return "strong_bear"
    if score <= -2:
        return "bear"
    return "neutral"


def _rationale(components: list[Component], direction: Direction) -> str:
    """One-line human-readable summary. Highlights the strongest
    contributors matching the overall direction."""
    if direction == "neutral":
        return "Mixed signals across VWAP, EMA stack, and recent price action — no clear edge."
    matching = [c for c in components if c.direction == ("bullish" if direction == "bullish" else "bearish")]
    if not matching:
        # shouldn't happen given how score is derived, but defensive
        return "Signals present but not directionally consistent."
    parts = []
    if any(c.key == "vwap" for c in matching):
        parts.append("VWAP as support" if direction == "bullish" else "VWAP as resistance")
    if any(c.key == "stack" for c in matching):
        parts.append(f"EMA stack {direction[:4]}ish")
    if any(c.key == "price_vs_emas" for c in matching):
        parts.append("price aligned with EMAs")
    trig = next((c for c in matching if c.key == "trigger"), None)
    if trig:
        parts.append(trig.label.lower())
    if not parts:
        parts = [c.label.lower() for c in matching]
    return "; ".join(parts).capitalize() + "."
