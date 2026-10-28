import { test, expect, type BrowserContext, type Page, type Response } from '@playwright/test';
import { randomBytes } from 'crypto';

/**
 * Issue #26 §4: the paper-trading safety matrix, driven through the real UI.
 *
 * Every user-facing step below is a genuine click / fill / select on the rendered
 * control on 127.0.0.1:3099; the HTTP payloads are read only to cross-check what
 * the screen claims against what was persisted in PostgreSQL on 127.0.0.1:8099.
 * Waits are waitForResponse / waitForURL / expect.poll conditions, never sleeps,
 * and every page opened here is closed on both the success and the failure path.
 *
 * Two things are exercised at the API layer and are labelled as such in their test
 * titles, because the UI deliberately offers no control for them (§3 allows API
 * checks as a supplement, never as a substitute for a browser flow):
 *   - order_type: the form has no order-type field at all;
 *   - idempotency-key conflict: the form mints a fresh key whenever a field changes,
 *     so a reused key with different contents cannot be produced by clicking.
 *
 * One route interception is used in the idempotency test. It performs the real
 * upstream request and then hands the browser a 502, i.e. it simulates a lost
 * response after the server already acted - the click itself is still a real click.
 *
 * All prices come from the built-in synthetic demo fixture (DEMO-*, last bar
 * 2024-02-13), so nothing here is evidence about live market data. Expected
 * numbers are recomputed from the stored fill's own observed price rather than
 * hardcoded, except where a test needs to size an order before any fill exists.
 */

const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';
const BACKEND_BASE = process.env.E2E_BACKEND_BASE ?? 'http://localhost:8099';

const ASSET = 'DEMO-ALPHA';
const SLIPPAGE = 0.001;
const COMMISSION = 0.001;
const STARTING_CASH = 100000;

const account = {
  email: `e2e-paper-${randomBytes(6).toString('hex')}@alethra.io`,
  password: `E2e${randomBytes(9).toString('base64url')}!7`,
  displayName: `E2E 模擬下單 ${randomBytes(3).toString('hex')}`,
};

interface Fill {
  observed_price: number;
  execution_price_before_costs: number;
  slippage_amount: number;
  commission_amount: number;
  quantity: number;
  gross_amount: number;
  net_cash_change: number;
}

interface OrderRecord {
  id: string;
  asset_id: string;
  side: string;
  quantity: number;
  status: string;
  risk_check_status?: string;
}

interface PositionRecord {
  id: string;
  asset_id: string;
  quantity: number;
  average_entry_price: number;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  status: string;
}

const round4 = (value: number) => Math.round(value * 10000) / 10000;

async function sessionToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem('token'));
  if (!token) throw new Error('the UI session holds no token; login did not persist');
  return token;
}

/** API cross-check helper. Supplements the UI assertions; never replaces a click. */
async function api(
  page: Page,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown
): Promise<{ status: number; json: any }> {
  const token = await sessionToken(page);
  const response = await page.request.fetch(`${BACKEND_BASE}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status(), json: text ? JSON.parse(text) : null };
}

async function createPortfolio(page: Page, cash: number): Promise<string> {
  await page.goto(`${FRONTEND_BASE}/paper-portfolio`);
  await page.getByTestId('pp-new-cash').fill(String(cash));
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/v1/paper-portfolio') &&
        response.request().method() === 'POST'
    ),
    page.getByTestId('pp-create-portfolio').click(),
  ]);
  await page.waitForURL(/\/paper-portfolio\/[0-9a-f-]{36}$/);
  const id = page.url().split('/paper-portfolio/')[1];
  await expect(page.getByTestId('pp-cash')).toBeVisible();
  return id;
}

async function fillOrderForm(
  page: Page,
  opts: { asset?: string; side: 'BUY' | 'SELL'; quantity: number; note: string }
) {
  await page.getByTestId('pp-asset').selectOption(opts.asset ?? ASSET);
  await page.getByTestId('pp-side').selectOption(opts.side);
  await page.getByTestId('pp-quantity').fill(String(opts.quantity));
  await page.getByTestId('pp-rationale').fill(`${opts.note} — 理由`);
  await page.getByTestId('pp-expected-outcome').fill(`${opts.note} — 預期結果`);
}

async function submitOrder(
  page: Page,
  portfolioId: string,
  opts: { asset?: string; side: 'BUY' | 'SELL'; quantity: number; note: string }
): Promise<{ status: number; body: any; sentIdempotencyKey: string | null }> {
  await fillOrderForm(page, opts);
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.url().includes(`/api/v1/paper-portfolio/${portfolioId}/orders`) &&
        r.request().method() === 'POST'
    ),
    page.getByTestId('pp-submit-order').click(),
  ]);
  const sent = response.request().postDataJSON() as { idempotency_key?: string };
  const body = await response.json().catch(() => null);
  return { status: response.status(), body, sentIdempotencyKey: sent.idempotency_key ?? null };
}

/** Reads the rendered cash figure; the UI rounds to 2 decimals, so compare with a cent tolerance. */
async function readCash(page: Page): Promise<number> {
  const text = (await page.getByTestId('pp-cash').innerText()).replace(/,/g, '');
  const match = text.match(/-?\d+(?:\.\d+)?\s*$/);
  if (!match) throw new Error(`could not read a cash figure from "${text}"`);
  return Number(match[0]);
}

async function storedOrders(page: Page, portfolioId: string): Promise<OrderRecord[]> {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/orders`);
  expect(status, 'the order list must be readable').toBe(200);
  return json as OrderRecord[];
}

async function storedPositions(page: Page, portfolioId: string): Promise<PositionRecord[]> {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/positions`);
  expect(status).toBe(200);
  return json as PositionRecord[];
}

async function storedPortfolio(page: Page, portfolioId: string): Promise<any> {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}`);
  expect(status).toBe(200);
  return json;
}

async function fillsFor(page: Page, portfolioId: string, orderId: string): Promise<Fill[]> {
  const { status, json } = await api(
    page,
    'GET',
    `/api/v1/paper-portfolio/${portfolioId}/orders/${orderId}/fills`
  );
  expect(status).toBe(200);
  return json as Fill[];
}

/** Recomputes a fill's economics from its own observed price. */
function expectedFill(side: 'BUY' | 'SELL', quantity: number, observed: number) {
  const exec = round4(observed * (side === 'BUY' ? 1 + SLIPPAGE : 1 - SLIPPAGE));
  const gross = round4(quantity * exec);
  const commission = round4(gross * COMMISSION);
  const net = side === 'BUY' ? -(gross + commission) : gross - commission;
  return { exec, gross, commission, net };
}

test.describe('paper order safety matrix', () => {
  test.describe.configure({ mode: 'serial' });

  let context: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    // An explicit context (not browser.newPage()) so a second tab can be opened
    // inside it, sharing the logged-in session.
    context = await browser.newContext();
    page = await context.newPage();
    await page.goto(`${FRONTEND_BASE}/login`);
    await page.getByRole('button', { name: '沒有帳號？註冊' }).click();
    await page.locator('input[type="text"]').first().fill(account.displayName);
    await page.locator('input[type="email"]').fill(account.email);
    await page.locator('input[type="password"]').fill(account.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test.afterAll(async () => {
    // Closing the context releases every page opened in it, including any tab a
    // failed test did not reach its own cleanup for.
    if (context) await context.close();
  });

  test('a filled BUY moves cash and position by exactly the stored fill, with commission and slippage charged once', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    const { status, body } = await submitOrder(page, portfolioId, {
      side: 'BUY',
      quantity: 10,
      note: '正常買入',
    });
    expect(status, 'the order must be accepted').toBe(201);
    expect(body.status, 'the paper executor fills synchronously').toBe('filled');

    const fills = await fillsFor(page, portfolioId, body.id);
    expect(fills.length, 'one filled order carries exactly one fill').toBe(1);
    const fill = fills[0];
    const expected = expectedFill('BUY', 10, fill.observed_price);

    expect(fill.execution_price_before_costs, 'fill price = observed open + slippage').toBeCloseTo(expected.exec, 6);
    expect(fill.gross_amount).toBeCloseTo(expected.gross, 6);
    expect(fill.commission_amount, 'commission is charged once, at the configured rate').toBeCloseTo(expected.commission, 6);
    expect(fill.net_cash_change, 'cash leg = gross + commission, taken once').toBeCloseTo(expected.net, 6);
    expect(fill.slippage_amount).toBeCloseTo(round4(Math.abs(expected.exec - fill.observed_price) * 10), 6);

    // What the screen shows must equal what was persisted.
    await expect
      .poll(() => readCash(page), { message: 'cash must fall by exactly the stored cash leg' })
      .toBeCloseTo(round4(STARTING_CASH + expected.net), 2);
    await expect(page.getByTestId('pp-open-position-count')).toHaveText('1');
    await expect(page.getByTestId(`pp-open-position-qty-${ASSET}`)).toHaveText('10');

    const positions = await storedPositions(page, portfolioId);
    expect(positions.length).toBe(1);
    expect(positions[0].average_entry_price, 'entry price is the slipped execution price').toBeCloseTo(expected.exec, 6);
    expect(positions[0].status).toBe('open');

    const unrealizedCell = page.getByTestId(`pp-open-position-unrealized-${ASSET}`);
    await expect(unrealizedCell).toHaveText((positions[0].unrealized_pnl as number).toFixed(2));
    expect(positions[0].unrealized_pnl, 'an open position must be marked, not left null').not.toBeNull();
    expect(
      positions[0].unrealized_pnl as number,
      'and the mark must be a real number for this fixture, not a fabricated zero'
    ).not.toBe(0);

    const portfolio = await storedPortfolio(page, portfolioId);
    expect(portfolio.total_unrealized_pnl).toBeCloseTo(positions[0].unrealized_pnl as number, 6);
    expect(portfolio.total_realized_pnl, 'nothing has been sold yet').toBe(0);

    // The valuation must disclose that it is a synthetic fixture, not a live quote.
    await expect(page.getByTestId('pp-valuation-basis')).toBeVisible();
    await expect(page.getByTestId('pp-valuation-provider')).toHaveText('Demo (Synthetic)');
    await expect(page.getByTestId('pp-valuation-mode')).toHaveText('合成數據');
    await expect(page.getByTestId('pp-valuation-as-of')).toHaveText(/^\d{4}-\d{2}-\d{2}$/);
    await expect(page.getByTestId('pp-valuation-basis')).toContainText('非即時行情');
    await expect(page.getByText('免責聲明：').first()).toBeVisible();

    // The order and its decision record must both exist: order -> fill -> decision -> cash.
    await page.getByTestId('pp-tab-orders').click();
    await expect(page.getByTestId(`pp-order-${ASSET}-filled`)).toHaveCount(1);
    await expect(page.getByTestId(`pp-order-${ASSET}-filled`).getByTestId('pp-order-side')).toHaveText('買入');
    await expect(page.getByTestId(`pp-order-${ASSET}-filled`).getByTestId('pp-order-qty')).toHaveText('10');
    await expect(page.getByTestId(`pp-order-${ASSET}-filled`).getByTestId('pp-order-status')).toHaveText('已成交');

    const decisions = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/decisions`);
    expect(decisions.status).toBe(200);
    expect(decisions.json.length, 'a filled order records exactly one human decision').toBe(1);
    expect(decisions.json[0].decision_type).toBe('entry');
  });

  test('an order that exceeds available cash is rejected and leaves no order, no fill and no half-updated state', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    // A one-share probe gives the fixture price without hardcoding it.
    const probe = await submitOrder(page, portfolioId, { side: 'BUY', quantity: 1, note: '探測價格' });
    expect(probe.status).toBe(201);
    const probeFill = (await fillsFor(page, portfolioId, probe.body.id))[0];
    const price = probeFill.observed_price;

    // Step 1: spend most of the cash, staying inside the position cap.
    const bigQty = Math.floor((0.8 * STARTING_CASH) / price);
    const big = await submitOrder(page, portfolioId, { side: 'BUY', quantity: bigQty, note: '大额買入' });
    expect(big.status, 'the large order must still be affordable').toBe(201);
    const bigFill = (await fillsFor(page, portfolioId, big.body.id))[0];
    const cashAfterBig = round4(STARTING_CASH + probeFill.net_cash_change + bigFill.net_cash_change);
    await expect.poll(() => readCash(page)).toBeCloseTo(cashAfterBig, 2);

    // Step 2: an order worth half the portfolio passes the position cap but not the cash check.
    const overQty = Math.floor((0.5 * STARTING_CASH) / price);
    const over = await submitOrder(page, portfolioId, { side: 'BUY', quantity: overQty, note: '超過現金' });
    expect(over.status, 'the unaffordable order must be refused').toBe(400);
    expect(over.body.detail, 'and must say why').toBe('Insufficient cash for order');
    await expect(page.getByTestId('pp-error')).toContainText('Insufficient cash for order');

    // No half state: cash, position, order list, fills and decisions all unchanged.
    await expect.poll(() => readCash(page), { message: 'a refused order must not move cash' }).toBeCloseTo(cashAfterBig, 2);
    const positions = await storedPositions(page, portfolioId);
    expect(positions.length, 'no second position may appear').toBe(1);
    expect(positions[0].quantity).toBeCloseTo(1 + bigQty, 6);

    const orders = await storedOrders(page, portfolioId);
    expect(orders.length, 'the rolled-back order must not be persisted').toBe(2);
    expect(orders.every((o) => o.status === 'filled'), 'both persisted orders are the earlier fills').toBe(true);

    const decisions = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/decisions`);
    expect(decisions.json.length, 'the rolled-back decision record must not survive either').toBe(2);

    const portfolio = await storedPortfolio(page, portfolioId);
    expect(portfolio.current_virtual_cash).toBeCloseTo(cashAfterBig, 4);
    expect(portfolio.current_virtual_cash, 'cash can never go negative').toBeGreaterThan(0);
  });

  test('overselling is refused rather than turned into a short sale, and the form offers no short option', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    // Short selling must be unavailable in the UI itself, not merely rejected later.
    const sideOptions = page.getByTestId('pp-side').locator('option');
    await expect(sideOptions).toHaveCount(2);
    expect(await sideOptions.nth(0).getAttribute('value')).toBe('BUY');
    expect(await sideOptions.nth(1).getAttribute('value')).toBe('SELL');
    await expect(page.getByTestId('pp-side')).toContainText('買入');
    await expect(page.getByTestId('pp-side')).not.toContainText(/借券|放空|做空|SHORT/i);

    const buy = await submitOrder(page, portfolioId, { side: 'BUY', quantity: 5, note: '建立部位' });
    expect(buy.status).toBe(201);
    const buyFill = (await fillsFor(page, portfolioId, buy.body.id))[0];
    const cashAfterBuy = round4(STARTING_CASH + buyFill.net_cash_change);
    await expect.poll(() => readCash(page)).toBeCloseTo(cashAfterBuy, 2);

    const oversell = await submitOrder(page, portfolioId, { side: 'SELL', quantity: 6, note: '超額賣出' });
    expect(oversell.status, 'selling more than held must be refused').toBe(400);
    expect(String(oversell.body.detail)).toMatch(
      /Risk check failed \[quantity_exceeds_position\]: 帳戶僅持有 5 股，不能賣出 6 股。/
    );
    await expect(page.getByTestId('pp-error')).toContainText('quantity_exceeds_position');

    await expect.poll(() => readCash(page), { message: 'a refused sell must not credit cash' }).toBeCloseTo(cashAfterBuy, 2);
    const positions = await storedPositions(page, portfolioId);
    expect(positions.length).toBe(1);
    expect(positions[0].quantity, 'the holding must be untouched').toBe(5);
    expect(positions[0].status).toBe('open');

    const orders = await storedOrders(page, portfolioId);
    expect(orders.length, 'the refusal is recorded as a rejected order').toBe(2);
    const rejected = orders.find((o) => o.status === 'rejected');
    expect(rejected, 'a rejected order row must exist').toBeDefined();
    expect(rejected!.side).toBe('SELL');
    expect(rejected!.quantity).toBe(6);
    expect(rejected!.risk_check_status).toBe('failed');
    const rejectedFills = await fillsFor(page, portfolioId, rejected!.id);
    expect(rejectedFills.length, 'a rejected order must not produce a fill').toBe(0);

    await page.getByTestId('pp-tab-orders').click();
    await expect(page.getByTestId(`pp-order-${ASSET}-rejected`)).toHaveCount(1);
    await expect(page.getByTestId(`pp-order-${ASSET}-rejected`).getByTestId('pp-order-status')).toHaveText('已拒絕');
    await expect(page.getByTestId(`pp-order-${ASSET}-filled`)).toHaveCount(1);
  });

  test('a lost response followed by a retry reuses the same idempotency key and fills the order exactly once', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    let serverOrderId = '';
    let firstKey = '';
    // Simulate the response being lost after the server already processed the order.
    // The click is still a real click; only the browser's view of the result is broken.
    await page.route(`**/api/v1/paper-portfolio/${portfolioId}/orders`, async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const upstream = await route.fetch();
      const payload = (await upstream.json()) as { id: string };
      serverOrderId = payload.id;
      firstKey = (route.request().postDataJSON() as { idempotency_key: string }).idempotency_key;
      await route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'upstream response lost (simulated)' }),
      });
    });

    await fillOrderForm(page, { side: 'BUY', quantity: 7, note: '回應遺失' });
    await page.getByTestId('pp-submit-order').click();
    await expect(page.getByTestId('pp-error')).toContainText('upstream response lost');

    const ordersAfterLoss = await storedOrders(page, portfolioId);
    expect(ordersAfterLoss.length, 'the server really did create the order').toBe(1);
    expect(ordersAfterLoss[0].id).toBe(serverOrderId);
    expect(ordersAfterLoss[0].status).toBe('filled');
    expect(firstKey, 'the first attempt must have carried an idempotency key').not.toBe('');

    await page.unroute(`**/api/v1/paper-portfolio/${portfolioId}/orders`);

    // Retry the very same submission without touching a single field.
    const [retryResponse] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.url().includes(`/api/v1/paper-portfolio/${portfolioId}/orders`) &&
          r.request().method() === 'POST'
      ),
      page.getByTestId('pp-submit-order').click(),
    ]);
    const retryKey = (retryResponse.request().postDataJSON() as { idempotency_key: string }).idempotency_key;
    expect(retryKey, 'the retry must reuse the key of the attempt the user is repeating').toBe(firstKey);
    expect(retryResponse.status(), 'a replayed key returns the stored order, not a new one').toBe(200);
    const retryBody = await retryResponse.json();
    expect(retryBody.id, 'the same order comes back').toBe(serverOrderId);

    const fills = await fillsFor(page, portfolioId, serverOrderId);
    expect(fills.length, 'still exactly one fill').toBe(1);
    const expected = expectedFill('BUY', 7, fills[0].observed_price);
    await expect
      .poll(() => readCash(page), { message: 'cash must be charged once, not twice' })
      .toBeCloseTo(round4(STARTING_CASH + expected.net), 2);
    await expect(page.getByTestId(`pp-open-position-qty-${ASSET}`)).toHaveText('7');

    const orders = await storedOrders(page, portfolioId);
    expect(orders.length, 'no duplicate order was created').toBe(1);
    const portfolio = await storedPortfolio(page, portfolioId);
    expect(portfolio.current_virtual_cash).toBeCloseTo(round4(STARTING_CASH + expected.net), 4);
  });

  test('the submit button is disabled until the journal fields a decision needs are filled in', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);
    await page.getByTestId('pp-asset').selectOption(ASSET);
    await page.getByTestId('pp-side').selectOption('BUY');
    await page.getByTestId('pp-quantity').fill('3');
    await expect(page.getByTestId('pp-submit-order'), 'no rationale, no order').toBeDisabled();
    await page.getByTestId('pp-rationale').fill('只有理由');
    await expect(page.getByTestId('pp-submit-order'), 'no expected outcome, no order').toBeDisabled();
    await page.getByTestId('pp-expected-outcome').fill('加上預期結果');
    await expect(page.getByTestId('pp-submit-order')).toBeEnabled();

    const orders = await storedOrders(page, portfolioId);
    expect(orders.length, 'nothing was submitted while the form was incomplete').toBe(0);
  });

  test('two concurrent submissions from two tabs cannot overspend the portfolio', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    const probe = await submitOrder(page, portfolioId, { side: 'BUY', quantity: 1, note: '探測價格' });
    expect(probe.status).toBe(201);
    const price = (await fillsFor(page, portfolioId, probe.body.id))[0].observed_price;
    const cashNow = (await storedPortfolio(page, portfolioId)).current_virtual_cash as number;

    // Each order alone is affordable at 60% of cash; together they are not.
    const qty = Math.floor((0.6 * cashNow) / price);
    expect(qty, 'the concurrent orders must be large enough to matter').toBeGreaterThan(0);

    const second: Page = await context.newPage();
    try {
      await second.goto(`${FRONTEND_BASE}/paper-portfolio/${portfolioId}`);
      await expect(second.getByTestId('pp-submit-order')).toBeVisible();
      await fillOrderForm(second, { side: 'BUY', quantity: qty, note: '併發下單 B' });
      await fillOrderForm(page, { side: 'BUY', quantity: qty, note: '併發下單 A' });

      const urlPattern = `/api/v1/paper-portfolio/${portfolioId}/orders`;
      const [responseA, responseB] = await Promise.all([
        page.waitForResponse((r) => r.url().includes(urlPattern) && r.request().method() === 'POST'),
        second.waitForResponse((r) => r.url().includes(urlPattern) && r.request().method() === 'POST'),
        page.getByTestId('pp-submit-order').click(),
        second.getByTestId('pp-submit-order').click(),
      ]);

      const statuses = [responseA.status(), responseB.status()].sort();
      expect(statuses, 'exactly one of the two competing orders may fill').toEqual([201, 400]);

      const loser = responseA.status() === 400 ? await responseA.json() : await responseB.json();
      expect(loser.detail, 'the loser must be refused for lack of cash, not silently queued').toBe(
        'Insufficient cash for order'
      );

      const orders = await storedOrders(page, portfolioId);
      const filled = orders.filter((o) => o.status === 'filled');
      expect(filled.length, 'probe plus one winning order').toBe(2);
      const winner = filled.find((o) => o.quantity === qty)!;
      expect(winner).toBeDefined();

      const winnerFill = (await fillsFor(page, portfolioId, winner.id))[0];
      const probeFill = (await fillsFor(page, portfolioId, probe.body.id))[0];
      const expectedCash = round4(STARTING_CASH + probeFill.net_cash_change + winnerFill.net_cash_change);

      const portfolio = await storedPortfolio(page, portfolioId);
      expect(portfolio.current_virtual_cash, 'no overspend: cash equals the two real fills').toBeCloseTo(expectedCash, 4);
      expect(portfolio.current_virtual_cash).toBeGreaterThan(0);

      const positions = await storedPositions(page, portfolioId);
      expect(positions.length).toBe(1);
      expect(positions[0].quantity, 'the winning quantity is booked once').toBeCloseTo(1 + qty, 6);

      await expect.poll(() => readCash(page)).toBeCloseTo(expectedCash, 2);
    } finally {
      await second.close();
    }
  });

  test('a paused portfolio refuses orders through the UI, and retiring it removes the order form', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    const [pauseResponse] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes(`/api/v1/paper-portfolio/${portfolioId}/pause`) && r.request().method() === 'POST'
      ),
      page.getByRole('button', { name: '暫停' }).click(),
    ]);
    expect(pauseResponse.status()).toBe(200);
    await expect(page.getByText('已暫停').first()).toBeVisible();

    const paused = await submitOrder(page, portfolioId, { side: 'BUY', quantity: 2, note: '暫停期間下單' });
    expect(paused.status, 'a paused portfolio must not accept orders').toBe(400);
    expect(String(paused.body.detail)).toMatch(/Risk check failed \[portfolio_paused\]/);
    await expect(page.getByTestId('pp-error')).toContainText('portfolio_paused');
    await expect.poll(() => readCash(page), { message: 'a paused portfolio must not move cash' }).toBeCloseTo(STARTING_CASH, 2);

    const rejected = (await storedOrders(page, portfolioId)).filter((o) => o.status === 'rejected');
    expect(rejected.length, 'the refusal is recorded').toBe(1);

    const [resumeResponse] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes(`/api/v1/paper-portfolio/${portfolioId}/resume`) && r.request().method() === 'POST'
      ),
      page.getByRole('button', { name: '恢復' }).click(),
    ]);
    expect(resumeResponse.status()).toBe(200);
    await expect(page.getByText('進行中').first()).toBeVisible();

    const resumed = await submitOrder(page, portfolioId, { side: 'BUY', quantity: 2, note: '恢復後下單' });
    expect(resumed.status, 'resuming must restore trading').toBe(201);

    page.once('dialog', (dialog) => dialog.accept());
    const [retireResponse] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes(`/api/v1/paper-portfolio/${portfolioId}/retire`) && r.request().method() === 'POST'
      ),
      page.getByRole('button', { name: '退休' }).click(),
    ]);
    expect(retireResponse.status()).toBe(200);
    await expect(page.getByText('已退休').first()).toBeVisible();
    await expect(page.getByTestId('pp-submit-order'), 'a retired portfolio offers no order form').toHaveCount(0);

    // API supplement: the retired guard is enforced server-side too, not only hidden in the UI.
    const afterRetire = await api(page, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, {
      asset_id: ASSET,
      side: 'BUY',
      quantity: 1,
      rationale: 'retired guard',
      expected_outcome: 'must be refused',
      self_reported_confidence: 'medium',
    });
    expect(afterRetire.status).toBe(400);
    expect(String(afterRetire.json.detail)).toMatch(/Cannot create order for 'retired' portfolio/);
  });

  test('the daily entry budget refuses the eleventh same-day entry through the UI instead of queuing it', async () => {
    // Eleven real clicks plus their cross-checks do not fit the 30s default.
    test.setTimeout(180_000);
    // Mirrors RiskPolicy.max_daily_entries (backend/app/services/risk_policy.py).
    const DAILY_ENTRY_BUDGET = 10;
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    const accepted: string[] = [];
    for (let i = 0; i < DAILY_ENTRY_BUDGET; i += 1) {
      const { status, body } = await submitOrder(page, portfolioId, {
        side: 'BUY',
        quantity: 1,
        note: `進場 ${i + 1}`,
      });
      expect(status, `entry ${i + 1} of ${DAILY_ENTRY_BUDGET} must be accepted`).toBe(201);
      expect(body.status, 'each accepted entry fills on submit').toBe('filled');
      accepted.push(body.id);
      // The page clears the journal fields after the post-submit reload; typing
      // the next entry before that lands would race the reset.
      await expect(page.getByTestId('pp-rationale')).toHaveValue('');
    }

    let cashLegs = 0;
    for (const id of accepted) {
      const fills = await fillsFor(page, portfolioId, id);
      expect(fills.length, `order ${id} carries exactly one fill`).toBe(1);
      cashLegs += fills[0].net_cash_change;
    }
    const cashAfterBudget = round4(STARTING_CASH + cashLegs);
    await expect.poll(() => readCash(page)).toBeCloseTo(cashAfterBudget, 2);

    // The eleventh entry of the same UTC day is refused at submit time.
    const refused = await submitOrder(page, portfolioId, { side: 'BUY', quantity: 1, note: '超出每日額度' });
    expect(refused.status, 'an entry over the daily budget must be refused').toBe(400);
    expect(String(refused.body.detail)).toMatch(
      /^Risk check failed \[daily_entry_limit\]: .*進場次數已達上限 10 次/
    );
    await expect(page.getByTestId('pp-error')).toContainText('daily_entry_limit');
    await expect(page.getByTestId('pp-error')).toContainText('進場次數已達上限 10 次');

    // No executable intent: nothing is pending, nothing fills, cash and holdings do not move.
    const orders = await storedOrders(page, portfolioId);
    expect(orders.length, 'ten fills plus one rejected row, and no queued order').toBe(DAILY_ENTRY_BUDGET + 1);
    expect(orders.filter((o) => o.status === 'pending'), 'a refused entry must not be left queued').toEqual([]);
    expect(orders.filter((o) => o.status === 'filled').length).toBe(DAILY_ENTRY_BUDGET);
    const rejected = orders.filter((o) => o.status === 'rejected');
    expect(rejected.length).toBe(1);
    expect(rejected[0].side).toBe('BUY');
    expect(rejected[0].quantity).toBe(1);
    expect(rejected[0].risk_check_status, 'the refusal is labelled failed, never passed').toBe('failed');
    expect((await fillsFor(page, portfolioId, rejected[0].id)).length, 'a refused entry must not fill').toBe(0);

    await expect
      .poll(() => readCash(page), { message: 'a refused entry must not move cash' })
      .toBeCloseTo(cashAfterBudget, 2);
    const portfolio = await storedPortfolio(page, portfolioId);
    expect(portfolio.current_virtual_cash).toBeCloseTo(cashAfterBudget, 4);

    const positions = await storedPositions(page, portfolioId);
    expect(positions.length).toBe(1);
    expect(positions[0].quantity, 'only the ten executed entries are held').toBe(DAILY_ENTRY_BUDGET);

    await page.getByTestId('pp-tab-orders').click();
    await expect(page.getByTestId(`pp-order-${ASSET}-filled`)).toHaveCount(DAILY_ENTRY_BUDGET);
    await expect(page.getByTestId(`pp-order-${ASSET}-rejected`)).toHaveCount(1);
    await expect(page.getByTestId(`pp-order-${ASSET}-rejected`).getByTestId('pp-order-status')).toHaveText('已拒絕');

    // The screen's refusal is the server's decision: this id is the row the audit query follows.
    console.log(
      `DAILY_LIMIT_EVIDENCE portfolio=${portfolioId} rejected_order=${rejected[0].id} accepted=${accepted.length} code=daily_entry_limit`
    );
  });

  test('partial and full exits keep order, fill, position, cash and P&L consistent', async () => {
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    const buy = await submitOrder(page, portfolioId, { side: 'BUY', quantity: 10, note: '建立部位' });
    expect(buy.status).toBe(201);
    const buyFill = (await fillsFor(page, portfolioId, buy.body.id))[0];
    const entry = buyFill.execution_price_before_costs;
    // Realised P&L is booked NET of commission: each exit carries its own commission
    // plus the pro-rated share of the entry commission. Deriving the per-share entry
    // commission from the recorded entry fill keeps the expectation tied to the ledger
    // rather than re-deriving the rate, so the identity below still cross-checks cash.
    const entryCommissionPerShare = buyFill.commission_amount / buyFill.quantity;

    const partial = await submitOrder(page, portfolioId, { side: 'SELL', quantity: 4, note: '部分出場' });
    expect(partial.status).toBe(201);
    const partialFill = (await fillsFor(page, portfolioId, partial.body.id))[0];
    const expectedPartialPnl = round4(
      (partialFill.execution_price_before_costs - entry) * 4
        - partialFill.commission_amount
        - entryCommissionPerShare * 4
    );

    await expect(page.getByTestId(`pp-open-position-qty-${ASSET}`), 'six shares remain open').toHaveText('6');
    await expect(page.getByTestId('pp-open-position-count')).toHaveText('1');
    const afterPartial = await storedPositions(page, portfolioId);
    expect(afterPartial[0].status, 'a partial exit does not close the position').toBe('open');
    expect(afterPartial[0].realized_pnl, 'the shares that left must book realised P&L').not.toBeNull();
    expect(afterPartial[0].realized_pnl as number).toBeCloseTo(expectedPartialPnl, 6);
    await expect(page.getByTestId('pp-realized-pnl')).toContainText((expectedPartialPnl as number).toFixed(2));

    const full = await submitOrder(page, portfolioId, { side: 'SELL', quantity: 6, note: '全部出場' });
    expect(full.status).toBe(201);
    const fullFill = (await fillsFor(page, portfolioId, full.body.id))[0];
    const expectedTotalPnl = round4(
      expectedPartialPnl
        + round4(
            (fullFill.execution_price_before_costs - entry) * 6
              - fullFill.commission_amount
              - entryCommissionPerShare * 6
          )
    );

    await expect(page.getByTestId('pp-open-position-count'), 'the position is gone from the open list').toHaveText('0');
    await expect(page.getByText('尚無持有部位')).toBeVisible();

    const closed = await storedPositions(page, portfolioId);
    expect(closed[0].status).toBe('closed');
    expect(closed[0].quantity).toBe(0);
    expect(closed[0].realized_pnl as number, 'both exit legs are in the record').toBeCloseTo(expectedTotalPnl, 6);
    expect(closed[0].unrealized_pnl, 'a closed position has no open mark').toBeNull();

    // Cash identity across every fill: nothing double-charged, nothing leaked.
    const orders = await storedOrders(page, portfolioId);
    expect(orders.length).toBe(3);
    let cashLegs = 0;
    for (const order of orders) {
      const fills = await fillsFor(page, portfolioId, order.id);
      expect(fills.length, `order ${order.id} carries one fill`).toBe(1);
      cashLegs += fills[0].net_cash_change;
    }
    const portfolio = await storedPortfolio(page, portfolioId);
    expect(portfolio.current_virtual_cash).toBeCloseTo(round4(STARTING_CASH + cashLegs), 4);
    expect(portfolio.total_realized_pnl).toBeCloseTo(expectedTotalPnl, 6);
    // Independent identity: once the round trip is fully closed the booked realised
    // P&L equals the net cash movement, so no commission or slippage was double-charged.
    expect(portfolio.total_realized_pnl).toBeCloseTo(round4(cashLegs), 6);
    await expect.poll(() => readCash(page)).toBeCloseTo(round4(STARTING_CASH + cashLegs), 2);

    // The result is whatever the fixture produces, including a loss - never a staged profit.
    await page.getByTestId('pp-tab-orders').click();
    await expect(page.getByTestId(`pp-order-${ASSET}-filled`)).toHaveCount(3);
  });

  test('[API supplement — not a browser flow] an unsupported order_type is refused instead of being filled as a market order, and a reused key with different contents conflicts', async () => {
    // The order form exposes no order-type control and mints a fresh idempotency key
    // whenever a field changes, so these two guards cannot be reached by clicking.
    const portfolioId = await createPortfolio(page, STARTING_CASH);

    const limit = await api(page, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, {
      asset_id: ASSET,
      side: 'BUY',
      quantity: 3,
      order_type: 'limit',
      rationale: 'limit order probe',
      expected_outcome: 'must be refused',
      self_reported_confidence: 'medium',
    });
    expect(limit.status, 'a limit order must not be accepted').toBe(400);
    expect(String(limit.json.detail)).toMatch(/Unsupported order_type 'limit'/);

    const afterLimit = await storedOrders(page, portfolioId);
    expect(afterLimit.length, 'it must not be silently filled as a market order').toBe(0);
    const afterLimitPortfolio = await storedPortfolio(page, portfolioId);
    expect(afterLimitPortfolio.current_virtual_cash, 'and must not touch cash').toBeCloseTo(STARTING_CASH, 4);

    const unknownType = await api(page, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, {
      asset_id: ASSET,
      side: 'BUY',
      quantity: 3,
      order_type: 'stop_loss',
      rationale: 'unknown type probe',
      expected_outcome: 'must be refused',
    });
    expect(unknownType.status, 'an unknown order type fails schema validation').toBe(422);
    expect((await storedOrders(page, portfolioId)).length).toBe(0);

    const key = `e2e-conflict-${randomBytes(6).toString('hex')}`;
    const first = await api(page, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, {
      asset_id: ASSET,
      side: 'BUY',
      quantity: 3,
      rationale: 'idempotency probe',
      expected_outcome: 'first use of the key',
      self_reported_confidence: 'medium',
      idempotency_key: key,
    });
    expect(first.status).toBe(201);

    const replay = await api(page, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, {
      asset_id: ASSET,
      side: 'BUY',
      quantity: 3,
      rationale: 'idempotency probe',
      expected_outcome: 'first use of the key',
      self_reported_confidence: 'medium',
      idempotency_key: key,
    });
    expect(replay.status, 'the identical request replays').toBe(200);
    expect(replay.json.id).toBe(first.json.id);

    const conflict = await api(page, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, {
      asset_id: ASSET,
      side: 'BUY',
      quantity: 9,
      rationale: 'idempotency probe',
      expected_outcome: 'different order, same key',
      self_reported_confidence: 'medium',
      idempotency_key: key,
    });
    expect(conflict.status, 'a different order under a used key must conflict').toBe(409);
    expect(conflict.json.detail.error).toBe('idempotency_key_conflict');
    expect(conflict.json.detail.existing_order_id).toBe(first.json.id);

    const orders = await storedOrders(page, portfolioId);
    expect(orders.length, 'the conflicting request created nothing').toBe(1);
    const fills = await fillsFor(page, portfolioId, first.json.id);
    expect(fills.length).toBe(1);
    const expected = expectedFill('BUY', 3, fills[0].observed_price);
    const portfolio = await storedPortfolio(page, portfolioId);
    expect(portfolio.current_virtual_cash, 'charged once for the one real order').toBeCloseTo(
      round4(STARTING_CASH + expected.net),
      4
    );
  });
});
