"""
IBKR trade import — APPLY (destructive).

DELETES the target portfolio's existing trades + linked
account_transactions + positions, INSERTS 297 rows from the spreadsheet,
then INSERTS one reconciliation transaction per Cash Account to bring
each balance to the user-declared target.

Everything runs inside ONE DB transaction — any failure rolls back
completely, leaving the DB in its original state.

Requires --confirm flag. Refuses to run against paper portfolios.

Usage:
    docker exec jarvis-backend-1 python scripts/ibkr_import_apply.py \\
        /tmp/ibkr.xlsx --portfolio-id 1 --confirm
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import UTC, datetime, time
from decimal import Decimal

import pandas as pd
from sqlalchemy import delete, select

sys.path.insert(0, "/app")

from app.database import AsyncSessionLocal
from app.models.account import (
    AccountBalance,
    AccountTransaction,
    TransactionType,
)
from app.models.portfolio import (
    AssetType,
    BrokerType,
    Portfolio,
    Position,
    Trade,
    TradeAction,
)
from app.services.portfolio import PortfolioService


# Same mappings as dryrun.
CURRENCY_TO_ACCOUNT_ID = {
    "USD": 1,
    "SGD": 2,
    "EUR": 3,
    "GBP": 7,
}

TARGET_BALANCES = {
    1: ("USD", Decimal("0.00")),
    2: ("SGD", Decimal("6.93")),
    3: ("EUR", Decimal("18.04")),
    7: ("GBP", Decimal("0.00")),
}


def _load_spreadsheet(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    required = {"Symbol", "TradeDate", "TradeTime", "Type", "Quantity",
                "Price", "Proceeds", "Comm", "Fee", "Currency"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Spreadsheet missing columns: {missing}")
    if df["Quantity"].isna().any() or (df["Quantity"] == 0).any():
        raise SystemExit("Rows with zero/NaN Quantity — cannot import.")
    if df["Price"].isna().any() or (df["Price"] == 0).any():
        raise SystemExit("Rows with zero/NaN Price — cannot import.")
    for ccy in df["Currency"].unique():
        if str(ccy).upper() not in CURRENCY_TO_ACCOUNT_ID:
            raise SystemExit(f"No account mapping for currency {ccy!r}")
    return df


def _combine_datetime(row) -> datetime:
    """TradeDate is a Timestamp; TradeTime is a pandas Timestamp/time.
    Combine them into a single tz-aware UTC datetime for storage.
    NB: IBKR statement times are in the exchange local zone but we treat
    them as UTC since we don't know the exchange TZ per row and the
    absolute clock doesn't matter for portfolio arithmetic — only the
    calendar date does."""
    d = row["TradeDate"]
    t = row["TradeTime"]
    if isinstance(t, pd.Timestamp):
        t = t.time()
    if not isinstance(t, time):
        t = time(0, 0, 0)
    return datetime.combine(d.date(), t, tzinfo=UTC)


async def _apply(path: str, portfolio_id: int) -> None:
    df = _load_spreadsheet(path)

    async with AsyncSessionLocal() as db:
        # Everything below happens in a single implicit transaction —
        # async SQLAlchemy sessions don't commit until we say so, and
        # if the block raises we simply don't commit → all writes
        # rolled back. Safety net.
        portfolio = await db.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise SystemExit(f"Portfolio id={portfolio_id} not found.")
        if portfolio.broker == BrokerType.paper:
            raise SystemExit(
                f"Portfolio id={portfolio_id} is a PAPER portfolio — refusing "
                "to run the destructive rebuild. Pick your real IBKR portfolio."
            )
        print(f"Target portfolio: id={portfolio.id} name={portfolio.name!r} "
              f"broker={portfolio.broker.value}")

        # ── Step 1: delete existing account_transactions for those trades ──
        trade_ids_result = await db.execute(
            select(Trade.id).where(Trade.portfolio_id == portfolio.id)
        )
        existing_trade_ids = [r[0] for r in trade_ids_result.all()]

        if existing_trade_ids:
            del_txns = await db.execute(
                delete(AccountTransaction).where(
                    AccountTransaction.trade_id.in_(existing_trade_ids)
                )
            )
            print(f"  Deleted {del_txns.rowcount} account_transactions")

        # ── Step 2: delete positions for this portfolio ─────────────────────
        del_pos = await db.execute(
            delete(Position).where(Position.portfolio_id == portfolio.id)
        )
        print(f"  Deleted {del_pos.rowcount} positions")

        # ── Step 3: delete existing trades ─────────────────────────────────
        del_trades = await db.execute(
            delete(Trade).where(Trade.portfolio_id == portfolio.id)
        )
        print(f"  Deleted {del_trades.rowcount} trades")

        # ── Step 4: insert 297 trades + matching account_transactions ──────
        inserted_trades = 0
        inserted_txns = 0
        for _, row in df.iterrows():
            ccy = str(row["Currency"]).upper()
            acct_id = CURRENCY_TO_ACCOUNT_ID[ccy]
            action_type = str(row["Type"]).upper()
            is_buy = action_type == "BUY"
            qty_abs = abs(float(row["Quantity"]))
            price = float(row["Price"])
            fees = abs(float(row["Comm"])) + abs(float(row["Fee"]))
            traded_at = _combine_datetime(row)

            trade = Trade(
                portfolio_id=portfolio.id,
                ticker=str(row["Symbol"]).upper(),
                asset_type=AssetType.stock,
                action=TradeAction.buy if is_buy else TradeAction.sell,
                quantity=Decimal(str(round(qty_abs, 6))),
                price=Decimal(str(round(price, 4))),
                fees=Decimal(str(round(fees, 4))),
                currency=ccy,
                traded_at=traded_at,
                account_id=acct_id,
            )
            db.add(trade)
            await db.flush()  # need trade.id for the txn FK
            inserted_trades += 1

            # Cash impact — matches the trade_cash service semantics.
            #   BUY  → withdrawal of (notional + fees)
            #   SELL → deposit of (notional - fees)
            notional = qty_abs * price
            if is_buy:
                cash_amount = notional + fees
                txn_type = TransactionType.withdrawal
            else:
                cash_amount = notional - fees
                txn_type = TransactionType.deposit

            db.add(AccountTransaction(
                account_id=acct_id,
                transaction_type=txn_type,
                amount=Decimal(str(round(cash_amount, 4))),
                currency=ccy,
                notes=(
                    f"IBKR import: {action_type} {qty_abs} {row['Symbol']} "
                    f"@ {price} {ccy} (comm={abs(float(row['Comm']))}, "
                    f"fee={abs(float(row['Fee']))})"
                ),
                transacted_at=traded_at,
                trade_id=trade.id,
            ))
            inserted_txns += 1

        await db.flush()
        print(f"  Inserted {inserted_trades} trades + {inserted_txns} account_transactions")

        # ── Step 5: recompute positions from the fresh trade ledger ────────
        # PortfolioService._recalculate_position walks the trade ledger
        # for one ticker and rebuilds the Position row (or deletes it if
        # net quantity ends at 0). Run per unique ticker.
        unique_tickers = df["Symbol"].str.upper().unique()
        svc = PortfolioService(db)
        for ticker in unique_tickers:
            await svc._recalculate_position(portfolio.id, ticker)
        await db.flush()
        # Count what actually stuck around (net qty > 0).
        pos_count = (await db.execute(
            select(Position).where(Position.portfolio_id == portfolio.id)
        )).all()
        print(f"  Recomputed positions: {len(pos_count)} tickers with open positions")

        # ── Step 6: recompute account balances from ALL txns, then adjust ──
        # Post-import, each Cash Account's balance = sum of every deposit
        # minus every withdrawal on it, across all currencies it holds.
        # If it doesn't equal the target, insert ONE reconciliation
        # transaction to bridge, then update the account_balances row.
        print("\n  Reconciliation adjustments:")
        for acct_id, (target_ccy, target_amt) in TARGET_BALANCES.items():
            # Recompute this account's current balance in target_ccy from
            # all its transactions (post-import).
            txns_result = await db.execute(
                select(AccountTransaction).where(
                    AccountTransaction.account_id == acct_id,
                    AccountTransaction.currency == target_ccy,
                )
            )
            txns = txns_result.scalars().all()
            computed = Decimal("0")
            for tx in txns:
                sign = Decimal("1") if tx.transaction_type == TransactionType.deposit else Decimal("-1")
                computed += sign * tx.amount

            delta = target_amt - computed
            if abs(delta) < Decimal("0.005"):
                print(f"    acct {acct_id} {target_ccy}: no adjustment "
                      f"(computed {computed:.4f} ≈ target {target_amt:.4f})")
                continue

            if delta > 0:
                txn_type = TransactionType.deposit
                amount = delta
            else:
                txn_type = TransactionType.withdrawal
                amount = -delta

            db.add(AccountTransaction(
                account_id=acct_id,
                transaction_type=txn_type,
                amount=amount,
                currency=target_ccy,
                notes=(
                    f"IBKR import reconciliation 2026-07-28: brings "
                    f"account balance to {target_amt} {target_ccy} "
                    f"(matches IBKR portal). Captures cumulative "
                    f"transfers/FX/dividends not in the trades sheet."
                ),
                transacted_at=datetime.now(UTC),
            ))
            print(f"    acct {acct_id} {target_ccy}: {txn_type.value} {amount:.2f} "
                  f"(computed {computed:.4f} → target {target_amt})")

            # Set the balance row directly to the target so we don't rely
            # on some other pass rebuilding it.
            bal_row = (await db.execute(
                select(AccountBalance).where(
                    AccountBalance.account_id == acct_id,
                    AccountBalance.currency == target_ccy,
                )
            )).scalar_one_or_none()
            if bal_row is None:
                db.add(AccountBalance(
                    account_id=acct_id,
                    currency=target_ccy,
                    balance=target_amt,
                ))
            else:
                bal_row.balance = target_amt

        await db.flush()

        # ── Step 7: final balance snapshot for verification ────────────────
        print("\n  Final account balances:")
        all_bals = (await db.execute(
            select(AccountBalance).order_by(AccountBalance.account_id, AccountBalance.currency)
        )).scalars().all()
        for b in all_bals:
            print(f"    acct {b.account_id} {b.currency}: {float(b.balance):>14,.4f}")

        # Commit — we made it through everything.
        await db.commit()
        print("\n  COMMITTED.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", help="Path to the IBKR trades .xlsx export")
    parser.add_argument("--portfolio-id", type=int, required=True,
                        help="IBKR portfolio id to rebuild")
    parser.add_argument("--confirm", action="store_true",
                        help="Required flag — proves you know this is destructive")
    args = parser.parse_args()

    if not args.confirm:
        raise SystemExit(
            "Refusing to run without --confirm. This script DELETES the "
            "target portfolio's existing trades + txns + positions and "
            "rebuilds them from the spreadsheet. Add --confirm when you're "
            "sure the dry-run output looks correct."
        )

    await _apply(args.xlsx, args.portfolio_id)


if __name__ == "__main__":
    asyncio.run(main())
