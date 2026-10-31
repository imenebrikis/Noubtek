<script lang="ts">
	import { page } from '$app/state';
	import { remember } from '#lib/patientSession.svelte.js';

	// --- clinic time ------------------------------------------------------------
	// The API stores UTC; patients see and pick Algiers wall-clock time.
	const CLINIC_TZ = 'Africa/Algiers';
	const CLINIC_UTC_OFFSET = '+01:00'; // Algeria has no DST — keep in sync with backend CLINIC_TIMEZONE
	const MAX_DAYS_AHEAD = 60; // backend limit
	const SLOT_TAKEN = "Ce créneau vient d'être réservé, veuillez en choisir un autre.";

	// calendar days are 'YYYY-MM-DD' strings; arithmetic in UTC so the browser's zone never leaks in
	const todayInClinic = () =>
		new Intl.DateTimeFormat('en-CA', { timeZone: CLINIC_TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
	const nowHHMM = () =>
		new Intl.DateTimeFormat('fr-FR', { timeZone: CLINIC_TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date());
	const addDays = (ymd: string, n: number) => {
		const [y, m, d] = ymd.split('-').map(Number);
		return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
	};
	const noonUTC = (ymd: string) => new Date(`${ymd}T12:00:00Z`);
	const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
	const fmtUTC = (ymd: string, opts: Intl.DateTimeFormatOptions) =>
		new Intl.DateTimeFormat('fr-FR', { timeZone: 'UTC', ...opts }).format(noonUTC(ymd));

	// --- visit types: design labels -> backend ProcedureCategory (minutes match PROCEDURE_MINUTES)
	const CARDS = [
		{ label: 'Consultation de Routine', minutes: 15, category: 'routine' },
		{ label: 'Approfondie', minutes: 30, category: 'procedure_minor' },
		{ label: 'Consultation de suivi', minutes: 15, category: 'routine' },
		{ label: 'Bilan de santé', minutes: 45, category: 'procedure_complex' }
	] as const;

	type Booking = {
		appointment_id: string;
		tracking_token: string;
		tracker_url: string;
		patient_name: string;
		doctor_name: string;
		procedure_category: string;
		expected_duration_minutes: number;
		booked_time: string;
		estimated_start: string;
		queue_position: number;
		status: string;
	};

	// --- form state ---------------------------------------------------------------
	const today = todayInClinic();
	// the clinic shares /book?doctor=<id>; doctor ids are UUIDs, so there is no meaningful default
	let doctor_id = $state(page.url.searchParams.get('doctor') ?? '');
	let category = $state('Consultation de Routine');
	let date = $state(today);
	let time = $state('');
	let patient_name = $state('');
	let phone = $state('');
	let email = $state('');
	let isLoading = $state(false);
	let errorMessage = $state('');
	let successData = $state<Booking | null>(null);

	const card = $derived(CARDS.find((c) => c.label === category) ?? CARDS[0]);

	// --- date strip: 6 open days per page (closed Sundays, as in the design) ---------
	let weekPage = $state(0);
	const lastBookable = addDays(today, MAX_DAYS_AHEAD);
	const days = $derived.by(() => {
		const out: string[] = [];
		for (let i = 0; out.length < (weekPage + 1) * 6; i++) {
			const ymd = addDays(today, i);
			// ponytail: Sunday closure hardcoded; read clinic opening days once we model them
			if (noonUTC(ymd).getUTCDay() !== 0) out.push(ymd);
		}
		return out.slice(weekPage * 6);
	});
	const monthLabel = $derived(cap(fmtUTC(date, { month: 'long', year: 'numeric' })));

	function pickDate(ymd: string) {
		if (ymd > lastBookable) return;
		date = ymd;
	}

	// --- slots + doctor profile (public endpoint) ----------------------------------
	let slots = $state<string[]>([]);
	let slotsLoading = $state(false);
	let doctorName = $state('');
	let specialty = $state('');
	let openedOnToday = true; // first load only: if today is already over, jump to the next open day

	async function loadSlots(id: string, ymd: string) {
		slotsLoading = true;
		try {
			const res = await fetch(`/api/v1/public/slots?doctor_id=${encodeURIComponent(id)}&date=${ymd}`);
			if (ymd !== date) return; // a newer date was picked while this was in flight
			if (!res.ok) {
				errorMessage =
					res.status === 404 || res.status === 422
						? 'Lien de réservation invalide : médecin introuvable.'
						: 'Impossible de charger les créneaux.';
				slots = [];
				return;
			}
			const body = await res.json();
			remember('doctorId', id); // a doctor the API knows: the navbar keeps linking to it
			doctorName = body.doctor_name ?? '';
			specialty = body.specialty ?? '';
			const cutoff = ymd === today ? nowHHMM() : ''; // today: hide slots already past
			slots = (body.free_slots as string[]).filter((s) => s > cutoff);
			if (!slots.includes(time)) time = '';
			if (openedOnToday && ymd === today && !slots.length) date = days[1];
			openedOnToday = false;
		} catch {
			errorMessage = 'Connexion impossible. Vérifiez votre réseau.';
		} finally {
			slotsLoading = false;
		}
	}

	$effect(() => {
		if (doctor_id) loadSlots(doctor_id, date);
	});

	// --- submit ---------------------------------------------------------------------
	async function submit(e: SubmitEvent) {
		e.preventDefault();
		errorMessage = '';
		if (!time) {
			errorMessage = 'Choisissez un horaire.';
			return;
		}
		isLoading = true;
		try {
			const res = await fetch('/api/v1/booking', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					patient_name,
					phone,
					email: email.trim() || null,
					doctor_id,
					procedure_category: card.category,
					booked_time: `${date}T${time}:00${CLINIC_UTC_OFFSET}` // e.g. 2026-11-12T09:20:00+01:00
				})
			});
			if (res.status === 201) {
				successData = await res.json();
				remember('token', successData!.tracking_token); // navbar "Suivi en direct" -> this booking
				return;
			}
			if (res.status === 409) {
				errorMessage = SLOT_TAKEN;
				time = '';
				loadSlots(doctor_id, date); // drop the slot someone else just took
				return;
			}
			const detail = (await res.json().catch(() => null))?.detail;
			errorMessage =
				typeof detail === 'string'
					? detail
					: res.status === 422
						? 'Vérifiez vos informations (nom, téléphone, e-mail).'
						: 'Une erreur est survenue, veuillez réessayer.';
		} catch {
			errorMessage = 'Connexion impossible. Vérifiez votre réseau.';
		} finally {
			isLoading = false;
		}
	}

	function newBooking() {
		successData = null;
		time = '';
		patient_name = phone = email = errorMessage = '';
		loadSlots(doctor_id, date);
	}

	// --- digital pass formatting ------------------------------------------------------
	function longDateTime(iso: string) {
		const p = Object.fromEntries(
			new Intl.DateTimeFormat('fr-FR', {
				timeZone: CLINIC_TZ, weekday: 'short', day: 'numeric', month: 'long', year: 'numeric',
				hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
			})
				.formatToParts(new Date(iso))
				.map((x) => [x.type, x.value])
		);
		return `${cap(p.weekday)} ${p.day} ${p.month} ${p.year} · ${p.hour}:${p.minute}`;
	}
	// display reference from the slot (NB-DDMM-HHMM), as in the design — not an identifier
	const reference = (iso: string) => {
		const [, mo, d, h, mi] = iso.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/) ?? [];
		return `NB-${d}${mo}-${h}${mi}`;
	};
	const initialWait = (b: Booking) =>
		Math.max(0, Math.round((Date.parse(b.estimated_start) - Date.parse(b.booked_time)) / 60000));

	// --- icons (lucide geometry, inlined: no icon dependency) ---------------------------
	const ICONS = {
		plus: ['M5 12h14', 'M12 5v14'],
		check: ['M20 6 9 17l-5-5'],
		circleCheck: ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'm9 12 2 2 4-4'],
		x: ['M18 6 6 18', 'm6 6 12 12'],
		user: ['M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2', 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0'],
		stethoscope: [
			'M11 2v2', 'M5 2v2', 'M5 3H4a2 2 0 0 0-2 2v4a6 6 0 0 0 12 0V5a2 2 0 0 0-2-2h-1',
			'M8 15a6 6 0 0 0 12 0v-3', 'M22 10a2 2 0 1 1-4 0 2 2 0 0 1 4 0'
		],
		clipboard: [
			'M9 2h6a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z',
			'M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2', 'M9 14h6', 'M12 17v-6'
		],
		calendar: ['M8 2v4', 'M16 2v4', 'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z', 'M3 10h18'],
		sparkles: [
			'M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z',
			'M20 3v4', 'M22 5h-4', 'M4 17v2', 'M5 18H3'
		],
		shield: [
			'M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z',
			'm9 12 2 2 4-4'
		],
		phone: [
			'M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z'
		],
		mail: ['M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z', 'm22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7'],
		chevronRight: ['m9 18 6-6-6-6'],
		message: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
		activity: ['M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2'],
		chevronLeft: ['m15 18-6-6 6-6']
	} satisfies Record<string, string[]>;
</script>

{#snippet icon(name: keyof typeof ICONS, cls = 'h-5 w-5')}
	<svg class={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
		{#each ICONS[name] as d}<path {d} />{/each}
	</svg>
{/snippet}

<svelte:head><title>Prendre rendez-vous · Noubtek</title></svelte:head>

<div class="w-full">
<main class="mx-auto w-full max-w-7xl px-4 py-6 sm:py-8 lg:px-8">
	<div class="grid gap-6 lg:grid-cols-2 lg:gap-8">
		<!-- Left: branded banner (same card as the tracker hero) -->
		<section
			class="flex flex-col rounded-4xl bg-linear-to-r from-[#3b82f6] via-[#1d3fbf] to-[#142a7c] p-6 text-white shadow-[0_20px_50px_-12px_rgba(30,58,138,0.35)] sm:p-8 lg:sticky lg:top-8 lg:min-h-184 lg:self-start lg:p-10"
		>
			<span class="inline-flex w-fit items-center gap-1.5 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-[11px] font-medium">
				<span class="h-1.5 w-1.5 rounded-full bg-emerald-300"></span>
				Prise de rendez-vous en ligne
			</span>

			<div class="mt-8 lg:my-auto lg:py-10">
				<h1 class="text-3xl leading-tight font-extrabold tracking-tight sm:text-4xl lg:text-5xl">
					{successData ? 'Votre place est réservée.' : 'Votre prochain rendez-vous'}
				</h1>
				<p class="mt-3 max-w-md text-base text-white/85">
					{successData
						? 'Gagnez en sérénité : suivez votre passage en direct, sans attendre sur place.'
						: 'Choisissez votre créneau en moins d’une minute. Nous nous occupons du reste.'}
				</p>

				{#if doctorName}
					<div class="mt-6 flex w-fit items-center gap-3 rounded-2xl border border-white/15 bg-white/10 px-4 py-3 backdrop-blur-sm">
						<span class="flex h-10 w-10 items-center justify-center rounded-full bg-white/15">{@render icon('stethoscope', 'h-5 w-5')}</span>
						<div class="leading-tight">
							<p class="font-semibold">{doctorName}</p>
							{#if specialty}<p class="mt-0.5 text-sm text-white/75">{specialty}</p>{/if}
						</div>
					</div>
				{/if}
			</div>

			<ul class="mt-8 hidden space-y-3 text-sm text-white/90 sm:block">
				{#each [
					{ ic: 'message', t: 'Confirmation immédiate par SMS, avec votre lien de suivi.' },
					{ ic: 'activity', t: 'Votre position dans la file, en direct sur votre téléphone.' },
					{ ic: 'sparkles', t: 'Temps d’attente estimé par l’IA, mis à jour en temps réel.' },
					{ ic: 'shield', t: 'Vos informations restent confidentielles.' }
				] as item (item.t)}
					<li class="flex items-start gap-3">
						<span class="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white/10">{@render icon(item.ic as keyof typeof ICONS, 'h-4 w-4')}</span>
						<span class="pt-1">{item.t}</span>
					</li>
				{/each}
			</ul>
		</section>

		<!-- Right: form, then the digital pass -->
		<section
			class="rounded-4xl border border-slate-200/80 p-5 shadow-[0_1px_3px_rgba(15,23,42,0.04)] sm:p-8 lg:p-10 {successData ? 'bg-slate-50' : 'bg-white'}"
		>
		{#if !successData}
			<!-- ===================== Prise de rendez-vous ===================== -->
			<form onsubmit={submit} class="flex flex-col">
				{#if !doctor_id}
					<p role="alert" class="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
						Lien de réservation incomplet. Utilisez le lien envoyé par votre clinique.
					</p>
				{:else}
					<!-- Type d'acte -->
					<h2 class="text-base font-semibold text-slate-900">Type d’acte</h2>
					<div class="mt-2.5 grid grid-cols-2 gap-2.5">
						{#each CARDS as c (c.label)}
							{@const on = category === c.label}
							<button
								type="button"
								aria-pressed={on}
								onclick={() => (category = c.label)}
								class="relative flex min-h-16 flex-col items-start rounded-xl border p-3 text-left transition
								{on ? 'border-blue-600 bg-blue-50 ring-1 ring-blue-600' : 'border-slate-200 bg-white hover:border-slate-300'}"
							>
								<span class="pr-5 text-[12px] leading-snug font-semibold {on ? 'text-blue-700' : 'text-slate-900'}">{c.label}</span>
								<span class="mt-2 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">{c.minutes} min</span>
								{#if on}
									<span class="absolute top-3 right-3 text-blue-600">{@render icon('circleCheck', 'h-4 w-4')}</span>
								{/if}
							</button>
						{/each}
					</div>

					<!-- Date & Heure -->
					<div class="mt-6 flex items-center justify-between">
						<h2 class="text-base font-semibold text-slate-900">Date &amp; Heure</h2>
						<div class="flex items-center gap-1 text-xs text-slate-500">
							{#if weekPage > 0}
								<button type="button" aria-label="Jours précédents" onclick={() => weekPage--} class="rounded p-1 hover:bg-slate-100">
									{@render icon('chevronLeft', 'h-3.5 w-3.5')}
								</button>
							{/if}
							<span>{monthLabel}</span>
							<button
								type="button"
								aria-label="Jours suivants"
								disabled={days[days.length - 1] >= lastBookable}
								onclick={() => weekPage++}
								class="rounded p-1 hover:bg-slate-100 disabled:opacity-30"
							>
								{@render icon('chevronRight', 'h-3.5 w-3.5')}
							</button>
						</div>
					</div>
					<div class="mt-2.5 grid grid-cols-6 gap-2">
						{#each days as d (d)}
							{@const on = d === date}
							<button
								type="button"
								aria-pressed={on}
								aria-label={cap(fmtUTC(d, { weekday: 'long', day: 'numeric', month: 'long' }))}
								disabled={d > lastBookable}
								onclick={() => pickDate(d)}
								class="flex flex-col items-center rounded-xl border py-2 transition disabled:opacity-30
								{on ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-200 bg-white text-slate-900 hover:border-slate-300'}"
							>
								<span class="text-[11px] {on ? 'text-blue-100' : 'text-slate-500'}">{cap(fmtUTC(d, { weekday: 'short' }))}</span>
								<span class="text-lg leading-tight font-semibold">{fmtUTC(d, { day: 'numeric' })}</span>
							</button>
						{/each}
					</div>

					<div class="mt-2.5 grid grid-cols-4 gap-2" aria-busy={slotsLoading}>
						{#each slots as s (s)}
							{@const on = s === time}
							<button
								type="button"
								aria-pressed={on}
								onclick={() => (time = s)}
								class="rounded-xl border py-2 text-sm transition
								{on ? 'border-blue-600 bg-blue-50 font-semibold text-blue-700 ring-1 ring-blue-600' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'}"
							>
								{s}
							</button>
						{:else}
							<p class="col-span-4 rounded-xl border border-dashed border-slate-200 py-3 text-center text-xs text-slate-400">
								{slotsLoading ? 'Chargement des créneaux…' : 'Aucun créneau disponible ce jour-là.'}
							</p>
						{/each}
					</div>

					<!-- Vos coordonnées -->
					<div class="mt-6 flex items-center justify-between">
						<h2 class="text-base font-semibold text-slate-900">Vos coordonnées</h2>
						<span class="text-xs text-slate-500">Informations confidentielles</span>
					</div>
					<div class="mt-2.5 space-y-2.5">
						{#each [
							{ id: 'bk-name', label: 'Nom', ic: 'user', type: 'text', auto: 'name', req: true },
							{ id: 'bk-phone', label: 'Téléphone', ic: 'phone', type: 'tel', auto: 'tel', req: true },
							{ id: 'bk-email', label: 'E-mail', ic: 'mail', type: 'email', auto: 'email', req: false }
						] as f (f.id)}
							<label for={f.id} class="flex h-12 items-center gap-3 rounded-xl border border-slate-200 px-4 focus-within:border-blue-600 focus-within:ring-1 focus-within:ring-blue-600">
								<span class="text-slate-400">{@render icon(f.ic as keyof typeof ICONS, 'h-[18px] w-[18px]')}</span>
								<span class="w-[4.75rem] shrink-0 text-[13px] text-slate-500">{f.label}</span>
								{#if f.id === 'bk-name'}
									<input id={f.id} type="text" autocomplete="name" required minlength="2" maxlength="120" bind:value={patient_name} placeholder="Votre nom complet" class="min-w-0 flex-1 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-300" />
								{:else if f.id === 'bk-phone'}
									<input id={f.id} type="tel" autocomplete="tel" inputmode="tel" required bind:value={phone} placeholder="06 12 34 56 78" class="min-w-0 flex-1 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-300" />
								{:else}
									<input id={f.id} type="email" autocomplete="email" bind:value={email} placeholder="Facultatif" class="min-w-0 flex-1 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-300" />
								{/if}
							</label>
						{/each}
					</div>
				{/if}

				{#if errorMessage}
					<p role="alert" class="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700">{errorMessage}</p>
				{/if}

				{#if doctor_id}
					<button
						type="submit"
						disabled={isLoading}
						class="mt-6 h-14 w-full rounded-xl bg-blue-600 text-[15px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.99] disabled:opacity-60"
					>
						{isLoading ? 'Réservation en cours…' : 'Confirmer le rendez-vous →'}
					</button>
				{/if}
			</form>
		{:else}
			<!-- ===================== Rendez-vous Confirmé ===================== -->
			<div class="flex flex-col" aria-live="polite">
				<div class="flex items-center justify-between">
					<h2 class="text-base font-semibold text-slate-900">Votre confirmation</h2>
					<button type="button" aria-label="Nouveau rendez-vous" onclick={newBooking} class="flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 hover:bg-slate-100">
						{@render icon('x', 'h-4 w-4')}
					</button>
				</div>

				<!-- the pass -->
				<div class="relative mt-5 rounded-[1.75rem] bg-white shadow-[0_8px_30px_rgba(15,23,42,0.06)]">
					<div class="flex flex-col items-center px-6 pt-7 pb-6 text-center">
						<span class="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-50 text-emerald-500">{@render icon('check', 'h-6 w-6')}</span>
						<h1 class="mt-4 text-[22px] font-bold text-slate-900">Rendez-vous Confirmé</h1>
						<p class="mt-1 text-xs text-slate-500">Un SMS avec votre lien a été envoyé</p>
					</div>

					<!-- perforation -->
					<div class="relative h-0" aria-hidden="true">
						<span class="absolute -top-3 -left-3 h-6 w-6 rounded-full bg-slate-50"></span>
						<span class="absolute -top-3 -right-3 h-6 w-6 rounded-full bg-slate-50"></span>
						<div class="mx-5 border-t border-dashed border-slate-200"></div>
					</div>

					<dl class="px-5 pt-3">
						{#each [
							{ ic: 'user', k: 'Patiente', v: successData.patient_name },
							{ ic: 'stethoscope', k: 'Médecin', v: successData.doctor_name },
							{ ic: 'clipboard', k: 'Type de visite', v: `${card.label} · ${successData.expected_duration_minutes} min` },
							{ ic: 'calendar', k: 'Date et heure', v: longDateTime(successData.booked_time) }
						] as row, i (row.k)}
							<div class="flex items-start gap-3 py-2.5 {i < 3 ? 'border-b border-slate-100' : ''}">
								<span class="mt-0.5 text-slate-400">{@render icon(row.ic as keyof typeof ICONS, 'h-[18px] w-[18px]')}</span>
								<div class="min-w-0">
									<dt class="text-[11px] text-slate-500">{row.k}</dt>
									<dd class="mt-0.5 text-sm font-medium text-slate-900">{row.v}</dd>
								</div>
							</div>
						{/each}
					</dl>

					<div class="mx-5 mt-2 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
						<p class="flex items-center gap-1.5 text-[10px] font-semibold tracking-wider text-emerald-600 uppercase">
							{@render icon('sparkles', 'h-3.5 w-3.5')} Suivi intelligent · IA
						</p>
						<p class="mt-2 text-sm font-semibold text-slate-900">Position estimée: #{successData.queue_position} dans la file</p>
						<p class="mt-1.5 text-xs text-emerald-600">
							{#if initialWait(successData) > 0}
								Temps d’attente initial: ~{initialWait(successData)} min
							{:else}
								Temps d’attente initial: aucun retard prévu
							{/if}
						</p>
					</div>

					<div class="flex items-center justify-between px-5 pt-4 pb-5 text-[10px]">
						<span class="tracking-wide text-slate-400">RÉF. {reference(successData.booked_time)}</span>
						<span class="flex items-center gap-1 font-medium text-emerald-600">{@render icon('shield', 'h-3.5 w-3.5')} Réservé</span>
					</div>
				</div>

				<p class="mt-5 text-center text-xs text-slate-600">Votre place est réservée. Gagnez en sérénité.</p>
				<p class="mt-1 text-center text-[11px] text-slate-400">Le temps d’attente se met à jour en temps réel.</p>

				<a
					href={successData.tracker_url}
					class="mt-6 flex h-14 w-full items-center justify-center rounded-xl bg-blue-600 text-[15px] font-semibold text-white shadow-sm transition hover:bg-blue-700"
				>
					📱 Suivre ma position en temps réel
				</a>
			</div>
		{/if}
		</section>
	</div>
</main>
</div>
