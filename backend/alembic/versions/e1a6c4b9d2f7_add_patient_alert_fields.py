"""add appointments.notified_start / alert_shift_minutes / alert_at (proactive alerts)

Additive, nullable. Existing rows get a baseline on the next queue recalculation.

Revision ID: e1a6c4b9d2f7
Revises: d7f3a9c2e8b1
Create Date: 2026-10-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a6c4b9d2f7"
down_revision: Union[str, None] = "d7f3a9c2e8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("notified_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appointments", sa.Column("alert_shift_minutes", sa.Integer(), nullable=True))
    op.add_column("appointments", sa.Column("alert_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("appointments", "alert_at")
    op.drop_column("appointments", "alert_shift_minutes")
    op.drop_column("appointments", "notified_start")
