"""
Overnight-return backtest across your portfolio + watchlist + a few indices.

Strategy tested (the "honest" version, unconditional):
  For every trading day t in the sample:
    buy  ticker at close_t
    sell ticker at open_{t+1}
    return = (open_{t+1} - close_t) / close_t
  Compound daily returns for cumulative wealth. Repeat every day.

Also computes two comparisons for context:
  Intraday: buy at open_t, sell at close_t.
  Buy&Hold: hold from first close to last close.

The academic finding (Bessembinder et al., Cliff & Kappel, others) is
that historically ~all of the equity risk premium in US indices came
from the overnight period, with the intraday period contributing
roughly zero or negative. This script lets you check the effect on
your specific tickers.

CAVEAT — this is a *paper* backtest using yfinance's reported OHLC.
Real-world execution eats a meaningful chunk of the per-night edge:
  - Opening print isn't always achievable at retail
  - Bid-ask spread at open + close
  - 250+ round-trips/year × commissions
  - Slippage on illiquid names
  - Dividend adjustments already handled (yfinance auto-adjust)
Treat the numbers as a rough upper bound, not real P&L.

Usage:
    docker exec jarvis-backend-1 python scripts/backtest_overnight.py \\
        --email stoufeeq@gmail.com --period 2y

Options:
    --period          yfinance period (1y, 2y, 5y, max). Default 2y.
    --extra-tickers   Comma-separated tickers to always include.
                      Default: SPY,QQQ,VOO
    --skip-portfolio  Don't include your positions (watchlist + extras only)
    --skip-watchlist  Don't include watchlist (positions + extras only)
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


@dataclass
class TickerStats:
    ticker: str
    n_days: int
    # Cumulative returns (compounded), %
    overnight_cum_pct: float
    intraday_cum_pct: float
    buyhold_cum_pct: float
    # Overnight per-day mean, %
    overnight_mean_pct: float
    # Annualised Sharpe (rf = 0)
    overnight_sharpe: float
    intraday_sharpe: float
    buyhold_sharpe: float
    # % of nights with positive return
    overnight_hit_rate_pct: float
    # Extremes
    overnight_best_pct: float
    overnight_worst_pct: float


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
    """Union of currently-held positions across the user's real (non-paper)
    portfolios + their watchlist items."""
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


def _compute_stats(df: pd.DataFrame, ticker: str) -> TickerStats | None:
    """Compute overnight/intraday/buy-and-hold stats from a daily OHLC df.
    Returns None if the frame is too thin to be meaningful."""
    if df is None or df.empty or len(df) < 20:
        return None

    # Drop rows with NaN close (weekend today-bar artefact from yfinance).
    df = df.dropna(subset=["Close", "Open"])
    if len(df) < 20:
        return None

    prev_close = df["Close"].shift(1)
    overnight = ((df["Open"] - prev_close) / prev_close).dropna()
    intraday = ((df["Close"] - df["Open"]) / df["Open"]).dropna()
    buyhold = df["Close"].pct_change().dropna()

    if len(overnight) < 10:
        return None

    # Cumulative wealth = compound (1 + r), report as pct
    def _cum(r: pd.Series) -> float:
        return float(((1 + r).prod() - 1) * 100)

    # Annualised Sharpe (rf=0)
    def _sharpe(r: pd.Series) -> float:
        s = float(r.std())
        return float(r.mean() / s * (252 ** 0.5)) if s > 0 else 0.0

    return TickerStats(
        ticker=ticker,
        n_days=len(overnight),
        overnight_cum_pct=_cum(overnight),
        intraday_cum_pct=_cum(intraday),
        buyhold_cum_pct=_cum(buyhold),
        overnight_mean_pct=float(overnight.mean() * 100),
        overnight_sharpe=_sharpe(overnight),
        intraday_sharpe=_sharpe(intraday),
        buyhold_sharpe=_sharpe(buyhold),
        overnight_hit_rate_pct=float((overnight > 0).sum() / len(overnight) * 100),
        overnight_best_pct=float(overnight.max() * 100),
        overnight_worst_pct=float(overnight.min() * 100),
    )


def _header(title: str) -> str:
    return "\n" + "─" * 108 + f"\n {title}\n" + "─" * 108


def _print_table(stats: list[TickerStats], period: str) -> None:
    print(_header(f"OVERNIGHT vs INTRADAY vs BUY-AND-HOLD  ({len(stats)} tickers · period={period})"))
    print()
    # Two-row header
    print(f"  {'Ticker':<8} {'Days':>5}  "
          f"{'OVERNIGHT':>27}  "
          f"{'INTRADAY':>18}  "
          f"{'BUY&HOLD':>18}  "
          f"{'ON Hit':>7}  {'ON Best/Worst':>14}")
    print(f"  {'':<8} {'':>5}  "
          f"{'Total%':>9} {'Sharpe':>7} {'Mean/d%':>9}  "
          f"{'Total%':>9} {'Sharpe':>7}  "
          f"{'Total%':>9} {'Sharpe':>7}  "
          f"{'%':>6}  ")
    print("  " + "─" * 104)

    for s in stats:
        print(f"  {s.ticker:<8} {s.n_days:>5}  "
              f"{s.overnight_cum_pct:>9,.1f} {s.overnight_sharpe:>7.2f} {s.overnight_mean_pct:>9.3f}  "
              f"{s.intraday_cum_pct:>9,.1f} {s.intraday_sharpe:>7.2f}  "
              f"{s.buyhold_cum_pct:>9,.1f} {s.buyhold_sharpe:>7.2f}  "
              f"{s.overnight_hit_rate_pct:>6.1f}  "
              f"{s.overnight_best_pct:>+5.1f}/{s.overnight_worst_pct:>+5.1f}")


def _print_summary(stats: list[TickerStats]) -> None:
    """Equal-weighted averages across tickers. Not a proper portfolio
    backtest (would need correlations for real vol) but a decent
    'typical name' summary."""
    if not stats:
        return
    n = len(stats)
    print()
    print("  " + "─" * 104)
    print(f"  {'AVG':<8} {'':>5}  "
          f"{sum(s.overnight_cum_pct for s in stats)/n:>9,.1f} "
          f"{sum(s.overnight_sharpe for s in stats)/n:>7.2f} "
          f"{sum(s.overnight_mean_pct for s in stats)/n:>9.3f}  "
          f"{sum(s.intraday_cum_pct for s in stats)/n:>9,.1f} "
          f"{sum(s.intraday_sharpe for s in stats)/n:>7.2f}  "
          f"{sum(s.buyhold_cum_pct for s in stats)/n:>9,.1f} "
          f"{sum(s.buyhold_sharpe for s in stats)/n:>7.2f}  "
          f"{sum(s.overnight_hit_rate_pct for s in stats)/n:>6.1f}")


def _print_legend() -> None:
    print()
    print(" KEY:")
    print("   Overnight = buy at close, sell at next open (the strategy being tested)")
    print("   Intraday  = buy at open, sell at close (same day) — the 'dead' period historically")
    print("   Buy&Hold  = hold from first bar to last bar")
    print("   Sharpe    = annualised (rf = 0). >1 decent, >1.5 strong.")
    print("   ON Hit    = % of nights the open exceeded the previous close")
    print()
    print(" INTERPRETATION HINTS:")
    print("   • Overnight Sharpe > Buy&Hold Sharpe → risk-efficient edge (you're paid better per unit of risk).")
    print("   • Overnight Sharpe > Intraday Sharpe → the classic overnight anomaly is present.")
    print("   • ON Hit > 55% → statistical edge worth investigating.")
    print("   • ON Hit ~ 50% but positive Sharpe → the wins are bigger than the losses.")
    print()
    print(" CAVEATS:")
    print("   • Real execution: bid-ask at open eats ~0.05-0.20% per trade → shaves annualised Sharpe.")
    print("   • Commissions: 250+ round-trips/year × broker fee — verify your true cost.")
    print("   • Index ETFs (SPY/QQQ/VOO) usually show the cleanest overnight anomaly historically.")
    print("   • Single-stock overnight edges are noisier and rotate over time.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--email", help="Scope tickers to this user's portfolio+watchlist")
    scope.add_argument("--user-id", type=int, help="Same, by numeric id")
    parser.add_argument("--period", default="2y", choices=["1y", "2y", "5y", "max"])
    parser.add_argument(
        "--extra-tickers", default="SPY,QQQ,VOO",
        help="Comma-separated tickers to always include (defaults to major US index ETFs)",
    )
    parser.add_argument("--skip-portfolio", action="store_true")
    parser.add_argument("--skip-watchlist", action="store_true")
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
    print(f"Fetching daily OHLC for {len(ticker_list)} tickers "
          f"(period={args.period}) ...")

    mds = MarketDataService()
    sem = asyncio.Semaphore(8)  # concurrency cap for yfinance politeness

    async def _one(ticker: str) -> TickerStats | None:
        async with sem:
            try:
                df = await mds.get_ohlcv_dataframe(ticker, period=args.period, interval="1d")
                return _compute_stats(df, ticker)
            except Exception as exc:
                print(f"  {ticker}: error — {exc}")
                return None

    results = await asyncio.gather(*(_one(t) for t in ticker_list))
    stats = [r for r in results if r is not None]

    if not stats:
        raise SystemExit("No tickers returned enough data to backtest.")

    # Sort by overnight Sharpe descending — best risk-adjusted overnight
    # setups at the top.
    stats.sort(key=lambda s: -s.overnight_sharpe)

    _print_table(stats, args.period)
    _print_summary(stats)
    _print_legend()


if __name__ == "__main__":
    asyncio.run(main())
