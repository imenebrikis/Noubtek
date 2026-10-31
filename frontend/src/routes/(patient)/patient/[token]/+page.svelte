<script lang="ts">
	import { page } from '$app/state';
	import { patientSession, remember } from '#lib/patientSession.svelte.js';
	import { fade, fly } from 'svelte/transition';

	type Status = 'scheduled' | 'waiting' | 'in_session' | 'completed' | 'no_show' | 'cancelled';
	type Track = {
		clinic_name: string;
		doctor_id: string | null; // same id as /book?doctor=… — powers re-booking
		patient_first_name: string;
		status: Status;
		position: number;
		people_ahead: number;
		eta_minutes: number;
		consult_time: string | null;
		delay_minutes: number;
		ai_message: string;
		ai_source: 'gemini' | 'heuristic';
		alert: { message: string; shift_minutes: number; at: string; source: string } | null;
		latest_message: string | null; // staff broadcast ("SMS de retard"), while waiting
		latest_message_at: string | null; // ISO, clinic-local — changes on every new broadcast
	};

	const token = page.params.token ?? ''; // always set on this route
	let data = $state<Track | null>(null);
	let error = $state('');
	let loading = $state(true);

	// ponytail: 3s so a staff broadcast lands on screen during the live demo; 15s is plenty in production
	const POLL_MS = 3000;
	let inFlight = false;

	async function load() {
		// one request at a time (a Gemini refresh can take a few seconds), none from a hidden tab
		if (inFlight || (data && document.hidden)) return;
		inFlight = true;
		try {
			const res = await fetch(`/api/v1/public/track/${token}`);
			if (res.status === 404 && patientSession.token === token) remember('token', ''); // stale: stop linking to it
			if (!res.ok) throw new Error(res.status === 404 ? 'Lien de suivi introuvable' : 'Erreur serveur');
			data = await res.json();
			// a token the API knows: the navbar's "Suivi en direct" and "Prise de rendez-vous" follow it
			if (patientSession.token !== token) remember('token', token);
			if (data?.doctor_id && patientSession.doctorId !== data.doctor_id) remember('doctorId', data.doctor_id);
			error = '';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			inFlight = false;
			loading = false;
		}
	}

	// initial load + live polling
	$effect(() => {
		load();
		const id = setInterval(load, POLL_MS);
		return () => clearInterval(id);
	});

	// broadcast toast: shows each new message once; closing hides that message until a newer one arrives
	let dismissedAt = $state<string | null>(null);
	const toast = $derived(
		data?.latest_message && data.latest_message_at && data.latest_message_at !== dismissedAt
			? { message: data.latest_message, at: data.latest_message_at }
			: null
	);
	const clinicTime = (iso: string) =>
		new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Algiers' });

	const ordinal = (n: number) => (n === 1 ? '1er' : `${n}e`);

	const headline = $derived.by(() => {
		if (!data) return '';
		if (data.status === 'in_session') return 'À vous !';
		if (data.status === 'completed') return 'Terminé';
		if (data.status === 'waiting' && data.position > 0) return `Vous êtes ${ordinal(data.position)}`;
		return 'Bientôt';
	});
	const subline = $derived.by(() => {
		if (!data) return '';
		if (data.status === 'in_session') return 'Veuillez vous présenter au cabinet.';
		if (data.status === 'completed') return 'Votre consultation est terminée. Bonne journée !';
		if (data.status === 'waiting')
			return data.eta_minutes > 0 ? `Temps d'attente estimé : ~${data.eta_minutes} min` : 'Vous êtes le prochain patient';
		return 'Signalez votre arrivée à l’accueil.';
	});

	const steps = [
		{ k: 'waiting', label: 'En attente' },
		{ k: 'in_session', label: 'En consultation' },
		{ k: 'completed', label: 'Terminé' }
	] as const;
	// -1 when scheduled / no_show / cancelled → nothing lit
	const stepIdx = $derived(data ? steps.findIndex((s) => s.k === data!.status) : -1);
</script>

<div class="w-full">
	{#if toast}
		{#key toast.at}
			<!-- slides in from the top; re-keyed so every new broadcast animates in again -->
			<div class="pointer-events-none fixed inset-x-0 top-0 z-50 flex justify-center px-4 pt-4 sm:pt-6">
				<div
					role="alert"
					in:fly={{ y: -40, duration: 450 }}
					out:fade={{ duration: 200 }}
					class="pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-2xl border border-amber-200/80 bg-white/95 p-4 shadow-[0_24px_60px_-15px_rgba(15,23,42,0.4)] ring-1 ring-black/5 backdrop-blur"
				>
					<span class="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-600">
						<span class="absolute -top-0.5 -right-0.5 flex h-3 w-3">
							<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75"></span>
							<span class="relative inline-flex h-3 w-3 rounded-full border-2 border-white bg-amber-500"></span>
						</span>
						<svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
							<path d="M10.268 21a2 2 0 0 0 3.464 0" />
							<path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" />
						</svg>
					</span>
					<div class="min-w-0 flex-1">
						<p class="text-xs font-semibold tracking-wide text-amber-700 uppercase">Message de la clinique · {clinicTime(toast.at)}</p>
						<p class="mt-1 text-sm leading-relaxed text-slate-800">{toast.message}</p>
					</div>
					<button
						type="button"
						aria-label="Fermer le message"
						onclick={() => (dismissedAt = toast.at)}
						class="-mt-1 -mr-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
					>
						<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
					</button>
				</div>
			</div>
		{/key}
	{/if}

	<main class="mx-auto w-full max-w-7xl px-4 py-8 lg:px-8">
		{#if loading}
			<div class="grid gap-6 lg:grid-cols-2 lg:gap-8">
				<div class="h-80 animate-pulse rounded-4xl bg-[#2563eb]/20"></div>
				<div class="h-80 animate-pulse rounded-4xl bg-slate-200"></div>
			</div>
		{:else if error}
			<div class="mx-auto max-w-md rounded-4xl border border-rose-200 bg-rose-50 p-8 text-center">
				<p class="font-semibold text-rose-700">{error}</p>
				<p class="mt-1 text-sm text-rose-400">Vérifiez le lien reçu par SMS.</p>
			</div>
		{:else if data}
			<div class="grid gap-6 lg:grid-cols-2 lg:gap-8">
				<!-- Left: hero -->
				<section
					class="flex flex-col rounded-4xl bg-linear-to-r from-[#3b82f6] via-[#1d3fbf] to-[#142a7c] p-8 text-white shadow-[0_20px_50px_-12px_rgba(30,58,138,0.35)] lg:min-h-184 lg:p-10"
				>
					<div>
						<div class="flex items-center gap-2">
							<span class="flex h-7 w-7 items-center justify-center rounded-lg bg-[#3b82f6] text-lg font-bold leading-none">+</span>
							<span class="text-xs font-semibold uppercase tracking-[0.12em] text-white/50">Espace patient</span>
						</div>
						<p class="mt-1 text-3xl font-bold tracking-tight">Votre file d’attente</p>
						<p class="mt-2 text-base text-white/85">Suivez votre passage, en toute sérénité.</p>
						<p class="mt-1 flex items-center gap-2 text-sm text-white/90">
							<span class="relative flex h-2 w-2">
								<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-white/60"></span>
								<span class="relative inline-flex h-2 w-2 rounded-full bg-white/80"></span>
							</span>
							File en direct · {data.clinic_name}
						</p>
					</div>

					<div class="my-12 flex flex-1 flex-col items-center justify-center text-center" aria-live="polite">
						<h1 class="text-6xl font-extrabold leading-none tracking-tight sm:text-7xl">{headline}</h1>
						<p class="mt-6 text-lg text-white/90">{subline}</p>

						{#if data.status === 'completed' && data.doctor_id}
							<a
								href="/book?doctor={data.doctor_id}"
								class="mt-8 inline-flex min-h-12 items-center gap-2 rounded-full bg-white px-6 text-sm font-semibold text-[#1d3fbf] shadow-lg transition hover:bg-blue-50 active:scale-[0.98]"
							>
								<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2v4M16 2v4M3 10h18M12 14v4M10 16h4" /><path d="M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" /></svg>
								Prendre un nouveau rendez-vous
							</a>
						{/if}

						{#if data.status === 'waiting' && data.alert}
							{@const earlier = data.alert.shift_minutes < 0}
							<div
								role="status"
								class="mt-8 w-full max-w-md rounded-2xl border p-4 text-left {earlier ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-amber-200 bg-amber-50 text-amber-900'}"
							>
								<p class="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide">
									<span aria-hidden="true">🔔</span>
									{earlier ? 'Votre passage est avancé' : 'Votre passage est retardé'} · {data.alert.at}
								</p>
								<p class="mt-1.5 text-sm leading-relaxed">{data.alert.message}</p>
							</div>
						{/if}

						{#if data.status === 'waiting' && data.ai_message}
							<div class="mt-8 w-full max-w-md rounded-2xl border border-white/20 bg-white/10 p-4 text-left backdrop-blur-sm">
								<span class="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide">
									<svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2l1.9 5.8L20 9.7l-5.1 3.6L16.8 20 12 16.3 7.2 20l1.9-6.7L4 9.7l6.1-1.9z" /></svg>
									{data.ai_source === 'gemini' ? 'Prédiction IA' : 'Estimation'}
									{#if data.delay_minutes > 0}· +{data.delay_minutes} min{/if}
								</span>
								<!-- the toast already shows this delay message: don't repeat it while it's up -->
								{#if !toast}
									<p class="mt-2 text-sm leading-relaxed text-white/90">{data.ai_message}</p>
								{/if}
							</div>
						{/if}
					</div>

					<div class="flex flex-wrap justify-center gap-3">
						<span class="flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-4 py-2 text-sm font-medium">
							<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 7h18v4a2 2 0 0 0 0 4v4H3v-4a2 2 0 0 0 0-4z" /></svg>
							Patient: {data.patient_first_name}
						</span>
						{#if data.consult_time}
							<span class="flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-4 py-2 text-sm font-medium">
								<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
								Consult. estimée {data.consult_time}
							</span>
						{/if}
					</div>
				</section>

				<!-- Right: journey -->
				<section class="flex flex-col rounded-4xl border border-slate-200/80 bg-white p-8 shadow-[0_1px_3px_rgba(15,23,42,0.04)] lg:p-10">
					<h2 class="text-xl font-bold text-slate-900">Votre parcours</h2>

					<ol class="mt-8 grid grid-cols-3">
						{#each steps as step, i}
							{@const done = stepIdx > i}
							{@const current = stepIdx === i}
							<li class="relative flex flex-col items-center text-center" aria-current={current ? 'step' : undefined}>
								{#if i < steps.length - 1}
									<span class="absolute left-1/2 top-5.5 h-0.5 w-full {done ? 'bg-[#2563eb]' : 'bg-slate-200'}"></span>
								{/if}
								<span
									class="relative flex h-11 w-11 items-center justify-center rounded-full text-base font-semibold transition-colors
									{done || current ? 'bg-[#2563eb] text-white' : 'border border-slate-200 bg-slate-100 text-slate-500'}"
								>
									{done ? '✓' : i + 1}
								</span>
								<p class="mt-4 text-sm {current ? 'font-semibold text-[#2563eb]' : 'font-medium text-slate-500'}">
									{i + 1} - {step.label}
								</p>
							</li>
						{/each}
					</ol>

					<!-- pre-visit tips: pointless once the consultation is over -->
					{#if data.status !== 'completed'}
						<div class="mt-8 rounded-2xl border border-slate-200/80 bg-slate-50 p-6">
							<h3 class="text-lg font-bold text-slate-900">Préparation</h3>
							<p class="mt-3 text-sm leading-relaxed text-slate-500">
								Ayez votre carte vitale, votre carte mutuelle et vos médicaments actuels prêts à présenter au moment de l’appel.
							</p>
						</div>

						<div class="mt-6 rounded-2xl border border-slate-200/80 bg-slate-50 p-6">
							<h3 class="text-lg font-bold text-slate-900">À préparer</h3>
							<ul class="mt-4 space-y-4 text-sm text-slate-500">
								{#each ['Arriver 10 minutes avant l’horaire estimé.', 'Avoir vos documents médicaux et vos questions prêts.', 'Conserver votre téléphone à portée de main pour l’appel du cabinet.'] as tip}
									<li class="flex items-start gap-3">
										<span class="mt-1.75 h-1.5 w-1.5 shrink-0 rounded-full bg-[#2563eb]"></span>{tip}
									</li>
								{/each}
							</ul>
						</div>
					{/if}

					<p class="mt-auto pt-8 text-center text-xs text-slate-400">Mise à jour automatique toutes les {POLL_MS / 1000} s</p>
				</section>
			</div>
		{/if}
	</main>
</div>
