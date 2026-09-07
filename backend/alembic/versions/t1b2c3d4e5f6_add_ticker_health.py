"""Add ticker_health table.

Tracks whether each ticker the user actually references (positions,
watchlist) can be resolved at the market-data provider. Exists because
MBGD and SYTA silently 404'd on every price refresh, signal scan,
insider fetch and dividend sync for weeks with nothing in the UI to
say so — they simply contributed zero everywhere.

consecutive_failures is the important column. yfinance 404s transiently
even for real, liquid S&P constituents (CTRA, BK, MMC and HOLX have all
done it mid-heatmap), so flagging on a single failure would cry wolf
constantly. Only a sustained run means the symbol is genuinely wrong.

last_ok_at separates "never resolved, probably a typo" from "worked
until recently, probably delisted or renamed" — different problems with
different fixes.

Revision ID: t1b2c3d4e5f6
Revises: s0a1b2c3d4e5
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op

revision = "t1b2c3d4e5f6"
down_revision = "s0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticker_health",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("resolves", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_ticker_health_resolves", "ticker_health", ["resolves"])


def downgrade() -> None:
    op.drop_index("ix_ticker_health_resolves", table_name="ticker_health")
    op.drop_table("ticker_health")
