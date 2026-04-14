"""add_acknowledged_at_to_alerts

Revision ID: 847ff64d5995
Revises: dd2192779795
Create Date: 2026-04-15 00:15:14.913086

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '847ff64d5995'
down_revision: Union[str, None] = 'dd2192779795'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("alerts", "acknowledged_at")
