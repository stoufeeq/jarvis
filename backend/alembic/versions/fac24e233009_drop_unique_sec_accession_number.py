"""drop_unique_sec_accession_number

Revision ID: fac24e233009
Revises: 847ff64d5995
Create Date: 2026-04-15 00:59:15.157523

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'fac24e233009'
down_revision: Union[str, None] = '847ff64d5995'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("insider_trades_sec_accession_number_key", "insider_trades", type_="unique")
    op.create_index("ix_insider_trades_sec_accession_number", "insider_trades", ["sec_accession_number"])


def downgrade() -> None:
    op.drop_index("ix_insider_trades_sec_accession_number", "insider_trades")
    op.create_unique_constraint("insider_trades_sec_accession_number_key", "insider_trades", ["sec_accession_number"])
