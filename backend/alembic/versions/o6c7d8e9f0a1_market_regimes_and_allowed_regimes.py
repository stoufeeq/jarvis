"""Regime detection: market_regimes daily table + strategies.allowed_regimes.

The RegimeService classifies each trading day into one of four buckets
based on SPX-vs-200SMA (trend) and VIX level (vol):

  bull_low_vol   SPX > 200SMA  and  VIX < 20
  bull_high_vol  SPX > 200SMA  and  VIX >= 20
  bear_low_vol   SPX < 200SMA  and  VIX < 20
  bear_high_vol  SPX < 200SMA  and  VIX >= 20

Storing the per-day classification lets:
  - auto_trader gate signals on the current day's regime cheaply
  - historical trades be joined back to the regime they were entered in
    for the "which strategy works in which regime" backtest.

Revision ID: o6c7d8e9f0a1
Revises: n5b6c7d8e9f0
Create Date: 2026-07-27
"""
import sqlalchemy as sa
from alembic import op


revision = "o6c7d8e9f0a1"
down_revision = "n5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_regimes",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("regime", sa.String(32), nullable=False),
        sa.Column("spx_close", sa.Numeric(12, 4), nullable=True),
        sa.Column("spx_sma200", sa.Numeric(12, 4), nullable=True),
        sa.Column("vix_close", sa.Numeric(8, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_market_regimes_date", "market_regimes", ["date"])

    # Comma-separated list of regime names the strategy is allowed to
    # trade in. NULL = allow any regime (legacy behaviour). Stored as
    # comma-separated string for parity with `tickers`/`excluded_tickers`
    # rather than a JSON list — simpler to query and edit in the UI.
    op.add_column(
        "strategies",
        sa.Column("allowed_regimes", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategies", "allowed_regimes")
    op.drop_index("ix_market_regimes_date", table_name="market_regimes")
    op.drop_table("market_regimes")
