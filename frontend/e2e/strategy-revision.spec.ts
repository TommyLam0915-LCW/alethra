import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for strategy revisions (slice 1):
 * create an experiment -> run it -> derive a revision that changes one rule ->
 * run the revision -> save the A/B comparison -> record whether the revision met
 * its expectation -> reload and confirm both the conclusion and the compare
 * entry survive, while the source version is untouched.
 *
 * Runs against the disposable E2E runtime created by `start_e2e_research_flow.sh`
 * (frontend :3099, backend :8099, database alethra_e2e). Credentials come from
 * the generated git-ignored file; nothing secret is declared here. The only
 * non-UI calls are read-only version/experiment fetches used to prove the source
 * version did not change.
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
  assumptions: Record<string, any> | null;
  config_hash: string | null;
  locked_at: string | null;
}

const runtime = loadRuntime();
// A run costs plan quota, so each execution registers its own disposable account
// instead of draining a shared one. The journey itself is unchanged.
const ACCOUNT = {
  email: `revision.journey.${Date.now()}@alethra.io`,
  password: `RevJourney${Date.now()}!aA1`,
};
const COVERED = 'US:AAPL';
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const TITLE = `E2E 策略修訂（僅供自動化測試）${stamp}`;
const MARKER = `rev-${Date.now()}`;
const CHANGE_REASON = `自動化驗證 ${MARKER}：長均線窗口 20 → 30，檢驗訊號頻率對報酬的影響`;
const EXPECTATION = '長均線拉長後，交易次數應減少';
const PURPOSE = `修訂 vs 來源 A/B 比較 ${MARKER}`;
const NOTES = `來源版本與修訂版本在同一資料期間下的差異；結論記錄於修訂紀錄 ${MARKER}`;
const OUTCOME = `已完成 A/B 回測並記錄結論 ${MARKER}`;

const journey = {
  experimentId: '',
  sourceVersionId: '',
  revisionVersionId: '',
  sourceBefore: null as VersionPayload | null,
  currentVersionBefore: '',
  statusBefore: '',
  revisionSpec: null as Record<string, any> | null,
  revisionHash: '',
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

async function versions(page: Page): Promise<VersionPayload[]> {
  return apiGet<VersionPayload[]>(page, `/api/v1/experiments/${journey.experimentId}/versions`);
}

async function login(page: Page): Promise<void> {
  await page.goto(`${runtime.frontendBase}/login`, { waitUntil: 'networkidle' });
  const zone = page.locator('.login-form-zone');
  await zone.locator('input[type="email"]').fill(ACCOUNT.email);
  await zone.locator('input[type="password"]').fill(ACCOUNT.password);
  await page.getByRole('button', { name: '登入', exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 });
}

const revisionRows = (page: Page) =>
  page.locator('[data-testid^="revision-row-"]').filter({ hasText: MARKER });

test.describe('strategy revision journey', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        email: ACCOUNT.email,
        password: ACCOUNT.password,
        display_name: 'Revision Journey User',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register disposable account').toBe(201);
    await login(page);
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('creates an experiment and runs its first version', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/new`, { waitUntil: 'networkidle' });
    await page.getByTestId('exp-title').fill(TITLE);
    await page.locator('form select').first().selectOption('sma_crossover');
    await page.getByTestId('exp-asset').selectOption(COVERED);
    await page.getByTestId('sma-short-window').fill('5');
    await page.getByTestId('sma-long-window').fill('20');
    await page.getByRole('button', { name: '建立實驗', exact: true }).click();
    await page.waitForURL(/\/experiments\/[0-9a-f-]{36}$/, { timeout: 30000 });
    journey.experimentId = new URL(page.url()).pathname.split('/').pop() as string;

    const resultsCard = page
      .locator('div.card')
      .filter({ has: page.getByRole('heading', { name: '回測結果', exact: true }) });
    await resultsCard.getByRole('button', { name: '執行回測', exact: true }).click();
    await expect(resultsCard.getByText('總報酬', { exact: true })).toBeVisible({ timeout: 120000 });

    const before = await versions(page);
    expect(before).toHaveLength(1);
    journey.sourceBefore = before[0];
    journey.sourceVersionId = before[0].id;
    expect(before[0].strategy_spec.signal_parameters.long_window).toBe(20);
    const experiment = await apiGet<{ current_version_id: string; status: string }>(
      page,
      `/api/v1/experiments/${journey.experimentId}`,
    );
    journey.currentVersionBefore = experiment.current_version_id;
    journey.statusBefore = experiment.status;
  });

  test('seeded revision form reports no change before the owner edits anything', async () => {
    await page.getByTestId('revision-panel').waitFor({ timeout: 30000 });
    await page.getByTestId('revision-open').click();
    await page.getByTestId('revision-form').waitFor({ timeout: 15000 });
    // Regression: equal arrays used to compare by reference, so asset_ids
    // showed up as an edit the owner never made.
    await expect(page.getByTestId('revision-preview').getByText('目前沒有變更')).toBeVisible();
  });

  test('a one-rule change is the only difference the preview reports', async () => {
    await page.getByTestId('revision-long-window').fill('30');
    await page.getByTestId('revision-change-reason').fill(CHANGE_REASON);
    await page.getByTestId('revision-expectation').fill(EXPECTATION);

    const preview = page.getByTestId('revision-preview');
    await expect(preview).toContainText('策略設定變更');
    await expect(preview).toContainText('signal_parameters.long_window：20 → 30');
    for (const noise of ['schema_version', 'frequency', 'asset_ids', 'dataset.', 'synthetic']) {
      await expect(preview).not.toContainText(noise);
    }

    await page.getByTestId('revision-submit').click();
    await expect(page.getByTestId('revision-notice')).toContainText('已建立修訂版本');
    await expect(revisionRows(page)).toHaveCount(1);

    const after = await versions(page);
    expect(after).toHaveLength(2);
    const revision = after.find((v) => v.version_number === 2);
    expect(revision).toBeTruthy();
    journey.revisionVersionId = revision!.id;
    expect(revision!.assumptions?.revision.changed_fields).toEqual([
      'signal_parameters.long_window',
    ]);
    expect(revision!.assumptions?.revision.of_version_id).toBe(journey.sourceVersionId);
    expect(revision!.assumptions?.revision.of_version_number).toBe(1);
    expect(revision!.strategy_spec.signal_parameters.long_window).toBe(30);
    journey.revisionSpec = revision!.strategy_spec;
    journey.revisionHash = revision!.config_hash as string;
  });

  test('the source version is not rewritten by the revision', async () => {
    const after = await versions(page);
    const source = after.find((v) => v.id === journey.sourceVersionId);
    expect(source).toEqual(journey.sourceBefore);
    expect(source!.strategy_spec.signal_parameters.long_window).toBe(20);

    const experiment = await apiGet<{ current_version_id: string; status: string }>(
      page,
      `/api/v1/experiments/${journey.experimentId}`,
    );
    // An unlocked experiment points at its newest version; a locked one must keep
    // the pointer where the replay entry expects it (covered by the backend
    // suite, which locks the source before revising).
    if (journey.statusBefore === 'locked') {
      expect(experiment.current_version_id).toBe(journey.currentVersionBefore);
    } else {
      expect(experiment.current_version_id).toBe(journey.revisionVersionId);
    }
  });

  test('the revision can be researched and compared against its source', async () => {
    const row = revisionRows(page);
    await row.locator('[data-testid^="revision-run-"]').click();
    await expect(page.getByTestId('revision-notice')).toContainText('回測完成', {
      timeout: 120000,
    });
    await expect(row.getByText('修訂回測：已完成')).toBeVisible({ timeout: 120000 });

    const compareLink = row.locator('[data-testid^="revision-compare-"]');
    await expect(compareLink).toBeVisible({ timeout: 60000 });
    await compareLink.click();
    await page.waitForURL((url) => url.pathname.includes('/runs/compare'), { timeout: 30000 });
    await expect(page.getByRole('heading', { name: '儲存比較', exact: true })).toBeVisible();
    await expect(page.getByText('選取嘅結果：')).toBeVisible();

    await page.getByPlaceholder('例如：檢查佣金率對回報嘅影響').fill(PURPOSE);
    await page.getByPlaceholder('記錄你嘅觀察同結論...').fill(NOTES);
    await page.getByRole('button', { name: '儲存', exact: true }).click();
    await expect(page.getByText('比較已儲存')).toBeVisible({ timeout: 30000 });

    const stored = await apiGet<Array<{ purpose: string; notes: string }>>(
      page,
      `/api/v1/experiments/${journey.experimentId}/saved-comparisons`,
    );
    const match = stored.filter((c) => c.purpose === PURPOSE);
    expect(match).toHaveLength(1);
    expect(match[0].notes).toBe(NOTES);
  });

  test('the research conclusion survives a reload', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentId}`, {
      waitUntil: 'networkidle',
    });
    const row = revisionRows(page);
    await row.waitFor({ timeout: 30000 });
    await row.locator('[data-testid^="revision-outcome-met-"]').selectOption({ label: '符合預期' });
    await row.locator('[data-testid^="revision-outcome-text-"]').fill(OUTCOME);
    await row.locator('[data-testid^="revision-outcome-save-"]').click();
    await expect(page.getByTestId('revision-notice')).toContainText('已記錄');
    await expect(row.locator('[data-testid^="revision-outcome-value-"]')).toBeVisible();

    await page.reload({ waitUntil: 'networkidle' });
    const reloaded = revisionRows(page);
    await reloaded.waitFor({ timeout: 30000 });
    await expect(reloaded.locator('[data-testid^="revision-outcome-value-"]')).toContainText(
      '符合預期',
    );
    await expect(reloaded.locator('[data-testid^="revision-outcome-value-"]')).toContainText(MARKER);
    await expect(reloaded.locator('[data-testid^="revision-compare-"]')).toBeVisible({ timeout: 30000 });
    await expect(reloaded.getByText('修訂回測：已完成')).toBeVisible();
    await expect(page.getByText('總報酬', { exact: true })).toBeVisible();

    const stored = await versions(page);
    const revision = stored.find((v) => v.id === journey.revisionVersionId);
    expect(revision!.assumptions?.revision.outcome).toBe(OUTCOME);
    expect(revision!.assumptions?.revision.met_expectation).toBe(true);
    // Recording an outcome is an annotation: spec and hash must not move.
    expect(revision!.strategy_spec).toEqual(journey.revisionSpec);
    expect(revision!.config_hash).toBe(journey.revisionHash);
  });
});
