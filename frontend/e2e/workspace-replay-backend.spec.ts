import { readFileSync } from 'fs';
import {
  test,
  expect,
  request as playwrightRequest,
  type Page,
  type APIRequestContext,
} from '@playwright/test';

/**
 * TIER B — the replay clock bound to the REAL local backend and its REAL stored dataset.
 *
 * Counterpart to the interception-based replay tests in workspace.spec.ts (tier A:
 * mocked API, UI/layout evidence only). Nothing here is routed or stubbed: the browser
 * talks to the stack created by start_e2e_research_flow.sh (frontend 127.0.0.1:3099,
 * backend 127.0.0.1:8099, PostgreSQL database alethra_e2e) and renders the bars and the
 * simulation session rows that already exist there.
 *
 * WHY IT EXISTS: the today-anchored range bug was invisible under interception — it was
 * only found by reading the real session cursor. The stored US:AAPL demo series runs to
 * 2025-12-31 while the active session's cursor sits inside the 2024 window, so this
 * dataset genuinely contains future bars: a leak caught here is a real leak, not a
 * fixture artefact.
 *
 * DATA IS SYNTHETIC DEMO DATA, NOT LIVE QUOTES — historical replay, 非即時行情. A fixed
 * demo series is not market-data validation, and advancing a disposable demo session is
 * not broker integration. Missing live-provider credentials do not block this tier.
 *
 * UI GESTURES vs ORACLE READS (disclosed):
 *   - Every product action is a real browser gesture: typing the login, opening
 *     /workspace, clicking 「下一日」. No API call performs a product step.
 *   - GETs issued through the request context are independent reads of server state
 *     (session row, full stored window) compared against what the page shows. They write
 *     nothing.
 *   - The "which portfolio is active" pick is replicated here because the workspace makes
 *     it and the test must know which session row to read. The replicated pick is
 *     cross-checked against the page's own session fingerprint (the 8-char strategy
 *     version), so the oracle cannot silently diverge.
 *   - Container ⑦ (操作面板) is covered by real clicks on its tabs and on its review
 *     control, and its proof is an ABSENCE: the stored dataset makes the honest submit
 *     target unresolvable, so the correct product behaviour is to refuse. Every ⑦
 *     assertion is an oracle read plus a client-side write log, in which the shell's
 *     layout-preference save is classified apart from decision/order/execution writes —
 *     only the latter must be absent. Nothing in this tier writes through ⑦.
 *   - Container ⑧ (證據面板) is covered read-only over the portfolio the session is
 *     bound to: positions, paper orders, fills, the journal envelope and the portfolio
 *     detail are each fetched through the same endpoints the panel calls and compared
 *     cell for cell, including the real CSV download and the detail drawer's
 *     per-column record. It clicks tabs, the scope filter and the export button only —
 *     no decision, order, execution or export write may appear in the client-side log.
 *
 * REPEATABILITY: one advance per run, asserted against whatever the server cursor
 * actually is — never a hardcoded date — so a rerun performs the same checks from the new
 * cursor. The cursor walks toward the stored cutoff (2024-01-31) and the suite stops
 * making sense once the session completes; the tests fail loudly on that instead of
 * quietly passing.
 *
 * Credentials come from the generated git-ignored file; no secret value is declared,
 * printed, asserted or written to an artifact.
 */

import type {
  JournalResponse,
  OrderRow,
  PositionRow,
} from '../src/components/workspace/evidence';

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
const SYMBOL = 'US:AAPL';

interface BarsCall {
  start: string | null;
  end: string | null;
  dates: string[];
}

interface SessionRow {
  id: string;
  status: string;
  timeframe: string | null;
  current_snapshot_date: string | null;
  data_cutoff_at: string | null;
  observed_days: number;
  provider_id: string | null;
  experiment_version_id: string;
}

type BarRow = { date: string; close: number };

/** What the experiments list carries; `current_version_id` is the only version→owner key. */
interface ExperimentRow {
  id: string;
  title: string;
  status: string;
  current_version_id: string | null;
}

interface VersionRow {
  id: string;
  version_number: number;
  strategy_spec: Record<string, unknown> | null;
  assumptions: Record<string, unknown> | null;
  change_reason: string | null;
  config_hash: string | null;
  locked_at: string | null;
  user_approved_at: string | null;
}

interface DecisionRow {
  id: string;
  step_number: number;
  snapshot_date: string | null;
  decision_type: string;
  decision_detail: Record<string, unknown> | null;
}

const journey = {
  token: '',
  sessionId: '',
  session: null as SessionRow | null,
  bars: [] as BarsCall[],
  stored: [] as BarRow[],
  /** Every non-GET the browser issued (path only), so a test can prove it wrote nothing. */
  writes: [] as string[],
};

let apiRead: APIRequestContext;

/** Independent read of server state. GET only — it never performs a product action. */
async function oracleGet<T>(path: string): Promise<T> {
  const res = await apiRead.get(`${runtime.backendBase}${path}`, {
    headers: { Authorization: `Bearer ${journey.token}` },
  });
  if (!res.ok()) throw new Error(`oracle GET ${path} -> ${res.status()}`);
  return (await res.json()) as T;
}

/** Close of the last stored bar at or before the cursor, from the full oracle window. */
function storedThrough(cursor: string): BarRow {
  const visible = journey.stored.filter((b) => b.date <= cursor);
  expect(visible.length, `the demo dataset must store a bar on or before ${cursor}`).toBeGreaterThan(0);
  return visible[visible.length - 1];
}

/**
 * Read a rendered money cell back as a number. Comparing values rather than strings
 * keeps a thousands separator, a currency suffix or the formatters' U+2212 minus from
 * turning a correct figure into a failure — and still fails if the printed digits
 * differ from the stored ones.
 */
function printedMoney(text: string): number {
  const match = text.replace(/,/g, '').replace(/−/g, '-').match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : NaN;
}

test.describe('workspace replay against the real local backend (tier B)', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  /**
   * SETUP BOUNDARY (not part of UI-under-test):
   * This beforeAll creates a fresh session and portfolio via API for each test run.
   * The browser login is real (UI gesture), but the session/portfolio creation is
   * scaffolding — it does not test any product feature. The actual UI test begins
   * in the first test() block, which navigates to /workspace and interacts with
   * the replay controller.
   *
   * WHY: the previous approach found an existing session, but advancing it exhausted
   * the dataset after ~21 runs. Creating a fresh session per run makes the test
   * repeatable without resetting fixtures or deleting shared data.
   */
  test.beforeAll(async ({ browser }) => {
    // Manually created: the { request } fixture cannot cross from beforeAll into tests.
    apiRead = await playwrightRequest.newContext();
    page = await browser.newPage();

    // Capture what the browser actually receives, bodies included: the leak check is
    // about transported data, not only about request parameters.
    page.on('response', async (res) => {
      const url = new URL(res.url());
      if (!url.pathname.startsWith('/api/v1/instruments/') || !url.pathname.endsWith('/bars')) return;
      if (res.status() !== 200) return;
      let dates: string[] = [];
      try {
        const body = await res.json();
        dates = (body.bars ?? []).map((b: BarRow) => b.date);
      } catch {
        return;
      }
      journey.bars.push({
        start: url.searchParams.get('start_date'),
        end: url.searchParams.get('end_date'),
        dates,
      });
    });

    // Method + path only — never a query string or body, so no credential can ride in.
    page.on('request', (req) => {
      if (req.method() === 'GET' || req.method() === 'HEAD') return;
      const path = new URL(req.url()).pathname;
      if (!path.startsWith('/api/')) return;
      if (path.startsWith('/api/auth/')) return;
      journey.writes.push(`${req.method()} ${path}`);
    });

    // Real browser login: the user gesture that establishes the session token.
    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
    journey.token = await page.evaluate(() => localStorage.getItem('token') ?? '');
    expect(journey.token.length, 'real login must yield a session token').toBeGreaterThan(20);

    // SETUP: find a locked daily version to bind the test session to.
    // This is scaffolding, not a product action under test.
    const experiments = await oracleGet<ExperimentRow[]>('/api/v1/experiments');
    expect(experiments.length, 'the E2E runtime must hold at least one experiment').toBeGreaterThan(0);

    let lockedVersionId: string | null = null;
    for (const exp of experiments) {
      if (!exp.current_version_id) continue;
      const versions = await oracleGet<VersionRow[]>(`/api/v1/experiments/${exp.id}/versions`);
      const locked = versions.find((v) => v.locked_at !== null);
      if (locked) {
        // Check if this version is daily (timeframe null or '1d' in strategy_spec)
        const spec = locked.strategy_spec || {};
        const timeframe = spec.timeframe as string | undefined;
        if (!timeframe || timeframe === '1d') {
          lockedVersionId = locked.id;
          break;
        }
      }
    }
    expect(lockedVersionId, 'must find a locked daily version for test session creation').not.toBeNull();

    // SETUP: create a fresh session for this test run.
    // POST /api/v1/simulations/versions/{version_id} with initial_virtual_cash.
    const createSessionRes = await apiRead.post(`${runtime.backendBase}/api/v1/simulations/versions/${lockedVersionId}`, {
      headers: {
        Authorization: `Bearer ${journey.token}`,
        'Content-Type': 'application/json',
      },
      data: { initial_virtual_cash: 100000 },
    });
    expect(createSessionRes.status(), 'session creation must succeed').toBe(201);
    const newSession = await createSessionRes.json() as { id: string; status: string };
    expect(['pending', 'active'], 'new session must be pending or active').toContain(newSession.status);
    journey.sessionId = newSession.id;

    // SETUP: create a portfolio bound to the new session.
    // POST /api/v1/paper-portfolio with simulation_session_id.
    const createPortfolioRes = await apiRead.post(`${runtime.backendBase}/api/v1/paper-portfolio`, {
      headers: {
        Authorization: `Bearer ${journey.token}`,
        'Content-Type': 'application/json',
      },
      data: {
        initial_virtual_cash: 100000,
        currency: 'USD',
        simulation_session_id: journey.sessionId,
      },
    });
    expect(createPortfolioRes.status(), 'portfolio creation must succeed').toBe(201);
    const newPortfolio = await createPortfolioRes.json() as { id: string };

    // SETUP: retire all other active portfolios so the workspace loads the one we just created.
    // These are test artifacts from previous runs, not shared fixtures. The workspace picks the first
    // active portfolio, and the database returns them in insertion order (oldest first), so without
    // this cleanup the workspace would load an old portfolio instead of the new one.
    const portfoliosAfter = await oracleGet<
      { id: string; status: string; simulation_session_id: string | null }[]
    >('/api/v1/paper-portfolio');
    for (const p of portfoliosAfter) {
      if (p.status === 'active' && p.id !== newPortfolio.id) {
        const retireRes = await apiRead.post(`${runtime.backendBase}/api/v1/paper-portfolio/${p.id}/retire`, {
          headers: {
            Authorization: `Bearer ${journey.token}`,
            'Content-Type': 'application/json',
          },
        });
        // Retire may fail if already retired/completed; that's OK, just skip it.
        if (!retireRes.ok() && retireRes.status() !== 400) {
          throw new Error(`failed to retire old portfolio ${p.id}: ${retireRes.status()}`);
        }
      }
    }

    // SETUP: advance the session once to move it from 'pending' to 'active' and establish the initial cursor.
    // A pending session has no cursor yet; the test needs an active session with a cursor to verify the UI.
    // This is scaffolding, not part of the UI test. The actual "下一日" click test will advance it again.
    const advanceRes = await apiRead.post(`${runtime.backendBase}/api/v1/simulations/${journey.sessionId}/advance`, {
      headers: {
        Authorization: `Bearer ${journey.token}`,
        'Content-Type': 'application/json',
      },
    });
    expect(advanceRes.status(), 'initial advance must succeed').toBe(200);
  });

  test.afterAll(async () => {
    await page.close();
    await apiRead.dispose();
  });

  test('the workspace clock is the server cursor and no future bar reaches the browser', async () => {
    const before = await oracleGet<SessionRow>(`/api/v1/simulations/${journey.sessionId}`);
    // A completed/paused or intraday session would make these checks meaningless; fail loudly.
    expect(['pending', 'active'], `session ${journey.sessionId} must be pending or active for this journey`).toContain(before.status);
    // Daily sessions have timeframe '1d' or null (backend default is '1d')
    expect(before.timeframe === null || before.timeframe === '1d', 'this journey covers the daily cursor; intraday is tier-A tested').toBe(true);
    const cursor = before.current_snapshot_date;
    expect(cursor, 'an active daily session must carry a server cursor').toBeTruthy();
    journey.session = before;

    await page.goto(`${runtime.frontendBase}/workspace`);
    await page.waitForLoadState('networkidle');

    const controller = page.getByTestId('replay-controller');
    await expect(controller, 'a bound session must render the replay controller').toBeVisible();
    await expect(page.getByTestId('replay-clock-value')).toHaveText(cursor as string);
    await expect(page.getByTestId('replay-status')).toHaveText('進行中');
    // The provenance is ⑥'s second layer: collapsed by default, revealed by a
    // real click, and it carries the same server-owned values.
    const summaryToggle = controller.getByTestId('replay-meta-toggle');
    await expect(page.getByTestId('replay-summary'), 'the secondary layer starts collapsed').toHaveCount(0);
    await summaryToggle.click();
    const summary = page.getByTestId('replay-summary');
    await expect(summary).toBeVisible();
    await expect(summary).toContainText(before.provider_id ?? '未提供');
    await expect(summary).toContainText(before.experiment_version_id.slice(0, 8));
    await expect(page.getByTestId('replay-bars-end')).toHaveText(cursor as string);
    await summaryToggle.click();
    await expect(page.getByTestId('replay-summary')).toHaveCount(0);

    // Non-vacuity: the stored dataset genuinely runs past the cursor, so the checks
    // below are cutting real bars out of the picture.
    const full = await oracleGet<{ bars: BarRow[] }>(
      `/api/v1/instruments/${encodeURIComponent(SYMBOL)}/bars?start_date=1900-01-01&end_date=2099-12-31`
    );
    journey.stored = full.bars;
    expect(full.bars.length, 'the demo dataset must store bars for the workspace symbol').toBeGreaterThan(0);
    const newestStored = full.bars[full.bars.length - 1];
    expect(
      newestStored.date > (cursor as string),
      `stored bars must run past the cursor for this to prove anything (newest ${newestStored.date})`
    ).toBe(true);

    await expect.poll(() => journey.bars.length).toBeGreaterThan(0);
    for (const call of journey.bars) {
      expect(call.end, 'in a replay context every bars request must carry the cursor cut').toBe(cursor);
      expect(
        (call.start as string) < (cursor as string),
        `start ${call.start} must sit behind the cursor, never after it`
      ).toBe(true);
      expect(call.dates.length).toBeGreaterThan(0);
      expect(
        call.dates[call.dates.length - 1] <= (cursor as string),
        'no bar after the cursor may reach the browser at all'
      ).toBe(true);
    }

    // Summary and price must come from the clipped series, not the newest stored bar.
    const lastVisible = storedThrough(cursor as string);
    await expect(page.getByTestId('chart-meta-line')).toContainText(`數據截至 ${lastVisible.date}`);
    await expect(page.getByTestId('instrument-last-price')).toContainText(lastVisible.close.toFixed(2));
    expect(
      lastVisible.close.toFixed(2) !== newestStored.close.toFixed(2),
      'the cursor close must differ from the newest stored close for this check to have teeth'
    ).toBe(true);
    await expect(page.getByTestId('instrument-header')).not.toContainText(newestStored.date);
  });

  test('⑦ resolves its rules from the real experiment version and refuses the order path it cannot price', async () => {
    const cursor = journey.session?.current_snapshot_date as string;
    const versionId = journey.session?.experiment_version_id as string;
    const writesBefore = journey.writes.length;
    /** The shell persists the visitor's panel layout; that is a preference write. */
    const isPreference = (call: string) => /^PUT \/api\/v1\/layouts\/\w+$/.test(call);
    const decisionsBefore = await oracleGet<DecisionRow[]>(
      `/api/v1/simulations/${journey.sessionId}/decisions`
    );

    // Oracle-side resolution, the same two hops the product makes: version → owning
    // experiment (only via current_version_id) → that experiment's version list.
    const experiments = await oracleGet<ExperimentRow[]>('/api/v1/experiments/');
    const owner = experiments.find((e) => e.current_version_id === versionId) ?? null;
    expect(owner, `no stored experiment reports current_version_id ${versionId}`).not.toBeNull();
    const versions = await oracleGet<VersionRow[]>(`/api/v1/experiments/${owner!.id}/versions`);
    const version = versions.find((v) => v.id === versionId) ?? null;
    expect(version, `experiment ${owner!.id} does not list version ${versionId}`).not.toBeNull();
    const spec = (version!.strategy_spec ?? {}) as Record<string, any>;
    const assets: string[] = Array.isArray(spec.asset_ids) ? spec.asset_ids : [];
    expect(assets.length, 'the stored version must bind at least one asset for ⑦ to judge').toBeGreaterThan(0);
    expect(owner!.title.length, 'the experiment must carry a title to display').toBeGreaterThan(0);

    await page.goto(`${runtime.frontendBase}/workspace`);
    await page.waitForLoadState('networkidle');

    // 策略規則 first: ⑦'s order guard is only meaningful if the rules really resolved.
    await page.getByTestId('action-tab-rules').click();
    const rules = page.getByTestId('strategy-rules-panel');
    await expect(rules.getByTestId('rules-table')).toBeVisible({ timeout: 15_000 });
    // A missing API field renders as this literal, never as 0, '—' or an engine default.
    const NOT_PROVIDED_UI = '未提供';
    const shown = (value: unknown): string =>
      value === null || value === undefined || value === '' ? NOT_PROVIDED_UI : String(value);

    await expect(rules.getByTestId('rules-experiment')).toHaveText(`${owner!.title}（${owner!.status}）`);
    await expect(rules.getByTestId('rules-effective-version')).toHaveText(
      `${versionId.slice(0, 8)} · v${version!.version_number}`
    );
    await expect(rules.getByTestId('rules-saved-version')).toHaveText(owner!.current_version_id!.slice(0, 8));
    await expect(rules.getByTestId('rules-template')).toHaveText(shown(spec.template_type));
    await expect(rules.getByTestId('rules-assets')).toHaveText(assets.join('、'));
    await expect(rules.getByTestId('rules-period')).toHaveText(
      `${shown(spec.start_date)} ~ ${shown(spec.end_date)}`
    );
    await expect(rules.getByTestId('rules-initial-cash')).toHaveText(shown(spec.initial_cash));
    await expect(rules.getByTestId('rules-config-hash')).toHaveText(shown(version!.config_hash));
    await expect(rules.getByTestId('rules-locked-at')).toHaveText(shown(version!.locked_at));
    await expect(rules.getByTestId('rules-approved-at')).toHaveText(shown(version!.user_approved_at));
    await expect(rules.getByTestId('rules-change-reason')).toHaveText(shown(version!.change_reason));
    // Assumed-cost and signal fields the spec does not carry must stay 未提供: printing
    // an engine default here would make a synthetic session look like a priced one.
    for (const [key, testId] of [
      ['commission_rate', 'rules-commission-rate'],
      ['slippage_rate', 'rules-slippage-rate'],
      ['signal_parameters', 'rules-signal-parameters'],
    ] as const) {
      await expect(
        rules.getByTestId(testId),
        `${key} is absent from the stored strategy_spec`
      ).toHaveText(key in spec ? String(spec[key]) : NOT_PROVIDED_UI);
    }

    // 手動模擬單: the session trades assets[0], the workspace is on a different
    // instrument, and manual-decision only fills assets[0] — so the honest target is
    // unknown and the panel must refuse rather than guess.
    await page.getByTestId('action-tab-order').click();
    const order = page.getByTestId('order-preview-panel');
    await expect(order.getByTestId('order-target')).toContainText('停用');
    const blocked = order.getByTestId('order-blocked');
    await expect(blocked).toBeVisible();
    await expect(blocked).toContainText(SYMBOL);
    await expect(blocked).toContainText(assets[0]);
    expect(
      assets[0] !== SYMBOL,
      `non-vacuity: the guard only has teeth while the session asset (${assets[0]}) differs from ${SYMBOL}`
    ).toBe(true);

    // Not cosmetic: with no trustworthy target the panel offers no form, no review
    // button, no submit, and — critically — no fallback to the endpoint that prices
    // from the newest stored bar instead of the replay cursor.
    for (const absent of [
      'order-action',
      'order-build-review',
      'order-submit-manual',
      'order-create-intent',
      'order-review',
    ]) {
      await expect(order.getByTestId(absent), `⑦ must not render ${absent} while blocked`).toHaveCount(0);
    }

    // The write classification below must not be vacuous, so this test makes one real
    // preference write with an ordinary click and then reads it back. The range is
    // chosen against what the server already stores: clicking the range that is already
    // saved is a genuine no-op, the shell's no-op filter drops it, and nothing is PUT.
    const layoutPath = '/api/v1/layouts/workspace';
    const storedLayout = await oracleGet<{ layout: { range?: string } | null }>(layoutPath);
    const storedRange = storedLayout.layout?.range ?? '';
    const nextRange = (['6M', '3M', '1Y', 'ALL'] as const).find((r) => r !== storedRange) as string;
    await page.locator('[data-testid="chart-range-buttons"]').getByText(nextRange).click();
    await expect
      .poll(() => oracleGet<{ layout: { range?: string } | null }>(layoutPath).then((r) => r.layout?.range), {
        timeout: 15_000,
        message: `the shell must persist the ${nextRange} range the UI clicked`,
      })
      .toBe(nextRange);
    await expect
      .poll(() => journey.writes.slice(writesBefore).filter(isPreference).length, {
        timeout: 10_000,
        message: 'a persisted layout change must appear as a preference write in the client log',
      })
      .toBeGreaterThan(0);

    // The refusal wrote nothing economic: the decision ledger and the session row are
    // unchanged, and the browser issued no decision/order/execution call.
    expect(decisionsBefore.length, 'the session must already hold decisions for this to compare').toBeGreaterThan(0);
    const decisionsAfter = await oracleGet<DecisionRow[]>(
      `/api/v1/simulations/${journey.sessionId}/decisions`
    );
    expect(decisionsAfter, 'a blocked order path must add no decision').toEqual(decisionsBefore);
    const after = await oracleGet<SessionRow>(`/api/v1/simulations/${journey.sessionId}`);
    expect(after, 'a blocked order path must not move the server cursor or its counters').toEqual(
      journey.session
    );
    expect(after.current_snapshot_date).toBe(cursor);

    const during = journey.writes.slice(writesBefore);
    expect(
      during.filter((call) => !isPreference(call)),
      '⑦ must issue no decision, order or execution write'
    ).toEqual([]);
    expect(
      during.filter(isPreference).length,
      'the filter above is only honest while a preference write really happened'
    ).toBeGreaterThan(0);
  });

  test('⑧ shows the real book the replay session is bound to, with its clocks and stored zeros', async () => {
    const writesBefore = journey.writes.length;
    await page.goto(`${runtime.frontendBase}/workspace`);
    await page.waitForLoadState('networkidle');

    const dock = page.getByTestId('evidence-dock');
    await expect(dock, 'a session-bound portfolio must render the evidence dock').toBeVisible();

    // Which book is this? Read the id the dock itself claims, then ask the server
    // whether that portfolio really belongs to the replay session under test. The
    // dock picks the portfolio; the oracle only verifies the claim it made.
    const portfolioId = (await page.getByTestId('evidence-portfolio-id').textContent())?.trim() as string;
    expect(portfolioId, '⑧ must name the portfolio it is speaking for').toBeTruthy();
    const portfolios = await oracleGet<
      { id: string; status: string; currency: string; simulation_session_id: string | null }[]
    >('/api/v1/paper-portfolio');
    const claimed = portfolios.find((p) => p.id === portfolioId) ?? null;
    expect(claimed, `⑧ names ${portfolioId}, which the portfolio endpoint does not return`).not.toBeNull();
    expect(
      claimed!.simulation_session_id,
      `⑧ presents portfolio ${portfolioId} as this replay's evidence, but the server binds it to session ${claimed!.simulation_session_id}`
    ).toBe(journey.sessionId);
    const currency = claimed!.currency;
    await expect(page.getByTestId('evidence-resolution')).toContainText(journey.sessionId.slice(0, 8));

    const positions = await oracleGet<PositionRow[]>(
      `/api/v1/paper-portfolio/${portfolioId}/positions`
    );
    const orders = await oracleGet<OrderRow[]>(`/api/v1/paper-portfolio/${portfolioId}/orders`);
    const journal = await oracleGet<JournalResponse>(
      `/api/v1/paper-portfolio/${portfolioId}/journal?format=json`
    );
    expect(positions.length, 'this book must hold positions for the dock comparison to mean anything').toBeGreaterThan(0);
    expect(orders.length, 'this book must hold orders').toBeGreaterThan(0);
    expect(journal.row_count, 'the journal envelope must carry rows').toBeGreaterThan(0);
    expect(journal.records).toHaveLength(journal.row_count);

    // 持倉 — the row set is the endpoint's, cell for cell.
    await page.getByTestId('tab-positions').click();
    await expect(page.getByTestId('tab-positions')).toHaveAttribute('aria-selected', 'true');
    const positionsTable = page.getByTestId('evidence-positions-table');
    await expect(positionsTable.locator('tbody tr')).toHaveCount(positions.length);
    const openPosition = positions.find((p) => p.status === 'open') ?? positions[0];
    const positionRow = positionsTable.locator('tr', {
      has: page.getByTestId(`evidence-position-${openPosition.id}`),
    });
    await expect(positionRow.locator('td').nth(0)).toHaveText(openPosition.asset_id);
    // Read the printed number back and compare it to the stored quantity, so a
    // formatter that rounds or drops digits fails here.
    const printedQty = Number((await positionRow.locator('td').nth(1).textContent())?.replace(/,/g, ''));
    expect(printedQty, 'the quantity cell must print the stored quantity').toBe(openPosition.quantity);
    const costCell = (await positionRow.locator('td').nth(2).textContent()) ?? '';
    if (openPosition.average_entry_price === null) {
      expect(costCell).toBe('未提供');
    } else {
      expect(printedMoney(costCell)).toBe(Number(openPosition.average_entry_price.toFixed(2)));
      expect(costCell).toContain(currency);
    }
    // A missing figure and an inapplicable figure are different strings, and the two
    // P&L columns never share one sentinel: this is the real endpoint returning null,
    // not a fixture chosen to make the point.
    const realizedCell = positionRow.locator('td').nth(3);
    const unrealizedCell = positionRow.locator('td').nth(4);
    if (openPosition.realized_pnl === null) {
      await expect(realizedCell).toHaveText('未提供');
    } else {
      // Signed: a stored loss must not render as a gain.
      expect(printedMoney((await realizedCell.textContent()) ?? '')).toBe(
        Number(openPosition.realized_pnl.toFixed(2))
      );
    }
    if (openPosition.unrealized_pnl === null) {
      await expect(unrealizedCell).toHaveText('無法計算');
    } else {
      expect(printedMoney((await unrealizedCell.textContent()) ?? '')).toBe(
        Number(openPosition.unrealized_pnl.toFixed(2))
      );
    }
    // Column-level: 無法計算 may only appear on rows whose stored unrealized figure
    // is null. A fabricated 0.00 (or a fabricated sentinel) breaks this equality.
    await expect(
      positionsTable.locator('tbody tr').filter({ hasText: '無法計算' })
    ).toHaveCount(positions.filter((p) => p.unrealized_pnl === null).length);

    // 訂單與成交 — three books, each with its own clock column.
    await page.getByTestId('tab-orders-fills').click();
    const ordersTable = page.getByTestId('evidence-orders-table');
    await expect(ordersTable).toBeVisible();
    await expect(ordersTable.locator('tbody tr')).toHaveCount(orders.length);
    const fillRows = journal.records.filter((r) => r.record_type === 'fill');
    const fillsTable = page.getByTestId('evidence-fills-table');
    await expect(fillsTable.locator('tbody tr')).toHaveCount(fillRows.length);

    const targetOrder = orders.find((o) => o.side === 'SELL') ?? orders[orders.length - 1];
    const targetFill = fillRows.find((r) => r.order_id === targetOrder.id);
    expect(targetFill, `the journal must carry a fill for order ${targetOrder.id}`).toBeDefined();
    const execCell = page.getByTestId(`evidence-order-exec-time-${targetOrder.id}`);
    await expect(execCell).toContainText(targetFill!.execution_date as string);
    // Non-vacuity: on this dataset the replay clock and the record's own created
    // instant are years apart, so a cell that printed created_at in the execution
    // column could not pass this.
    expect(
      (targetFill!.execution_date as string).slice(0, 4) !== (targetOrder.created_at as string).slice(0, 4),
      'the two clocks must be far enough apart for the separation check to have teeth'
    ).toBe(true);
    const targetRow = ordersTable.locator('tr', { has: page.getByTestId(`evidence-order-${targetOrder.id}`) });
    await expect(targetRow.locator('td').nth(1)).toHaveText('系統規則');
    expect(targetOrder.actor_type, 'this dataset records the order as system-authored').toBe('system');
    // The side label depends on which order we got (SELL or BUY fallback)
    const expectedSideLabel = targetOrder.side === 'SELL' ? '賣出' : '買入';
    await expect(targetRow.locator('td').nth(2)).toHaveText(expectedSideLabel);
    await expect(targetRow.locator('td').nth(5)).toContainText((targetOrder.created_at as string).slice(0, 10));

    // A stored 0.00 cost is a real zero and prints as one; only an absent value is 未提供.
    const targetFillRow = fillsTable.locator('tr', {
      has: page.getByTestId(`evidence-fill-${targetFill!.fill_id}`),
    });
    await expect(targetFillRow.locator('td').nth(4)).toHaveText(`0.00 ${currency}`);
    await expect(targetFillRow.locator('td').nth(5)).toHaveText(`0.00 ${currency}`);
    expect(targetFill!.slippage_amount, 'the stored fill carries a real zero slippage').toBe(0);
    expect(targetFill!.commission_amount, 'the stored fill carries a real zero commission').toBe(0);
    // The economic column carries the server's signed figure, not an absolute value
    // and not a browser-side recomputation.
    expect(printedMoney((await targetFillRow.locator('td').nth(6).textContent()) ?? '')).toBe(
      Number((targetFill!.net_cash_change as number).toFixed(2))
    );
    // Daily replay has no bar instant: 不適用, which is neither 未提供 nor 無法計算.
    await expect(page.getByTestId(`evidence-fill-bar-time-${targetFill!.fill_id}`)).toHaveText('不適用');
    expect(targetFill!.bar_timestamp, 'this session is daily, so the bar column must be empty').toBeNull();

    // Execution intents are a different book and this account has none — the panel
    // must say so rather than leave the section out or show the paper fills again.
    const intents = await oracleGet<{ intent_id: string }[]>('/api/v1/execution/intents');
    if (intents.length === 0) {
      await expect(page.getByTestId('evidence-no-intents')).toHaveText('沒有執行意向。');
    } else {
      await expect(page.getByTestId('evidence-intents-table')).toBeVisible();
    }

    // 自動交易日誌 + the real CSV. The table shows twelve curated columns; the 27-column
    // contract is proven in the detail drawer and in the exported file below.
    await page.getByTestId('tab-journal').click();
    const journalTable = page.getByTestId('evidence-journal-table');
    await expect(journalTable).toBeVisible();
    await expect(journalTable.locator('tbody tr')).toHaveCount(journal.row_count);
    await expect(journalTable.locator('thead th')).toHaveCount(12);
    expect(journal.columns.length, 'the journal contract carries 27 named columns').toBe(27);

    // The scope control must show which symbol it filters on, and the rows it leaves
    // must be exactly the rows that carry it — derived from the fetched data, because
    // the chart symbol and this book's traded asset need not agree.
    const scopeLabel = ((await page.getByTestId('evidence-scope-current').textContent()) ?? '').trim();
    const scopeSymbol = /^目前標的（(.+)）$/.exec(scopeLabel)?.[1] ?? '';
    expect(scopeSymbol, '⑧ must name the symbol the client-side scope filters on').toBeTruthy();
    const journalForSymbol = journal.records.filter((r) => r.asset_id === scopeSymbol).length;
    await page.getByTestId('evidence-scope-current').click();
    if (journalForSymbol === 0) {
      const empty = page.getByTestId('evidence-empty-日誌紀錄');
      await expect(empty).toBeVisible();
      await expect(journalTable).toHaveCount(0);
      // The panel says how many rows it hid rather than implying the book is empty.
      await expect(empty).toContainText(`在本面板已載入的 ${journal.row_count} 筆`);
    } else {
      await expect(journalTable.locator('tbody tr')).toHaveCount(journalForSymbol);
    }
    await expect(page.getByTestId('evidence-scope-note')).toContainText('不向後端查詢');
    // One-click clear restores the whole book.
    await page.getByTestId('evidence-scope-clear').click();
    await expect(journalTable.locator('tbody tr')).toHaveCount(journal.row_count);

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('evidence-export-csv').click(),
    ]);
    expect(download.suggestedFilename()).toBe(`trade-journal-${portfolioId}.csv`);
    const csv = readFileSync((await download.path()) as string, 'utf-8');
    const csvLines = csv.trim().split('\r\n');
    expect(csvLines[0].split(',')).toHaveLength(journal.columns.length);
    // Header + every row: the export is the whole portfolio, not the filtered view.
    expect(csvLines.length).toBe(journal.row_count + 1);
    await expect(page.getByTestId('evidence-export-scope-note')).toContainText('匯出範圍：整個組合');
    await expect(page.getByTestId('evidence-export-csv')).toContainText('匯出整個組合');
    expect(csvLines.some((line) => line.includes(targetFill!.fill_id as string)), 'the fill must be in the export').toBe(true);
    expect(csvLines[0]).toContain('simulated_fill_reason');

    // Detail drawer: keyed by the exact record id it was opened from, and rendering
    // one line per stored contract column — that is where the 27 columns surface.
    const journalLink = page.getByTestId(`evidence-journal-row-${targetFill!.fill_id}`);
    await journalLink.click();
    const drawer = page.getByTestId('evidence-drawer');
    await expect(drawer).toBeVisible();
    await expect(page.getByTestId('evidence-drawer-record-id')).toHaveText(targetFill!.fill_id as string);
    await expect(drawer.locator('.ws-evidence-drawer-row')).toHaveCount(journal.columns.length);
    for (const key of ['execution_date', 'simulated_fill_reason', 'asset_id', 'order_id']) {
      await expect(drawer.getByTestId(`drawer-journal-${key}`)).toContainText(String(targetFill![key]));
    }
    // A null bar_timestamp is 不適用 in the drawer too, never an empty string or 0.
    await expect(drawer.getByTestId('drawer-journal-bar_timestamp')).toHaveText('不適用');
    await expect(drawer).toContainText('column_semantics');
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
    // Focus returns to the row that opened it.
    await expect(journalLink).toBeFocused();

    // 結果 — only fields the portfolio detail endpoint really returns.
    const detail = await oracleGet<{
      initial_virtual_cash: number;
      current_virtual_cash: number;
      total_realized_pnl: number | null;
      total_unrealized_pnl: number | null;
      valuation?: Record<string, unknown> | null;
    }>(`/api/v1/paper-portfolio/${portfolioId}`);
    const decisions = await oracleGet<unknown[]>(`/api/v1/paper-portfolio/${portfolioId}/decisions`);
    await page.getByTestId('tab-results').click();
    const results = page.getByTestId('evidence-results-table');
    await expect(results).toBeVisible();
    const initialCash = (await page.getByTestId('results-initial-cash').textContent()) ?? '';
    const currentCash = (await page.getByTestId('results-cash').textContent()) ?? '';
    expect(printedMoney(initialCash)).toBe(Number(detail.initial_virtual_cash.toFixed(2)));
    expect(printedMoney(currentCash)).toBe(Number(detail.current_virtual_cash.toFixed(2)));
    expect(initialCash).toContain(currency);
    await expect(page.getByTestId('results-open-positions')).toHaveText(
      String(positions.filter((p) => p.status === 'open').length)
    );
    await expect(page.getByTestId('results-order-count')).toHaveText(String(orders.length));
    await expect(page.getByTestId('results-fill-count')).toHaveText(String(fillRows.length));
    await expect(page.getByTestId('results-decision-count')).toHaveText(String(decisions.length));
    if (detail.total_unrealized_pnl === null) {
      await expect(page.getByTestId('results-unrealized-pnl')).toHaveText('無法計算');
    } else {
      expect(printedMoney((await page.getByTestId('results-unrealized-pnl').textContent()) ?? '')).toBe(
        Number(detail.total_unrealized_pnl.toFixed(2))
      );
    }
    // The disclosure that no portfolio-level performance endpoint exists must stay
    // visible: backtest metrics belong to the run, not to this book.
    await expect(page.getByTestId('evidence-results-gap')).toContainText('後端沒有組合層級的成果端點');

    // The joint ⑤/⑧ floors, measured under the real runtime's own chrome (app bar,
    // context bar and the replay strip inside ⑤ all take height here, unlike the mocked
    // tier-A shell). A fixed 240px band was mutually impossible with ⑤'s ~320px canvas on
    // this runtime — the column held 776px and the pair needs 810 — so the shell now
    // relaxes the pair together instead of letting one clip the other. Before the fix this
    // band painted 228.6px and clipped 12 of the dock's 25 controls behind overflow:hidden
    // with no scroll path to reach them.
    await page.setViewportSize({ width: 1440, height: 900 });
    const dockGeom = () =>
      page.getByTestId('evidence-dock').evaluate((el) => {
        const panel = el.closest('[data-panel-id]') as HTMLElement;
        const body = el.querySelector('.ws-panel-body') as HTMLElement;
        const clip = panel.getBoundingClientRect();
        const link = el.querySelector('[data-testid="evidence-results-link"]') as HTMLElement;
        const r = link.getBoundingClientRect();
        const chartPanel = document
          .querySelector('[data-testid="price-chart"]')
          ?.closest('[data-panel-id]') as HTMLElement;
        const canvas = document.querySelector('.ws-chart-canvas') as HTMLElement;
        const canvasH = canvas.getBoundingClientRect().height;
        const chartH = chartPanel.getBoundingClientRect().height;
        return {
          bandH: Math.round(clip.height),
          canvasH: Math.round(canvasH),
          columnH: Math.round(chartH + clip.height + 8),
          chromeH: Math.round(chartH - canvasH),
          roomy: Math.round(chartH + clip.height + 8) >= Math.round(chartH - canvasH) + 320 + 8 + 240,
          overflow: panel.scrollHeight - panel.clientHeight,
          scrollable: ['auto', 'scroll'].includes(getComputedStyle(body).overflowY),
          bodyH: body.clientHeight,
          contentH: body.scrollHeight,
          linkInsideBand: r.top >= clip.top - 1 && r.bottom <= clip.bottom + 1,
        };
      });
    await expect
      .poll(async () => {
        const g = await dockGeom();
        return g.roomy ? g.bandH >= 240 && g.canvasH >= 320 : g.bandH >= 160 && g.canvasH >= 200;
      }, {
        timeout: 15_000,
        message: '⑤ and ⑧ must hold their joint floors for this column height on the real runtime',
      })
      .toBe(true);

    const band = await dockGeom();
    expect(band.canvasH, `⑤ keeps its canvas floor (${band.canvasH}px, roomy=${band.roomy})`).toBeGreaterThanOrEqual(
      band.roomy ? 320 : 200
    );
    expect(band.bandH, '⑧ keeps its band floor').toBeGreaterThanOrEqual(band.roomy ? 240 : 160);
    expect(band.overflow, 'the band must never overflow its own clip').toBeLessThanOrEqual(1);
    expect(band.scrollable, 'the dock body is the scroll container for taller content').toBe(true);
    expect(band.contentH, 'the real book has more rows than the band shows at once').toBeGreaterThan(
      band.bodyH
    );

    // a real wheel gesture, repeated until the last control of the tab is inside the band
    const dockBox = await page.getByTestId('evidence-dock').boundingBox();
    await page.mouse.move(dockBox!.x + dockBox!.width / 2, dockBox!.y + dockBox!.height - 12);
    await expect
      .poll(
        async () => {
          await page.mouse.wheel(0, 300);
          return (await dockGeom()).linkInsideBand;
        },
        { timeout: 15_000, message: 'scrolling must bring 組合完整頁面 into the band' }
      )
      .toBe(true);

    // Read-only journey: nothing above created, changed or cancelled anything. The CSV
    // is a GET. Layout preference saves are legitimate here — switching evidence tabs
    // changes the persisted bottomTab — so they are classified instead of hidden by a
    // narrower allowlist, and everything that is not one must be absent. The positive
    // proof that a layout change does PUT and round-trips is the ⑦ journey above plus
    // the tier-A persistence test.
    const during = journey.writes.slice(writesBefore);
    const isPreference = (call: string) => /^PUT \/api\/v1\/layouts\/\w+$/.test(call);
    expect(
      during.filter((call) => !isPreference(call)),
      '⑧ must issue no decision, order, execution or export write — the CSV is a GET'
    ).toEqual([]);
  });

  test('one real 下一日 click moves the server cursor and the chart window together', async () => {
    const beforeCursor = journey.session?.current_snapshot_date as string;
    const beforeObserved = journey.session?.observed_days as number;

    const advance = page.getByTestId('replay-advance');
    await expect(advance).toHaveText('下一日');
    const requestsBefore = journey.bars.length;
    // The click's POST is what moves the cursor, so the server read must not race it.
    const [advanceResponse] = await Promise.all([
      page.waitForResponse(
        (res) => res.url().includes(`/api/v1/simulations/${journey.sessionId}/advance`),
        { timeout: 20_000 }
      ),
      advance.click(),
    ]);
    expect(advanceResponse.status(), 'the real advance request must succeed').toBe(200);
    await expect(page.getByTestId('replay-clock-value')).not.toHaveText(beforeCursor, { timeout: 15_000 });

    // The server, not the browser, decides the next cursor.
    const after = await oracleGet<SessionRow>(`/api/v1/simulations/${journey.sessionId}`);
    const newCursor = after.current_snapshot_date as string;
    expect(
      newCursor > beforeCursor,
      `server cursor must move forward from ${beforeCursor}, got ${newCursor}`
    ).toBe(true);
    expect(after.observed_days, 'the server must record one more observed point').toBe(beforeObserved + 1);
    expect(
      after.data_cutoff_at === journey.session?.data_cutoff_at,
      'advancing one step must not extend the dataset cutoff'
    ).toBe(true);

    // The UI adopts the cursor carried by the advance response. A locally incremented
    // next-day clock would show a different date here.
    await expect(page.getByTestId('replay-clock-value')).toHaveText(newCursor);
    const summaryToggle = page
      .getByTestId('replay-controller')
      .getByTestId('replay-meta-toggle');
    await summaryToggle.click();
    await expect(page.getByTestId('replay-bars-end')).toHaveText(newCursor);
    await expect(page.getByTestId('replay-summary')).toContainText(`${beforeObserved + 1} 個觀測點`);
    await expect(page.getByTestId('ctx-replay-clock')).toContainText(newCursor);

    await expect.poll(() => journey.bars.length).toBeGreaterThan(requestsBefore);
    const latest = journey.bars[journey.bars.length - 1];
    expect(latest.end).toBe(newCursor);
    expect(latest.start! < newCursor).toBe(true);
    expect(latest.dates[latest.dates.length - 1] <= newCursor).toBe(true);

    const lastVisible = storedThrough(newCursor);
    await expect(page.getByTestId('chart-meta-line')).toContainText(`數據截至 ${lastVisible.date}`);
    await expect(page.getByTestId('instrument-last-price')).toContainText(lastVisible.close.toFixed(2));
  });
});
