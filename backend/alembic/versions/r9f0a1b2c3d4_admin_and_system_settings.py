"""Add users.is_admin + system_settings key-value table.

Groundwork for admin-only capabilities (starting with the runtime LLM
model-selection UI). Also seeds the founding admin (stoufeeq@gmail.com)
so an existing self-hosted instance doesn't have to touch the DB by hand.

  users.is_admin          BOOLEAN NOT NULL DEFAULT false
  system_settings         (key text PK, value text NULL,
                           updated_by int FK users(id) NULL,
                           updated_at timestamptz)

`system_settings` is intentionally schemaless key-value — every setting
is JSON-serialisable free text. Callers own the encoding (usually JSON).
Small table, low write volume, values fetched on-demand (no caching
required for the initial LLM use case).

Revision ID: r9f0a1b2c3d4
Revises: q8e9f0a1b2c3
Create Date: 2026-08-24
"""
import sqlalchemy as sa
from alembic import op

revision = "r9f0a1b2c3d4"
down_revision = "q8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Column is added NOT NULL with a server-side default so existing rows
    # get `false` without a two-step migration.
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Seed the founding admin. Safe on fresh installs (WHERE matches nothing);
    # idempotent on re-run (UPDATE with same value is a no-op).
    op.execute("UPDATE users SET is_admin = true WHERE email = 'stoufeeq@gmail.com'")


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_column("users", "is_admin")
