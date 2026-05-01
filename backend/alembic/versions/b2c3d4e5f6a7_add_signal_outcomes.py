"""Add signal_outcomes table for tracking signal performance

Captures the price at signal creation and at +1d, +5d, +30d, +90d
intervals so we can measure whether each signal type/direction/strength
actually predicts price movement. Denormalized: stores its own copy
of signal_type/direction/strength so rows survive scan_ticker rescans
which delete prior signals.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-28

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


# Reference existing enum types created by earlier migrations — do not re-create.
signal_type_enum = postgresql.ENUM(
    "technical", "insider", "ai_news", "options_flow", "fundamental",
    "earnings_upcoming", "macro_event", "cross_impact",
    name="signal_type",
    create_type=False,
)
signal_direction_enum = postgresql.ENUM(
    "bullish", "bearish", "neutral",
    name="signal_direction",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "signal_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "signal_id",
            sa.Integer(),
            sa.ForeignKey("signals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ticker", sa.String(20), nullable=False, index=True),
        sa.Column("signal_type", signal_type_enum, nullable=False),
        sa.Column("direction", signal_direction_enum, nullable=False),
        sa.Column("strength", sa.SmallInteger(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("entry_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("signal_created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("price_1d", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_5d", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_30d", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_90d", sa.Numeric(18, 4), nullable=True),
        sa.Column("snapshot_1d_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_5d_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_30d_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_90d_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_signal_outcomes_ticker_created", "signal_outcomes", ["ticker", "signal_created_at"])
    op.create_index("ix_signal_outcomes_type_direction", "signal_outcomes", ["signal_type", "direction"])


def downgrade() -> None:
    op.drop_index("ix_signal_outcomes_type_direction", table_name="signal_outcomes")
    op.drop_index("ix_signal_outcomes_ticker_created", table_name="signal_outcomes")
    op.drop_table("signal_outcomes")
