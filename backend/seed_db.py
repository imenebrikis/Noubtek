"""
Seed the Neon database with a demo clinic for the Saturday hackathon demo.

Re-runnable: wipes and recreates everything under the demo clinic (matched by
name) so judges can reset state. Uses the real auth hashing + enforces clinic_id
on every tenant row — same rules as the app (see CLAUDE.md).

Run:  cd backend && python seed_db.py
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    Appointment, AppointmentEvent, AppointmentStatus, Clinic, ClinicMember,
    ClinicRole, Patient, User,
)
from app.services.auth_service import AuthService

CLINIC_NAME = "Noubtek — Clinique Médicale"
ADMIN_EMAIL = "judge@noubtek.dz"
ADMIN_PASSWORD = "admin123"
DOCTOR_EMAIL = "amine@noubtek.dz"

# (full_name, phone, scheduled offset from 9h, final status)
PATIENTS = [
    ("Yasmine Bouaziz", "+213 555 01 02 03", 0, AppointmentStatus.completed),
    ("Karim Hadj",      "+213 555 04 05 06", 1, AppointmentStatus.in_session),
    ("Lina Saïdi",      "+213 555 07 08 09", 2, AppointmentStatus.waiting),
    ("Omar Belkacem",   "+213 555 10 11 12", 3, AppointmentStatus.waiting),
    ("Sofia Meziane",   "+213 555 13 14 15", 4, AppointmentStatus.waiting),
]

# which actual timestamps each final status implies, and the transition chain
_CHAINS = {
    AppointmentStatus.waiting:    [AppointmentStatus.waiting],
    AppointmentStatus.in_session: [AppointmentStatus.waiting, AppointmentStatus.in_session],
    AppointmentStatus.completed:  [AppointmentStatus.waiting, AppointmentStatus.in_session,
                                   AppointmentStatus.completed],
}


async def seed() -> None:
    async with SessionLocal() as s:
        # --- wipe prior demo data for this clinic (idempotent re-seed) --------
        existing = (await s.execute(select(Clinic).where(Clinic.name == CLINIC_NAME))).scalars().all()
        for c in existing:
            await s.execute(delete(AppointmentEvent).where(AppointmentEvent.clinic_id == c.id))
            await s.execute(delete(Appointment).where(Appointment.clinic_id == c.id))
            await s.execute(delete(Patient).where(Patient.clinic_id == c.id))
            await s.execute(delete(ClinicMember).where(ClinicMember.clinic_id == c.id))
            await s.execute(delete(Clinic).where(Clinic.id == c.id))
        # users aren't tenant-scoped; drop the demo ones so emails are free
        await s.execute(delete(User).where(User.email.in_([ADMIN_EMAIL, DOCTOR_EMAIL])))
        await s.commit()

        # --- clinic + users --------------------------------------------------
        clinic = Clinic(name=CLINIC_NAME)
        admin_user = User(
            email=ADMIN_EMAIL,
            password_hash=AuthService.hash_password(ADMIN_PASSWORD),
            full_name="Judge Admin",
        )
        doctor_user = User(
            email=DOCTOR_EMAIL,
            password_hash=AuthService.hash_password(ADMIN_PASSWORD),
            full_name="Dr. Amine",
        )
        s.add_all([clinic, admin_user, doctor_user])
        await s.flush()  # need ids

        admin_member = ClinicMember(
            clinic_id=clinic.id, user_id=admin_user.id, role=ClinicRole.admin,
        )
        doctor_member = ClinicMember(
            clinic_id=clinic.id, user_id=doctor_user.id, role=ClinicRole.doctor,
            display_name="Dr. Amine", specialty="Généraliste",
        )
        s.add_all([admin_member, doctor_member])
        await s.flush()

        # --- appointments (today) + their event logs ------------------------
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=9, minute=0, second=0, microsecond=0)

        for full_name, phone, offset, final in PATIENTS:
            patient = Patient(clinic_id=clinic.id, full_name=full_name, phone=phone)
            s.add(patient)
            await s.flush()

            scheduled = day_start + timedelta(minutes=30 * offset)
            appt = Appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                doctor_member_id=doctor_member.id,
                scheduled_start=scheduled,
                status=final,
            )
            # stamp actual timestamps to match the final status
            chain = _CHAINS[final]
            if AppointmentStatus.waiting in chain:
                appt.arrived_at = scheduled
            if AppointmentStatus.in_session in chain:
                appt.session_started_at = scheduled + timedelta(minutes=5)
            if AppointmentStatus.completed in chain:
                appt.completed_at = scheduled + timedelta(minutes=25)
            s.add(appt)
            await s.flush()

            # append-only transition log (the future AI training set)
            prev = AppointmentStatus.scheduled
            for to in chain:
                s.add(AppointmentEvent(
                    clinic_id=clinic.id, appointment_id=appt.id,
                    from_status=prev, to_status=to, created_by=admin_user.id,
                ))
                prev = to

        await s.commit()

    print(f"Seeded clinic '{CLINIC_NAME}'")
    print(f"  admin:  {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    print(f"  doctor: Dr. Amine — Généraliste")
    print(f"  appointments: 1 terminé, 1 en consultation, 3 en attente")


if __name__ == "__main__":
    asyncio.run(seed())
