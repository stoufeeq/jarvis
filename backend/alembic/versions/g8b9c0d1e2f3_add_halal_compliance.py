"""Add halal_compliance table for cached Sharia screening verdicts

Cache table populated by HalalScreenerService. One row per ticker;
status verdict + the financial ratios we screened on. 24h TTL enforced
in service layer.

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-05-17

"""
import sqlalchemy as sa
from alembic import op

revision = "g8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'halal_status') THEN "
        "  CREATE TYPE halal_status AS ENUM ('compliant', 'non_compliant', 'unknown'); "
        "END IF; "
        "END $$;"
    )

    from sqlalchemy.dialects.postgresql import ENUM
    halal_status = ENUM("compliant", "non_compliant", "unknown", name="halal_status", create_type=False)

    op.create_table(
        "halal_compliance",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("status", halal_status, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("quote_type", sa.String(20), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(200), nullable=True),
        sa.Column("debt_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("cash_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("halal_compliance")
    sa.Enum(name="halal_status").drop(op.get_bind(), checkfirst=True)
