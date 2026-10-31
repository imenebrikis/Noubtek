import { redirect } from '@sveltejs/kit';

// the base domain is the clinic's entry point: send it straight to the staff login
// (patients arrive through their own /book?doctor=… and /patient/… links)
export function load() {
	redirect(307, '/login');
}
