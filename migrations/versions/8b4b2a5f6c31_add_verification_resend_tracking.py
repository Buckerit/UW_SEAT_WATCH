"""add verification resend tracking

Revision ID: 8b4b2a5f6c31
Revises: c31b8a7f4d2a
Create Date: 2026-08-20 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "8b4b2a5f6c31"
down_revision: Union[str, None] = "c31b8a7f4d2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("watches")
    }

    if "verification_resend_count" not in existing_columns:
        op.add_column(
            "watches",
            sa.Column(
                "verification_resend_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )

    if "verification_email_sent_at" not in existing_columns:
        op.add_column(
            "watches",
            sa.Column(
                "verification_email_sent_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("watches", "verification_email_sent_at")
    op.drop_column("watches", "verification_resend_count")
