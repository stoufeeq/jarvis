"""Add signal_type_strength_overrides JSONB column to strategies

Per-signal-type strength gate for the auto-trader, e.g.
{"fundamental": 4, "technical": 5, "options_flow": 3,
 "earnings_upcoming": 3, "insider": 4}. Falls back to min_strength
for signal types not present in the map.

Revision ID: i0d1e2f3a4b5
Revises: h9c0d1e2f3a4
Create Date: 2026-06-04

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "i0d1e2f3a4b5"
down_revision = "h9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategies",
        sa.Column("signal_type_strength_overrides", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategies", "signal_type_strength_overrides")
