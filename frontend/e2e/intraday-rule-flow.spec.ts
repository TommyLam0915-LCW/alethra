import { readFileSync } from 'fs';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for the INTRADAY (5-minute) RULE-TRIGGERED forward-replay loop.
 *
 * This is the intraday analogue of rule-triggered-flow.spec.ts (which stays the DAILY
 * 1d regression and is NOT relabelled here). It drives the REAL per-bar replay path the
 * product now exposes:
 *   login -> open the seeded 5-minute experiment's simulation -> create session ->
 *   assert the synthetic/replay/non-live badge + the per-bar advance label -> link a
 *   paper portfolio -> advance one 5-minute bar at a time to completion -> assert an
 *   entry, a target exit, a stop exit and a session-end forced exit all fired, every
 *   decision carrying timeframe=5m and a 5-minute-aligned exchange-local bar timestamp
 *   -> open the linked portfolio and assert the system-actor orders, the auto-labelled
 *   decision journal (exit reasons target/stop/session_end) and the cash / realised-P&L
 *   metrics -> download the trade-journal CSV and assert it carries a bar_timestamp
 *   column and exactly the 6 intraday fills -> reload and assert the journal persisted.
 *
 * DATA SETUP vs UI UNDER TEST (disclosed item-by-item, not presented as full-UI):
 *   - The account, the LOCKED intraday experiment, the opening_range_breakout rule
 *     (stop_pct=0.05, target_pct=0.02) and the 18 synthetic DEMO-ALPHA 5-minute bars
 *     (provider "demo-5m", 2024-03-04..06, America/New_York naive exchange-local, EST
 *     pre-DST) are created by the tracked seed backend/scripts/seed_e2e_intraday_flow.py
 *     at the DATABASE level. The bars are clearly-labelled SYNTHETIC deterministic data,
 *     never real-market data; a fixed synthetic profit is NOT strategy-efficacy evidence.
 *     The frozen StrategySpec HTTP schema only admits the three daily templates at 1d, so
 *     the intraday version is authored via the seed (ORM) and SELECTED here — exactly as
 *     the daily spec selects its seeded version. Strategy/risk-plan authoring UI is
 *     covered separately. provider_id "demo-5m" is the timeframe-isolation key
 *     (MarketDataBar has no timeframe column; unique key is instrument+timestamp+provider),
 *     so these bars can never be confused with the daily "demo" bars.
 *   - EVERY product action this spec verifies is a real UI gesture: typing the login,
 *     clicking 開始模擬 / 推進下一根 5 分鐘 K 線 / the link-portfolio button / the portfolio
 *     link / the journal tabs / the CSV download. No API call stands in for a user step and
 *     no API call writes product state. Waits are waitForURL / waitForResponse /
 *     waitForEvent / expect-poll, never arbitrary sleeps.
 *
 * Runs against the disposable E2E runtime created by start_e2e_research_flow.sh
 * (frontend 127.0.0.1:3099, backend 127.0.0.1:8099, database alethra_e2e). Credentials are
 * read from the generated git-ignored file; nothing secret is declared or logged here.
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
const EXPERIMENT_TITLE = 'E2E 日內規則觸發流程（5 分鐘合成，僅供自動化測試）';
const ASSET = 'DEMO-ALPHA';
const PROVIDER = 'demo-5m';
const TIMEFRAME = '5m';
// 18 seeded bars -> 18 successful advances; the 19th returns 400 (replay complete).
const EXPECTED_BARS = 18;
const MAX_ADVANCES = EXPECTED_BARS + 3;
const EXPECTED_FILL_COUNT = 6; // rule_entry x3 + rule_target + rule_session_end + rule_stop
// The order-side cell renders SIDE_LABELS[o.side], so the DOM shows localized labels.
const SIDE_LABEL_BUY = '買入';
const SIDE_LABEL_SELL = '賣出';

interface AdvanceDecision {
  decision_type: string;
  side?: string;
  exit_reason?: string;
  bar_timestamp?: string | null;
  timeframe?: string;
}

const journey = {
  experimentId: '',
  portfolioId: '',
  advances: 0,
  completed400: false,
  decisions: [] as AdvanceDecision[],
};

// Minimal RFC-4180 line parser (handles quoted fields with embedded commas). The
// journal's free-text rationale is the LAST column and may contain commas.
function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i += 1; } else { inQuotes = false; }
      } else { cur += ch; }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      out.push(cur); cur = '';
    } else { cur += ch; }
  }
  out.push(cur);
  return out;
}

function barMinute(barTs: string): number {
  const m = /T\d{2}:(\d{2})/.exec(barTs);
  return m ? parseInt(m[1], 10) : -1;
}

test.describe('intraday 5-minute rule-triggered forward replay', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();

    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');

    // The seeded intraday experiment must be on the dashboard exactly once.
    const card = page.locator('.dash-experiment-item', { hasText: EXPERIMENT_TITLE });
    await expect(card, 'seeded intraday experiment must be listed exactly once').toHaveCount(1);
    const href = (await card.first().getAttribute('href')) ?? '';
    journey.experimentId = new URL(href, runtime.frontendBase).pathname.split('/').pop() ?? '';
    expect(journey.experimentId, 'experiment id resolved from the dashboard card').not.toBe('');
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('creates an intraday session and labels it synthetic / historical-replay / non-live', async () => {
    await page.goto(`${runtime.frontendBase}/experiments/${journey.experimentId}/simulation`);
    const createBtn = page.locator('[data-testid="sim-create-btn"]');
    await createBtn.waitFor({ state: 'visible', timeout: 20000 });
    await createBtn.click();

    // The intraday badge only renders once a session with timeframe=5m exists.
    const badge = page.locator('[data-testid="sim-intraday-badge"]');
    await expect(badge, 'intraday session must reveal the synthetic/replay/non-live badge')
      .toBeVisible({ timeout: 20000 });
    await expect(badge, 'badge must state synthetic / historical replay / non-live')
      .toContainText('合成資料');
    await expect(badge).toContainText('歷史回放');
    await expect(badge).toContainText('非即時行情');
    await expect(page.locator('[data-testid="sim-intraday-timeframe"]'), 'timeframe label must be 5 分鐘')
      .toContainText('5 分鐘');
    await expect(page.locator('[data-testid="sim-intraday-provider"]'), 'provider must be the timeframe-isolated demo-5m')
      .toHaveText(PROVIDER);

    // The advance control must read the PER-BAR intraday label, never the daily 自動推進一日.
    const advanceBtn = page.locator('[data-testid="sim-advance-btn"]');
    await expect(advanceBtn, 'session creation must reveal the per-bar advance control').toBeVisible({ timeout: 20000 });
    await expect(advanceBtn, 'advance label must be per-bar (推進下一根 5 分鐘 K 線)')
      .toContainText('推進下一根 5 分鐘 K 線');
  });

  test('links a paper portfolio through the UI', async () => {
    const linkBtn = page.locator('[data-testid="sim-link-portfolio-btn"]');
    await linkBtn.waitFor({ state: 'visible', timeout: 20000 });
    await linkBtn.click();
    await expect(page.locator('[data-testid="sim-portfolio-status"]'), 'linked portfolio status must render')
      .toBeVisible({ timeout: 20000 });
  });

  test('advances bar-by-bar to completion, firing entry + target + stop + session-end exits', async () => {
    for (let i = 0; i < MAX_ADVANCES; i += 1) {
      const btn = page.locator('[data-testid="sim-advance-btn"]');
      await btn.waitFor({ state: 'visible', timeout: 15000 }).catch(() => undefined);
      if (!(await btn.isVisible().catch(() => false))) break;

      const [resp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes('/simulations/') && r.url().includes('/advance'),
          { timeout: 20000 },
        ),
        btn.click(),
      ]);

      if (resp.status() === 400) { journey.completed400 = true; break; }
      expect(resp.status(), 'advance must succeed or signal completion').toBe(200);
      journey.advances += 1;

      const body = (await resp.json()) as { decision?: { decision_type?: string; decision_detail?: Record<string, any> } };
      const detail = body.decision?.decision_detail ?? {};
      journey.decisions.push({
        decision_type: body.decision?.decision_type ?? '',
        side: detail.side,
        exit_reason: detail.exit_reason,
        bar_timestamp: detail.bar_timestamp ?? null,
        timeframe: detail.timeframe,
      });
    }

    expect(journey.advances, `the replay must advance exactly ${EXPECTED_BARS} bars`).toBe(EXPECTED_BARS);
    expect(journey.completed400, 'the replay must terminate with a 400 completion signal').toBe(true);

    const entries = journey.decisions.filter((d) => d.decision_type === 'simulated_entry');
    expect(entries.length, 'three breakout entries must fire (one per day)').toBe(3);
    expect(entries.every((d) => d.side === 'BUY'), 'every entry must be a BUY').toBe(true);

    const exits = journey.decisions.filter((d) => d.decision_type === 'simulated_exit' || d.decision_type === 'forced_exit');
    const exitReasons = exits.map((d) => d.exit_reason);
    expect(exitReasons, 'a target exit must fire').toContain('target');
    expect(exitReasons, 'a stop exit must fire').toContain('stop');
    expect(exitReasons, 'a session-end forced exit must fire').toContain('session_end');
    const sessionEnd = journey.decisions.find((d) => d.exit_reason === 'session_end');
    expect(sessionEnd?.decision_type, 'the session-end exit must be a forced_exit').toBe('forced_exit');

    // Timeframe isolation: every bar-bearing decision must be 5m, never a daily bar.
    const barBearing = journey.decisions.filter((d) => d.bar_timestamp);
    expect(barBearing.length, 'entries and exits must carry a bar timestamp').toBeGreaterThan(0);
    expect(barBearing.every((d) => d.timeframe === TIMEFRAME), 'every intraday decision must be timeframe=5m').toBe(true);

    // Bar timestamps must be 5-minute aligned, exchange-local, in the seeded window, strictly increasing.
    let prev = '';
    for (const d of barBearing) {
      const ts = d.bar_timestamp as string;
      expect(ts, 'bar timestamp within the seeded 2024-03-04..06 window').toMatch(/^2024-03-0[456]T\d{2}:\d{2}:\d{2}/);
      expect(barMinute(ts) % 5, 'bar timestamp must be 5-minute aligned').toBe(0);
      expect(ts > prev, 'bar timestamps must strictly increase (no daily-bar reuse, correct ordering)').toBe(true);
      prev = ts;
    }
  });

  test('opens the linked portfolio and shows the intraday badge, rule orders, journal and metrics', async () => {
    const pfLink = page.locator('[data-testid="sim-portfolio-link"]');
    await pfLink.waitFor({ state: 'visible', timeout: 20000 });
    const href = (await pfLink.getAttribute('href')) ?? '';
    journey.portfolioId = href.split('/').pop() ?? '';
    expect(journey.portfolioId, 'linked portfolio id resolved from the UI link').not.toBe('');

    await pfLink.click();
    await page.waitForURL('**/paper-portfolio/**');

    // The portfolio page must disclose the intraday synthetic replay too.
    const pfBadge = page.locator('[data-testid="pp-intraday-badge"]');
    await expect(pfBadge, 'portfolio must disclose the intraday synthetic/replay/non-live basis').toBeVisible({ timeout: 20000 });
    await expect(pfBadge).toContainText('非即時行情');
    await expect(page.locator('[data-testid="pp-intraday-provider"]'), 'portfolio badge provider must be demo-5m').toHaveText(PROVIDER);

    // Orders tab: system-actor entry BUY(s) and exit SELL(s).
    await page.locator('[data-testid="pp-tab-orders"]').click();
    const orderRows = page.locator(`[data-testid^="pp-order-${ASSET}-"]`);
    await orderRows.first().waitFor({ state: 'visible', timeout: 20000 });
    const sides = (await page.locator('[data-testid="pp-order-side"]').allTextContents()).map((s) => s.trim());
    expect(sides, 'entry BUY orders must be booked').toContain(SIDE_LABEL_BUY);
    expect(sides, 'exit SELL orders must be booked').toContain(SIDE_LABEL_SELL);
    const actors = (await page.locator('[data-testid="pp-order-actor"]').allTextContents())
      .map((a) => a.replace(/\s+/g, ' ').trim());
    expect(
      actors.some((a) => /系統|system|規則|rule|自動/i.test(a)),
      'the rule-triggered orders must be attributed to a system/rule actor, not a human',
    ).toBe(true);

    // Decisions tab: auto-labelled, with the raw exit reasons target/stop/session_end.
    await page.locator('[data-testid="pp-tab-decisions"]').click();
    const decActors = page.locator('[data-testid="pp-decision-actor"]');
    await decActors.first().waitFor({ state: 'visible', timeout: 20000 });
    expect(await page.locator('[data-testid="pp-decision-auto"]').count(), 'decisions must be auto-labelled')
      .toBeGreaterThan(0);
    const journalExitReasons = (await page.locator('[data-testid="pp-decision-exit-reason"]').allTextContents())
      .map((e) => e.replace(/\s+/g, ' ').trim());
    expect(journalExitReasons.some((r) => /target|目標/.test(r)), 'journal must show the target exit').toBe(true);
    expect(journalExitReasons.some((r) => /stop|停損/.test(r)), 'journal must show the stop exit').toBe(true);
    expect(journalExitReasons.some((r) => /session_end|收盤/.test(r)), 'journal must show the session-end exit').toBe(true);

    // Headline metrics: cash and realised P&L are present and numeric.
    const cash = (await page.locator('[data-testid="pp-cash"]').textContent()) ?? '';
    const realized = (await page.locator('[data-testid="pp-realized-pnl"]').textContent()) ?? '';
    expect(cash.replace(/\s+/g, ' ').trim(), 'cash metric must render a number').toMatch(/\d/);
    expect(realized.replace(/\s+/g, ' ').trim(), 'realised P&L metric must render').not.toBe('');
  });

  test('downloads the trade-journal CSV with a bar_timestamp column and the 6 intraday fills', async () => {
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 20000 }),
      page.locator('[data-testid="pp-download-csv"]').click(),
    ]);
    const path = await download.path();
    expect(path, 'CSV download must produce a file').toBeTruthy();
    const text = readFileSync(path as string, 'utf-8');
    const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
    const header = parseCsvLine(lines[0]);
    expect(header, 'CSV must carry the intraday bar_timestamp column').toContain('bar_timestamp');

    const idxType = header.indexOf('record_type');
    const idxBar = header.indexOf('bar_timestamp');
    const idxReason = header.indexOf('simulated_fill_reason');
    const idxFillId = header.indexOf('fill_id');
    expect(idxType, 'record_type column present').toBeGreaterThanOrEqual(0);
    expect(idxBar, 'bar_timestamp column present').toBeGreaterThanOrEqual(0);
    expect(idxReason, 'simulated_fill_reason column present').toBeGreaterThanOrEqual(0);
    expect(idxFillId, 'fill_id column present').toBeGreaterThanOrEqual(0);

    const fillRows = lines.slice(1).map(parseCsvLine).filter((cols) => cols[idxType] === 'fill');
    expect(fillRows.length, `CSV must contain exactly ${EXPECTED_FILL_COUNT} intraday fills`).toBe(EXPECTED_FILL_COUNT);

    const fillIds = new Set<string>();
    const reasons = new Set<string>();
    for (const cols of fillRows) {
      const bar = cols[idxBar];
      expect(bar, 'every fill must carry a non-empty bar_timestamp').not.toBe('');
      expect(bar, 'fill bar_timestamp must be 5-minute aligned exchange-local').toMatch(/^2024-03-0[456]T\d{2}:\d{2}:00$/);
      expect(barMinute(bar) % 5, 'fill bar_timestamp minute must be a multiple of 5').toBe(0);
      fillIds.add(cols[idxFillId]);
      reasons.add(cols[idxReason]);
    }
    expect(fillIds.size, 'each fill must have a distinct fill_id').toBe(EXPECTED_FILL_COUNT);
    expect(reasons, 'CSV must include the rule entry fill reason').toContain('rule_entry');
    expect(reasons, 'CSV must include the rule target fill reason').toContain('rule_target');
    expect(reasons, 'CSV must include the rule stop fill reason').toContain('rule_stop');
    expect(reasons, 'CSV must include the rule session-end fill reason').toContain('rule_session_end');
  });

  test('the intraday journal survives a reload (server-side, not component state)', async () => {
    await page.goto(`${runtime.frontendBase}/paper-portfolio/${journey.portfolioId}`);
    await expect(page.locator('[data-testid="pp-intraday-badge"]'), 'reloaded portfolio still discloses intraday basis')
      .toBeVisible({ timeout: 20000 });
    await page.locator('[data-testid="pp-tab-orders"]').click();
    const orderRows = page.locator(`[data-testid^="pp-order-${ASSET}-"]`);
    await orderRows.first().waitFor({ state: 'visible', timeout: 20000 });
    const sides = (await page.locator('[data-testid="pp-order-side"]').allTextContents()).map((s) => s.trim());
    expect(sides, 'reloaded journal still shows the intraday round trips').toContain(SIDE_LABEL_BUY);
    expect(sides, 'reloaded journal still shows the intraday round trips').toContain(SIDE_LABEL_SELL);
  });
});
