# Noubtek; ClinicFlow

> Eliminating waiting room friction for dental clinics — one queue at a time.

🌟 **Highlights**
- Live queue dashboard — receptionists update patient status in real time
- AI delay prediction — tells patients their *real* appointment time, not the scheduled one
- Patient live-tracker — a personal link showing exact queue position, no app needed
- Automated WhatsApp/SMS alerts — patients are notified before they even leave home
- Owner analytics — average delay, revenue lost to no-shows, peak bottleneck hours
- Multi-tenant — built for many clinics, strict data isolation between them
- Bilingual — French and English throughout

---

## Overview

Dental clinics in Algeria run on paper schedules and optimism. A single overrun procedure cascades into an hour of delays, frustrated patients crowding the waiting room, and receptionists fielding complaints instead of doing their job.

Noubtek gives clinics a real-time queue engine, an AI layer that predicts how late each doctor is running today (based on their own historical pace), and a patient-facing tracker so people can wait at home instead of in a plastic chair.

It's a B2B SaaS — clinics subscribe, their patients use it for free via a personal link sent at booking.

**Who made this:** Built by Imane, a CS student in Algeria, as a graduation-adjacent project targeting a gap in local healthtech infrastructure.

**How it fits the ecosystem:** Think a lighter, clinic-specific alternative to full practice management systems — focused entirely on the waiting room problem rather than billing, records, or insurance.

---

## What it looks like

**Receptionist moves a patient to "In Session":**
```
PATCH /appointments/{id}/status
{ "status": "in_session" }

→ Timestamps the transition automatically
→ Recalculates predicted start times for everyone behind them
→ Sends WhatsApp alert to the next patient
```

**Patient checks their tracker link:**
```
GET /track/a3f9c21b-...

→ "2 patients ahead of you"
→ "Estimated start: 14:45"
→ Updates automatically every 30 seconds
```

**Owner checks today's performance:**
```
GET /analytics/summary

→ Average delay: 23 min
→ No-shows today: 2 (revenue lost: 6,000 DZD)
→ Peak bottleneck: 10:00–11:30
```

---

## ⬇️ Getting started

Requires Python 3.12+ and a free [Neon](https://neon.tech) Postgres project.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in your DATABASE_URL and JWT_SECRET
$env:PYTHONPATH = "."
alembic upgrade head
uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs` — no frontend needed to explore the API.

Frontend (SvelteKit) setup instructions coming once Phase 1b is complete.

---

## Status

 **In active development**

| Phase | Description | Status |
|---|---|---|
| 0 | Backend scaffold + database | ✅ Done |
| 1 | Auth, multi-tenancy, RBAC | ✅ Done |
| 1b | SvelteKit login + route guards | 🔜 Next |
| 2 | Queue API + receptionist dashboard | ⏳ |
| 3 | Patient live-tracker | ⏳ |
| 4 | AI delay prediction engine | ⏳ |
| 5 | WhatsApp/SMS notifications | ⏳ |
| 6 | Owner BI dashboard | ⏳ |

---

## Feedback & ideas

This is being built in public. If you're a clinic owner, receptionist, or patient in Algeria with thoughts on the waiting room problem — open an issue or start a discussion. Real feedback shapes real features.

---

## Stack

FastAPI · PostgreSQL (Neon) · SQLAlchemy 2.0 · SvelteKit 2 · Svelte 5 · Tailwind v4 · Groq (AI copy generation)
