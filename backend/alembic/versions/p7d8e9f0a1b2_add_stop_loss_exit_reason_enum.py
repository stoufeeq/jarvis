"""Add 'stop_loss' to the strategy_exit_reason Postgres enum.

The Python StrategyExitReason enum grew a `stop_loss` member alongside
the stop-loss feature (migration n5b6c7d8e9f0) but the Postgres ENUM
type didn't get the corresponding value, so every stop-loss close
transaction was rolling back with:

    invalid input value for enum strategy_exit_reason: "stop_loss"

That silently kept 6+ positions open past their -8% threshold for
5 days. This migration adds the missing value with ALTER TYPE.

Revision ID: p7d8e9f0a1b2
Revises: o6c7d8e9f0a1
Create Date: 2026-07-27
"""
from alembic import op


revision = "p7d8e9f0a1b2"
down_revision = "o6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on older
    # Postgres; wrap in autocommit so alembic doesn't wrap it.
    # IF NOT EXISTS is a no-op if the value is somehow already present
    # (defensive — this migration is a one-shot fix).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE strategy_exit_reason ADD VALUE IF NOT EXISTS 'stop_loss'")


def downgrade() -> None:
    # Postgres doesn't support removing enum values. Downgrade is a no-op —
    # the value would just be dead on rollback, which is harmless.
    pass
