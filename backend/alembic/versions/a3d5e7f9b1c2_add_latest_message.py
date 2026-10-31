"""add appointments.latest_message / latest_message_at (staff broadcast on the tracker)

Additive, nullable.

Revision ID: a3d5e7f9b1c2
Revises: f7c647d2c94e
Create Date: 2026-10-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d5e7f9b1c2"
down_revision: Union[str, None] = "f7c647d2c94e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("latest_message", sa.Text(), nullable=True))
    op.add_column("appointments", sa.Column("latest_message_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("appointments", "latest_message_at")
    op.drop_column("appointments", "latest_message")
