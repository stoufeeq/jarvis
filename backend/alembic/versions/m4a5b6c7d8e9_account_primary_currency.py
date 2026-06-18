"""Add accounts.primary_currency with smart backfill

The credit side of trade-cash wiring (sell proceeds) deposits into the
account's primary currency, FX-converting from the trade currency when
they differ. For backfill, default each account's primary to whichever
currency holds its largest existing balance; fall back to USD when an
account has no balance rows yet.

Revision ID: m4a5b6c7d8e9
Revises: l3f4a5b6c7d8
Create Date: 2026-06-19

"""
import sqlalchemy as sa
from alembic import op

revision = "m4a5b6c7d8e9"
down_revision = "l3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL with DEFAULT 'USD' — Postgres backfills existing rows.
    op.add_column(
        "accounts",
        sa.Column(
            "primary_currency", sa.String(10),
            nullable=False, server_default="USD",
        ),
    )
    # Smart backfill: for any account with at least one balance row, set
    # primary_currency to the currency of its largest balance. Accounts
    # with no balances stay on the 'USD' default.
    op.execute("""
        UPDATE accounts a
        SET primary_currency = COALESCE(
            (SELECT b.currency
             FROM account_balances b
             WHERE b.account_id = a.id
             ORDER BY b.balance DESC
             LIMIT 1),
            'USD'
        )
    """)


def downgrade() -> None:
    op.drop_column("accounts", "primary_currency")
