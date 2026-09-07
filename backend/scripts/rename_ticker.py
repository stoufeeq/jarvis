"""
Rename a ticker across the trade ledger and rebuild the affected positions.

Why this exists rather than a hand-written UPDATE: positions are
maintained incrementally as trades arrive (PortfolioService._update_position),
so there is no code path that recomputes one from scratch. Renaming a
ticker with plain SQL leaves the Position row stale — wrong quantity,
wrong average cost — and if the same instrument already exists under the
correct symbol you end up with two rows that should be one.

Concretely: a single 2024 trade recorded as MBGD alongside 15 correct
MBG.DE trades means the Mercedes holding is split in two. Cost basis,
realised P&L and dividend entitlement for the 2024 shares are all
detached from the rest, and the orphaned symbol resolves to nothing at
the data provider so it silently contributes zero everywhere.

The rebuild walks the ledger chronologically with the same
moving-average-cost convention the app uses, so the resulting Position
matches what incremental updates would have produced had the ticker been
right all along.

Refuses to merge across differing currencies, which would corrupt the
cost basis rather than fix it.

Usage:
    # always dry-run first — prints the plan, writes nothing
    python scripts/rename_ticker.py --from MBGD --to MBG.DE

    # apply it
    python scripts/rename_ticker.py --from MBGD --to MBG.DE --apply
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.portfolio import Portfolio, Position, Trade, TradeAction
from app.models.watchlist import WatchlistItem


def _rebuild_from_trades(trades: list[Trade]) -> dict | None:
    """Replay the ledger for one ticker in one portfolio.

    Moving-average cost, long-only — the same convention as
    PortfolioService._update_position and compute_realised_pnl, so the
    rebuilt row agrees with the rest of the app rather than introducing
    a third accounting method.

    Returns None when the position nets to flat (fully exited).
    """
    qty = 0.0
    avg = 0.0
    opened_at = None
    currency = None
    asset_type = None

    for t in sorted(trades, key=lambda x: (x.traded_at, x.id)):
        q = float(t.quantity)
        px = float(t.price)
        if t.action in (TradeAction.buy, TradeAction.short):
            new_qty = qty + q
            if new_qty > 0:
                avg = ((avg * qty) + (px * q)) / new_qty
            qty = new_qty
            if opened_at is None:
                opened_at = t.traded_at
                currency = t.currency
                asset_type = t.asset_type
        elif t.action in (TradeAction.sell, TradeAction.cover):
            qty -= q
            if qty <= 1e-9:
                # Flat — a later re-entry starts a fresh basis.
                qty, avg, opened_at = 0.0, 0.0, None

    if qty <= 1e-9:
        return None
    return {
        "quantity": qty,
        "avg_cost": avg,
        "opened_at": opened_at,
        "currency": currency,
        "asset_type": asset_type,
    }


async def run(old: str, new: str, apply: bool) -> None:
    old, new = old.upper(), new.upper()
    async with AsyncSessionLocal() as db:
        trades = list((await db.execute(
            select(Trade).where(Trade.ticker.in_([old, new]))
        )).scalars().all())
        if not trades:
            print(f"No trades found for {old} or {new}. Nothing to do.")
            return

        old_trades = [t for t in trades if t.ticker.upper() == old]
        new_trades = [t for t in trades if t.ticker.upper() == new]
        print(f"Trades: {len(old_trades)} as {old}, {len(new_trades)} as {new}")

        # NOTE: currency is validated PER PORTFOLIO below, not globally.
        # Trades in different portfolios are never merged with one another,
        # so a EUR holding in a real portfolio and a USD paper position in
        # another are not in conflict. An earlier version checked globally
        # and aborted on exactly that, blocking a valid rename.

        # Group affected portfolios so each is rebuilt independently.
        by_portfolio: dict[int, list[Trade]] = defaultdict(list)
        for t in trades:
            by_portfolio[t.portfolio_id].append(t)

        print()
        print(f"{'DRY RUN — no changes written' if not apply else 'APPLYING CHANGES'}")
        print("=" * 72)
        skipped: list[int] = []

        for pid, ptrades in sorted(by_portfolio.items()):
            portfolio = await db.get(Portfolio, pid)
            pname = portfolio.name if portfolio else f"#{pid}"

            rows = list((await db.execute(
                select(Position).where(
                    Position.portfolio_id == pid,
                    Position.ticker.in_([old, new]),
                )
            )).scalars().all())

            broker = portfolio.broker.value if portfolio else "?"
            print(f"\nPortfolio {pid} — {pname}  [{broker}]")

            # Averaging prices denominated in different units gives a
            # meaningless cost basis, so refuse — but only for the
            # portfolio actually affected, leaving others processable.
            pcurrencies = {(t.currency or "USD").upper() for t in ptrades}
            if len(pcurrencies) > 1:
                print(f"  ✗ SKIPPED: trades here span {sorted(pcurrencies)}.")
                print("    Merging would average prices in different units.")
                print("    Fix the currency on these trades first:")
                for t in sorted(ptrades, key=lambda x: x.traded_at):
                    print(f"      #{t.id}  {t.ticker:<8} {t.action.value:<5} "
                          f"{float(t.quantity):>10.4f} @ {float(t.price):>10.4f} "
                          f"{t.currency}  {t.traded_at.date()}")
                skipped.append(pid)
                continue
            print(f"  Currency: {pcurrencies.pop()} (consistent)")

            print(f"  Current positions:")
            for r in rows:
                print(f"    {r.ticker:<8} qty={float(r.quantity):>12.4f} "
                      f"avg={float(r.avg_cost):>10.4f} {r.currency}")
            if not rows:
                print("    (none)")

            rebuilt = _rebuild_from_trades(ptrades)
            print(f"  Rebuilt from {len(ptrades)} trades:")
            if rebuilt is None:
                print(f"    {new:<8} → flat (fully exited); position row removed")
            else:
                print(f"    {new:<8} qty={rebuilt['quantity']:>12.4f} "
                      f"avg={rebuilt['avg_cost']:>10.4f} {rebuilt['currency']}")

            if not apply:
                continue

            for t in ptrades:
                if t.ticker.upper() == old:
                    t.ticker = new

            for r in rows:
                await db.delete(r)
            await db.flush()

            if rebuilt is not None:
                db.add(Position(
                    portfolio_id=pid,
                    ticker=new,
                    asset_type=rebuilt["asset_type"],
                    quantity=Decimal(str(rebuilt["quantity"])),
                    avg_cost=Decimal(str(rebuilt["avg_cost"])),
                    currency=rebuilt["currency"],
                    opened_at=rebuilt["opened_at"],
                ))
            await db.flush()

        # Watchlist: rename, dropping the old row if the new one exists
        # (the unique constraint is on (watchlist_id, ticker)).
        wl = list((await db.execute(
            select(WatchlistItem).where(WatchlistItem.ticker.in_([old, new]))
        )).scalars().all())
        old_wl = [w for w in wl if w.ticker.upper() == old]
        new_wl_keys = {(w.watchlist_id, w.ticker.upper()) for w in wl if w.ticker.upper() == new}
        if old_wl:
            print(f"\nWatchlist: {len(old_wl)} entr{'y' if len(old_wl) == 1 else 'ies'} as {old}")
            for w in old_wl:
                dup = (w.watchlist_id, new) in new_wl_keys
                print(f"  watchlist {w.watchlist_id}: "
                      f"{'delete (─ ' + new + ' already present)' if dup else '→ ' + new}")
                if apply:
                    if dup:
                        await db.delete(w)
                    else:
                        w.ticker = new

        if skipped:
            print(f"\n⚠ {len(skipped)} portfolio(s) skipped on a currency clash — "
                  f"see above. Any other portfolios were still processed.")

        if apply:
            await db.commit()
            print("\n✓ Committed.")
            print("  Next: open the Dividends tab and hit Sync so history is pulled")
            print("  for the corrected symbol. Income is derived from the ledger, so")
            print("  the full history appears without re-importing anything.")
        else:
            print("\nNothing written. Re-run with --apply to commit.")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="old", required=True, help="Ticker to rename FROM")
    p.add_argument("--to", dest="new", required=True, help="Ticker to rename TO")
    p.add_argument("--apply", action="store_true",
                   help="Actually write. Without this it is a dry run.")
    args = p.parse_args()
    asyncio.run(run(args.old, args.new, args.apply))


if __name__ == "__main__":
    main()
