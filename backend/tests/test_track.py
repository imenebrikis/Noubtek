import uuid
from datetime import datetime, timedelta, timezone

from app.models import Appointment, AppointmentStatus, Clinic, ClinicMember, ClinicRole, Patient, User
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

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # seeded straight into 'waiting' — fixture setup, not a business transition
    appts = [
        Appointment(
            clinic_id=clinic.id, patient_id=p.id, doctor_member_id=doctor.id,
            scheduled_start=today + timedelta(minutes=10 * i),
            arrived_at=today + timedelta(minutes=10 * i),
            status=AppointmentStatus.waiting,
        )
        for i, p in enumerate([first, second])
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

    r = await client.get(f"/api/v1/public/track/{uuid.uuid4()}")
    assert r.status_code == 404
