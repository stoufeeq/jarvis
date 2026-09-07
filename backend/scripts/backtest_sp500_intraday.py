"""
Capital-constrained backtest: S&P 500, buy strong-bull momentum near the
open, sell at the close, flat overnight.

Differs from backtest_intraday_momentum.py in two ways that matter:

  1. Universe is the full S&P 500 rather than one user's watchlist, so
     the result isn't a read on whatever sector that watchlist happens
     to concentrate in.
  2. It simulates an actual ACCOUNT rather than averaging per-trade
     returns. With a fixed pot you cannot take every signal — on a
     typical day far more names qualify than you can fund — so position
     sizing, slot allocation and compounding all bind. Per-trade means
     hide that entirely.

The account model is where day trading usually dies, and the reason it's
modelled explicitly here: brokerage commission is a FIXED dollar amount
per side. Splitting $10,000 across 10 positions makes each one $1,000,
where IBKR's ~$2.09 round trip is ~21bps — several times the size of the
per-trade edge this strategy is looking for. Fewer, larger positions pay
proportionally less commission but concentrate risk. That tradeoff is a
real dial, so --max-positions is a real parameter.

6 months requires 1h bars: yfinance caps 15m history at 60 days.
At 1h a session is 7 bars, so entry bar 1 is ~10:30 ET.

Usage:
    docker exec jarvis-backend-1 python scripts/backtest_sp500_intraday.py \\
        --capital 10000 --max-positions 10

    # re-runs reuse the cached bars, so sweeping is fast
    docker exec jarvis-backend-1 python scripts/backtest_sp500_intraday.py \\
        --capital 10000 --max-positions 3
"""

import argparse
import asyncio
import math
import os
import pickle
import sys
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")

from app.data.sp500 import SP500
from app.services.market_data import MarketDataService
from app.services.momentum_score import (
    _price_vs_emas_component,
    _session_vwap,
    _stack_component,
    _trigger_component,
    _verdict,
    _vwap_component,
)

CACHE_PATH = "/tmp/sp500_intraday_bars.pkl"
EMA_WARMUP_BARS = 50

# Weight per ticker, for the liquidity-ranked slot allocation.
WEIGHT = {s["ticker"]: s.get("weight", 0.0) for s in SP500}


# ────────────────────────────────────────────────────────────────────
# Data
# ────────────────────────────────────────────────────────────────────


async def _fetch_all(tickers: list[str], period: str, interval: str, concurrency: int) -> dict:
    mds = MarketDataService()
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, pd.DataFrame] = {}
    done = 0

    async def _one(t: str):
        nonlocal done
        async with sem:
            try:
                df = await mds.get_ohlcv_dataframe(t, period=period, interval=interval)
                if df is not None and not df.empty and len(df) >= EMA_WARMUP_BARS + 10:
                    out[t] = df
            except Exception:
                pass
            done += 1
            if done % 50 == 0:
                print(f"    … {done}/{len(tickers)} fetched", flush=True)

    await asyncio.gather(*(_one(t) for t in tickers))
    return out


def _load_or_fetch(tickers, period, interval, concurrency, refresh: bool) -> dict:
    """Bars are cached to /tmp so parameter sweeps don't refetch 452
    tickers each time. The cache key includes period+interval so a
    different window can't silently reuse the wrong bars."""
    key = f"{period}:{interval}:{len(tickers)}"
    if not refresh and os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "rb") as f:
                blob = pickle.load(f)
            if blob.get("key") == key:
                age_min = (datetime.now().timestamp() - blob["ts"]) / 60
                print(f"Using cached bars ({len(blob['data'])} tickers, {age_min:.0f} min old). "
                      f"Pass --refresh to refetch.")
                return blob["data"]
        except Exception:
            pass

    print(f"Fetching {interval} bars (period={period}) for {len(tickers)} tickers …")
    data = asyncio.run(_fetch_all(tickers, period, interval, concurrency))
    try:
        with open(CACHE_PATH, "wb") as f:
            pickle.dump({"key": key, "ts": datetime.now().timestamp(), "data": data}, f)
    except Exception:
        pass
    return data


# ────────────────────────────────────────────────────────────────────
# Scoring
# ────────────────────────────────────────────────────────────────────


def _sessions(index) -> pd.DatetimeIndex:
    idx = pd.to_datetime(index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").normalize()
    else:
        idx = idx.normalize()
    return idx


@dataclass
class Candidate:
    session: str
    ticker: str
    verdict: str
    entry: float
    exit: float
    vwap_gap_pct: float
    weight: float


def _score_ticker(ticker: str, df: pd.DataFrame, entry_bar: int, min_bars: int) -> list[Candidate]:
    """One candidate per session: the verdict at the entry bar plus the
    entry/exit prices. Returns every session regardless of verdict so the
    simulator can also measure the unconditional baseline."""
    df = df.dropna(subset=["Close", "Open", "High", "Low", "Volume"]).copy()
    if len(df) < EMA_WARMUP_BARS + 10:
        return []

    df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["vwap"] = _session_vwap(df)

    sess = _sessions(df.index)
    out: list[Candidate] = []

    for day, positions in pd.Series(range(len(df)), index=sess).groupby(level=0):
        idxs = list(positions.values)
        if len(idxs) < min_bars or entry_bar >= len(idxs):
            continue
        gi = idxs[entry_bar]
        if gi < EMA_WARMUP_BARS:
            continue

        row = df.iloc[gi]
        price = float(row["Close"])
        if price <= 0:
            continue
        vwap = float(row["vwap"]) if not pd.isna(row["vwap"]) else float("nan")

        components = [
            _vwap_component(price, vwap),
            _stack_component(float(row["ema9"]), float(row["ema20"]), float(row["ema50"])),
            _price_vs_emas_component(price, float(row["ema9"]), float(row["ema20"]), float(row["ema50"])),
            _trigger_component(df.iloc[: gi + 1]),   # no lookahead
        ]
        verdict = _verdict(sum(c.contribution for c in components))

        gap = 0.0
        if vwap == vwap and vwap > 0:
            gap = (price - vwap) / vwap * 100

        out.append(Candidate(
            session=str(day)[:10],
            ticker=ticker,
            verdict=verdict,
            entry=price,
            exit=float(df.iloc[idxs[-1]]["Close"]),
            vwap_gap_pct=gap,
            weight=WEIGHT.get(ticker, 0.0),
        ))
    return out


# ────────────────────────────────────────────────────────────────────
# Portfolio simulation
# ────────────────────────────────────────────────────────────────────


@dataclass
class SimResult:
    equity: list[tuple[str, float]] = field(default_factory=list)
    n_trades: int = 0
    n_sessions_traded: int = 0
    n_sessions_total: int = 0
    gross_pnl: float = 0.0
    commission_paid: float = 0.0
    spread_paid: float = 0.0
    wins: int = 0
    losses: int = 0
    per_trade_returns: list[float] = field(default_factory=list)
    skipped_too_small: int = 0


def _simulate(
    candidates: list[Candidate],
    *,
    capital: float,
    max_positions: int,
    gate: set[str],
    commission_per_side: float,
    spread_bps: float,
    rank: str,
    min_position_dollars: float,
) -> SimResult:
    """Walk sessions chronologically, compounding a single pot.

    Each session: filter to the gate, rank, take the top `max_positions`,
    split available capital equally, buy at entry and sell at close.
    Commission is charged per side as a fixed dollar amount — the whole
    point of simulating an account rather than averaging returns.
    """
    res = SimResult()
    by_session: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_session.setdefault(c.session, []).append(c)

    equity = capital
    for session in sorted(by_session):
        res.n_sessions_total += 1
        res.equity.append((session, equity))

        picks = [c for c in by_session[session] if c.verdict in gate]
        if not picks:
            continue

        if rank == "weight":          # largest index weight = most liquid
            picks.sort(key=lambda c: -c.weight)
        elif rank == "vwap_gap":      # most extended above VWAP
            picks.sort(key=lambda c: -c.vwap_gap_pct)
        else:                          # deterministic, no implicit hypothesis
            picks.sort(key=lambda c: c.ticker)
        picks = picks[:max_positions]

        per_slot = equity / len(picks)
        if per_slot < min_position_dollars:
            # Too little capital to justify the fixed commission — a real
            # trader would take fewer, larger positions rather than pay
            # 40bps to enter a $200 line.
            usable = max(1, int(equity // min_position_dollars))
            picks = picks[:usable]
            per_slot = equity / len(picks)
            if per_slot < min_position_dollars:
                res.skipped_too_small += 1
                continue

        res.n_sessions_traded += 1
        session_pnl = 0.0
        for c in picks:
            shares = per_slot / c.entry
            gross = (c.exit - c.entry) * shares
            spread = per_slot * (spread_bps / 10000.0)
            commission = commission_per_side * 2
            net = gross - spread - commission

            res.n_trades += 1
            res.gross_pnl += gross
            res.commission_paid += commission
            res.spread_paid += spread
            res.per_trade_returns.append(net / per_slot)
            if net > 0:
                res.wins += 1
            else:
                res.losses += 1
            session_pnl += net

        equity += session_pnl

    res.equity.append(("final", equity))
    return res


# ────────────────────────────────────────────────────────────────────
# Reporting
# ────────────────────────────────────────────────────────────────────


def _hr(w: int = 100) -> str:
    return "─" * w


def _metrics(res: SimResult, capital: float) -> dict:
    curve = [e for _, e in res.equity]
    final = curve[-1]
    total_ret = (final / capital - 1) * 100

    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak * 100)

    daily = pd.Series(curve).pct_change().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if len(daily) > 1 and daily.std() > 0 else 0.0

    r = pd.Series(res.per_trade_returns)
    t_stat = 0.0
    if len(r) > 1 and r.std() > 0:
        t_stat = float(r.mean() / (r.std() / np.sqrt(len(r))))

    sessions = max(1, res.n_sessions_total)
    ann = (pow(final / capital, 252 / sessions) - 1) * 100 if final > 0 else -100.0

    return {
        "final": final, "total_ret": total_ret, "ann": ann,
        "mdd": mdd, "sharpe": sharpe, "t_stat": t_stat,
        "mean_trade_pct": float(r.mean() * 100) if len(r) else 0.0,
    }


def _print_report(res: SimResult, capital: float, args, bench: dict | None) -> None:
    m = _metrics(res, capital)
    win_rate = res.wins / max(1, res.wins + res.losses) * 100
    total_costs = res.commission_paid + res.spread_paid
    cost_pct_of_capital = total_costs / capital * 100

    print(f"\n{_hr()}")
    print(f" ACCOUNT SIMULATION — ${capital:,.0f} seed, max {args.max_positions} positions/day")
    print(_hr())
    print(f"  Sessions              {res.n_sessions_total}  "
          f"(traded on {res.n_sessions_traded}, {res.n_sessions_traded / max(1, res.n_sessions_total) * 100:.0f}%)")
    print(f"  Trades                {res.n_trades:,}")
    print(f"  Win rate              {win_rate:.1f}%")
    print()
    print(f"  Starting capital      ${capital:>12,.2f}")
    print(f"  Final capital         ${m['final']:>12,.2f}")
    print(f"  Total return          {m['total_ret']:>12,.2f}%")
    print(f"  Annualised            {m['ann']:>12,.2f}%")
    print(f"  Max drawdown          {m['mdd']:>12,.2f}%")
    print(f"  Sharpe (daily equity) {m['sharpe']:>12,.2f}")
    print()
    print(f"  Gross P&L             ${res.gross_pnl:>12,.2f}")
    print(f"  Commission            ${-res.commission_paid:>12,.2f}   "
          f"({res.n_trades:,} round trips × ${args.commission_per_side * 2:.2f})")
    print(f"  Spread                ${-res.spread_paid:>12,.2f}   ({args.spread_bps:.0f} bps/trade)")
    print(f"  {'':<21} {'─' * 14}")
    print(f"  Net P&L               ${m['final'] - capital:>12,.2f}")
    print()
    print(f"  Costs as % of seed    {cost_pct_of_capital:>12,.1f}%")
    if res.gross_pnl != 0:
        print(f"  Costs as % of gross   {total_costs / abs(res.gross_pnl) * 100:>12,.1f}%")
    print(f"  Mean net per trade    {m['mean_trade_pct']:>12,.4f}%   (t = {m['t_stat']:+.2f})")

    if res.skipped_too_small:
        print(f"\n  ⚠ {res.skipped_too_small} sessions skipped — capital fell below "
              f"${args.min_position_dollars:.0f}/position")

    if bench:
        print(f"\n{_hr()}")
        print(" BENCHMARK — SPY buy & hold, same window")
        print(_hr())
        print(f"  Total return          {bench['total_ret']:>12,.2f}%")
        print(f"  Final on ${capital:,.0f}      ${capital * (1 + bench['total_ret'] / 100):>12,.2f}")
        print(f"  Trades                {2:>12,}   (one buy, one sell)")
        delta = m["total_ret"] - bench["total_ret"]
        print(f"\n  Strategy vs benchmark {delta:>+12,.2f} pp")

    print(f"\n{_hr()}")
    print(" VERDICT")
    print(_hr())
    if m["final"] <= capital:
        print("  ✗ Lost money. Do not deploy.")
    elif abs(m["t_stat"]) < 1.96:
        print(f"  ~ Ended up, but per-trade t = {m['t_stat']:+.2f} — not distinguishable")
        print("    from zero. The gain is inside the noise band, not evidence of edge.")
    elif bench and m["total_ret"] < bench["total_ret"]:
        print("  ~ Profitable and statistically real, but underperformed simply")
        print("    holding SPY — with far more trades, screen time and tax events.")
    else:
        print("  ✓ Profitable, statistically real, and beat the benchmark.")


def _print_signal_frequency(candidates: list[Candidate], gate: set[str]) -> None:
    df = pd.DataFrame([{"session": c.session, "verdict": c.verdict} for c in candidates])
    total = len(df)
    print(f"\n{_hr()}")
    print(" SIGNAL FREQUENCY across the whole universe")
    print(_hr())
    counts = df["verdict"].value_counts()
    for v in ("strong_bull", "bull", "neutral", "bear", "strong_bear"):
        n = int(counts.get(v, 0))
        print(f"  {v:<14} {n:>8,}  {n / total * 100:>5.1f}%")
    per_day = df[df["verdict"].isin(gate)].groupby("session").size()
    if len(per_day):
        print()
        print(f"  Qualifying names per session: median {int(per_day.median())}, "
              f"min {int(per_day.min())}, max {int(per_day.max())}")
        print(f"  → with a fixed pot you can only fund a few of these per day,")
        print(f"    which is what makes slot allocation and commission bind.")


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--max-positions", type=int, default=10,
                        help="Slots per day. Fewer = larger positions = lower "
                             "commission drag but more concentration.")
    parser.add_argument("--period", default="6mo")
    parser.add_argument("--interval", default="1h",
                        help="1h is required for 6 months — yfinance caps 15m at 60 days.")
    parser.add_argument("--entry-bar", type=int, default=1,
                        help="Session bar to evaluate and enter at. At 1h, bar 1 ≈ 10:30 ET. "
                             "Bar 0 is avoided: VWAP has no volume behind it yet.")
    parser.add_argument("--min-session-bars", type=int, default=4)
    parser.add_argument("--gate", default="strong_bull")
    parser.add_argument("--rank", default="weight", choices=["weight", "vwap_gap", "ticker"],
                        help="Which names to fund when more qualify than there are slots. "
                             "'weight' favours mega-caps (best execution); 'vwap_gap' favours "
                             "the most extended; 'ticker' is deterministic and hypothesis-free.")
    parser.add_argument("--commission-per-side", type=float, default=1.05,
                        help="Fixed dollar commission per side (IBKR ~$1.09 buy / $1.00 sell).")
    parser.add_argument("--spread-bps", type=float, default=3.0,
                        help="Round-trip spread cost in bps, on top of commission.")
    parser.add_argument("--min-position-dollars", type=float, default=500,
                        help="Don't open a line smaller than this — fixed commission "
                             "makes tiny positions uneconomic.")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--refresh", action="store_true", help="Ignore the bar cache.")
    parser.add_argument("--limit-tickers", type=int, default=None,
                        help="Debug: only use the first N constituents.")
    args = parser.parse_args()

    tickers = [s["ticker"] for s in SP500]
    if args.limit_tickers:
        tickers = tickers[: args.limit_tickers]

    bars = _load_or_fetch(tickers, args.period, args.interval, args.concurrency, args.refresh)
    if not bars:
        raise SystemExit("No bars fetched.")
    print(f"Scoring {len(bars)} tickers with usable history …")

    candidates: list[Candidate] = []
    for t, df in bars.items():
        candidates.extend(_score_ticker(t, df, args.entry_bar, args.min_session_bars))
    if not candidates:
        raise SystemExit("No scoreable sessions.")

    n_sessions = len({c.session for c in candidates})
    print(f"Scored {len(candidates):,} ticker-sessions over {n_sessions} sessions "
          f"({len({c.ticker for c in candidates})} tickers).")

    gate = {g.strip() for g in args.gate.split(",") if g.strip()}
    _print_signal_frequency(candidates, gate)

    res = _simulate(
        candidates,
        capital=args.capital,
        max_positions=args.max_positions,
        gate=gate,
        commission_per_side=args.commission_per_side,
        spread_bps=args.spread_bps,
        rank=args.rank,
        min_position_dollars=args.min_position_dollars,
    )

    # Benchmark: SPY over the same window, bought at the first session's
    # entry bar and held to the last session's close.
    bench = None
    spy = bars.get("SPY")
    if spy is None:
        try:
            spy = asyncio.run(
                MarketDataService().get_ohlcv_dataframe("SPY", period=args.period, interval=args.interval)
            )
        except Exception:
            spy = None
    if spy is not None and not spy.empty:
        c = spy["Close"].dropna()
        if len(c) > 1:
            bench = {"total_ret": float((c.iloc[-1] / c.iloc[0] - 1) * 100)}

    _print_report(res, args.capital, args, bench)


if __name__ == "__main__":
    main()
