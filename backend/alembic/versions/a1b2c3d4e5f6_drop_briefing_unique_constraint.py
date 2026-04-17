"""Drop unique constraint on daily_briefings (user_id, briefing_date)

Allow multiple briefings per day — each regeneration creates a new row
rather than overwriting the existing one.

Revision ID: a1b2c3d4e5f6
Revises: f2a3b4c5d6e7
Create Date: 2026-04-17

"""
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_daily_briefings_user_date", "daily_briefings", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_daily_briefings_user_date", "daily_briefings", ["user_id", "briefing_date"]
    )
