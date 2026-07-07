# ClinicFlow — Project Brief for Claude Code

Multi-tenant SaaS that eliminates dental waiting-room friction: real-time queue
tracking, data-driven delay prediction, and automated patient notifications.

## Stack
- **Backend (owns everything):** FastAPI (Python 3.12), SQLAlchemy 2.0 + Alembic,
  PostgreSQL. Own JWT auth (access + refresh), argon2 or bcrypt password hashing.
- **Frontend (UI only):** SvelteKit 2 + **Svelte 5 (runes)** + Tailwind CSS v4.
  Talks to the FastAPI API over REST. SSR used for the public patient tracker.
- **AI copy:** Groq free tier (OpenAI-compatible, `llama-3.3-70b-versatile`),
  used OFFLINE only (see AI section).
- **Hosting (all free tier):** Neon (Postgres) + Render or Fly.io (API) +
  Cloudflare Pages or Vercel (frontend).
- **Dev env:** Windows / PowerShell. Use PowerShell-safe commands.
- **i18n:** FR / EN from day one, on both API messages and UI.

## The tenancy rule (most important thing in this file)
There is NO database RLS anymore. Tenant isolation is 100% application-layer.
**Every query that reads or writes a tenant table MUST filter by `clinic_id`.**
A single missing filter leaks one clinic's data into another. Enforce it with a
dependency + a scoped helper, and never bypass them:

```python
# auth extracts this from the JWT on every request
@dataclass
class TenantContext:
    user_id: uuid.UUID
    clinic_id: uuid.UUID
    role: ClinicRole

def get_context(token=Depends(oauth2)) -> TenantContext: ...          # FastAPI dependency
def require_role(*roles): ...                                          # 403 if ctx.role not in roles

# ALL tenant reads go through a scoped query so clinic_id can't be forgotten:
def scoped(ctx: TenantContext, model):
    return select(model).where(model.clinic_id == ctx.clinic_id)
```

Rules that follow from this:
- No endpoint queries a tenant table without `ctx.clinic_id`.
- Writes set `clinic_id = ctx.clinic_id` server-side — never trust a client-supplied clinic_id.
- Users can never change their own role. Membership edits are admin-only, server-validated.

## RBAC
- `admin` (owner): analytics, staff/settings, full appointment control.
- `receptionist`: queue, patients, appointment statuses.
- `doctor`: **read-only** view of their own queue + notes.
Gate every route with `require_role(...)`.

## Queue state machine
`scheduled → waiting → in_session → completed` (or `no_show` / `cancelled`).
ALL status changes go through **one** method, `AppointmentService.transition()`,
which in a single transaction: (1) updates `status`, (2) stamps the matching
actual timestamp (arrived_at / session_started_at / completed_at), and (3) inserts
an `appointment_events` row. Nothing else writes `appointment_events`. This log is
the future AI training set — it must never be skipped.

## AI Tardiness Engine — heuristic, NOT an LLM
Prediction is math on your own data, done in Python:
`predicted_start = scheduled_start + rolling_avg_delay`, averaged over recent
COMPLETED appointments segmented by (doctor_member_id, procedure_type_id, weekday,
time-of-day bucket). Write it to `appointments.predicted_start`; recompute on each
relevant transition. UI shows `predicted_start` if set, else `scheduled_start`.
Phase 2 (only when asked): swap the average for a regression model on
`appointment_events`. Do NOT scaffold any ML training pipeline before then.

## Notifications — free LLM, zero PII
Groq is used **offline, once**, by a maintenance script that generates a small
library of FR/EN message templates into `notification_templates` (placeholders
like `{patient_name}`, `{time}`, `{doctor}`). At send time the app fills the
placeholders LOCALLY — **no patient data is ever sent to the LLM** (free tiers may
train on prompts; this is a health app). Delivery goes through one abstracted
`send_notification()` (WhatsApp Business API primary candidate for Algeria, SMS
provider as fallback).

## SvelteKit gotchas (Claude Code often defaults to old syntax — don't)
- Use **Svelte 5 runes**: `$state`, `$derived`, `$effect`, `$props()`. No legacy
  `export let` for props, no `writable` stores for component state.
- SvelteKit 2: import reactive app state from **`$app/state`**, not the deprecated
  `$app/stores`.
- TypeScript throughout. No business logic in `+page.server.ts` beyond calling the
  FastAPI API / SSR fetch — SvelteKit is not the backend.

## Working style
- Surgical, minimal-change edits. Touch only what the task needs.
- Before any large refactor or schema change, state the plan and pause for confirmation.
- Keep `clinic_id` explicit in every data-access path. Defense in depth.
- Migrations are additive (Alembic autogenerate + review); never rewrite the initial one.

## Build phases (see BUILD_PLAN.md for the prompt sequence)
0. Backend foundation (FastAPI + DB + models + first migration)
1. Auth + clinic onboarding + TenantContext/RBAC + SvelteKit login/guards
2. Queue API + receptionist dashboard (state machine)
3. Patient live-tracker (public API endpoint + SSR route)
4. Heuristic prediction engine
5. Notifications (offline Groq template gen + send abstraction)
6. Owner BI dashboard


