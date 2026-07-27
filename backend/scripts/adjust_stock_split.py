"""
Adjust a portfolio's trade ledger for a stock split (or reverse split).

For every trade in the given portfolio for the given ticker whose
`traded_at` is BEFORE `--effective-date`:
  quantity *= ratio
  price    /= ratio
  fees      (unchanged)

Then re-runs PortfolioService._recalculate_position so the `positions`
table reflects the new cost basis using the same walking-average logic
the rest of the app uses.

Cash impact = qty * price = unchanged, so account_transactions don't
need touching — the notional per trade stays the same.

Ratio semantics: for a 10-for-1 forward split (1 share becomes 10),
pass --ratio 10. For a 1-for-10 reverse split, pass --ratio 0.1.

Usage:
    docker exec jarvis-backend-1 python scripts/adjust_stock_split.py \\
        --portfolio-id 1 --ticker NVDA --ratio 10 --effective-date 2024-06-10 \\
        --confirm

Safety:
  - Requires --confirm (won't run accidentally)
  - Refuses paper portfolios
  - Single DB transaction: any failure rolls everything back
  - Prints before/after position + trade counts
"""

import argparse
import asyncio
import sys
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.portfolio import BrokerType, Portfolio, Position, Trade
from app.services.portfolio import PortfolioService


async def _adjust(
    portfolio_id: int,
    ticker: str,
    ratio: Decimal,
    effective_date: date,
) -> None:
    async with AsyncSessionLocal() as db:
        portfolio = await db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise SystemExit(f"Portfolio id={portfolio_id} not found.")
        if portfolio.broker == BrokerType.paper:
            raise SystemExit("Refusing to adjust a paper portfolio.")

        ticker = ticker.upper()

        # ── Before-state snapshot ──────────────────────────────────────
        before_pos = (await db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.ticker == ticker,
            )
        )).scalar_one_or_none()
        print(f"Portfolio: id={portfolio_id} name={portfolio.name!r}")
        print(f"Ticker: {ticker}")
        print(f"Ratio: {ratio} (>1 = forward split, <1 = reverse split)")
        print(f"Effective date: {effective_date} (adjusting trades BEFORE this date)")
        if before_pos:
            print(f"\nBEFORE: {float(before_pos.quantity):,.4f} shares "
                  f"@ avg_cost ${float(before_pos.avg_cost):,.4f}")
        else:
            print("\nBEFORE: no open position")

        # ── Identify the trades to adjust ──────────────────────────────
        all_trades = (await db.execute(
            select(Trade).where(
                Trade.portfolio_id == portfolio_id,
                Trade.ticker == ticker,
            ).order_by(Trade.traded_at)
        )).scalars().all()

        pre_split = [t for t in all_trades if t.traded_at.date() < effective_date]
        print(f"\nFound {len(all_trades)} total trades, {len(pre_split)} pre-split "
              f"(will be adjusted).")

        if not pre_split:
            raise SystemExit("Nothing to do — no pre-split trades match.")

        # ── Apply the adjustment ───────────────────────────────────────
        for t in pre_split:
            old_qty = t.quantity
            old_price = t.price
            t.quantity = Decimal(str(round(float(old_qty) * float(ratio), 6)))
            t.price = Decimal(str(round(float(old_price) / float(ratio), 4)))
            # Sanity: notional preserved (within rounding).
            old_notional = float(old_qty) * float(old_price)
            new_notional = float(t.quantity) * float(t.price)
            if abs(new_notional - old_notional) > 0.01:
                raise SystemExit(
                    f"Notional drift too large on trade {t.id}: "
                    f"{old_notional:.4f} → {new_notional:.4f}"
                )
        await db.flush()

        # ── Rebuild position from the adjusted ledger ──────────────────
        svc = PortfolioService(db)
        await svc._recalculate_position(portfolio_id, ticker)
        await db.flush()

        # ── After-state snapshot ───────────────────────────────────────
        after_pos = (await db.execute(
            select(Position).where(
                Position.portfolio_id == portfolio_id,
                Position.ticker == ticker,
            )
        )).scalar_one_or_none()
        if after_pos:
            print(f"AFTER:  {float(after_pos.quantity):,.4f} shares "
                  f"@ avg_cost ${float(after_pos.avg_cost):,.4f}")
        else:
            print("AFTER:  no open position (all sold?)")

        await db.commit()
        print("\nCOMMITTED.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio-id", type=int, required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--ratio", type=Decimal, required=True,
                        help="Forward split ratio (10 for 10-for-1). "
                             "For reverse splits, use a fractional value.")
    parser.add_argument("--effective-date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                        required=True,
                        help="YYYY-MM-DD — trades BEFORE this date are adjusted")
    parser.add_argument("--confirm", action="store_true",
                        help="Required — script refuses to modify trades otherwise")
    args = parser.parse_args()

    if not args.confirm:
        raise SystemExit(
            "Refusing to run without --confirm. This script modifies trade "
            "history and rebuilds the position row for the ticker. Add "
            "--confirm when you're sure the parameters are correct."
        )

    await _adjust(args.portfolio_id, args.ticker, args.ratio, args.effective_date)


if __name__ == "__main__":
    asyncio.run(main())
