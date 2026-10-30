"""
Dynamic queue-delay engine (see updated CLAUDE.md: Gemini Pro for delay + FR copy).

PII rule (GEMINI.md still applies): the prompt carries ONLY timing numbers and the
doctor's display name — never a patient name, phone, or any patient data.

Safety: if GEMINI_API_KEY is missing or the call fails for any reason, we fall back
to a simple heuristic (waiting_count * 15 min) + a default French apology, so the
queue endpoints never crash.
"""
import json
from dataclasses import dataclass

import httpx

from app.config import settings

GEMINI_MODEL = "gemini-3-1-pro"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
PER_PATIENT_MINUTES = 15  # heuristic fallback pacing


@dataclass
class DelayResult:
    estimated_delay_minutes: int
    apology_message: str
    source: str  # 'gemini' | 'heuristic'


def _default_apology(doctor: str, delay: int) -> str:
    if delay <= 0:
        return "La file est à l'heure. Merci de votre patience."
    return f"Le {doctor} a environ {delay} minutes de retard. Merci de patienter."


def _heuristic(snapshot: dict) -> DelayResult:
    delay = max(0, snapshot["waiting_count"] * PER_PATIENT_MINUTES)
    return DelayResult(delay, _default_apology(snapshot["doctor"], delay), "heuristic")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.removeprefix("json").strip()
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def _build_prompt(s: dict) -> str:
    return (
        "Tu es un assistant de gestion de file d'attente pour une clinique médicale. "
        "Analyse la dérive de rythme du médecin et estime le retard actuel de la file. "
        "Réponds UNIQUEMENT en JSON valide, sans aucun texte autour, au format exact:\n"
        '{"estimated_delay_minutes": <entier>, "apology_message": "<message en français, '
        'concis et empathique, pour les patients>"}\n'
        f"Médecin: {s['doctor']}\n"
        f"Patients en attente: {s['waiting_count']}\n"
        f"Consultation en cours depuis (minutes): {s['in_session_elapsed_min']}\n"
        f"Consultations terminées aujourd'hui: {s['completed_count']}\n"
        f"Durée réelle moyenne par consultation (minutes): {s['avg_actual_duration_min']}\n"
        f"Durée prévue par consultation (minutes): {s['baseline_duration_min']}\n"
        "Le message doit mentionner le médecin et le retard estimé en minutes."
    )


async def compute_delay(snapshot: dict) -> DelayResult:
    """snapshot keys: doctor, waiting_count, in_session_elapsed_min, completed_count,
    avg_actual_duration_min, baseline_duration_min. Returns DelayResult (never raises)."""
    key = settings.GEMINI_API_KEY
    if not key:
        return _heuristic(snapshot)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                GEMINI_URL,
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{"parts": [{"text": _build_prompt(snapshot)}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
            )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(_strip_fences(text))
        delay = max(0, int(data["estimated_delay_minutes"]))
        msg = str(data.get("apology_message", "")).strip() or _default_apology(snapshot["doctor"], delay)
        return DelayResult(delay, msg, "gemini")
    except Exception:
        # network error, bad JSON, rate limit, missing field — degrade gracefully
        return _heuristic(snapshot)


if __name__ == "__main__":  # ponytail: smallest runnable check of the fallback
    import asyncio

    snap = {"doctor": "Dr. Amine", "waiting_count": 3, "in_session_elapsed_min": 12,
            "completed_count": 1, "avg_actual_duration_min": 25.0, "baseline_duration_min": 20}
    res = asyncio.run(compute_delay(snap))  # no key -> heuristic
    assert res.source == "heuristic" and res.estimated_delay_minutes == 45, res
    assert "Dr. Amine" in res.apology_message and "45" in res.apology_message, res
    print("fallback OK:", res)
