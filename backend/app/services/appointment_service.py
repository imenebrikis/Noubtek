"""
The ONE place appointment status ever changes (see CLAUDE.md queue state machine).

transition() does, in a single transaction: (1) update status, (2) stamp/clear the
matching actual timestamp, (3) append an appointment_events row. appointment_events
is the future AI training set — it must never be skipped, so nothing else writes it.
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status as http
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import TenantContext, scoped
from app.models import Appointment, AppointmentEvent, AppointmentStatus

# from_status -> allowed to_status. Intake (scheduled->waiting) + forward + undo.
_ALLOWED: dict[AppointmentStatus, set[AppointmentStatus]] = {
    AppointmentStatus.scheduled: {AppointmentStatus.waiting},
    AppointmentStatus.waiting: {AppointmentStatus.in_session},
    AppointmentStatus.in_session: {AppointmentStatus.completed, AppointmentStatus.waiting},
    AppointmentStatus.completed: {AppointmentStatus.waiting},
}


class AppointmentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def transition(
        self, ctx: TenantContext, appointment_id: uuid.UUID, new_status: AppointmentStatus,
    ) -> Appointment:
        # scoped() enforces clinic_id — can't touch another clinic's appointment
        appt = (
            await self.session.execute(
                scoped(ctx, Appointment).where(Appointment.id == appointment_id)
            )
        ).scalar_one_or_none()
        if appt is None:
            raise HTTPException(http.HTTP_404_NOT_FOUND, "Appointment not found")

        old_status = appt.status
        if new_status not in _ALLOWED.get(old_status, set()):
            raise HTTPException(
                http.HTTP_409_CONFLICT,
                f"Illegal transition: {old_status.value} -> {new_status.value}",
            )

        now = datetime.now(timezone.utc)
        appt.status = new_status
        if new_status is AppointmentStatus.in_session:
            appt.session_started_at = now
        elif new_status is AppointmentStatus.completed:
            appt.completed_at = now
        elif new_status is AppointmentStatus.waiting:
            # undo: keep arrival, drop the forward timestamps we're reversing
            appt.arrived_at = appt.arrived_at or now
            appt.session_started_at = None
            appt.completed_at = None

        self.session.add(AppointmentEvent(
            clinic_id=ctx.clinic_id,
            appointment_id=appt.id,
            from_status=old_status,
            to_status=new_status,
            created_by=ctx.user_id,
        ))
        await self.session.commit()
        await self.session.refresh(appt)
        return appt
