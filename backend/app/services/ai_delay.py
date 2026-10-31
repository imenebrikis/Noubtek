"""
Queue delay engine.

- Numbers come from a fast local heuristic (estimate_queue): deterministic, no network.
  Wait = remaining time of the session in progress + the procedure-category minutes of
  every patient ahead (see PROCEDURE_MINUTES in models) × the doctor's pace today.
- Gemini only writes the French patient-facing message (delay_insight), cached per doctor.

PII rule (.gemini/GEMINI.md): the prompt carries ONLY timing numbers and generic category
labels ("Consultation étendue", "Acte médical en cours"). The doctor's name is
a `{doctor}` placeholder filled locally — no patient or clinic data ever leaves the server.

Safety: no GEMINI_API_KEY, a failed call, or a message that doesn't match our number →
default French message. Nothing here ever raises into an endpoint.
"""
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from app.config import settings
from app.localtime import aware as _aware
from app.models import PROCEDURE_MINUTES, Appointment, AppointmentStatus, ProcedureCategory

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PACE_MIN, PACE_MAX = 0.75, 1.50  # one odd consultation can't swing the whole queue
OVERRUN_BUFFER_MINUTES = 5  # a session past its expected length still needs *some* time
INSIGHT_TTL_SECONDS = 120   # tracker polls every 15s — don't call Gemini per poll


def queue_order(a: Appointment) -> datetime:
    # same ordering as the staff board: arrival, then schedule
    return _aware(a.arrived_at or a.scheduled_start)


# generic, specialty-agnostic labels — the only "clinical" context Gemini ever sees
CATEGORY_LABELS: dict[ProcedureCategory, str] = {
    ProcedureCategory.routine: "Consultation de routine",
    ProcedureCategory.extended: "Consultation étendue",
    ProcedureCategory.procedure_minor: "Acte médical court",
    ProcedureCategory.procedure_complex: "Acte médical complexe",
}


def category(a: Appointment) -> ProcedureCategory:
    # column default only applies on INSERT — unsaved objects read None
    return ProcedureCategory(a.procedure_category or ProcedureCategory.routine)


def expected_minutes(a: Appointment) -> int:
    return PROCEDURE_MINUTES[category(a)]


def pace_factor(appts: list[Appointment]) -> tuple[float, int]:
    """sum(actual minutes) / sum(scheduled minutes) over today's completed consultations,
    clamped to [PACE_MIN, PACE_MAX]. 1.0 when nothing is completed yet. -> (factor, completed count)"""
    done = [
        a for a in appts
        if a.status is AppointmentStatus.completed and a.session_started_at and a.completed_at
    ]
    actual = sum((_aware(a.completed_at) - _aware(a.session_started_at)).total_seconds() / 60 for a in done)
    planned = sum(a.scheduled_duration_minutes or expected_minutes(a) for a in done)
    if not planned:
        return 1.0, len(done)
    return min(PACE_MAX, max(PACE_MIN, actual / planned)), len(done)


@dataclass
class QueueEstimate:
    delay_minutes: int                       # how late the next waiting patient will be seen
    waiting_count: int
    in_session_elapsed_min: int | None
    overrun_minutes: int = 0                 # how far the active session is past its expected length
    current_category: ProcedureCategory | None = None
    waiting_categories: list[ProcedureCategory] = field(default_factory=list)  # in queue order
    pace_factor: float = 1.0                 # doctor's actual/scheduled time today (clamped)
    completed_count: int = 0                 # consultations the pace is measured on
    predicted: dict[uuid.UUID, datetime] = field(default_factory=dict)  # waiting appt id -> start


def estimate_queue(appts: list[Appointment], now: datetime) -> QueueEstimate:
    """One doctor's appointments for today -> predicted start of every waiting patient.

    start(k-th waiting) = now + remaining(active session) + pace × sum(expected minutes of the k ahead)
    remaining(active)   = expected - elapsed, or OVERRUN_BUFFER_MINUTES once it overruns
    pace                = pace_factor(today's completed); scales only the waiting line
    """
    pace, completed_count = pace_factor(appts)
    current = next((a for a in appts if a.status is AppointmentStatus.in_session and a.session_started_at), None)
    elapsed = overrun = 0.0
    t = now
    if current:
        # clamp: a start in the future (clock skew, bad data) must not inflate the wait
        elapsed = max(0.0, (now - _aware(current.session_started_at)).total_seconds() / 60)
        left = expected_minutes(current) - elapsed
        overrun = max(0.0, -left)
        t = now + timedelta(minutes=left if left > 0 else OVERRUN_BUFFER_MINUTES)

    waiting = sorted((a for a in appts if a.status is AppointmentStatus.waiting), key=queue_order)
    predicted = {}
    for a in waiting:
        predicted[a.id] = t
        t += timedelta(minutes=expected_minutes(a) * pace)

    delay = 0
    if waiting:
        delay = max(0, round((predicted[waiting[0].id] - _aware(waiting[0].scheduled_start)).total_seconds() / 60))
    return QueueEstimate(
        delay_minutes=delay,
        waiting_count=len(waiting),
        in_session_elapsed_min=round(elapsed) if current else None,
        overrun_minutes=round(overrun),
        current_category=category(current) if current else None,
        waiting_categories=[category(a) for a in waiting],
        pace_factor=pace,
        completed_count=completed_count,
        predicted=predicted,
    )


ACCURATE_WITHIN_MINUTES = 10


@dataclass
class Accuracy:
    sample_size: int
    mae_minutes: float | None        # mean |actual start - first prediction|
    bias_minutes: float | None       # mean signed error: > 0 = patients seen later than announced
    within_10_min_pct: int | None    # share of patients whose first ETA was within ±10 min


def prediction_accuracy(appts: list[Appointment]) -> Accuracy:
    """Feedback loop: how good was the ETA each patient was given when they joined the line?"""
    errors = [
        (_aware(a.session_started_at) - _aware(a.first_predicted_start)).total_seconds() / 60
        for a in appts
        if a.first_predicted_start and a.session_started_at
    ]
    if not errors:
        return Accuracy(0, None, None, None)
    n = len(errors)
    return Accuracy(
        sample_size=n,
        mae_minutes=round(sum(abs(e) for e in errors) / n, 1),
        bias_minutes=round(sum(errors) / n, 1),
        within_10_min_pct=round(100 * sum(abs(e) <= ACCURATE_WITHIN_MINUTES for e in errors) / n),
    )


@dataclass
class DelayResult:
    estimated_delay_minutes: int
    apology_message: str
    source: str  # 'gemini' | 'heuristic'


def _default_message(doctor: str, delay: int) -> str:
    if delay <= 0:
        return "La file est à l'heure. Merci de votre patience."
    return f"{doctor} a environ {delay} minutes de retard. Merci de patienter."


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.removeprefix("json").strip()
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def pace_label(est: QueueEstimate) -> str:
    if not est.completed_count:
        return "pas encore mesuré (aucune consultation terminée)"
    pct = round((est.pace_factor - 1) * 100)
    if pct == 0:
        return "conforme au planning"
    return f"{pct:+d}% de temps par consultation"


def _build_prompt(est: QueueEstimate) -> str:
    current = (
        f"{CATEGORY_LABELS[est.current_category]} en cours depuis {est.in_session_elapsed_min} min"
        + (f" (dépassement de {est.overrun_minutes} min)" if est.overrun_minutes else "")
        if est.current_category else "Aucune consultation en cours"
    )
    mix = ", ".join(f"{n} × {CATEGORY_LABELS[c]}" for c, n in Counter(est.waiting_categories).items()) or "aucun"
    return (
        "Tu rédiges un court message pour les patients d'une salle d'attente de clinique médicale générale. "
        "Ton concis, rassurant et empathique, 1 à 2 phrases, en français. "
        "Reste général : n'invente aucune spécialité, aucun diagnostic, et ne cite pas de patient. "
        "Tu peux expliquer le retard par la nature de l'acte en cours ou par le rythme du jour "
        "(ex. le médecin prend plus de temps avec chaque patient), sans citer de pourcentage. "
        "Désigne le médecin uniquement par le texte exact {doctor} (remplacé ensuite par son nom, titre inclus : "
        "n'écris jamais « le docteur » ou « Dr » devant). "
        f"Le message doit mentionner exactement {est.delay_minutes} minutes de retard"
        + (" (ou dire que la file est à l'heure)" if est.delay_minutes == 0 else "")
        + ". Réponds UNIQUEMENT en JSON valide, sans texte autour, au format exact: "
        '{"message": "<texte>"}\n'
        f"Situation actuelle: {current}\n"
        f"Patients en attente: {est.waiting_count} ({mix})\n"
        f"Rythme du jour: {pace_label(est)}\n"
    )


async def gemini_message(prompt: str) -> str | None:
    """POST a PII-free prompt that asks for {"message": ...}; None on any failure or no key."""
    if not settings.GEMINI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                GEMINI_URL.format(model=settings.GEMINI_MODEL),
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
            )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return str(json.loads(_strip_fences(text))["message"]).strip() or None
    except Exception:
        # network error, bad JSON, rate limit, missing field — degrade gracefully
        return None


async def _gemini_message(est: QueueEstimate) -> str | None:
    msg = await gemini_message(_build_prompt(est))
    # the number is ours, not the model's: reject a message that states a different delay
    if msg and est.delay_minutes > 0 and str(est.delay_minutes) not in msg:
        return None
    return msg


# (clinic_id, doctor_id) -> (expires_at, (delay, waiting_count, current_category), result)
# ponytail: per-process dict; move to Redis/DB if we run >1 worker
_cache: dict[tuple, tuple[float, tuple, DelayResult]] = {}


async def delay_insight(key: tuple, est: QueueEstimate, doctor: str) -> DelayResult:
    """French message for the queue state. Reuses the cached one while the numbers are unchanged."""
    fingerprint = (est.delay_minutes, est.waiting_count, est.current_category)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic() and hit[1] == fingerprint:
        return hit[2]

    msg = await _gemini_message(est)
    result = (
        DelayResult(est.delay_minutes, msg.replace("{doctor}", doctor), "gemini")
        if msg else DelayResult(est.delay_minutes, _default_message(doctor, est.delay_minutes), "heuristic")
    )
    # failures are cached too, so a Gemini outage doesn't add 5s to every poll
    _cache[key] = (time.monotonic() + INSIGHT_TTL_SECONDS, fingerprint, result)
    return result
