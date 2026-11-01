import { json, type RequestHandler } from '@sveltejs/kit';

// Production bridge: every page calls /api/… on its own domain (login is /api/v1/auth/login);
// this forwards to the FastAPI backend at BACKEND_URL. Same-origin for the browser, so no
// CORS. In dev, vite.config.ts proxies /api before this route is ever reached.
//
// Diagnose a deployment by opening /api/bridge-health on the frontend domain.

// a sleeping free-tier backend can take ~50s to wake; Vercel reads this, other adapters ignore it
export const config = { maxDuration: 60 };

const HOP_BY_HOP = ['host', 'connection', 'content-length', 'accept-encoding', 'transfer-encoding'];

// tolerate the usual copy-paste variants: spaces, trailing slashes, a trailing /api
function backendUrl(): string {
	return (process.env.BACKEND_URL ?? '').trim().replace(/\/+$/, '').replace(/\/api$/i, '');
}

const forward: RequestHandler = async ({ request, params, url }) => {
	const backend = backendUrl();
	if (!backend) {
		return json({ detail: 'BACKEND_URL n’est pas configuré sur l’hébergeur du frontend.' }, { status: 500 });
	}

	if (params.path === 'bridge-health') {
		let status: number | string;
		try {
			status = (await fetch(`${backend}/health`, { signal: AbortSignal.timeout(55_000) })).status;
		} catch (e) {
			status = e instanceof Error ? e.message : String(e);
		}
		return json({ bridge: 'ok', backend_host: new URL(backend).host, backend_health: status });
	}

	const headers = new Headers(request.headers); // keeps Authorization + Content-Type
	for (const h of HOP_BY_HOP) headers.delete(h);

	let res: Response;
	try {
		res = await fetch(`${backend}/api/${params.path}${url.search}`, {
			method: request.method,
			headers,
			body: request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer(),
			redirect: 'manual',
			signal: AbortSignal.timeout(55_000)
		});
	} catch {
		// DNS / refused / timeout: answer clearly instead of letting the page hang
		return json({ detail: 'Serveur API injoignable. Réessayez dans un instant.' }, { status: 502 });
	}

	// fetch already decoded the body: drop headers that described the encoded bytes
	const out = new Headers(res.headers);
	for (const h of ['content-encoding', 'content-length', 'transfer-encoding']) out.delete(h);
	return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
};

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const PUT = forward;
export const DELETE = forward;
