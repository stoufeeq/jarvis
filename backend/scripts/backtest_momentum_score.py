"""
Backtest of the 9/20/50 EMA + VWAP momentum score.

Walks the last N days of intraday bars for the user's tickers,
computes the momentum score at every bar using the SAME logic the
live scorer uses (imports from app.services.momentum_score — no code
duplication), records the score + forward return N bars ahead, and
aggregates by verdict.

Two questions this answers:

1. Do "Strong Bull" / "Bull" verdicts actually precede positive
   forward returns often enough to matter, or is the setup random?
2. After realistic trading costs (--cost-bps), is there anything left?

Constraints:
- yfinance intraday history: 5m/15m capped at ~60 days; 1h at ~730 days.
  Use 15m for the default — best resolution × sample size trade-off.
- Session-boundary handling: a signal at 15:59 with a 4-bar forward
  horizon would cross into next day and inherit the overnight gap.
  We drop those samples — this is a strict intraday backtest.
- Cost model: --cost-bps subtracts from every trade's return (one
  round-trip per signal). Suggested: 5-10 bps for typical liquid names,
  10-20+ for small caps.

Usage:
    docker exec jarvis-backend-1 python scripts/backtest_momentum_score.py \\
        --email stoufeeq@gmail.com --interval 15m --horizon 4

Options:
    --interval        5m | 15m | 1h. Default 15m.
    --horizon         forward-return bars. Default 4 (=1h at 15m).
    --extra-tickers   comma-separated adds. Default: SPY,QQQ.
    --skip-portfolio / --skip-watchlist
    --cost-bps        per-round-trip cost. Default 0 (gross).
    --min-samples     min per-verdict sample size to include in output.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.portfolio import BrokerType, Portfolio, Position
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem
from app.services.market_data import MarketDataService
from app.services.momentum_score import (
    ALLOWED_INTERVALS,
    PERIOD_FOR_INTERVAL,
    Verdict,
    _price_vs_emas_component,
    _session_vwap,
    _stack_component,
    _trigger_component,
    _verdict,
    _vwap_component,
)


HORIZON_LABEL = {"5m": {1: "5m", 4: "20m", 12: "1h", 26: "2h"},
                 "15m": {1: "15m", 4: "1h", 12: "3h", 26: "6.5h"},
                 "1h": {1: "1h", 4: "4h", 12: "12h", 26: "1d"}}


# ────────────────────────────────────────────────────────────────────
# User + ticker resolution — same helpers as sibling scripts
# ────────────────────────────────────────────────────────────────────


async def _resolve_user(email: str | None, user_id: int | None) -> int:
    if user_id is not None:
        return user_id
    if email is None:
        raise SystemExit("Provide --email or --user-id")
    async with AsyncSessionLocal() as db:
        row = await db.execute(select(User.id).where(User.email == email))
        uid = row.scalar_one_or_none()
        if uid is None:
            raise SystemExit(f"No user found with email {email!r}")
        return uid


async def _collect_tickers(user_id: int, include_portfolio: bool, include_watchlist: bool) -> set[str]:
    tickers: set[str] = set()
    async with AsyncSessionLocal() as db:
        if include_portfolio:
            rows = (await db.execute(
                select(Position.ticker).distinct()
                .join(Portfolio, Portfolio.id == Position.portfolio_id)
                .where(
                    Portfolio.user_id == user_id,
                    Portfolio.broker != BrokerType.paper,
                    Position.quantity > 0,
                )
            )).all()
            tickers.update(r[0].upper() for r in rows)
        if include_watchlist:
            rows = (await db.execute(
                select(WatchlistItem.ticker).distinct()
                .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
                .where(Watchlist.user_id == user_id)
            )).all()
            tickers.update(r[0].upper() for r in rows)
    return tickers


# ────────────────────────────────────────────────────────────────────
# Per-ticker walk-forward evaluation
# ────────────────────────────────────────────────────────────────────


def _session_of(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Same session-normalization used by _session_vwap. Necessary so
    we can detect end-of-day where forward returns would cross into
    the next session."""
    idx = pd.to_datetime(index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").normalize()
    else:
        idx = idx.normalize()
    return idx


def _walk_forward(df: pd.DataFrame, ticker: str, horizon: int) -> pd.DataFrame:
    """For each bar past the EMA warmup, compute the verdict at that
    bar and the forward return `horizon` bars later. Returns a
    DataFrame with columns [verdict, forward_return, entry_price]."""
    if df is None or df.empty or len(df) < 60:
        return pd.DataFrame(columns=["verdict", "forward_return", "entry_price", "ticker"])

    df = df.dropna(subset=["Close", "Open", "High", "Low", "Volume"]).copy()
    if len(df) < 60:
        return pd.DataFrame(columns=["verdict", "forward_return", "entry_price", "ticker"])

    # Precompute EMAs + VWAP once on the full series.
    df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["vwap"] = _session_vwap(df)

    session = _session_of(df.index)

    verdicts: list[Verdict] = []
    forward_returns: list[float] = []
    entry_prices: list[float] = []

    # Start at index 50 so EMA50 has warmed up; stop far enough back
    # that a `horizon`-bar look-forward exists.
    start = 50
    end = len(df) - horizon
    for i in range(start, end):
        row = df.iloc[i]
        price = float(row["Close"])
        ema9 = float(row["ema9"])
        ema20 = float(row["ema20"])
        ema50 = float(row["ema50"])
        vwap = float(row["vwap"]) if not pd.isna(row["vwap"]) else float("nan")

        # Compose the four components — pure functions from the live scorer,
        # so this exactly mirrors what the UI shows at the same point in time.
        components = [
            _vwap_component(price, vwap),
            _stack_component(ema9, ema20, ema50),
            _price_vs_emas_component(price, ema9, ema20, ema50),
            _trigger_component(df.iloc[: i + 1]),  # trigger only sees history up to now
        ]
        score = sum(c.contribution for c in components)
        verdict = _verdict(score)

        # Session-boundary check: only keep the sample if the
        # forward bar is in the same trading session. Prevents
        # overnight gap contamination.
        if session[i] != session[i + horizon]:
            continue

        exit_price = float(df.iloc[i + horizon]["Close"])
        fwd_ret = (exit_price - price) / price

        verdicts.append(verdict)
        forward_returns.append(fwd_ret)
        entry_prices.append(price)

    return pd.DataFrame({
        "verdict": verdicts,
        "forward_return": forward_returns,
        "entry_price": entry_prices,
        "ticker": [ticker] * len(verdicts),
    })


# ────────────────────────────────────────────────────────────────────
# Aggregation + reporting
# ────────────────────────────────────────────────────────────────────


VERDICT_ORDER: tuple[Verdict, ...] = (
    "strong_bull", "bull", "neutral", "bear", "strong_bear",
)


@dataclass
class VerdictStats:
    verdict: str
    n: int
    share_pct: float
    mean_pct: float
    median_pct: float
    sharpe: float
    hit_rate_pct: float
    p5_pct: float
    p95_pct: float


def _verdict_stats(pooled: pd.DataFrame) -> list[VerdictStats]:
    total = len(pooled)
    stats: list[VerdictStats] = []
    for v in VERDICT_ORDER:
        r = pooled[pooled["verdict"] == v]["forward_return"]
        if len(r) == 0:
            stats.append(VerdictStats(v, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
            continue
        s = float(r.std())
        stats.append(VerdictStats(
            verdict=v,
            n=len(r),
            share_pct=float(len(r) / total * 100),
            mean_pct=float(r.mean() * 100),
            median_pct=float(r.median() * 100),
            # sqrt(252*bars_per_day) — but we're pooling ticker-bars, not
            # a portfolio return series, so Sharpe here is a "per-signal"
            # comparability metric, not an annualised portfolio Sharpe.
            sharpe=float(r.mean() / s) if s > 0 else 0.0,
            hit_rate_pct=float((r > 0).sum() / len(r) * 100),
            p5_pct=float(np.percentile(r, 5) * 100),
            p95_pct=float(np.percentile(r, 95) * 100),
        ))
    return stats


def _hr(w: int = 108) -> str:
    return "─" * w


def _print_distribution(stats: list[VerdictStats], horizon: int, interval: str) -> None:
    horizon_label = HORIZON_LABEL.get(interval, {}).get(horizon, f"{horizon} bars")
    print(f"\n{_hr()}")
    print(f" VERDICT DISTRIBUTION AND FORWARD RETURN  (horizon = {horizon} bars ≈ {horizon_label})")
    print(_hr())
    print(f"  {'Verdict':<14} {'n':>7} {'Share%':>8} {'Mean%':>9} {'Median%':>9} "
          f"{'Sharpe':>8} {'Hit%':>7} {'p5%':>8} {'p95%':>8}")
    print("  " + "─" * 92)
    for s in stats:
        marker = "  "
        if s.verdict == "strong_bull" and s.mean_pct > 0 and s.hit_rate_pct > 55:
            marker = "✓ "
        elif s.verdict == "strong_bear" and s.mean_pct < 0 and s.hit_rate_pct < 45:
            marker = "✓ "
        print(f"  {marker}{s.verdict:<12} {s.n:>7,} {s.share_pct:>7.1f}% "
              f"{s.mean_pct:>+8.4f} {s.median_pct:>+8.4f} "
              f"{s.sharpe:>+8.3f} {s.hit_rate_pct:>6.1f} "
              f"{s.p5_pct:>+7.2f} {s.p95_pct:>+7.2f}")
    print()
    print("  ✓ next to a bull row = mean > 0 AND hit rate > 55% (edge candidate)")
    print("  ✓ next to a bear row = mean < 0 AND hit rate < 45% (short/avoid candidate)")


def _print_strategy(pooled: pd.DataFrame, gate: set[str], cost_bps: float) -> None:
    """Simulate 'trade only when verdict ∈ gate' vs unconditional
    baseline. Applies cost_bps once per trade."""
    cost = cost_bps / 10000.0

    all_ret = pooled["forward_return"] - cost
    gated_mask = pooled["verdict"].isin(gate)
    gated_ret = pooled[gated_mask]["forward_return"] - cost

    def _stats(r: pd.Series) -> tuple[float, float, float, int]:
        if len(r) == 0:
            return (0.0, 0.0, 0.0, 0)
        s = float(r.std())
        return (
            float(r.mean() * 100),                                  # mean %
            float(r.mean() / s) if s > 0 else 0.0,                  # Sharpe
            float((r > 0).sum() / len(r) * 100),                    # hit %
            len(r),
        )

    m_all, sh_all, hit_all, n_all = _stats(all_ret)
    m_g, sh_g, hit_g, n_g = _stats(gated_ret)
    activity = (n_g / n_all * 100) if n_all else 0

    label = " · ".join(sorted(gate)) if gate else "(none)"
    print(f"\n{_hr()}")
    print(f" STRATEGY SIMULATION  |  gate = {{{label}}}  |  cost = {cost_bps:.1f} bps/round-trip")
    print(_hr())
    print(f"  {'':<15} {'n':>8} {'Mean%':>10} {'Sharpe':>9} {'Hit%':>7} {'Activity%':>11}")
    print("  " + "─" * 62)
    print(f"  {'Unconditional':<15} {n_all:>8,} {m_all:>+10.4f} {sh_all:>+9.3f} {hit_all:>6.1f} {100.0:>10.1f}%")
    print(f"  {'Gated':<15} {n_g:>8,} {m_g:>+10.4f} {sh_g:>+9.3f} {hit_g:>6.1f} {activity:>10.1f}%")
    print()
    print(f"  Δ Sharpe (gated − unconditional) = {sh_g - sh_all:+.3f}")
    print(f"  Δ Mean/trade                     = {m_g - m_all:+.4f} pp")
    if sh_g > sh_all + 0.05 and hit_g > 50:
        print("  ✓ Gate improves risk-adjusted return with above-coinflip hit rate.")
    elif sh_g > sh_all:
        print("  ~ Marginal improvement; verify with a fresh out-of-sample window.")
    else:
        print("  ✗ Gate does NOT improve on unconditional. Setup is not extractable on this basket.")


def _print_legend() -> None:
    print(f"\n{_hr()}")
    print(" HOW TO READ")
    print(_hr())
    print("  For each historical intraday bar we computed the same 4-component score the")
    print("  live UI shows, then measured the forward return N bars later.")
    print("  • Mean%      = average forward return per signal (in %)")
    print("  • Sharpe     = mean/std per-signal — comparability, not annualised")
    print("  • Hit%       = fraction of forward returns > 0")
    print("  • p5/p95     = 5th/95th percentile — how wide the tail is")
    print()
    print("  DECISION GUIDE")
    print("  • Strong Bull mean > 0.15% at hit rate > 55%  →  edge candidate, proceed to paper")
    print("  • Strong Bear mean < -0.15% at hit rate < 45% →  can be used as an exit/avoid gate")
    print("  • Neither of the above                        →  no extractable edge here")
    print()
    print("  CAVEATS")
    print("  • Sample only covers up to 60 days of intraday history (yfinance limit).")
    print("  • End-of-session signals are excluded (would cross into next-day gap).")
    print("  • Costs assumed constant per trade — real spread widens on small caps.")
    print("  • yfinance intraday bars are ~15-min delayed and occasionally missing —")
    print("    treat this as directional evidence, not a production strategy signal.")


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--email", help="Scope tickers to this user's portfolio+watchlist")
    scope.add_argument("--user-id", type=int)
    parser.add_argument("--interval", default="15m", choices=list(ALLOWED_INTERVALS))
    parser.add_argument(
        "--horizon", type=int, default=4,
        help="Forward-return bars. At 15m: 4=1h, 12=3h, 26≈full session.",
    )
    parser.add_argument(
        "--extra-tickers", default="SPY,QQQ",
        help="Comma-separated to always include. Default: SPY,QQQ",
    )
    parser.add_argument("--skip-portfolio", action="store_true")
    parser.add_argument("--skip-watchlist", action="store_true")
    parser.add_argument(
        "--cost-bps", type=float, default=0.0,
        help="Per-round-trip cost in basis points (see backtest_overnight_regime.py for guidance).",
    )
    parser.add_argument(
        "--min-samples", type=int, default=100,
        help="Suppress verdict rows with fewer than this many samples in the strategy sim gate.",
    )
    args = parser.parse_args()

    user_id = await _resolve_user(args.email, args.user_id)
    tickers = await _collect_tickers(
        user_id,
        include_portfolio=not args.skip_portfolio,
        include_watchlist=not args.skip_watchlist,
    )
    for t in args.extra_tickers.split(","):
        if t.strip():
            tickers.add(t.strip().upper())
    ticker_list = sorted(tickers)

    print(f"Fetching intraday OHLC ({args.interval}, period={PERIOD_FOR_INTERVAL[args.interval]}) "
          f"for {len(ticker_list)} tickers ...")

    mds = MarketDataService()
    sem = asyncio.Semaphore(6)

    async def _one(ticker: str) -> pd.DataFrame | None:
        async with sem:
            try:
                df = await mds.get_ohlcv_dataframe(
                    ticker, period=PERIOD_FOR_INTERVAL[args.interval], interval=args.interval,
                )
                out = _walk_forward(df, ticker, horizon=args.horizon)
                if len(out) == 0:
                    return None
                return out
            except Exception as exc:
                print(f"  {ticker}: error — {exc}")
                return None

    results = await asyncio.gather(*(_one(t) for t in ticker_list))
    kept = [r for r in results if r is not None and len(r) > 0]
    if not kept:
        raise SystemExit("No tickers returned usable intraday data (weekends / delisted / thin history?).")

    pooled = pd.concat(kept, ignore_index=True)
    print(f"Pooled: {len(pooled):,} signal-samples across {pooled['ticker'].nunique()} tickers.")

    if args.cost_bps > 0:
        print(f"Cost model: {args.cost_bps:.1f} bps per round-trip applied to strategy simulation.")

    stats = _verdict_stats(pooled)
    _print_distribution(stats, args.horizon, args.interval)

    # Two strategy sims — the strict "only strong bull" and the looser
    # "bull or strong bull". Both against unconditional baseline.
    strict_gate = {"strong_bull"}
    strict_n = int(pooled["verdict"].isin(strict_gate).sum())
    if strict_n >= args.min_samples:
        _print_strategy(pooled, strict_gate, args.cost_bps)
    else:
        print(f"\n(Skipping strict 'strong_bull only' sim — only {strict_n} samples, need {args.min_samples}.)")

    loose_gate = {"strong_bull", "bull"}
    loose_n = int(pooled["verdict"].isin(loose_gate).sum())
    if loose_n >= args.min_samples:
        _print_strategy(pooled, loose_gate, args.cost_bps)
    else:
        print(f"\n(Skipping loose 'bull+strong_bull' sim — only {loose_n} samples.)")

    _print_legend()


if __name__ == "__main__":
    asyncio.run(main())
