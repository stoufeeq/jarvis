"""
Backtest: buy near the open on a bullish momentum reading, sell at the close.

The exact strategy shape:
    For each trading session:
      - Wait N bars into the session (--entry-bar) so VWAP has accumulated
      - Compute the 9/20/50 EMA + VWAP momentum score at that bar
      - If the verdict is in the gate set, buy at that bar's close
      - Sell at the session's final close
      - Flat overnight

Why --entry-bar exists: VWAP is session-anchored, so at bar 1 it equals
that bar's own typical price and "price vs VWAP" carries no information.
The score only becomes meaningful once some volume has accumulated. Bar
2 is ~30 min into the session at 15m bars; bar 4 is ~1 hour. Sweeping
this parameter is the point — "at the open" is not one thing.

The baseline is unconditional buy-at-entry-bar / sell-at-close on the
same bars, so the gate is measured against the honest alternative rather
than against zero.

Two prior measurements bear on this and are worth remembering when
reading the output:
  - The momentum forward-return backtest found strong_bull at −0.007%
    over 1h and −0.16% over 3h, i.e. no edge decaying to negative.
  - The overnight backtest found the intraday (open→close) leg is the
    historically weak half of the day: basket Sharpe 0.22 intraday vs
    0.97 overnight.
This script tests whether the momentum filter selects the subset of
sessions where intraday nonetheless works.

Usage:
    docker exec jarvis-backend-1 python scripts/backtest_intraday_momentum.py \\
        --email stoufeeq@gmail.com --entry-bar 2 --cost-bps 8

    # sweep the entry bar
    for N in 1 2 4 8; do ... --entry-bar $N ; done
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
    _price_vs_emas_component,
    _session_vwap,
    _stack_component,
    _trigger_component,
    _verdict,
    _vwap_component,
)

VERDICT_ORDER = ("strong_bull", "bull", "neutral", "bear", "strong_bear")

# Bars needed before the EMA(50) is meaningful. Sessions starting before
# this point in the series are skipped entirely.
EMA_WARMUP_BARS = 50


# ────────────────────────────────────────────────────────────────────
# User + ticker resolution
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
# Per-session replay
# ────────────────────────────────────────────────────────────────────


@dataclass
class Trade:
    ticker: str
    session: str
    verdict: str
    entry_price: float
    exit_price: float
    ret: float          # gross, before costs
    bars_held: int


def _sessions(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Normalise the intraday index to session dates (same convention as
    the VWAP anchor, so sessions line up with VWAP resets)."""
    idx = pd.to_datetime(index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").normalize()
    else:
        idx = idx.normalize()
    return idx


def _replay(df: pd.DataFrame, ticker: str, entry_bar: int, min_session_bars: int) -> list[Trade]:
    """One entry per session at bar `entry_bar`, exit at the session's
    last bar. Returns every session's outcome regardless of verdict — the
    gate is applied later so the same replay serves both the gated
    strategy and the unconditional baseline."""
    if df is None or df.empty or len(df) < EMA_WARMUP_BARS + 5:
        return []

    df = df.dropna(subset=["Close", "Open", "High", "Low", "Volume"]).copy()
    if len(df) < EMA_WARMUP_BARS + 5:
        return []

    # Indicators computed once over the whole series. EMAs intentionally
    # carry across sessions (that is what an EMA is); VWAP resets daily.
    df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["vwap"] = _session_vwap(df)

    sess = _sessions(df.index)
    out: list[Trade] = []

    for day, positions in pd.Series(range(len(df)), index=sess).groupby(level=0):
        idxs = list(positions.values)
        if len(idxs) < min_session_bars:
            continue  # half day / thin data
        if entry_bar >= len(idxs):
            continue
        gi = idxs[entry_bar]          # global row index of the entry bar
        if gi < EMA_WARMUP_BARS:
            continue                  # EMAs not warm yet
        gexit = idxs[-1]

        row = df.iloc[gi]
        price = float(row["Close"])
        ema9, ema20, ema50 = float(row["ema9"]), float(row["ema20"]), float(row["ema50"])
        vwap = float(row["vwap"]) if not pd.isna(row["vwap"]) else float("nan")

        # Same four components the live scorer uses. The trigger component
        # only sees bars up to and including the entry bar — no lookahead.
        components = [
            _vwap_component(price, vwap),
            _stack_component(ema9, ema20, ema50),
            _price_vs_emas_component(price, ema9, ema20, ema50),
            _trigger_component(df.iloc[: gi + 1]),
        ]
        verdict = _verdict(sum(c.contribution for c in components))

        exit_price = float(df.iloc[gexit]["Close"])
        if price <= 0:
            continue

        out.append(Trade(
            ticker=ticker,
            session=str(day)[:10],
            verdict=verdict,
            entry_price=price,
            exit_price=exit_price,
            ret=(exit_price - price) / price,
            bars_held=gexit - gi,
        ))

    return out


# ────────────────────────────────────────────────────────────────────
# Stats + reporting
# ────────────────────────────────────────────────────────────────────


def _stats(rets: pd.Series, cost: float) -> dict:
    """Per-trade stats, net of one round-trip cost. Sharpe here is
    per-trade (mean/std), annualised at ~252 trades/yr since the
    strategy takes at most one position per session."""
    net = rets - cost
    if len(net) == 0:
        return {"n": 0, "mean_pct": 0.0, "sharpe": 0.0, "hit_pct": 0.0,
                "ann_pct": 0.0, "worst_pct": 0.0, "best_pct": 0.0}
    sd = float(net.std())
    return {
        "n": len(net),
        "mean_pct": float(net.mean() * 100),
        "sharpe": float(net.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0,
        "hit_pct": float((net > 0).sum() / len(net) * 100),
        "ann_pct": float(net.mean() * 252 * 100),
        "worst_pct": float(net.min() * 100),
        "best_pct": float(net.max() * 100),
    }


def _hr(w: int = 100) -> str:
    return "─" * w


def _print_by_verdict(pooled: pd.DataFrame, cost: float, entry_bar: int, interval: str) -> None:
    print(f"\n{_hr()}")
    print(f" OPEN→CLOSE RETURN BY MOMENTUM VERDICT AT ENTRY  "
          f"(entry = bar {entry_bar} of session, {interval} bars)")
    print(_hr())
    print(f"  {'Verdict':<14} {'n':>7} {'Share%':>8} {'Mean%':>9} {'Sharpe':>8} "
          f"{'Hit%':>7} {'Ann%':>9} {'Worst%':>8}")
    print("  " + "─" * 86)
    total = len(pooled)
    for v in VERDICT_ORDER:
        r = pooled[pooled["verdict"] == v]["ret"]
        s = _stats(r, cost)
        if s["n"] == 0:
            continue
        share = s["n"] / total * 100 if total else 0
        mark = "✓ " if (v.endswith("bull") and s["mean_pct"] > 0.10 and s["hit_pct"] > 53) else "  "
        print(f"  {mark}{v:<12} {s['n']:>7,} {share:>7.1f}% {s['mean_pct']:>+8.4f} "
              f"{s['sharpe']:>+8.2f} {s['hit_pct']:>6.1f} {s['ann_pct']:>+8.2f} {s['worst_pct']:>+7.2f}")
    print()
    print("  ✓ marks a bull verdict clearing mean > +0.10% AND hit > 53% net of cost")


def _print_strategy(pooled: pd.DataFrame, gate: set[str], cost: float) -> dict:
    label = " + ".join(sorted(gate))
    all_r = pooled["ret"]
    gated_r = pooled[pooled["verdict"].isin(gate)]["ret"]

    base = _stats(all_r, cost)
    gated = _stats(gated_r, cost)
    activity = gated["n"] / base["n"] * 100 if base["n"] else 0
    # Effective annual return accounts for the days spent flat.
    effective = gated["mean_pct"] * 252 * (activity / 100)

    print(f"\n{_hr()}")
    print(f" STRATEGY: buy when verdict in {{{label}}}, sell at close")
    print(_hr())
    print(f"  {'':<22} {'n':>7} {'Mean%':>9} {'Sharpe':>8} {'Hit%':>7} {'Ann%':>9}")
    print("  " + "─" * 68)
    print(f"  {'Unconditional (all)':<22} {base['n']:>7,} {base['mean_pct']:>+8.4f} "
          f"{base['sharpe']:>+8.2f} {base['hit_pct']:>6.1f} {base['ann_pct']:>+8.2f}")
    print(f"  {'Gated':<22} {gated['n']:>7,} {gated['mean_pct']:>+8.4f} "
          f"{gated['sharpe']:>+8.2f} {gated['hit_pct']:>6.1f} {gated['ann_pct']:>+8.2f}")
    print()
    print(f"  Traded on {activity:.1f}% of sessions → effective annual {effective:+.2f}%")
    print(f"  Δ vs unconditional: mean {gated['mean_pct'] - base['mean_pct']:+.4f} pp, "
          f"Sharpe {gated['sharpe'] - base['sharpe']:+.2f}")

    # Hit rate is reported but deliberately NOT a gate: a positive-
    # expectancy strategy that wins 48% of the time with larger winners
    # is perfectly valid, and gating on hit rate would reject it.
    # What matters is (a) positive net expectancy and (b) beating the
    # unconditional baseline on risk-adjusted return.
    sharpe_lift = gated["sharpe"] - base["sharpe"]
    if gated["mean_pct"] <= 0:
        print("  ✗ Negative expectancy net of cost. Do not deploy.")
    elif sharpe_lift > 0.3:
        print(f"  ✓ Positive expectancy AND {sharpe_lift:+.2f} Sharpe over the")
        print("    unconditional baseline — the filter is doing real work.")
        if gated["n"] < 200:
            print(f"    ⚠ Only {gated['n']} trades though — treat as provisional.")
    elif sharpe_lift > 0:
        print("  ~ Positive expectancy but only a small edge over trading every")
        print("    session. The filter adds little; check whether the effective")
        print("    annual return justifies the operational cost.")
    else:
        print("  ✗ Positive mean, but no better than trading unconditionally —")
        print("    the momentum filter is not selecting anything useful.")
    return {"gated": gated, "base": base, "activity": activity, "effective": effective}


def _print_legend(cost_bps: float) -> None:
    print(f"\n{_hr()}")
    print(" HOW TO READ")
    print(_hr())
    print(f"  Every figure is NET of {cost_bps:.1f} bps round-trip cost.")
    print("  Mean%   = average per-trade return")
    print("  Ann%    = mean × 252 (one session = one trade opportunity)")
    print("  Hit%    = share of trades closing above entry")
    print()
    print("  CONTEXT FROM PRIOR RUNS")
    print("  • Momentum forward-return backtest: strong_bull was −0.007% over 1h")
    print("    and −0.16% over 3h. No edge, decaying with horizon.")
    print("  • Overnight backtest: the open→close leg is the historically weak")
    print("    half of the day (basket Sharpe 0.22 intraday vs 0.97 overnight).")
    print("  This script asks whether the momentum filter picks out the sessions")
    print("  where intraday works anyway. A positive result here would have to")
    print("  overcome both of those.")
    print()
    print("  CAVEATS")
    print("  • Fills assumed at the bar's close. Real entries slip.")
    print("  • yfinance intraday history is capped (~60d at 15m), so the sample")
    print("    is one market regime, not many.")
    print("  • No borrow/short side — long-only, matching the paper trader.")


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--email")
    scope.add_argument("--user-id", type=int)
    parser.add_argument("--interval", default="15m", choices=list(ALLOWED_INTERVALS))
    parser.add_argument(
        "--entry-bar", type=int, default=2,
        help="Bar index within the session to evaluate and enter at. "
             "0 = the opening bar (VWAP uninformative there). "
             "At 15m: 2 ≈ 30min in, 4 ≈ 1h in.",
    )
    parser.add_argument(
        "--min-session-bars", type=int, default=10,
        help="Skip sessions with fewer bars than this (half days, thin data).",
    )
    parser.add_argument("--extra-tickers", default="SPY,QQQ")
    parser.add_argument("--skip-portfolio", action="store_true")
    parser.add_argument("--skip-watchlist", action="store_true")
    parser.add_argument(
        "--cost-bps", type=float, default=8.0,
        help="Round-trip cost in bps. Default 8 (mixed-liquidity basket at "
             "~$5k trade size). Use 3 for large-cap/ETF-only.",
    )
    parser.add_argument(
        "--gate", default="strong_bull",
        help="Comma-separated verdicts to trade. Default strong_bull. "
             "Try 'strong_bull,bull' for a looser gate.",
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

    print(f"Fetching {args.interval} bars (period={PERIOD_FOR_INTERVAL[args.interval]}) "
          f"for {len(ticker_list)} tickers …")

    mds = MarketDataService()
    sem = asyncio.Semaphore(6)

    async def _one(t: str) -> list[Trade]:
        async with sem:
            try:
                df = await mds.get_ohlcv_dataframe(
                    t, period=PERIOD_FOR_INTERVAL[args.interval], interval=args.interval,
                )
                return _replay(df, t, args.entry_bar, args.min_session_bars)
            except Exception as exc:
                print(f"  {t}: error — {exc}")
                return []

    results = await asyncio.gather(*(_one(t) for t in ticker_list))
    trades = [tr for group in results for tr in group]
    if not trades:
        raise SystemExit("No sessions produced a usable entry (weekend? thin data?).")

    pooled = pd.DataFrame([{
        "ticker": t.ticker, "session": t.session, "verdict": t.verdict,
        "ret": t.ret, "bars_held": t.bars_held,
    } for t in trades])

    print(f"Replayed {len(pooled):,} ticker-sessions across "
          f"{pooled['ticker'].nunique()} tickers, "
          f"{pooled['session'].nunique()} sessions. "
          f"Median hold {int(pooled['bars_held'].median())} bars.")

    cost = args.cost_bps / 10000.0
    _print_by_verdict(pooled, cost, args.entry_bar, args.interval)

    gate = {g.strip() for g in args.gate.split(",") if g.strip()}
    _print_strategy(pooled, gate, cost)
    _print_legend(args.cost_bps)


if __name__ == "__main__":
    asyncio.run(main())
