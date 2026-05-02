"""Add telegram_chat_id to users

Allows pushing triggered alerts and briefing summaries to a user's
Telegram via the configured Jarvis bot.

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-05-02

"""
import sqlalchemy as sa
from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_chat_id", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "telegram_chat_id")
