# Noubtek — Project Instructions for Claude Code

Multi-tenant B2B SaaS for medical clinic queue tracking, delay prediction, and automated patient alerts.

## Stack
- Backend: FastAPI (Python 3.12), SQLAlchemy 2.0, Alembic, PostgreSQL (Neon).
- Frontend: SvelteKit 2 + Svelte 5 (runes) + Tailwind CSS v4 + shadcn-svelte.
- AI: Gemini Pro API (dynamic queue delay prediction & French messaging).
- Dev: Windows / Bun.

## Mandatory Svelte 5 Rules
- Reactivity: $state(), $derived(), $effect(). NO legacy writable stores.
- Props: $props(). NO legacy "export let".
- App state: import from $app/state (SvelteKit 2).

## Strict Multi-Tenancy
- Application-layer tenancy ONLY.
- Every tenant table query MUST filter by `clinic_id`.
- Server-side routes MUST enforce `TenantContext` extracted from JWT.

## State Machine
- `scheduled -> waiting -> in_session -> completed`.
- All transitions go through `AppointmentService.transition()` to log `appointment_events`.

## Survival Deadline Rules
- Primary target: A fully polished demo loop for Saturday night.
- Focus exclusively on: DB seeding, Login, Receptionist Dashboard, Patient Tracker `/patient/[id]`, and Gemini Pro queue recalculation.
