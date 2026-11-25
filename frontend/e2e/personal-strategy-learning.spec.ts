import { spawn, spawnSync, type ChildProcess } from 'child_process';
import { existsSync, mkdirSync, openSync, readFileSync } from 'fs';
import path from 'path';
import { test, expect, type Locator, type Page } from '@playwright/test';

/**
 * Tracked E2E for the personal strategy learning product loop:
 * consent -> review -> frozen snapshot -> training job -> artifact/model version
 * -> one skill account -> activate for that account -> prediction resolved from
 * that account's own selection
 * -> second version -> comparability -> comparison -> select -> rollback.
 *
 * Every activation, selection, rollback and prediction below names one skill
 * account (a paper portfolio), because that account's own selection is what
 * authorizes its inference. The account is opened through the product's own
 * create page, not by a fixture.
 *
 * DATA SETUP vs UI UNDER TEST (disclosed item-by-item):
 *   - beforeAll runs the tracked seed backend/scripts/seed_e2e_personal_learning.py
 *     against the DISPOSABLE personal-learning database only. It creates a fresh
 *     stamped account, one locked strategy version, 140 synthetic daily bars and
 *     205 already-reviewed decisions. The seed refuses any DATABASE_URL that is not
 *     a postgresql URL containing "alethra_personal_e2e".
 *   - beforeAll also starts the real training worker (`python -m app.workers.training_worker`)
 *     against that same disposable database, because the normal operator path is
 *     "user submits in the UI, a separate worker processes the job".
 *   - Every product mutation below is a real UI gesture (login, consent, label,
 *     snapshot, train, activate, predict, compare, select, rollback). No API call
 *     stands in for a user step and no API call writes product state; API reads are
 *     used only to obtain server-assigned identities and numbers to assert against
 *     the rendered UI. The second model version is produced by a second real
 *     training job on the same frozen evidence, which is what makes the versions
 *     comparable and share one evaluation set.
 *   - Waits are waitForResponse / expect / server-state predicates; there is no fixed
 *     sleep on product state and no assertion that depends on card order.
 *
 * Required environment (never the shared runtime):
 *   E2E_PERSONAL_DB_URL   e.g. postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/alethra_personal_e2e_YYYYMMDD
 *   E2E_PERSONAL_BACKEND  default http://127.0.0.1:8098
 *   E2E_FRONTEND_BASE     default http://127.0.0.1:3002 (a frontend started with
 *                         NEXT_PUBLIC_API_URL pointing at E2E_PERSONAL_BACKEND)
 *   E2E_PERSONAL_PYTHON   python interpreter that can import app.* (backend venv)
 *
 * The file skips itself when E2E_PERSONAL_DB_URL is absent or not a disposable
 * personal-learning database, so it can never touch the shared acceptance runtime.
 */

const DB_URL = process.env.E2E_PERSONAL_DB_URL ?? '';
const REQUIRES_DB =
  'requires E2E_PERSONAL_DB_URL pointing at a disposable alethra_personal_e2e* database';

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const BACKEND_DIR = path.join(REPO_ROOT, 'backend');
const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://127.0.0.1:3002';
const BACKEND_BASE = (process.env.E2E_PERSONAL_BACKEND ?? 'http://127.0.0.1:8098').replace(/\/$/, '');
const PYTHON = process.env.E2E_PERSONAL_PYTHON ?? 'python3';
const WORKER_LOG =
  process.env.E2E_PERSONAL_WORKER_LOG ??
  path.join(REPO_ROOT, 'frontend', 'test-results', 'personal-learning-worker.log');
const SHOT_DIR = process.env.E2E_PERSONAL_SHOT_DIR ?? '';

const stamp = Date.now();
const ACCOUNT = {
  email: `personal.learning.e2e.${stamp}@alethra.io`,
  password: `PersonalLearning${stamp}!aA1`,
};

let worker: ChildProcess | null = null;
let strategyVersionId = '';
let skillUnitId = '';
let expectedConfirms = 0;

function seedEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    DATABASE_URL: DB_URL,
    E2E_EMAIL: ACCOUNT.email,
    E2E_PASSWORD: ACCOUNT.password,
    PYTHONPATH: BACKEND_DIR,
    JWT_SECRET_KEY: 'e2e-personal-learning-local-only',
  };
}

async function waitForFileText(file: string, needle: string, timeoutMs: number): Promise<void> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (existsSync(file) && readFileSync(file, 'utf-8').includes(needle)) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`worker did not report "${needle}" within ${timeoutMs}ms (log: ${file})`);
}

async function shot(page: Page, name: string): Promise<void> {
  if (!SHOT_DIR) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SHOT_DIR, `${name}.png`) });
}

async function shotElement(locator: Locator, name: string): Promise<void> {
  if (!SHOT_DIR) return;
  mkdirSync(SHOT_DIR, { recursive: true });
  await locator.screenshot({ path: path.join(SHOT_DIR, `${name}.png`) });
}

async function login(page: Page): Promise<void> {
  await page.goto(`${FRONTEND_BASE}/login`, { waitUntil: 'networkidle' });
  const zone = page.locator('.login-form-zone');
  await expect(zone).toBeVisible();
  await zone.locator('input[type="email"]').fill(ACCOUNT.email);
  await zone.locator('input[type="password"]').fill(ACCOUNT.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 });
}

async function token(page: Page): Promise<string> {
  const value = await page.evaluate(() => localStorage.getItem('token'));
  expect(value, 'login must store a token').toBeTruthy();
  return value as string;
}

test.beforeAll(async () => {
  if (!DB_URL.includes('alethra_personal_e2e')) return;
  const seed = spawnSync(
    PYTHON,
    [path.join(BACKEND_DIR, 'scripts', 'seed_e2e_personal_learning.py')],
    { cwd: REPO_ROOT, env: seedEnv(), encoding: 'utf-8' },
  );
  expect(seed.status, `seed failed:\n${seed.stdout ?? ''}\n${seed.stderr ?? ''}`).toBe(0);
  const versionMatch = /version\s*:\s*([0-9a-f-]{36})/.exec(seed.stdout ?? '');
  expect(versionMatch, 'seed must print the strategy version id').toBeTruthy();
  strategyVersionId = versionMatch![1];
  const labelsMatch = /labels\s*:\s*(\d+)\s+confirmed/.exec(seed.stdout ?? '');
  expect(labelsMatch, 'seed must print the confirmed label count').toBeTruthy();
  expectedConfirms = Number(labelsMatch![1]);
  expect(expectedConfirms).toBeGreaterThan(0);

  mkdirSync(path.dirname(WORKER_LOG), { recursive: true });
  const logFd = openSync(WORKER_LOG, 'w');
  worker = spawn(PYTHON, ['-m', 'app.workers.training_worker', '1'], {
    cwd: BACKEND_DIR,
    env: seedEnv(),
    stdio: ['ignore', logFd, logFd],
  });
  await waitForFileText(WORKER_LOG, 'Training worker started', 15000);
});

test.afterAll(async () => {
  if (!worker || worker.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => worker!.once('exit', () => resolve()));
  worker.kill('SIGTERM');
  await Promise.race([
    exited,
    new Promise<void>((resolve) => setTimeout(resolve, 15000).unref()),
  ]);
  if (worker.exitCode === null) worker.kill('SIGKILL');
});

test('desktop journey: consent -> review -> snapshot -> job -> version -> activate -> predict -> compare -> select -> rollback', async ({
  browser,
}) => {
  test.skip(!DB_URL.includes('alethra_personal_e2e'), REQUIRES_DB);
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await login(page);
    const authorization = { Authorization: `Bearer ${await token(page)}` };

    // 0b. One skill account. A skill account *is* a paper portfolio, and it is
    //     the scope of every activation, selection, rollback and prediction
    //     below — the account's own selection is what authorizes its inference.
    //     Opened the way a customer opens one: the product's create form.
    await page.goto(`${FRONTEND_BASE}/paper-portfolio`, { waitUntil: 'networkidle' });
    const createResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/paper-portfolio') && res.request().method() === 'POST',
    );
    await page.getByTestId('pp-new-cash').fill('100000');
    await page.getByTestId('pp-create-portfolio').click();
    const createdRes = await createResponse;
    expect(createdRes.status(), 'skill account create').toBe(201);
    skillUnitId = ((await createdRes.json()) as { id: string }).id;
    await page.waitForURL((url) => url.pathname === `/paper-portfolio/${skillUnitId}`, {
      timeout: 30000,
    });
    await shot(page, '00-skill-account');

    await page.goto(`${FRONTEND_BASE}/training`, { waitUntil: 'networkidle' });

    // 0. Consent — granted through the product UI, not by a DB fixture.
    const scope = page.getByTestId(`personalization-scope-${strategyVersionId}`);
    await scope.scrollIntoViewIfNeeded();
    await scope.check();
    await page.getByTestId('personalization-acknowledge').check();
    const consentResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/ml/personalization/consent') &&
        res.request().method() === 'PUT',
    );
    await page.getByTestId('personalization-grant').click();
    expect((await consentResponse).status(), 'consent grant').toBe(200);
    await expect(page.getByTestId('personalization-consent-state')).toBeVisible();
    await shot(page, '01-consent-granted');

    // 1. Review — the panel selects the seeded strategy version by server id.
    const panel = page.getByTestId('personal-learning-panel');
    const strategySelect = panel.getByTestId('pl-strategy-select');
    await expect(strategySelect).toHaveValue(strategyVersionId);
    const summary = panel.getByTestId('pl-review-summary');
    await expect(summary).toContainText(String(expectedConfirms));

    const reviewResponse = await page.request.get(
      `${BACKEND_BASE}/api/v1/decision-review?strategy_version_id=${strategyVersionId}`,
      { headers: authorization },
    );
    expect(reviewResponse.status()).toBe(200);
    const review = (await reviewResponse.json()) as {
      summary: { confirmed: number };
      decisions: Array<{ decision_event_id: string }>;
    };
    expect(review.summary.confirmed).toBe(expectedConfirms);
    const decisionEventId = review.decisions[0].decision_event_id;

    const labelResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith(`/api/v1/decision-review/${decisionEventId}/labels`) &&
        res.request().method() === 'POST',
    );
    await panel.getByTestId(`pl-confirm-${decisionEventId}`).click();
    expect([200, 201]).toContain((await labelResponse).status());
    await expect(panel.getByTestId('pl-notice')).toContainText('已確認決策');
    await expect(panel.getByTestId(`pl-label-state-${decisionEventId}`)).toContainText('已確認');

    // 1b. Label revisions are append-only; the row must expose the history, not
    // only the newest label. The toggle is the product way to reach it.
    const historyResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith(`/api/v1/decision-review/${decisionEventId}/labels`) &&
        res.request().method() === 'GET',
    );
    await panel.getByTestId(`pl-history-${decisionEventId}`).click();
    expect((await historyResponse).status(), 'label revision history').toBe(200);
    const historyPanel = panel.getByTestId(`pl-history-panel-${decisionEventId}`);
    await expect(historyPanel).toContainText('修訂 1');

    // 2. Frozen snapshot — assert the POST response id, not card position.
    const snapshotResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/ml/personal/snapshots') &&
        res.request().method() === 'POST',
    );
    await panel.getByTestId('pl-snapshot-create').click();
    const snapshotRes = await snapshotResponse;
    expect(snapshotRes.status(), 'snapshot create').toBe(201);
    const snapshot = (await snapshotRes.json()) as {
      snapshot_id: string;
      included_count: number;
      checksum: string;
    };
    await expect(panel.getByTestId('pl-notice')).toContainText(`納入 ${snapshot.included_count} 筆`);
    await expect(panel.getByTestId('pl-snapshot-select')).toHaveValue(snapshot.snapshot_id);
    await expect(panel.getByTestId('pl-snapshot-list')).toContainText(
      snapshot.snapshot_id.slice(0, 8),
    );
    await shot(page, '02-snapshot-created');

    // 3. Training job — the separate worker must complete it; UI polls server state.
    let jobId = '';
    const modelsPromise = page.waitForResponse(
      async (res) => {
        if (!res.url().includes('/api/v1/ml/personal/models')) return false;
        if (res.request().method() !== 'GET') return false;
        if (!jobId) return false;
        try {
          const body = (await res.json()) as {
            models: Array<{ training_job_id: string | null }>;
          };
          return body.models.some((m) => m.training_job_id === jobId);
        } catch {
          return false;
        }
      },
      { timeout: 180000 },
    );
    const jobResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/ml/personal/training-jobs') &&
        res.request().method() === 'POST',
    );
    await panel.getByTestId('pl-train-start').click();
    const jobRes = await jobResponse;
    expect(jobRes.status(), 'training job accepted').toBe(202);
    jobId = ((await jobRes.json()) as { job_id: string }).job_id;
    await expect(panel.getByTestId('pl-train-status')).toContainText(jobId.slice(0, 8));
    await expect(panel.getByTestId('pl-train-status')).toContainText('已完成', {
      timeout: 180000,
    });
    await shot(page, '03-training-completed');

    const modelsRes = await modelsPromise;
    expect(modelsRes.status()).toBe(200);
    const modelsBody = (await modelsRes.json()) as {
      models: Array<{
        model_version_id: string;
        record_id: string;
        version_number: number;
        training_job_id: string | null;
        artifact_sha256: string | null;
      }>;
    };
    const model = modelsBody.models.find((m) => m.training_job_id === jobId);
    expect(model, 'the completed job must produce a model version').toBeTruthy();
    const modelId = model!.model_version_id;
    const card = panel.getByTestId(`pl-model-${modelId}`);
    await expect(card).toContainText(`版本 ${model!.version_number}`);
    await expect(card).toContainText(`artifact ${model!.artifact_sha256!.slice(0, 8)}`);

    // 4. Activate — for this one skill account. The account's own selection is
    //    what authorizes its inference; the record-wide alias authorizes nobody.
    const activateResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith(`/api/v1/ml/personal/models/${modelId}/activate`) &&
        res.request().method() === 'POST',
    );
    await panel.getByTestId(`pl-activate-${modelId}`).click();
    const activateRes = await activateResponse;
    expect(activateRes.status(), 'activate').toBe(200);
    expect(
      (activateRes.request().postDataJSON() as { skill_unit_id?: string }).skill_unit_id,
      'activation must name the skill account it activates for',
    ).toBe(skillUnitId);
    await expect(card.getByTestId('pl-model-active')).toHaveText('此帳號使用中');
    await expect(panel.getByTestId('pl-skill-unit-version')).toContainText(
      `此帳號目前使用：版本 ${model!.version_number}`,
    );
    await shot(page, '04-model-active');

    // 5. Predict — resolved from this account's selection, not from a global flag.
    const predictResponse = page.waitForResponse(
      async (res) => {
        if (!res.url().endsWith('/predict')) return false;
        if (!res.url().includes('/api/v1/ml/personal/records/')) return false;
        try {
          const body = (await res.json()) as {
            resolved_version?: { model_version_id?: string };
          };
          return body.resolved_version?.model_version_id === modelId;
        } catch {
          return false;
        }
      },
    );
    const asOf = '2024-06-17';
    await panel.getByTestId('pl-predict-date').fill(asOf);
    await panel.getByTestId('pl-predict-run').click();
    const predictRes = await predictResponse;
    expect(predictRes.status(), 'prediction').toBe(200);
    expect(
      (predictRes.request().postDataJSON() as { skill_unit_id?: string }).skill_unit_id,
      'a prediction must be asked for one skill account',
    ).toBe(skillUnitId);
    const prediction = (await predictRes.json()) as {
      resolved_version: { version_number: number; resolved_from: string };
      skill_unit_id: string;
      as_of: { as_of_date: string; feature_bar_date: string };
    };
    expect(prediction.skill_unit_id, 'the answer says which account it belongs to').toBe(
      skillUnitId,
    );
    expect(prediction.resolved_version.resolved_from).toBe('skill_unit_selection');
    expect(prediction.as_of.feature_bar_date < prediction.as_of.as_of_date).toBe(true);
    const result = panel.getByTestId('pl-predict-result');
    await expect(result).toContainText(`實際解析版本 ${model!.version_number}`);
    await expect(result).toContainText('自己的選用解析');
    await expect(panel.getByTestId('pl-predict-scope')).toContainText(
      `此估計只屬於技能帳號 ${skillUnitId.slice(0, 8)}`,
    );
    await expect(result).toContainText(`特徵 K 線 ${prediction.as_of.feature_bar_date}`);
    await expect(result).toContainText('同意目前有效：是');
    await shot(page, '05-prediction');

    // 6. Second version — a different model type trained on the same reviewed
    //    evidence. The panel must now offer comparison instead of the
    //    "need two versions" notice.
    const secondSnapshotResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/ml/personal/snapshots') &&
        res.request().method() === 'POST',
    );
    await panel.getByTestId('pl-snapshot-create').click();
    const secondSnapshotRes = await secondSnapshotResponse;
    expect(secondSnapshotRes.status(), 'second snapshot create').toBe(201);
    const secondSnapshot = (await secondSnapshotRes.json()) as {
      snapshot_id: string;
      included_count: number;
    };
    await expect(panel.getByTestId('pl-snapshot-select')).toHaveValue(
      secondSnapshot.snapshot_id,
    );
    await panel.getByTestId('pl-model-type').selectOption('baseline');

    let secondJobId = '';
    const secondModelsPromise = page.waitForResponse(
      async (res) => {
        if (!res.url().includes('/api/v1/ml/personal/models')) return false;
        if (res.request().method() !== 'GET') return false;
        if (!secondJobId) return false;
        try {
          const body = (await res.json()) as {
            models: Array<{ training_job_id: string | null }>;
          };
          return body.models.some((m) => m.training_job_id === secondJobId);
        } catch {
          return false;
        }
      },
      { timeout: 180000 },
    );
    const secondJobResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/ml/personal/training-jobs') &&
        res.request().method() === 'POST',
    );
    await panel.getByTestId('pl-train-start').click();
    const secondJobRes = await secondJobResponse;
    expect(secondJobRes.status(), 'second training job accepted').toBe(202);
    secondJobId = ((await secondJobRes.json()) as { job_id: string }).job_id;
    await expect(panel.getByTestId('pl-train-status')).toContainText(
      secondJobId.slice(0, 8),
    );
    await expect(panel.getByTestId('pl-train-status')).toContainText('已完成', {
      timeout: 180000,
    });
    const secondModelsRes = await secondModelsPromise;
    expect(secondModelsRes.status()).toBe(200);
    const secondModelsBody = (await secondModelsRes.json()) as {
      models: Array<{
        model_version_id: string;
        version_number: number;
        training_job_id: string | null;
      }>;
    };
    const secondModel = secondModelsBody.models.find(
      (m) => m.training_job_id === secondJobId,
    );
    expect(secondModel, 'the second job must produce a model version').toBeTruthy();
    const secondModelId = secondModel!.model_version_id;
    await expect(panel.getByTestId(`pl-model-${secondModelId}`)).toContainText(
      `版本 ${secondModel!.version_number}`,
    );
    await expect(panel.getByTestId('pl-compare')).toContainText('版本比較與選用');
    await shot(page, '06-second-version');

    // 7. Comparability is free; the comparison itself consumes one AI unit.
    await panel.getByTestId('pl-compare-a').selectOption(modelId);
    await panel.getByTestId('pl-compare-b').selectOption(secondModelId);
    const comparabilityResponse = page.waitForResponse(
      (res) =>
        res.url().includes('/api/v1/ml/personal/models/comparability?') &&
        res.request().method() === 'GET',
    );
    await panel.getByTestId('pl-compare-check').click();
    const comparabilityRes = await comparabilityResponse;
    expect(comparabilityRes.status(), 'comparability report').toBe(200);
    const comparability = (await comparabilityRes.json()) as {
      comparable: boolean;
      same_evaluation_set: boolean;
      checks: Array<{ code: string; ok: boolean; required: boolean }>;
    };
    expect(comparability.comparable, 'same task/features/labels').toBe(true);
    expect(comparability.same_evaluation_set, 'one frozen evaluation set').toBe(true);
    await expect(panel.getByTestId('pl-compare-checks')).toContainText('可比');
    await expect(panel.getByTestId('pl-compare-checks')).toContainText('同一評估集');
    // The purpose label comes from the server's row-identity relationship, not
    // from the UI guessing that two test windows mean one evaluation set.
    await expect(panel.getByTestId('pl-comparability-purpose')).toContainText(
      '共同未見評估',
    );

    const comparisonResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/ml/personal/models/comparisons') &&
        res.request().method() === 'POST',
      { timeout: 120000 },
    );
    await panel.getByTestId('pl-compare-run').click();
    const comparisonRes = await comparisonResponse;
    expect(comparisonRes.status(), 'comparison run').toBe(200);
    const comparison = (await comparisonRes.json()) as {
      comparison_run_id: string;
      mode: string;
      rankable: boolean;
      evaluation_purpose: string;
      independent_out_of_sample: boolean;
      common_evaluation: { n_rows: number; n_groups: number } | null;
      versions: {
        a: { model_version_id: string; artifact_sha256: string };
        b: { model_version_id: string; artifact_sha256: string };
      };
      delta: { accuracy: number; macro_f1: number } | null;
    };
    expect(comparison.versions.a.model_version_id).toBe(modelId);
    expect(comparison.versions.b.model_version_id).toBe(secondModelId);
    expect(comparison.rankable, 'same evaluation set is rankable').toBe(true);
    expect(comparison.evaluation_purpose).toBe('common_unseen');
    expect(comparison.independent_out_of_sample).toBe(true);
    expect(comparison.common_evaluation, 'a common batch is reported').not.toBeNull();
    expect(comparison.delta, 'same-set comparison reports a delta').not.toBeNull();
    const resultCard = panel.getByTestId('pl-comparison-result');
    await expect(resultCard).toContainText('同一評估集');
    await expect(panel.getByTestId('pl-comparison-purpose')).toContainText(
      '共同未見評估',
    );
    await expect(panel.getByTestId('pl-comparison-common')).toContainText(
      `共同列 ${comparison.common_evaluation!.n_rows}`,
    );
    await expect(panel.getByTestId('pl-comparison-common-A')).toContainText('共同批次');
    await expect(panel.getByTestId('pl-comparison-common-B')).toContainText('共同批次');
    await expect(panel.getByTestId('pl-comparison-version-A')).toContainText(
      `artifact ${comparison.versions.a.artifact_sha256.slice(0, 8)}`,
    );
    await expect(panel.getByTestId('pl-comparison-version-B')).toContainText(
      `artifact ${comparison.versions.b.artifact_sha256.slice(0, 8)}`,
    );
    await expect(panel.getByTestId('pl-comparison-delta')).toBeVisible();
    await expect(
      panel.getByTestId(`pl-comparison-history-${comparison.comparison_run_id}`),
    ).toContainText(`版本 ${model!.version_number} vs ${secondModel!.version_number}`);
    await panel.getByTestId('pl-comparison-purpose').scrollIntoViewIfNeeded();
    await shot(page, '07-comparison');
    await shotElement(panel.getByTestId('pl-comparison-result'), '07-comparison-result');

    // 8. Explicit select for this skill account, with a saved rationale, then
    //    rollback to the version this account used before the switch. History
    //    must show both actions and label the scope of each.
    const selectRationale = 'E2E：比較後選用版本 B';
    await panel.getByTestId('pl-selection-rationale').fill(selectRationale);
    const selectResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith(`/api/v1/ml/personal/models/${secondModelId}/select`) &&
        res.request().method() === 'POST',
      { timeout: 60000 },
    );
    await panel.getByTestId('pl-select-b').click();
    const selectRes = await selectResponse;
    expect(selectRes.status(), 'select version').toBe(200);
    const selectBody = selectRes.request().postDataJSON() as {
      expected_active_version_id?: string | null;
      skill_unit_id?: string;
    };
    expect(
      Object.prototype.hasOwnProperty.call(selectBody, 'expected_active_version_id'),
      'the UI must send the active version it last saw',
    ).toBe(true);
    expect(
      selectBody.expected_active_version_id,
      'the guard compares against this account, not the record alias',
    ).toBe(modelId);
    expect(selectBody.skill_unit_id, 'a switch is made for one skill account').toBe(
      skillUnitId,
    );
    const selection = (await selectRes.json()) as {
      already_active: boolean;
      skill_unit_id: string | null;
      selection: {
        selection_id: string;
        action: string;
        version_number: number | null;
        previous_version_number: number | null;
        rationale: string | null;
        comparison_run_id: string | null;
      } | null;
    };
    expect(selection.already_active).toBe(false);
    expect(selection.skill_unit_id, 'the server binds the row to this account').toBe(
      skillUnitId,
    );
    expect(selection.selection, 'a new selection row').not.toBeNull();
    expect(selection.selection!.action).toBe('select');
    expect(selection.selection!.comparison_run_id).toBe(comparison.comparison_run_id);
    expect(selection.selection!.rationale).toBe(selectRationale);
    expect(selection.selection!.previous_version_number).toBe(model!.version_number);
    await expect(
      panel.getByTestId(`pl-selection-${selection.selection!.selection_id}`),
    ).toContainText(`選用 版本 ${secondModel!.version_number}`);
    await expect(
      panel.getByTestId(`pl-selection-${selection.selection!.selection_id}`),
    ).toContainText(`範圍：帳號 ${skillUnitId.slice(0, 8)}`);
    await expect(panel.getByTestId('pl-scope-active')).toContainText(
      `目前使用：版本 ${secondModel!.version_number}`,
    );

    const rollbackResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith(`/api/v1/ml/personal/records/${model!.record_id}/rollback`) &&
        res.request().method() === 'POST',
      { timeout: 60000 },
    );
    await panel.getByTestId('pl-rollback').click({ timeout: 30000 });
    const rollbackRes = await rollbackResponse;
    expect(rollbackRes.status(), 'rollback').toBe(200);
    const rollbackBody = rollbackRes.request().postDataJSON() as {
      expected_active_version_id?: string | null;
      skill_unit_id?: string;
    };
    expect(
      rollbackBody.expected_active_version_id,
      'rollback carries the version the UI shows as active',
    ).toBe(secondModelId);
    expect(rollbackBody.skill_unit_id, 'rollback undoes this account, not the alias').toBe(
      skillUnitId,
    );
    const rollback = (await rollbackRes.json()) as {
      selection: {
        selection_id: string;
        action: string;
        version_number: number | null;
      };
    };
    expect(rollback.selection.action).toBe('rollback');
    expect(rollback.selection.version_number).toBe(model!.version_number);
    await expect(
      panel.getByTestId(`pl-selection-${rollback.selection.selection_id}`),
    ).toContainText(`回退 版本 ${model!.version_number}`);
    await expect(panel.getByTestId('pl-scope-active')).toContainText(
      `目前使用：版本 ${model!.version_number}`,
    );
    await expect(card.getByTestId('pl-model-active')).toHaveText('此帳號使用中');
    await shot(page, '08-selection-rollback');

    // 9. Isolation, through the same panel: a second skill account opened the
    //    same way has never been given a selection, so the panel says so rather
    //    than inheriting whatever the first account happens to use. Switching
    //    back must show the first account's own state again.
    await page.goto(`${FRONTEND_BASE}/paper-portfolio`, { waitUntil: 'networkidle' });
    const secondCreateResponse = page.waitForResponse(
      (res) =>
        res.url().endsWith('/api/v1/paper-portfolio') && res.request().method() === 'POST',
    );
    await page.getByTestId('pp-new-cash').fill('50000');
    await page.getByTestId('pp-create-portfolio').click();
    const secondCreated = await secondCreateResponse;
    expect(secondCreated.status(), 'second skill account create').toBe(201);
    const secondSkillUnitId = ((await secondCreated.json()) as { id: string }).id;

    await page.goto(`${FRONTEND_BASE}/training`, { waitUntil: 'networkidle' });
    const skillSelect = page.getByTestId('pl-skill-unit-select');
    await expect(skillSelect).not.toHaveValue('');
    await skillSelect.selectOption(secondSkillUnitId);
    await expect(page.getByTestId('pl-skill-unit-version')).toContainText(
      '尚未為此帳號選用任何版本',
    );
    await expect(page.getByTestId('pl-scope-active')).toContainText(
      '此範圍還沒有任何使用中版本',
    );
    await expect(page.getByTestId('pl-predict-run')).toBeDisabled();
    await expect(page.getByTestId('pl-compare')).toBeVisible();
    await shot(page, '09-second-account-isolated');

    await skillSelect.selectOption(skillUnitId);
    await expect(page.getByTestId('pl-skill-unit-version')).toContainText(
      `此帳號目前使用：版本 ${model!.version_number}`,
    );
    await expect(page.getByTestId('pl-predict-run')).toBeEnabled();
  } finally {
    await page.close();
  }
});

test('mobile smoke: the personal learning panel renders with its controls', async ({
  browser,
}) => {
  test.skip(!DB_URL.includes('alethra_personal_e2e'), REQUIRES_DB);
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  try {
    await login(page);
    await page.goto(`${FRONTEND_BASE}/training`, { waitUntil: 'networkidle' });
    const panel = page.getByTestId('personal-learning-panel');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('個人策略學習');
    // The scope control is rendered, and it is on one of this account's skill
    // units — the one the desktop journey left a selection on.
    const skillCard = panel.getByTestId('pl-skill-unit');
    await skillCard.scrollIntoViewIfNeeded();
    await expect(skillCard.getByTestId('pl-skill-unit-select')).not.toHaveValue('');
    const summary = panel.getByTestId('pl-review-summary');
    await summary.scrollIntoViewIfNeeded();
    await expect(summary).toContainText(String(expectedConfirms));
    const createButton = panel.getByTestId('pl-snapshot-create');
    await createButton.scrollIntoViewIfNeeded();
    await expect(createButton).toBeVisible();
    const predictDate = panel.getByTestId('pl-predict-date');
    await predictDate.scrollIntoViewIfNeeded();
    await expect(predictDate).toBeVisible();
    const compare = panel.getByTestId('pl-compare');
    await compare.scrollIntoViewIfNeeded();
    await expect(compare).toBeVisible();
    await expect(compare).toContainText('版本比較與選用');
    await expect(compare).toContainText('不是投資回報');
    await shot(page, '06-mobile-panel');
  } finally {
    await page.close();
  }
});
