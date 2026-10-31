import uuid
from datetime import timedelta

from app.localtime import day_bounds, today as local_today
from app.models import (
    Appointment, AppointmentStatus, Clinic, ClinicMember, ClinicRole, Patient, ProcedureCategory, User,
)
from app.services.auth_service import AuthService


async def test_track_returns_queue_position_without_ticket(client, session):
    user = User(email="doc@example.com", password_hash=AuthService.hash_password("password123"))
    clinic = Clinic(name="Smile Clinic")
    session.add_all([user, clinic])
    await session.flush()
    doctor = ClinicMember(clinic_id=clinic.id, user_id=user.id, role=ClinicRole.doctor)
    first = Patient(clinic_id=clinic.id, full_name="Amine Benali")
    second = Patient(clinic_id=clinic.id, full_name="Sara Haddad")
    session.add_all([doctor, first, second])
    await session.flush()

    today = day_bounds(local_today())[0]   # clinic-local midnight, as UTC
    # seeded straight into 'waiting' — fixture setup, not a business transition
    appts = [
        Appointment(
            clinic_id=clinic.id, patient_id=p.id, doctor_member_id=doctor.id,
            scheduled_start=today + timedelta(minutes=10 * i),
            arrived_at=today + timedelta(minutes=10 * i),
            status=AppointmentStatus.waiting, procedure_category=cat,
        )
        for i, (p, cat) in enumerate([(first, ProcedureCategory.procedure_minor), (second, None)])
    ]
    session.add_all(appts)
    await session.commit()

    r = await client.get(f"/api/v1/public/track/{appts[1].tracking_token}")
    assert r.status_code == 200
    body = r.json()
    assert body["patient_first_name"] == "Sara"
    assert body["status"] == "waiting"
    assert body["position"] == 2
    assert body["people_ahead"] == 1
    assert "ticket" not in body
    assert body["doctor_id"] == str(doctor.id)          # lets a finished patient re-book the same doctor
    # first in line is a minor procedure (30 min); second's category is unset -> stored as routine
    assert 29 <= body["eta_minutes"] <= 30
    assert appts[1].procedure_category is ProcedureCategory.routine
    assert body["ai_source"] in ("gemini", "heuristic")
    assert body["ai_message"]
    assert body["delay_minutes"] >= 0

    r = await client.get(f"/api/v1/public/track/{uuid.uuid4()}")
    assert r.status_code == 404
