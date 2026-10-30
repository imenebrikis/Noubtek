# Noubtek — 3-Day Hackathon Sprint Plan

## Thursday (Day 1) — Data & Dashboard Scaffolding
1. Backend: Ensure SQLAlchemy models have `clinic_id`, User, Doctor, Appointment, and AppointmentEvent.
2. Seed: Run `seed_db.py` to create clinic 'Noubtek', user 'judge@noubtek.dz' (pass: 'admin123'), Doctor 'Dr. Amine', and 5 appointments for today.
3. Frontend: Scaffold SvelteKit in `frontend/` using `bun`. Install Tailwind v4 and `shadcn-svelte`.
4. UI: Build `/dashboard` route matching Mosa Health design (Header, 3 Stat Cards, Patient Queue Table with status transition buttons).

## Friday (Day 2) — AI & Patient Tracker
1. Backend AI: Create route `/queue/transition` that updates appointment state and calls Gemini Pro to calculate new `estimated_delay_minutes` and an empathetic French apology message.
2. Patient Tracker: Build public mobile route `/patient/[id]` displaying patient queue position, AI wait time, and French apology card.

## Saturday (Day 3) — Deploy & Demo Video
1. Deploy Backend (Render) & Frontend (Vercel).
2. Record 3-minute hackathon demo video.
3. Submit platform application form before 23:59.
