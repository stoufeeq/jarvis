"""add ticker_profiles

Revision ID: v3d4e5f6a7b8
Revises: u2c3d4e5f6a7
Create Date: 2026-09-27

Reference data cache (sector / industry / country / quote type) per
ticker, backing the portfolio Allocation tab.
"""

import sqlalchemy as sa
from alembic import op

revision = "v3d4e5f6a7b8"
down_revision = "u2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticker_profiles",
        sa.Column("ticker", sa.String(length=20), primary_key=True),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("industry", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("quote_type", sa.String(length=20), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ticker_profiles_sector", "ticker_profiles", ["sector"]
    )


def downgrade() -> None:
    op.drop_index("ix_ticker_profiles_sector", table_name="ticker_profiles")
    op.drop_table("ticker_profiles")
