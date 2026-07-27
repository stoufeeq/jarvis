"""Add allowed_account_ids to portfolios.

Comma-separated list of account IDs that trades in this portfolio may
use as their funding account. NULL / empty = no restriction (legacy
behaviour). When populated:
  - Explicit trade.account_id must be in the list, else 400
  - Auto-funded (account_id NULL) trades: fallback chain restricted
    to only these accounts

Prevents the class of drift that had IBKR trades quietly tapping the
SRS SGD account when USD Cash ran short.

Revision ID: q8e9f0a1b2c3
Revises: p7d8e9f0a1b2
Create Date: 2026-07-28
"""
import sqlalchemy as sa
from alembic import op

revision = "q8e9f0a1b2c3"
down_revision = "p7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column("allowed_account_ids", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolios", "allowed_account_ids")
