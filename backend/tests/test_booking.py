import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.localtime import CLINIC_TZ, today
from app.models import (
    Appointment, AppointmentStatus, Clinic, ClinicMember, ClinicRole, Patient, ProcedureCategory as PC, User,
)
from app.routers import booking
from app.routers.booking import confirmation_text, initial_eta, queue_position
from app.services.auth_service import AuthService

TOMORROW = today() + timedelta(days=1)
at_tomorrow = lambda hh, mm: datetime.combine(TOMORROW, time(hh, mm)).isoformat()   # naive = clinic-local


@pytest.fixture
async def doctor(session) -> ClinicMember:
    clinic = Clinic(name="Clinique Test")
    doc_user = User(email="doc@example.com", password_hash=AuthService.hash_password("password123"))
    admin_user = User(email="admin@example.com", password_hash=AuthService.hash_password("password123"))
    session.add_all([clinic, doc_user, admin_user])
    await session.flush()
    doc = ClinicMember(clinic_id=clinic.id, user_id=doc_user.id, role=ClinicRole.doctor, display_name="Dr. Amine")
    session.add_all([doc, ClinicMember(clinic_id=clinic.id, user_id=admin_user.id, role=ClinicRole.admin)])
    await session.commit()
    return doc


def _body(doctor, **kw):
    return {"patient_name": "Sara  Haddad", "phone": "06 61 23 45 67", "email": "sara@example.com",
            "doctor_id": str(doctor.id), "procedure_category": "extended", "booked_time": at_tomorrow(10, 0), **kw}


async def test_booking_creates_appointment_and_sends_confirmation(client, session, doctor, capsys):
    r = await client.post("/api/v1/booking", json=_body(doctor))       # no Authorization header

    assert r.status_code == 201, r.text
    b = r.json()
    assert b["patient_name"] == "Sara Haddad" and b["doctor_name"] == "Dr. Amine"
    assert b["procedure_category"] == "extended" and b["expected_duration_minutes"] == 20
    assert b["booked_time"] == f"{TOMORROW}T10:00:00+01:00"            # clinic-local, offset explicit
    assert b["estimated_start"] == b["booked_time"]                    # no line on another day
    assert b["queue_position"] == 1
    assert b["status"] == "scheduled"
    assert b["tracker_url"] == f"http://localhost:5173/patient/{b['tracking_token']}"

    appt = await session.get(Appointment, uuid.UUID(b["appointment_id"]))
    assert appt.clinic_id == doctor.clinic_id and appt.doctor_member_id == doctor.id
    assert appt.source == "online" and appt.status is AppointmentStatus.scheduled
    assert appt.scheduled_start.replace(tzinfo=timezone.utc) == datetime.combine(
        TOMORROW, time(10, 0), CLINIC_TZ)                              # stored UTC = 09:00Z
    assert appt.scheduled_duration_minutes == 20
    assert appt.first_predicted_start and appt.notified_start          # announced ETA recorded
    patient = await session.get(Patient, appt.patient_id)
    assert (patient.phone, patient.email) == ("0661234567", "sara@example.com")

    out = capsys.readouterr().out
    assert (f"[SMS Dispatch] To: 0661234567 Body: Bonjour Sara, votre rendez-vous avec Dr. Amine "
            f"est confirmé le {TOMORROW:%d/%m} à 10:00.") in out
    assert b["tracker_url"] in out
    assert "[Email Dispatch] To: sara@example.com Subject: Confirmation de votre rendez-vous" in out


async def test_slot_taken_is_409_and_patient_is_reused_by_phone(client, session, doctor):
    assert (await client.post("/api/v1/booking", json=_body(doctor))).status_code == 201

    r = await client.post("/api/v1/booking", json=_body(doctor, patient_name="Autre Personne", phone="0770000000"))
    assert r.status_code == 409

    r = await client.post("/api/v1/booking", json=_body(doctor, booked_time=at_tomorrow(10, 20), email=None))
    assert r.status_code == 201
    assert (await session.execute(select(func.count()).select_from(Patient))).scalar_one() == 1

    assert r.json()["queue_position"] == 2                              # behind the 10:00 booking

    # the public slot grid agrees: both clinic-local slots are gone, the next one is free
    slots = (await client.get("/api/v1/public/slots",
                              params={"doctor_id": str(doctor.id), "date": str(TOMORROW)})).json()
    assert "10:00" not in slots["free_slots"] and "10:20" not in slots["free_slots"] and "10:40" in slots["free_slots"]
    assert slots["doctor_name"] == "Dr. Amine"                          # booking page header


@pytest.mark.parametrize("override,status", [
    ({"booked_time": (datetime.now(CLINIC_TZ) - timedelta(hours=1)).replace(tzinfo=None).isoformat()}, 422),
    ({"booked_time": at_tomorrow(10, 7)}, 422),                          # off the 20-min grid
    ({"booked_time": at_tomorrow(20, 0)}, 422),                          # after hours
    ({"booked_time": datetime.combine(today() + timedelta(days=90), time(10)).isoformat()}, 422),
    ({"phone": "hello"}, 422),
    ({"email": "not-an-email"}, 422),
    ({"procedure_category": "surgery"}, 422),
    ({"patient_name": " "}, 422),
    ({"doctor_id": str(uuid.uuid4())}, 404),
])
async def test_booking_rejects_bad_input(client, session, doctor, override, status):
    r = await client.post("/api/v1/booking", json=_body(doctor, **override))
    assert r.status_code == status, r.text
    assert (await session.execute(select(func.count()).select_from(Appointment))).scalar_one() == 0


async def test_non_doctor_member_cannot_be_booked(client, session, doctor):
    admin = (await session.execute(select(ClinicMember).where(ClinicMember.role == ClinicRole.admin))).scalar_one()
    r = await client.post("/api/v1/booking", json=_body(doctor, doctor_id=str(admin.id)))
    assert r.status_code == 404
    r = await client.get("/api/v1/public/slots", params={"doctor_id": str(admin.id), "date": str(TOMORROW)})
    assert r.status_code == 404                                         # staff aren't publicly listed


# --- initial ETA ------------------------------------------------------------
NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def test_initial_eta_waits_behind_todays_line():
    current = Appointment(id=uuid.uuid4(), status=AppointmentStatus.in_session, procedure_category=PC.procedure_complex,
                          scheduled_start=NOW - timedelta(minutes=30), session_started_at=NOW - timedelta(minutes=10))
    waiting = Appointment(id=uuid.uuid4(), status=AppointmentStatus.waiting, procedure_category=PC.routine,
                          scheduled_start=NOW - timedelta(minutes=5), arrived_at=NOW - timedelta(minutes=5))
    booked = NOW + timedelta(minutes=20)

    # 35 min left on the complex act + 15 min routine ahead -> in at +50, not at the +20 slot
    assert initial_eta([current, waiting], booked, PC.extended, NOW) == NOW + timedelta(minutes=50)
    assert initial_eta([], booked, PC.extended, NOW) == booked


def test_queue_position_counts_only_patients_not_yet_seen_ahead():
    mk = lambda status, minutes, arrived=None: Appointment(
        id=uuid.uuid4(), status=status, scheduled_start=NOW + timedelta(minutes=minutes),
        arrived_at=NOW + timedelta(minutes=arrived) if arrived is not None else None)
    booked = NOW + timedelta(minutes=60)
    day = [
        mk(AppointmentStatus.completed, -60),             # already seen
        mk(AppointmentStatus.in_session, -20),            # already seen
        mk(AppointmentStatus.waiting, 0, arrived=-5),     # ahead
        mk(AppointmentStatus.scheduled, 40),              # ahead (earlier slot)
        mk(AppointmentStatus.scheduled, 80),              # after
        mk(AppointmentStatus.cancelled, 20),              # gone
    ]
    assert queue_position(day, booked) == 3
    assert queue_position([], booked) == 1


def test_confirmation_mentions_running_late_only_when_it_matters():
    booked = NOW + timedelta(minutes=20)
    on_time = confirmation_text("Sara", "Dr. Amine", booked, booked + timedelta(minutes=3), "URL")
    late = confirmation_text("Sara", "Dr. Amine", booked, booked + timedelta(minutes=30), "URL")
    assert "retard" not in on_time and on_time.endswith("Suivez votre passage en direct : URL")
    assert "passage estimé vers 11:50" in late                          # 10:50Z shown in Algiers time


# --- double-booking race (unique index uq_appointments_doctor_slot_live) -----
SLOT_TAKEN_MSG = "Ce créneau vient d'être réservé, veuillez en choisir un autre."


async def test_race_past_the_precheck_is_stopped_by_the_db(client, session, doctor, monkeypatch, capsys):
    # Two requests arriving together both see the slot as free in the pre-check. Simulate
    # that by making the pre-check blind, so only the DB index stands between them.
    async def nothing_taken(*_):
        return set()
    monkeypatch.setattr(booking, "taken_slots", nothing_taken)
    first, second = _body(doctor), _body(doctor, patient_name="Karim Hadj", phone="0770000000", email=None)

    r1 = await client.post("/api/v1/booking", json=first)
    r2 = await client.post("/api/v1/booking", json=second)

    assert r1.status_code == 201
    assert r2.status_code == 409 and r2.json()["detail"] == SLOT_TAKEN_MSG
    count = lambda model: session.execute(select(func.count()).select_from(model))
    assert (await count(Appointment)).scalar_one() == 1
    assert (await count(Patient)).scalar_one() == 1      # loser's new patient row rolled back too
    out = capsys.readouterr().out
    assert out.count("[SMS Dispatch]") == 1 and "Karim" not in out   # no confirmation for the loser


async def test_precheck_conflict_uses_the_same_message(client, doctor):
    assert (await client.post("/api/v1/booking", json=_body(doctor))).status_code == 201
    r = await client.post("/api/v1/booking", json=_body(doctor, phone="0770000000"))
    assert r.status_code == 409 and r.json()["detail"] == SLOT_TAKEN_MSG


async def test_cancelled_appointment_frees_its_slot(client, session, doctor):
    body = _body(doctor)
    r = await client.post("/api/v1/booking", json=body)
    appt = await session.get(Appointment, uuid.UUID(r.json()["appointment_id"]))
    appt.status = AppointmentStatus.cancelled          # fixture shortcut, not a queue transition
    await session.commit()

    r = await client.post("/api/v1/booking", json=_body(doctor, phone="0770000000"))
    assert r.status_code == 201                         # partial index ignores cancelled rows
