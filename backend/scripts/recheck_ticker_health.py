"""
Re-probe every tracked ticker now, and say what the provider actually
returned.

Two jobs:

  Diagnose — the banner only ever shows a verdict ("likely delisted").
  When that verdict is clearly wrong (AAPL and NVDA do not delist), the
  useful information is the raw provider error, which lives in
  ticker_health.last_error and is otherwise never surfaced.

  Reset — a successful probe clears resolves/consecutive_failures, so
  running this once the provider is healthy again drops the banner
  immediately instead of waiting for the 04:30 UTC job.

With --show-only nothing is probed or written; it just prints the stored
state, which is the right first step when you want to know what happened
without adding more load to a provider that may be throttling you.

Usage:
    python scripts/recheck_ticker_health.py --show-only
    python scripts/recheck_ticker_health.py
    python scripts/recheck_ticker_health.py --clear-flags
"""

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.ticker_health import TickerHealth
from app.services.ticker_health import TickerHealthService


def _bucket(err: str | None) -> str:
    """Group raw errors so one line summarises seventy-five rows."""
    if not err:
        return "(none)"
    low = err.lower()
    if any(m in low for m in ("429", "too many requests", "rate limit", "ratelimit")):
        return "RATE LIMITED (429)"
    if any(m in low for m in ("timeout", "timed out", "max retries")):
        return "TIMEOUT"
    if any(m in low for m in ("connection", "dns", "resolve", "curl", "ssl")):
        return "NETWORK / TLS"
    if "no price and no recent history" in low:
        return "empty response (no price, no history)"
    return err.split(":")[0][:60]


async def show(db) -> None:
    rows = list((await db.execute(select(TickerHealth))).scalars().all())
    if not rows:
        print("No ticker_health rows yet.")
        return

    broken = [r for r in rows if not r.resolves]
    print(f"{len(rows)} tracked, {len(broken)} currently flagged as unresolvable")
    print()

    if broken:
        buckets = Counter(_bucket(r.last_error) for r in broken)
        print("Flagged tickers grouped by the error that flagged them:")
        for label, n in buckets.most_common():
            print(f"  {n:>4}  {label}")
        print()
        worked_before = [r for r in broken if r.last_ok_at is not None]
        print(f"  {len(worked_before)} of {len(broken)} worked before "
              f"(so the symbols were valid at some point)")
        if len(broken) >= 8 and len(worked_before) == len(broken):
            print()
            print("  ⚠ Every flagged symbol previously resolved. That is a provider")
            print("    problem, not seventy-five renamed companies. The verdict text")
            print("    is wrong; look at the error bucket above.")
        print()
        print("First five in full:")
        for r in broken[:5]:
            print(f"  {r.ticker:<10} failures={r.consecutive_failures} "
                  f"last_ok={r.last_ok_at} \n    err={r.last_error}")


async def run(show_only: bool, clear_flags: bool) -> None:
    try:
        await _run(show_only, clear_flags)
    finally:
        # The limiter holds a Redis connection; closing it here keeps
        # interpreter shutdown from printing an "Event loop is closed"
        # traceback that looks like a failure and isn't.
        from app.services.rate_limit import shutdown
        await shutdown()


async def _run(show_only: bool, clear_flags: bool) -> None:
    async with AsyncSessionLocal() as db:
        await show(db)

        if show_only:
            print("\n--show-only: nothing probed, nothing written.")
            return

        if clear_flags:
            rows = list((await db.execute(
                select(TickerHealth).where(TickerHealth.resolves.is_(False))
            )).scalars().all())
            for r in rows:
                r.resolves = True
                r.consecutive_failures = 0
                r.last_error = None
            await db.commit()
            print(f"\n✓ Cleared {len(rows)} flag(s). They will be re-evaluated on the "
                  f"next check; a genuinely bad symbol comes back within three days.")
            return

        print("\nRe-probing every tracked ticker…")
        result = await TickerHealthService(db).check_all()
        await db.commit()

        if result.get("outage"):
            print(f"\n⚠ PROVIDER OUTAGE: {result['checked']} probed, "
                  f"{result['failure_ratio'] * 100:.0f}% failed.")
            print("  No health rows were touched — this run is treated as if it")
            print("  did not happen, rather than flagging every symbol.")
            print(f"  Sample error: {result.get('sample_error')}")
            print()
            print("  The existing flags are stale verdicts from before this guard")
            print("  existed. Once the provider answers again, one clean run clears")
            print("  them; --clear-flags drops them now.")
        else:
            print(f"\n✓ checked={result['checked']} newly-broken={result['broken']} "
                  f"recovered={result['recovered']} throttled={result.get('throttled', 0)}")
            print()
            await show(db)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--show-only", action="store_true",
                   help="Print stored state only. No probes, no writes.")
    p.add_argument("--clear-flags", action="store_true",
                   help="Clear every unresolvable flag without probing. Use when "
                        "the flags are known-bogus; real problems re-flag in 3 days.")
    args = p.parse_args()
    asyncio.run(run(args.show_only, args.clear_flags))


if __name__ == "__main__":
    main()
