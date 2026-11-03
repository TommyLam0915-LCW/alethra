import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for robustness studies (slice 2):
 * plan a bounded study -> see the pre-flight quota refusal -> re-plan with a
 * smaller study -> create -> execute -> read every attempt and the aggregate ->
 * reload and confirm the study survives -> cancel a second study and confirm it
 * starts no runs.
 *
 * Runs against the disposable E2E runtime created by `start_e2e_research_flow.sh`
 * (frontend :3099, backend :8099, database alethra_e2e). Credentials come from
 * the generated git-ignored file; nothing secret is declared here. The only
 * non-UI calls are read-only fetches used to prove the source version did not
 * change, that the timeline carries the study events, and that cancelling a
 * study starts no research runs.
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
  strategy_spec: Record<string, any>;
  config_hash: string | null;
}

const runtime = loadRuntime();
// Every attempt is a real research run and spends plan quota, so each execution
// registers its own disposable account instead of draining a shared one.
const ACCOUNT = {
  email: `robustness.journey.${Date.now()}@alethra.io`,
  password: `RobJourney${Date.now()}!aA1`,
};
const COVERED = 'US:AAPL';
const WINDOW_START = '2024-01-02';
const WINDOW_END = '2024-12-31';
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const TITLE = `E2E robustness study (automated test only) ${stamp}`;

const journey = {
  experimentId: '',
  versionId: '',
  versionBefore: null as VersionPayload | null,
  versionsBefore: 0,
  runsBefore: 0,
  studyId: '',
  cancelStudyId: '',
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

async function login(page: Page): Promise<void> {
  await page.goto(`${runtime.frontendBase}/login`, { waitUntil: 'networkidle' });
  const zone = page.locator('.login-form-zone');
  await zone.locator('input[type="email"]').fill(ACCOUNT.email);
  await zone.locator('input[type="password"]').fill(ACCOUNT.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 });
}

const panel = (page: Page) => page.getByTestId('robustness-panel');

test.describe('robustness study journey', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        risk_acknowledged: true,
        email: ACCOUNT.email,
        password: ACCOUNT.password,
        display_name: 'Robustness Journey User',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register disposable account').toBe(201);
    await login(page);
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('creates an experiment over a window that has stored bars', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/new`, { waitUntil: 'networkidle' });
    await page.getByTestId('exp-title').fill(TITLE);
    await page.locator('form select').first().selectOption('sma_crossover');
    await page.getByTestId('exp-asset').selectOption(COVERED);
    // Pin the window so slicing arithmetic does not depend on when the coverage
    // fetch resolves: 2024 has ~260 stored bars, 3 slices of >=21 bars each.
    await page.getByTestId('exp-start-date').fill(WINDOW_START);
    await page.getByTestId('exp-end-date').fill(WINDOW_END);
    await page.getByTestId('sma-short-window').fill('5');
    await page.getByTestId('sma-long-window').fill('20');
    await page.getByRole('button', { name: 'Create experiment', exact: true }).click();
    await page.waitForURL(/\/experiments\/[0-9a-f-]{36}$/, { timeout: 30000 });
    journey.experimentId = new URL(page.url()).pathname.split('/').pop() as string;

    const versions = await apiGet<VersionPayload[]>(
      page,
      `/api/v1/experiments/${journey.experimentId}/versions`,
    );
    expect(versions).toHaveLength(1);
    journey.versionBefore = versions[0];
    journey.versionId = versions[0].id;
    journey.versionsBefore = versions.length;
  });

  test('a nine-cell plan fits inside one authorization unit', async () => {
    await panel(page).waitFor({ timeout: 30000 });
    await page.getByTestId('robustness-kind').selectOption('cost_scenarios');
    await page.getByTestId('robustness-plan').click();

    const quota = page.getByTestId('robustness-quota');
    await expect(quota).toBeVisible({ timeout: 30000 });
    // The cost grid is 3 × 3 = 9 backtests, charged as one research-run unit,
    // so the free plan's remaining units can start it.
    await expect(page.getByTestId('robustness-estimated')).toContainText('estimated 9 backtests');
    await expect(quota).toContainText('1 research-run authorization');
    await expect(quota).toContainText('Quota is sufficient');
    await expect(page.getByTestId('robustness-start')).toBeEnabled();

    const runs = await apiGet<Array<{ id: string }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/runs`,
    );
    journey.runsBefore = runs.length;
    expect(journey.runsBefore, 'planning must not execute a backtest').toBe(0);
  });

  test('the smaller period-slice plan is priced, disclosed and ready', async () => {
    await page.getByTestId('robustness-kind').selectOption('period_slices');
    await page.getByTestId('robustness-slices').fill('3');
    await page.getByTestId('robustness-plan').click();

    const estimated = page.getByTestId('robustness-estimated');
    await expect(estimated).toContainText('estimated 3 backtests');
    await expect(estimated).toContainText('1 research-run authorization');
    await expect(page.getByTestId('robustness-quota')).toContainText('Quota is sufficient');
    await expect(page.getByTestId('robustness-plan-preview')).toContainText(
      'stored market data'
    );
    // The honest disclosures must be visible, not just stored.
    await expect(page.getByTestId('robustness-plan-preview')).toContainText(
      'Every attempt, including failed and skipped ones'
    );
    await expect(page.getByTestId('robustness-plan-preview')).toContainText(
      'Slices are contiguous and non-overlapping'
    );
    await expect(page.getByTestId('robustness-start')).toBeEnabled();
  });

  test('creating the study plans three attempts and runs nothing yet', async () => {
    await page.getByTestId('robustness-start').click();
    await expect(page.getByTestId('robustness-notice')).toContainText('Study created', {
      timeout: 30000,
    });
    await expect(page.getByTestId('robustness-detail')).toContainText('Planned, not run');
    await expect(page.getByTestId('robustness-attempts')).toHaveCount(0);

    const runs = await apiGet<Array<{ id: string }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/runs`,
    );
    expect(runs.length, 'creating a study must not spend a run').toBe(journey.runsBefore);
  });

  test('executing the study runs every cell and shows all attempts', async () => {
    await page.getByTestId('robustness-run').click();
    await expect(page.getByTestId('robustness-notice')).toContainText('3 attempts completed', {
      timeout: 180000,
    });

    const attempts = page.locator('[data-testid^="robustness-attempt-"]');
    await expect(attempts).toHaveCount(3);
    for (let i = 0; i < 3; i += 1) {
      await expect(attempts.nth(i)).toContainText('Completed');
    }
    await expect(page.getByTestId('robustness-aggregate')).toContainText('3 completed');
    await expect(page.getByTestId('robustness-aggregate')).toContainText('median');
    await expect(page.getByTestId('robustness-aggregate')).toContainText('failed 0');
    // The warning that the best single result is not evidence must be on screen.
    await expect(page.getByTestId('robustness-aggregate')).toContainText(
      'not robustness evidence'
    );

    const studies = await apiGet<Array<{ id: string; status: string; attempts: any[] }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/robustness`,
    );
    expect(studies).toHaveLength(1);
    expect(studies[0].status).toBe('completed');
    expect(studies[0].attempts).toHaveLength(3);
    expect(studies[0].attempts.every((a) => a.status === 'completed' && a.run_id)).toBe(true);
    journey.studyId = studies[0].id;

    const runs = await apiGet<Array<{ id: string }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/runs`,
    );
    expect(runs.length).toBe(journey.runsBefore + 3);
  });

  test('the study is recorded on the research timeline', async () => {
    const events = await apiGet<Array<{ event_type: string }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/timeline`,
    );
    const types = events.map((e) => e.event_type);
    expect(types).toContain('robustness_study_started');
    expect(types).toContain('robustness_study_completed');

    await page.getByTestId('exp-timeline-toggle').click();
    await expect(page.getByText('Robustness study completed').first()).toBeVisible({
      timeout: 15000,
    });
  });

  test('the study and its attempts survive a reload, and the version is untouched', async () => {
    await page.reload({ waitUntil: 'networkidle' });
    await panel(page).waitFor({ timeout: 30000 });
    await expect(page.getByTestId('robustness-detail')).toContainText('Completed');
    await expect(page.locator('[data-testid^="robustness-attempt-"]')).toHaveCount(3);
    await expect(page.getByTestId('robustness-aggregate')).toContainText('3 completed');

    const versions = await apiGet<VersionPayload[]>(
      page,
      `/api/v1/experiments/${journey.experimentId}/versions`,
    );
    expect(versions).toHaveLength(journey.versionsBefore);
    const source = versions.find((v) => v.id === journey.versionId);
    expect(source).toEqual(journey.versionBefore);
  });

  test('a cancelled study starts no runs', async () => {
    // Reloading restored the default study kind, so pick it again explicitly.
    await page.getByTestId('robustness-kind').selectOption('period_slices');
    await page.getByTestId('robustness-slices').fill('2');
    await page.getByTestId('robustness-plan').click();
    await expect(page.getByTestId('robustness-estimated')).toContainText('estimated 2 attempts');
    await expect(page.getByTestId('robustness-start')).toBeEnabled({ timeout: 30000 });
    await page.getByTestId('robustness-start').click();
    await expect(page.getByTestId('robustness-notice')).toContainText('Study created', {
      timeout: 30000,
    });

    const before = await apiGet<Array<{ id: string; status: string }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/robustness`,
    );
    expect(before).toHaveLength(2);
    const newest = before.find((s) => s.id !== journey.studyId);
    journey.cancelStudyId = newest!.id;

    await page.getByTestId('robustness-cancel').click();
    await expect(page.getByTestId('robustness-notice')).toContainText('Study cancelled', {
      timeout: 15000,
    });
    await expect(page.getByTestId('robustness-run')).toHaveCount(0);

    const after = await apiGet<Array<{ id: string; status: string; attempts: any[] }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/robustness`,
    );
    const cancelled = after.find((s) => s.id === journey.cancelStudyId);
    expect(cancelled!.status).toBe('cancelled');
    expect(cancelled!.attempts).toHaveLength(0);

    const runs = await apiGet<Array<{ id: string }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/runs`,
    );
    expect(runs.length, 'a cancelled study must not spend a run').toBe(journey.runsBefore + 3);
  });
});
