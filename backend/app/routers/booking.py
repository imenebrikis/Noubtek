"""Public online booking — no auth. Tenancy comes from the doctor: clinic_id is always
derived server-side from doctor_id, never accepted from the client."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status as http
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.localtime import day_bounds, hhmm, parse_local, to_local, today
from app.models import (
    PROCEDURE_MINUTES, Appointment, AppointmentStatus, ClinicMember, ClinicRole, Patient, ProcedureCategory,
    is_slot_conflict,
)
from app.routers.appointments import _all_slots, taken_slots
from app.services.ai_delay import estimate_queue, queue_order
from app.services.notifications import notifier
from app.services.patient_alerts import tracker_url

router = APIRouter(prefix="/api/v1/booking", tags=["booking"])

MAX_DAYS_AHEAD = 60
PHONE_PATTERN = r"^\+?[0-9][0-9 .\-]{7,18}[0-9]$"   # 06 61 23 45 67 / +213 661 23 45 67
SLOT_TAKEN = "Ce créneau vient d'être réservé, veuillez en choisir un autre."


class BookingIn(BaseModel):
    patient_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(pattern=PHONE_PATTERN)
    email: EmailStr | None = None
    doctor_id: uuid.UUID
    procedure_category: ProcedureCategory = ProcedureCategory.routine
    booked_time: datetime  # ISO 8601; without an offset it's the clinic's wall clock


class BookingOut(BaseModel):
    appointment_id: uuid.UUID
    tracking_token: uuid.UUID
    tracker_url: str
    patient_name: str
    doctor_name: str
    procedure_category: ProcedureCategory
    expected_duration_minutes: int
    booked_time: datetime        # clinic-local, with offset
    estimated_start: datetime    # clinic-local; later than booked_time when today's line runs late
    queue_position: int          # 1-based place in the doctor's line that day
    status: AppointmentStatus


def initial_eta(day_appts: list[Appointment], booked: datetime, category: ProcedureCategory,
                now: datetime) -> datetime:
    """When the patient will really go in: their slot, or later if today's line is running behind.
    Probes the live engine with a stand-in that 'arrives' at the slot (never added to the DB).
    Other days have no waiting line, so this is simply the slot."""
    probe = Appointment(id=uuid.uuid4(), status=AppointmentStatus.waiting, procedure_category=category,
                        scheduled_start=booked, arrived_at=booked)
    # ponytail: booked-but-not-arrived patients don't hold the line yet (no-show handling is separate)
    return max(booked, estimate_queue([*day_appts, probe], now).predicted[probe.id])


def queue_position(day_appts: list[Appointment], booked: datetime) -> int:
    """1 + patients not yet seen that day whose turn comes before this slot (by arrival/slot)."""
    pending = (AppointmentStatus.scheduled, AppointmentStatus.waiting)
    return 1 + sum(1 for a in day_appts if a.status in pending and queue_order(a) < booked)


def confirmation_text(first_name: str, doctor: str, booked: datetime, eta: datetime, url: str) -> str:
    text = (f"Bonjour {first_name}, votre rendez-vous avec {doctor} est confirmé "
            f"le {to_local(booked).strftime('%d/%m à %H:%M')}.")
    if eta - booked >= timedelta(minutes=5):
        text += f" La file a du retard : passage estimé vers {hhmm(eta)}."
    return f"{text} Suivez votre passage en direct : {url}"


async def send_confirmation(phone: str, email: str | None, text: str) -> None:
    await notifier.send_sms(phone, text)
    if email:
        await notifier.send_email(email, "Confirmation de votre rendez-vous", text)


@router.post("", response_model=BookingOut, status_code=http.HTTP_201_CREATED)
async def create_booking(
    body: BookingIn, background: BackgroundTasks, session: AsyncSession = Depends(get_session),
):
    name = " ".join(body.patient_name.split())
    phone = re.sub(r"[ .\-]", "", body.phone)

    doctor = (
        await session.execute(select(ClinicMember).where(
            ClinicMember.id == body.doctor_id, ClinicMember.role == ClinicRole.doctor,
        ))
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Médecin introuvable")

    # --- the slot: future, bookable window, on the grid, still free -------------
    now = datetime.now(timezone.utc)
    booked = parse_local(body.booked_time)
    local = to_local(booked)
    if booked < now:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "Ce créneau est déjà passé")
    if local.date() > today() + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Réservation possible jusqu'à {MAX_DAYS_AHEAD} jours à l'avance")
    slot = local.strftime("%H:%M")
    if local.second or local.microsecond or slot not in _all_slots():
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Horaire invalide : choisissez un créneau de /api/v1/public/slots")
    # fast path for the common case; two simultaneous requests can both pass it — the
    # unique index (models.SLOT_UNIQUE_INDEX) catches that at insert time, below
    if slot in await taken_slots(session, doctor, local.date()):
        raise HTTPException(http.HTTP_409_CONFLICT, SLOT_TAKEN)

    # --- patient: reuse by phone within this clinic, never edit an existing record from public input
    patient = (
        await session.execute(select(Patient).where(Patient.clinic_id == doctor.clinic_id, Patient.phone == phone))
    ).scalars().first()
    if patient is None:
        patient = Patient(clinic_id=doctor.clinic_id, full_name=name, phone=phone, email=body.email)
        session.add(patient)
        await session.flush()

    appt = Appointment(
        clinic_id=doctor.clinic_id,
        patient_id=patient.id,
        doctor_member_id=doctor.id,
        procedure_category=body.procedure_category,
        scheduled_start=booked,
        scheduled_duration_minutes=PROCEDURE_MINUTES[body.procedure_category],
        source="online",
        # status defaults to 'scheduled' until they check in; tracking_token = uuid4
    )
    session.add(appt)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()  # also drops a patient row created above — nothing half-saved
        if is_slot_conflict(exc):
            raise HTTPException(http.HTTP_409_CONFLICT, SLOT_TAKEN) from exc
        raise

    start, end = day_bounds(local.date())
    day_appts = (
        await session.execute(select(Appointment).where(
            Appointment.clinic_id == doctor.clinic_id,
            Appointment.doctor_member_id == doctor.id,
            Appointment.scheduled_start >= start,
            Appointment.scheduled_start < end,
            Appointment.id != appt.id,
        ))
    ).scalars().all()
    eta = initial_eta(day_appts, booked, body.procedure_category, now)
    # the ETA we announce now is the reference for accuracy (step 3) and alerts (step 4)
    appt.predicted_start = appt.first_predicted_start = appt.notified_start = eta
    await session.commit()

    doctor_name = doctor.display_name or "votre médecin"
    url = tracker_url(appt.tracking_token)
    # after commit, off the request path: a failed send never loses the booking
    background.add_task(send_confirmation, phone, body.email,
                        confirmation_text(name.split(" ")[0], doctor_name, booked, eta, url))

    return BookingOut(
        appointment_id=appt.id, tracking_token=appt.tracking_token, tracker_url=url,
        patient_name=name, doctor_name=doctor_name, procedure_category=body.procedure_category,
        expected_duration_minutes=PROCEDURE_MINUTES[body.procedure_category],
        booked_time=to_local(booked), estimated_start=to_local(eta),
        queue_position=queue_position(day_appts, booked), status=appt.status,
    )
