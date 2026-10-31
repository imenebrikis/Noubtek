import uuid

from sqlalchemy import func, select

from app.models import Appointment, Clinic, ClinicMember, ClinicRole, Patient, ProcedureCategory, User
from app.services.auth_service import AuthService


async def _clinic_with_doctor(client, session) -> tuple[dict, ClinicMember]:
    r = await client.post("/api/v1/auth/register", json={
        "email": "owner@example.com", "password": "password123",
        "full_name": "Owner", "clinic_name": "Smile Clinic",
    })
    assert r.status_code == 201
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    clinic = (await session.execute(select(Clinic))).scalar_one()
    doc_user = User(email="doc@example.com", password_hash=AuthService.hash_password("password123"))
    session.add(doc_user)
    await session.flush()
    doctor = ClinicMember(clinic_id=clinic.id, user_id=doc_user.id, role=ClinicRole.doctor)
    session.add(doctor)
    await session.commit()
    return auth, doctor


async def test_walk_in_stores_procedure_category(client, session):
    auth, doctor = await _clinic_with_doctor(client, session)

    base = {"full_name": "Sara Haddad", "doctor_id": str(doctor.id)}

    r = await client.post("/api/v1/queue/walk-in", json={**base, "procedure_category": "procedure_complex"}, headers=auth)
    assert r.status_code == 201
    appt = await session.get(Appointment, uuid.UUID(r.json()["created"]["id"]))
    assert appt.procedure_category is ProcedureCategory.procedure_complex
    assert appt.scheduled_duration_minutes == 45
    assert r.json()["queue"][0]["procedure_category"] == "procedure_complex"

    # omitted -> routine (15 min)
    r = await client.post("/api/v1/queue/walk-in", json={**base, "full_name": "Amine B"}, headers=auth)
    assert r.status_code == 201
    appt = await session.get(Appointment, uuid.UUID(r.json()["created"]["id"]))
    assert appt.procedure_category is ProcedureCategory.routine
    assert appt.scheduled_duration_minutes == 15

    # unknown category is rejected at the trust boundary
    r = await client.post("/api/v1/queue/walk-in", json={**base, "procedure_category": "surgery"}, headers=auth)
    assert r.status_code == 422


async def test_first_eta_is_kept_and_feeds_accuracy(client, session):
    auth, doctor = await _clinic_with_doctor(client, session)
    walk_in = lambda name, cat: client.post("/api/v1/queue/walk-in", headers=auth, json={
        "full_name": name, "doctor_id": str(doctor.id), "procedure_category": cat,
    })

    r = await walk_in("Sara Haddad", "procedure_complex")
    sara_id = uuid.UUID(r.json()["created"]["id"])
    r = await walk_in("Amine B", "routine")
    amine_id = uuid.UUID(r.json()["created"]["id"])

    sara = await session.get(Appointment, sara_id)
    amine = await session.get(Appointment, amine_id)
    first_eta = amine.first_predicted_start
    assert sara.first_predicted_start is not None and first_eta is not None

    # Sara goes in -> board recalculates and rewrites predicted_start; first ETA must not change
    r = await client.post("/api/v1/queue/transition", headers=auth,
                          json={"appointment_id": str(sara_id), "new_status": "in_session"})
    assert r.status_code == 200
    await session.refresh(amine)
    assert amine.first_predicted_start == first_eta

    r = await client.get("/api/v1/queue/accuracy", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["sample_size"] == 1                    # only Sara has started
    assert body["mae_minutes"] is not None and body["mae_minutes"] < 1   # seen right when predicted

    r = await client.get("/api/v1/queue/accuracy", headers=auth, params={"doctor_id": str(uuid.uuid4())})
    assert r.json()["sample_size"] == 0                # other doctor: nothing yet


async def test_scheduled_walk_in_on_a_held_slot_is_409(client, session):
    auth, doctor = await _clinic_with_doctor(client, session)
    body = {"doctor_id": str(doctor.id), "is_scheduled": True, "slot_time": "10:00"}

    r = await client.post("/api/v1/queue/walk-in", headers=auth, json={**body, "full_name": "Sara Haddad"})
    assert r.status_code == 201
    r = await client.post("/api/v1/queue/walk-in", headers=auth, json={**body, "full_name": "Amine B"})
    assert r.status_code == 409 and "déjà pris" in r.json()["detail"]
    assert (await session.execute(select(func.count()).select_from(Appointment))).scalar_one() == 1


async def test_notify_delay_texts_this_doctors_waiting_line(client, session, capsys):
    auth, doctor = await _clinic_with_doctor(client, session)
    for name, phone in [("Sara Haddad", "0661000001"), ("Amine B", "0661000002"), ("Sans Tel", None)]:
        r = await client.post("/api/v1/queue/walk-in", headers=auth,
                              json={"full_name": name, "phone": phone, "doctor_id": str(doctor.id)})
        assert r.status_code == 201
    capsys.readouterr()                                       # drop walk-in output

    r = await client.post("/api/v1/queue/notify-delay", headers=auth, json={"doctor_id": str(doctor.id)})

    assert r.status_code == 200
    body = r.json()
    assert (body["sent"], body["without_phone"]) == (2, 1) and body["message"]
    out = capsys.readouterr().out
    assert out.count("[SMS Dispatch]") == 2
    assert "To: 0661000001 Body: Bonjour Sara." in out and "To: 0661000002 Body: Bonjour Amine." in out
    assert body["message"] in out and "/patient/" in out

    # saved for every waiting tracker — the patient without a phone sees it on screen
    assert body["on_screen"] == 3
    appts = (await session.execute(select(Appointment))).scalars().all()
    for a in appts:
        await session.refresh(a)
    assert {a.latest_message for a in appts} == {body["message"]} and all(a.latest_message_at for a in appts)
    no_phone_id = (await session.execute(select(Patient.id).where(Patient.phone.is_(None)))).scalar_one()
    no_phone = next(a for a in appts if a.patient_id == no_phone_id)
    track = (await client.get(f"/api/v1/public/track/{no_phone.tracking_token}")).json()
    assert track["latest_message"] == body["message"] and track["latest_message_at"].endswith("+01:00")

    # once called in, the broadcast no longer shows
    await client.post("/api/v1/queue/transition", headers=auth,
                      json={"appointment_id": str(no_phone.id), "new_status": "in_session"})
    assert (await client.get(f"/api/v1/public/track/{no_phone.tracking_token}")).json()["latest_message"] is None

    capsys.readouterr()   # the transition above may fire step-4 ETA alerts; start clean
    # a doctor id outside this clinic reaches nobody (rows are clinic-scoped)
    r = await client.post("/api/v1/queue/notify-delay", headers=auth, json={"doctor_id": str(uuid.uuid4())})
    assert r.json()["sent"] == 0 and "[SMS Dispatch]" not in capsys.readouterr().out
    # public callers can't trigger it
    r = await client.post("/api/v1/queue/notify-delay", json={"doctor_id": str(doctor.id)})
    assert r.status_code in (401, 403)
