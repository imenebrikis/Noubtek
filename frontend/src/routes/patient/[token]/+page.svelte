<script lang="ts">
	import { page } from '$app/state';

	type Status = 'scheduled' | 'waiting' | 'in_session' | 'completed' | 'no_show' | 'cancelled';
	type Track = {
		clinic_name: string;
		patient_first_name: string;
		status: Status;
		position: number;
		people_ahead: number;
		eta_minutes: number;
		consult_time: string | null;
	};

	const token = page.params.token;
	let data = $state<Track | null>(null);
	let error = $state('');
	let loading = $state(true);

	async function load() {
		try {
			const res = await fetch(`/api/v1/public/track/${token}`);
			if (!res.ok) throw new Error(res.status === 404 ? 'Lien de suivi introuvable' : 'Erreur serveur');
			data = await res.json();
			error = '';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	// initial load + live polling every 15s
	$effect(() => {
		load();
		const id = setInterval(load, 15000);
		return () => clearInterval(id);
	});

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

<div class="min-h-screen w-full bg-[#f8fafc]">
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

					<p class="mt-auto pt-8 text-center text-xs text-slate-400">Mise à jour automatique toutes les 15 s</p>
				</section>
			</div>
		{/if}
	</main>
</div>
