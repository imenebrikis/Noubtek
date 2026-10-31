// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}

	interface Window {
		// Google Identity Services — injected at runtime by /login (accounts.google.com/gsi/client)
		google?: {
			accounts: {
				id: {
					initialize(options: { client_id: string; callback: (response: { credential: string }) => void }): void;
					renderButton(parent: HTMLElement, options: Record<string, unknown>): void;
				};
			};
		};
	}
}

export {};
