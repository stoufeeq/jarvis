"""
Analyse closed paper trades to find which signals are producing wins
vs losses.

Aggregates by signal_type (technical / insider / options_flow / etc.),
by strength, by exit_reason, and by ticker — then flags the worst
individual trades. Output is a text report to stdout, suitable for
piping into a file.

Run inside the backend container (uses the same DATABASE_URL as the API):

    docker exec jarvis-backend-1 python scripts/analyze_paper_trades.py
    docker exec jarvis-backend-1 python scripts/analyze_paper_trades.py > /tmp/paper_report.txt

The script is read-only — no DB writes, safe to run against production.
"""

import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

sys.path.insert(0, "/app")  # match the container's working dir if run elsewhere

from app.database import AsyncSessionLocal
from app.models.signal import Signal, SignalType, SignalDirection
from app.models.strategy import (
    Strategy,
    StrategyTrade,
    StrategyTradeStatus,
    StrategyExitReason,
)


@dataclass
class ClosedTrade:
    """Flattened view of a closed StrategyTrade with everything the
    aggregations need — pulled out of ORM objects so we don't have to
    keep the session alive across the reporting phase."""
    id: int
    ticker: str
    strategy_name: str
    direction: SignalDirection
    signal_type: SignalType | None    # None if signal was rescanned + SET NULL
    signal_strength: int | None
    entry_price: float
    exit_price: float
    quantity: float
    entry_at: datetime
    exited_at: datetime
    exit_reason: StrategyExitReason | None
    pnl: float                        # $ P&L (long: exit-entry, short: entry-exit) × qty

    @property
    def pnl_pct(self) -> float:
        return (self.pnl / (self.entry_price * self.quantity)) * 100 if self.entry_price else 0

    @property
    def hold_days(self) -> float:
        return (self.exited_at - self.entry_at).total_seconds() / 86400

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


def _pnl(entry: float, exit_: float, qty: float, direction: SignalDirection) -> float:
    # Long ("bullish"): profit when price rises. Short ("bearish"): opposite.
    if direction == SignalDirection.bullish:
        return (exit_ - entry) * qty
    if direction == SignalDirection.bearish:
        return (entry - exit_) * qty
    return 0.0


async def load_closed_trades() -> list[ClosedTrade]:
    async with AsyncSessionLocal() as db:
        # Left-join strategy + trigger signal so we still see trades that
        # lost their signal FK (rescans SET NULL on signals rewrite).
        stmt = (
            select(StrategyTrade)
            .where(StrategyTrade.status == StrategyTradeStatus.closed)
            .options(
                selectinload(StrategyTrade.strategy),
            )
        )
        result = await db.execute(stmt)
        st_rows: list[StrategyTrade] = list(result.scalars().all())

        # Pull the signals we need in one batch keyed by id.
        signal_ids = [t.trigger_signal_id for t in st_rows if t.trigger_signal_id]
        signals_by_id: dict[int, Signal] = {}
        if signal_ids:
            sig_res = await db.execute(select(Signal).where(Signal.id.in_(signal_ids)))
            for s in sig_res.scalars().all():
                signals_by_id[s.id] = s

    out: list[ClosedTrade] = []
    for t in st_rows:
        if t.exit_price is None or t.exited_at is None:
            continue  # defensive — closed but somehow missing exit fields
        sig = signals_by_id.get(t.trigger_signal_id) if t.trigger_signal_id else None
        entry = float(t.entry_price)
        exit_ = float(t.exit_price)
        qty = float(t.quantity)
        out.append(
            ClosedTrade(
                id=t.id,
                ticker=t.ticker,
                strategy_name=t.strategy.name if t.strategy else "?",
                direction=t.direction,
                signal_type=sig.signal_type if sig else None,
                signal_strength=sig.strength if sig else None,
                entry_price=entry,
                exit_price=exit_,
                quantity=qty,
                entry_at=t.entry_at,
                exited_at=t.exited_at,
                exit_reason=t.exit_reason,
                pnl=_pnl(entry, exit_, qty, t.direction),
            )
        )
    return out


# ── Formatting helpers ────────────────────────────────────────────────

def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _pct(x: float) -> str:
    return f"{x:+.2f}%"


def _header(title: str) -> str:
    line = "─" * 74
    return f"\n{line}\n {title}\n{line}"


def _summarise(trades: list[ClosedTrade]) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "hit_rate": 0.0,
                "total_pnl": 0.0, "avg_pnl": 0.0, "median_pnl": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0}
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    total = sum(t.pnl for t in trades)
    n = len(trades)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = -sum(losses)  # positive number
    # Profit factor = gross wins / gross losses. >1 means the strategy
    # made more than it lost. 1.5+ is decent, 2+ is strong. <1 = losing.
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "hit_rate": (len(wins) / n) * 100 if n else 0.0,
        "total_pnl": total, "avg_pnl": total / n,
        "median_pnl": median(t.pnl for t in trades),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": profit_factor,
    }


def _fmt_bucket_line(label: str, s: dict, width: int = 34) -> str:
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (
        f"{label:<{width}} "
        f"n={s['n']:>4}  "
        f"hit={s['hit_rate']:>5.1f}%  "
        f"total={_money(s['total_pnl']):>12}  "
        f"avg={_money(s['avg_pnl']):>10}  "
        f"pf={pf:>5}"
    )


# ── Report sections ───────────────────────────────────────────────────

def report_overall(trades: list[ClosedTrade]) -> str:
    s = _summarise(trades)
    if s["n"] == 0:
        return _header("OVERALL") + "\n\n No closed paper trades in the database.\n"
    lines = [_header("OVERALL")]
    lines.append(f"\n Closed trades:        {s['n']}")
    lines.append(f" Wins / Losses:        {s['wins']} / {s['losses']}  ({s['hit_rate']:.1f}% hit rate)")
    lines.append(f" Total P&L:            {_money(s['total_pnl'])}")
    lines.append(f" Avg trade P&L:        {_money(s['avg_pnl'])}")
    lines.append(f" Median trade P&L:     {_money(s['median_pnl'])}")
    lines.append(f" Avg win / Avg loss:   {_money(s['avg_win'])} / {_money(s['avg_loss'])}")
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    lines.append(f" Profit factor:        {pf}   (>1 = profitable, 1.5+ decent, 2+ strong)")
    return "\n".join(lines) + "\n"


def report_by_signal_type(trades: list[ClosedTrade]) -> str:
    buckets: dict[str, list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        key = t.signal_type.value if t.signal_type else "(unknown — signal deleted)"
        buckets[key].append(t)
    lines = [_header("BY SIGNAL TYPE")]
    # Sort by total P&L descending so top winners lead
    ordered = sorted(buckets.items(), key=lambda kv: -_summarise(kv[1])["total_pnl"])
    lines.append("")
    for name, group in ordered:
        s = _summarise(group)
        lines.append(_fmt_bucket_line(name, s))
    return "\n".join(lines) + "\n"


def report_by_signal_type_and_strength(trades: list[ClosedTrade]) -> str:
    buckets: dict[tuple[str, int | None], list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        key = (t.signal_type.value if t.signal_type else "?", t.signal_strength)
        buckets[key].append(t)
    lines = [_header("BY SIGNAL TYPE × STRENGTH")]
    lines.append(" (does higher strength → better outcomes?)")
    lines.append("")
    ordered = sorted(buckets.items(), key=lambda kv: (kv[0][0], -(kv[0][1] or -1)))
    for (name, strength), group in ordered:
        s = _summarise(group)
        label = f"{name} · str={strength if strength is not None else '?'}"
        lines.append(_fmt_bucket_line(label, s))
    return "\n".join(lines) + "\n"


def report_by_exit_reason(trades: list[ClosedTrade]) -> str:
    buckets: dict[str, list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        key = t.exit_reason.value if t.exit_reason else "?"
        buckets[key].append(t)
    lines = [_header("BY EXIT REASON")]
    lines.append(" (were planned exits better than opposite-signal exits?)")
    lines.append("")
    ordered = sorted(buckets.items(), key=lambda kv: -_summarise(kv[1])["total_pnl"])
    for name, group in ordered:
        s = _summarise(group)
        lines.append(_fmt_bucket_line(name, s))
    return "\n".join(lines) + "\n"


def report_worst_tickers(trades: list[ClosedTrade], limit: int = 10) -> str:
    buckets: dict[str, list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        buckets[t.ticker].append(t)
    ordered = sorted(buckets.items(), key=lambda kv: _summarise(kv[1])["total_pnl"])[:limit]
    lines = [_header(f"WORST {limit} TICKERS (by cumulative P&L)")]
    lines.append("")
    for name, group in ordered:
        s = _summarise(group)
        lines.append(_fmt_bucket_line(name, s, width=8))
    return "\n".join(lines) + "\n"


def report_worst_trades(trades: list[ClosedTrade], limit: int = 10) -> str:
    ordered = sorted(trades, key=lambda t: t.pnl)[:limit]
    lines = [_header(f"WORST {limit} INDIVIDUAL TRADES")]
    lines.append(
        f"\n {'Ticker':<8} {'Type':<14} {'Dir':<8} {'Str':<4} "
        f"{'Entry':>9} {'Exit':>9} {'Qty':>6} {'HoldDays':>9} "
        f"{'P&L':>12} {'P&L %':>9} Exit"
    )
    lines.append(" " + "─" * 116)
    for t in ordered:
        st = t.signal_type.value if t.signal_type else "?"
        dir_ = t.direction.value
        er = t.exit_reason.value if t.exit_reason else "?"
        lines.append(
            f" {t.ticker:<8} {st:<14} {dir_:<8} {str(t.signal_strength or '?'):<4} "
            f"{t.entry_price:>9.2f} {t.exit_price:>9.2f} {t.quantity:>6.1f} {t.hold_days:>9.1f} "
            f"{_money(t.pnl):>12} {_pct(t.pnl_pct):>9} {er}"
        )
    return "\n".join(lines) + "\n"


def report_recommendations(trades: list[ClosedTrade]) -> str:
    """Concrete strategy-config actions, derived from the aggregates."""
    lines = [_header("RECOMMENDATIONS")]
    if not trades:
        lines.append(" No trades to analyse.")
        return "\n".join(lines) + "\n"

    # (signal_type × strength) buckets meeting a min sample of 3 —
    # anything smaller is noise but still worth surfacing.
    MIN_N = 3

    by_type_str: dict[tuple[str, int | None], list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        by_type_str[(t.signal_type.value if t.signal_type else "?", t.signal_strength)].append(t)

    losers: list[tuple[str, int | None, dict]] = []
    winners: list[tuple[str, int | None, dict]] = []
    for (name, strength), group in by_type_str.items():
        s = _summarise(group)
        if s["n"] < MIN_N:
            continue
        # profit factor + hit rate + total P&L combined to sort
        if s["total_pnl"] < 0 and s["hit_rate"] < 50:
            losers.append((name, strength, s))
        elif s["total_pnl"] > 0 and s["profit_factor"] >= 1.3:
            winners.append((name, strength, s))

    losers.sort(key=lambda x: x[2]["total_pnl"])
    winners.sort(key=lambda x: -x[2]["total_pnl"])

    lines.append("")
    lines.append(f" (only buckets with n ≥ {MIN_N} — smaller samples are noise)")

    lines.append("\n DROP or gate up in your strategy config:")
    if not losers:
        lines.append("   • No consistently losing (signal_type × strength) bucket. Losses may")
        lines.append("     be from small-sample or unrouted trades — inspect the worst trades")
        lines.append("     section above for individual outliers.")
    else:
        for name, strength, s in losers[:10]:
            lines.append(
                f"   • {name} @ strength {strength}: "
                f"n={s['n']}, hit={s['hit_rate']:.0f}%, total={_money(s['total_pnl'])}"
            )
            if strength is not None:
                lines.append(
                    f"     → raise min_strength for '{name}' to {strength + 1} "
                    "(signal_type_strength_overrides)"
                )
            else:
                lines.append(f"     → consider removing '{name}' from your strategy filter")

    lines.append("\n KEEP or expand:")
    if not winners:
        lines.append("   • No bucket meeting the 'profitable + profit factor ≥ 1.3' bar.")
        lines.append("     Focus on cutting losers before broadening.")
    else:
        for name, strength, s in winners[:10]:
            pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
            lines.append(
                f"   • {name} @ strength {strength}: "
                f"n={s['n']}, hit={s['hit_rate']:.0f}%, total={_money(s['total_pnl'])}, pf={pf}"
            )

    return "\n".join(lines) + "\n"


async def main():
    trades = await load_closed_trades()

    print(_header(f"PAPER TRADING SIGNAL ANALYSIS  ({datetime.now(timezone.utc).isoformat(timespec='minutes')} UTC)"))
    print(f"\n Loaded {len(trades)} closed paper trades from strategy_trades.\n")

    if not trades:
        print(" Nothing to analyse. Either no strategies have run to completion")
        print(" or all trades are still open.")
        return

    print(report_overall(trades))
    print(report_by_signal_type(trades))
    print(report_by_signal_type_and_strength(trades))
    print(report_by_exit_reason(trades))
    print(report_worst_tickers(trades))
    print(report_worst_trades(trades))
    print(report_recommendations(trades))


if __name__ == "__main__":
    asyncio.run(main())
