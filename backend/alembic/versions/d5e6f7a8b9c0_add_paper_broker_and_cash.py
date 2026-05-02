"""Add paper broker type and cash balance columns to portfolios

Adds:
- 'paper' to the broker_type enum
- initial_cash, cash_balance columns to portfolios (nullable; only used when
  broker = 'paper')

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-05-02

"""
import sqlalchemy as sa
from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Extend the existing broker_type enum with the 'paper' value
    op.execute("ALTER TYPE broker_type ADD VALUE IF NOT EXISTS 'paper'")

    # 2. Add cash columns to portfolios — null for non-paper portfolios
    op.add_column("portfolios", sa.Column("initial_cash", sa.Numeric(18, 4), nullable=True))
    op.add_column("portfolios", sa.Column("cash_balance", sa.Numeric(18, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("portfolios", "cash_balance")
    op.drop_column("portfolios", "initial_cash")
    # Note: removing an enum value in PostgreSQL requires recreating the enum.
    # Skipped here — leaving 'paper' in the enum is harmless on downgrade.
