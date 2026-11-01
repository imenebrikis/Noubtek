Markdown
# Noubtek (نوبةك)

> **Eliminating waiting room friction for private healthcare practices in Algeria — one queue at a time.**

Noubtek is a B2B SaaS queue management engine that replaces chaotic waiting rooms and paper schedules with AI-predicted wait times, dynamic receptionist control, and real-time patient tracking.

---

## 🌟 Key Features

- **Live Receptionist Dashboard:** Complete queue control — check in walk-ins, manage status transitions (`checked_in`, `in_session`, `completed`), and re-book follow-ups instantly.
- **AI Delay Prediction Engine:** Real-time dynamic delay calculation based on historic doctor pace, current consultation durations, and active bottlenecks.
- **Patient Live Tracker (`/patient/[token]`):** Zero-install, web-based tracker showing live queue position, updated estimated start times, and direct status alerts.
- **Algeria-Optimized Booking (`/book`):** Clean two-column desktop/mobile booking experience with phone number validation built specifically for Algerian formats.
- **Persistent Session State:** Smart client-side store keeping track of active doctor slots and recent patient tracking links across navigation steps.
- **Fully Tested Backend:** Validated with **53 automated unit and integration tests** covering concurrency, auth, queue logic, and edge-case handling.

---

## 🛠️ Tech Stack

- **Frontend:** SvelteKit 2 (Svelte 5 Runes) · Tailwind CSS v4 · Deployed on **Vercel**
- **Backend:** FastAPI (Python 3.12) · SQLAlchemy 2.0 · Deployed on **Render**
- **Database:** PostgreSQL (Neon Serverless) · Alembic Migrations
- **Testing:** Pytest (53/53 tests passing)

---

## 🏗️ Core API & Data Flow

1. **Patient Booking:**
   `POST /api/v1/appointments/book`
   Creates a appointment record and outputs a unique patient tracking token.

2. **Queue State Transition:**
   `PATCH /api/v1/queue/{id}/status`
   Timestamps state changes and automatically recalculates predicted ETAs for every patient further down the queue.

3. **Live Tracker Polling:**
   `GET /api/v1/queue/track/{token}`
   Delivers current queue position, doctor status, and AI-predicted consultation time.

---

## 🚀 Local Development Setup

### Prerequisites
- Python 3.12+
- Node.js 18+
- PostgreSQL database instance (or free [Neon.tech](https://neon.tech) project)

### 1. Backend (FastAPI)
```bash
cd backend
python -m venv .venv

# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # Update DATABASE_URL and JWT_SECRET

$env:PYTHONPATH = "."
alembic upgrade head
uvicorn app.main:app --reload
API Interactive Docs: http://localhost:8000/docs

Run Backend Tests: pytest

2. Frontend (SvelteKit)
Bash
cd frontend
npm install
cp .env.example .env   # Set PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
🗺️ Post-MVP Roadmap (Next 90 Days)
Local Network Notifications: WhatsApp and SMS triggers formatted for Algerian mobile carriers (Mobilis, Djezzy, Ooredoo).

Reception QR Check-ins: Self-check-in kiosk mode for patients arriving on-site.

Multi-Specialty Polyclinic Portal: Multi-doctor dashboard switcher and department-level patient routing.

Analytics & BI Suite: Reporting on peak bottleneck hours, practitioner delay averages, and lost revenue metrics.

👤 Author
Built by Imane — Lead Developer & Architect (Algeria).
