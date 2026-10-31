"""add appointments.first_predicted_start (accuracy feedback loop)

Additive, nullable. Existing rows stay NULL and are simply left out of accuracy stats.

Revision ID: d7f3a9c2e8b1
Revises: b4e2d8a1c3f5
Create Date: 2026-10-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7f3a9c2e8b1"
down_revision: Union[str, None] = "b4e2d8a1c3f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("first_predicted_start", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("appointments", "first_predicted_start")
