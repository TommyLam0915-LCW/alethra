import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Tracked regression for the load-failure surfaces that used to be silent:
 *
 *   /dashboard  a failed experiments request rendered as "還沒有任何實驗",
 *               which read as an empty account instead of a broken request.
 *   /models     a failed model-research request rendered as "尚無模型研究記錄".
 *   /pilot      a failed applications/entitlements request rendered as the
 *               untouched application form.
 *   /training   a failed instruments/models/entitlement load left the picker,
 *               model list and quota card looking like a new account.
 *
 * Also covers the two paths that must stay quiet: the Pilot 404 ("nothing
 * submitted yet") and the tools-menu entry that makes /models reachable.
 *
 * DATA SETUP vs UI UNDER TEST:
 *   - No database and no backend are involved. Every API response the pages
 *     touch is intercepted here, so a forced 500 is a real HTTP failure the
 *     page receives through `fetch`, not a stubbed component prop.
 *   - The only auth step is a token in localStorage; the app shell and the
 *     pages gate on its presence, and no request leaves the mocked set.
 *   - Nothing is written: no consent, no orders, no jobs, no training data.
 *
 * Required environment (never the shared runtime):
 *   E2E_FRONTEND_BASE  a frontend whose NEXT_PUBLIC_API_URL the routes below
 *                      can intercept, e.g. http://127.0.0.1:3100
 *
 * The file skips itself when E2E_FRONTEND_BASE is absent, so a default
 * `playwright test` never assumes a particular stack is running.
 */

const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? '';
const READY = FRONTEND_BASE.length > 0;
const SKIP_REASON = 'requires E2E_FRONTEND_BASE pointing at the frontend under test';

const TOKEN = 'e2e-silent-failure-local-only';

const ROUTE = {
  experiments: /\/api\/v1\/experiments\/?(\?.*)?$/,
  modelResearch: /\/api\/v1\/model-research\/?(\?.*)?$/,
  pilotApplication: /\/api\/v1\/pilot\/applications\/me\/?$/,
  pilotEntitlement: /\/api\/v1\/pilot\/entitlements\/me\/?$/,
  instruments: /\/api\/v1\/instruments\/?(\?.*)?$/,
  mlModels: /\/api\/v1\/ml\/models\/?(\?.*)?$/,
  mlEntitlement: /\/api\/v1\/ml\/entitlement\/?(\?.*)?$/,
  trainingJobs: /\/api\/v1\/training\/jobs\/?(\?.*)?$/,
};

async function open(page: Page, path: string): Promise<void> {
  // The token must exist before the page's first effect runs; typing it in
  // after navigation would race the auth guard.
  await page.addInitScript((value: string) => {
    localStorage.setItem('token', value);
  }, TOKEN);
  await page.goto(`${FRONTEND_BASE}${path}`);
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

function serverError(route: Route) {
  return route.fulfill({
    status: 500,
    contentType: 'application/json',
    body: JSON.stringify({ detail: '模擬的伺服器錯誤（回歸測試）' }),
  });
}

test.describe('load failures are visible, not empty states', () => {
  test.skip(!READY, SKIP_REASON);

  test('dashboard shows a retryable error instead of "no experiments"', async ({ page }) => {
    let failing = true;
    await page.route(ROUTE.experiments, (route) =>
      failing ? serverError(route) : json(route, []),
    );

    await open(page, '/dashboard');

    const notice = page.getByTestId('dashboard-load-error');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('無法載入實驗列表');
    await expect(page.getByRole('heading', { name: '還沒有任何實驗' })).toHaveCount(0);

    failing = false;
    await notice.getByRole('button', { name: '重試' }).click();
    await expect(page.getByRole('heading', { name: '還沒有任何實驗' })).toBeVisible();
    await expect(notice).toHaveCount(0);
  });

  test('model records show a retryable error instead of "none yet"', async ({ page }) => {
    let failing = true;
    await page.route(ROUTE.modelResearch, (route) =>
      failing ? serverError(route) : json(route, []),
    );

    await open(page, '/models');

    const notice = page.getByTestId('model-records-error');
    await expect(notice).toBeVisible();
    // The server's own reason is shown rather than a generic string, so a
    // structured refusal is not flattened into "something went wrong".
    await expect(notice).toContainText('模擬的伺服器錯誤（回歸測試）');
    await expect(page.getByText('尚無模型研究記錄', { exact: false })).toHaveCount(0);

    failing = false;
    await notice.getByRole('button', { name: '重試' }).click();
    await expect(page.getByText('尚無模型研究記錄', { exact: false })).toBeVisible();
    await expect(notice).toHaveCount(0);
  });

  test('pilot load failure is reported instead of rendering the form as untouched', async ({ page }) => {
    let failing = true;
    await page.route(ROUTE.pilotApplication, (route) =>
      failing ? serverError(route) : json(route, { detail: 'Not found' }, 404),
    );
    await page.route(ROUTE.pilotEntitlement, (route) =>
      failing ? serverError(route) : json(route, { detail: 'Not found' }, 404),
    );

    await open(page, '/pilot');

    const notice = page.getByTestId('pilot-load-error');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('無法載入Pilot 申請狀態與Pilot 權限資料');

    // The 404 answer is the normal "nothing submitted yet" case and must not
    // render an error once the requests stop failing.
    failing = false;
    await notice.getByRole('button', { name: '重試' }).click();
    await expect(page.getByRole('heading', { name: '提交 Pilot 申請' })).toBeVisible();
    await expect(notice).toHaveCount(0);
  });

  test('training reports a failed ML data load instead of showing a fresh account', async ({ page }) => {
    await page.route(ROUTE.instruments, (route) => json(route, { instruments: [] }));
    await page.route(ROUTE.mlModels, serverError);
    await page.route(ROUTE.mlEntitlement, (route) =>
      json(route, {
        entitlement_active: true,
        limit: 3,
        used: 0,
        remaining: 3,
        period_start: null,
        period_end: null,
        unlimited: false,
      }),
    );
    await page.route(ROUTE.trainingJobs, (route) => json(route, []));

    await open(page, '/training');

    const notice = page.getByTestId('ml-load-error');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('無法載入模型與額度資料');
  });

  test('model research records stay reachable from the tools menu', async ({ page }) => {
    await page.route(ROUTE.experiments, (route) => json(route, []));

    await open(page, '/dashboard');

    await page.getByTestId('tools-toggle').click();
    const entry = page.getByRole('menuitem', { name: '模型研究記錄' });
    await expect(entry).toBeVisible();
    await expect(entry).toHaveAttribute('href', '/models');
  });
});
