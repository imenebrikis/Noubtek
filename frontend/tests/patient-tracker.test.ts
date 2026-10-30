// Smoke test for /patient/[token]: load, mobile stacking, desktop 2-col, no ticket numbers.
// Backend is mocked via page.route so this runs with only the Vite dev server up.
// Run: `bun run dev` in one terminal, then `bun run test:e2e`. Screenshots land in test-results/.
import { chromium } from 'playwright';

const TOKEN = '3f2b8c1e-7a4d-4e9b-9c2a-1d5e6f708192';
const URL = `http://localhost:5173/patient/${TOKEN}`;
const FIXTURE = {
	clinic_name: 'Cabinet Test',
	patient_first_name: 'Sofia',
	status: 'waiting',
	position: 3,
	people_ahead: 2,
	eta_minutes: 30,
	consult_time: '11:00'
};

// ponytail: reuses the Chromium already cached by the Playwright MCP; set PW_CHROMIUM elsewhere,
// or unset it and run `bunx playwright install chromium` to use Playwright's bundled build.
const browser = await chromium.launch({
	executablePath:
		process.env.PW_CHROMIUM ?? process.env.LOCALAPPDATA + '\\ms-playwright\\chromium-1223\\chrome-win64\\chrome.exe'
});
const results: [string, boolean, string][] = [];
const check = (name: string, ok: boolean, info = '') => results.push([name, ok, info]);

async function open(width: number, height: number) {
	const page = await browser.newPage({ viewport: { width, height } });
	const errors: string[] = [];
	page.on('pageerror', (e) => errors.push(e.message));
	await page.route(`**/api/v1/public/track/${TOKEN}`, (r) => r.fulfill({ json: FIXTURE }));
	const res = await page.goto(URL);
	await page.getByRole('heading', { level: 1 }).waitFor();
	return { page, res, errors };
}

const sectionBoxes = async (page: any) =>
	Promise.all((await page.locator('main section').all()).map((s: any) => s.boundingBox()));

// Desktop
{
	const { page, res, errors } = await open(1440, 900);
	check('desktop: HTTP 200', res?.status() === 200, String(res?.status()));
	check('desktop: no page errors', errors.length === 0, errors.join('; '));
	const h1 = await page.getByRole('heading', { level: 1 }).textContent();
	check('desktop: headline rendered', h1?.trim() === 'Vous êtes 3e', h1 ?? '');
	check('desktop: h2 "Votre parcours"', await page.getByRole('heading', { name: 'Votre parcours' }).isVisible());
	check('desktop: aria-live region', (await page.locator('[aria-live="polite"]').count()) === 1);
	check('desktop: aria-current step', (await page.locator('[aria-current="step"]').textContent())?.includes('En attente') ?? false);
	const cols = await page.locator('main > div.grid').evaluate((el: Element) => getComputedStyle(el).gridTemplateColumns);
	check('desktop: grid has 2 columns', cols.split(' ').length === 2, cols);
	const [a, b] = await sectionBoxes(page);
	check('desktop: cards side by side', Math.abs(a.y - b.y) < 2 && b.x > a.x + a.width, `L(${a.x},${a.y}) R(${b.x},${b.y})`);
	await page.screenshot({ path: 'test-results/desktop.png', fullPage: true });

	// Ticket-number check: no token, no "ticket", no "#123" / "N° 12" / "No. 12" patterns anywhere in DOM
	const html = await page.content();
	const text = await page.locator('body').innerText();
	check('no tracking token in DOM', !html.includes(TOKEN));
	check('no "ticket" in DOM', !/ticket/i.test(html));
	const numPattern = /(#\s?\d+|N[°o]\.?\s?\d+|num[ée]ro\s*\d+)/i;
	check('no ticket-number patterns in text', !numPattern.test(text), text.match(numPattern)?.[0] ?? '');
	await page.close();
}

// Mobile
{
	const { page, errors } = await open(390, 844);
	check('mobile: no page errors', errors.length === 0, errors.join('; '));
	const cols = await page.locator('main > div.grid').evaluate((el: Element) => getComputedStyle(el).gridTemplateColumns);
	check('mobile: grid has 1 column', cols.split(' ').length === 1, cols);
	const [a, b] = await sectionBoxes(page);
	check('mobile: cards stacked vertically', b.y >= a.y + a.height && Math.abs(a.x - b.x) < 2, `L(${a.x},${a.y},h${a.height}) R(${b.x},${b.y})`);
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
	check('mobile: no horizontal overflow', !overflow);
	await page.screenshot({ path: 'test-results/mobile.png', fullPage: true });
	await page.close();
}

await browser.close();
for (const [n, ok, info] of results) console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${info ? `  [${info}]` : ''}`);
process.exit(results.every((r) => r[1]) ? 0 : 1);
