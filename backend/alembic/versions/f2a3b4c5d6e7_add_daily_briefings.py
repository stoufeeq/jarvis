"""Add daily_briefings table

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-17

"""
import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_briefings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("briefing_date", sa.Date(), nullable=False),
        sa.Column("overall_sentiment", sa.Text(), nullable=False, server_default="neutral"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content_json", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_daily_briefings_user_id", "daily_briefings", ["user_id"])
    op.create_index("ix_daily_briefings_briefing_date", "daily_briefings", ["briefing_date"])
    op.create_unique_constraint(
        "uq_daily_briefings_user_date", "daily_briefings", ["user_id", "briefing_date"]
    )


def downgrade() -> None:
    op.drop_table("daily_briefings")
