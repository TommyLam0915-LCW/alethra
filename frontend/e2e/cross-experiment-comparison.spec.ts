import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for the cross-experiment comparison gap:
 * create two experiments for one owner -> run one backtest in each -> open the
 * comparison page with one run from each experiment -> confirm each side keeps
 * its own experiment/version identity -> confirm a differing basis is never
 * presented as like-for-like -> a forced save failure never claims success ->
 * save with POST, read the record back by id, and only then accept the UI's
 * "saved" claim -> confirm the record is listed under its anchor experiment only
 * -> confirm another account gets a generic refusal that leaks no owner content.
 *
 * The product has no cross-experiment run picker, so the comparison URL is the
 * supported entry point; everything after that URL load is real UI interaction.
 *
 * Runs against the disposable E2E runtime created by `start_e2e_research_flow.sh`
 * (frontend :3099, backend :8099, database alethra_e2e). Credentials come from
 * the generated git-ignored file; nothing secret is declared here.
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

interface VersionPayload {
  id: string;
  version_number: number;
}

interface SavedComparisonPayload {
  id: string;
  scope: string;
  experiment_id: string;
  run_a_id: string;
  run_b_id: string;
  run_a_experiment_id: string;
  run_b_experiment_id: string;
  run_a_experiment_version_id: string;
  run_b_experiment_version_id: string;
  purpose: string;
  notes: string | null;
}

interface ComparePayload {
  run_a_id: string;
  run_b_id: string;
  experiment_a_id: string;
  experiment_b_id: string;
  experiment_version_a_id: string;
  experiment_version_b_id: string;
  sides: Array<{
    run_id: string;
    experiment_id: string;
    experiment_version_id: string;
    experiment_version_number: number | null;
    spec_source: string;
  }>;
  comparability: {
    same_experiment: boolean;
    fair_comparison: boolean;
    dimensions: Array<{ dimension: string; status: string }>;
  };
}

const runtime = loadRuntime();
// Each backtest costs plan quota, so this journey registers its own disposable
// account instead of draining a shared one.
const ACCOUNT = {
  email: `cross.exp.${Date.now()}@alethra.io`,
  password: `CrossExp${Date.now()}!cC3`,
};
const OUTSIDER = {
  email: `cross.exp.outsider.${Date.now()}@alethra.io`,
  password: `CrossOutsider${Date.now()}!dD4`,
};
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const TITLE_A = `E2E cross-experiment A (automated only) ${stamp}`;
const TITLE_B = `E2E cross-experiment B (automated only) ${stamp}`;
// Free text the user typed; kept in Chinese to prove the UI never translates
// research content. The purpose is also the lookup key for the saved row.
const PURPOSE = `跨實驗比較 ${stamp}`;
const NOTES = `同一 owner、兩個實驗、各一個已完成 run；成本基礎不同 ${stamp}`;
// Different commission rates make the costs basis differ deterministically, so
// a cross-experiment comparison must not be presented as like-for-like.
const COMMISSION_A = '0.001';
const COMMISSION_B = '0.002';

const journey = {
  experimentA: '',
  experimentB: '',
  versionA: '',
  versionB: '',
  runA: '',
  runB: '',
  comparisonId: '',
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

async function login(page: Page, email: string, password: string): Promise<void> {
  await page.goto(`${runtime.frontendBase}/login`, { waitUntil: 'networkidle' });
  const zone = page.locator('.login-form-zone');
  await zone.locator('input[type="email"]').fill(email);
  await zone.locator('input[type="password"]').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 });
}

async function createExperimentAndRun(
  page: Page,
  title: string,
  commissionRate: string,
): Promise<{ experimentId: string; versionId: string; runId: string }> {
  await page.goto(`${runtime.frontendBase}/experiments/new`, { waitUntil: 'networkidle' });
  await page.getByTestId('exp-title').fill(title);
  await page.locator('form select').first().selectOption('sma_crossover');
  await page.getByTestId('exp-asset').selectOption('US:AAPL');
  await page.getByTestId('sma-short-window').fill('5');
  await page.getByTestId('sma-long-window').fill('20');
  await page.getByLabel('Commission rate', { exact: true }).fill(commissionRate);
  await page.getByRole('button', { name: 'Create experiment', exact: true }).click();
  await page.waitForURL(/\/experiments\/[0-9a-f-]{36}$/, { timeout: 30000 });
  const experimentId = new URL(page.url()).pathname.split('/').pop() as string;

  const resultsCard = page
    .locator('div.card')
    .filter({ has: page.getByRole('heading', { name: 'Backtest results', exact: true }) });
  await resultsCard.getByRole('button', { name: 'Run Backtest', exact: true }).click();
  await expect(resultsCard.getByText('Total return', { exact: true })).toBeVisible({
    timeout: 120000,
  });

  const rows = page.locator('[data-testid^="run-row-"]');
  await expect(rows).toHaveCount(1, { timeout: 30000 });
  const runId = ((await rows.first().getAttribute('data-testid')) ?? '').replace('run-row-', '');
  expect(runId, 'the run history row must carry the created run id').toMatch(
    /^[0-9a-f-]{36}$/,
  );

  const versions = await apiGet<VersionPayload[]>(
    page,
    `/api/v1/experiments/${experimentId}/versions`,
  );
  expect(versions).toHaveLength(1);

  return { experimentId, versionId: versions[0].id, runId };
}

const comparePath = () =>
  `/experiments/${journey.experimentA}/runs/compare?run_a=${journey.runA}&run_b=${journey.runB}`;

test.describe('cross-experiment comparison journey', () => {
  test.describe.configure({ mode: 'serial', timeout: 240_000 });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        risk_acknowledged: true,
        email: ACCOUNT.email,
        password: ACCOUNT.password,
        display_name: 'Cross Experiment Owner',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register disposable owner account').toBe(201);
    await login(page, ACCOUNT.email, ACCOUNT.password);
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('creates two experiments with one completed run each', async () => {
    const a = await createExperimentAndRun(page, TITLE_A, COMMISSION_A);
    journey.experimentA = a.experimentId;
    journey.versionA = a.versionId;
    journey.runA = a.runId;

    const b = await createExperimentAndRun(page, TITLE_B, COMMISSION_B);
    journey.experimentB = b.experimentId;
    journey.versionB = b.versionId;
    journey.runB = b.runId;

    expect(journey.experimentA).not.toBe(journey.experimentB);
    expect(journey.runA).not.toBe(journey.runB);

    // The API must keep each run's own experiment and version identity.
    const compare = await apiGet<ComparePayload>(
      page,
      `/api/v1/runs/compare?run_a=${journey.runA}&run_b=${journey.runB}`,
    );
    expect(compare.experiment_a_id).toBe(journey.experimentA);
    expect(compare.experiment_b_id).toBe(journey.experimentB);
    expect(compare.experiment_version_a_id).toBe(journey.versionA);
    expect(compare.experiment_version_b_id).toBe(journey.versionB);
    expect(compare.comparability.same_experiment).toBe(false);
    const sides = new Map(compare.sides.map((side) => [side.run_id, side]));
    expect(sides.get(journey.runA)?.experiment_id).toBe(journey.experimentA);
    expect(sides.get(journey.runB)?.experiment_id).toBe(journey.experimentB);
    expect(sides.get(journey.runA)?.experiment_version_id).toBe(journey.versionA);
    expect(sides.get(journey.runB)?.experiment_version_id).toBe(journey.versionB);
  });

  test('keeps each side identity and never calls a differing basis like-for-like', async () => {
    await page.goto(`${runtime.frontendBase}${comparePath()}`, { waitUntil: 'networkidle' });
    await expect(page.getByText('Selected results:')).toBeVisible();
    await expect(page.getByTestId('comparison-experiment')).toContainText(TITLE_A);

    const runARow = page.getByTestId('comparison-run-a');
    const runBRow = page.getByTestId('comparison-run-b');

    await expect(runARow).toContainText('Run A');
    await expect(runARow).toContainText('version v1');
    await expect(runARow).toContainText(`(${journey.runA.slice(0, 8)}`);
    // Run A's experiment is the page anchor, so no cross-experiment marker.
    await expect(runARow).not.toContainText('· experiment ');

    await expect(runBRow).toContainText('Run B');
    await expect(runBRow).toContainText('version v1');
    await expect(runBRow).toContainText(`(${journey.runB.slice(0, 8)}`);
    // Run B belongs to its own experiment; the row must say which one.
    await expect(runBRow).toContainText(`· experiment ${journey.experimentB.slice(0, 8)}…`);

    await expect(page.getByTestId('comparison-run-a-spec-source')).toBeVisible();
    await expect(page.getByTestId('comparison-run-b-spec-source')).toBeVisible();

    await expect(runARow.locator('a')).toHaveAttribute(
      'href',
      `/experiments/${journey.experimentA}/runs/${journey.runA}`,
    );
    await expect(runBRow.locator('a')).toHaveAttribute(
      'href',
      `/experiments/${journey.experimentB}/runs/${journey.runB}`,
    );

    // Clicking the "outside" side opens the run in its own experiment, not in
    // the anchor experiment the comparison page was opened from.
    await runBRow.locator('a').click();
    await expect(page).toHaveURL(
      new RegExp(`/experiments/${journey.experimentB}/runs/${journey.runB}$`),
    );
    await expect(page.getByTestId('run-detail-page')).toContainText(journey.runB);
    await page.goBack();
    await page.waitForURL('**/runs/compare**');
    await expect(page.getByText('Selected results:')).toBeVisible();

    const panel = page.getByTestId('comparison-comparability');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('These runs belong to different experiments');
    await expect(panel).toContainText('Each side keeps its own experiment and version identity');

    const statuses: string[] = [];
    for (const dimension of ['dataset', 'period', 'costs', 'engine_version', 'spec_source']) {
      const row = page.getByTestId(`comparability-dimension-${dimension}`);
      await expect(row, `${dimension} must be reported`).toBeVisible();
      statuses.push((await row.locator('td').nth(1).innerText()).trim());
    }
    expect(statuses.every((status) => ['Match', 'Differs', 'Unknown'].includes(status))).toBe(true);

    // The two runs deliberately use different commission rates.
    await expect(
      page.getByTestId('comparability-dimension-costs').locator('td').nth(1),
    ).toHaveText('Differs');

    // Only an all-match basis may read "Same basis". This pair differs, so the
    // verdict may not claim a like-for-like comparison, and an Unknown basis is
    // never promoted to a match (the unknown-never-match rule is also pinned by
    // backend test_compare_runs_different_experiments_is_labelled_not_rejected).
    const verdict = page.getByTestId('comparability-verdict');
    await expect(verdict).not.toContainText('Same basis');
    await expect(verdict).toContainText('Not a like-for-like comparison');
    expect(statuses.some((status) => status !== 'Match')).toBe(true);
  });

  test('a failed save shows an error and never claims the comparison was saved', async () => {
    await page.getByPlaceholder('e.g. Check how commission rate affects returns').fill(PURPOSE);
    await page.getByPlaceholder('Record your observations and conclusions…').fill(NOTES);

    let postAttempts = 0;
    await page.route('**/api/v1/saved-comparisons', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }
      postAttempts += 1;
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'isolated test: save endpoint unavailable' }),
      });
    });

    try {
      await page.getByRole('button', { name: 'Save', exact: true }).click();

      // Next.js renders a route announcer with role="alert"; scope to the save
      // error by its own text so the assertion cannot pass on that empty node.
      const alert = page
        .getByRole('alert')
        .filter({ hasText: 'save endpoint unavailable' });
      await expect(alert, 'the failure must be shown to the user').toBeVisible({
        timeout: 15000,
      });
      await expect(alert).toContainText('save endpoint unavailable');
      await expect(page.getByTestId('comparison-save-success')).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeVisible();
      // The user's text survives a failed save.
      await expect(
        page.getByPlaceholder('e.g. Check how commission rate affects returns'),
      ).toHaveValue(PURPOSE);
      await expect(
        page.getByPlaceholder('Record your observations and conclusions…'),
      ).toHaveValue(NOTES);
      expect(postAttempts, 'exactly one POST was attempted').toBe(1);
    } finally {
      await page.unroute('**/api/v1/saved-comparisons');
    }

    // Nothing may have been persisted by the failed attempt.
    const stored = await apiGet<SavedComparisonPayload[]>(
      page,
      `/api/v1/experiments/${journey.experimentA}/saved-comparisons`,
    );
    expect(stored.filter((row) => row.purpose === PURPOSE)).toHaveLength(0);
  });

  test('reads the saved record back by id before claiming it was saved', async () => {
    const postPromise = page.waitForResponse(
      (res) =>
        res.request().method() === 'POST' &&
        res.url().endsWith('/api/v1/saved-comparisons'),
    );
    const readbackPromise = page.waitForResponse(
      (res) =>
        res.request().method() === 'GET' &&
        /\/api\/v1\/saved-comparisons\/[0-9a-f-]{36}$/.test(new URL(res.url()).pathname),
    );

    await page.getByRole('button', { name: 'Save', exact: true }).click();

    const created = await postPromise;
    expect(created.status()).toBe(201);
    const body = (await created.json()) as SavedComparisonPayload;
    journey.comparisonId = body.id;
    expect(body.purpose).toBe(PURPOSE);
    expect(body.scope).toBe('cross_experiment');
    // The page opened under experiment A, so A is the list anchor.
    expect(body.experiment_id).toBe(journey.experimentA);
    expect(body.run_a_experiment_id).toBe(journey.experimentA);
    expect(body.run_b_experiment_id).toBe(journey.experimentB);
    expect(body.run_a_experiment_version_id).toBe(journey.versionA);
    expect(body.run_b_experiment_version_id).toBe(journey.versionB);

    const readback = await readbackPromise;
    expect(readback.status()).toBe(200);
    const readBody = (await readback.json()) as SavedComparisonPayload;
    expect(readBody.id).toBe(body.id);
    expect(readBody.purpose).toBe(PURPOSE);

    await expect(page.getByTestId('comparison-save-success')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('comparison-saved-id')).toHaveText(body.id);
    await expect(page.getByRole('button', { name: 'Update' })).toBeVisible();
  });

  test('lists the record under its anchor experiment only, with a cross-experiment note', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentA}`, {
      waitUntil: 'networkidle',
    });
    const anchorRow = page.getByTestId('saved-comparison').filter({ hasText: PURPOSE });
    await expect(anchorRow).toHaveCount(1);
    await expect(anchorRow.getByTestId('saved-comparison-cross-experiment')).toBeVisible();
    await expect(anchorRow).toContainText(
      `Run B belongs to experiment ${journey.experimentB.slice(0, 8)}`,
    );

    // Experiment B is a participant, not the anchor: the row is not listed there.
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentB}`, {
      waitUntil: 'networkidle',
    });
    await expect(
      page.getByTestId('saved-comparison').filter({ hasText: PURPOSE }),
    ).toHaveCount(0);

    // Reopening the stored record fills the form from the server, not memory.
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentA}`, {
      waitUntil: 'networkidle',
    });
    await page
      .getByTestId('saved-comparison')
      .filter({ hasText: PURPOSE })
      .click();
    await page.waitForURL(`**saved_comparison_id=${journey.comparisonId}`);
    await expect(
      page.getByPlaceholder('e.g. Check how commission rate affects returns'),
    ).toHaveValue(PURPOSE);
    await expect(
      page.getByPlaceholder('Record your observations and conclusions…'),
    ).toHaveValue(NOTES);
  });

  test('another account is refused without leaking the owner content', async ({ browser }) => {
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        risk_acknowledged: true,
        email: OUTSIDER.email,
        password: OUTSIDER.password,
        display_name: 'Cross Experiment Outsider',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register outsider account').toBe(201);
    const loginResponse = await page.request.post(`${runtime.backendBase}/api/auth/login`, {
      data: { email: OUTSIDER.email, password: OUTSIDER.password },
    });
    expect(loginResponse.status()).toBe(200);
    const outsiderToken = (await loginResponse.json()).access_token as string;
    const outsiderHeaders = { Authorization: `Bearer ${outsiderToken}` };

    const compare = await page.request.get(
      `${runtime.backendBase}/api/v1/runs/compare?run_a=${journey.runA}&run_b=${journey.runB}`,
      { headers: outsiderHeaders },
    );
    expect(compare.status(), 'outsider compare').toBe(404);
    const compareBody = (await compare.json()) as { detail?: string };
    expect(compareBody.detail).toBe('Run not found or not authorized');

    const save = await page.request.post(`${runtime.backendBase}/api/v1/saved-comparisons`, {
      headers: outsiderHeaders,
      data: {
        experiment_id: journey.experimentA,
        run_a_id: journey.runA,
        run_b_id: journey.runB,
        purpose: 'outsider attempt',
      },
    });
    expect(save.status(), 'outsider save').toBe(404);
    const saveBody = (await save.json()) as { detail?: string };
    expect(saveBody.detail).toBe('Run A not found, not authorized, or not completed');
    expect(saveBody.detail).not.toContain(TITLE_A);
    expect(saveBody.detail).not.toContain(PURPOSE);

    const read = await page.request.get(
      `${runtime.backendBase}/api/v1/saved-comparisons/${journey.comparisonId}`,
      { headers: outsiderHeaders },
    );
    expect(read.status(), 'outsider read saved record').toBe(404);
    const readBody = (await read.json()) as { detail?: string };
    expect(readBody.detail).toBe('Saved comparison not found or not authorized');

    // The browser must show the same generic refusal and none of the owner's
    // research text or experiment title.
    const outsiderContext = await browser.newContext();
    const outsiderPage = await outsiderContext.newPage();
    try {
      await login(outsiderPage, OUTSIDER.email, OUTSIDER.password);

      await outsiderPage.goto(`${runtime.frontendBase}${comparePath()}`, {
        waitUntil: 'networkidle',
      });
      await expect(
        outsiderPage
          .getByRole('alert')
          .filter({ hasText: 'Run not found, or you do not have access' }),
      ).toBeVisible();
      const compareText = await outsiderPage.locator('body').innerText();
      expect(compareText).not.toContain(TITLE_A);
      expect(compareText).not.toContain(TITLE_B);
      expect(compareText).not.toContain(PURPOSE);
      expect(compareText).not.toContain(NOTES);

      await outsiderPage.goto(
        `${runtime.frontendBase}/experiments/${journey.experimentA}/runs/compare?saved_comparison_id=${journey.comparisonId}`,
        { waitUntil: 'networkidle' },
      );
      await expect(
        outsiderPage
          .getByRole('alert')
          .filter({ hasText: 'Saved comparison not found, or you do not have access' }),
      ).toBeVisible();
      const savedText = await outsiderPage.locator('body').innerText();
      expect(savedText).not.toContain(TITLE_A);
      expect(savedText).not.toContain(PURPOSE);
      expect(savedText).not.toContain(NOTES);
    } finally {
      await outsiderContext.close();
    }
  });
});
