"""add appointments.source (walk_in | scheduled)

Additive only. Existing rows default to 'scheduled' (they were pre-booked).

Revision ID: 7c1a9f0b2e10
Revises: 29ad646de5a8
Create Date: 2026-10-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7c1a9f0b2e10"
down_revision: Union[str, None] = "29ad646de5a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("source", sa.String(length=16), server_default="scheduled", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("appointments", "source")
