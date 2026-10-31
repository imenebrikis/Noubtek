<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { loadPatientSession, patientSession } from '#lib/patientSession.svelte.js';

	let { children } = $props();

	onMount(loadPatientSession);

	const bookHref = $derived(patientSession.doctorId ? `/book?doctor=${patientSession.doctorId}` : '/book');
	const onBook = $derived(page.url.pathname.startsWith('/book'));
	const onTracker = $derived(page.url.pathname.startsWith('/patient/'));

	// --- "Suivi en direct" without a remembered token: ask for the SMS link or code ---------
	let dialog = $state<HTMLDialogElement>();
	let codeInput = $state('');
	let codeError = $state('');
	const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

	function openTracking(e: MouseEvent) {
		if (patientSession.token) return; // plain link to /patient/<token>
		e.preventDefault();
		codeInput = codeError = '';
		dialog?.showModal();
	}

	function submitCode(e: SubmitEvent) {
		e.preventDefault();
		const token = codeInput.match(UUID)?.[0]; // accepts the full link from the SMS or just the code
		if (!token) {
			codeError = 'Lien ou code de suivi invalide. Copiez le lien reçu par SMS.';
			return;
		}
		dialog?.close();
		goto(`/patient/${token.toLowerCase()}`);
	}

	const tab = (active: boolean) =>
		`flex min-h-10 items-center gap-2 rounded-full px-3 text-sm font-semibold transition sm:px-4 ${
			active ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-100' : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800'
		}`;
</script>

<div class="min-h-screen bg-[#f8fafc]">
	<header class="sticky top-0 z-40 border-b border-slate-200/80 bg-white/80 backdrop-blur">
		<nav class="mx-auto flex h-16 w-full max-w-7xl items-center justify-between gap-3 px-4 lg:px-8" aria-label="Espace patient">
			<a href={bookHref} class="flex items-center gap-2.5" aria-label="Noubtek — prendre rendez-vous">
				<span class="flex h-9 w-9 items-center justify-center rounded-[10px] bg-blue-600 text-white shadow-sm">
					<svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14M12 5v14" /></svg>
				</span>
				<span class="text-xl font-bold tracking-tight text-slate-900">Noubtek</span>
			</a>

			<div class="flex items-center gap-1 rounded-full bg-slate-100/70 p-1">
				<a href={bookHref} class={tab(onBook)} aria-current={onBook ? 'page' : undefined}>
					<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2v4M16 2v4M3 10h18" /><path d="M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" /></svg>
					<span class="sm:hidden">Rendez-vous</span><span class="hidden sm:inline">Prise de Rendez-vous</span>
				</a>
				<a
					href={patientSession.token ? `/patient/${patientSession.token}` : '#suivi'}
					onclick={openTracking}
					class={tab(onTracker)}
					aria-current={onTracker ? 'page' : undefined}
				>
					<span class="relative flex h-2 w-2" aria-hidden="true">
						{#if onTracker}<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75"></span>{/if}
						<span class="relative inline-flex h-2 w-2 rounded-full {onTracker ? 'bg-blue-600' : 'bg-slate-400'}"></span>
					</span>
					<span class="sm:hidden">Suivi</span><span class="hidden sm:inline">Suivi en direct</span>
				</a>
			</div>
		</nav>
	</header>

	{@render children()}
</div>

<dialog
	bind:this={dialog}
	aria-labelledby="suivi-title"
	class="m-auto w-[calc(100%-2rem)] max-w-md rounded-3xl bg-white p-0 shadow-2xl backdrop:bg-slate-900/40 backdrop:backdrop-blur-sm"
>
	<form onsubmit={submitCode} class="p-6">
		<div class="flex items-start justify-between gap-4">
			<div>
				<h2 id="suivi-title" class="text-lg font-bold text-slate-900">Suivre mon passage</h2>
				<p class="mt-1 text-sm text-slate-500">Collez le lien reçu par SMS, ou votre code de suivi.</p>
			</div>
			<button type="button" onclick={() => dialog?.close()} aria-label="Fermer" class="-mt-1 -mr-2 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
				<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
			</button>
		</div>

		<label for="suivi-code" class="mt-5 block text-xs font-medium text-slate-600">Lien ou code de suivi</label>
		<input
			id="suivi-code"
			bind:value={codeInput}
			autocomplete="off"
			spellcheck="false"
			placeholder="https://…/patient/… ou 9a0c6cda-…"
			aria-invalid={codeError ? 'true' : undefined}
			aria-describedby={codeError ? 'suivi-error' : undefined}
			class="mt-1.5 min-h-12 w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-sm focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-100 focus:outline-none"
		/>
		{#if codeError}
			<p id="suivi-error" role="alert" class="mt-2 text-sm text-red-600">{codeError}</p>
		{/if}

		<button type="submit" class="mt-5 h-12 w-full rounded-xl bg-blue-600 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700">
			Voir ma position
		</button>
		<p class="mt-3 text-center text-xs text-slate-400">Pas encore de rendez-vous ? Utilisez « Prise de Rendez-vous ».</p>
	</form>
</dialog>
