"""add appointments.procedure_category (routine | extended | procedure_minor | procedure_complex)

Additive only. Existing rows default to 'routine' (15 min).

Revision ID: b4e2d8a1c3f5
Revises: 7c1a9f0b2e10
Create Date: 2026-10-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e2d8a1c3f5"
down_revision: Union[str, None] = "7c1a9f0b2e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("procedure_category", sa.String(length=24), server_default="routine", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("appointments", "procedure_category")
