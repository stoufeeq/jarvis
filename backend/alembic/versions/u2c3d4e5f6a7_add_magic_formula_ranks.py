"""Add magic_formula_ranks table.

Caches the Greenblatt Magic Formula screen. Computing it needs three
yfinance calls per ticker (info + financials + balance_sheet), so ~500
names takes 15-25 minutes — far too slow for a page load. A weekly
Celery job writes here and the page reads the cached snapshot.

Weekly is generous: the inputs are annual-report figures that change at
most quarterly.

Raw inputs (ebit, capital_employed, enterprise_value) are stored
alongside the derived ratios so a surprising rank can be traced back to
the numbers that produced it rather than being taken on faith.

Excluded rows are KEPT rather than dropped, with a reason, so the page
can explain why a name the user expected to see is absent — "where is
JPM" is the first question this screen provokes.

Revision ID: u2c3d4e5f6a7
Revises: t1b2c3d4e5f6
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op

revision = "u2c3d4e5f6a7"
down_revision = "t1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "magic_formula_ranks",
        sa.Column("ticker", sa.String(20), primary_key=True),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        # Raw inputs, kept for traceability
        sa.Column("ebit", sa.Numeric(24, 2), nullable=True),
        sa.Column("capital_employed", sa.Numeric(24, 2), nullable=True),
        sa.Column("enterprise_value", sa.Numeric(24, 2), nullable=True),
        # Derived ratios
        sa.Column("roce", sa.Numeric(12, 6), nullable=True),
        sa.Column("earnings_yield", sa.Numeric(12, 6), nullable=True),
        # Ranks — NULL for excluded rows
        sa.Column("rank_roce", sa.Integer(), nullable=True),
        sa.Column("rank_yield", sa.Integer(), nullable=True),
        sa.Column("combined_rank", sa.Integer(), nullable=True),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclusion_reason", sa.String(200), nullable=True),
        sa.Column("in_sp500", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_mf_combined_rank", "magic_formula_ranks", ["combined_rank"])
    op.create_index("ix_mf_excluded", "magic_formula_ranks", ["excluded"])


def downgrade() -> None:
    op.drop_index("ix_mf_excluded", table_name="magic_formula_ranks")
    op.drop_index("ix_mf_combined_rank", table_name="magic_formula_ranks")
    op.drop_table("magic_formula_ranks")
