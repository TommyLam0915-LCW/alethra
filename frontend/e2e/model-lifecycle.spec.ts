import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for personal-model version lifecycle (slice 4):
 * an owner freezes two of their own strategy versions into model versions,
 * marks one as the active version, compares both without any ranking,
 * rolls back to the older version with a reason, and the choice survives a
 * reload. A different account cannot read, compare or roll back the record.
 *
 * Runs against the disposable E2E runtime (frontend :3099, backend :8099,
 * database alethra_e2e). Credentials come from the git-ignored file.
 */

interface Runtime {
  frontendBase: string;
  backendBase: string;
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
  return {
    frontendBase: pick('E2E_FRONTEND_BASE') ?? 'http://localhost:3099',
    backendBase: pick('E2E_BACKEND_BASE') ?? 'http://127.0.0.1:8099',
  };
}

const runtime = loadRuntime();
const stamp = Date.now();
const ACCOUNT = {
  email: `model.lifecycle.${stamp}@alethra.io`,
  password: `ModelLifecycle${stamp}!aA1`,
};
const OUTSIDER = {
  email: `model.outsider.${stamp}@alethra.io`,
  password: `ModelOutsider${stamp}!bB2`,
};
const EXPERIMENT_TITLE = `E2E 模型生命週期來源（自動化）${stamp}`;
const RECORD_TITLE = `E2E 個人模型生命週期（自動化）${stamp}`;
const RECORD_PURPOSE = `驗證模型版本凍結、使用中版本、比較與回滾 ${stamp}`;
const NOTE = `由策略版本凍結 ${stamp}`;
const ROLLBACK_REASON = `回滾驗證 ${stamp}：舊版較保守，先回到 v1`;

const STRATEGY_SPEC = {
  schema_version: '1.0',
  template_type: 'buy_and_hold',
  asset_ids: ['DEMO-ALPHA'],
  frequency: '1d',
  start_date: '2024-01-02',
  end_date: '2024-02-14',
  initial_cash: 10000,
  signal_parameters: {},
  commission_rate: 0.001,
  slippage_rate: 0.001,
  benchmark_id: 'DEMO-BENCHMARK',
  currency: 'USD',
};

interface ExperimentPayload {
  id: string;
  title: string;
  current_version_id: string;
}

interface VersionPayload {
  id: string;
  version_number: number;
}

interface RecordPayload {
  id: string;
  active_version_id: string | null;
}

const journey = {
  experimentId: '',
  strategyV1: '',
  strategyV2: '',
  recordId: '',
};

async function token(page: Page): Promise<string> {
  const value = await page.evaluate(() => localStorage.getItem('token'));
  expect(value, 'login must store a token').toBeTruthy();
  return value as string;
}

async function login(page: Page): Promise<void> {
  await page.goto(`${runtime.frontendBase}/login`, { waitUntil: 'networkidle' });
  const zone = page.locator('.login-form-zone');
  await zone.locator('input[type="email"]').fill(ACCOUNT.email);
  await zone.locator('input[type="password"]').fill(ACCOUNT.password);
  await page.getByRole('button', { name: '登入', exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 });
}

test.describe('personal model version lifecycle journey', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        email: ACCOUNT.email,
        password: ACCOUNT.password,
        display_name: 'Model Lifecycle User',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register disposable account').toBe(201);
    await login(page);

    const authorization = { Authorization: `Bearer ${await token(page)}` };
    const experiment = await page.request.post(`${runtime.backendBase}/api/v1/experiments/`, {
      headers: authorization,
      data: {
        title: EXPERIMENT_TITLE,
        hypothesis: '模型版本必須可追溯',
        research_type: 'quantitative',
        strategy_spec: STRATEGY_SPEC,
      },
    });
    expect(experiment.status(), 'create source experiment').toBe(201);
    const created = (await experiment.json()) as ExperimentPayload;
    journey.experimentId = created.id;
    journey.strategyV1 = created.current_version_id;

    const revision = await page.request.post(
      `${runtime.backendBase}/api/v1/experiments/${created.id}/revisions`,
      {
        headers: authorization,
        data: {
          base_version_id: created.current_version_id,
          strategy_spec: { ...STRATEGY_SPEC, commission_rate: 0.002 },
          change_reason: '提高手續費以檢驗成本假設',
          expectation: '淨報酬應低於原版本',
          request_id: crypto.randomUUID(),
        },
      },
    );
    expect(revision.status(), 'create strategy revision').toBe(201);
    const revisionBody = (await revision.json()) as { version: VersionPayload };
    expect(revisionBody.version.version_number).toBe(2);
    journey.strategyV2 = revisionBody.version.id;
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('creates a model record and freezes two strategy versions', async () => {
    await page.goto(`${runtime.frontendBase}/models`, { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '建立模型', exact: true }).click();
    await page.getByPlaceholder('例：SMA 交叉策略模型').fill(RECORD_TITLE);
    await page.getByPlaceholder('此模型的研究目的').fill(RECORD_PURPOSE);
    await page.getByRole('button', { name: '建立', exact: true }).click();
    await page.getByRole('link', { name: new RegExp(RECORD_TITLE) }).click();
    await page.waitForURL(/\/models\/[0-9a-f-]{36}$/, { timeout: 30000 });
    journey.recordId = new URL(page.url()).pathname.split('/').pop() as string;

    await expect(page.getByTestId('active-version-label')).toHaveText('尚未指定目前使用版本');

    await page.getByTestId('open-from-strategy').click();
    await page.getByTestId('strategy-experiment-select').selectOption({ label: EXPERIMENT_TITLE });
    await page.getByTestId('strategy-version-select').selectOption(journey.strategyV1);
    await page.getByTestId('strategy-note-input').fill(NOTE);
    await page.getByTestId('strategy-version-submit').click();

    const first = page.getByTestId('model-version-1');
    await expect(first).toContainText('來自策略版本 v1');
    await expect(first).toContainText(NOTE);

    await page.getByTestId('open-from-strategy').click();
    await page.getByTestId('strategy-experiment-select').selectOption({ label: EXPERIMENT_TITLE });
    await page.getByTestId('strategy-version-select').selectOption(journey.strategyV2);
    await page.getByTestId('strategy-version-submit').click();

    const second = page.getByTestId('model-version-2');
    await expect(second).toContainText('來自策略版本 v2');

    const versions = (await (
      await page.request.get(
        `${runtime.backendBase}/api/v1/model-research/${journey.recordId}/versions`,
        { headers: { Authorization: `Bearer ${await token(page)}` } },
      )
    ).json()) as VersionPayload[];
    expect(versions).toHaveLength(2);
  });

  test('the owner marks a version as active', async () => {
    await page.getByTestId('set-active-2').click();
    await expect(page.getByTestId('active-version-label')).toHaveText('目前使用版本：v2');
    await expect(page.getByTestId('active-badge-2')).toHaveText('目前使用');
    await expect(page.getByTestId('set-active-2')).toHaveCount(0);

    const record = (await (
      await page.request.get(`${runtime.backendBase}/api/v1/model-research/${journey.recordId}`, {
        headers: { Authorization: `Bearer ${await token(page)}` },
      })
    ).json()) as RecordPayload;
    expect(record.active_version_id).not.toBeNull();
  });

  test('comparison lists the spec difference without declaring a winner', async () => {
    await page.getByRole('button', { name: '版本比較', exact: true }).click();
    await expect(page.getByTestId('compare-select-a')).toBeVisible();
    await page.getByTestId('compare-select-a').selectOption({ label: 'v1' });
    await page.getByTestId('compare-select-b').selectOption({ label: 'v2' });
    await page.getByTestId('compare-run').click();

    const result = page.getByTestId('compare-result');
    await expect(result).toBeVisible({ timeout: 30000 });
    await expect(result).toContainText('版本 A：v1');
    await expect(result).toContainText('版本 B：v2');

    const commission = result
      .locator('[data-testid^="compare-change-"]')
      .filter({ hasText: 'strategy_spec.commission_rate' });
    await expect(commission).toHaveCount(1);
    await expect(commission).toContainText('0.001');
    await expect(commission).toContainText('0.002');

    await expect(page.getByTestId('compare-note')).toContainText('不會自動判定哪個版本更好');
    await expect(result).not.toContainText('建議採用');
  });

  test('rollback needs a reason and moves the active pointer to the older version', async () => {
    await page.getByRole('button', { name: /版本 \(2\)/ }).click();
    await page.getByTestId('rollback-1').click();
    await expect(page.getByTestId('rollback-confirm-1')).toBeDisabled();
    await page.getByTestId('rollback-reason-1').fill(ROLLBACK_REASON);
    await page.getByTestId('rollback-confirm-1').click();

    await expect(page.getByTestId('active-version-label')).toHaveText('目前使用版本：v1');
    await expect(page.getByTestId('active-badge-1')).toHaveText('目前使用');
    await expect(page.getByTestId('active-badge-2')).toHaveCount(0);

    await page.reload({ waitUntil: 'networkidle' });
    await expect(page.getByTestId('active-version-banner')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('active-version-label')).toHaveText('目前使用版本：v1');

    const record = (await (
      await page.request.get(`${runtime.backendBase}/api/v1/model-research/${journey.recordId}`, {
        headers: { Authorization: `Bearer ${await token(page)}` },
      })
    ).json()) as RecordPayload;
    expect(record.active_version_id).not.toBeNull();
  });

  test('another account cannot compare or roll back the record', async () => {
    const registration = await page.request.post(`${runtime.backendBase}/api/auth/register`, {
      data: {
        email: OUTSIDER.email,
        password: OUTSIDER.password,
        display_name: 'Model Outsider',
        user_type: 'individual',
      },
    });
    expect(registration.status(), 'register outsider account').toBe(201);
    const loginResponse = await page.request.post(`${runtime.backendBase}/api/auth/login`, {
      data: { email: OUTSIDER.email, password: OUTSIDER.password },
    });
    expect(loginResponse.status()).toBe(200);
    const outsiderToken = (await loginResponse.json()).access_token as string;

    const versions = (await (
      await page.request.get(
        `${runtime.backendBase}/api/v1/model-research/${journey.recordId}/versions`,
        { headers: { Authorization: `Bearer ${await token(page)}` } },
      )
    ).json()) as VersionPayload[];

    const comparePath =
      `${runtime.backendBase}/api/v1/model-research/${journey.recordId}/versions/compare` +
      `?version_a_id=${versions[0].id}&version_b_id=${versions[1].id}`;
    const asOutsider = await page.request.get(comparePath, {
      headers: { Authorization: `Bearer ${outsiderToken}` },
    });
    expect([403, 404]).toContain(asOutsider.status());

    const rollback = await page.request.post(
      `${runtime.backendBase}/api/v1/model-research/${journey.recordId}/rollback`,
      {
        headers: { Authorization: `Bearer ${outsiderToken}` },
        data: { to_version_id: versions[0].id, reason: 'outsider attempt' },
      },
    );
    expect([403, 404]).toContain(rollback.status());

    const anonymous = await page.request.get(comparePath);
    expect([401, 403]).toContain(anonymous.status());
  });
});
