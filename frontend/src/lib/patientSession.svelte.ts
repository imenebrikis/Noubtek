// What the patient navbar needs to know about this device's patient, kept in localStorage:
// the doctor they book with (so "Prise de rendez-vous" keeps its ?doctor=…) and their
// active tracking token (so "Suivi en direct" goes straight to it). Only values the API
// has accepted are stored; a stale token is cleared by the tracker when it 404s.
const KEYS = { doctorId: 'noubtek_doctor', token: 'noubtek_tracking' } as const;
type Key = keyof typeof KEYS;

export const patientSession = $state({ doctorId: '', token: '' });

// called from onMount (never during SSR) so server and client render the same first frame
export function loadPatientSession() {
	for (const key of Object.keys(KEYS) as Key[]) {
		try {
			patientSession[key] = localStorage.getItem(KEYS[key]) ?? '';
		} catch {
			// storage blocked (private mode): the navbar just falls back to the prompt
		}
	}
}

export function remember(key: Key, value: string) {
	patientSession[key] = value;
	try {
		if (value) localStorage.setItem(KEYS[key], value);
		else localStorage.removeItem(KEYS[key]);
	} catch {
		// non-fatal: state still works for this tab
	}
}
