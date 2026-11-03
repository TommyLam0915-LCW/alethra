import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for the durable BTC/USD research record on the crypto
 * page: every simulated buy and sell is listed with its earn/loss reason,
 * exportable as CSV, and drawn as a marker on the candle chart.
 *
 * The marker half pins a defect this spec was written for: the page used to
 * clear the chart's markers in a parent effect that ran after the research
 * panel published them, so the panel said "markers are drawn" while the chart
 * held none. The chart now exposes `data-marker-count`, the number of fills the
 * series plugin actually received, and the spec cross-checks it against the
 * stored study and the loaded candles with an independent computation.
 *
 * Runs against the disposable E2E runtime created by `start_e2e_research_flow.sh`
 * (frontend :3099, backend :8099, database alethra_e2e). Each execution
 * registers its own disposable account because a study run is a real Kraken
 * fetch and counts against the account's research quota.
 */

interface Runtime {
  frontendBase: string;
  backendBase: string;
  email: string;
  password: string;
}

function loadRuntime(): Runtime {
  const fromFile: Record<string, string> = {};
  const credFile = process.env.E2E_CRED_FILE;
  if (credFile) {
    for (const line of readFileSync(credFile, 'utf-8').split('\n')) {
      const match = /^([A-Z0-9_]+)=(.*)$/.exec(line.trim());
      if (match) fromFile[match[1]] = match[2];
    }
  }
  const pick = (key: string): string | undefined => process.env[key] ?? fromFile[key];
  const email = pick('E2E_EMAIL');
  const password = pick('E2E_PASSWORD');
  if (!email || !password) {
    throw new Error('Missing E2E_EMAIL / E2E_PASSWORD. Set E2E_CRED_FILE to .local/e2e-credentials.env.');
  }
  return {
    frontendBase: pick('E2E_FRONTEND_BASE') ?? 'http://localhost:3099',
    backendBase: pick('E2E_BACKEND_BASE') ?? 'http://127.0.0.1:8099',
    email,
    password,
  };
}

interface StoredTrade {
  entry_time: string;
  exit_time: string;
}

interface StudyDetail {
  id: string;
  interval_minutes: number;
  trades: StoredTrade[];
}

interface SeriesPayload {
  completed: Array<{ open_time: string }>;
}

const runtime = loadRuntime();
const EMAIL = process.env.E2E_MARKER_EMAIL ?? `marker.journey.${Date.now()}@alethra.io`;
const PASSWORD = process.env.E2E_MARKER_PASSWORD ?? `MarkerJourney${Date.now()}!aA1`;
const INSTRUMENT = 'CRYPTO:KRAKEN:BTC-USD';
const INTERVAL = '1d';
const CHART_BARS = 500;

const chart = (page: Page) => page.getByTestId('crypto-chart');
const note = (page: Page) => page.getByTestId('strategy-research-marker-note');
const appliedMarkerCount = async (page: Page): Promise<number> => {
  const raw = await chart(page).getAttribute('data-marker-count');
  return Number(raw ?? '0');
};

async function token(page: Page): Promise<string> {
  const value = await page.evaluate(() => localStorage.getItem('token'));
  expect(value, 'login must store a token').toBeTruthy();
  return value as string;
}

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${runtime.backendBase}${path}`, {
    headers: { Authorization: `Bearer ${await token(page)}` },
  });
  expect(response.status(), `GET ${path}`).toBe(200);
  return (await response.json()) as T;
}

/** The chart's own rule: a fill still has a candle only if its bar is loaded. */
function expectedAppliedMarkers(detail: StudyDetail, series: SeriesPayload): number {
  const bars = new Set(series.completed.map((row) => Date.parse(row.open_time)));
  let count = 0;
  for (const trade of detail.trades) {
    if (bars.has(Date.parse(trade.entry_time))) count += 1;
    if (bars.has(Date.parse(trade.exit_time))) count += 1;
  }
  return count;
}

async function login(page: Page): Promise<void> {
  await page.goto(`${runtime.frontendBase}/login`, { waitUntil: 'networkidle' });
  const zone = page.locator('.login-form-zone');
  await zone.locator('input[type="email"]').fill(EMAIL);
  await zone.locator('input[type="password"]').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 });
}

async function openDailySeries(page: Page): Promise<void> {
  await page.goto(`${runtime.frontendBase}/market-data/crypto`, { waitUntil: 'networkidle' });
  const instrument = page.getByTestId('crypto-instrument-select');
  await instrument.waitFor({ state: 'visible', timeout: 60000 });
  await page.waitForFunction(
    () => ((document.querySelector('[data-testid="crypto-instrument-select"]') as HTMLSelectElement | null)?.options.length ?? 0) > 0,
    null,
    { timeout: 60000 },
  );
  await instrument.selectOption(INSTRUMENT);
  const interval = page.getByTestId('crypto-interval-select');
  await page.waitForFunction(
    () => ((document.querySelector('[data-testid="crypto-interval-select"]') as HTMLSelectElement | null)?.options.length ?? 0) > 0,
    null,
    { timeout: 60000 },
  );
  await interval.selectOption(INTERVAL);
}

test.describe('crypto research record and chart markers', () => {
  test.describe.configure({ mode: 'serial', timeout: 180000 });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        risk_acknowledged: true,
        email: EMAIL,
        password: PASSWORD,
        display_name: 'Marker Journey User',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register disposable account').toBe(201);
    await login(page);
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('a stored daily study lists every fill with a reason and exports it', async () => {
    await openDailySeries(page);

    await page.getByTestId('strategy-research-panel').waitFor({ state: 'visible', timeout: 60000 });
    await page.getByTestId('strategy-research-run').click();

    const detail = page.getByTestId('strategy-research-detail');
    await detail.waitFor({ state: 'visible', timeout: 120000 });
    await expect(note(page)).toContainText(/drawn on the chart from this study's \d+ out-of-sample trades/);
    const noteText = (await note(page).innerText()).replace(/\s+/g, ' ');
    const tradeCount = Number(/this study's (\d+) out-of-sample trades/.exec(noteText)?.[1]);
    expect(tradeCount, `marker note must state the trade count: ${noteText}`).toBeGreaterThan(0);
    expect(tradeCount).toBeLessThan(200);

    const rows = page.getByTestId('strategy-research-trades').locator('tbody tr');
    await expect(rows).toHaveCount(tradeCount);
    const reasonCells = page
      .getByTestId('strategy-research-trades')
      .locator('tbody tr td:last-child');
    await expect(reasonCells).toHaveCount(tradeCount);
    const reasons = await reasonCells.allInnerTexts();
    expect(
      reasons.every((cell) => cell.trim().length > 20),
      'every fill must state its earn/loss reason',
    ).toBe(true);

    const downloadPromise = page.waitForEvent('download', { timeout: 60000 });
    await page.getByTestId('strategy-research-csv').click();
    const download = await downloadPromise;
    const csvPath = await download.path();
    expect(csvPath, 'CSV download must produce a file').toBeTruthy();
    const csv = readFileSync(csvPath as string, 'utf8')
      .split(/\r?\n/)
      .filter((line) => line.trim());
    expect(csv.length - 1, 'CSV must carry one row per fill').toBe(tradeCount);

    // Keep the study for the reload check below.
    const runs = await apiGet<{ records: Array<{ id: string }> }>(
      page,
      `/api/v1/strategy-lab/research/runs?instrument_id=${encodeURIComponent(INSTRUMENT)}&limit=1`,
    );
    expect(runs.records.length).toBe(1);
  });

  test('the chart applies the fills as markers, and the toggle clears and restores them', async () => {
    const runs = await apiGet<{ records: Array<{ id: string }> }>(
      page,
      `/api/v1/strategy-lab/research/runs?instrument_id=${encodeURIComponent(INSTRUMENT)}&limit=1`,
    );
    const study = await apiGet<StudyDetail>(
      page,
      `/api/v1/strategy-lab/research/runs/${runs.records[0].id}?include_bars=true`,
    );
    expect(study.interval_minutes, 'the chart must show the study interval').toBe(1440);
    const series = await apiGet<SeriesPayload>(
      page,
      `/api/v1/market/crypto/series?instrument_id=${encodeURIComponent(INSTRUMENT)}&interval=${INTERVAL}&limit=${CHART_BARS}`,
    );
    const expected = expectedAppliedMarkers(study, series);
    expect(expected, 'at least one fill must fall inside the loaded candles').toBeGreaterThan(0);

    await expect.poll(() => appliedMarkerCount(page), { timeout: 30000 }).toBe(expected);

    const toggle = page.getByTestId('strategy-research-markers-toggle');
    await toggle.uncheck();
    await expect(note(page)).toContainText('Markers hidden by you');
    await expect.poll(() => appliedMarkerCount(page), { timeout: 30000 }).toBe(0);

    await toggle.check();
    await expect(note(page)).toContainText('drawn on the chart');
    await expect.poll(() => appliedMarkerCount(page), { timeout: 30000 }).toBe(expected);
  });

  test('reloading the page restores the stored record and its markers', async () => {
    await openDailySeries(page);
    const detail = page.getByTestId('strategy-research-detail');
    await detail.waitFor({ state: 'visible', timeout: 60000 });
    await expect(note(page)).toContainText(/drawn on the chart from this study's \d+ out-of-sample trades/);
    await expect.poll(() => appliedMarkerCount(page), { timeout: 30000 }).toBeGreaterThan(0);
  });
});
