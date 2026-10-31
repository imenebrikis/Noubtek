"""Queue API — today's board, the single transition entry point, delay recalc."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import TenantContext, get_context, require_role, scoped
from app.localtime import day_bounds, today
from app.models import Appointment, AppointmentStatus, ClinicMember, ClinicRole, Patient, ProcedureCategory
from app.services.ai_delay import delay_insight, estimate_queue, prediction_accuracy
from app.services.appointment_service import AppointmentService
from app.services.notifications import notifier
from app.services.patient_alerts import apply_alerts, pending_alerts, send_alert_sms, tracker_url

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])


class QueueItemOut(BaseModel):
    id: uuid.UUID
    patient_name: str
    patient_phone: str | None
    scheduled_start: datetime
    predicted_start: datetime | None
    procedure_category: ProcedureCategory
    status: AppointmentStatus
    alert_shift_minutes: int | None   # latest proactive alert: > 0 later, < 0 earlier
    alert_at: datetime | None
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


async def _load_rows(ctx: TenantContext, session: AsyncSession):
    start, end = day_bounds(today())
    stmt = (
        scoped(ctx, Appointment)  # enforces clinic_id
        .join(Patient, Patient.id == Appointment.patient_id)
        .where(Appointment.scheduled_start >= start, Appointment.scheduled_start < end)
        # ponytail: no position_in_queue column — arrival order, then schedule
        .order_by(func.coalesce(Appointment.arrived_at, Appointment.scheduled_start))
        .add_columns(Patient.full_name, Patient.phone)
    )
    return (await session.execute(stmt)).all()


async def _doctor_name(ctx: TenantContext, session: AsyncSession, doctor_id: uuid.UUID | None) -> str:
    if doctor_id is not None:
        member = (
            await session.execute(scoped(ctx, ClinicMember).where(ClinicMember.id == doctor_id))
        ).scalar_one_or_none()
        if member and member.display_name:
            return member.display_name
    return "Votre médecin"


def _item(a: Appointment, full_name: str, phone: str | None) -> QueueItemOut:
    return QueueItemOut(
        id=a.id, patient_name=full_name, patient_phone=phone,
        scheduled_start=a.scheduled_start, predicted_start=a.predicted_start,
        procedure_category=a.procedure_category, status=a.status,
        alert_shift_minutes=a.alert_shift_minutes, alert_at=a.alert_at, arrived_at=a.arrived_at,
        session_started_at=a.session_started_at, completed_at=a.completed_at,
    )


async def build_queue_response(
    ctx: TenantContext, session: AsyncSession, doctor_id: uuid.UUID | None, background: BackgroundTasks,
) -> QueueResponse:
    """Recalculate the delay, write predicted_start on that doctor's waiting appts, queue
    SMS for patients whose ETA moved, and return the full board + delay alert.
    Used by transition() and walk-in."""
    rows = await _load_rows(ctx, session)
    doctor_name = await _doctor_name(ctx, session, doctor_id)

    # numbers: local heuristic; message: Gemini (PII-free, cached)
    now = datetime.now(timezone.utc)
    doctor_appts = [a for a, _, _ in rows if a.doctor_member_id == doctor_id]
    est = estimate_queue(doctor_appts, now)
    delay = await delay_insight((ctx.clinic_id, doctor_id), est, doctor_name)

    # UI shows predicted_start if set — stamp it on this doctor's waiting appts
    for a, _, _ in rows:
        if a.id in est.predicted:
            a.predicted_start = est.predicted[a.id]
            # the ETA announced on joining the line is the one we're judged on
            a.first_predicted_start = a.first_predicted_start or est.predicted[a.id]
    fired = apply_alerts(doctor_appts, est, now)  # patients whose ETA moved ≥ 10 min
    await session.commit()
    if fired:
        contacts = {a.patient_id: (name, phone) for a, name, phone in rows}
        background.add_task(send_alert_sms, pending_alerts(fired, contacts))

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


class AccuracyOut(BaseModel):
    sample_size: int                 # patients seen today who had a first ETA
    mae_minutes: float | None        # mean absolute error of that first ETA
    bias_minutes: float | None       # > 0: patients seen later than announced
    within_10_min_pct: int | None


@router.get("/accuracy", response_model=AccuracyOut)
async def accuracy(
    doctor_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(get_context),
    session: AsyncSession = Depends(get_session),
):
    """Accuracy feedback loop: today's first-ETA vs actual start, for the clinic or one doctor."""
    appts = [a for a, _, _ in await _load_rows(ctx, session) if doctor_id is None or a.doctor_member_id == doctor_id]
    return AccuracyOut(**prediction_accuracy(appts).__dict__)


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
    background: BackgroundTasks,
    ctx: TenantContext = Depends(require_role(ClinicRole.admin, ClinicRole.receptionist)),
    session: AsyncSession = Depends(get_session),
):
    appt = await AppointmentService(session).transition(ctx, body.appointment_id, body.new_status)
    # recompute delay for the affected doctor's queue, return board + alert
    return await build_queue_response(ctx, session, appt.doctor_member_id, background)


# --- POST /api/v1/queue/notify-delay — dashboard "Envoyer SMS de retard" -------
class NotifyDelayIn(BaseModel):
    doctor_id: uuid.UUID


class NotifyDelayOut(BaseModel):
    sent: int                 # SMS queued (waiting patients with a phone)
    without_phone: int        # waiting patients reachable on their tracker screen only
    on_screen: int            # waiting patients whose tracker now shows the message
    message: str              # the delay message that went out
    source: str               # 'gemini' | 'heuristic'


async def send_delay_sms(recipients: list[tuple[str, str, str]], message: str) -> None:
    for phone, first_name, token in recipients:
        await notifier.send_sms(phone, f"Bonjour {first_name}. {message} Suivi : {tracker_url(token)}")


@router.post("/notify-delay", response_model=NotifyDelayOut)
async def notify_delay(
    body: NotifyDelayIn,
    background: BackgroundTasks,
    ctx: TenantContext = Depends(require_role(ClinicRole.admin, ClinicRole.receptionist)),
    session: AsyncSession = Depends(get_session),
):
    """Receptionist broadcast: send the current delay message to this doctor's waiting line."""
    rows = [(a, name, phone) for a, name, phone in await _load_rows(ctx, session)
            if a.doctor_member_id == body.doctor_id]
    est = estimate_queue([a for a, _, _ in rows], datetime.now(timezone.utc))
    delay = await delay_insight((ctx.clinic_id, body.doctor_id), est,
                                await _doctor_name(ctx, session, body.doctor_id))

    waiting = [(a, name, phone) for a, name, phone in rows if a.status is AppointmentStatus.waiting]
    recipients = [(phone, name.split(" ")[0], str(a.tracking_token)) for a, name, phone in waiting if phone]
    # every waiting tracker shows it — including patients we can't text
    now = datetime.now(timezone.utc)
    for a, _, _ in waiting:
        a.latest_message, a.latest_message_at = delay.apology_message, now
    await session.commit()
    if recipients:
        background.add_task(send_delay_sms, recipients, delay.apology_message)
    return NotifyDelayOut(sent=len(recipients), without_phone=len(waiting) - len(recipients), on_screen=len(waiting),
                          message=delay.apology_message, source=delay.source)
