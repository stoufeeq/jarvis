"""
IBKR trade import — DRY RUN (read-only, no writes).

Reads the IBKR trade statement Excel export and prints:
  - What would be inserted (per currency, per action)
  - What existing IBKR trades would be deleted first
  - Projected per-account transaction sums after the rebuild
  - Recommended reconciliation adjustment per account to hit the target
    balances the user provided.

Nothing is written to the DB. Sister script `ibkr_import_apply.py`
performs the actual rebuild after the user reviews this output.

Usage:
    docker cp <path-to-xlsx> jarvis-backend-1:/tmp/ibkr.xlsx
    docker exec jarvis-backend-1 python scripts/ibkr_import_dryrun.py \\
        /tmp/ibkr.xlsx --portfolio-id 1

Portfolio ID must be given explicitly — the DB may contain multiple
portfolios named 'IBKR' across different users, so guessing is unsafe.
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from decimal import Decimal

import pandas as pd
from sqlalchemy import select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.account import AccountTransaction, TransactionType
from app.models.portfolio import Portfolio, Trade


# Currency → account_id mapping. Confirmed by the user 2026-07-28.
# Empty currency (should never happen) falls to USD as a safety.
CURRENCY_TO_ACCOUNT_ID = {
    "USD": 1,
    "SGD": 2,
    "EUR": 3,
    "GBP": 7,
}

# Target ending balance per account (from the user, matched to actual
# IBKR portal). The rebuild inserts one adjustment transaction per
# account to make the computed balance match this exact number.
TARGET_BALANCES = {
    1: ("USD", 0.00),      # Cash Account USD
    2: ("SGD", 6.93),      # Cash Account SGD
    3: ("EUR", 18.04),     # Cash Account EUR (unchanged — dividends likely)
    7: ("GBP", 0.00),      # Cash Account GBP (new)
}


def _load_spreadsheet(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    # Sanity checks so a malformed row doesn't sneak through.
    required = {"Symbol", "TradeDate", "TradeTime", "Type", "Quantity",
                "Price", "Proceeds", "Comm", "Fee", "Currency"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Spreadsheet missing columns: {missing}")
    if df["Quantity"].isna().any() or (df["Quantity"] == 0).any():
        raise SystemExit("Row(s) with zero/NaN Quantity detected — cannot import.")
    if df["Price"].isna().any() or (df["Price"] == 0).any():
        raise SystemExit("Row(s) with zero/NaN Price detected — cannot import.")
    if df["Currency"].isna().any():
        raise SystemExit("Row(s) with missing Currency detected — cannot import.")
    return df


def _summarise_import(df: pd.DataFrame) -> None:
    print("─" * 72)
    print(f" SPREADSHEET SUMMARY  ({len(df)} rows)")
    print("─" * 72)
    print(f"  Date range:  {df['TradeDate'].min().date()} → {df['TradeDate'].max().date()}")
    print()
    print("  By currency:")
    for ccy, count in df.groupby("Currency").size().items():
        acct_id = CURRENCY_TO_ACCOUNT_ID.get(ccy)
        target = "→ account #%s" % acct_id if acct_id else "→ NO MAPPING (would fail)"
        print(f"    {ccy}: {count:>4} trades  {target}")
    print()
    print("  By action:")
    for typ, count in df.groupby("Type").size().items():
        print(f"    {typ}: {count:>4}")
    print()
    print(f"  Unique tickers: {df['Symbol'].nunique()}")


async def _snapshot_existing(portfolio_id: int) -> tuple[Portfolio, list[Trade]]:
    """Fetch the target portfolio + its existing trades. Portfolio ID
    is passed explicitly (not looked up by name) because the DB can
    contain multiple portfolios named 'IBKR' across users."""
    async with AsyncSessionLocal() as db:
        portfolio = await db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise SystemExit(f"Portfolio id={portfolio_id} not found.")
        if not portfolio.is_active:
            raise SystemExit(f"Portfolio id={portfolio_id} is inactive.")

        trades_result = await db.execute(
            select(Trade).where(Trade.portfolio_id == portfolio.id)
        )
        trades = list(trades_result.scalars().all())
        return portfolio, trades


async def _snapshot_balances() -> dict[int, dict[str, float]]:
    """Current balance snapshot keyed by account_id → {currency: amount}."""
    from app.models.account import AccountBalance
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AccountBalance))).scalars().all()
        out: dict[int, dict[str, float]] = defaultdict(dict)
        for r in rows:
            out[r.account_id][r.currency] = float(r.balance)
        return out


def _project_transactions(df: pd.DataFrame) -> dict[int, dict[str, float]]:
    """Sum the projected account_transaction impact per (account, currency)
    for the imported rows. Positive = deposit, negative = withdrawal.

    For each row:
      - amount = |Proceeds| + |Comm| + |Fee|
      - BUY (Type=='BUY', Quantity>0)  → withdrawal (negative delta)
      - SELL (Type=='SELL', Quantity<0) → deposit (positive delta, minus fees)

    We match the trade_cash service semantics: BUY cash-out = notional + fees,
    SELL cash-in = notional - fees.
    """
    delta: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for _, row in df.iterrows():
        ccy = str(row["Currency"]).upper()
        acct = CURRENCY_TO_ACCOUNT_ID.get(ccy)
        if acct is None:
            continue  # would fail at apply; already flagged
        notional = float(row["Quantity"]) * float(row["Price"])
        fees = abs(float(row["Comm"])) + abs(float(row["Fee"]))
        if str(row["Type"]).upper() == "BUY":
            # notional > 0 for buys (positive qty), cash goes OUT.
            delta[acct][ccy] -= notional + fees
        else:  # SELL — quantity is negative, notional is negative
            # cash IN = |notional| - fees
            delta[acct][ccy] += abs(notional) - fees
    return delta


def _print_deletions(portfolio: Portfolio, existing_trades: list[Trade]) -> None:
    print("─" * 72)
    print(f" WOULD DELETE  (from portfolio '{portfolio.name}' id={portfolio.id})")
    print("─" * 72)
    print(f"  {len(existing_trades)} existing trades")
    print(f"  All account_transactions linked to those trades (via trade_id)")
    print(f"  All positions for portfolio {portfolio.id} (will be re-derived)")
    print()


def _print_balance_projection(
    current: dict[int, dict[str, float]],
    delta: dict[int, dict[str, float]],
) -> None:
    print("─" * 72)
    print(" BALANCE PROJECTION")
    print("─" * 72)
    print(
        f"  {'Acct':<6} {'Ccy':<4} {'Current':>14} "
        f"{'Δ from import':>16} {'Projected':>14} {'Target':>12} {'Adjustment':>12}"
    )
    all_accounts = sorted(set(current.keys()) | set(delta.keys()) | set(TARGET_BALANCES.keys()))
    for acct_id in all_accounts:
        # Determine currency for this row
        target_entry = TARGET_BALANCES.get(acct_id)
        currencies = set(current.get(acct_id, {}).keys()) | set(delta.get(acct_id, {}).keys())
        if target_entry:
            currencies.add(target_entry[0])
        for ccy in sorted(currencies):
            cur = current.get(acct_id, {}).get(ccy, 0.0)
            d = delta.get(acct_id, {}).get(ccy, 0.0)
            projected = cur + d
            # NB: because we're going to WIPE existing IBKR-trade txns and
            # replace with spreadsheet-derived ones, the *pure rebuild*
            # projection is delta minus whatever the current IBKR-trade
            # txns contributed. We simplify here by computing final =
            # projected − existing_ibkr_txn_sum in the apply script; for
            # this dry-run we show ONLY spreadsheet delta so the user can
            # see what the import produces on its own. The adjustment
            # column then bridges to target using both effects.
            target_ccy, target_amt = (target_entry if (target_entry and target_entry[0] == ccy) else (None, None))
            target_str = f"{target_amt:>12,.2f}" if target_amt is not None else " " * 12
            adjustment_str = f"{(target_amt - projected):>12,.2f}" if target_amt is not None else " " * 12
            print(
                f"  {acct_id:<6} {ccy:<4} {cur:>14,.2f} {d:>16,.2f} "
                f"{projected:>14,.2f} {target_str} {adjustment_str}"
            )
    print()
    print("  NOTE: 'Δ from import' assumes existing IBKR-trade txns are wiped.")
    print("        'Adjustment' is what the apply script would insert as a")
    print("        compensating 'Reconciliation' transaction to hit target.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", help="Path to the IBKR trades .xlsx export")
    parser.add_argument(
        "--portfolio-id", type=int, required=True,
        help="Explicit portfolio id to rebuild (avoids name-based guessing)",
    )
    args = parser.parse_args()

    df = _load_spreadsheet(args.xlsx)
    _summarise_import(df)

    portfolio, existing = await _snapshot_existing(args.portfolio_id)
    _print_deletions(portfolio, existing)

    current_balances = await _snapshot_balances()

    # Subtract the impact of existing IBKR trade txns from the "current"
    # column, since those will be deleted before the rebuild. This gives
    # the true starting point for the delta math.
    from app.models.account import AccountTransaction
    async with AsyncSessionLocal() as db:
        existing_trade_ids = [t.id for t in existing]
        if existing_trade_ids:
            existing_txns = (await db.execute(
                select(AccountTransaction).where(
                    AccountTransaction.trade_id.in_(existing_trade_ids)
                )
            )).scalars().all()
            for tx in existing_txns:
                sign = 1 if tx.transaction_type == TransactionType.deposit else -1
                current_balances[tx.account_id].setdefault(tx.currency, 0.0)
                current_balances[tx.account_id][tx.currency] -= sign * float(tx.amount)

    projected_delta = _project_transactions(df)
    _print_balance_projection(current_balances, projected_delta)

    print("─" * 72)
    print(" WHAT HAPPENS NEXT")
    print("─" * 72)
    print("  1. Review the numbers above.")
    print("  2. If they look right, run:")
    print("       docker exec jarvis-backend-1 python scripts/ibkr_import_apply.py /tmp/ibkr.xlsx")
    print("  3. If they don't, tell me what's off before applying.")


if __name__ == "__main__":
    asyncio.run(main())
