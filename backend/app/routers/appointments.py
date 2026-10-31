"""Walk-in registration + public free-slot engine."""
import uuid
from datetime import date as date_cls, datetime, time, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status as http
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import TenantContext, require_role, scoped
from app.localtime import day_bounds, hhmm, local_at, parse_local, to_local, today
from app.models import (
    PROCEDURE_MINUTES, Appointment, AppointmentStatus, Clinic, ClinicMember, ClinicRole, Patient,
    ProcedureCategory, ProcedureType, is_slot_conflict,
)
from app.routers.queue import DelayAlert, QueueItemOut, build_queue_response
from app.services.ai_delay import delay_insight, estimate_queue
from app.services.appointment_service import AppointmentService
from app.services.patient_alerts import alert_message, apply_alerts, pending_alerts, send_alert_sms

router = APIRouter(tags=["appointments"])

SLOT_MINUTES = 20
DAY_START = time(9, 0)
DAY_END = time(17, 0)
# slots that still occupy the calendar (free = everything minus these)
_LIVE = (AppointmentStatus.scheduled, AppointmentStatus.waiting,
         AppointmentStatus.in_session, AppointmentStatus.completed)


# --- GET /api/v1/public/track/{tracking_token} (patient live tracker) --------
class TrackAlertOut(BaseModel):
    message: str           # French, ready to display
    shift_minutes: int     # > 0 later, < 0 earlier than previously announced
    at: str                # HH:MM the alert was raised
    source: str            # 'gemini' | 'heuristic'


class PublicTrackOut(BaseModel):
    clinic_name: str
    doctor_id: uuid.UUID | None  # public booking key (same id as /book?doctor=…) — for re-booking
    patient_first_name: str
    status: AppointmentStatus
    position: int        # 1-based rank among waiting; 0 = in session; -1 = done/other
    people_ahead: int
    eta_minutes: int
    consult_time: str | None  # HH:MM (live prediction while waiting, else scheduled)
    delay_minutes: int        # doctor's current lateness (local heuristic)
    ai_message: str           # French message for patients (Gemini, or default text)
    ai_source: str            # 'gemini' | 'heuristic'
    alert: TrackAlertOut | None = None  # proactive alert while waiting
    latest_message: str | None = None   # last staff broadcast (dashboard "SMS de retard"), while waiting
    latest_message_at: datetime | None = None  # clinic-local; changes on every new broadcast


@router.get("/api/v1/public/track/{tracking_token}", response_model=PublicTrackOut)
async def track(
    tracking_token: uuid.UUID, background: BackgroundTasks, session: AsyncSession = Depends(get_session),
):
    appt = (
        await session.execute(select(Appointment).where(Appointment.tracking_token == tracking_token))
    ).scalar_one_or_none()
    if appt is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Lien de suivi introuvable")

    clinic = (await session.execute(select(Clinic).where(Clinic.id == appt.clinic_id))).scalar_one()
    patient = (await session.execute(select(Patient).where(Patient.id == appt.patient_id))).scalar_one()
    doctor = (
        await session.execute(select(ClinicMember).where(
            ClinicMember.clinic_id == appt.clinic_id, ClinicMember.id == appt.doctor_member_id,
        ))
    ).scalar_one_or_none()

    # this doctor's whole day: the estimator needs the in-session row (overrun) + the waiting line
    day_start, day_end = day_bounds(today())
    day_appts = (
        await session.execute(
            select(Appointment).where(
                Appointment.clinic_id == appt.clinic_id,
                Appointment.doctor_member_id.is_not_distinct_from(appt.doctor_member_id),
                Appointment.scheduled_start >= day_start,
                Appointment.scheduled_start < day_end,
            )
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    est = estimate_queue(day_appts, now)
    # polls also catch drift with no staff action (e.g. a session overrunning)
    # ponytail: write on GET; move to a periodic job once we have a scheduler
    fired = apply_alerts(day_appts, est, now)
    await session.commit()
    if fired:
        contacts = {
            pid: (name, phone) for pid, name, phone in (await session.execute(
                select(Patient.id, Patient.full_name, Patient.phone).where(
                    Patient.clinic_id == appt.clinic_id, Patient.id.in_([a.patient_id for a in fired]),
                )
            )).all()
        }
        background.add_task(send_alert_sms, pending_alerts(fired, contacts))
    insight = await delay_insight(
        (appt.clinic_id, appt.doctor_member_id), est, (doctor and doctor.display_name) or "Votre médecin",
    )

    predicted = est.predicted.get(appt.id)
    if predicted is not None:
        position = sorted(est.predicted, key=est.predicted.get).index(appt.id) + 1
        people_ahead = position - 1
        eta = max(0, round((predicted - now).total_seconds() / 60))
    elif appt.status is AppointmentStatus.in_session:
        position, people_ahead, eta = 0, 0, 0
    else:
        position, people_ahead, eta = -1, 0, 0
    consult_dt = predicted or appt.scheduled_start

    alert = None
    if predicted is not None and appt.alert_at and appt.alert_shift_minutes:
        text = await alert_message(appt.alert_shift_minutes, appt.notified_start)
        alert = TrackAlertOut(
            message=text.message, shift_minutes=appt.alert_shift_minutes,
            at=hhmm(appt.alert_at), source=text.source,
        )

    # a delay broadcast only matters while they're still in the line
    show_message = bool(appt.status is AppointmentStatus.waiting and appt.latest_message and appt.latest_message_at)

    return PublicTrackOut(
        clinic_name=clinic.name,
        doctor_id=appt.doctor_member_id,
        patient_first_name=patient.full_name.split(" ")[0],
        status=appt.status,
        position=position,
        people_ahead=people_ahead,
        eta_minutes=eta,
        consult_time=hhmm(consult_dt),
        delay_minutes=est.delay_minutes,
        ai_message=insight.apology_message,
        ai_source=insight.source,
        alert=alert,
        latest_message=appt.latest_message if show_message else None,
        latest_message_at=to_local(appt.latest_message_at) if show_message else None,
    )


def _all_slots() -> list[str]:
    out, t = [], datetime.combine(date_cls.min, DAY_START)
    end = datetime.combine(date_cls.min, DAY_END)
    while t < end:
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=SLOT_MINUTES)
    return out


async def taken_slots(session: AsyncSession, doctor: ClinicMember, day: date_cls) -> set[str]:
    """Clinic-local "HH:MM" starts already held on `day` for this doctor."""
    start, end = day_bounds(day)
    booked = (
        await session.execute(
            select(Appointment.scheduled_start).where(
                Appointment.clinic_id == doctor.clinic_id,
                Appointment.doctor_member_id == doctor.id,
                Appointment.scheduled_start >= start,
                Appointment.scheduled_start < end,
                Appointment.status.in_(_LIVE),
            )
        )
    ).scalars().all()
    return {hhmm(s) for s in booked}


# --- GET /api/v1/public/slots ------------------------------------------------
class SlotsOut(BaseModel):
    doctor_id: uuid.UUID
    doctor_name: str | None   # public profile for the booking page header
    specialty: str | None
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
        await session.execute(select(ClinicMember).where(
            ClinicMember.id == doctor_id, ClinicMember.role == ClinicRole.doctor,
        ))
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Doctor not found")

    taken = await taken_slots(session, doctor, date)
    return SlotsOut(
        doctor_id=doctor_id, doctor_name=doctor.display_name, specialty=doctor.specialty,
        date=date, slot_minutes=SLOT_MINUTES,
        free_slots=[s for s in _all_slots() if s not in taken],
    )


# --- POST /api/v1/queue/walk-in ----------------------------------------------
class WalkInIn(BaseModel):
    full_name: str = Field(min_length=1)
    phone: str | None = None
    procedure_type: str | None = None   # free-text name, clinic's own catalogue
    procedure_category: ProcedureCategory = ProcedureCategory.routine  # drives the wait estimate
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
    """"HH:MM" (clinic-local, today) or ISO datetime (naive = clinic-local) -> UTC."""
    try:
        return parse_local(datetime.fromisoformat(slot_time))
    except ValueError:
        hh, mm = (int(x) for x in slot_time.split(":"))
        return local_at(today(), time(hh, mm))


@router.post("/api/v1/queue/walk-in", response_model=WalkInResponse, status_code=http.HTTP_201_CREATED)
async def register_walk_in(
    body: WalkInIn,
    background: BackgroundTasks,
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
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "slot_time required when is_scheduled")

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

    # expected chair time comes from the category; ProcedureType is just the clinic's label
    duration, procedure_id = PROCEDURE_MINUTES[body.procedure_category], None
    if body.procedure_type:
        proc = (
            await session.execute(
                scoped(ctx, ProcedureType).where(ProcedureType.name == body.procedure_type)
            )
        ).scalars().first()
        if proc is None:
            proc = ProcedureType(
                clinic_id=ctx.clinic_id, name=body.procedure_type,
                default_duration_minutes=duration,
            )
            session.add(proc)
            await session.flush()
        procedure_id = proc.id

    scheduled_start = _parse_slot(body.slot_time) if body.is_scheduled else datetime.now(timezone.utc)

    appt = Appointment(
        clinic_id=ctx.clinic_id,              # server-side, never trust client
        patient_id=patient.id,
        doctor_member_id=body.doctor_id,
        procedure_type_id=procedure_id,
        procedure_category=body.procedure_category,
        scheduled_start=scheduled_start,
        scheduled_duration_minutes=duration,
        source="scheduled" if body.is_scheduled else "walk_in",
        # status defaults to 'scheduled'; tracking_token auto-generated (uuid4)
    )
    session.add(appt)
    try:
        await session.flush()  # assign id before the transition reads it
    except IntegrityError as exc:
        await session.rollback()
        if is_slot_conflict(exc):  # scheduled slot already held for this doctor
            raise HTTPException(http.HTTP_409_CONFLICT, "Ce créneau est déjà pris pour ce médecin.") from exc
        raise

    # route the intake event through the ONE writer of appointment_events
    appt = await AppointmentService(session).transition(ctx, appt.id, AppointmentStatus.waiting)
    created = WalkInOut(
        id=appt.id, patient_id=patient.id, tracking_token=appt.tracking_token,
        status=appt.status, source=appt.source, scheduled_start=appt.scheduled_start,
    )
    # recalculate delay for this doctor and return the refreshed board + alert
    board = await build_queue_response(ctx, session, body.doctor_id, background)
    return WalkInResponse(created=created, queue=board.queue, delay_alert=board.delay_alert)
