"""Add calendar_events table for earnings + ex-dividend dates

Caches yfinance Ticker.calendar / Ticker.info data for upcoming earnings
and ex-dividend events. Refreshed daily by a Celery task. Macro events
are queried directly from the signals table (signal_type=macro_event)
and not duplicated here.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-05-10

"""
import sqlalchemy as sa
from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent enum creation — survives partial-rerun after a failed migration
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'calendar_event_type') THEN "
        "  CREATE TYPE calendar_event_type AS ENUM ('earnings', 'ex_dividend'); "
        "END IF; "
        "END $$;"
    )

    from sqlalchemy.dialects.postgresql import ENUM
    event_type = ENUM("earnings", "ex_dividend", name="calendar_event_type", create_type=False)

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False, index=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ticker", "event_type", "event_date", name="uq_calendar_event"),
    )
    op.create_index("ix_calendar_events_ticker_date", "calendar_events", ["ticker", "event_date"])
    op.create_index("ix_calendar_events_type_date", "calendar_events", ["event_type", "event_date"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_type_date", table_name="calendar_events")
    op.drop_index("ix_calendar_events_ticker_date", table_name="calendar_events")
    op.drop_table("calendar_events")
    sa.Enum(name="calendar_event_type").drop(op.get_bind(), checkfirst=True)
