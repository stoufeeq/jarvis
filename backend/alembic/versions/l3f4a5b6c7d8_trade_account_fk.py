"""Add trades.account_id FK — explicit funding account per trade

When set, TradeCashService debits/credits only this account; HTTP 400
on insufficient funds in the trade currency. NULL preserves the
existing USD→SGD→EUR fallback-chain behaviour.

Revision ID: l3f4a5b6c7d8
Revises: k2f3a4b5c6d7
Create Date: 2026-06-17

"""
import sqlalchemy as sa
from alembic import op

revision = "l3f4a5b6c7d8"
down_revision = "k2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("account_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_trades_account_id",
        "trades",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_trades_account_id", "trades", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_trades_account_id", table_name="trades")
    op.drop_constraint("fk_trades_account_id", "trades", type_="foreignkey")
    op.drop_column("trades", "account_id")
