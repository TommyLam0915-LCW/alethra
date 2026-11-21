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
 *   /journal    a failed CSV export request was swallowed by `catch {}`, so the
 *               button looked dead, and a filter with no trades downloaded a
 *               header-only file that read as a successful export.
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
  journalTrades: /\/api\/v1\/journal\/trades(\?.*)?$/,
  journalSkillAttribution: /\/api\/v1\/journal\/skill-attribution(\?.*)?$/,
  journalFilters: /\/api\/v1\/journal\/filters(\?.*)?$/,
  journalCsv: /\/api\/v1\/journal\/trades\.csv(\?.*)?$/,
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
    await expect(notice).toContainText('Could not load experiments');
    await expect(page.getByRole('heading', { name: 'No experiments yet' })).toHaveCount(0);

    failing = false;
    await notice.getByRole('button', { name: 'Retry' }).click();
    await expect(page.getByRole('heading', { name: 'No experiments yet' })).toBeVisible();
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

  test('model research records stay reachable from the primary navigation', async ({ page }) => {
    await page.route(ROUTE.experiments, (route) => json(route, []));

    await open(page, '/dashboard');

    const entry = page.getByTestId('nav-models');
    await expect(entry).toBeVisible();
    await expect(entry).toHaveAttribute('href', '/models');
  });
});

test.describe('journal CSV export says what happened', () => {
  test.skip(!READY, SKIP_REASON);

  // The export button reads only the body it is handed, so the table behind it is
  // served empty on purpose: none of these cases may pass because of the list.
  async function openJournal(page: Page) {
    await page.route(ROUTE.journalFilters, (route) =>
      json(route, { instruments: [], rule_families: [], exit_reasons: [] }),
    );
    await page.route(ROUTE.journalTrades, (route) =>
      json(route, {
        trades: [],
        total_count: 0,
        summary: { total_trades: 0, total_pnl: '0', win_rate: null },
      }),
    );
    await page.route(ROUTE.journalSkillAttribution, (route) => json(route, { skills: [] }));
    await open(page, '/journal');
    return page.getByRole('button', { name: 'Export trades to CSV' });
  }

  const CSV_HEADER = 'run_id,trade_id,instrument_id,net_pnl,pnl_reason_zh\r\n';
  const csv = (route: Route, body: string) =>
    route.fulfill({ status: 200, contentType: 'text/csv; charset=utf-8', body });

  test('a failed export names the server reason instead of doing nothing', async ({ page }) => {
    const button = await openJournal(page);
    await page.route(ROUTE.journalCsv, serverError);

    await button.click();

    const notice = page.getByTestId('journal-export-error');
    await expect(notice).toBeVisible();
    // The detail the backend sent, not a generic "download failed".
    await expect(notice).toContainText('模擬的伺服器錯誤（回歸測試）');
    await expect(page.getByTestId('journal-export-notice')).toHaveCount(0);
  });

  test('a header-only export is reported as nothing to export', async ({ page }) => {
    const button = await openJournal(page);
    // Exactly what /journal/trades.csv returns for a filter with no trades.
    await page.route(ROUTE.journalCsv, (route) => csv(route, CSV_HEADER));

    await button.click();

    const notice = page.getByTestId('journal-export-notice');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('nothing to export');
    await expect(page.getByTestId('journal-export-error')).toHaveCount(0);
  });

  test('a real export states the record count, not just that it succeeded', async ({ page }) => {
    const button = await openJournal(page);
    await page.route(ROUTE.journalCsv, (route) =>
      csv(
        route,
        `${CSV_HEADER}run-1,t-1,BTC-USD,10.5,目標價達成\r\nrun-1,t-2,ETH-USD,-3.25,訊號反轉\r\n`,
      ),
    );

    await button.click();

    await expect(page.getByTestId('journal-export-notice')).toContainText('Exported 2 trades');
    await expect(page.getByTestId('journal-export-error')).toHaveCount(0);
  });

  test('a reason cell holding a line break is still one trade', async ({ page }) => {
    const button = await openJournal(page);
    // csv.QUOTE_MINIMAL quotes such a field, so counting physical lines would
    // announce two trades from a file that holds one.
    await page.route(ROUTE.journalCsv, (route) =>
      csv(route, `${CSV_HEADER}run-1,t-1,BTC-USD,10.5,"前半段持有\r\n後半段提前出場"\r\n`),
    );

    await button.click();

    await expect(page.getByTestId('journal-export-notice')).toContainText('Exported 1 trade');
  });
});
