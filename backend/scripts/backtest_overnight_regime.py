"""
Regime-filtered overnight backtest.

Same strategy as backtest_overnight.py — buy at close, sell at next
open — but every night is tagged with the market regime in effect at
the moment of that close, using the SAME classifier (app.services.regime.classify)
that populates the market_regimes table.

Regimes:
  bull_low_vol    SPX > 200-SMA  and  VIX < 20
  bull_high_vol   SPX > 200-SMA  and  20 <= VIX < 30
  bull_crisis     SPX > 200-SMA  and  VIX >= 30
  bear_low_vol    SPX < 200-SMA  and  VIX < 20
  bear_high_vol   SPX < 200-SMA  and  20 <= VIX < 30
  bear_crisis     SPX < 200-SMA  and  VIX >= 30

Two questions answered:
  1. Pooled across all tickers, which regime carries the overnight edge?
     If bull_low_vol is where most of the return + best Sharpe lives,
     filtering trades to that regime should improve risk-adjusted returns.
  2. Per ticker, how much does the best regime beat the unconditional
     Sharpe? Large gaps ⇒ regime gating is worth applying to that name.

Usage:
    docker exec jarvis-backend-1 python scripts/backtest_overnight_regime.py \\
        --email stoufeeq@gmail.com --period 2y
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.portfolio import BrokerType, Portfolio, Position
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem
from app.services.market_data import MarketDataService
from app.services.regime import (
    REGIMES,
    SMA_WINDOW,
    SPX_TICKER,
    VIX_TICKER,
    _normalize_index,
    classify,
)


# ────────────────────────────────────────────────────────────────────
# User + ticker resolution (same as backtest_overnight.py)
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
# Regime timeline construction
# ────────────────────────────────────────────────────────────────────


async def _build_regime_series() -> pd.Series:
    """Return a Series indexed by trading-date whose value is the
    regime label as of that date's SPX close. Fetches 5 years of SPY +
    VIX so the 200-day SMA is valid across a 2y-4y user sample window.

    Uses the same classifier + _normalize_index helper as the app's
    regime backfill — so bucketing in this script matches what would
    be stored in the market_regimes table."""
    mds = MarketDataService()
    spx_df = await mds.get_ohlcv_dataframe(SPX_TICKER, period="5y", interval="1d")
    vix_df = await mds.get_ohlcv_dataframe(VIX_TICKER, period="5y", interval="1d")

    if spx_df is None or vix_df is None or spx_df.empty or vix_df.empty:
        raise SystemExit("Could not fetch SPY / VIX history for regime classification.")

    spx_close = _normalize_index(spx_df["Close"].dropna())
    vix_close = _normalize_index(vix_df["Close"].dropna())
    sma200 = spx_close.rolling(SMA_WINDOW).mean()

    joined = pd.DataFrame({"spx": spx_close, "sma200": sma200, "vix": vix_close}).dropna()
    return joined.apply(
        lambda r: classify(float(r["spx"]), float(r["sma200"]), float(r["vix"])),
        axis=1,
    )


# ────────────────────────────────────────────────────────────────────
# Overnight-return + regime attach
# ────────────────────────────────────────────────────────────────────


def _overnight_with_regime(df: pd.DataFrame, regime_series: pd.Series) -> pd.DataFrame:
    """Return DataFrame with columns [return, regime] indexed by the
    SELL date (next open). Regime is the one classified at the BUY
    (previous close) — that's the info the decision-maker would have
    had at trade time."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["return", "regime"])

    df = df.dropna(subset=["Close", "Open"])
    if len(df) < 20:
        return pd.DataFrame(columns=["return", "regime"])

    # Normalize the ticker's index to naive midnight so the reindex
    # against regime_series works (regime_series is naive midnight-indexed).
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    df = df.copy()
    df.index = idx.normalize()

    prev_close = df["Close"].shift(1)
    overnight = (df["Open"] - prev_close) / prev_close

    # Regime at the BUY date (previous close). The overnight return
    # dated at t was earned on a position opened at t-1's close, so
    # shift regime forward by 1 trading day to align with the return
    # index. (Regime is SMA200-based → t vs t-1 nearly identical, but
    # this is the honest way to do it.)
    regime_at_buy = regime_series.shift(1)

    out = pd.DataFrame({
        "return": overnight,
        "regime": regime_at_buy.reindex(df.index),
    }).dropna()
    return out


# ────────────────────────────────────────────────────────────────────
# Stats
# ────────────────────────────────────────────────────────────────────


@dataclass
class RegimeStat:
    regime: str
    n_nights: int
    mean_pct: float
    sharpe: float
    hit_rate_pct: float
    best_pct: float
    worst_pct: float


def _regime_stats(df: pd.DataFrame) -> list[RegimeStat]:
    """Group by regime, compute per-regime stats. df has columns
    [return, regime] — one row per ticker-night."""
    stats: list[RegimeStat] = []
    for regime in REGIMES:
        rows = df[df["regime"] == regime]["return"]
        if len(rows) < 5:
            continue
        s = float(rows.std())
        stats.append(RegimeStat(
            regime=regime,
            n_nights=len(rows),
            mean_pct=float(rows.mean() * 100),
            sharpe=float(rows.mean() / s * (252 ** 0.5)) if s > 0 else 0.0,
            hit_rate_pct=float((rows > 0).sum() / len(rows) * 100),
            best_pct=float(rows.max() * 100),
            worst_pct=float(rows.min() * 100),
        ))
    return stats


def _per_ticker_regime_best(pooled: pd.DataFrame, min_nights_per_regime: int = 30) -> pd.DataFrame:
    """For each ticker, find the regime with the best Sharpe (subject
    to the min-nights threshold — a 3-night regime with Sharpe 5 is
    just noise) and compare to that ticker's unconditional stats."""
    out_rows: list[dict] = []
    for ticker in pooled["ticker"].unique():
        subset = pooled[pooled["ticker"] == ticker]

        # Unconditional stats
        r_all = subset["return"]
        s_all = float(r_all.std())
        sharpe_all = float(r_all.mean() / s_all * (252 ** 0.5)) if s_all > 0 else 0.0
        cum_all = float(((1 + r_all).prod() - 1) * 100)

        # Per-regime — only regimes with enough nights to be trustworthy
        best_regime = None
        best_sharpe = float("-inf")
        best_cum = 0.0
        best_n = 0
        for regime in REGIMES:
            rr = subset[subset["regime"] == regime]["return"]
            if len(rr) < min_nights_per_regime:
                continue
            sr = float(rr.std())
            sh = float(rr.mean() / sr * (252 ** 0.5)) if sr > 0 else 0.0
            if sh > best_sharpe:
                best_sharpe = sh
                best_regime = regime
                best_cum = float(((1 + rr).prod() - 1) * 100)
                best_n = len(rr)

        out_rows.append({
            "ticker": ticker,
            "sharpe_all": sharpe_all,
            "cum_all_pct": cum_all,
            "best_regime": best_regime or "-",
            "best_regime_n": best_n,
            "best_sharpe": best_sharpe if best_regime else 0.0,
            "best_cum_pct": best_cum,
            "delta_sharpe": (best_sharpe - sharpe_all) if best_regime else 0.0,
        })
    return pd.DataFrame(out_rows).sort_values("delta_sharpe", ascending=False)


# ────────────────────────────────────────────────────────────────────
# Output
# ────────────────────────────────────────────────────────────────────


def _hr(width: int = 108) -> str:
    return "─" * width


def _print_regime_distribution(regime_series: pd.Series, period: str) -> None:
    # Trim regime_series to just the requested period for accurate
    # distribution reporting.
    period_days = {"1y": 365, "2y": 730, "5y": 1825, "max": 20 * 365}[period]
    cutoff = regime_series.index.max() - pd.Timedelta(days=period_days)
    trimmed = regime_series[regime_series.index >= cutoff]

    total = len(trimmed)
    print(f"\n{_hr()}")
    print(f" REGIME DISTRIBUTION over {period} sample ({total} trading days)")
    print(_hr())
    counts = trimmed.value_counts()
    for regime in REGIMES:
        n = int(counts.get(regime, 0))
        pct = (n / total * 100) if total else 0
        bar = "█" * int(pct / 2)  # 1 block per 2% for a small bar
        print(f"  {regime:<18} {n:>5} days  {pct:>5.1f}%  {bar}")


def _print_pooled_regime_stats(stats: list[RegimeStat]) -> None:
    print(f"\n{_hr()}")
    print(" POOLED OVERNIGHT STATS by REGIME  (all tickers combined — 'typical night' per regime)")
    print(_hr())
    print(f"  {'Regime':<18} {'Nights':>8} {'Mean/d%':>10} {'Sharpe':>8} {'Hit%':>7} {'Best%':>8} {'Worst%':>8}")
    print("  " + "─" * 76)
    for s in stats:
        print(f"  {s.regime:<18} {s.n_nights:>8,} {s.mean_pct:>10.4f} {s.sharpe:>8.2f} "
              f"{s.hit_rate_pct:>7.1f} {s.best_pct:>+7.1f}  {s.worst_pct:>+7.1f}")


def _print_per_ticker(per_ticker: pd.DataFrame, top_n: int = 20) -> None:
    print(f"\n{_hr()}")
    print(f" PER-TICKER: unconditional vs best regime  (top {top_n} by ΔSharpe)")
    print(_hr())
    print(f"  {'Ticker':<8} {'Uncond':>18} {'Best regime':>32} {'ΔSharpe':>10}")
    print(f"  {'':<8} {'Sharpe':>9} {'Cum%':>8}  "
          f"{'Regime':<18} {'Nights':>6} {'Sharpe':>7}")
    print("  " + "─" * 76)
    for _, row in per_ticker.head(top_n).iterrows():
        arrow = "↑" if row["delta_sharpe"] > 0 else "↓" if row["delta_sharpe"] < 0 else " "
        print(f"  {row['ticker']:<8} {row['sharpe_all']:>9.2f} {row['cum_all_pct']:>8,.0f}  "
              f"{row['best_regime']:<18} {row['best_regime_n']:>6} {row['best_sharpe']:>7.2f} "
              f"{arrow}{row['delta_sharpe']:>+7.2f}")


def _print_gated_simulation(pooled: pd.DataFrame, gate_regime: str) -> None:
    """Simulate: 'only trade nights where regime == gate_regime'.

    Reports mean-per-night, Sharpe, annualised mean return, hit-rate, and
    activity%. We deliberately do NOT compound the pooled return series —
    pooled contains one row per (ticker, night), so compounding treats
    all ticker-nights as a single sequential portfolio, which is meaningless
    (produced 10²² total returns in the first version). Annualised
    mean × 252 is the honest "typical strategy return" number."""
    print(f"\n{_hr()}")
    print(f" GATED STRATEGY SIMULATION — trade only when regime == {gate_regime}")
    print(_hr())

    unconditional = pooled["return"]
    gated = pooled[pooled["regime"] == gate_regime]["return"]

    def _stats(r: pd.Series) -> tuple[float, float, float, float]:
        if len(r) == 0:
            return (0.0, 0.0, 0.0, 0.0)
        s = float(r.std())
        return (
            float(r.mean() * 100),                                          # mean/night %
            float(r.mean() / s * (252 ** 0.5)) if s > 0 else 0.0,           # sharpe
            float((r > 0).sum() / len(r) * 100),                            # hit%
            float(r.mean() * 252 * 100),                                    # annualised mean %
        )

    m_u, sh_u, hit_u, ann_u = _stats(unconditional)
    m_g, sh_g, hit_g, ann_g = _stats(gated)

    activity_pct = (len(gated) / len(unconditional) * 100) if len(unconditional) else 0
    # "In-market" return: gated strategy is only in position on gate
    # nights, so its expected annualised gross return is the gate's
    # mean × (activity × 252). This is what your account actually earns.
    in_market_g = m_g * activity_pct / 100 * 252
    print(f"  {'':<15} {'Mean/d%':>10} {'Sharpe':>8} {'Hit%':>7} {'Ann.Mean%':>11} {'Activity%':>11}")
    print("  " + "─" * 68)
    print(f"  {'Unconditional':<15} {m_u:>10.4f} {sh_u:>8.2f} {hit_u:>7.1f} {ann_u:>11.2f} {100.0:>10.1f}%")
    print(f"  {'Gated (in-mkt)':<15} {m_g:>10.4f} {sh_g:>8.2f} {hit_g:>7.1f} {ann_g:>11.2f} {activity_pct:>10.1f}%")
    print()
    print(f"  Interpretation:")
    print(f"    • Δ Sharpe (gated − unconditional) = {sh_g - sh_u:+.2f}")
    print(f"    • Gate is 'on' {activity_pct:.0f}% of the time — you'd be flat the rest.")
    print(f"    • Effective annualised gross return of gated strategy: {in_market_g:.2f}%")
    print(f"      (gate's mean/d × activity × 252 — accounts for time in cash).")
    if sh_g > sh_u and activity_pct >= 20:
        print(f"    • ✓ Gated version is more risk-efficient with meaningful activity.")
    elif sh_g > sh_u:
        print(f"    • ⚠ Gated Sharpe higher but activity is low — opportunity cost matters.")
    else:
        print(f"    • ✗ Gate does not improve risk-adjusted returns. Regime doesn't help here.")


def _print_legend() -> None:
    print(f"\n{_hr()}")
    print(" HOW TO READ")
    print(_hr())
    print("  Pooled table: does one regime carry disproportionate return / Sharpe?")
    print("    → If bull_low_vol Sharpe ≫ others, the anomaly is a low-vol bull phenomenon.")
    print("    → If bear/crisis regimes show *negative* mean, that's the case for gating.")
    print()
    print("  Per-ticker table: who benefits most from regime filtering?")
    print("    → Large ΔSharpe (>0.5) = filtering to best regime meaningfully improves that name.")
    print("    → Small ΔSharpe = ticker's overnight profile is regime-independent (rare).")
    print()
    print("  Gated simulation: what happens if you only trade the pooled-best regime?")
    print("    → Activity% is a HUGE factor. Sharpe up 30% while trading 40% of nights = win.")
    print("    → Sharpe up 30% while trading 5% of nights ≠ win (opportunity cost).")


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--email", help="Scope tickers to this user's portfolio+watchlist")
    scope.add_argument("--user-id", type=int, help="Same, by numeric id")
    parser.add_argument("--period", default="2y", choices=["1y", "2y", "5y", "max"])
    parser.add_argument(
        "--extra-tickers", default="SPY,QQQ,VOO",
        help="Comma-separated tickers to always include",
    )
    parser.add_argument("--skip-portfolio", action="store_true")
    parser.add_argument("--skip-watchlist", action="store_true")
    parser.add_argument(
        "--gate-regime", default=None,
        help="Simulate gated strategy on this regime. Defaults to pooled-best.",
    )
    parser.add_argument(
        "--min-nights-per-regime", type=int, default=100,
        help="Min nights required for a regime to be considered 'best' per ticker. "
             "Raised from 30 after the first run showed cherry-picking: a regime "
             "with only 35 nights per ticker gave flatteringly high Sharpes just "
             "from small-sample noise. 100+ is credible.",
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

    print(f"Building regime timeline from SPY + VIX (5y history) ...")
    regime_series = await _build_regime_series()

    print(f"Fetching daily OHLC for {len(ticker_list)} tickers (period={args.period}) ...")
    mds = MarketDataService()
    sem = asyncio.Semaphore(8)

    async def _one(ticker: str) -> pd.DataFrame | None:
        async with sem:
            try:
                df = await mds.get_ohlcv_dataframe(ticker, period=args.period, interval="1d")
                out = _overnight_with_regime(df, regime_series)
                if len(out) == 0:
                    return None
                out = out.copy()
                out["ticker"] = ticker
                return out
            except Exception as exc:
                print(f"  {ticker}: error — {exc}")
                return None

    results = await asyncio.gather(*(_one(t) for t in ticker_list))
    kept = [r for r in results if r is not None]
    if not kept:
        raise SystemExit("No tickers returned usable data.")

    pooled = pd.concat(kept, ignore_index=False)
    pooled = pooled[["ticker", "return", "regime"]]
    print(f"Pooled: {len(pooled):,} ticker-nights across {pooled['ticker'].nunique()} tickers.")

    # ── Report ──────────────────────────────────────────────────────
    _print_regime_distribution(regime_series, args.period)

    pooled_stats = _regime_stats(pooled)
    _print_pooled_regime_stats(pooled_stats)

    per_ticker = _per_ticker_regime_best(pooled, min_nights_per_regime=args.min_nights_per_regime)
    _print_per_ticker(per_ticker)

    # Pick gate: user override, else best "utility" = sharpe × sqrt(activity_frac).
    # Rationale: a Sharpe-1.5 regime active 3% of the time is a worse strategy
    # than a Sharpe-1.0 regime active 75% of the time — sqrt(activity)
    # is the standard adjustment for how much of the Sharpe you actually
    # capture given the time you spend in market.
    if args.gate_regime:
        gate = args.gate_regime
    else:
        total_nights = sum(s.n_nights for s in pooled_stats)
        def _utility(s: RegimeStat) -> float:
            if s.n_nights < 200 or s.sharpe <= 0:
                return -1.0  # exclude tiny-sample or negative-Sharpe regimes
            activity_frac = s.n_nights / total_nights
            return s.sharpe * (activity_frac ** 0.5)
        scored = sorted(pooled_stats, key=_utility, reverse=True)
        gate = scored[0].regime if _utility(scored[0]) > 0 else pooled_stats[0].regime
    _print_gated_simulation(pooled, gate)

    _print_legend()


if __name__ == "__main__":
    asyncio.run(main())
