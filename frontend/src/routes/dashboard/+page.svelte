<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	const TOKEN_KEY = 'noubtek_token';

	type Status = 'scheduled' | 'waiting' | 'in_session' | 'completed' | 'no_show' | 'cancelled';
	type Row = {
		id: string;
		patient_name: string;
		patient_phone: string | null;
		scheduled_start: string;
		predicted_start: string | null;
		status: Status;
	};
	type Doctor = { id: string; display_name: string | null; specialty: string | null };
	type DelayAlert = { estimated_delay_minutes: number; apology_message: string; source: string };

	// --- server state ---------------------------------------------------------
	let token = $state('');
	let queue = $state<Row[]>([]);
	let doctors = $state<Doctor[]>([]);
	let selectedDoctorId = $state('');
	let delayAlert = $state<DelayAlert | null>(null);
	let loading = $state(true);
	let busy = $state(false);
	let error = $state('');

	// --- live clock -----------------------------------------------------------
	let now = $state(new Date());
	$effect(() => {
		const t = setInterval(() => (now = new Date()), 1000);
		return () => clearInterval(t);
	});
	const today = $derived(
		now.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })
	);

	// --- API client (same-origin via Vite proxy -> FastAPI) -------------------
	async function api(path: string, opts: { method?: string; body?: unknown } = {}) {
		const res = await fetch(path, {
			method: opts.method ?? 'GET',
			headers: {
				'Content-Type': 'application/json',
				...(token ? { Authorization: `Bearer ${token}` } : {})
			},
			body: opts.body ? JSON.stringify(opts.body) : undefined
		});
		if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
		return res.status === 204 ? null : res.json();
	}

	function logout() {
		localStorage.removeItem(TOKEN_KEY);
		goto('/login');
	}

	async function boot() {
		// route guard: real JWT from the login page, or back to /login
		const stored = localStorage.getItem(TOKEN_KEY);
		if (!stored) {
			goto('/login');
			return;
		}
		token = stored;
		try {
			// fetch concurrently (both carry the Bearer token via api()) — no waterfall
			const [docs, q] = await Promise.all([
				api('/api/v1/queue/doctors'),
				api('/api/v1/queue')
			]);
			doctors = docs;
			if (docs.length) selectedDoctorId = docs[0].id;
			queue = q;
		} catch (e) {
			const msg = e instanceof Error ? e.message : String(e);
			if (msg.startsWith('401')) {
				logout(); // expired / invalid token
				return;
			}
			error = msg;
		} finally {
			loading = false;
		}
	}
	onMount(boot);

	// --- reactive metrics (full queue) ----------------------------------------
	const waiting = $derived(queue.filter((r) => r.status === 'waiting'));
	const inSession = $derived(queue.filter((r) => r.status === 'in_session'));
	const done = $derived(queue.filter((r) => r.status === 'completed'));
	// real backend delay once an action has run; local heuristic until then
	const estimatedDelay = $derived(delayAlert?.estimated_delay_minutes ?? waiting.length * 15);

	// --- search ---------------------------------------------------------------
	let query = $state('');
	const matches = (r: Row) => {
		const q = query.trim().toLowerCase();
		return (
			!q ||
			r.patient_name.toLowerCase().includes(q) ||
			(r.patient_phone ?? '').replace(/\s/g, '').includes(q)
		);
	};
	const waitingV = $derived(waiting.filter(matches));
	const inSessionV = $derived(inSession.filter(matches));
	const doneV = $derived(done.filter(matches));

	const doctorName = $derived(
		doctors.find((d) => d.id === selectedDoctorId)?.display_name ?? 'Médecin'
	);

	// --- actions (optimistic: move the card now, reconcile with the server) ---
	async function transition(r: Row, new_status: Status) {
		const prev = r.status;
		r.status = new_status; // instant: $derived columns re-sort immediately
		try {
			const res = await api('/api/v1/queue/transition', {
				method: 'POST',
				body: { appointment_id: r.id, new_status }
			});
			// silent sync to DB truth (predicted_start, Gemini delay alert, ordering)
			queue = res.queue;
			delayAlert = res.delay_alert;
		} catch (e) {
			r.status = prev; // revert the optimistic move on failure
			error = e instanceof Error ? e.message : String(e);
		}
	}
	const appeler = (r: Row) => transition(r, 'in_session');
	const terminer = (r: Row) => transition(r, 'completed');
	const annuler = (r: Row) => transition(r, r.status === 'completed' ? 'in_session' : 'waiting');

	// --- walk-in modal --------------------------------------------------------
	let showModal = $state(false);
	let form = $state({ full_name: '', phone: '', procedure_type: '', is_scheduled: false, slot_time: '' });
	const resetForm = () =>
		(form = { full_name: '', phone: '', procedure_type: '', is_scheduled: false, slot_time: '' });

	async function submitWalkIn(e: Event) {
		e.preventDefault();
		if (busy || !form.full_name.trim() || !selectedDoctorId) return;
		busy = true;
		try {
			const res = await api('/api/v1/queue/walk-in', {
				method: 'POST',
				body: {
					full_name: form.full_name,
					phone: form.phone || null,
					procedure_type: form.procedure_type || null,
					doctor_id: selectedDoctorId,
					is_scheduled: form.is_scheduled,
					slot_time: form.is_scheduled ? form.slot_time : null
				}
			});
			queue = res.queue;
			delayAlert = res.delay_alert;
			showModal = false;
			resetForm();
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			busy = false;
		}
	}

	// --- presentation helpers -------------------------------------------------
	const hhmm = (iso: string) => iso.slice(11, 16);
	const rowTime = (r: Row) => hhmm(r.predicted_start ?? r.scheduled_start);
	const initials = (name: string) =>
		name.split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase();
	const AVATARS = ['bg-teal-500', 'bg-indigo-500', 'bg-orange-400', 'bg-emerald-500', 'bg-rose-400'];
	function avatarColor(name: string) {
		let h = 0;
		for (const ch of name) h = (h + ch.charCodeAt(0)) % AVATARS.length;
		return AVATARS[h];
	}
	const STATUS_BADGE: Record<string, string> = {
		waiting: 'bg-orange-100 text-orange-600',
		in_session: 'bg-indigo-100 text-indigo-600',
		completed: 'bg-slate-100 text-slate-500'
	};
	const STATUS_LABEL: Record<string, string> = {
		waiting: 'En Attente',
		in_session: 'En Consultation',
		completed: 'Terminé'
	};

	const NAV = [
		{ label: "Aujourd'hui", icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M3 10l9-7 9 7v9a2 2 0 01-2 2h-3v-6H8v6H5a2 2 0 01-2-2z"/>', active: true },
		{ label: 'File en direct', icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M3 12h4l2 6 4-14 2 8h6"/>' },
		{ label: 'Rendez-vous', icon: '<rect x="3" y="4" width="18" height="17" rx="2"/><path stroke-linecap="round" d="M3 9h18M8 2v4M16 2v4"/>' },
		{ label: 'Patients', icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M17 20v-2a4 4 0 00-4-4H7a4 4 0 00-4 4v2"/><circle cx="10" cy="7" r="4"/>' },
		{ label: 'Médecins', icon: '<circle cx="12" cy="8" r="4"/><path stroke-linecap="round" stroke-linejoin="round" d="M4 21v-1a6 6 0 0112 0v1"/>' },
		{ label: 'Rapports', icon: '<path stroke-linecap="round" stroke-linejoin="round" d="M4 20V10M10 20V4M16 20v-7M2 20h20"/>' },
		{ label: 'Paramètres', icon: '<circle cx="12" cy="12" r="3"/><path stroke-linecap="round" stroke-linejoin="round" d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>' }
	];

	const COLUMNS = $derived([
		{ label: 'En Attente', list: waitingV, dot: 'bg-orange-400' },
		{ label: 'En Consultation', list: inSessionV, dot: 'bg-indigo-500' },
		{ label: 'Terminé', list: doneV, dot: 'bg-slate-300' }
	]);
</script>

<div class="flex min-h-screen bg-[#f6f7f9] text-slate-900">
	<!-- Sidebar -->
	<aside class="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-white px-4 py-5 lg:flex">
		<div class="flex items-center gap-2 px-2">
			<div class="flex h-8 w-8 items-center justify-center rounded-lg bg-[#1f2a52] text-sm font-bold text-white">N</div>
			<span class="text-lg font-semibold">Noubtek</span>
			<span class="ml-auto rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">Clinique</span>
		</div>

		<div class="mt-5 flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
			<div class="flex h-9 w-9 items-center justify-center rounded-lg bg-teal-500 text-white">
				<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8"><path stroke-linecap="round" stroke-linejoin="round" d="M12 21s-7-4.4-7-10a7 7 0 1114 0c0 5.6-7 10-7 10z"/><circle cx="12" cy="11" r="2.5"/></svg>
			</div>
			<div class="leading-tight">
				<p class="text-sm font-semibold">Clinique Médicale</p>
				<p class="text-xs text-slate-500">Alger · {waiting.length + inSession.length} patients en file</p>
			</div>
		</div>

		<nav class="mt-5 flex flex-1 flex-col gap-1">
			{#each NAV as item}
				<a href="#{item.label}" class="flex min-h-[44px] items-center gap-3 rounded-lg px-3 text-sm font-medium transition {item.active ? 'bg-indigo-50 text-indigo-700' : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800'}">
					<svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">{@html item.icon}</svg>
					{item.label}
				</a>
			{/each}
		</nav>

		<div class="mt-4 rounded-xl bg-[#1f2a52] p-4 text-white">
			<p class="text-sm font-semibold">En retard aujourd'hui ?</p>
			<p class="mt-1 text-xs text-indigo-200">Prévenez les {waiting.length} patients en attente en un seul clic.</p>
			<button class="mt-3 min-h-[44px] w-full rounded-lg bg-white px-3 text-sm font-semibold text-[#1f2a52] transition hover:bg-indigo-50">Envoyer SMS de retard</button>
		</div>

		<button onclick={logout} class="mt-2 flex min-h-[44px] items-center gap-3 rounded-lg px-3 text-sm font-medium text-slate-500 transition hover:bg-slate-50 hover:text-rose-600">
			<svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8"><path stroke-linecap="round" stroke-linejoin="round" d="M15 3H6a2 2 0 00-2 2v14a2 2 0 002 2h9M10 12h11m0 0l-3-3m3 3l-3 3"/></svg>
			Déconnexion
		</button>
	</aside>

	<!-- Main -->
	<div class="flex min-w-0 flex-1 flex-col">
		<header class="flex flex-col gap-4 border-b border-slate-200 bg-[#f6f7f9] px-4 py-5 sm:px-6 lg:px-8 xl:flex-row xl:items-center xl:justify-between">
			<div>
				<p class="text-sm capitalize text-slate-500">{today}</p>
				<h1 class="mt-0.5 text-2xl font-bold tracking-tight">Noubtek — Clinique Médicale</h1>
			</div>
			<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
				<div class="relative">
					<svg class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path stroke-linecap="round" d="M21 21l-4-4"/></svg>
					<input type="search" bind:value={query} placeholder="Rechercher un patient ou un numéro…" class="min-h-[44px] w-full rounded-xl border border-slate-200 bg-white pl-10 pr-4 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 sm:w-64" />
				</div>
				<select bind:value={selectedDoctorId} class="min-h-[44px] rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100">
					{#each doctors as d}<option value={d.id}>{d.display_name}{d.specialty ? ` — ${d.specialty}` : ''}</option>{/each}
				</select>
				<button onclick={() => (showModal = true)} class="flex min-h-[44px] items-center justify-center gap-2 rounded-xl bg-[#1f2a52] px-4 text-sm font-semibold text-white transition hover:bg-[#28356a] active:scale-95">
					<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 5v14M5 12h14"/></svg>
					Walk-in / RDV
				</button>
			</div>
		</header>

		<main class="flex-1 space-y-6 px-4 py-6 sm:px-6 lg:px-8">
			{#if error}
				<div class="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">⚠ {error}</div>
			{/if}

			<!-- Delay alert (real backend apology) -->
			{#if delayAlert && estimatedDelay > 0}
				<div class="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
					<span class="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-400 text-xs font-bold text-white">!</span>
					<div class="text-sm">
						<p class="font-medium text-amber-900">{delayAlert.apology_message}</p>
						<p class="mt-0.5 text-xs text-amber-600">Retard estimé : {estimatedDelay} min · source : {delayAlert.source}</p>
					</div>
				</div>
			{/if}

			{#if loading}
				<!-- Skeleton: real column headers + pulsing placeholder cards, so layout feels instant -->
				<div class="animate-pulse space-y-6">
					<section class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
						{#each Array(4) as _}
							<div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
								<div class="flex items-center justify-between">
									<span class="h-3 w-24 rounded bg-slate-200"></span>
									<span class="h-2.5 w-2.5 rounded-full bg-slate-200"></span>
								</div>
								<span class="mt-3 block h-9 w-16 rounded bg-slate-200"></span>
								<span class="mt-3 block h-2.5 w-20 rounded bg-slate-200"></span>
							</div>
						{/each}
					</section>

					<section class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
						<div class="mb-4 h-5 w-32 rounded bg-slate-200"></div>
						<div class="grid grid-cols-1 gap-4 md:grid-cols-3">
							{#each [{ label: 'En Attente', dot: 'bg-orange-400', cards: 3 }, { label: 'En Consultation', dot: 'bg-indigo-500', cards: 1 }, { label: 'Terminé', dot: 'bg-slate-300', cards: 2 }] as col}
								<div class="rounded-xl bg-slate-50 p-3">
									<div class="mb-3 flex items-center gap-2 px-1">
										<span class="h-2 w-2 rounded-full {col.dot}"></span>
										<h3 class="text-sm font-semibold text-slate-700">{col.label}</h3>
										<span class="ml-auto h-5 w-6 rounded-full bg-slate-200"></span>
									</div>
									<div class="space-y-3">
										{#each Array(col.cards) as _}
											<div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
												<div class="flex items-center justify-between">
													<span class="h-3 w-10 rounded bg-slate-200"></span>
													<span class="h-4 w-16 rounded-full bg-slate-200"></span>
												</div>
												<div class="mt-2 flex items-center gap-3">
													<span class="h-9 w-9 rounded-full bg-slate-200"></span>
													<div class="flex-1 space-y-1.5">
														<span class="block h-3 w-24 rounded bg-slate-200"></span>
														<span class="block h-2.5 w-20 rounded bg-slate-200"></span>
													</div>
												</div>
												<div class="mt-3 h-11 rounded-lg bg-slate-200"></div>
											</div>
										{/each}
									</div>
								</div>
							{/each}
						</div>
					</section>
				</div>
			{:else}
				<!-- Metric cards -->
				<section class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
					<div class="rounded-2xl bg-[#1f2a52] p-5 text-white shadow-sm">
						<p class="text-sm font-medium text-indigo-200">En Attente</p>
						<p class="mt-2 text-4xl font-bold">{waiting.length}</p>
						<div class="mt-4 flex flex-wrap gap-1">
							{#each Array(16) as _, i}
								<span class="h-1.5 w-1.5 rounded-full {i < waiting.length ? 'bg-white' : 'bg-white/25'}"></span>
							{/each}
						</div>
					</div>

					<div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
						<div class="flex items-center justify-between">
							<p class="text-sm font-medium text-slate-500">Temps d'attente Moyen</p>
							<span class="rounded-full bg-teal-50 px-2 py-0.5 text-[10px] font-semibold text-teal-600">{delayAlert?.source === 'gemini' ? 'Gemini Pro' : 'Estimation IA'}</span>
						</div>
						<p class="mt-2 text-4xl font-bold text-slate-800">~{estimatedDelay}<span class="ml-1 text-base font-medium text-slate-400">min</span></p>
						<p class="mt-2 text-xs text-slate-400">retard estimé de la file</p>
					</div>

					<div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
						<div class="flex items-center justify-between">
							<p class="text-sm font-medium text-slate-500">En Consultation</p>
							<span class="relative flex h-2.5 w-2.5">
								<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
								<span class="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500"></span>
							</span>
						</div>
						<p class="mt-2 text-4xl font-bold text-slate-800">{inSession.length}</p>
						<p class="mt-2 text-xs text-slate-400">en cours maintenant</p>
					</div>

					<div class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
						<p class="text-sm font-medium text-slate-500">Terminé</p>
						<p class="mt-2 text-4xl font-bold text-slate-800">{done.length}</p>
						<p class="mt-2 text-xs text-slate-400">vus aujourd'hui</p>
					</div>
				</section>

				<!-- Queue board -->
				<section class="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
					<div class="mb-4 flex items-center justify-between">
						<h2 class="text-base font-semibold text-slate-800">File d'attente</h2>
						<span class="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-500">{doctorName}</span>
					</div>

					<div class="grid grid-cols-1 gap-4 md:grid-cols-3">
						{#each COLUMNS as col}
							<div class="rounded-xl bg-slate-50 p-3">
								<div class="mb-3 flex items-center gap-2 px-1">
									<span class="h-2 w-2 rounded-full {col.dot}"></span>
									<h3 class="text-sm font-semibold text-slate-700">{col.label}</h3>
									<span class="ml-auto rounded-full bg-white px-2 py-0.5 text-xs font-semibold text-slate-500">{col.list.length}</span>
								</div>

								<div class="space-y-3">
									{#each col.list as row (row.id)}
										<div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm {row.status === 'completed' ? 'opacity-60' : ''}">
											<div class="flex items-center justify-between">
												<span class="font-mono text-xs text-slate-400">{rowTime(row)}</span>
												<span class="rounded-full px-2 py-0.5 text-[10px] font-semibold {STATUS_BADGE[row.status] ?? 'bg-slate-100 text-slate-500'}">{STATUS_LABEL[row.status] ?? row.status}</span>
											</div>
											<div class="mt-2 flex items-center gap-3">
												<div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white {avatarColor(row.patient_name)}">{initials(row.patient_name)}</div>
												<div class="min-w-0 leading-tight">
													<p class="truncate text-sm font-semibold text-slate-800">{row.patient_name}</p>
													<p class="truncate text-xs text-slate-400">{row.patient_phone ?? '—'}</p>
												</div>
											</div>

											<div class="mt-3 flex gap-2">
												{#if row.status === 'waiting'}
													<button onclick={() => appeler(row)} disabled={busy} class="min-h-[44px] flex-1 rounded-lg bg-indigo-600 px-3 text-sm font-semibold text-white transition hover:bg-indigo-700 active:scale-95 disabled:opacity-50">Appeler</button>
												{:else if row.status === 'in_session'}
													<button onclick={() => terminer(row)} disabled={busy} class="min-h-[44px] flex-1 rounded-lg bg-emerald-600 px-3 text-sm font-semibold text-white transition hover:bg-emerald-700 active:scale-95 disabled:opacity-50">Terminer</button>
													<button onclick={() => annuler(row)} disabled={busy} class="min-h-[44px] rounded-lg border border-slate-200 px-3 text-sm font-medium text-slate-500 transition hover:bg-slate-50 active:scale-95 disabled:opacity-50">Annuler</button>
												{:else}
													<button onclick={() => annuler(row)} disabled={busy} class="min-h-[44px] flex-1 rounded-lg border border-slate-200 px-3 text-sm font-medium text-slate-500 transition hover:bg-slate-50 active:scale-95 disabled:opacity-50">Annuler</button>
												{/if}
											</div>
										</div>
									{/each}

									{#if col.list.length === 0}
										<p class="px-1 py-6 text-center text-xs text-slate-400">Aucun patient</p>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</section>
			{/if}
		</main>
	</div>
</div>

<!-- Walk-in / RDV modal -->
{#if showModal}
	<div class="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center" onclick={() => (showModal = false)} role="presentation">
		<form onsubmit={submitWalkIn} onclick={(e) => e.stopPropagation()} class="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
			<h3 class="text-lg font-semibold">Nouveau patient</h3>
			<p class="mt-0.5 text-xs text-slate-500">Walk-in ou rendez-vous programmé</p>

			<div class="mt-4 space-y-3">
				<div>
					<label class="text-xs font-medium text-slate-600" for="wi-name">Nom complet</label>
					<input id="wi-name" bind:value={form.full_name} required placeholder="Ex : Ahmed Benali" class="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100" />
				</div>
				<div class="grid grid-cols-2 gap-3">
					<div>
						<label class="text-xs font-medium text-slate-600" for="wi-phone">Téléphone</label>
						<input id="wi-phone" bind:value={form.phone} placeholder="+213…" class="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100" />
					</div>
					<div>
						<label class="text-xs font-medium text-slate-600" for="wi-proc">Motif</label>
						<input id="wi-proc" bind:value={form.procedure_type} placeholder="Consultation" class="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100" />
					</div>
				</div>
				<label class="flex items-center gap-2 text-sm text-slate-700">
					<input type="checkbox" bind:checked={form.is_scheduled} class="h-4 w-4 rounded border-slate-300" />
					Rendez-vous programmé
				</label>
				{#if form.is_scheduled}
					<div>
						<label class="text-xs font-medium text-slate-600" for="wi-slot">Créneau (HH:MM)</label>
						<input id="wi-slot" bind:value={form.slot_time} placeholder="09:20" class="mt-1 min-h-[44px] w-full rounded-lg border border-slate-300 px-3 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100" />
					</div>
				{/if}
			</div>

			<div class="mt-5 flex gap-2">
				<button type="button" onclick={() => (showModal = false)} class="min-h-[44px] flex-1 rounded-lg border border-slate-200 px-3 text-sm font-medium text-slate-600 hover:bg-slate-50">Annuler</button>
				<button type="submit" disabled={busy || !form.full_name.trim()} class="min-h-[44px] flex-1 rounded-lg bg-indigo-600 px-3 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? 'Ajout…' : 'Ajouter'}</button>
			</div>
		</form>
	</div>
{/if}
