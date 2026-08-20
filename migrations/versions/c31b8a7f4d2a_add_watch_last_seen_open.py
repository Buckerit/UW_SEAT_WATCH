"""add watch last seen open

Revision ID: c31b8a7f4d2a
Revises: 8956ba165afb
Create Date: 2026-08-18 18:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c31b8a7f4d2a"
down_revision: Union[str, Sequence[str], None] = "8956ba165afb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "watches",
        sa.Column(
            "last_seen_open",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("watches", "last_seen_open")
