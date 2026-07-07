from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.dependencies import TenantContext, scoped
from app.models import Appointment, Clinic, ClinicMember, ClinicRole, Patient, User
from app.services.auth_service import AuthService


async def _seed_clinic(session, name: str) -> tuple[Clinic, ClinicMember, Appointment]:
    user = User(email=f"{name}@example.com", password_hash=AuthService.hash_password("password123"))
    clinic = Clinic(name=name)
    session.add_all([user, clinic])
    await session.flush()
    member = ClinicMember(clinic_id=clinic.id, user_id=user.id, role=ClinicRole.admin)
    patient = Patient(clinic_id=clinic.id, full_name=f"Patient of {name}")
    session.add_all([member, patient])
    await session.flush()
    appt = Appointment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(appt)
    await session.commit()
    return clinic, member, appt


async def test_clinic_a_cannot_read_clinic_b_appointments(session):
    clinic_a, member_a, appt_a = await _seed_clinic(session, "clinic-a")
    _, _, appt_b = await _seed_clinic(session, "clinic-b")

    ctx_a = TenantContext(user_id=member_a.user_id, clinic_id=clinic_a.id, role=ClinicRole.admin)
    rows = (await session.execute(scoped(ctx_a, Appointment))).scalars().all()

    ids = {a.id for a in rows}
    assert ids == {appt_a.id}
    assert appt_b.id not in ids


async def test_receptionist_cannot_change_own_role(client, session):
    # clinic + admin owner via the real register endpoint
    r = await client.post("/auth/register", json={
        "email": "owner@example.com",
        "password": "password123",
        "full_name": "Owner",
        "clinic_name": "Smile Clinic",
    })
    assert r.status_code == 201

    # add a receptionist to that clinic
    clinic = (await session.execute(select(Clinic))).scalar_one()
    recep_user = User(
        email="recep@example.com",
        password_hash=AuthService.hash_password("password123"),
    )
    session.add(recep_user)
    await session.flush()
    recep_member = ClinicMember(
        clinic_id=clinic.id, user_id=recep_user.id, role=ClinicRole.receptionist,
    )
    session.add(recep_member)
    await session.commit()

    login = await client.post("/auth/login", json={
        "email": "recep@example.com", "password": "password123",
    })
    assert login.status_code == 200
    token = login.json()["access_token"]

    # receptionist tries to promote themselves
    r = await client.patch(
        f"/members/{recep_member.id}",
        json={"role": "admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403

    await session.refresh(recep_member)
    assert recep_member.role == ClinicRole.receptionist
