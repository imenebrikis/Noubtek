import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Appointment, AppointmentStatus
from app.models import ProcedureCategory as PC
from app.services import ai_delay
from app.services.ai_delay import delay_insight, estimate_queue, pace_factor

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def _appt(status, scheduled_min, *, cat=None, started=None, arrived=None, took=None, duration=None):
    started_at = NOW + timedelta(minutes=started) if started is not None else None
    return Appointment(
        id=uuid.uuid4(), status=status, procedure_category=cat, scheduled_duration_minutes=duration,
        scheduled_start=NOW + timedelta(minutes=scheduled_min),
        arrived_at=NOW + timedelta(minutes=arrived) if arrived is not None else None,
        session_started_at=started_at,
        completed_at=started_at + timedelta(minutes=took) if took is not None else None,
    )


def _done(planned, took):
    """A completed consultation booked for `planned` min that actually took `took` min."""
    return _appt(AppointmentStatus.completed, -180, started=-180, took=took, duration=planned)


# --- pace calibration (step 2) ----------------------------------------------
def test_pace_factor_is_actual_over_scheduled_totals():
    factor, n = pace_factor([_done(15, 18), _done(20, 26)])
    assert n == 2
    assert factor == pytest.approx(44 / 35)          # totals ratio, not mean of ratios


def test_pace_factor_defaults_to_one_without_completed_today():
    waiting = _appt(AppointmentStatus.waiting, 0)
    no_timestamps = _appt(AppointmentStatus.completed, -60, duration=15)   # can't measure -> ignored
    assert pace_factor([]) == (1.0, 0)
    assert pace_factor([waiting, no_timestamps]) == (1.0, 0)


@pytest.mark.parametrize("planned,took,expected", [
    (15, 60, 1.50),     # 4x slower -> capped
    (15, 5, 0.75),      # 3x faster -> floored
    (20, 30, 1.50),     # exactly at the cap
    (20, 15, 0.75),     # exactly at the floor
    (20, 22, 1.10),     # inside the band -> untouched
])
def test_pace_factor_is_clamped(planned, took, expected):
    assert pace_factor([_done(planned, took)])[0] == pytest.approx(expected)


def test_pace_scales_waiting_line_but_not_active_session():
    current = _appt(AppointmentStatus.in_session, -60, cat=PC.procedure_complex, started=-10)   # 45m, 10 in
    w1 = _appt(AppointmentStatus.waiting, -30, cat=PC.extended, arrived=-35)      # 20m × 1.2 = 24
    w2 = _appt(AppointmentStatus.waiting, -20, cat=PC.routine, arrived=-25)       # 15m × 1.2 = 18
    w3 = _appt(AppointmentStatus.waiting, 0, arrived=-5)

    est = estimate_queue([_done(15, 18), current, w1, w2, w3], NOW)

    assert est.pace_factor == pytest.approx(1.2) and est.completed_count == 1
    assert est.predicted[w1.id] == NOW + timedelta(minutes=35)            # active remainder: unscaled
    assert est.predicted[w2.id] == NOW + timedelta(minutes=35 + 24)
    assert est.predicted[w3.id] == NOW + timedelta(minutes=35 + 24 + 18)
    assert est.delay_minutes == 65                                         # first in line: pace not yet applied

    # same queue at nominal pace: w3 is 7 min earlier (4 + 3 min saved ahead of it)
    nominal = estimate_queue([current, w1, w2, w3], NOW)
    assert nominal.predicted[w3.id] == NOW + timedelta(minutes=35 + 20 + 15)


def test_prompt_states_pace_of_the_day():
    slow = estimate_queue([_done(15, 18), _appt(AppointmentStatus.waiting, -20)], NOW)
    fast = estimate_queue([_done(20, 16), _appt(AppointmentStatus.waiting, -20)], NOW)
    fresh = estimate_queue([_appt(AppointmentStatus.waiting, -20)], NOW)

    assert "Rythme du jour: +20% de temps par consultation" in ai_delay._build_prompt(slow)
    assert "Rythme du jour: -20% de temps par consultation" in ai_delay._build_prompt(fast)
    assert "Rythme du jour: pas encore mesuré" in ai_delay._build_prompt(fresh)


def test_estimate_sums_category_minutes_of_patients_ahead():
    done = _appt(AppointmentStatus.completed, -90, cat=PC.procedure_complex)            # ignored
    current = _appt(AppointmentStatus.in_session, -60, cat=PC.procedure_complex, started=-10)  # 45m, 10 in
    w1 = _appt(AppointmentStatus.waiting, -30, cat=PC.extended, arrived=-35)             # 20m
    w2 = _appt(AppointmentStatus.waiting, 0, cat=PC.procedure_minor, arrived=-5)         # 30m
    w3 = _appt(AppointmentStatus.waiting, 5, arrived=-1)                                 # no category -> routine

    est = estimate_queue([w3, w2, done, current, w1], NOW)

    assert est.predicted[w1.id] == NOW + timedelta(minutes=35)        # 45 - 10 left on the complex act
    assert est.predicted[w2.id] == NOW + timedelta(minutes=35 + 20)   # + extended ahead
    assert est.predicted[w3.id] == NOW + timedelta(minutes=35 + 20 + 30)
    assert est.delay_minutes == 65                                    # w1 booked 30 min ago, seen in 35
    assert est.overrun_minutes == 0
    assert est.current_category is PC.procedure_complex
    assert est.waiting_categories == [PC.extended, PC.procedure_minor, PC.routine]


def test_estimate_overrunning_session_gets_buffer():
    current = _appt(AppointmentStatus.in_session, -60, started=-25)   # routine 15m, 25 in -> 10 over
    w = _appt(AppointmentStatus.waiting, -10)

    est = estimate_queue([current, w], NOW)

    assert est.overrun_minutes == 10
    assert est.predicted[w.id] == NOW + timedelta(minutes=ai_delay.OVERRUN_BUFFER_MINUTES)
    assert est.delay_minutes == 10 + ai_delay.OVERRUN_BUFFER_MINUTES


def test_prompt_carries_category_labels_only():
    current = _appt(AppointmentStatus.in_session, -60, cat=PC.procedure_complex, started=-50)
    ws = [_appt(AppointmentStatus.waiting, -40 + i, cat=c) for i, c in enumerate([PC.extended, PC.extended, PC.routine])]

    prompt = ai_delay._build_prompt(estimate_queue([current, *ws], NOW))

    assert "Acte médical complexe en cours depuis 50 min (dépassement de 5 min)" in prompt
    assert "3 (2 × Consultation étendue, 1 × Consultation de routine)" in prompt
    assert "procedure_" not in prompt   # labels, not internal enum values


@pytest.fixture
def est():
    w = _appt(AppointmentStatus.waiting, -25)
    return estimate_queue([w], NOW)   # 25 min late


async def test_gemini_message_filled_locally_and_cached(fake_gemini, est):
    prompts = fake_gemini("{doctor} a environ 25 minutes de retard, merci de votre patience.")

    r1 = await delay_insight(("c", "d"), est, "Dr. Amine")
    r2 = await delay_insight(("c", "d"), est, "Dr. Amine")

    assert r1.source == "gemini" and r1.estimated_delay_minutes == 25
    assert r1.apology_message.startswith("Dr. Amine a environ 25 minutes")
    assert r2 == r1 and len(prompts) == 1          # second call served from cache
    assert "Amine" not in prompts[0]               # PII rule: no names in the prompt


async def test_gemini_message_with_wrong_number_is_rejected(fake_gemini, est):
    fake_gemini("{doctor} a environ 40 minutes de retard.")

    r = await delay_insight(("c", "d"), est, "Dr. Amine")

    assert r.source == "heuristic"
    assert r.apology_message == "Dr. Amine a environ 25 minutes de retard. Merci de patienter."


async def test_no_key_uses_default_message(monkeypatch, est):
    monkeypatch.setattr(ai_delay.settings, "GEMINI_API_KEY", None)
    ai_delay._cache.clear()

    r = await delay_insight(("c", "d"), est, "Dr. Amine")

    assert r.source == "heuristic" and r.estimated_delay_minutes == 25


def test_session_start_in_future_does_not_inflate_wait():
    # regression: seed data / clock skew put session_started_at after now -> ETA of 400+ min
    current = _appt(AppointmentStatus.in_session, 0, cat=PC.procedure_complex, started=+300)
    w = _appt(AppointmentStatus.waiting, 0)

    est = estimate_queue([current, w], NOW)

    assert est.predicted[w.id] == NOW + timedelta(minutes=45)   # treated as just started


# --- accuracy feedback loop (step 3) ----------------------------------------
def _seen(first_eta_min, started_min):
    """Patient told they'd go in at NOW+first_eta_min, actually went in at NOW+started_min."""
    a = _appt(AppointmentStatus.completed, -120, started=started_min, took=15)
    a.first_predicted_start = NOW + timedelta(minutes=first_eta_min)
    return a


def test_prediction_accuracy_mae_bias_and_hit_rate():
    appts = [
        _seen(-60, -56),   # +4  late
        _seen(-40, -46),   # -6  early
        _seen(-30, -18),   # +12 late, outside ±10
        _appt(AppointmentStatus.waiting, 0),                          # not started -> excluded
        _appt(AppointmentStatus.completed, -90, started=-90, took=15),  # no first ETA -> excluded
    ]

    acc = ai_delay.prediction_accuracy(appts)

    assert acc.sample_size == 3
    assert acc.mae_minutes == round(22 / 3, 1)      # 7.3
    assert acc.bias_minutes == round(10 / 3, 1)     # 3.3 -> seen later than announced
    assert acc.within_10_min_pct == 67


def test_prediction_accuracy_empty_day():
    acc = ai_delay.prediction_accuracy([_appt(AppointmentStatus.waiting, 0)])
    assert (acc.sample_size, acc.mae_minutes, acc.bias_minutes, acc.within_10_min_pct) == (0, None, None, None)
