"""Add password_reset_tokens table

Single-use, short-lived 6-digit codes used to reset a forgotten password.
Codes are hashed (sha256) before storage; attempt_count caps brute-force
against the 6-decimal-digit code space.

Revision ID: j1e2f3a4b5c6
Revises: i0d1e2f3a4b5
Create Date: 2026-06-04

"""
import sqlalchemy as sa
from alembic import op

revision = "j1e2f3a4b5c6"
down_revision = "i0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # SHA-256 of the plaintext 6-digit code. 64 hex chars.
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Set when the code is successfully consumed OR when attempt_count
        # hits MAX_ATTEMPTS (locked out).
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        # Counts wrong-code attempts against this token.
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_password_reset_tokens_user_used",
        "password_reset_tokens",
        ["user_id", "used_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_user_used",
                  table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
