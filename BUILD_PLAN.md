# ClinicFlow — Build Plan & Prompt Sequence (FastAPI + SvelteKit)

## Fable 5 window
Fable 5 is included in your plan's weekly limits **through July 7**; after that it
bills as usage credits. Front-load the heavy generation — backend scaffold, auth,
models→migration, the prediction heuristic — into today/tomorrow on Fable 5. Leave
component polish, styling, and i18n copy for Sonnet afterward. Pick the model per
task inside Claude Code.

## Repo layout (monorepo)
```
clinicflow/
  backend/    # FastAPI: app/, models.py, alembic/, services/, routers/, tests/
  frontend/   # SvelteKit 2 app
  CLAUDE.md   # at root
```

## One-time setup before prompting
- **Neon:** create a free Postgres project, copy the connection string.
- **Groq:** create a free API key at the Groq console (no credit card). Note the
  OpenAI-compatible base URL and model `llama-3.3-70b-versatile`. Only needed at Phase 5.
- Put `models.py` into `backend/app/`.

## Phases (each depends on the previous — don't reorder)

**Phase 0 — Backend foundation.** FastAPI app skeleton, settings via env,
SQLAlchemy engine/session, Alembic wired to `models.py`, first migration applied to
Neon, `/health` endpoint. Verify the tables exist.

**Phase 1 — Auth + tenancy + RBAC.** Register creates User + Clinic + an `admin`
ClinicMember in one transaction. Login issues JWT access + refresh; passwords via
argon2/bcrypt. Implement `TenantContext` dependency, `get_context`, `require_role`,
and the `scoped()` helper from CLAUDE.md. Add an invite flow for
receptionists/doctors. Frontend: SvelteKit login page, token storage, and route
guards by role. **Write a test proving Clinic A cannot read Clinic B's rows and a
member cannot elevate their own role.**

**Phase 2 — Queue API + receptionist dashboard.** Clinic-scoped CRUD for patients,
procedure_types, appointments. `AppointmentService.transition()` (status +
timestamp + event, one transaction). SvelteKit dashboard: daily list sorted by
predicted-or-scheduled start, status buttons driving the state machine. Build this
early so real timestamps start feeding the AI.

**Phase 3 — Patient live-tracker.** Public `GET /track/{token}` returns ONLY
sanitized data (position "2 ahead of you", ETA, doctor display_name) — never raw
rows. SvelteKit public SSR route at `/t/[token]`. MVP realtime = 15–30s polling
(WebSocket later).

**Phase 4 — Heuristic prediction.** Python service computing rolling average delay
per (doctor, procedure, weekday, time bucket); writes `predicted_start`; re-runs on
each transition. Dashboard reflows to predicted times.

**Phase 5 — Notifications.** Offline script calls Groq to generate FR/EN templates
into `notification_templates`. Runtime `send_notification()` fills placeholders
locally and dispatches via a provider abstraction (WhatsApp primary / SMS fallback).
Trigger on prediction shifts past a threshold and on key status changes.

**Phase 6 — Owner BI dashboard.** Aggregation endpoints over `appointment_events` +
`appointments`: avg daily delay, revenue lost to no-shows (uses
`procedure_types.price`), peak bottleneck hours, actual vs scheduled duration.
SvelteKit charts (e.g. LayerChart / Chart.js).

## Opening prompt sequence for Claude Code

1. **Bootstrap backend:** "Read CLAUDE.md and backend/app/models.py. Scaffold a
   FastAPI project: settings/env, SQLAlchemy 2.0 engine + session, Alembic wired to
   these models, and a /health route. Give me the exact PowerShell commands to
   create the venv, install deps, generate the first migration, and apply it to my
   Neon DATABASE_URL. Plan first, then write files."

2. **Auth + tenancy:** "Implement Phase 1 exactly as CLAUDE.md's tenancy section
   specifies: register (User+Clinic+admin member in one txn), JWT access+refresh,
   argon2 hashing, TenantContext dependency, require_role, and the scoped() helper.
   Then write pytest tests proving cross-tenant isolation and no self-role-escalation."

3. **Bootstrap frontend:** "Scaffold a SvelteKit 2 app with Svelte 5 runes and
   Tailwind v4. Set up an API client to the FastAPI backend, a login page, token
   handling, and role-based route guards. Use $app/state, $props(), and runes — no
   legacy stores. Surgical changes only."

Then proceed phase by phase; after each, ask for a short manual test checklist.

## Decisions to confirm before Phase 3 / 5
- Tracker realtime: polling (recommended for MVP) vs WebSocket.
- Notification channel for Algeria: WhatsApp Business API vs SMS provider.
- Whether doctors get a login at launch or later.

