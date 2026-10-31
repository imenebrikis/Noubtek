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
    PROCEDURE_MINUTES, Appointment, AppointmentEvent, AppointmentStatus, Clinic, ClinicMember,
    ClinicRole, Patient, ProcedureCategory, User,
)
from app.services.ai_delay import estimate_queue
from app.services.auth_service import AuthService

CLINIC_NAME = "Noubtek — Clinique Médicale"
ADMIN_EMAIL = "judge@noubtek.dz"
ADMIN_PASSWORD = "admin123"
DOCTOR_EMAIL = "amine@noubtek.dz"

# (full_name, phone, booked / session-start minutes relative to NOW, final status, category)
# Relative to now so the demo reads right whenever it's seeded: Karim's complex act has
# ~7 min left, so Lina (booked 30 min ago) is seen ~37 min late, then routine, then complex.
# ponytail: seeding within ~1h30 after midnight (clinic time) pushes the first rows into "yesterday"
PATIENTS = [
    ("Yasmine Bouaziz", "+213 555 01 02 03", -90, -87, AppointmentStatus.completed,  ProcedureCategory.routine),
    ("Karim Hadj",      "+213 555 04 05 06", -60, -38, AppointmentStatus.in_session, ProcedureCategory.procedure_complex),
    ("Lina Saïdi",      "+213 555 07 08 09", -30, None, AppointmentStatus.waiting,   ProcedureCategory.extended),
    ("Omar Belkacem",   "+213 555 10 11 12", -10, None, AppointmentStatus.waiting,   ProcedureCategory.routine),
    ("Sofia Meziane",   "+213 555 13 14 15", 15, None, AppointmentStatus.waiting,    ProcedureCategory.procedure_complex),
]

# accuracy feedback loop: how many minutes later than first announced the already-seen
# patients actually went in (> 0 = late). Gives the dashboard's precision line real numbers.
FIRST_ETA_ERROR_MIN = {"Yasmine Bouaziz": 4, "Karim Hadj": 9}

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
        appts = []

        for full_name, phone, booked_min, started_min, final, category in PATIENTS:
            patient = Patient(clinic_id=clinic.id, full_name=full_name, phone=phone)
            s.add(patient)
            await s.flush()

            scheduled = now + timedelta(minutes=booked_min)
            appt = Appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                doctor_member_id=doctor_member.id,
                scheduled_start=scheduled,
                scheduled_duration_minutes=PROCEDURE_MINUTES[category],
                procedure_category=category,
                status=final,
            )
            # stamp actual timestamps to match the final status
            chain = _CHAINS[final]
            if AppointmentStatus.waiting in chain:
                appt.arrived_at = min(now, scheduled - timedelta(minutes=2))
            if AppointmentStatus.in_session in chain:
                appt.session_started_at = now + timedelta(minutes=started_min)
            if AppointmentStatus.completed in chain:
                appt.completed_at = appt.session_started_at + timedelta(minutes=PROCEDURE_MINUTES[category] + 3)
            if full_name in FIRST_ETA_ERROR_MIN:
                appt.first_predicted_start = appt.session_started_at - timedelta(minutes=FIRST_ETA_ERROR_MIN[full_name])
            appts.append(appt)
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

        # waiting patients get the ETA the live engine would have announced
        for appt_id, start in estimate_queue(appts, now).predicted.items():
            appt = next(a for a in appts if a.id == appt_id)
            appt.predicted_start = appt.first_predicted_start = start

        await s.commit()

    print(f"Seeded clinic '{CLINIC_NAME}'")
    print(f"  admin:  {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    print(f"  doctor: Dr. Amine — Généraliste")
    print(f"  appointments: 1 terminé, 1 en consultation, 3 en attente")
    print(f"  catégories: routine ×2, étendue ×1, acte complexe ×2")


if __name__ == "__main__":
    asyncio.run(seed())
