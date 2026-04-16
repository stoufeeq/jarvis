"""Add earnings_upcoming, macro_event, cross_impact signal types

Revision ID: e1f2a3b4c5d6
Revises: f1a2b3c4d5e6
Create Date: 2026-04-17

"""
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE signal_type ADD VALUE IF NOT EXISTS 'earnings_upcoming'")
    op.execute("ALTER TYPE signal_type ADD VALUE IF NOT EXISTS 'macro_event'")
    op.execute("ALTER TYPE signal_type ADD VALUE IF NOT EXISTS 'cross_impact'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op.
    pass
