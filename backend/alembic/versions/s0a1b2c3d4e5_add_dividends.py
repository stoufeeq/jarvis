"""Add dividends table.

Stores per-share dividend events scraped from yfinance, keyed by
(ticker, ex_date). Portfolio-level income is computed on read by
reconstructing shares-held-at-ex-date from the trade ledger — we
deliberately do NOT denormalise a per-portfolio amount, because
back-dated trade edits would silently leave it stale.

ex_date is the entitlement date (you must hold on/before it). pay_date
is when cash actually lands and is frequently unknown: yfinance's
historical `Ticker.dividends` series carries ex-dates only, and the
pay date is available just for the upcoming event via `Ticker.calendar`.
Hence nullable.

Revision ID: s0a1b2c3d4e5
Revises: r9f0a1b2c3d4
Create Date: 2026-09-05
"""
import sqlalchemy as sa
from alembic import op

revision = "s0a1b2c3d4e5"
down_revision = "r9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dividends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=True),
        sa.Column("amount_per_share", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("source", sa.String(20), nullable=False, server_default="yfinance"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # One row per ticker per ex-date. Re-syncing is then a cheap
        # upsert rather than a delete-and-rewrite.
        sa.UniqueConstraint("ticker", "ex_date", name="uq_dividend_ticker_ex_date"),
    )
    op.create_index("ix_dividends_ticker", "dividends", ["ticker"])
    op.create_index("ix_dividends_ex_date", "dividends", ["ex_date"])
    # Income queries filter by ticker set + date range together.
    op.create_index("ix_dividends_ticker_ex_date", "dividends", ["ticker", "ex_date"])


def downgrade() -> None:
    op.drop_index("ix_dividends_ticker_ex_date", table_name="dividends")
    op.drop_index("ix_dividends_ex_date", table_name="dividends")
    op.drop_index("ix_dividends_ticker", table_name="dividends")
    op.drop_table("dividends")
