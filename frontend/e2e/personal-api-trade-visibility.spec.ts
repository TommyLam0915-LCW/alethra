import { test, expect, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { randomBytes } from 'crypto';

/**
 * Issue #26 §6: a program outside the browser trades through the personal API,
 * and its owner then sees that very same trade in the UI.
 *
 * The two halves are deliberately asymmetric.  The trading half is a plain HTTP
 * client holding only an API key — that is what "a user program" means, and it
 * is what fails if the personal API stops reusing the key machinery.  The
 * reading half is a real sign-in with real clicks on the rendered controls,
 * because §3 does not accept an API call as a substitute for a user flow: the
 * point is that a human can reach and recognise what their program did.
 *
 * Waits are waitForURL / expect.poll conditions, never sleeps, and the context
 * is closed on both the success and the failure path.
 *
 * The raw key and the account password are generated in this process and are
 * never placed in an assertion message, a log line or a screenshot; traces are
 * off in the runner config for the same reason a DOM snapshot would carry the
 * password.  Prices come from the built-in synthetic demo fixture (DEMO-*, last
 * bar 2024-02-13), so nothing here is evidence about live market data.
 */

const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';
const BACKEND_BASE = process.env.E2E_BACKEND_BASE ?? 'http://localhost:8099';

const ASSET = 'DEMO-ALPHA';
const STARTING_CASH = 50000;

const account = {
  email: `e2e-api-${randomBytes(6).toString('hex')}@alethra.io`,
  password: `E2e${randomBytes(9).toString('base64url')}!7`,
  displayName: `E2E API 程式 ${randomBytes(3).toString('hex')}`,
};

interface Trade {
  portfolioId: string;
  entryOrderId: string;
  entryQuantity: number;
  partialQuantity: number;
  finalQuantity: number;
  cashFinal: number;
  realized: number;
  cutoff: string;
  orderIds: string[];
}

/** Reads "USD 49,960.472" or "已實現損益 USD -39.53" as a number. */
const amount = (text: string) => Number(text.replace(/[^0-9.\-]/g, ''));

async function call(
  api: APIRequestContext,
  method: string,
  path: string,
  token?: string,
  body?: unknown,
) {
  const response = await api.fetch(`${BACKEND_BASE}${path}`, {
    method,
    data: body as object | undefined,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  const text = await response.text();
  let json: any = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  // Status only: the bodies on the auth paths carry the session token and the
  // raw key, so neither may reach a failure message.
  return { status: response.status(), json, headers: response.headers() };
}

/**
 * The user program: mint a key the way an operator would, then trade with it.
 *
 * Returns the identifiers and amounts the browser half must find on screen.
 */
async function runUserProgram(api: APIRequestContext): Promise<Trade> {
  await call(api, 'POST', '/api/auth/register', undefined, {
    email: account.email,
    password: account.password,
    display_name: account.displayName,
    user_type: 'individual',
  });
  const login = await call(api, 'POST', '/api/auth/login', undefined, {
    email: account.email,
    password: account.password,
  });
  expect(login.status, 'the operator session must be established').toBe(200);
  const session = login.json.access_token as string;

  const created = await call(api, 'POST', '/api/v1/api-keys', session, {
    name: `e2e-program-${randomBytes(3).toString('hex')}`,
    scopes: ['read', 'write'],
  });
  expect(created.status, 'the key must be minted through the existing key API').toBe(201);
  const key = created.json.raw_key as string;
  expect(key, 'a created key must return its raw value once').toBeTruthy();

  const bars = await call(
    api, 'GET',
    `/api/v1/market-data/bars/${ASSET}?start_date=2024-01-01&end_date=2024-12-31`,
  );
  expect(bars.status, 'the program needs price history to trade against').toBe(200);
  expect(bars.json.provider, 'the feed must be labelled synthetic, not live').toBe('Demo (Synthetic)');
  const series = bars.json.bars as { date: string; close: number }[];
  const closes = series.slice(-2).map((b) => Number(b.close));
  expect(closes[1] > closes[0], 'the fixture series must produce an entry signal').toBe(true);

  const opened = await call(api, 'POST', '/api/v1/paper-portfolio', key, {
    initial_virtual_cash: STARTING_CASH,
    currency: 'USD',
  });
  expect(opened.status, 'the program must be able to open its own account').toBe(201);
  const portfolioId = opened.json.id as string;

  const quantity = Math.max(1, Math.floor((STARTING_CASH * 0.2) / closes[1]));
  const entryBody = {
    asset_id: ASSET,
    side: 'BUY',
    quantity,
    order_type: 'market',
    rationale: 'E2E program: fixture close above prior close',
    expected_outcome: 'the owner can see this trade in the UI',
    idempotency_key: `e2e-${randomBytes(6).toString('hex')}-entry`,
  };
  const entry = await call(api, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, key, entryBody);
  expect(entry.status, 'the entry order must be accepted').toBe(201);
  expect(entry.json.actor_type, 'the order must be attributed to the key').toBe('api_key');

  // A program that loses a response retries. The retry must not trade twice.
  const retry = await call(api, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, key, entryBody);
  expect(retry.status, 'replaying the idempotency key must not create a second order').toBe(200);
  expect(retry.json.id, 'the retry must return the original order').toBe(entry.json.id);

  const partial = Math.max(1, Math.floor(quantity / 2));
  const remainder = Number((quantity - partial).toFixed(8));
  const exits = [];
  for (const [label, qty] of [['partial', partial], ['full', remainder]] as const) {
    const exit = await call(api, 'POST', `/api/v1/paper-portfolio/${portfolioId}/orders`, key, {
      asset_id: ASSET,
      side: 'SELL',
      quantity: qty,
      order_type: 'market',
      rationale: `E2E program: ${label} exit`,
      expected_outcome: 'the exit and its journal entry reach the UI',
      idempotency_key: `e2e-${randomBytes(6).toString('hex')}-${label}`,
    });
    expect(exit.status, `the ${label} exit must be accepted`).toBe(201);
    exits.push(exit.json);
  }

  const orders = await call(api, 'GET', `/api/v1/paper-portfolio/${portfolioId}/orders`, key);
  expect(orders.status).toBe(200);
  const orderRows = orders.json as { id: string; quantity: number; data_cutoff_at: string }[];
  expect(orderRows.length, 'entry, partial exit and full exit; the retry booked nothing').toBe(3);

  const final = await call(api, 'GET', `/api/v1/paper-portfolio/${portfolioId}`, key);
  expect(final.status).toBe(200);

  const decisions = await call(api, 'GET', `/api/v1/paper-portfolio/${portfolioId}/decisions`, key);
  expect(decisions.status).toBe(200);
  expect((decisions.json as unknown[]).length, 'every order must leave a journal decision').toBe(3);

  return {
    portfolioId,
    entryOrderId: entry.json.id as string,
    entryQuantity: quantity,
    partialQuantity: partial,
    finalQuantity: remainder,
    cashFinal: Number(final.json.current_virtual_cash),
    realized: Number(final.json.total_realized_pnl),
    cutoff: orderRows[0].data_cutoff_at,
    orderIds: orderRows.map((o) => o.id),
  };
}

test.describe('a program trading through the personal API is visible to its owner', () => {
  test.describe.configure({ mode: 'serial' });

  let api: APIRequestContext;
  let context: BrowserContext;
  let page: Page;
  let trade: Trade;

  /** The cash figure as the page renders it, e.g. "USD 49,960.472" -> 49960.472. */
  const cashOnScreen = async () =>
    amount(((await page.getByTestId('pp-cash').textContent()) ?? '').replace('USD', ''));

  test.beforeAll(async ({ browser, playwright }) => {
    api = await playwright.request.newContext();
    trade = await runUserProgram(api);

    context = await browser.newContext();
    page = await context.newPage();
    await page.goto(`${FRONTEND_BASE}/login`);
    await page.locator('input[type="email"]').fill(account.email);
    await page.locator('input[type="password"]').fill(account.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test.afterAll(async () => {
    if (context) await context.close();
    if (api) await api.dispose();
  });

  test('the owner can click from the dashboard to the account their program traded', async () => {
    // Paper accounts live under the shell's Tools menu; the dashboard must still
    // offer a path that lands on the account list.
    await page.getByTestId('tools-toggle').click();
    const nav = page.getByRole('menuitem', { name: 'Paper portfolios' });
    await expect(nav, 'the dashboard must offer a path to the paper accounts').toBeVisible();
    await nav.click();
    await page.waitForURL('**/paper-portfolio');

    const card = page.locator(`a[href="/paper-portfolio/${trade.portfolioId}"]`);
    await expect(card, 'the account the program opened must be listed for its owner').toBeVisible();
    await card.click();
    await page.waitForURL(`**/paper-portfolio/${trade.portfolioId}`);

    await expect.poll(cashOnScreen, 'the account must show the cash the API reports')
      .toBeCloseTo(trade.cashFinal, 2);
  });

  test('the cash and P&L on screen are the amounts the API returned', async () => {
    await page.goto(`${FRONTEND_BASE}/paper-portfolio/${trade.portfolioId}`);
    await expect(page.getByTestId('pp-cash')).toBeVisible();

    const cashText = (await page.getByTestId('pp-cash').textContent()) ?? '';
    expect(amount(cashText), 'the cash the UI shows must be the stored cash')
      .toBeCloseTo(trade.cashFinal, 2);

    const realizedText = (await page.getByTestId('pp-realized-pnl').textContent()) ?? '';
    expect(amount(realizedText), 'realised P&L must match the ledger')
      .toBeCloseTo(trade.realized, 2);
    expect(realizedText, 'a computed loss must be shown as a loss, not hidden')
      .toContain(trade.realized.toFixed(2));
  });

  test('the order list names the API program, not a human, and shows the bar it priced from', async () => {
    await page.goto(`${FRONTEND_BASE}/paper-portfolio/${trade.portfolioId}`);
    await page.getByTestId('pp-tab-orders').click();

    const rows = page.locator(`[data-testid^="pp-order-${ASSET}"]`);
    await expect(rows, 'three orders: one entry and two exits').toHaveCount(3);

    const actors = page.locator('[data-testid="pp-order-actor"]');
    await expect(actors).toHaveCount(3);
    for (let i = 0; i < 3; i += 1) {
      const text = (await actors.nth(i).textContent()) ?? '';
      expect(text, 'the row must be labelled as an API program').toContain('API 程式');
      expect(text, 'the raw enum value must not be shown to a user').not.toContain('api_key');
    }

    const cutoffs = page.locator('[data-testid="pp-order-cutoff"]');
    await expect(cutoffs, 'every API-key order states the bar it was priced from').toHaveCount(3);
    await expect(cutoffs.first()).toContainText(`定價日期 ${trade.cutoff}`);

    const quantities = (await page.locator('[data-testid="pp-order-qty"]').allTextContents())
      .map((q) => Number(q));
    expect(quantities, 'the sizes on screen are the sizes the program sent')
      .toEqual(expect.arrayContaining([trade.entryQuantity, trade.partialQuantity, trade.finalQuantity]));

    const sides = await page.locator('[data-testid="pp-order-side"]').allTextContents();
    expect(sides.filter((s) => s.includes('買入')).length, 'one entry').toBe(1);
    expect(sides.filter((s) => s.includes('賣出')).length, 'two exits').toBe(2);
  });

  test('the journal records the entry and both exits for this account only', async () => {
    await page.goto(`${FRONTEND_BASE}/paper-portfolio/${trade.portfolioId}`);
    await page.getByTestId('pp-tab-decisions').click();

    const actors = page.locator('[data-testid="pp-decision-actor"]');
    await expect(actors, 'one journal decision per order').toHaveCount(3);

    const body = await page.locator('h2:has-text("決策記錄") + div').innerText();
    expect(body, 'the entry must be recorded as an entry').toContain('進場');
    expect(body, 'the exits must be recorded as exits, not as notes').toContain('出場');
  });

  test('the program’s trade is still there after a reload', async () => {
    await page.goto(`${FRONTEND_BASE}/paper-portfolio/${trade.portfolioId}`);
    await page.getByTestId('pp-tab-orders').click();
    await expect(page.locator(`[data-testid^="pp-order-${ASSET}"]`)).toHaveCount(3);

    await page.reload();
    await page.getByTestId('pp-tab-orders').click();

    const rows = page.locator(`[data-testid^="pp-order-${ASSET}"]`);
    await expect(rows, 'a reload must not drop the program’s orders').toHaveCount(3);
    await expect(page.locator('[data-testid="pp-order-actor"]').first()).toContainText('API 程式');
    await expect.poll(cashOnScreen, 'a reload must not change the stored cash')
      .toBeCloseTo(trade.cashFinal, 2);
  });
});
