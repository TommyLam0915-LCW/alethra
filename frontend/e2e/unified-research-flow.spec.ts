import { readFileSync } from 'fs';
import { test, expect, type Page, type Response } from '@playwright/test';

/**
 * Tracked regression for the unified research flow:
 * login -> experiment history -> direct run detail -> two-run selection ->
 * comparison identity -> save -> leave/reopen -> PATCH the same record -> reload.
 *
 * Runs against the disposable E2E runtime created by `start_e2e_research_flow.sh`
 * (frontend 127.0.0.1:3099, backend 127.0.0.1:8099, database alethra_e2e).
 * Credentials are read from the generated git-ignored file; nothing secret is
 * declared here.
 */

interface Runtime {
  frontendBase: string;
  email: string;
  password: string;
}

interface SavedComparisonCall {
  method: string;
  path: string;
  status: number;
  id: string;
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
    email,
    password,
  };
}

const runtime = loadRuntime();
// The seed script owns this title; it is the owner's own research content, so it
// stays exactly as seeded and the UI must render it untranslated.
const EXPERIMENT_TITLE = 'E2E 合成研究實驗（僅供自動化測試）';
const stamp = new Date().toISOString().replace(/[:.]/g, '-');

// purpose / notesV1 / notesV2 are free text the user typed. They stay in Chinese
// to prove the UI shows user content verbatim and never translates research notes.
const journey = {
  experimentPath: '',
  experimentId: '',
  runIds: [] as string[],
  completedRunIds: [] as string[],
  purpose: `E2E 檢查兩個合成 runs 嘅回撤差異 (${stamp})`,
  notesV1: `E2E 筆記第一版 (${stamp})`,
  notesV2: `E2E 筆記第二版 (${stamp})`,
  savedId: '',
};

// Response metadata only - bodies are parsed for the record id and never logged.
const savedComparisonCalls: SavedComparisonCall[] = [];

function recordCall(response: Response): void {
  const url = new URL(response.url());
  if (!url.pathname.includes('/saved-comparisons')) return;
  const method = response.request().method();
  const status = response.status();
  if (status >= 400) {
    savedComparisonCalls.push({ method, path: url.pathname, status, id: `HTTP ${status}` });
    return;
  }
  response
    .json()
    .then((body: { id?: unknown }) => {
      savedComparisonCalls.push({
        method,
        path: url.pathname,
        status,
        id: typeof body.id === 'string' ? body.id : '',
      });
    })
    .catch(() => undefined);
}

function historyCard(page: Page) {
  return page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Run history' }) });
}

function savedComparisonsCard(page: Page) {
  return page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Saved comparisons' }) });
}

function ownedSavedLinks(page: Page) {
  return savedComparisonsCard(page)
    .locator('a[href*="saved_comparison_id="]')
    .filter({ hasText: journey.purpose });
}

function runIdFromTestId(value: string | null): string {
  expect(value, 'expected a run-detail-link test id').not.toBeNull();
  return (value ?? '').replace('run-detail-link-', '');
}

test.describe('unified research flow', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    page.on('response', recordCall);

    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');

    const experimentCard = page.getByRole('link', { name: EXPERIMENT_TITLE, exact: true });
    await expect(experimentCard, 'E2E experiment must be listed on the dashboard').toHaveCount(1);
    const href = (await experimentCard.getAttribute('href')) ?? '';
    journey.experimentPath = new URL(href, runtime.frontendBase).pathname;
    journey.experimentId = journey.experimentPath.split('/').pop() ?? '';
    await experimentCard.click();
    await page.waitForURL(`**${journey.experimentPath}`);

    const detailLinks = historyCard(page).locator('[data-testid^="run-detail-link-"]');
    await expect(detailLinks).not.toHaveCount(0);
    const ids: string[] = [];
    for (let index = 0; index < (await detailLinks.count()); index += 1) {
      ids.push(runIdFromTestId(await detailLinks.nth(index).getAttribute('data-testid')));
    }
    journey.runIds = ids;

    // The list is ordered newest-first, so pick eligible (completed) runs by status.
    const completedRows = historyCard(page)
      .locator('[data-testid^="run-row-"]')
      .filter({ hasText: 'Completed' });
    // A resumed disposable runtime can carry completed runs added after the
    // seed; the flow needs two to compare and one more to prove the cap.
    await expect(completedRows, 'need two runs to compare and a third to prove the cap').not.toHaveCount(0);
    expect(
      await completedRows.count(),
      'need two runs to compare and a third to prove the cap'
    ).toBeGreaterThanOrEqual(3);
    const comparable: string[] = [];
    for (let index = 0; index < 3; index += 1) {
      const testId = await completedRows.nth(index).getAttribute('data-testid');
      comparable.push((testId ?? '').replace('run-row-', ''));
    }
    journey.completedRunIds = comparable;
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('opens a run detail straight from history and returns with selection untouched', async () => {
    const runId = journey.runIds[0];
    const detailLink = page.getByTestId(`run-detail-link-${runId}`);
    await expect(detailLink).toHaveAccessibleName(new RegExp(runId.slice(0, 8)));

    await detailLink.click();
    await expect(page).toHaveURL(new RegExp(`/experiments/.+/runs/${runId}$`));
    const detail = page.getByTestId('run-detail-page');
    await expect(detail).toBeVisible();
    await expect(detail).toContainText(runId);

    await page.getByTestId('run-detail-back').click();
    await page.waitForURL(`**${journey.experimentPath}`);

    const checkboxes = historyCard(page).locator('[data-testid^="run-select-"]');
    for (let index = 0; index < (await checkboxes.count()); index += 1) {
      await expect(checkboxes.nth(index), 'detail link must not toggle selection').not.toBeChecked();
    }
  });

  test('keyboard activation opens the run detail', async () => {
    const runId = journey.runIds[1];
    const detailLink = page.getByTestId(`run-detail-link-${runId}`);
    await detailLink.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
    await expect(page.getByTestId('run-detail-page')).toContainText(runId);

    await page.getByTestId('run-detail-back').click();
    await page.waitForURL(`**${journey.experimentPath}`);
  });

  test('selects exactly two runs and blocks a third', async () => {
    const [runA, runB, runC] = journey.completedRunIds;
    const boxA = page.getByTestId(`run-select-${runA}`);
    const boxB = page.getByTestId(`run-select-${runB}`);
    const boxC = page.getByTestId(`run-select-${runC}`);

    await boxA.click();
    await expect(boxA).toBeChecked();
    const clearButton = page.getByRole('button', { name: /Clear selection/ });
    await expect(clearButton, 'one of two selected offers a clear action').toBeVisible();

    // Keyboard selection works on the real control, not on the row.
    await boxA.focus();
    await page.keyboard.press('Space');
    await expect(boxA).not.toBeChecked();
    await expect(clearButton).toHaveCount(0);

    await boxA.click();
    await boxB.click();
    await expect(boxA).toBeChecked();
    await expect(boxB).toBeChecked();
    await expect(boxC, 'a third run must not be selectable').toBeDisabled();

    const compareLink = page.getByRole('link', { name: 'Compare selected runs' });
    await expect(compareLink).toHaveAttribute(
      'href',
      `${journey.experimentPath}/runs/compare?run_a=${runA}&run_b=${runB}`
    );
    await compareLink.click();
    await page.waitForURL('**/runs/compare**');
  });

  test('comparison names both runs and links back to their original detail pages', async () => {
    const params = new URL(page.url()).searchParams;
    const runA = params.get('run_a') ?? '';
    const runB = params.get('run_b') ?? '';
    expect([runA, runB], 'comparison carries the two runs selected in history').toEqual(
      journey.completedRunIds.slice(0, 2)
    );
    await expect(page.getByText('Selected results:')).toBeVisible();

    const runARow = page.getByTestId('comparison-run-a');
    await expect(runARow).toContainText('Run A');
    await expect(runARow).toContainText(`(${runA.slice(0, 8)}`);
    const runBRow = page.getByTestId('comparison-run-b');
    await expect(runBRow).toContainText('Run B');
    await expect(runBRow).toContainText(`(${runB.slice(0, 8)}`);

    // Both runs are in this experiment, so neither side may be presented as
    // belonging to some other experiment.
    await expect(runARow).not.toContainText('· experiment ');
    await expect(runBRow).not.toContainText('· experiment ');

    const runALink = runARow.locator('a');
    await expect(runALink).toHaveAttribute('href', `${journey.experimentPath}/runs/${runA}`);
    await runALink.click();
    await expect(page.getByTestId('run-detail-page')).toContainText(runA);

    await page.goBack();
    await page.waitForURL('**/runs/compare**');
    await expect(page.getByText('Selected results:')).toBeVisible();
  });

  test('states whether the two results are comparable like-for-like', async () => {
    const panel = page.getByTestId('comparison-comparability');
    await expect(panel, 'the server comparability report must be shown').toBeVisible();

    const verdict = page.getByTestId('comparability-verdict');
    await expect(verdict).toBeVisible();

    const dimensions = ['dataset', 'period', 'costs', 'engine_version', 'spec_source'];
    const statuses: string[] = [];
    for (const dimension of dimensions) {
      const row = page.getByTestId(`comparability-dimension-${dimension}`);
      await expect(row, `${dimension} must be reported`).toBeVisible();
      // Read the status cell only; A/B value cells may contain the word too.
      statuses.push((await row.locator('td').nth(1).innerText()).trim());
    }
    expect(statuses.every((status) => ['Match', 'Differs', 'Unknown'].includes(status))).toBe(true);

    // An unknown basis is never promoted to a match: the like-for-like
    // verdict may only appear when every reported basis is a match.
    const verdictText = (await verdict.innerText()).trim();
    if (verdictText.startsWith('Same basis')) {
      expect(statuses.every((status) => status === 'Match')).toBe(true);
    } else {
      expect(statuses.some((status) => status !== 'Match')).toBe(true);
    }
  });

  test('saves the comparison purpose and notes', async () => {
    await page.getByPlaceholder('e.g. Check how commission rate affects returns').fill(journey.purpose);
    await page.getByPlaceholder('Record your observations and conclusions…').fill(journey.notesV1);

    const responsePromise = page.waitForResponse(
      (res) => res.request().method() === 'POST' && res.url().includes('/api/v1/saved-comparisons')
    );
    // The page may only say "saved" after reading the record back. Capture that
    // GET so the test proves the claim rests on server state, not local state.
    const readbackPromise = page.waitForResponse(
      (res) =>
        res.request().method() === 'GET' &&
        /\/api\/v1\/saved-comparisons\/[0-9a-f-]{36}$/.test(new URL(res.url()).pathname)
    );
    await page.getByRole('button', { name: 'Save' }).click();
    const created = await responsePromise;
    expect(created.status()).toBe(201);
    const body = (await created.json()) as { id: string; notes: string };
    journey.savedId = body.id;
    expect(body.notes).toBe(journey.notesV1);

    const readback = await readbackPromise;
    expect(readback.status()).toBe(200);
    expect(((await readback.json()) as { id: string }).id).toBe(journey.savedId);

    await expect(page.getByText('Comparison saved')).toBeVisible();
    await expect(page.getByTestId('comparison-saved-id')).toHaveText(journey.savedId);
    await expect(page.getByRole('button', { name: 'Update' })).toBeVisible();
  });

  test('reopens the saved comparison after leaving the page', async () => {
    await page.goto(`${runtime.frontendBase}/dashboard`);
    await page.goto(`${runtime.frontendBase}${journey.experimentPath}`);

    await expect(ownedSavedLinks(page)).toHaveCount(1);
    // Both runs live in this experiment: the row must not claim a cross-
    // experiment comparison.
    await expect(
      savedComparisonsCard(page).getByTestId('saved-comparison-cross-experiment')
    ).toHaveCount(0);
    await ownedSavedLinks(page).first().click();
    await page.waitForURL(`**saved_comparison_id=${journey.savedId}`);

    await expect(page.getByPlaceholder('e.g. Check how commission rate affects returns')).toHaveValue(journey.purpose);
    await expect(page.getByPlaceholder('Record your observations and conclusions…')).toHaveValue(journey.notesV1);
  });

  test('updates the same saved record through PATCH, then survives a reload', async () => {
    await page.getByPlaceholder('Record your observations and conclusions…').fill(journey.notesV2);

    const responsePromise = page.waitForResponse(
      (res) =>
        res.request().method() === 'PATCH' &&
        res.url().endsWith(`/api/v1/saved-comparisons/${journey.savedId}`)
    );
    await page.getByRole('button', { name: 'Update' }).click();
    const updated = await responsePromise;
    expect(updated.status()).toBe(200);
    expect(((await updated.json()) as { id: string; notes: string }).id).toBe(journey.savedId);

    await page.reload();
    await page.waitForURL(`**saved_comparison_id=${journey.savedId}`);
    await expect(page.getByPlaceholder('Record your observations and conclusions…')).toHaveValue(journey.notesV2);
    await expect(page.getByPlaceholder('e.g. Check how commission rate affects returns')).toHaveValue(journey.purpose);

    // Persistence is server-side, not component state: the list page shows one owned row.
    await page.goto(`${runtime.frontendBase}${journey.experimentPath}`);
    await expect(ownedSavedLinks(page)).toHaveCount(1);

    const posts = savedComparisonCalls.filter((call) => call.method === 'POST');
    const patches = savedComparisonCalls.filter((call) => call.method === 'PATCH');
    expect(posts.map((call) => `${call.method} ${call.path} ${call.status}`)).toEqual([
      'POST /api/v1/saved-comparisons 201',
    ]);
    expect(patches.map((call) => `${call.method} ${call.path} ${call.status}`)).toEqual([
      `PATCH /api/v1/saved-comparisons/${journey.savedId} 200`,
    ]);
    const touchedIds = new Set([...posts, ...patches].map((call) => call.id));
    expect(touchedIds, 'one saved record touched, no duplicate created').toEqual(new Set([journey.savedId]));
  });
});
