"""Walk-in registration + public free-slot engine."""
import uuid
from datetime import date as date_cls, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status as http
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import TenantContext, require_role, scoped
from app.models import (
    Appointment, AppointmentStatus, Clinic, ClinicMember, ClinicRole, Patient, ProcedureType,
)
from app.routers.queue import DelayAlert, QueueItemOut, build_queue_response
from app.services.appointment_service import AppointmentService

router = APIRouter(tags=["appointments"])

SLOT_MINUTES = 20
DAY_START = time(9, 0)
DAY_END = time(17, 0)
# slots that still occupy the calendar (free = everything minus these)
_LIVE = (AppointmentStatus.scheduled, AppointmentStatus.waiting,
         AppointmentStatus.in_session, AppointmentStatus.completed)


# --- GET /api/v1/public/track/{tracking_token} (patient live tracker) --------
class PublicTrackOut(BaseModel):
    clinic_name: str
    patient_first_name: str
    status: AppointmentStatus
    position: int        # 1-based rank among waiting; 0 = in session; -1 = done/other
    people_ahead: int
    eta_minutes: int
    consult_time: str | None  # HH:MM (predicted if known, else scheduled)


@router.get("/api/v1/public/track/{tracking_token}", response_model=PublicTrackOut)
async def track(tracking_token: uuid.UUID, session: AsyncSession = Depends(get_session)):
    appt = (
        await session.execute(select(Appointment).where(Appointment.tracking_token == tracking_token))
    ).scalar_one_or_none()
    if appt is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Lien de suivi introuvable")

    clinic = (await session.execute(select(Clinic).where(Clinic.id == appt.clinic_id))).scalar_one()
    patient = (await session.execute(select(Patient).where(Patient.id == appt.patient_id))).scalar_one()

    # rank among this doctor's waiting queue today (same ordering as the staff board)
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    waiting_ids = (
        await session.execute(
            select(Appointment.id)
            .where(
                Appointment.clinic_id == appt.clinic_id,
                Appointment.doctor_member_id == appt.doctor_member_id,
                Appointment.status == AppointmentStatus.waiting,
                Appointment.scheduled_start >= day,
                Appointment.scheduled_start < day + timedelta(days=1),
            )
            .order_by(func.coalesce(Appointment.arrived_at, Appointment.scheduled_start))
        )
    ).scalars().all()

    if appt.status is AppointmentStatus.waiting and appt.id in waiting_ids:
        position = waiting_ids.index(appt.id) + 1
        people_ahead = position - 1
    elif appt.status is AppointmentStatus.in_session:
        position, people_ahead = 0, 0
    else:
        position, people_ahead = -1, 0

    now = datetime.now(timezone.utc)
    if appt.predicted_start:
        eta = max(0, round((appt.predicted_start - now).total_seconds() / 60))
    else:
        eta = people_ahead * 15  # ponytail: same per-patient pacing as the delay heuristic
    consult_dt = appt.predicted_start or appt.scheduled_start

    return PublicTrackOut(
        clinic_name=clinic.name,
        patient_first_name=patient.full_name.split(" ")[0],
        status=appt.status,
        position=position,
        people_ahead=people_ahead,
        eta_minutes=eta,
        consult_time=consult_dt.astimezone(timezone.utc).strftime("%H:%M"),
    )


def _all_slots() -> list[str]:
    out, t = [], datetime.combine(date_cls.min, DAY_START)
    end = datetime.combine(date_cls.min, DAY_END)
    while t < end:
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=SLOT_MINUTES)
    return out


# --- GET /api/v1/public/slots ------------------------------------------------
class SlotsOut(BaseModel):
    doctor_id: uuid.UUID
    date: date_cls
    slot_minutes: int
    free_slots: list[str]


@router.get("/api/v1/public/slots", response_model=SlotsOut)
async def free_slots(
    doctor_id: uuid.UUID = Query(...),
    date: date_cls = Query(...),
    session: AsyncSession = Depends(get_session),
):
    # public endpoint: derive clinic_id from the doctor so tenancy still holds
    doctor = (
        await session.execute(select(ClinicMember).where(ClinicMember.id == doctor_id))
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Doctor not found")

    day = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    booked = (
        await session.execute(
            select(Appointment.scheduled_start).where(
                Appointment.clinic_id == doctor.clinic_id,
                Appointment.doctor_member_id == doctor_id,
                Appointment.scheduled_start >= day,
                Appointment.scheduled_start < day + timedelta(days=1),
                Appointment.status.in_(_LIVE),
            )
        )
    ).scalars().all()
    taken = {s.astimezone(timezone.utc).strftime("%H:%M") for s in booked}
    return SlotsOut(
        doctor_id=doctor_id, date=date, slot_minutes=SLOT_MINUTES,
        free_slots=[s for s in _all_slots() if s not in taken],
    )


# --- POST /api/v1/queue/walk-in ----------------------------------------------
class WalkInIn(BaseModel):
    full_name: str = Field(min_length=1)
    phone: str | None = None
    procedure_type: str | None = None
    doctor_id: uuid.UUID
    is_scheduled: bool = False
    slot_time: str | None = None  # "HH:MM" (today) or full ISO datetime; required if is_scheduled


class WalkInOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    tracking_token: uuid.UUID
    status: AppointmentStatus
    source: str
    scheduled_start: datetime


class WalkInResponse(BaseModel):
    created: WalkInOut
    queue: list[QueueItemOut]      # recalculated board
    delay_alert: DelayAlert        # recalculated delay + FR apology


def _parse_slot(slot_time: str) -> datetime:
    try:
        dt = datetime.fromisoformat(slot_time)
    except ValueError:
        hh, mm = (int(x) for x in slot_time.split(":"))
        today = datetime.now(timezone.utc)
        dt = today.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/api/v1/queue/walk-in", response_model=WalkInResponse, status_code=http.HTTP_201_CREATED)
async def register_walk_in(
    body: WalkInIn,
    ctx: TenantContext = Depends(require_role(ClinicRole.admin, ClinicRole.receptionist)),
    session: AsyncSession = Depends(get_session),
):
    # doctor must belong to this clinic (scoped enforces clinic_id)
    doctor = (
        await session.execute(scoped(ctx, ClinicMember).where(ClinicMember.id == body.doctor_id))
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Doctor not found in this clinic")

    if body.is_scheduled and not body.slot_time:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_ENTITY, "slot_time required when is_scheduled")

    # find-or-create Patient by phone (within clinic)
    patient = None
    if body.phone:
        patient = (
            await session.execute(scoped(ctx, Patient).where(Patient.phone == body.phone))
        ).scalars().first()
    if patient is None:
        patient = Patient(clinic_id=ctx.clinic_id, full_name=body.full_name, phone=body.phone)
        session.add(patient)
        await session.flush()

    # find-or-create ProcedureType by name (optional)
    duration, procedure_id = SLOT_MINUTES, None
    if body.procedure_type:
        proc = (
            await session.execute(
                scoped(ctx, ProcedureType).where(ProcedureType.name == body.procedure_type)
            )
        ).scalars().first()
        if proc is None:
            proc = ProcedureType(
                clinic_id=ctx.clinic_id, name=body.procedure_type,
                default_duration_minutes=SLOT_MINUTES,
            )
            session.add(proc)
            await session.flush()
        procedure_id, duration = proc.id, proc.default_duration_minutes

    scheduled_start = _parse_slot(body.slot_time) if body.is_scheduled else datetime.now(timezone.utc)

    appt = Appointment(
        clinic_id=ctx.clinic_id,              # server-side, never trust client
        patient_id=patient.id,
        doctor_member_id=body.doctor_id,
        procedure_type_id=procedure_id,
        scheduled_start=scheduled_start,
        scheduled_duration_minutes=duration,
        source="scheduled" if body.is_scheduled else "walk_in",
        # status defaults to 'scheduled'; tracking_token auto-generated (uuid4)
    )
    session.add(appt)
    await session.flush()  # assign id before the transition reads it

    # route the intake event through the ONE writer of appointment_events
    appt = await AppointmentService(session).transition(ctx, appt.id, AppointmentStatus.waiting)
    created = WalkInOut(
        id=appt.id, patient_id=patient.id, tracking_token=appt.tracking_token,
        status=appt.status, source=appt.source, scheduled_start=appt.scheduled_start,
    )
    # recalculate delay for this doctor and return the refreshed board + alert
    board = await build_queue_response(ctx, session, body.doctor_id)
    return WalkInResponse(created=created, queue=board.queue, delay_alert=board.delay_alert)
