import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for strategy authoring (P1-A):
 * the new-experiment form must default its window on bars this server actually
 * stores, say so when an instrument has none, refuse to move a window the owner
 * typed, and freeze the version only through the lock confirmation card.
 *
 * Runs against the disposable E2E runtime created by `start_e2e_research_flow.sh`
 * (frontend 127.0.0.1:3099, backend 127.0.0.1:8099, database alethra_e2e).
 * Credentials come from the generated git-ignored file; nothing secret is
 * declared here. Every step is a real UI action; the only non-UI call is the
 * read-only coverage fetch that derives the expected window — API scaffolding
 * for the assertion, not evidence of the product path.
 */

interface Runtime {
  frontendBase: string;
  backendBase: string;
  email: string;
  password: string;
}

interface DataCoverage {
  stored_start: string | null;
  stored_end: string | null;
  stored_bars: number;
  available_start: string | null;
  available_end: string | null;
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

const runtime = loadRuntime();
const COVERED = 'US:AAPL';
const UNCOVERED = 'US:MSFT';
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const TITLE = `E2E default coverage strategy (automated test only) ${stamp}`;

async function fetchCoverage(instrumentId: string): Promise<DataCoverage> {
  const response = await fetch(`${runtime.backendBase}/api/v1/instruments/${instrumentId}`);
  expect(response.status, `coverage lookup for ${instrumentId}`).toBe(200);
  const body = (await response.json()) as { data_coverage: DataCoverage | null };
  expect(body.data_coverage, `${instrumentId} must report data_coverage`).not.toBeNull();
  return body.data_coverage as DataCoverage;
}

function addDays(isoDay: string, days: number): string {
  const d = new Date(`${isoDay}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

const startDate = (page: Page) => page.getByTestId('exp-start-date');
const endDate = (page: Page) => page.getByTestId('exp-end-date');
const assetSelect = (page: Page) => page.getByTestId('exp-asset');
const coverageLine = (page: Page) => page.getByTestId('exp-coverage-line');
const lockCard = (page: Page) =>
  page.locator('.card').filter({ has: page.getByTestId('exp-lock-confirm') });
const timelineCard = (page: Page) =>
  page.locator('.card').filter({ has: page.getByRole('heading', { name: /Research timeline/ }) });

async function openForm(page: Page): Promise<void> {
  await page.goto(`${runtime.frontendBase}/experiments/new`);
  await expect(coverageLine(page)).toBeVisible();
}

const journey = {
  covered: null as DataCoverage | null,
  uncovered: null as DataCoverage | null,
  picked: { start: '', end: '' },
  outside: { start: '', end: '' },
  experimentPath: '',
  putCalls: [] as string[],
};

// Test-controlled gate over the instrument-detail request: held only during the
// late-coverage test, and released by the test itself rather than by a timer.
let holdCoverage = false;
let releaseCoverage!: () => void;

test.describe('strategy authoring window and immutable lock', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    const gate = new Promise<void>((resolve) => {
      releaseCoverage = resolve;
    });
    await page.route(`**/api/v1/instruments/${COVERED}`, async (route) => {
      if (holdCoverage) await gate;
      await route.fallback();
    });
    page.on('request', (request) => {
      if (request.method() === 'PUT' && request.url().includes('/api/v1/experiments/')) {
        journey.putCalls.push(request.url());
      }
    });

    journey.covered = await fetchCoverage(COVERED);
    journey.uncovered = await fetchCoverage(UNCOVERED);
    const storedStart = journey.covered.stored_start;
    const storedEnd = journey.covered.stored_end;
    // Without this the whole spec would pass vacuously on an empty dataset.
    expect(journey.covered.stored_bars, `${COVERED} must have stored bars`).toBeGreaterThan(2);
    expect(Boolean(storedStart && storedEnd && storedStart < storedEnd), 'stored window must span days').toBe(true);
    expect(journey.uncovered.stored_bars, `${UNCOVERED} must have no stored bars`).toBe(0);

    // An owner-chosen window inside the stored range, and one entirely before it.
    journey.picked = { start: addDays(storedStart as string, 20), end: addDays(storedStart as string, 80) };
    journey.outside = { start: addDays(storedStart as string, -100), end: addDays(storedStart as string, -50) };

    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('defaults the window on stored bars, not on declared availability', async () => {
    await openForm(page);
    await expect(coverageLine(page)).toContainText(`the server stores bars from ${journey.covered!.stored_start}`);
    await expect(coverageLine(page)).toContainText('the provider declares it can supply');

    const today = new Date().toISOString().slice(0, 10);
    const storedStart = journey.covered!.stored_start as string;
    const storedEnd = journey.covered!.stored_end as string;
    const expectedEnd = storedEnd < today ? storedEnd : today;
    const horizon = addDays(expectedEnd, -366);

    await expect(endDate(page)).toHaveValue(expectedEnd);
    await expect(startDate(page)).toHaveValue(horizon < storedStart ? storedStart : horizon);
    // A window ending today would sit over empty storage for a historical dataset.
    expect(expectedEnd < today, 'stored window is in the past').toBe(true);
    // Declared availability reaches further than what is stored; it must not be the anchor.
    expect(expectedEnd).not.toBe(today);
  });

  test('labels an instrument with no stored bars instead of hiding it', async () => {
    await assetSelect(page).selectOption(UNCOVERED);
    await expect(page.getByTestId('exp-coverage-empty')).toBeVisible();
    await expect(page.getByTestId('exp-coverage-empty')).toContainText(UNCOVERED);
    await expect(coverageLine(page)).toContainText('(0 bars)');
  });

  test('warns when a hand-picked window cannot touch any stored bar', async () => {
    await assetSelect(page).selectOption(COVERED);
    await expect(coverageLine(page)).toContainText('the server stores bars from');
    await expect(page.getByTestId('exp-coverage-mismatch')).toHaveCount(0);

    await startDate(page).fill(journey.outside.start);
    await endDate(page).fill(journey.outside.end);
    const mismatch = page.getByTestId('exp-coverage-mismatch');
    await expect(mismatch).toBeVisible();
    await expect(mismatch).toContainText(journey.outside.start);
    await expect(mismatch).toContainText(journey.covered!.stored_end as string);
  });

  test('leaves a window the owner typed alone when coverage arrives late', async () => {
    holdCoverage = true;
    await page.goto(`${runtime.frontendBase}/experiments/new`);
    // Held response: no coverage yet, so the line says UNKNOWN rather than guessing.
    await expect(coverageLine(page)).toContainText('UNKNOWN');

    await startDate(page).fill(journey.picked.start);
    await endDate(page).fill(journey.picked.end);

    releaseCoverage();
    await expect(coverageLine(page)).toContainText('the server stores bars from');
    await expect(startDate(page)).toHaveValue(journey.picked.start);
    await expect(endDate(page)).toHaveValue(journey.picked.end);
    holdCoverage = false;
  });

  test('creates the experiment over the window the owner chose', async () => {
    await openForm(page);
    await assetSelect(page).selectOption(COVERED);
    await startDate(page).fill(journey.picked.start);
    await endDate(page).fill(journey.picked.end);
    await page.getByTestId('exp-title').fill(TITLE);

    const created = page.waitForResponse(
      (res) => res.request().method() === 'POST' && res.url().endsWith('/api/v1/experiments/')
    );
    await page.getByRole('button', { name: 'Create experiment' }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    // The submitted spec carries the window the form showed, not a naive default.
    const sent = response.request().postDataJSON() as {
      strategy_spec: { asset_ids: string[]; start_date: string; end_date: string };
    };
    expect(sent.strategy_spec.start_date).toBe(journey.picked.start);
    expect(sent.strategy_spec.end_date).toBe(journey.picked.end);
    expect(sent.strategy_spec.asset_ids).toEqual([COVERED]);

    await page.waitForURL(/\/experiments\/[0-9a-f-]+$/);
    journey.experimentPath = new URL(page.url()).pathname;
    await expect(page.getByTestId('exp-lock-btn')).toBeVisible();
  });

  test('cancel in the confirmation card freezes nothing', async () => {
    await page.getByTestId('exp-lock-btn').click();
    await expect(lockCard(page)).toContainText(COVERED);
    await expect(lockCard(page)).toContainText(`${journey.picked.start} to ${journey.picked.end}`);

    await page.getByTestId('exp-lock-cancel').click();
    await expect(page.getByTestId('exp-lock-confirm')).toHaveCount(0);
    expect(journey.putCalls, 'cancel must not issue the freeze PUT').toEqual([]);
    await expect(page.getByText('Draft', { exact: true })).toBeVisible();
  });

  test('confirming the card locks the version and survives a reload', async () => {
    await page.getByTestId('exp-lock-btn').click();
    const frozen = page.waitForResponse(
      (res) => res.request().method() === 'PUT' && res.url().endsWith(journey.experimentPath)
    );
    await page.getByTestId('exp-lock-confirm').click();
    const response = await frozen;
    expect(response.status()).toBe(200);
    expect(((await response.json()) as { status: string }).status).toBe('locked');
    expect(journey.putCalls, 'exactly one freeze').toHaveLength(1);

    await expect(page.getByText('Locked', { exact: true })).toBeVisible();
    await expect(page.getByTestId('exp-lock-btn')).toHaveCount(0);
    await expect(page.getByTestId('exp-lock-confirm')).toHaveCount(0);
    await expect(page.getByTestId('exp-simulation-link')).toBeVisible();

    // Persistence is server-side, not component state.
    await page.reload();
    await expect(page.getByText('Locked', { exact: true })).toBeVisible();
    await expect(page.getByTestId('exp-lock-btn')).toHaveCount(0);
    await expect(page.getByTestId('exp-simulation-link')).toBeVisible();

    await timelineCard(page).getByRole('button', { name: 'Expand' }).click();
    await expect(timelineCard(page)).toContainText('Strategy locked');
  });
});
