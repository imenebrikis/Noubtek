"""unique live slot per doctor (closes the double-booking race)

Partial unique index on appointments(doctor_member_id, scheduled_start) for live statuses
only — a cancelled / no-show appointment frees its slot. Fails if live duplicates already
exist; check with GROUP BY ... HAVING count(*) > 1 first (Neon had none on 2026-10-31).

Revision ID: f7c647d2c94e
Revises: e1a6c4b9d2f7
Create Date: 2026-10-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7c647d2c94e"
down_revision: Union[str, None] = "e1a6c4b9d2f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LIVE = sa.text("status IN ('scheduled', 'waiting', 'in_session', 'completed')")


def upgrade() -> None:
    op.create_index(
        "uq_appointments_doctor_slot_live", "appointments", ["doctor_member_id", "scheduled_start"],
        unique=True, postgresql_where=LIVE,
    )


def downgrade() -> None:
    op.drop_index("uq_appointments_doctor_slot_live", table_name="appointments")
