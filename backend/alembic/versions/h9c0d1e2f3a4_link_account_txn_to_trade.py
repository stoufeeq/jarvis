"""Link account_transactions to trades

Adds nullable trade_id FK on account_transactions so trade-driven cash
flows (auto-settle from buy/sell on real portfolios) can be located and
reversed when the trade is edited or deleted. ON DELETE SET NULL so a
hard-deleted trade leaves the historical txn row intact.

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-05-20

"""
import sqlalchemy as sa
from alembic import op

revision = "h9c0d1e2f3a4"
down_revision = "g8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_transactions",
        sa.Column("trade_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_account_transactions_trade_id",
        "account_transactions",
        "trades",
        ["trade_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_account_transactions_trade_id",
        "account_transactions",
        ["trade_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_transactions_trade_id", table_name="account_transactions")
    op.drop_constraint("fk_account_transactions_trade_id", "account_transactions", type_="foreignkey")
    op.drop_column("account_transactions", "trade_id")
