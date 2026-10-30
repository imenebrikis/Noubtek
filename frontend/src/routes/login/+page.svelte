<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	declare global {
		interface Window {
			google?: any;
		}
	}

	const TOKEN_KEY = 'noubtek_token';
	// paste your real Client ID in frontend/.env as VITE_GOOGLE_CLIENT_ID=...
	const GOOGLE_CLIENT_ID =
		(import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) ??
		'YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com';

	let mode = $state<'login' | 'signup'>('login');
	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);
	let googleBtnHost = $state<HTMLDivElement>();
	let googleBtnWidth = $state(0);

	onMount(() => {
		if (localStorage.getItem(TOKEN_KEY)) {
			goto('/dashboard');
			return;
		}
		// load Google Identity Services, then render the real (popup-flow) button
		const s = document.createElement('script');
		s.src = 'https://accounts.google.com/gsi/client';
		s.async = true;
		s.defer = true;
		s.onload = () => {
			if (!window.google) return;
			window.google.accounts.id.initialize({
				client_id: GOOGLE_CLIENT_ID,
				callback: handleGoogleCredential
			});
			// render Google's own button (reliable; One Tap prompt() is often suppressed by Chrome).
			// it sits transparently over our custom button, so clicks reach it.
			if (googleBtnHost) {
				window.google.accounts.id.renderButton(googleBtnHost, {
					type: 'standard',
					theme: 'outline',
					size: 'large',
					text: 'continue_with',
					shape: 'pill',
					width: googleBtnWidth || 360
				});
			}
		};
		document.head.appendChild(s);
	});

	async function handleGoogleCredential(response: { credential: string }) {
		error = '';
		try {
			const data = await postAuth('/api/v1/auth/google', { token: response.credential });
			localStorage.setItem(TOKEN_KEY, data.access_token); // real JWT
			goto('/dashboard');
		} catch (err) {
			error = err instanceof Error ? err.message : 'Échec de la connexion Google.';
		}
	}

	async function postAuth(path: string, body: unknown) {
		const res = await fetch(path, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		});
		if (!res.ok) {
			let detail = res.statusText;
			try {
				detail = (await res.json()).detail ?? detail;
			} catch {
				/* non-JSON error */
			}
			throw new Error(typeof detail === 'string' ? detail : 'Erreur serveur');
		}
		return res.json();
	}

	async function submit(e?: Event) {
		e?.preventDefault();
		error = '';
		if (!email.trim() || !password.trim()) {
			error = 'Veuillez saisir votre email et votre mot de passe.';
			return;
		}
		loading = true;
		try {
			const path = mode === 'login' ? '/api/v1/auth/login' : '/api/v1/auth/signup';
			const data = await postAuth(path, { email, password });
			localStorage.setItem(TOKEN_KEY, data.access_token); // real JWT
			goto('/dashboard');
		} catch (err) {
			const msg = err instanceof Error ? err.message : String(err);
			error = msg === 'Invalid credentials' ? 'Email ou mot de passe incorrect.' : msg;
		} finally {
			loading = false;
		}
	}

</script>

<div class="relative flex min-h-screen flex-col bg-[#2f2be6] px-6 pb-28 pt-14 text-white">
	<!-- Headline (no logo, per spec) -->
	<div class="mx-auto w-full max-w-md text-center">
		<h1 class="font-['Poppins'] text-4xl font-extrabold leading-[1.1] tracking-tight sm:text-5xl">
			Bienvenue sur<br />Noubtek.
		</h1>
		<p class="mt-3 text-base text-indigo-200">La file d'attente, sans l'attente.</p>
	</div>

	<!-- White card -->
	<div class="mx-auto mt-8 w-full max-w-md rounded-[2rem] bg-white p-6 text-slate-900 shadow-xl">
		<!-- Log In / Sign Up toggle -->
		<div class="flex rounded-full bg-slate-100 p-1 text-sm font-semibold">
			<button
				type="button"
				onclick={() => (mode = 'login')}
				class="min-h-[44px] flex-1 rounded-full transition {mode === 'login'
					? 'bg-white text-slate-900 shadow-sm'
					: 'text-slate-500'}"
			>
				Connexion
			</button>
			<button
				type="button"
				onclick={() => (mode = 'signup')}
				class="min-h-[44px] flex-1 rounded-full transition {mode === 'signup'
					? 'bg-white text-slate-900 shadow-sm'
					: 'text-slate-500'}"
			>
				Inscription
			</button>
		</div>

		<form onsubmit={submit} class="mt-5 space-y-3">
			<div>
				<label class="text-xs font-medium text-slate-500" for="email">Email</label>
				<input
					id="email"
					type="email"
					bind:value={email}
					autocomplete="email"
					placeholder="vous@clinique.dz"
					class="mt-1 min-h-[44px] w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-sm focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100"
				/>
			</div>
			<div>
				<label class="text-xs font-medium text-slate-500" for="password">Mot de passe</label>
				<input
					id="password"
					type="password"
					bind:value={password}
					autocomplete={mode === 'login' ? 'current-password' : 'new-password'}
					placeholder="••••••••"
					class="mt-1 min-h-[44px] w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-sm focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100"
				/>
			</div>
			{#if error}<p class="text-xs text-rose-500">{error}</p>{/if}
			<!-- hidden submit so Enter works; visible submit is the fixed peach pill below -->
			<button type="submit" class="hidden" aria-hidden="true"></button>
		</form>

		<div class="my-4 flex items-center gap-3 text-xs text-slate-400">
			<span class="h-px flex-1 bg-slate-200"></span>ou<span class="h-px flex-1 bg-slate-200"></span>
		</div>

		<div class="relative" bind:clientWidth={googleBtnWidth}>
			<!-- visual custom button -->
			<div class="pointer-events-none flex min-h-[44px] w-full items-center justify-center gap-3 rounded-full border border-slate-200 bg-white text-sm font-semibold text-slate-700">
				<svg class="h-5 w-5" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.76h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.76c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"/></svg>
				Continuer avec Google
			</div>
			<!-- real Google button (popup flow), transparent, overlaid to catch the click -->
			<div bind:this={googleBtnHost} class="absolute inset-0 z-10 flex items-center justify-center overflow-hidden opacity-[0.001]"></div>
		</div>
	</div>

	<!-- Fixed bottom peach pill submit -->
	<div class="fixed inset-x-0 bottom-0 px-6 pb-5 pt-2">
		<button
			type="button"
			onclick={submit}
			disabled={loading}
			class="mx-auto flex min-h-[44px] w-full max-w-md items-center justify-center gap-2 rounded-full bg-[#f6b28e] py-4 text-base font-bold text-[#3a2416] shadow-lg transition hover:bg-[#f3a479] active:scale-[0.99] disabled:opacity-60"
		>
			{loading ? 'Connexion…' : mode === 'login' ? 'Se connecter' : 'Créer un compte'}
			{#if !loading}
				<svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14M13 6l6 6-6 6"/></svg>
			{/if}
		</button>
	</div>
</div>
