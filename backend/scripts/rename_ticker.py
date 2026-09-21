"""
Rename a ticker across the trade ledger and rebuild the affected positions,
optionally applying stock splits that happened along the way.

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

Splits
------
A ticker change is often the visible end of a corporate action, and the
action usually includes a reverse split (SYTA → CHAI came with 1-for-4).
A rename alone would then price pre-split share counts against post-split
quotes: 4x the shares you own at a quarter of the cost. `--split` fixes
that by rescaling every trade dated before the effective date — quantity
divided, price multiplied, total cost unchanged — which is exactly what
the data provider does to its own price history, so the ledger and the
chart agree again. Each adjusted trade keeps its original figures in the
notes column, so the audit trail survives.

Fractional shares left over by a reverse split are handled per the
issuer's terms: `--round-up` books the free fraction as a zero-cost buy
on the split date (most US issuers round up; some cash out instead, in
which case leave the fraction and record the cash manually).

Price alerts dated before a split are rescaled too, so a "SYTA above $2"
alert becomes "CHAI above $8" rather than firing instantly.

Refuses to merge across differing currencies, which would corrupt the
cost basis rather than fix it.

Usage:
    # always dry-run first — prints the plan, writes nothing
    python scripts/rename_ticker.py --from MBGD --to MBG.DE

    # apply it
    python scripts/rename_ticker.py --from MBGD --to MBG.DE --apply

    # rename with a 1-for-4 reverse split effective 2025-10-07, rounding
    # fractions up (repeat --split for a chain of splits)
    python scripts/rename_ticker.py --from SYTA --to CHAI \
        --split 4:1@2025-10-07 --round-up
"""

import argparse
import asyncio
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.alert import Alert, AlertType
from app.models.dividend import Dividend
from app.models.portfolio import Portfolio, Position, Trade, TradeAction
from app.models.ticker_health import TickerHealth
from app.models.watchlist import WatchlistItem


# ── Split arithmetic (pure, tested) ───────────────────────────────────

@dataclass(frozen=True)
class Split:
    """`old` shares before the split became `new` shares after it, effective
    at the start of `effective`. Trades dated strictly before that day are
    pre-split; the effective day itself already trades in new units."""
    old: int
    new: int
    effective: date

    @property
    def factor(self) -> float:
        """Multiply a pre-split quantity by this to get post-split."""
        return self.new / self.old

    @property
    def label(self) -> str:
        kind = "reverse split" if self.new < self.old else "split"
        return f"{self.old}:{self.new} {kind} {self.effective.isoformat()}"


def parse_split(text: str) -> Split:
    """'4:1@2025-10-07' → Split(old=4, new=1, effective=2025-10-07)."""
    try:
        ratio, day = text.split("@")
        old, new = (int(x) for x in ratio.split(":"))
        if old <= 0 or new <= 0:
            raise ValueError
        return Split(old=old, new=new, effective=date.fromisoformat(day))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"bad split {text!r}: expected OLD:NEW@YYYY-MM-DD, e.g. 4:1@2025-10-07"
        ) from None


@dataclass
class LedgerRow:
    """Split-adjusted view of a Trade. Kept separate from the ORM row so a
    dry run can compute the plan without dirtying the session.

    `trade_id` is None for a synthetic rounding row that does not (yet)
    exist in the database."""
    trade_id: int | None
    portfolio_id: int
    action: TradeAction
    quantity: float
    price: float
    traded_at: datetime
    currency: str | None
    asset_type: object
    original_quantity: float
    original_price: float
    adjustments: list[str] = field(default_factory=list)
    synthetic_note: str | None = None

    @property
    def adjusted(self) -> bool:
        return bool(self.adjustments)


def _rows_from_trades(trades: list[Trade]) -> list[LedgerRow]:
    return [
        LedgerRow(
            trade_id=t.id,
            portfolio_id=t.portfolio_id,
            action=t.action,
            quantity=float(t.quantity),
            price=float(t.price),
            traded_at=t.traded_at,
            currency=t.currency,
            asset_type=t.asset_type,
            original_quantity=float(t.quantity),
            original_price=float(t.price),
        )
        for t in trades
    ]


def _net_quantity(rows: list[LedgerRow], before: date) -> float:
    """Shares held at the start of `before`, replaying rows dated earlier."""
    qty = 0.0
    for r in sorted(rows, key=lambda x: (x.traded_at, x.trade_id or 0)):
        if r.traded_at.date() >= before:
            break
        if r.action in (TradeAction.buy, TradeAction.short):
            qty += r.quantity
        else:
            qty -= r.quantity
            if qty < 1e-9:
                qty = 0.0
    return qty


def apply_splits(rows: list[LedgerRow], splits: list[Split], round_up: bool) -> list[LedgerRow]:
    """Rescale pre-split rows for each split in date order.

    Applied chronologically so that a trade before two splits gets both
    factors, and a rounding row booked at the first split is itself
    rescaled by the second — the same thing the transfer agent does.

    Rounding is per portfolio: it is the aggregate holding that gets
    rounded, not each trade, so one zero-cost buy per portfolio per
    split carries the free fraction. Its `traded_at` is the split date at
    00:00 UTC, which sorts after every pre-split trade and before any
    trade on the effective day.
    """
    rows = [replace(r, adjustments=list(r.adjustments)) for r in rows]
    for s in sorted(splits, key=lambda x: x.effective):
        for r in rows:
            if r.traded_at.date() < s.effective:
                r.quantity *= s.factor
                r.price /= s.factor
                r.adjustments.append(s.label)

        if not round_up or s.new >= s.old:
            continue

        by_portfolio: dict[int, list[LedgerRow]] = defaultdict(list)
        for r in rows:
            by_portfolio[r.portfolio_id].append(r)
        for pid, prow in by_portfolio.items():
            held = _net_quantity(prow, s.effective)
            if held <= 1e-9:
                continue
            frac = math.ceil(held - 1e-9) - held
            if frac <= 1e-9:
                continue
            template = next(r for r in prow if r.traded_at.date() < s.effective)
            rows.append(LedgerRow(
                trade_id=None,
                portfolio_id=pid,
                action=TradeAction.buy,
                quantity=frac,
                price=0.0,
                traded_at=datetime.combine(s.effective, time.min, tzinfo=UTC),
                currency=template.currency,
                asset_type=template.asset_type,
                original_quantity=frac,
                original_price=0.0,
                synthetic_note=(
                    f"Fractional share rounded up per {s.label}: "
                    f"{held:.6f} held → {math.ceil(held - 1e-9)} shares"
                ),
            ))
    return rows


# ── Position rebuild ──────────────────────────────────────────────────

def _rebuild_from_trades(trades: list) -> dict | None:
    """Replay the ledger for one ticker in one portfolio.

    Moving-average cost, long-only — the same convention as
    PortfolioService._update_position and compute_realised_pnl, so the
    rebuilt row agrees with the rest of the app rather than introducing
    a third accounting method.

    Accepts Trade rows or LedgerRows (same attribute names). Returns None
    when the position nets to flat (fully exited).
    """
    qty = 0.0
    avg = 0.0
    opened_at = None
    currency = None
    asset_type = None

    def _key(x):
        return (x.traded_at, getattr(x, "trade_id", None) or getattr(x, "id", 0) or 0)

    for t in sorted(trades, key=_key):
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


def _alert_threshold_after_splits(alert: Alert, splits: list[Split]) -> float | None:
    """Price thresholds set before a split are in old units; a 1-for-4
    turns 'above $2' into 'above $8'. Signal/P&L alerts carry no price."""
    if alert.threshold_value is None:
        return None
    if alert.alert_type not in (AlertType.price_above, AlertType.price_below):
        return float(alert.threshold_value)
    v = float(alert.threshold_value)
    set_on = (alert.created_at or datetime.now(UTC)).date()
    for s in splits:
        if set_on < s.effective:
            v /= s.factor
    return v


# ── Main ──────────────────────────────────────────────────────────────

async def run(old: str, new: str, apply: bool, splits: list[Split], round_up: bool) -> None:
    old, new = old.upper(), new.upper()
    splits = sorted(splits, key=lambda s: s.effective)
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
        if splits:
            print("Splits to apply: " + "; ".join(s.label for s in splits)
                  + (" (fractions rounded up)" if round_up else " (fractions left as-is)"))
            # A split applies to the instrument, not the symbol — trades
            # already recorded under the new ticker but dated before the
            # split are just as pre-split as the old ones.
            pre_new = [t for t in new_trades if t.traded_at.date() < splits[0].effective]
            if pre_new:
                print(f"  note: {len(pre_new)} trade(s) already under {new} predate the "
                      f"first split and will be rescaled too")

        # NOTE: currency is validated PER PORTFOLIO below, not globally.
        # Trades in different portfolios are never merged with one another,
        # so a EUR holding in a real portfolio and a USD paper position in
        # another are not in conflict. An earlier version checked globally
        # and aborted on exactly that, blocking a valid rename.

        # Group affected portfolios so each is rebuilt independently.
        by_portfolio: dict[int, list[Trade]] = defaultdict(list)
        for t in trades:
            by_portfolio[t.portfolio_id].append(t)
        trade_by_id = {t.id: t for t in trades}

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

            ledger = apply_splits(_rows_from_trades(ptrades), splits, round_up)
            if splits:
                print("  Ledger after split adjustment:")
                for r in sorted(ledger, key=lambda x: (x.traded_at, x.trade_id or 0)):
                    tag = f"#{r.trade_id}" if r.trade_id else "NEW"
                    if r.synthetic_note:
                        print(f"    {tag:<6} {r.action.value:<5} {r.quantity:>12.6f} @ {r.price:>10.4f}"
                              f"  {r.traded_at.date()}  ← {r.synthetic_note}")
                    elif r.adjusted:
                        print(f"    {tag:<6} {r.action.value:<5} {r.quantity:>12.6f} @ {r.price:>10.4f}"
                              f"  {r.traded_at.date()}  (was {r.original_quantity:.6f} @ "
                              f"{r.original_price:.4f})")
                    else:
                        print(f"    {tag:<6} {r.action.value:<5} {r.quantity:>12.6f} @ {r.price:>10.4f}"
                              f"  {r.traded_at.date()}  (post-split, unchanged)")
                if not round_up:
                    for s in splits:
                        held = _net_quantity(ledger, s.effective)
                        if held > 1e-9 and abs(held - round(held)) > 1e-9:
                            print(f"  ⚠ {held:.6f} shares held at {s.label} — fractional. "
                                  f"Pass --round-up if the issuer rounds up, or record the "
                                  f"cash-in-lieu manually.")

            rebuilt = _rebuild_from_trades(ledger)
            print(f"  Rebuilt from {len(ledger)} ledger rows:")
            if rebuilt is None:
                print(f"    {new:<8} → flat (fully exited); position row removed")
            else:
                print(f"    {new:<8} qty={rebuilt['quantity']:>12.4f} "
                      f"avg={rebuilt['avg_cost']:>10.4f} {rebuilt['currency']}")

            if not apply:
                continue

            for r in ledger:
                if r.trade_id is None:
                    db.add(Trade(
                        portfolio_id=pid,
                        ticker=new,
                        asset_type=r.asset_type,
                        action=TradeAction.buy,
                        quantity=Decimal(str(round(r.quantity, 6))),
                        price=Decimal("0"),
                        fees=Decimal("0"),
                        currency=r.currency,
                        notes=r.synthetic_note,
                        traded_at=r.traded_at,
                    ))
                    continue
                t = trade_by_id[r.trade_id]
                t.ticker = new
                if r.adjusted:
                    t.quantity = Decimal(str(round(r.quantity, 6)))
                    t.price = Decimal(str(round(r.price, 4)))
                    stamp = (f"[adjusted for {'; '.join(r.adjustments)}: originally "
                             f"{r.original_quantity:.6f} @ {r.original_price:.4f} as {old}]")
                    t.notes = f"{t.notes}\n{stamp}" if t.notes else stamp

            for r in rows:
                await db.delete(r)
            await db.flush()

            if rebuilt is not None:
                db.add(Position(
                    portfolio_id=pid,
                    ticker=new,
                    asset_type=rebuilt["asset_type"],
                    quantity=Decimal(str(round(rebuilt["quantity"], 6))),
                    avg_cost=Decimal(str(round(rebuilt["avg_cost"], 4))),
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

        # Alerts: rename, rescaling price thresholds set before a split.
        alerts = list((await db.execute(
            select(Alert).where(Alert.ticker == old)
        )).scalars().all())
        if alerts:
            print(f"\nAlerts: {len(alerts)} on {old}")
            for a in alerts:
                nv = _alert_threshold_after_splits(a, splits)
                changed = nv is not None and abs(nv - float(a.threshold_value)) > 1e-9
                print(f"  #{a.id} {a.alert_type.value:<12} "
                      f"{float(a.threshold_value) if a.threshold_value is not None else '—'}"
                      + (f" → {nv:.4f}" if changed else "") + f"  → {new}")
                if apply:
                    a.ticker = new
                    if changed:
                        a.threshold_value = Decimal(str(round(nv, 4)))

        # Dividends under the old symbol carry per-share amounts in old
        # units. Drop them; a Sync under the new symbol pulls the full,
        # provider-adjusted history and income is derived from the
        # ledger, so nothing is lost.
        divs = list((await db.execute(
            select(Dividend).where(Dividend.ticker == old)
        )).scalars().all())
        if divs:
            print(f"\nDividends: {len(divs)} row(s) under {old} will be removed "
                  f"(re-sync under {new} after applying)")
            if apply:
                for d in divs:
                    await db.delete(d)

        # The health row for the old symbol is what drives the warning
        # banner. Nothing tracks it once the position is gone, but clear
        # it anyway so a later re-add starts clean.
        th = await db.get(TickerHealth, old)
        if th is not None and apply:
            await db.delete(th)

        if skipped:
            print(f"\n⚠ {len(skipped)} portfolio(s) skipped on a currency clash — "
                  f"see above. Any other portfolios were still processed.")

        if apply:
            await db.commit()
            print("\n✓ Committed.")
            print("  Next: open the Dividends tab and hit Sync so history is pulled")
            print("  for the corrected symbol. Income is derived from the ledger, so")
            print("  the full history appears without re-importing anything.")
            print("  Prices refresh on the next 5-minute Celery cycle.")
        else:
            print("\nNothing written. Re-run with --apply to commit.")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="old", required=True, help="Ticker to rename FROM")
    p.add_argument("--to", dest="new", required=True, help="Ticker to rename TO")
    p.add_argument("--split", dest="splits", action="append", type=parse_split, default=[],
                   metavar="OLD:NEW@YYYY-MM-DD",
                   help="Split to apply to trades dated before the effective date. "
                        "4:1@2025-10-07 = four old shares became one new share. "
                        "Repeatable for a chain of splits.")
    p.add_argument("--round-up", action="store_true",
                   help="Round a fractional post-split holding up to a whole share, "
                        "booked as a zero-cost buy on the split date.")
    p.add_argument("--apply", action="store_true",
                   help="Actually write. Without this it is a dry run.")
    args = p.parse_args()
    asyncio.run(run(args.old, args.new, args.apply, args.splits, args.round_up))


if __name__ == "__main__":
    main()
