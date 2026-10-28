import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * TIER B — the ORDER LIFECYCLE journey against the REAL local backend (#396).
 *
 * Covers 交易提交 → pending／等待 → 市場走到可執行的那一根 bar → 真實執行 → fills／settlement
 * → portfolio cash／position → journal／CSV, and the three properties that make it
 * trustworthy rather than convenient:
 *
 *   1. pending ≠ executed — a submitted order books no cash, no position and no fill;
 *      the UI says which bar it is waiting for, and 決策基準 (the observed cursor) and
 *      下單時間 (the wall-clock submit) are two different printed values.
 *   2. the market clock decides execution — the order fills on the FIRST stored bar
 *      strictly after the cursor the user had observed, never on that bar itself.
 *      The test claims the window from the pending order's own view, then advances and
 *      requires the clock to land exactly on the claimed date.
 *   3. repeated processing is idempotent — advancing further does not re-book the
 *      fill, cancel-before-execution leaves nothing behind, and an order the dataset
 *      can no longer reach is closed out as expired rather than silently dropped.
 *
 * WHAT IS DATA SETUP vs UI UNDER TEST (disclosed item by item):
 *   - DATA SETUP at the database level, by the tracked seed
 *     `backend/scripts/seed_e2e_order_lifecycle.py`: the account, the LOCKED
 *     experiment, its passive strategy version and the synthetic DEMO-ALPHA daily
 *     bars 2024-02-01..2024-02-13, plus the watchlist row that makes the symbol
 *     clickable in the workspace. Strategy authoring and watchlist creation UI are
 *     covered by their own specs; this one is about the order lifecycle.
 *   - EVERY product action is a real browser gesture: typing the login, 開始模擬,
 *     連結模擬組合, clicking the watchlist symbol, choosing the account in ⑦,
 *     「下一日」, filling 動作／數量／理由, 檢視模擬單, 提交模擬單, 取消掛單,
 *     the ⑧ tabs, 重新載入證據 and the CSV export button. No API call performs a
 *     product step and no API call writes product state.
 *   - API calls in this file are READ-ONLY oracles (session row, order list, fills,
 *     portfolio, decisions) plus one CSV parse. They cross-check what the screen
 *     claims against what PostgreSQL persisted; each is labelled where it supplements.
 *   - No route is intercepted, stubbed or mocked. Waits are waitForResponse /
 *     waitForURL / expect-poll conditions, never sleeps.
 *
 * ALL DATA IS SYNTHETIC DEMO DATA (provider `demo`, timeframe 1d, 非即時行情). Advancing a
 * disposable replay and filling paper orders at stored fixture opens is paper simulation —
 * it is not broker integration, not live-market validation, and nothing here is evidence
 * about real execution prices.
 *
 * Dates and prices are never hardcoded: the expected execution window comes from the
 * pending order's own server-supplied view, the settlement date from the fill's own claim,
 * and both are then proven by walking the replay clock to that date.
 *
 * Credentials come from the generated git-ignored file; no secret value is declared,
 * printed, asserted or written to an artifact.
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

const runtime = loadRuntime();
const EXPERIMENT_TITLE = 'E2E 訂單生命週期流程（僅供自動化測試）';
const ASSET = 'DEMO-ALPHA';
const SESSION_CASH = 100000;
const BUY_QTY = 20;
// The rationale the user types for the order that is meant to fill. The journal export has
// to carry this exact text, so it is written down once here rather than retyped per test.
const FILLED_RATIONALE = '生命週期第二張單：由市場時間執行';
// Cost conventions the seeded strategy version pins (strategy_spec.commission_rate /
// slippage_rate). Read back from the version row and asserted before use, so a drift in
// the seed cannot silently turn the recomputed fill economics into a false positive.
const EXPECT_COMMISSION_RATE = 0.001;
const EXPECT_SLIPPAGE_RATE = 0.001;

const round4 = (v: number) => Math.round(v * 10000) / 10000;

interface FillRow {
  id: string;
  paper_order_id: string;
  execution_date: string;
  bar_timestamp: string | null;
  observed_price: number;
  execution_price_before_costs: number;
  slippage_amount: number;
  commission_amount: number;
  quantity: number;
  gross_amount: number;
  net_cash_change: number;
  simulated_fill_reason: string | null;
}

interface OrderRow {
  id: string;
  portfolio_id: string;
  asset_id: string;
  side: string;
  quantity: number;
  status: string;
  actor_type: string;
  data_cutoff_at: string | null;
  created_at: string | null;
}

interface SessionRow {
  id: string;
  status: string;
  timeframe: string | null;
  provider_id: string | null;
  data_cutoff_at: string;
  current_snapshot_date: string | null;
  initial_virtual_cash: number;
  observed_days: number;
}

interface DecisionRow {
  id: string;
  decision_type: string;
  outcome_status: string;
  actor_type: string;
  data_cutoff_at: string | null;
  linked_order_id?: string | null;
}

/** Read-only oracle. Supplements the UI assertions; never replaces or performs one. */
async function api(page: Page, method: 'GET' | 'POST', path: string, body?: unknown) {
  const token = await page.evaluate(() => localStorage.getItem('token'));
  if (!token) throw new Error('the UI session holds no token; login did not persist');
  const res = await page.request.fetch(`${runtime.backendBase}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status(), json: text ? JSON.parse(text) : null };
}

async function readSession(page: Page, sessionId: string): Promise<SessionRow> {
  const { status, json } = await api(page, 'GET', `/api/v1/simulations/${sessionId}`);
  expect(status, 'the session row must be readable as an oracle').toBe(200);
  return json as SessionRow;
}

async function storedOrders(page: Page, portfolioId: string): Promise<OrderRow[]> {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/orders`);
  expect(status).toBe(200);
  return (Array.isArray(json) ? json : []) as OrderRow[];
}

async function storedFills(page: Page, portfolioId: string, orderId: string): Promise<FillRow[]> {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/orders/${orderId}/fills`);
  expect(status).toBe(200);
  return (Array.isArray(json) ? json : []) as FillRow[];
}

async function storedDecisions(page: Page, portfolioId: string): Promise<DecisionRow[]> {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/decisions`);
  expect(status).toBe(200);
  return (Array.isArray(json) ? json : []) as DecisionRow[];
}

async function storedPortfolio(page: Page, portfolioId: string) {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}`);
  expect(status).toBe(200);
  return json as { current_virtual_cash: number; position_count?: number; total_unrealized_pnl?: number };
}

// ---- the ⑦ panel's own rendered statements ----

/**
 * The account picker is the panel's `<select id="order-portfolio-select">` — it carries an
 * element id rather than a data-testid, so it is addressed by id scoped inside ⑦. Picking
 * explicitly is the product rule (#385): the app never guesses which account to trade.
 */
const portfolioSelect = (page: Page) =>
  page.getByTestId('order-preview-panel').locator('#order-portfolio-select');

async function pickPortfolio(page: Page, portfolioId: string): Promise<void> {
  await portfolioSelect(page).selectOption(portfolioId);
  await expect(portfolioSelect(page), '⑦ 必須保留使用者明確選擇的組合').toHaveValue(portfolioId);
}

const cashItem = (page: Page) =>
  page.locator('.order-account-item', { hasText: '虛擬現金' }).locator('.order-account-value');

async function uiCash(page: Page): Promise<number> {
  const raw = (await cashItem(page).innerText()).replace(/,/g, '');
  const match = raw.match(/-?\d+(?:\.\d+)?/);
  if (!match) throw new Error(`could not read a cash figure from "${raw}"`);
  return Number(match[0]);
}

const executionCell = (page: Page) => page.getByTestId('order-manual-execution');

/** 「等待 {date} 開盤 {open} 執行」 — the window the server says the order waits for. */
async function claimedWindow(page: Page): Promise<{ date: string; open: number }> {
  const text = (await executionCell(page).innerText()).replace(/\s+/g, ' ');
  const m = /等待 (\d{4}-\d{2}-\d{2}) 開盤 ([\d.]+) 執行/.exec(text);
  if (!m) throw new Error(`掛單未聲稱任何可執行窗口：「${text}」`);
  return { date: m[1], open: Number(m[2]) };
}

/** 「已於 {date} 成交；結算日 {settlement}」 — where the order landed, per the server. */
async function claimedExecution(page: Page): Promise<{ date: string; settlement: string }> {
  const text = (await executionCell(page).innerText()).replace(/\s+/g, ' ');
  const m = /已於 (\d{4}-\d{2}-\d{2}) 成交；結算日 (\S+)/.exec(text);
  if (!m) throw new Error(`掛單未聲稱成交與結算日：「${text}」`);
  return { date: m[1], settlement: m[2] };
}

/**
 * ⑧'s 訂單與成交 table puts the id-bearing attributes on the asset button and the
 * 成交／回放時間 cell, not on the row, so the order's own row is the tr that contains that
 * button. Status text is read from the row, never from the button.
 */
const evidenceOrderRow = (page: Page, orderId: string) =>
  page
    .getByTestId('evidence-orders-table')
    .locator('tr', { has: page.getByTestId(`evidence-order-${orderId}`) });

const clockValue = (page: Page) => page.getByTestId('replay-clock-value');

async function advanceOnce(page: Page): Promise<{ status: number; body: any }> {
  const [resp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/api/v1/simulations/') && r.url().endsWith('/advance'),
      { timeout: 30000 },
    ),
    page.getByTestId('replay-advance').click(),
  ]);
  const body = await resp.json().catch(() => null);
  return { status: resp.status(), body };
}

async function waitForClock(page: Page, date: string): Promise<void> {
  await expect(clockValue(page), `回放時鐘必須走到 ${date}`).toHaveText(new RegExp(date));
}

/** Fill 動作／數量／理由, 檢視模擬單, then 提交模擬單 — the real three clicks. */
async function submitManual(
  page: Page,
  opts: { action: 'buy' | 'sell' | 'hold'; quantity?: number; rationale: string }
): Promise<{ status: number; body: any; idempotencyKey: string }> {
  await page.getByTestId('order-action').selectOption(opts.action);
  if (opts.action === 'buy') {
    await page.getByTestId('order-quantity-input').fill(String(opts.quantity));
  }
  await page.getByTestId('order-rationale').fill(opts.rationale);

  const buildReview = page.getByTestId('order-build-review');
  await expect(buildReview, '⑦ 必須能為這張單建立檢視').toBeEnabled();
  await buildReview.click();
  await expect(page.getByTestId('order-review')).toBeVisible();
  const reviewKeyShown = (await page.getByTestId('order-review-key').innerText()).trim();
  const reviewCursor = (await page.getByTestId('order-review-cursor').innerText()).trim();
  expect(reviewCursor, '檢視所綁定的回放游標必須是目前已觀察的那根 bar').not.toBe('');

  const [resp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/manual-decision') && r.request().method() === 'POST',
      { timeout: 30000 },
    ),
    page.getByTestId('order-submit-manual').click(),
  ]);
  const body = await resp.json().catch(() => null);
  // The cell shows only the first 18 characters of `ws-{session}-{Date.now()}`, a prefix
  // shared by every submission in the same ~11-minute window, so identity is judged on the
  // key the browser actually put on the wire.
  const sentKey = String((resp.request().postDataJSON() as any)?.idempotency_key ?? '');
  expect(sentKey, 'the submission must carry an operation identity').not.toBe('');
  expect(reviewKeyShown.replace(/…$/, ''), '⑦ 顯示的鍵必須就是送出去那一個的截斷').toBe(sentKey.slice(0, 18));
  return { status: resp.status(), body, idempotencyKey: sentKey };
}

const journey = {
  experimentId: '',
  sessionId: '',
  portfolioId: '',
  datasetEnd: '',
  cancelledOrderId: '',
  filledOrderId: '',
  expiredOrderId: '',
  windowDate: '',
  windowOpen: 0,
  filledCash: 0,
  filledRationale: '',
  fill: null as FillRow | null,
  keys: new Set<string>(),
};

test.describe('order lifecycle: submit → pending → market clock → fill → settlement → journal/CSV', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');

    // The research list is a table of name links; address the row by its accessible
    // name (exact, so the row's 「查看報告 →」 action link cannot match).
    const nameLink = page.getByRole('link', { name: EXPERIMENT_TITLE, exact: true });
    await expect(nameLink, 'seeded order-lifecycle experiment must be listed exactly once').toHaveCount(1);
    const href = (await nameLink.getAttribute('href')) ?? '';
    journey.experimentId = new URL(href, runtime.frontendBase).pathname.split('/').pop() ?? '';
    expect(journey.experimentId).not.toBe('');
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('setup through the UI: a session, its bound account, and the workspace pointed at the same instrument', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentId}/simulation`);
    await page.getByTestId('sim-create-btn').waitFor({ state: 'visible', timeout: 30000 });

    // The seeded strategy must pin the cost conventions this spec recomputes with.
    const versions = await api(page, 'GET', `/api/v1/experiments/${journey.experimentId}/versions`);
    expect(versions.status).toBe(200);
    const spec = (versions.json as any[])[0].strategy_spec;
    expect(spec.commission_rate, 'seeded commission rate').toBeCloseTo(EXPECT_COMMISSION_RATE, 12);
    expect(spec.slippage_rate, 'seeded slippage rate').toBeCloseTo(EXPECT_SLIPPAGE_RATE, 12);
    expect(spec.asset_ids, 'the strategy trades exactly one instrument').toEqual([ASSET]);

    await page
      .locator('div.form-group', { hasText: '虛擬初始資金' })
      .locator('input')
      .fill(String(SESSION_CASH));
    await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/v1/simulations/versions/') && r.request().method() === 'POST'),
      page.getByTestId('sim-create-btn').click(),
    ]);
    await expect(page.getByTestId('sim-advance-btn'), '建立工作階段後才出現推進控制').toBeVisible({
      timeout: 30000,
    });

    await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/api/v1/paper-portfolio') && r.request().method() === 'POST'),
      page.getByTestId('sim-link-portfolio-btn').click(),
    ]);
    await expect(page.getByTestId('sim-portfolio-status')).toBeVisible({ timeout: 30000 });
    const href = (await page.getByTestId('sim-portfolio-link').getAttribute('href')) ?? '';
    journey.portfolioId = href.split('/').filter(Boolean).pop() ?? '';
    expect(journey.portfolioId, 'linked account id resolved from the UI link').not.toBe('');

    const sessionList = await api(page, 'GET', `/api/v1/simulations/versions/${(versions.json as any[])[0].id}/sessions`);
    expect(sessionList.status).toBe(200);
    const rows = Array.isArray(sessionList.json) ? sessionList.json : (sessionList.json as any)?.sessions ?? [];
    expect(
      rows.length,
      '本旅程必須跑在剛重置過的堆疊上：seed 已清掉上一份工作階段，' +
        '若這裡出現 2 份，代表同一個 version 上還留著上一次跑過的 session — ' +
        '請先執行 backend/scripts/seed_e2e_order_lifecycle.py 再跑',
    ).toBe(1);
    journey.sessionId = String(rows[0].id);
    const session = await readSession(page, journey.sessionId);
    expect(session.provider_id, 'the session binds to one stored provider').toBe('demo');
    expect(session.timeframe).toBe('1d');
    expect(session.initial_virtual_cash).toBeCloseTo(SESSION_CASH, 6);
    journey.datasetEnd = session.data_cutoff_at;
    expect(session.current_snapshot_date, 'a fresh session has not observed a bar yet').toBeNull();

    const book = await storedPortfolio(page, journey.portfolioId);
    expect(book.current_virtual_cash, 'a brand-new account holds its starting cash').toBeCloseTo(SESSION_CASH, 6);

    // The workspace: choose the plotted symbol by clicking the seeded watchlist row,
    // then choose the account explicitly. ⑦ only opens for the session's own instrument.
    await page.goto(`${runtime.frontendBase}/workspace`);
    await page.getByTestId('watchlist-panel').waitFor({ state: 'visible', timeout: 30000 });
    await page
      .getByTestId('watchlist-panel')
      .getByRole('button', { name: `選取標的 ${ASSET}` })
      .click();
    await expect(page.getByTestId('order-symbol')).toContainText(ASSET);

    await pickPortfolio(page, journey.portfolioId);
    await expect(page.getByTestId('order-blocked')).toHaveCount(0);
    await expect(page.getByTestId('order-target')).toContainText('歷史回放工作階段');
    await expect(clockValue(page)).toContainText('尚未推進');
    await expect(cashItem(page)).toContainText(String(SESSION_CASH.toLocaleString()));
  });

  test('the clock walks to the first stored bar, which becomes the decision basis', async () => {
    const res = await advanceOnce(page);
    expect(res.status, 'the first advance must be accepted').toBe(200);
    const cursor = String(res.body?.session?.current_snapshot_date ?? '');
    expect(/^\d{4}-\d{2}-\d{2}$/.test(cursor), `advance must report its new cursor, got "${cursor}"`).toBe(true);
    await waitForClock(page, cursor);

    const session = await readSession(page, journey.sessionId);
    expect(session.current_snapshot_date, 'the rendered clock equals the stored cursor').toBe(cursor);
    expect(session.status, 'an in-progress replay accepts decisions').toBe('active');
  });

  test('a submitted order is pending, not executed: no cash, no position, and it waits for a LATER bar', async () => {
    const cashBefore = await uiCash(page);
    const cursor = (await clockValue(page).innerText()).trim().slice(0, 10);
    expect(cursor).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    const sent = await submitManual(page, { action: 'buy', quantity: BUY_QTY, rationale: '生命週期第一張單：先在已觀察的下一根 bar 掛起' });
    expect(sent.status, 'manual submission must be accepted').toBe(200);
    journey.cancelledOrderId = String(sent.body?.order?.order_id ?? '');
    expect(journey.cancelledOrderId).not.toBe('');
    journey.keys.add(sent.idempotencyKey);

    // Separate clocks, printed apart: the observed bar the decision is based on, and
    // the wall-clock instant the order was written.
    const decided = (await page.getByTestId('order-manual-decided').innerText()).replace(/\s+/g, ' ');
    const decidedMatch = /(\d{4}-\d{2}-\d{2}) \/ (\S+)/.exec(decided);
    expect(decidedMatch, `決策基準／下單時間 must both render, got "${decided}"`).not.toBeNull();
    expect(decidedMatch![1], 'decision basis is the observed cursor').toBe(cursor);
    expect(decidedMatch![2], 'order time is a distinct timestamp, not the same quantity').not.toBe(cursor);
    expect(decidedMatch![2]).toMatch(/^\d{4}-\d{2}-\d{2}T[\d:.]+/);

    // Pending reads as pending, never as a holding.
    await expect(page.getByTestId('order-manual-order')).toContainText('掛單待執行');
    await expect(page.getByTestId('order-manual-quantities')).toContainText(`0 / ${BUY_QTY}`);

    // The window is strictly AFTER the observed bar — the order is not crammed into it.
    const window = await claimedWindow(page);
    journey.windowDate = window.date;
    journey.windowOpen = window.open;
    expect(window.date > cursor, `執行窗口 ${window.date} 必須晚於決策基準 ${cursor}`).toBe(true);
    expect(window.open, 'the claimed window must name a real opening price').toBeGreaterThan(0);

    // Nothing economic happened yet: cash identical, no fill, no position anywhere.
    expect(await uiCash(page), 'pending 掛單不得變動現金').toBeCloseTo(cashBefore, 6);
    const orders = await storedOrders(page, journey.portfolioId);
    const mine = orders.find((o) => o.id === journey.cancelledOrderId);
    expect(mine, 'the submitted intent must be persisted').toBeDefined();
    expect(mine!.status).toBe('pending');
    expect(mine!.actor_type).toBe('human');
    expect(mine!.asset_id).toBe(ASSET);
    expect(mine!.quantity).toBeCloseTo(BUY_QTY, 6);
    expect(mine!.data_cutoff_at).toBe(cursor);
    expect(orders.filter((o) => o.status === 'filled'), 'no order may already be filled').toEqual([]);
    expect(await storedFills(page, journey.portfolioId, mine!.id), 'a pending order has no fill').toEqual([]);
    expect((await storedPortfolio(page, journey.portfolioId)).current_virtual_cash).toBeCloseTo(cashBefore, 6);

    // ⑧ shows the intent as an order with no execution time, and no fills at all —
    // without any 重新載入 click. Recording the decision bumps the session's
    // state_version, and the dock re-reads the account on that bump, so an order the
    // user just submitted cannot be invisible until they press a refresh button.
    await page.getByTestId('tab-orders-fills').click();
    const mineRow = evidenceOrderRow(page, mine!.id);
    await expect(mineRow, '⑧ must list the submitted intent as an order row').toBeVisible({ timeout: 30000 });
    // ⑧ uses its own wording for the same stored status (⑦ says 掛單待執行, ⑧ says 待成交;
    // the divergence is pinned by workspace.spec.ts, so each surface is read in its own terms).
    await expect(mineRow).toContainText('待成交');
    await expect(mineRow).toContainText('買入');
    await expect(mineRow, '本人 ≠ 系統規則：這是你親手提交的一單').toContainText('本人');
    await expect(page.getByTestId(`evidence-order-exec-time-${mine!.id}`)).toContainText('不適用');
    await expect(page.getByTestId('evidence-no-fills')).toBeVisible();

    // Same without 重新載入: 一筆掛單 must not create a position, and the dock must
    // re-read the account on the submission itself rather than showing a stale
    // table the user has to refresh.
    await page.getByTestId('tab-positions').click();
    await expect(page.getByTestId('evidence-no-positions')).toContainText('沒有持倉');
    await expect(page.getByTestId('evidence-positions-table'), 'pending 掛單不得產生持倉表').toHaveCount(0);
  });

  test('cancelling before execution cancels the intent and moves nothing', async () => {
    const cashBefore = await uiCash(page);
    const shortId = journey.cancelledOrderId.slice(0, 8);
    const row = page.getByTestId(`order-book-row-${shortId}`);
    await expect(row).toBeVisible();
    await expect(row).toContainText('掛單待執行');

    await page.getByTestId('tab-orders-fills').click();
    const dockRow = evidenceOrderRow(page, journey.cancelledOrderId);
    await expect(dockRow, '⑧ shows the same order as 待成交 before the cancel').toContainText('待成交');

    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes(`/orders/${journey.cancelledOrderId}/cancel`) && r.request().method() === 'POST',
        { timeout: 30000 },
      ),
      page.getByTestId(`order-book-cancel-${shortId}`).click(),
    ]);

    await expect(row, 'the book must re-read the cancelled state').toContainText('已取消');
    const stored = (await storedOrders(page, journey.portfolioId)).find((o) => o.id === journey.cancelledOrderId);
    expect(stored!.status).toBe('cancelled');
    expect(await storedFills(page, journey.portfolioId, journey.cancelledOrderId), 'a cancelled order never fills').toEqual([]);
    expect(await uiCash(page), '取消不得入帳或扣款').toBeCloseTo(cashBefore, 6);
    expect((await storedPortfolio(page, journey.portfolioId)).current_virtual_cash).toBeCloseTo(cashBefore, 6);

    // Cancel is POST /paper-portfolio/{portfolio}/orders/{id}/cancel: it moves neither
    // the replay cursor nor the session's state_version, the two clocks ⑧ otherwise
    // refetches on. The mutation site therefore names its own revision, and the dock
    // re-reads on that bump — so 待成交 becomes 已取消 with no 重新載入 click. Submit and
    // advance self-refresh for the same reason; nothing here presses refresh.
    await expect(dockRow, '⑧ must re-read the cancelled state without a manual reload').toContainText('已取消');
    await expect(dockRow).not.toContainText('待成交');
    await expect(page.getByTestId(`evidence-order-exec-time-${journey.cancelledOrderId}`)).toContainText('不適用');
    await expect(page.getByTestId('evidence-no-fills')).toBeVisible();
  });

  test('a fresh intent on the same clock waits for the same window the cancelled one named', async () => {
    const sent = await submitManual(page, { action: 'buy', quantity: BUY_QTY, rationale: FILLED_RATIONALE });
    expect(sent.status).toBe(200);
    journey.filledOrderId = String(sent.body?.order?.order_id ?? '');
    journey.filledRationale = FILLED_RATIONALE;
    expect(journey.filledOrderId).not.toBe('');
    expect(journey.keys.has(sent.idempotencyKey), 'a changed submission must mint a new key').toBe(false);
    journey.keys.add(sent.idempotencyKey);

    await expect(page.getByTestId('order-manual-order')).toContainText('掛單待執行');
    const window = await claimedWindow(page);
    expect(window.date, 'the same cursor must name the same execution window').toBe(journey.windowDate);
    expect(window.open).toBeCloseTo(journey.windowOpen, 6);
  });

  test('the market clock decides: it lands on the claimed bar and fills once, at that bar open', async () => {
    const res = await advanceOnce(page);
    expect(res.status, 'the advance that reaches the window must be accepted').toBe(200);
    const cursor = String(res.body?.session?.current_snapshot_date ?? '');
    expect(cursor, '執行必須發生在掛單自己聲稱的那根 bar，不是別的 bar').toBe(journey.windowDate);
    await waitForClock(page, cursor);

    await expect(page.getByTestId('order-manual-order'), 'the receipt must track live state').toContainText('已成交');
    await expect(page.getByTestId('order-manual-quantities')).toContainText(`${BUY_QTY} / ${BUY_QTY}`);
    await expect(page.getByTestId('order-manual-quantities')).toContainText('成交筆數 1');

    const claimed = await claimedExecution(page);
    expect(claimed.date).toBe(journey.windowDate);
    expect(claimed.settlement !== claimed.date, '結算日 must be a different session from 成交日').toBe(true);

    // Oracle: exactly one fill row, priced off the stored bar the panel named.
    const fills = await storedFills(page, journey.portfolioId, journey.filledOrderId);
    expect(fills.length, 'one advance books exactly one fill').toBe(1);
    const fill = fills[0];
    journey.fill = fill;
    expect(fill.execution_date).toBe(journey.windowDate);
    expect(fill.observed_price, '觀察價 must be the window open the UI claimed').toBeCloseTo(journey.windowOpen, 6);
    expect(fill.quantity).toBeCloseTo(BUY_QTY, 6);
    const exec = round4(fill.observed_price * (1 + EXPECT_SLIPPAGE_RATE));
    const gross = round4(BUY_QTY * exec);
    const commission = round4(gross * EXPECT_COMMISSION_RATE);
    expect(fill.execution_price_before_costs, '成交價 = 觀察開盤 × (1+滑點)').toBeCloseTo(exec, 6);
    expect(fill.slippage_amount).toBeCloseTo(round4(Math.abs(exec - fill.observed_price) * BUY_QTY), 6);
    expect(fill.commission_amount, '手續費只計一次').toBeCloseTo(commission, 6);
    expect(fill.gross_amount).toBeCloseTo(gross, 6);
    expect(fill.net_cash_change, '現金腿 = 毛額 + 手續費，取一次').toBeCloseTo(-(gross + commission), 6);
    expect(fill.simulated_fill_reason, 'the fill must disclose how it was priced').not.toBeNull();

    journey.filledCash = round4(SESSION_CASH + fill.net_cash_change);
    await expect
      .poll(() => uiCash(page), { message: '現金必須恰好用掉這一筆成交的淨額' })
      .toBeCloseTo(journey.filledCash, 2);
    expect((await storedPortfolio(page, journey.portfolioId)).current_virtual_cash).toBeCloseTo(journey.filledCash, 4);

    const stored = (await storedOrders(page, journey.portfolioId)).find((o) => o.id === journey.filledOrderId);
    expect(stored!.status).toBe('filled');
    expect(stored!.data_cutoff_at! < fill.execution_date, '決策基準 must be earlier than 執行日').toBe(true);

    // The position exists only now, and carries the slipped entry price.
    const positions = await api(page, 'GET', `/api/v1/paper-portfolio/${journey.portfolioId}/positions`);
    expect(positions.status).toBe(200);
    const held = (positions.json as any[]).filter((p) => p.asset_id === ASSET);
    expect(held.length, 'the fill opens exactly one position').toBe(1);
    expect(held[0].quantity).toBeCloseTo(BUY_QTY, 6);
    expect(held[0].average_entry_price).toBeCloseTo(exec, 6);
    expect(held[0].status).toBe('open');

    // ⑧ now names the bar the fill came from, not the bar the user was watching.
    // No 重新載入 here: the advance that booked this fill moved the cursor, and the
    // dock re-reads on that, so the row must already be correct when the tab opens.
    await page.getByTestId('tab-orders-fills').click();
    const filledRow = evidenceOrderRow(page, journey.filledOrderId);
    await expect(filledRow).toContainText('已成交');
    await expect(filledRow, '本人 ≠ 系統規則').toContainText('本人');
    await expect(page.getByTestId('evidence-fills-table')).toBeVisible();
    await expect(page.getByTestId(`evidence-fill-${fill.id}`)).toBeVisible();
    // A 1d fill has no sub-bar timestamp: the stored row keeps it NULL, so ⑧'s K 線時間
    // honestly reads 不適用. Only the intraday journey can put a timestamp in that column.
    expect(fill.bar_timestamp, '日線成交不得有 bar 時間戳').toBeNull();
    await expect(page.getByTestId(`evidence-fill-bar-time-${fill.id}`)).toContainText('不適用');
    const fillRow = page
      .getByTestId('evidence-fills-table')
      .locator('tr', { has: page.getByTestId(`evidence-fill-${fill.id}`) });
    await expect(fillRow, '成交日必須是掛單自己聲稱的那根 bar').toContainText(journey.windowDate);
    await expect(fillRow).toContainText('manual_buy');
    const execTime = await page.getByTestId(`evidence-order-exec-time-${journey.filledOrderId}`).innerText();
    expect(execTime, '成交後執行時間必須是真實日期，不再是 不適用').not.toContain('不適用');
    expect(execTime).toContain(journey.windowDate);
  });

  test('the settlement date is the dataset\'s next session, and re-processing books nothing twice', async () => {
    const claimed = await claimedExecution(page);
    const res = await advanceOnce(page);
    expect(res.status).toBe(200);
    const cursor = String(res.body?.session?.current_snapshot_date ?? '');
    expect(cursor, '結算日必須就是數據集的下一個儲存交易日，而不是推算的日曆日').toBe(claimed.settlement);
    await waitForClock(page, cursor);

    // Idempotence: walking further must not re-book the same intent.
    const fills = await storedFills(page, journey.portfolioId, journey.filledOrderId);
    expect(fills.length, 'repeated processing must not add a second fill').toBe(1);
    expect(fills[0].execution_date, 'and must not move the fill onto the new bar').toBe(journey.windowDate);
    expect(await uiCash(page), 'and must not debit cash twice').toBeCloseTo(journey.filledCash, 2);
    const orders = await storedOrders(page, journey.portfolioId);
    expect(orders.length, 'no order may appear from re-processing').toBe(2);
    expect(orders.filter((o) => o.status === 'pending'), 'no order may sit pending unaccounted').toEqual([]);
    expect((await storedFills(page, journey.portfolioId, journey.cancelledOrderId)).length).toBe(0);
  });

  test('reloading the workspace recovers the same lifecycle state from the database', async () => {
    await page.goto(`${runtime.frontendBase}/workspace`);
    await page
      .getByTestId('watchlist-panel')
      .getByRole('button', { name: `選取標的 ${ASSET}` })
      .click();
    await pickPortfolio(page, journey.portfolioId);
    await expect(page.getByTestId('order-blocked')).toHaveCount(0);

    const shortId = journey.filledOrderId.slice(0, 8);
    const row = page.getByTestId(`order-book-row-${shortId}`);
    await expect(row).toBeVisible({ timeout: 30000 });
    await expect(row).toContainText('已成交');
    const rowText = (await row.innerText()).replace(/\s+/g, ' ');
    expect(rowText, 'the recovered row must still state the decision basis')
      .toContain(journey.fill!.execution_date);
    expect(rowText).toContain('已成交');

    // 生命週期位置 after a reload comes from the re-read book, not from the submit receipt.
    const bookRowExecution = row.locator('td').nth(4);
    await expect(bookRowExecution).toContainText(`已於 ${journey.windowDate} 成交`);
    await expect(cashItem(page)).toContainText(journey.filledCash.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }));
    await expect(clockValue(page)).not.toContainText('尚未推進');
  });

  test('an order the dataset can no longer reach is closed out as expired, not silently filled or lost', async () => {
    // Walk the real clock to the end of the bound dataset.
    for (let guard = 0; guard < 20; guard += 1) {
      const session = await readSession(page, journey.sessionId);
      if (session.current_snapshot_date === journey.datasetEnd) break;
      const res = await advanceOnce(page);
      expect(res.status, 'advancing inside the dataset must succeed').toBe(200);
      await waitForClock(page, String(res.body?.session?.current_snapshot_date ?? ''));
    }
    const atEnd = await readSession(page, journey.sessionId);
    expect(atEnd.current_snapshot_date, 'the clock must have reached the dataset end').toBe(journey.datasetEnd);

    const cashBefore = await uiCash(page);
    const sent = await submitManual(page, { action: 'sell', rationale: '收盤後才做的決定：數據集在之後沒有可執行的 bar' });
    expect(sent.status).toBe(200);
    journey.expiredOrderId = String(sent.body?.order?.order_id ?? '');
    await expect(page.getByTestId('order-manual-order')).toContainText('掛單待執行');
    const waitingText = (await executionCell(page).innerText()).replace(/\s+/g, ' ');
    expect(waitingText, '面板必須說出「沒有可執行窗口」，而不是假裝在等一根已經走過的 bar').toMatch(
      /沒有可用的 bar|沒有可執行報價/
    );

    // The clock has nowhere left to go: the replay says so instead of inventing a bar.
    const res = await advanceOnce(page);
    expect(res.status, 'a dataset with no bar left must refuse the advance').toBe(400);
    await expect(page.getByTestId('replay-error')).toBeVisible({ timeout: 30000 });

    // Recovered from the database: the intent is closed out, and nothing was booked.
    await page.goto(`${runtime.frontendBase}/workspace`);
    await page
      .getByTestId('watchlist-panel')
      .getByRole('button', { name: `選取標的 ${ASSET}` })
      .click();
    await pickPortfolio(page, journey.portfolioId);
    const shortId = journey.expiredOrderId.slice(0, 8);
    const row = page.getByTestId(`order-book-row-${shortId}`);
    await expect(row, '過期的掛單必須在畫面上有結算狀態，不能悄悄消失').toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('order-session-book'), '工作階段結束後掛單簿必須還在').toBeVisible();
    await expect(
      page.getByTestId('order-session-book-readonly'),
      '不能提交就改用只讀呈現紀錄，而不是把整本掛單簿收起來',
    ).toContainText('只讀');
    await expect(row).toContainText('已取消');
    expect(await storedFills(page, journey.portfolioId, journey.expiredOrderId), '過期不得成交').toEqual([]);
    expect(await uiCash(page), '過期不得入帳').toBeCloseTo(cashBefore, 2);

    const decisions = await storedDecisions(page, journey.portfolioId);
    const expiredDecision = decisions.find((d) => d.outcome_status === 'expired');
    expect(expiredDecision, '日誌必須以 expired 記下這條未成交的意圖').toBeDefined();
    const completed = await readSession(page, journey.sessionId);
    expect(completed.status).toBe('completed');
  });

  test('the CSV export the panel downloads carries the same ids, times, quantities and costs', async () => {
    await page.getByTestId('tab-journal').click();
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }),
      page.getByTestId('evidence-export-csv').click(),
    ]);
    expect(download.suggestedFilename()).toBe(`trade-journal-${journey.portfolioId}.csv`);
    const path = await download.path();
    const csv = readFileSync(path!, 'utf-8');
    const rows = parseCsv(csv);
    const header = rows[0];
    expect(header[0]).toBe('record_type');
    expect(header.length, 'the journal must export every declared column').toBeGreaterThanOrEqual(27);

    const byOrder = new Map<string, Record<string, string>[]>();
    for (const row of rows.slice(1)) {
      const record = Object.fromEntries(header.map((col, i) => [col, row[i] ?? '']));
      if (!record.order_id) continue;
      const list = byOrder.get(record.order_id) ?? [];
      list.push(record);
      byOrder.set(record.order_id, list);
    }

    // The cancelled intent: recorded, with no fill leg.
    const cancelled = byOrder.get(journey.cancelledOrderId) ?? [];
    expect(cancelled.length, 'a user-cancelled order must still appear in the journal').toBeGreaterThan(0);
    expect(cancelled.some((r) => r.order_status === 'cancelled'), 'and must read as cancelled').toBe(true);
    expect(cancelled.every((r) => !r.fill_id && !r.execution_date), 'and must carry no fill').toBe(true);

    // The expired intent: recorded apart from the user-cancelled one.
    const expired = byOrder.get(journey.expiredOrderId) ?? [];
    expect(expired.length, 'the expired order must appear, not vanish').toBeGreaterThan(0);
    expect(expired.some((r) => r.outcome_status === 'expired'), 'and be labelled expired').toBe(true);

    // The filled round: the exporter writes one row PER FILL and merges the order columns
    // into it (its own semantics: 'fill' = an executed fill, 'order' = an order that never
    // filled). So exactly one row may carry this order — a duplicate row would be a
    // duplicate entry in the books.
    const filled = byOrder.get(journey.filledOrderId) ?? [];
    expect(filled.length, '一筆成交在日誌裡只能留一列：重複行列就是重複入帳').toBe(1);
    const fillLeg = filled[0];
    expect(fillLeg.record_type, '已成交的意圖必須以成交列出現，不能退成一張掛單').toBe('fill');

    // The order half of that row.
    expect(fillLeg.order_status).toBe('filled');
    expect(fillLeg.actor_type, '人工單在導出裡必須仍是 human').toBe('human');
    expect(fillLeg.side).toBe('BUY');
    expect(fillLeg.asset_id).toBe(ASSET);
    expect(fillLeg.decision_id, '每一列都要能回溯到那一次的決策').not.toBe('');
    expect(fillLeg.rationale, '使用者寫的理由必須跟著導出走').toBe(journey.filledRationale);
    expect(fillLeg.settlement_policy, 'settlement policy must travel with the row').toContain('T+1');
    expect(fillLeg.order_created_at_utc, '下單時間 must be its own column, apart from 決策基準')
      .toMatch(/\d{4}-\d{2}-\d{2}T/);
    expect(fillLeg.data_cutoff_at, '決策基準就是掛單自己記下的 cutoff').toBeTruthy();

    // The fill half of the same row: the stored numbers the UI shows.
    expect(fillLeg.fill_id).toBe(journey.fill!.id);
    expect(fillLeg.execution_date).toBe(journey.windowDate);
    expect(fillLeg.settlement_date, 'the exported settlement date must be the one the panel named')
      .toBe((await claimedSettlementFromCsv(filled)).settlement);
    expect(Number(fillLeg.observed_price)).toBeCloseTo(journey.fill!.observed_price, 6);
    expect(Number(fillLeg.execution_price_before_costs)).toBeCloseTo(journey.fill!.execution_price_before_costs, 6);
    expect(Number(fillLeg.commission_amount)).toBeCloseTo(journey.fill!.commission_amount, 6);
    expect(Number(fillLeg.slippage_amount)).toBeCloseTo(journey.fill!.slippage_amount, 6);
    expect(Number(fillLeg.net_cash_change)).toBeCloseTo(journey.fill!.net_cash_change, 6);
    // The merged row overwrites quantity with the filled quantity, so on a fully filled
    // order it must still equal what was asked for — nothing more, nothing less.
    expect(Number(fillLeg.quantity)).toBeCloseTo(journey.fill!.quantity, 6);
    expect(Number(fillLeg.quantity)).toBeCloseTo(BUY_QTY, 6);
    // The export's own column semantics say bar_timestamp is 「Empty for daily fills, which
    // carry no intraday bar time」 — the same NULL proven from the stored row in test 6.
    // For a 1d fill the trading day travels in execution_date, so that column must carry it.
    expect(fillLeg.bar_timestamp, '日線成交不得虛構一個 bar 時間戳').toBe('');
    expect(fillLeg.execution_date, '日線的成交日期就是 execution_date 這一欄').toBe(journey.windowDate);

    // The cash identity across the whole journal: only the one fill moved money.
    const cashRows = rows
      .slice(1)
      .map((r) => Object.fromEntries(header.map((col, i) => [col, r[i] ?? ''])))
      .filter((r) => r.record_type === 'fill');
    expect(cashRows.length, '整份日誌只能有一列是成交').toBe(1);
    const netSum = cashRows.reduce((acc, r) => acc + Number(r.net_cash_change), 0);
    expect(round4(SESSION_CASH + netSum)).toBeCloseTo(journey.filledCash, 4);
  });
});

/** Fill the settlement claim out of the exported rows rather than the live panel, so the
 * CSV is compared against its own clock, not a panel state that has since advanced. */
async function claimedSettlementFromCsv(rows: Record<string, string>[]): Promise<{ settlement: string }> {
  const fillRow = rows.find((r) => r.record_type === 'fill') ?? rows[0];
  expect(fillRow.settlement_date, 'the fill leg must name a settlement date').toBeTruthy();
  return { settlement: fillRow.settlement_date };
}

/** Minimal RFC-4180 splitter: the journal quotes any field containing a comma. */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else quoted = false;
      } else field += c;
      continue;
    }
    if (c === '"') quoted = true;
    else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else if (c !== '\r') field += c;
  }
  if (field !== '' || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((cell) => cell !== ''));
}
