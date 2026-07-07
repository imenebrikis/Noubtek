"""
ClinicFlow — SQLAlchemy 2.0 domain models (PostgreSQL).

Multi-tenancy is enforced in the APPLICATION layer (no more Supabase RLS):
EVERY query that touches a tenant table MUST filter by clinic_id. There is no
database safety net anymore — see CLAUDE.md for the TenantContext pattern and
the non-negotiable rule. Generate Alembic migrations from these models:
    alembic revision --autogenerate -m "init"
    alembic upgrade head
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint, Uuid, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# --- Enums ------------------------------------------------------------------
class ClinicRole(str, enum.Enum):
    admin = "admin"
    receptionist = "receptionist"
    doctor = "doctor"


class AppointmentStatus(str, enum.Enum):
    scheduled = "scheduled"
    waiting = "waiting"
    in_session = "in_session"
    completed = "completed"
    no_show = "no_show"
    cancelled = "cancelled"


# --- Identity (we own auth now, so we own the users table) ------------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)  # argon2 / bcrypt — never plaintext
    full_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Tenant root ------------------------------------------------------------
class Clinic(Base):
    __tablename__ = "clinics"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Algiers")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- RBAC join: which user belongs to which clinic, as what role ------------
class ClinicMember(Base):
    __tablename__ = "clinic_members"
    __table_args__ = (UniqueConstraint("clinic_id", "user_id", name="uq_member_clinic_user"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinics.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[ClinicRole] = mapped_column(SAEnum(ClinicRole, name="clinic_role"))
    display_name: Mapped[str | None] = mapped_column(String(255))  # shown to patients (doctors)
    specialty: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcedureType(Base):
    __tablename__ = "procedure_types"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinics.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    default_duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # for revenue-lost BI
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinics.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinics.id", ondelete="CASCADE"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"))
    doctor_member_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinic_members.id", ondelete="SET NULL"))
    procedure_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("procedure_types.id", ondelete="SET NULL"))

    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    predicted_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # written by heuristic engine

    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(AppointmentStatus, name="appointment_status"),
        default=AppointmentStatus.scheduled, index=True,
    )
    # actual timestamps, stamped by AppointmentService.transition():
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tracking_token: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)  # public tracker key

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# --- Append-only audit log = the AI training dataset. Never UPDATE/DELETE. ---
class AppointmentEvent(Base):
    __tablename__ = "appointment_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    clinic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinics.id", ondelete="CASCADE"), index=True)
    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[AppointmentStatus | None] = mapped_column(SAEnum(AppointmentStatus, name="appointment_status"))
    to_status: Mapped[AppointmentStatus] = mapped_column(SAEnum(AppointmentStatus, name="appointment_status"))
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# --- Offline-generated notification copy (no PII ever sent to the LLM) -------
class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinics.id", ondelete="CASCADE"))  # null = global default
    channel: Mapped[str] = mapped_column(String(16))   # 'whatsapp' | 'sms'
    locale: Mapped[str] = mapped_column(String(5))     # 'fr' | 'en'
    key: Mapped[str] = mapped_column(String(64))        # e.g. 'delay_notice'
    body: Mapped[str] = mapped_column(Text)             # contains {patient_name}, {time}, {doctor}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
