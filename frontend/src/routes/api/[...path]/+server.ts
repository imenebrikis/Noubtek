import { error, type RequestHandler } from '@sveltejs/kit';

// Production bridge: pages call /api/… on their own domain; this forwards to the FastAPI
// backend (BACKEND_URL, e.g. https://noubtek-api.onrender.com). Same-origin for the browser,
// so no CORS needed. In dev, vite.config.ts proxies /api before this route is ever reached.
const HOP_BY_HOP = ['host', 'connection', 'content-length', 'accept-encoding', 'transfer-encoding'];

const forward: RequestHandler = async ({ request, params, url }) => {
	const backend = process.env.BACKEND_URL?.replace(/\/+$/, '');
	if (!backend) error(500, 'BACKEND_URL is not set on the frontend host');

	const headers = new Headers(request.headers); // keeps Authorization + Content-Type
	for (const h of HOP_BY_HOP) headers.delete(h);

	const res = await fetch(`${backend}/api/${params.path}${url.search}`, {
		method: request.method,
		headers,
		body: request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer(),
		redirect: 'manual'
	});

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
