import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for the RULE-TRIGGERED forward-replay loop:
 *   login -> open the seeded experiment's simulation -> create session ->
 *   link a paper portfolio -> advance one day at a time until the declared 5%
 *   stop fires -> open the linked portfolio -> assert the system-actor entry/exit
 *   orders, the auto-labelled decision journal (exit_reason=stop) and the cash /
 *   realised-P&L metrics -> reload and assert the journal persisted server-side.
 *
 * Runs against the disposable E2E runtime created by `start_e2e_research_flow.sh`
 * (frontend 127.0.0.1:3099, backend 127.0.0.1:8099, database alethra_e2e).
 * Credentials are read from the generated git-ignored file; nothing secret is
 * declared here.
 *
 * DATA SETUP vs UI UNDER TEST (disclosed item-by-item, not presented as full-UI):
 *   - The account, the LOCKED experiment, the long-only rule strategy
 *     (buy_and_hold + stop_pct=0.05) and the synthetic DEMO-ALPHA daily bars are
 *     created by the tracked seed `backend/scripts/seed_e2e_rule_flow.py` at the
 *     DATABASE level. They are NOT authored through the new-experiment UI form in
 *     this spec; strategy/risk-plan authoring UI is covered separately. The seed
 *     states timeframe=1d, provider=demo, timezone=UTC and a sha256 dataset
 *     checksum, and writes its OWN bars so this flow never depends on any
 *     ad-hoc manually-seeded bars or any pre-existing account/session/portfolio.
 *   - EVERY product action this spec verifies is a real UI gesture: typing the
 *     login, clicking Start simulation / Advance one day / the link-portfolio
 *     button / the portfolio link / the journal tabs. No API call stands in for a
 *     user step, and no API call writes product state. Waits are waitForURL /
 *     waitForResponse / expect-poll, never arbitrary sleeps.
 */

interface Runtime {
  frontendBase: string;
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
    email,
    password,
  };
}

const runtime = loadRuntime();
const EXPERIMENT_TITLE = 'E2E 規則觸發流程（僅供自動化測試）';
const ASSET = 'DEMO-ALPHA';
const MAX_ADVANCES = 30;
// The order-side cell renders SIDE_LABELS[o.side] (paper-portfolio/[id]/page.tsx),
// so the DOM shows the localized labels, never the raw BUY/SELL enum.
const SIDE_LABEL_BUY = 'Buy';
const SIDE_LABEL_SELL = 'Sell';

const journey = {
  experimentId: '',
  portfolioId: '',
  advances: 0,
  sawStop: false,
};

test.describe('rule-triggered forward replay', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();

    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');

    // The seeded rule-flow experiment must be on the dashboard exactly once.
    const card = page.getByRole('link', { name: EXPERIMENT_TITLE, exact: true });
    await expect(card, 'seeded rule-flow experiment must be listed').toHaveCount(1);
    const href = (await card.getAttribute('href')) ?? '';
    journey.experimentId = new URL(href, runtime.frontendBase).pathname.split('/').pop() ?? '';
    expect(journey.experimentId, 'experiment id resolved from the dashboard card').not.toBe('');
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('creates a forward-simulation session through the UI', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentId}/simulation`);
    const createBtn = page.locator('button.btn-primary', { hasText: 'Start simulation' });
    await createBtn.waitFor({ state: 'visible', timeout: 20000 });
    await createBtn.click();
    // The advance control only appears once a session exists.
    const advanceBtn = page.locator('button.btn-primary', { hasText: 'Advance one day' });
    await expect(advanceBtn, 'session creation must reveal the advance control').toBeVisible({ timeout: 20000 });
  });

  test('links a paper portfolio through the UI', async () => {
    const linkBtn = page.locator('[data-testid="sim-link-portfolio-btn"]');
    await linkBtn.waitFor({ state: 'visible', timeout: 20000 });
    await linkBtn.click();
    await expect(page.locator('[data-testid="sim-portfolio-status"]'), 'linked portfolio status must render')
      .toBeVisible({ timeout: 20000 });
  });

  test('advances day-by-day until the declared stop fires', async () => {
    for (let i = 0; i < MAX_ADVANCES; i += 1) {
      const btn = page.locator('button.btn-primary', { hasText: 'Advance one day' });
      await btn.waitFor({ state: 'visible', timeout: 15000 }).catch(() => undefined);
      if (!(await btn.isVisible().catch(() => false))) break;

      const [resp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes('/simulations/') && r.url().includes('/advance'),
          { timeout: 20000 },
        ),
        btn.click(),
      ]);
      journey.advances += 1;

      if (resp.status() === 400) break; // replay complete
      expect(resp.status(), 'advance must succeed or signal completion').toBe(200);
      const body = (await resp.json()) as {
        decision?: { decision_detail?: { side?: string; exit_reason?: string } };
      };
      const detail = body.decision?.decision_detail ?? {};
      if (detail.side === 'SELL' && detail.exit_reason === 'stop') {
        journey.sawStop = true;
        break;
      }
    }
    expect(journey.advances, 'the replay must advance at least one day').toBeGreaterThan(0);
    expect(journey.sawStop, 'the 5% stop must fire and exit the position').toBe(true);
  });

  test('opens the linked portfolio and shows the rule-triggered orders and journal', async () => {
    const pfLink = page.locator('[data-testid="sim-portfolio-link"]');
    await pfLink.waitFor({ state: 'visible', timeout: 20000 });
    const href = (await pfLink.getAttribute('href')) ?? '';
    journey.portfolioId = href.split('/').pop() ?? '';
    expect(journey.portfolioId, 'linked portfolio id resolved from the UI link').not.toBe('');

    await pfLink.click();
    await page.waitForURL('**/paper-portfolio/**');

    // Orders tab: a system-actor entry BUY and a system-actor stop SELL.
    await page.locator('[data-testid="pp-tab-orders"]').click();
    const orderRows = page.locator(`[data-testid^="pp-order-${ASSET}-"]`);
    await orderRows.first().waitFor({ state: 'visible', timeout: 20000 });
    const sides = (await page.locator('[data-testid="pp-order-side"]').allTextContents()).map((s) => s.trim());
    expect(sides, 'both an entry BUY and an exit SELL must be booked').toContain(SIDE_LABEL_BUY);
    expect(sides, 'both an entry BUY and an exit SELL must be booked').toContain(SIDE_LABEL_SELL);
    const actors = (await page.locator('[data-testid="pp-order-actor"]').allTextContents())
      .map((a) => a.replace(/\s+/g, ' ').trim());
    expect(
      actors.some((a) => /system|rule|automatic/i.test(a)),
      'the rule-triggered orders must be attributed to a system/rule actor, not a human',
    ).toBe(true);

    // Decisions tab: auto-labelled entry + exit, with the stop exit reason.
    await page.locator('[data-testid="pp-tab-decisions"]').click();
    const decActors = page.locator('[data-testid="pp-decision-actor"]');
    await decActors.first().waitFor({ state: 'visible', timeout: 20000 });
    expect(await page.locator('[data-testid="pp-decision-auto"]').count(), 'decisions must be auto-labelled')
      .toBeGreaterThan(0);
    const exitReasons = (await page.locator('[data-testid="pp-decision-exit-reason"]').allTextContents())
      .map((e) => e.replace(/\s+/g, ' ').trim());
    expect(exitReasons.some((r) => /stop/i.test(r)), 'the exit must carry the stop reason').toBe(true);

    // Headline metrics: cash and realised P&L are present and numeric.
    const cash = (await page.locator('[data-testid="pp-cash"]').textContent()) ?? '';
    const realized = (await page.locator('[data-testid="pp-realized-pnl"]').textContent()) ?? '';
    expect(cash.replace(/\s+/g, ' ').trim(), 'cash metric must render a number').toMatch(/\d/);
    expect(realized.replace(/\s+/g, ' ').trim(), 'realised P&L metric must render').not.toBe('');
  });

  test('the journal survives a reload (server-side, not component state)', async () => {
    await page.goto(`${runtime.frontendBase}/paper-portfolio/${journey.portfolioId}`);
    await page.locator('[data-testid="pp-tab-orders"]').click();
    const orderRows = page.locator(`[data-testid^="pp-order-${ASSET}-"]`);
    await orderRows.first().waitFor({ state: 'visible', timeout: 20000 });
    const sides = (await page.locator('[data-testid="pp-order-side"]').allTextContents()).map((s) => s.trim());
    expect(sides, 'reloaded journal still shows the rule-triggered round trip').toContain(SIDE_LABEL_BUY);
    expect(sides, 'reloaded journal still shows the rule-triggered round trip').toContain(SIDE_LABEL_SELL);
  });
});
