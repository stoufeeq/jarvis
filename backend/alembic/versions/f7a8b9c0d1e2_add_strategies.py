"""Add strategies + strategy_trades tables for Auto Paper Trader

Strategy: user-defined rule for auto-executing paper trades on matching signals.
StrategyTrade: links a strategy-triggered buy/sell pair back to its origin signal
and tracks exit metadata.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-05-11

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


# Reference existing enums (created by earlier migrations)
signal_type_enum = postgresql.ENUM(
    "technical", "insider", "ai_news", "options_flow", "fundamental",
    "earnings_upcoming", "macro_event", "cross_impact",
    name="signal_type", create_type=False,
)
signal_direction_enum = postgresql.ENUM(
    "bullish", "bearish", "neutral",
    name="signal_direction", create_type=False,
)


def upgrade() -> None:
    # New enums specific to Strategy/StrategyTrade — created idempotently
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname='allocation_mode') THEN "
        "  CREATE TYPE allocation_mode AS ENUM ('fixed','percent'); "
        "END IF; "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname='strategy_trade_status') THEN "
        "  CREATE TYPE strategy_trade_status AS ENUM ('open','closed'); "
        "END IF; "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname='strategy_exit_reason') THEN "
        "  CREATE TYPE strategy_exit_reason AS ENUM "
        "    ('planned','opposite_signal','max_hold','panic_close','manual'); "
        "END IF; "
        "END $$;"
    )

    allocation_mode_enum = postgresql.ENUM(
        "fixed", "percent", name="allocation_mode", create_type=False,
    )
    strategy_trade_status_enum = postgresql.ENUM(
        "open", "closed", name="strategy_trade_status", create_type=False,
    )
    strategy_exit_reason_enum = postgresql.ENUM(
        "planned", "opposite_signal", "max_hold", "panic_close", "manual",
        name="strategy_exit_reason", create_type=False,
    )

    # ── strategies ─────────────────────────────────────────────────────
    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),

        sa.Column("signal_type", signal_type_enum, nullable=True),
        sa.Column("direction", signal_direction_enum, nullable=True),
        sa.Column("min_strength", sa.SmallInteger(), nullable=False, server_default="4"),
        sa.Column("tickers", sa.String(2000), nullable=True),

        sa.Column("allocation_mode", allocation_mode_enum, nullable=False, server_default="fixed"),
        sa.Column("allocation_value", sa.Numeric(18, 4), nullable=False, server_default="2000"),

        sa.Column("max_position_pct", sa.Numeric(8, 4), nullable=False, server_default="10"),
        sa.Column("min_cash_reserve", sa.Numeric(18, 4), nullable=False, server_default="5000"),

        sa.Column("min_hold_days", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("base_hold_days", sa.SmallInteger(), nullable=False, server_default="5"),
        sa.Column("max_hold_days", sa.SmallInteger(), nullable=False, server_default="30"),

        sa.Column("exit_on_opposite_signal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("extend_on_continuing_signal", sa.Boolean(), nullable=False, server_default=sa.true()),

        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),

        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategies_user_active", "strategies", ["user_id", "is_active"])

    # ── strategy_trades ────────────────────────────────────────────────
    op.create_table(
        "strategy_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ticker", sa.String(20), nullable=False, index=True),
        sa.Column("direction", signal_direction_enum, nullable=False),

        sa.Column("buy_trade_id", sa.Integer(), sa.ForeignKey("trades.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sell_trade_id", sa.Integer(), sa.ForeignKey("trades.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trigger_signal_id", sa.Integer(), sa.ForeignKey("signals.id", ondelete="SET NULL"), nullable=True),

        sa.Column("entry_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),

        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_exit_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("status", strategy_trade_status_enum, nullable=False, server_default="open"),
        sa.Column("exit_reason", strategy_exit_reason_enum, nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_trades_strategy_status", "strategy_trades", ["strategy_id", "status"])
    op.create_index("ix_strategy_trades_ticker_status", "strategy_trades", ["ticker", "status"])


def downgrade() -> None:
    op.drop_index("ix_strategy_trades_ticker_status", table_name="strategy_trades")
    op.drop_index("ix_strategy_trades_strategy_status", table_name="strategy_trades")
    op.drop_table("strategy_trades")
    op.drop_index("ix_strategies_user_active", table_name="strategies")
    op.drop_table("strategies")
    # Drop custom enums
    op.execute("DROP TYPE IF EXISTS strategy_exit_reason")
    op.execute("DROP TYPE IF EXISTS strategy_trade_status")
    op.execute("DROP TYPE IF EXISTS allocation_mode")
