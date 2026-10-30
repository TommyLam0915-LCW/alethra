import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * /inference and /admin entry convergence (tracked regression).
 *
 * /inference is still a supported surface (it posts to /api/v1/inference/runs,
 * which executes an owned model version), but it used to require pasting a raw
 * model-version UUID and was unreachable from the app. This spec pins the two
 * product requirements:
 *
 *   1. The tools menu exposes 推論紀錄, and the page offers a selector built
 *      from the account's own model records/versions plus plain symbol/date
 *      inputs instead of a UUID field and a JSON textarea.
 *   2. /admin keeps its server-side role restriction and gains no navigation
 *      entry for ordinary users; a 403 renders an explicit permission message.
 *
 * Nothing leaves the browser: every API response is intercepted here, and the
 * only auth step is a token in localStorage. No database, backend or writes to
 * a real service are involved.
 *
 * Required environment (never the shared runtime):
 *   E2E_FRONTEND_BASE  the frontend under test, e.g. http://127.0.0.1:3100
 */

const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? '';
const READY = FRONTEND_BASE.length > 0;
const SKIP_REASON = 'requires E2E_FRONTEND_BASE pointing at the frontend under test';

const TOKEN = 'e2e-inference-entry-local-only';
const V1 = '11111111-1111-4111-8111-111111111111';
const V2 = '22222222-2222-4222-8222-222222222222';

interface RecordRow {
  id: string;
  title: string;
  status: string;
  active_version_id: string | null;
}

interface VersionRow {
  id: string;
  version_number: number;
  model_type: string;
  created_at: string;
  locked_at: string | null;
  revoked_at: string | null;
}

interface RunRow {
  id: string;
  model_version_id: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  input_spec: Record<string, unknown>;
  output_signals: unknown;
}

interface State {
  records: RecordRow[];
  versionsByRecord: Record<string, VersionRow[]>;
  versionListFails: boolean;
  runs: RunRow[];
  runListFails: boolean;
  posts: Array<Record<string, unknown>>;
  unexpected: string[];
}

const ROUTE = {
  modelRecords: /\/api\/v1\/model-research\/?(\?.*)?$/,
  modelVersions: /\/api\/v1\/model-research\/([^/]+)\/versions\/?$/,
  inferenceRuns: /\/api\/v1\/inference\/runs\/?(\?.*)?$/,
  experiments: /\/api\/v1\/experiments\/?(\?.*)?$/,
  adminDashboard: /\/api\/v1\/pilot\/admin\/dashboard\/?$/,
  adminHypotheses: /\/api\/v1\/pilot\/admin\/hypotheses-dashboard\/?$/,
};

function defaultState(): State {
  return {
    records: [
      { id: 'rec-moving-average', title: '均線研究', status: 'researching', active_version_id: V1 },
      { id: 'rec-momentum', title: '動量研究', status: 'draft', active_version_id: null },
    ],
    versionsByRecord: {
      'rec-moving-average': [
        {
          id: V1,
          version_number: 1,
          model_type: 'rule_based',
          created_at: '2026-10-01T02:00:00Z',
          locked_at: null,
          revoked_at: null,
        },
      ],
      'rec-momentum': [
        {
          id: V2,
          version_number: 2,
          model_type: 'prompt_based_research_assistant',
          created_at: '2026-10-02T02:00:00Z',
          locked_at: null,
          revoked_at: null,
        },
      ],
    },
    versionListFails: false,
    runs: [],
    runListFails: false,
    posts: [],
    unexpected: [],
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function install(page: Page, state: State): Promise<void> {
  // Later-registered handlers run first, so the catch-all goes in first.
  await page.route(/\/api\/v1\/.*/, (route) => {
    state.unexpected.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    return json(route, { detail: 'not mocked by the entry-convergence spec' }, 404);
  });

  await page.route(ROUTE.experiments, (route) => json(route, []));

  await page.route(ROUTE.modelRecords, (route) =>
    state.versionListFails
      ? json(route, { detail: '模擬的模型清單錯誤（回歸測試）' }, 500)
      : json(route, state.records),
  );

  await page.route(ROUTE.modelVersions, (route) => {
    const match = route.request().url().match(/\/model-research\/([^/]+)\/versions/);
    const recordId = match?.[1] ?? '';
    if (state.versionListFails) {
      return json(route, { detail: '模擬的版本清單錯誤（回歸測試）' }, 500);
    }
    return json(route, state.versionsByRecord[recordId] ?? []);
  });

  await page.route(ROUTE.inferenceRuns, (route) => {
    const request = route.request();
    if (request.method() === 'GET') {
      return state.runListFails
        ? json(route, { detail: '模擬的推論紀錄錯誤（回歸測試）' }, 500)
        : json(route, state.runs);
    }
    if (request.method() === 'POST') {
      const body = JSON.parse(request.postData() ?? '{}') as Record<string, unknown>;
      state.posts.push(body);
      const inputSpec = (body.input_spec ?? {}) as Record<string, unknown>;
      const run: RunRow = {
        id: `run-${state.runs.length + 1}`,
        model_version_id: String(body.model_version_id ?? ''),
        status: 'completed',
        created_at: '2026-10-30T03:00:00Z',
        completed_at: '2026-10-30T03:00:01Z',
        input_spec: inputSpec,
        output_signals: [{ date: inputSpec.end_date, signal: 'HOLD', confidence: 0.5 }],
      };
      state.runs.unshift(run);
      return json(route, run, 201);
    }
    return json(route, { detail: 'unsupported method' }, 405);
  });

  await page.route(ROUTE.adminDashboard, (route) =>
    json(route, { detail: 'Admin access required' }, 403),
  );
  await page.route(ROUTE.adminHypotheses, (route) =>
    json(route, { detail: 'Admin access required' }, 403),
  );
}

async function open(page: Page, path: string): Promise<void> {
  await page.addInitScript((value: string) => {
    localStorage.setItem('token', value);
  }, TOKEN);
  await page.goto(`${FRONTEND_BASE}${path}`, { waitUntil: 'domcontentloaded' });
  // The first paint is server HTML; clicks before React attaches are dropped
  // (measured 4/5 losses at domcontentloaded). Wait for a state that can only
  // appear after the mount effect ran, so the click is not a coin flip.
  await page.waitForSelector(
    path === '/inference'
      ? '[data-testid="inference-runs-empty"], [data-testid="inference-runs-error"], [data-testid="inference-run"]'
      : '[data-testid="app-bar"]',
    { state: 'attached' },
  );
}

async function openInferenceForm(page: Page): Promise<void> {
  const toggle = page.getByTestId('inference-toggle-form');
  await toggle.click();
  // 隱藏表單 only renders once showForm flipped, so this proves the click was
  // handled. The selector itself is asserted by the tests that expect versions.
  await expect(toggle).toHaveText('隱藏表單');
}

test.describe('inference entry and admin restriction', () => {
  test.skip(!READY, SKIP_REASON);

  test('tools menu links to 推論紀錄', async ({ page }) => {
    const state = defaultState();
    await install(page, state);
    await open(page, '/dashboard');

    await page.getByTestId('tools-toggle').click();
    const link = page.getByTestId('tools-toggle-menu').getByRole('menuitem', { name: '推論紀錄' });
    await expect(link).toBeVisible();
    await link.click();

    await expect(page).toHaveURL(/\/inference$/);
    await expect(page.getByRole('heading', { name: '推論紀錄' })).toBeVisible();
  });

  test('selector lists owned versions and the form posts structured input', async ({ page }) => {
    const state = defaultState();
    await install(page, state);
    await open(page, '/inference');
    await openInferenceForm(page);

    const selector = page.getByTestId('inference-version');
    await expect(selector.locator('option')).toHaveCount(3);
    await expect(selector).toContainText('均線研究 · 版本 1');
    await expect(selector).toContainText('動量研究 · 版本 2');

    await selector.selectOption(V1);
    await expect(page.getByTestId('inference-version-detail')).toContainText('規則型');

    await page.getByTestId('inference-symbol').fill('DEMO-ALPHA');
    await page.getByTestId('inference-start').fill('2024-01-02');
    await page.getByTestId('inference-end').fill('2024-01-31');
    await page.getByTestId('inference-submit').click();

    await expect(page.getByTestId('inference-notice')).toContainText('任務已建立');
    expect(state.posts).toHaveLength(1);
    expect(state.posts[0]).toEqual({
      model_version_id: V1,
      input_spec: { symbol: 'DEMO-ALPHA', start_date: '2024-01-02', end_date: '2024-01-31' },
    });

    const row = page.getByTestId('inference-run');
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId('inference-run-version')).toContainText('均線研究 · 版本 1');
    await expect(row).toContainText('已完成');
  });

  test('version load failure is visible and retryable, not an empty selector', async ({ page }) => {
    const state = defaultState();
    state.versionListFails = true;
    await install(page, state);
    await open(page, '/inference');
    await openInferenceForm(page);

    const error = page.getByTestId('inference-versions-error');
    await expect(error).toBeVisible();
    await expect(error).toContainText('無法載入你的模型版本');
    await expect(page.getByTestId('inference-version')).toHaveCount(0);

    state.versionListFails = false;
    await error.getByRole('button', { name: '重試載入版本' }).click();
    await expect(page.getByTestId('inference-version')).toBeVisible();
    await expect(page.getByTestId('inference-version').locator('option')).toHaveCount(3);
  });

  test('no owned versions explains the path instead of offering a dead form', async ({ page }) => {
    const state = defaultState();
    state.records = [];
    await install(page, state);
    await open(page, '/inference');
    await page.getByTestId('inference-toggle-form').click();

    const empty = page.getByTestId('inference-versions-empty');
    await expect(empty).toContainText('尚無模型版本');
    await expect(empty.getByRole('link', { name: '模型研究記錄' })).toHaveAttribute('href', '/models');
    await expect(page.getByTestId('inference-submit')).toHaveCount(0);
  });

  test('an inverted date range is refused without posting', async ({ page }) => {
    const state = defaultState();
    await install(page, state);
    await open(page, '/inference');
    await openInferenceForm(page);

    await page.getByTestId('inference-version').selectOption(V1);
    await page.getByTestId('inference-symbol').fill('DEMO-ALPHA');
    await page.getByTestId('inference-start').fill('2024-02-01');
    await page.getByTestId('inference-end').fill('2024-01-02');
    await page.getByTestId('inference-submit').click();

    await expect(page.getByTestId('inference-form-error')).toContainText('結束日期不可早於開始日期');
    expect(state.posts).toHaveLength(0);
  });

  test('run history failure offers a retry instead of reading as "no runs"', async ({ page }) => {
    const state = defaultState();
    state.runListFails = true;
    await install(page, state);
    await open(page, '/inference');

    const error = page.getByTestId('inference-runs-error');
    await expect(error).toBeVisible();
    await expect(page.getByTestId('inference-runs-empty')).toHaveCount(0);

    state.runListFails = false;
    await error.getByRole('button', { name: '重試載入紀錄' }).click();
    await expect(page.getByTestId('inference-runs-empty')).toBeVisible();
  });

  test('admin has no navigation entry and refuses an ordinary account', async ({ page }) => {
    const state = defaultState();
    await install(page, state);
    await open(page, '/dashboard');

    await page.getByTestId('tools-toggle').click();
    const menu = page.getByTestId('tools-toggle-menu');
    await expect(menu.getByRole('menuitem', { name: '推論紀錄' })).toBeVisible();
    await expect(menu.locator('a[href="/admin"]')).toHaveCount(0);

    await page.goto(`${FRONTEND_BASE}/admin`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('admin-access-error')).toContainText('需要管理員權限才能訪問此頁面');
    await expect(page.getByRole('heading', { name: '管理員儀表板' })).toBeVisible();
  });
});
