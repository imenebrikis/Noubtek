import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Appointment, AppointmentStatus, Clinic, ClinicMember, ClinicRole, User
from app.services import ai_delay, patient_alerts
from app.services.ai_delay import QueueEstimate
from app.services.auth_service import AuthService
from app.services.patient_alerts import alert_message, apply_alerts

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)   # 11:00 in Algiers
at = lambda m: NOW + timedelta(minutes=m)


def _waiting(notified=None, first=None):
    return Appointment(id=uuid.uuid4(), status=AppointmentStatus.waiting,
                       notified_start=notified, first_predicted_start=first)


def _est(predicted: dict) -> QueueEstimate:
    return QueueEstimate(delay_minutes=0, waiting_count=len(predicted), in_session_elapsed_min=None, predicted=predicted)


# --- detection --------------------------------------------------------------
def test_first_sighting_sets_baseline_without_alert():
    a, b = _waiting(first=at(30)), _waiting()
    fired = apply_alerts([a, b], _est({a.id: at(50), b.id: at(70)}), NOW)

    assert fired == []
    assert a.notified_start == at(30)      # the ETA they were actually given on arrival
    assert b.notified_start == at(70)      # no first ETA recorded -> current one
    assert a.alert_at is None


def test_alert_fires_on_threshold_both_directions_and_resets_reference():
    small, late, early, edge = _waiting(at(30)), _waiting(at(30)), _waiting(at(30)), _waiting(at(30))
    est = _est({small.id: at(39), late.id: at(45), early.id: at(18), edge.id: at(40)})

    fired = apply_alerts([small, late, early, edge], est, NOW)

    assert set(fired) == {late, early, edge}
    assert small.alert_at is None and small.notified_start == at(30)   # 9 min: below threshold
    assert (late.alert_shift_minutes, late.notified_start, late.alert_at) == (15, at(45), NOW)
    assert early.alert_shift_minutes == -12
    assert edge.alert_shift_minutes == 10                               # threshold is inclusive

    # same ETA on the next poll: already told -> no repeat alert
    assert apply_alerts([late], _est({late.id: at(45)}), NOW + timedelta(minutes=1)) == []


def test_non_waiting_appointments_are_ignored():
    a = _waiting(at(30))
    a.status = AppointmentStatus.in_session
    assert apply_alerts([a], _est({}), NOW) == [] and a.alert_at is None


# --- message ----------------------------------------------------------------
async def test_message_default_without_key(monkeypatch):
    monkeypatch.setattr(ai_delay.settings, "GEMINI_API_KEY", None)
    patient_alerts._templates.clear()

    later = await alert_message(15, at(45))
    earlier = await alert_message(-12, at(18))

    assert later.source == "heuristic" and "retardé d'environ 15 minutes" in later.message and "11:45" in later.message
    assert "avancé d'environ 12 minutes" in earlier.message and "11:18" in earlier.message


async def test_message_from_gemini_is_templated_and_pii_free(fake_gemini):
    prompts = fake_gemini("Votre passage est avancé de 12 minutes, soyez prêt vers {heure}.")

    first = await alert_message(-12, at(18))
    second = await alert_message(-12, at(25))       # same shift -> cached template, new time

    assert first.source == "gemini" and first.message.endswith("vers 11:18.")
    assert second.message.endswith("vers 11:25.") and len(prompts) == 1
    assert "avancé" in prompts[0] and "12" in prompts[0]


async def test_message_rejected_without_placeholder_or_with_wrong_number(fake_gemini):
    fake_gemini("Votre passage est retardé de 20 minutes.")       # wrong number, no {heure}
    r = await alert_message(15, at(45))
    assert r.source == "heuristic" and "15 minutes" in r.message


# --- end to end: overrun with no staff click -> tracker shows alert, board flags it
async def test_overrun_triggers_alert_on_tracker_board_and_sms(client, session, capsys):
    r = await client.post("/api/v1/auth/register", json={
        "email": "owner@example.com", "password": "password123", "full_name": "Owner", "clinic_name": "C",
    })
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    clinic = (await session.execute(select(Clinic))).scalar_one()
    doc_user = User(email="doc@example.com", password_hash=AuthService.hash_password("password123"))
    session.add(doc_user)
    await session.flush()
    doctor = ClinicMember(clinic_id=clinic.id, user_id=doc_user.id, role=ClinicRole.doctor)
    session.add(doctor)
    await session.commit()

    async def walk_in(name, cat, phone=None):
        r = await client.post("/api/v1/queue/walk-in", headers=auth, json={
            "full_name": name, "phone": phone, "doctor_id": str(doctor.id), "procedure_category": cat})
        return uuid.UUID(r.json()["created"]["id"])

    sara_id = await walk_in("Sara Haddad", "procedure_complex")    # 45 min
    amine_id = await walk_in("Amine B", "routine", "0550112233")    # told: in ~45 min
    await client.post("/api/v1/queue/transition", headers=auth,
                      json={"appointment_id": str(sara_id), "new_status": "in_session"})

    amine = await session.get(Appointment, amine_id)
    r = await client.get(f"/api/v1/public/track/{amine.tracking_token}")
    assert r.json()["alert"] is None                                # nothing moved yet

    # Sara has actually been in for 60 min: estimate collapses to the 5-min overrun buffer
    sara = await session.get(Appointment, sara_id)
    sara.session_started_at = datetime.now(timezone.utc) - timedelta(minutes=60)
    await session.commit()

    alert = (await client.get(f"/api/v1/public/track/{amine.tracking_token}")).json()["alert"]
    assert alert is not None
    assert -41 <= alert["shift_minutes"] <= -39                     # 45 -> 5 min: ~40 min earlier
    assert alert["message"]

    # pushed through the NotificationService (console transport) exactly once
    out = capsys.readouterr().out
    assert out.count("[SMS Dispatch] To: 0550112233 Body: Bonjour Amine.") == 1
    assert f"/patient/{amine.tracking_token}" in out
    await client.get(f"/api/v1/public/track/{amine.tracking_token}")   # next poll: already told
    assert "[SMS Dispatch]" not in capsys.readouterr().out

    board = (await client.get("/api/v1/queue", headers=auth)).json()
    row = next(x for x in board if x["id"] == str(amine_id))
    assert row["alert_shift_minutes"] == alert["shift_minutes"] and row["alert_at"]
