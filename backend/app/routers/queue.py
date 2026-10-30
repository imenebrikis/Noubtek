"""Queue API — today's board, the single transition entry point, delay recalc."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import TenantContext, get_context, require_role, scoped
from app.models import Appointment, AppointmentStatus, ClinicMember, ClinicRole, Patient
from app.services.ai_delay import compute_delay
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])


class QueueItemOut(BaseModel):
    id: uuid.UUID
    patient_name: str
    patient_phone: str | None
    scheduled_start: datetime
    predicted_start: datetime | None
    status: AppointmentStatus
    arrived_at: datetime | None
    session_started_at: datetime | None
    completed_at: datetime | None


class DelayAlert(BaseModel):
    estimated_delay_minutes: int
    apology_message: str
    source: str  # 'gemini' | 'heuristic'


class QueueResponse(BaseModel):
    queue: list[QueueItemOut]
    delay_alert: DelayAlert


class TransitionIn(BaseModel):
    appointment_id: uuid.UUID
    new_status: AppointmentStatus


class DoctorOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    specialty: str | None


def _today_window() -> tuple[datetime, datetime]:
    # ponytail: UTC day window; make clinic-tz aware when clinics span zones
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def _load_rows(ctx: TenantContext, session: AsyncSession):
    start, end = _today_window()
    stmt = (
        scoped(ctx, Appointment)  # enforces clinic_id
        .join(Patient, Patient.id == Appointment.patient_id)
        .where(Appointment.scheduled_start >= start, Appointment.scheduled_start < end)
        # ponytail: no position_in_queue column — arrival order, then schedule
        .order_by(func.coalesce(Appointment.arrived_at, Appointment.scheduled_start))
        .add_columns(Patient.full_name, Patient.phone)
    )
    return (await session.execute(stmt)).all()


def _item(a: Appointment, full_name: str, phone: str | None) -> QueueItemOut:
    return QueueItemOut(
        id=a.id, patient_name=full_name, patient_phone=phone,
        scheduled_start=a.scheduled_start, predicted_start=a.predicted_start,
        status=a.status, arrived_at=a.arrived_at,
        session_started_at=a.session_started_at, completed_at=a.completed_at,
    )


def _snapshot(rows, doctor_id: uuid.UUID, doctor_name: str) -> dict:
    """Build the PII-free timing snapshot for one doctor's today queue."""
    now = datetime.now(timezone.utc)
    appts = [a for a, _, _ in rows if a.doctor_member_id == doctor_id]
    waiting = [a for a in appts if a.status is AppointmentStatus.waiting]
    completed = [a for a in appts if a.status is AppointmentStatus.completed
                 and a.session_started_at and a.completed_at]
    in_session = [a for a in appts if a.status is AppointmentStatus.in_session and a.session_started_at]
    durs = [(a.completed_at - a.session_started_at).total_seconds() / 60 for a in completed]
    return {
        "doctor": doctor_name,
        "waiting_count": len(waiting),
        "in_session_elapsed_min": (
            round((now - in_session[0].session_started_at).total_seconds() / 60) if in_session else None
        ),
        "completed_count": len(completed),
        "avg_actual_duration_min": round(sum(durs) / len(durs), 1) if durs else None,
        "baseline_duration_min": (
            round(sum(a.scheduled_duration_minutes for a in appts) / len(appts)) if appts else 20
        ),
    }


async def build_queue_response(
    ctx: TenantContext, session: AsyncSession, doctor_id: uuid.UUID | None,
) -> QueueResponse:
    """Recalculate the delay, write predicted_start on that doctor's waiting appts,
    and return the full board + delay alert. Used by transition() and walk-in."""
    rows = await _load_rows(ctx, session)

    doctor_name = "médecin"
    if doctor_id is not None:
        member = (
            await session.execute(scoped(ctx, ClinicMember).where(ClinicMember.id == doctor_id))
        ).scalar_one_or_none()
        if member and member.display_name:
            doctor_name = member.display_name

    delay = await compute_delay(_snapshot(rows, doctor_id, doctor_name))

    # UI shows predicted_start if set — stamp it on this doctor's waiting appts
    for a, _, _ in rows:
        if a.doctor_member_id == doctor_id and a.status is AppointmentStatus.waiting:
            a.predicted_start = a.scheduled_start + timedelta(minutes=delay.estimated_delay_minutes)
    await session.commit()

    return QueueResponse(
        queue=[_item(a, name, phone) for a, name, phone in rows],
        delay_alert=DelayAlert(**delay.__dict__),
    )


@router.get("", response_model=list[QueueItemOut])
async def get_queue(
    ctx: TenantContext = Depends(get_context),  # any clinic member may view
    session: AsyncSession = Depends(get_session),
):
    return [_item(a, name, phone) for a, name, phone in await _load_rows(ctx, session)]


@router.get("/doctors", response_model=list[DoctorOut])
async def list_doctors(
    ctx: TenantContext = Depends(get_context),
    session: AsyncSession = Depends(get_session),
):
    members = (
        await session.execute(
            scoped(ctx, ClinicMember).where(ClinicMember.role == ClinicRole.doctor)
        )
    ).scalars().all()
    return [DoctorOut(id=m.id, display_name=m.display_name, specialty=m.specialty) for m in members]


@router.post("/transition", response_model=QueueResponse)
async def transition(
    body: TransitionIn,
    ctx: TenantContext = Depends(require_role(ClinicRole.admin, ClinicRole.receptionist)),
    session: AsyncSession = Depends(get_session),
):
    appt = await AppointmentService(session).transition(ctx, body.appointment_id, body.new_status)
    # recompute delay for the affected doctor's queue, return board + alert
    return await build_queue_response(ctx, session, appt.doctor_member_id)
