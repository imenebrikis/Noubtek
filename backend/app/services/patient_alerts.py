"""
Proactive patient alerts: tell a waiting patient when their ETA moves a lot.

- apply_alerts(): pure detection, run on every queue recalculation (transitions, walk-ins,
  tracker polls — so a session overrunning with no clicks still triggers it). Compares the
  live ETA with `notified_start` (what the patient was last told); a shift of
  ALERT_THRESHOLD_MINUTES or more fires an alert and becomes the new reference.
- alert_message(): French text, generated lazily when the tracker reads it so transitions
  stay fast. Gemini sees only the direction and minutes; `{heure}` is filled locally.
- send_alert_sms(): pushes fired alerts through the NotificationService (background task);
  greeting, time and tracker link are added locally, never sent to Gemini.
"""
from dataclasses import dataclass
from datetime import datetime

from app.config import settings
from app.localtime import aware, hhmm
from app.models import Appointment
from app.services.ai_delay import QueueEstimate, gemini_message
from app.services.notifications import notifier

ALERT_THRESHOLD_MINUTES = 10


def tracker_url(token) -> str:
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}/patient/{token}"


def apply_alerts(appts: list[Appointment], est: QueueEstimate, now: datetime) -> list[Appointment]:
    """Mutates waiting appointments in `est.predicted`; returns the ones that just got an alert."""
    fired = []
    for a in appts:
        new = est.predicted.get(a.id)
        if new is None:
            continue
        if a.notified_start is None:
            # baseline: the first ETA they were given — no alert for joining the line
            a.notified_start = a.first_predicted_start or new
            continue
        shift = round((new - aware(a.notified_start)).total_seconds() / 60)
        if abs(shift) >= ALERT_THRESHOLD_MINUTES:
            a.notified_start, a.alert_shift_minutes, a.alert_at = new, shift, now
            fired.append(a)
    return fired


@dataclass
class AlertText:
    message: str
    source: str  # 'gemini' | 'heuristic'


def _default_text(shift: int) -> str:
    if shift < 0:
        return f"Bonne nouvelle : votre passage est avancé d'environ {-shift} minutes. Nouvelle heure estimée : {{heure}}."
    return f"Votre passage est retardé d'environ {shift} minutes. Nouvelle heure estimée : {{heure}}. Merci de votre patience."


def _prompt(shift: int) -> str:
    direction = "avancé (bonne nouvelle, le patient doit se tenir prêt)" if shift < 0 else "retardé"
    return (
        "Tu rédiges une notification courte (1 à 2 phrases, en français) pour un patient "
        "qui attend son tour dans une clinique médicale générale. Ton rassurant et clair. "
        f"Son passage est {direction} d'exactement {abs(shift)} minutes. "
        "Indique la nouvelle heure estimée uniquement avec le texte exact {heure} (remplacé ensuite). "
        "Ne cite aucun nom, aucune spécialité, aucun diagnostic. "
        'Réponds UNIQUEMENT en JSON valide, sans texte autour, au format exact: {"message": "<texte>"}'
    )


# shift minutes -> template; the prompt depends on nothing else, so this never goes stale
# ponytail: per-process and unbounded — fine, keys are a few dozen ints
_templates: dict[int, AlertText] = {}


async def alert_message(shift: int, new_start: datetime) -> AlertText:
    tpl = _templates.get(shift)
    if tpl is None:
        tpl = AlertText(_default_text(shift), "heuristic")
        for _ in range(2):  # the model sometimes drops the number; one retry fixes most
            msg = await gemini_message(_prompt(shift))
            # our numbers, not the model's: must state the shift and keep the placeholder
            if msg and str(abs(shift)) in msg and "{heure}" in msg:
                tpl = AlertText(msg, "gemini")
                break
        _templates[shift] = tpl
    return AlertText(tpl.message.replace("{heure}", hhmm(new_start)), tpl.source)


@dataclass
class PendingAlert:
    """Plain values captured before the response — the background task outlives the DB session."""
    phone: str
    first_name: str
    shift: int
    new_start: datetime
    tracking_token: str


def pending_alerts(fired: list[Appointment], contacts: dict) -> list[PendingAlert]:
    """contacts: patient_id -> (full_name, phone). Patients without a phone are skipped."""
    out = []
    for a in fired:
        name, phone = contacts.get(a.patient_id, (None, None))
        if phone:
            out.append(PendingAlert(phone, name.split(" ")[0], a.alert_shift_minutes, a.notified_start,
                                    str(a.tracking_token)))
    return out


async def send_alert_sms(pending: list[PendingAlert]) -> None:
    for p in pending:
        text = await alert_message(p.shift, p.new_start)
        await notifier.send_sms(
            p.phone, f"Bonjour {p.first_name}. {text.message} Suivi : {tracker_url(p.tracking_token)}",
        )
