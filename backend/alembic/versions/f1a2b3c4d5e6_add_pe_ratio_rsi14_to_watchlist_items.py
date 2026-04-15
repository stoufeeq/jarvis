"""add pe_ratio and rsi14 to watchlist_items

Revision ID: f1a2b3c4d5e6
Revises: d4e5f6a7b8c9
Create Date: 2026-04-15

"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watchlist_items", sa.Column("pe_ratio", sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column("watchlist_items", sa.Column("rsi14", sa.Numeric(precision=6, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column("watchlist_items", "rsi14")
    op.drop_column("watchlist_items", "pe_ratio")
