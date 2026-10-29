import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for the experiment research record (slice 3):
 * create an experiment -> run it -> derive a revision -> run the revision ->
 * save the A/B comparison -> record that the revision missed its expectation ->
 * open the research-record panel and confirm it reports the same evidence ->
 * download the full record and check the file really carries the revision
 * conclusion, the comparison and the exclusions -> reload -> confirm the record
 * survives -> confirm another account cannot read it.
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
  strategy_spec: Record<string, any>;
  assumptions: Record<string, any> | null;
}

interface StoredComparison {
  id: string;
  purpose: string;
  notes: string | null;
}

const runtime = loadRuntime();
// A run costs plan quota, so each execution registers its own disposable account
// instead of draining a shared one. The journey itself is unchanged.
const ACCOUNT = {
  email: `record.journey.${Date.now()}@alethra.io`,
  password: `RecordJourney${Date.now()}!aA1`,
};
const OUTSIDER = {
  email: `record.outsider.${Date.now()}@alethra.io`,
  password: `RecordOutsider${Date.now()}!bB2`,
};
const COVERED = 'US:AAPL';
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const TITLE = `E2E 研究紀錄（僅供自動化測試）${stamp}`;
const MARKER = `rec-${Date.now()}`;
const CHANGE_REASON = `自動化驗證 ${MARKER}：長均線窗口 20 → 30，檢驗訊號頻率對報酬的影響`;
const EXPECTATION = '長均線拉長後，交易次數應減少';
const PURPOSE = `修訂 vs 來源比較 ${MARKER}`;
const NOTES = `來源版本與修訂版本在同一資料期間下的差異 ${MARKER}`;
const OUTCOME = `A/B 回測顯示交易次數下降，但總報酬未達預期 ${MARKER}`;

const journey = {
  experimentId: '',
  sourceVersionId: '',
  revisionVersionId: '',
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

const countValue = (page: Page, testId: string) =>
  page.getByTestId(testId).locator('div').last();

test.describe('research record journey', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        email: ACCOUNT.email,
        password: ACCOUNT.password,
        display_name: 'Record Journey User',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register disposable account').toBe(201);
    await login(page);
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('creates an experiment, runs it and derives a revision', async () => {
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

    const versions = await apiGet<VersionPayload[]>(
      page,
      `/api/v1/experiments/${journey.experimentId}/versions`,
    );
    expect(versions).toHaveLength(1);
    journey.sourceVersionId = versions[0].id;

    await page.getByTestId('revision-panel').waitFor({ timeout: 30000 });
    await page.getByTestId('revision-open').click();
    await page.getByTestId('revision-form').waitFor({ timeout: 15000 });
    await page.getByTestId('revision-long-window').fill('30');
    await page.getByTestId('revision-change-reason').fill(CHANGE_REASON);
    await page.getByTestId('revision-expectation').fill(EXPECTATION);
    await page.getByTestId('revision-submit').click();
    await expect(page.getByTestId('revision-notice')).toContainText('已建立修訂版本');

    const after = await apiGet<VersionPayload[]>(
      page,
      `/api/v1/experiments/${journey.experimentId}/versions`,
    );
    expect(after).toHaveLength(2);
    const revision = after.find((v) => v.version_number === 2);
    expect(revision).toBeTruthy();
    journey.revisionVersionId = revision!.id;
  });

  test('runs the revision and saves the A/B comparison', async () => {
    const row = revisionRows(page);
    await row.locator('[data-testid^="revision-run-"]').click();
    await expect(page.getByTestId('revision-notice')).toContainText('回測完成', {
      timeout: 120000,
    });
    await expect(row.getByText('修訂回測：已完成')).toBeVisible({ timeout: 120000 });

    const compareLink = row.locator('[data-testid^="revision-compare-"]');
    await compareLink.click();
    await page.waitForURL((url) => url.pathname.includes('/runs/compare'), { timeout: 30000 });
    await page.getByPlaceholder('例如：檢查佣金率對回報嘅影響').fill(PURPOSE);
    await page.getByPlaceholder('記錄你嘅觀察同結論...').fill(NOTES);
    await page.getByRole('button', { name: '儲存', exact: true }).click();
    await expect(page.getByText('比較已儲存')).toBeVisible({ timeout: 30000 });

    const stored = await apiGet<StoredComparison[]>(
      page,
      `/api/v1/experiments/${journey.experimentId}/saved-comparisons`,
    );
    const match = stored.filter((c) => c.purpose === PURPOSE);
    expect(match).toHaveLength(1);
    journey.comparisonId = match[0].id;
  });

  test('the research record reports the revision, the comparison and the emptiness', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentId}`, {
      waitUntil: 'networkidle',
    });
    const row = revisionRows(page);
    await row.waitFor({ timeout: 30000 });
    await row.locator('[data-testid^="revision-outcome-met-"]').selectOption('false');
    await row.locator('[data-testid^="revision-outcome-text-"]').fill(OUTCOME);
    await row.locator('[data-testid^="revision-outcome-save-"]').click();
    await expect(page.getByTestId('revision-notice')).toContainText('已記錄');

    await page.reload({ waitUntil: 'networkidle' });
    const panel = page.getByTestId('record-panel');
    await panel.getByTestId('record-counts').waitFor({ timeout: 30000 });

    await expect(countValue(page, 'record-count-versions')).toHaveText('2');
    await expect(countValue(page, 'record-count-revisions')).toHaveText('1');
    await expect(countValue(page, 'record-count-runs')).toHaveText('2');
    await expect(countValue(page, 'record-count-studies')).toHaveText('0');
    await expect(countValue(page, 'record-count-attempts')).toHaveText('0');
    await expect(countValue(page, 'record-count-comparisons')).toHaveText('1');
    await expect(countValue(page, 'record-count-decisions')).toHaveText('0');
    await expect(countValue(page, 'record-count-human')).toHaveText('0');
    await expect(countValue(page, 'record-count-notes')).toHaveText('0');

    const revisionEntry = panel.getByTestId(`record-revision-${journey.revisionVersionId}`);
    await expect(revisionEntry).toContainText('v2');
    await expect(revisionEntry).toContainText('v1');
    await expect(revisionEntry).toContainText('策略設定變更');
    await expect(revisionEntry).toContainText('signal_parameters.long_window');
    await expect(revisionEntry).toContainText(CHANGE_REASON);
    await expect(revisionEntry).toContainText(EXPECTATION);
    await expect(revisionEntry).toContainText('未符預期');
    await expect(revisionEntry).toContainText(OUTCOME);

    await expect(panel.getByTestId(`record-comparison-${journey.comparisonId}`)).toContainText(
      PURPOSE,
    );
    await expect(panel.getByTestId('record-studies-empty')).toBeVisible();
    await expect(panel.getByTestId('record-sessions-empty')).toBeVisible();
    await expect(panel.getByTestId('record-revisions-empty')).toHaveCount(0);

    const scope = panel.getByTestId('record-scope');
    await expect(scope).toContainText('experiment-record-2');
    await expect(scope).toContainText('逐筆交易明細');
    await expect(scope).toContainText('不是投資建議');
  });

  test('the downloaded record carries the conclusion, the comparison and the exclusions', async () => {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('record-download').click(),
    ]);
    expect(download.suggestedFilename()).toBe(
      `alethra-record-${journey.experimentId.slice(0, 8)}.json`,
    );
    await expect(page.getByTestId('record-notice')).toContainText('已下載完整研究紀錄');

    const filePath = await download.path();
    expect(filePath, 'download must have a local file').toBeTruthy();
    const record = JSON.parse(readFileSync(filePath as string, 'utf-8'));

    expect(record.schema_version).toBe('experiment-record-2');
    expect(record.experiment.id).toBe(journey.experimentId);
    expect(record.coverage.versions).toBe(2);
    expect(record.coverage.revisions).toBe(1);
    expect(record.coverage.runs).toBe(2);
    expect(record.coverage.completed_runs).toBe(2);
    expect(record.coverage.saved_comparisons).toBe(1);
    expect(record.coverage.robustness_studies).toBe(0);
    expect(record.coverage.simulation_decisions).toBe(0);
    expect(record.coverage.excluded).toContain('runs.result_json.transactions');

    const revision = record.decision_review.revisions[0];
    expect(revision.version_number).toBe(2);
    expect(revision.base_version_number).toBe(1);
    expect(revision.met_expectation).toBe(false);
    expect(revision.outcome).toBe(OUTCOME);
    expect(revision.changed_fields).toEqual(['signal_parameters.long_window']);

    expect(record.decision_review.saved_comparisons).toHaveLength(1);
    expect(record.decision_review.saved_comparisons[0].purpose).toBe(PURPOSE);
    expect(record.decision_review.saved_comparisons[0].notes).toBe(NOTES);

    for (const run of record.runs) {
      expect(run).not.toHaveProperty('transactions');
    }
    expect(record.coverage.events).toBeGreaterThan(0);
  });

  test('the record survives a reload', async () => {
    await page.reload({ waitUntil: 'networkidle' });
    const panel = page.getByTestId('record-panel');
    await panel.getByTestId('record-counts').waitFor({ timeout: 30000 });
    await expect(countValue(page, 'record-count-revisions')).toHaveText('1');
    await expect(countValue(page, 'record-count-comparisons')).toHaveText('1');
    const revisionEntry = panel.getByTestId(`record-revision-${journey.revisionVersionId}`);
    await expect(revisionEntry).toContainText('未符預期');
    await expect(revisionEntry).toContainText(OUTCOME);
  });

  test('another account and anonymous callers cannot read the record', async () => {
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        email: OUTSIDER.email,
        password: OUTSIDER.password,
        display_name: 'Record Outsider',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register outsider account').toBe(201);
    const loginResponse = await page.request.post(`${runtime.backendBase}/api/auth/login`, {
      data: { email: OUTSIDER.email, password: OUTSIDER.password },
    });
    expect(loginResponse.status()).toBe(200);
    const outsiderToken = (await loginResponse.json()).access_token as string;

    const recordPath = `/api/v1/experiments/${journey.experimentId}/record`;
    const asOutsider = await page.request.get(`${runtime.backendBase}${recordPath}`, {
      headers: { Authorization: `Bearer ${outsiderToken}` },
    });
    expect(asOutsider.status(), 'outsider GET record').toBe(404);

    const exportPath = `/api/v1/experiments/${journey.experimentId}/export`;
    const exportAsOutsider = await page.request.get(`${runtime.backendBase}${exportPath}`, {
      headers: { Authorization: `Bearer ${outsiderToken}` },
    });
    expect(exportAsOutsider.status(), 'outsider GET export').toBe(404);

    const anonymous = await page.request.get(`${runtime.backendBase}${recordPath}`);
    expect([401, 403]).toContain(anonymous.status());
  });
});
