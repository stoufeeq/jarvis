"""
Yahoo request budget: what the scheduled work costs, and what is left.

Nothing in the codebase used to know how many provider requests it was
spending, so each new feature added to a total no one tracked. The total
reached roughly 11,000 requests/hour against a limit the community puts
near 360, the IP was throttled, and the resulting 429s surfaced as 75
holdings "likely delisted" — AAPL and NVDA among them.

This script makes the budget legible. It prints measured per-call costs,
the recurring cost of every beat schedule entry, and live usage from the
shared Redis window.

Per-call costs were measured by counting HTTP calls through yfinance's
curl_cffi session on yfinance 1.2.2:

    /v7/finance/quote    1 request per 100 symbols   (true batch)
    Ticker.history       1 request per ticker
    fast_info            2 requests per ticker
    Ticker.info          2 requests per ticker
    yf.download(N)       N+1 requests  — threads per ticker, NOT a batch

That last line is the one that caused the problem: the name implies
batching and the heatmap was built on that assumption.

Usage:
    python scripts/yahoo_budget.py
    python scripts/yahoo_budget.py --tickers 90 --watchlist 40
"""

import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

from app.config import get_settings

# Measured request cost per primitive (yfinance 1.2.2).
COST_HISTORY = 1
COST_INFO = 2
COST_FAST_INFO = 2
QUOTE_CHUNK = 100


def quote_cost(n: int) -> int:
    return max(1, -(-n // QUOTE_CHUNK)) if n else 0


def plan(holdings: int, watchlist: int, sp500: int = 452) -> list[dict]:
    """Recurring hourly cost of each beat entry, at the current schedule."""
    priced = holdings + watchlist

    return [
        {
            "job": "warm-heatmap-cache",
            "every": "10 min",
            "per_run": quote_cost(sp500),
            "runs_per_hour": 6,
            "note": f"batched quotes for {sp500} constituents",
            "was": sp500 + 1 + sp500 * COST_FAST_INFO,
        },
        {
            "job": "refresh-prices",
            "every": "5 min",
            "per_run": quote_cost(priced),
            "runs_per_hour": 12,
            "note": f"batched quotes for {priced} symbols",
            "was": priced * (COST_HISTORY + COST_FAST_INFO),
        },
        {
            "job": "scan-signals",
            "every": "1 hour",
            "per_run": watchlist * COST_HISTORY,
            "runs_per_hour": 1,
            "note": "2y daily history per watchlist ticker — no batch endpoint exists",
            "was": watchlist * COST_HISTORY * 4,
        },
        {
            "job": "refresh-pe-rsi",
            "every": "4 hours",
            "per_run": watchlist * COST_HISTORY,
            "runs_per_hour": 0.25,
            "note": "per watchlist ticker",
            "was": watchlist * COST_HISTORY * 4,
        },
        {
            "job": "check-ticker-health",
            "every": "daily",
            "per_run": quote_cost(priced),
            "runs_per_hour": 1 / 24,
            "note": "batched; absence from the response means unresolved",
            "was": priced * (COST_HISTORY + COST_FAST_INFO),
        },
        {
            "job": "refresh-calendar",
            "every": "daily",
            "per_run": priced * COST_HISTORY,
            "runs_per_hour": 1 / 24,
            "note": "earnings + ex-dividend dates",
            "was": priced * COST_HISTORY,
        },
        {
            "job": "sync-dividends",
            "every": "daily",
            "per_run": priced * COST_HISTORY,
            "runs_per_hour": 1 / 24,
            "note": "dividend history per symbol",
            "was": priced * COST_HISTORY,
        },
        {
            "job": "refresh-market-snapshot",
            "every": "4 hours",
            "per_run": 40,
            "runs_per_hour": 0.25,
            "note": "indices, commodities, crypto, forex, sectors",
            "was": 40,
        },
        {
            "job": "regime-refresh",
            "every": "2x daily",
            "per_run": 4,
            "runs_per_hour": 2 / 24,
            "note": "SPY + VIX history",
            "was": 4,
        },
        {
            "job": "snapshot-signal-outcomes",
            "every": "6 hours",
            "per_run": 20,
            "runs_per_hour": 4 / 24,
            "note": "historical closes for open signals",
            "was": 20,
        },
        {
            "job": "refresh-magic-formula",
            "every": "weekly",
            "per_run": sp500 * 3 * COST_INFO,
            "runs_per_hour": 1 / 168,
            "note": "3 statements per name — the single most expensive job",
            "was": sp500 * 3 * COST_INFO,
        },
        {
            "job": "refresh-halal-compliance",
            "every": "monthly",
            "per_run": priced * COST_INFO,
            "runs_per_hour": 1 / 720,
            "note": "one .info per holding",
            "was": priced * COST_INFO,
        },
    ]


async def main(holdings: int, watchlist: int) -> None:
    s = get_settings()
    budget = s.yf_requests_per_hour
    rows = plan(holdings, watchlist)

    print(f"Assuming {holdings} holdings + {watchlist} watchlist tickers\n")
    print(f"{'job':<28}{'every':>10}{'per run':>9}{'per hour':>10}{'before':>10}")
    print("-" * 70)

    total = was_total = 0.0
    for r in rows:
        hourly = r["per_run"] * r["runs_per_hour"]
        was_hourly = r["was"] * r["runs_per_hour"]
        total += hourly
        was_total += was_hourly
        print(f"{r['job']:<28}{r['every']:>10}{r['per_run']:>9}"
              f"{hourly:>10.1f}{was_hourly:>10.1f}")
    print("-" * 70)
    print(f"{'TOTAL scheduled / hour':<28}{'':>10}{'':>9}{total:>10.1f}{was_total:>10.1f}")
    print()
    print(f"Budget: {budget}/hour  →  headroom {budget - total:.0f}/hour "
          f"({total / budget * 100:.0f}% used by scheduled work)")
    if total > budget:
        print("  ⚠ OVER BUDGET. Lengthen an interval or shrink a universe.")
    else:
        print("  Remaining headroom covers on-demand work: chart loads, momentum")
        print("  scores, ad-hoc scans, Allocation sector lookups.")
    print()
    for r in rows:
        print(f"  {r['job']}: {r['note']}")

    print()
    try:
        from app.services.rate_limit import get_limiter
        usage = await get_limiter().usage()
        print(f"Live window: {usage}")
    except Exception as exc:
        print(f"Live window unavailable: {exc}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tickers", type=int, default=90, help="open positions")
    p.add_argument("--watchlist", type=int, default=40, help="watchlist tickers")
    a = p.parse_args()
    asyncio.run(main(a.tickers, a.watchlist))
