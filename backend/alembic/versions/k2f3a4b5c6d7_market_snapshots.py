"""Add market_snapshots table

Cached market context (indices, commodities, crypto, forex, sectors,
movers, headlines, upcoming macro) refreshed every 4h by a Celery task.
The AI advisor pulls the latest row to ground chat responses in fresh
data so Gemini doesn't fall back to its training-cutoff knowledge.

Revision ID: k2f3a4b5c6d7
Revises: j1e2f3a4b5c6
Create Date: 2026-06-11

"""
import sqlalchemy as sa
from alembic import op

revision = "k2f3a4b5c6d7"
down_revision = "j1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # The hot read is "newest snapshot" — index on captured_at DESC.
    op.create_index(
        "ix_market_snapshots_captured_at",
        "market_snapshots",
        ["captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_snapshots_captured_at", table_name="market_snapshots")
    op.drop_table("market_snapshots")
