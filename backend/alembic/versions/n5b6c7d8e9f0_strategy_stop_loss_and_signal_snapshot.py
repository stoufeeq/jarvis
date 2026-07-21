"""Snapshot trigger signal onto strategy_trades + add stop_loss_pct
and excluded_tickers to strategies.

Why:
  - trigger_signal_id on strategy_trades gets NULLed whenever the signal
    scan runs (signals are deleted and re-written every 15 min), so
    post-hoc analysis can never see which signal type / strength drove
    a trade. Snapshot the fields at trade creation.
  - Add stop_loss_pct so auto_trader can cut losses at a threshold
    instead of holding to max_hold_days.
  - Add excluded_tickers so a strategy can blacklist known losers.

Revision ID: n5b6c7d8e9f0
Revises: m4a5b6c7d8e9
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op

revision = "n5b6c7d8e9f0"
down_revision = "m4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # strategy_trades: snapshot fields (nullable — historic rows can't
    # be filled with signal detail we no longer have).
    op.add_column(
        "strategy_trades",
        sa.Column(
            "trigger_signal_type",
            sa.Enum(
                "technical", "insider", "ai_news", "options_flow",
                "fundamental", "earnings_upcoming", "macro_event",
                "cross_impact",
                name="signal_type",
                # The Enum type is created by the initial signals migration.
                # create_type=False prevents Postgres from erroring on
                # "type already exists" when we reference it here.
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "strategy_trades",
        sa.Column("trigger_signal_strength", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "strategy_trades",
        sa.Column("trigger_signal_rationale", sa.Text(), nullable=True),
    )

    # Best-effort backfill: for open/closed trades whose trigger_signal_id
    # still points to a live signal row (unlikely for old ones since the
    # 15-min scan wipes signals), copy the fields across. Everything else
    # stays NULL and analyses will show "?".
    op.execute("""
        UPDATE strategy_trades st
        SET trigger_signal_type      = s.signal_type,
            trigger_signal_strength  = s.strength,
            trigger_signal_rationale = s.rationale
        FROM signals s
        WHERE st.trigger_signal_id = s.id
    """)

    # strategies: risk knobs. Both nullable so existing strategies keep
    # their current behaviour until the user opts in via the UI.
    op.add_column(
        "strategies",
        sa.Column("stop_loss_pct", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "strategies",
        sa.Column("excluded_tickers", sa.String(2000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategies", "excluded_tickers")
    op.drop_column("strategies", "stop_loss_pct")
    op.drop_column("strategy_trades", "trigger_signal_rationale")
    op.drop_column("strategy_trades", "trigger_signal_strength")
    op.drop_column("strategy_trades", "trigger_signal_type")
