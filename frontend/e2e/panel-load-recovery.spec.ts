import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Tracked regression for the personal-strategy panel's load recovery.
 *
 * WHAT THIS PROVES (all server responses are intercepted; no backend, no DB)
 *   - a request that never answers ends the "載入…" state at the panel's own
 *     deadline, shows a localised error and a retry, and does not spin;
 *   - a rejected load is reported as a failed request, not as "no strategies";
 *   - a stage that rejects names the stage ("整體進度：…"), and retrying it
 *     re-runs only that stage instead of reloading the version list;
 *   - a slow-but-successful response shows no premature error and the loading
 *     state settles;
 *   - a late reply for version A cannot overwrite version B after a switch;
 *   - an account switch (a real document reload, as the shell does it) sends
 *     only the new identity and renders only the new account's rows;
 *   - retrying keeps an unsubmitted correction note on screen;
 *   - background refresh stops after three consecutive failures instead of
 *     retrying forever.
 *
 * WHAT THIS DOES NOT PROVE
 *   - that the original one-off stall had this cause: it was never reproduced,
 *     so the root cause stays unknown. These tests pin the recovery behaviour,
 *     not a diagnosis.
 *   - anything about the backend: the payloads are synthetic fixtures.
 *
 * GATING
 *   E2E_FRONTEND_BASE must point at a frontend whose API calls the routes below
 *   can intercept, e.g. http://127.0.0.1:3100.
 */

const BASE = process.env.E2E_FRONTEND_BASE ?? '';
const READY = BASE.length > 0;
const SKIP_REASON = 'requires E2E_FRONTEND_BASE pointing at the frontend under test';

const TOKEN_A = 'e2e-panel-recovery-account-a';
const TOKEN_B = 'e2e-panel-recovery-account-b';

const V1 = 'aaaaaaaa-0000-0000-0000-000000000001';
const V2 = 'bbbbbbbb-0000-0000-0000-000000000002';

const ROUTE = {
  experiments: /\/api\/v1\/experiments\/?(\?.*)?$/,
  review: /\/api\/v1\/decision-review\/?(\?.*)?$/,
  preview: /\/api\/v1\/ml\/personal\/snapshots\/preview\/?$/,
  snapshots: /\/api\/v1\/ml\/personal\/snapshots\/?(\?.*)?$/,
  personalModels: /\/api\/v1\/ml\/personal\/models\/?(\?.*)?$/,
  journey: /\/api\/v1\/ml\/personal\/journey\/?(\?.*)?$/,
  trainingJobs: /\/api\/v1\/training\/jobs\/?(\?.*)?$/,
  instruments: /\/api\/v1\/instruments\/?(\?.*)?$/,
  mlModels: /\/api\/v1\/ml\/models\/?(\?.*)?$/,
  mlEntitlement: /\/api\/v1\/ml\/entitlement\/?(\?.*)?$/,
  capabilities: /\/api\/v1\/ml\/capabilities\/?$/,
  eligibility: /\/api\/v1\/ml\/personalization\/eligibility\/?$/,
  catchAll: /\/api\/v1\/.*/,
};

type Mode = 'ok' | 'reject' | 'hang';

interface LoggedRequest {
  method: string;
  path: string;
  auth: string | null;
}

interface State {
  experiments: Mode;
  review: Mode;
  journey: Mode;
  /** One-shot delay applied to the next journey request for this version. */
  slowJourneyVersion: string | null;
  journeyDelayMs: number;
  /** Journey payload that keeps the background poller alive. */
  workerPending: boolean;
  account: 'a' | 'b';
  requests: LoggedRequest[];
  counts: Record<string, number>;
  unexpected: string[];
}

function state(): State {
  return {
    experiments: 'ok',
    review: 'ok',
    journey: 'ok',
    slowJourneyVersion: null,
    journeyDelayMs: 3000,
    workerPending: false,
    account: 'a',
    requests: [],
    counts: {},
    unexpected: [],
  };
}

const marker = (versionId: string) => (versionId === V1 ? 'ALPHA' : 'BETA');

function experimentsBody() {
  return [
    { id: 'exp-a', title: '策略甲', current_version_id: V1 },
    { id: 'exp-b', title: '策略乙', current_version_id: V2 },
  ];
}

function reviewBody(versionId: string, account: string) {
  const total = versionId === V1 ? 5 : 2;
  return {
    strategy_version_id: versionId,
    summary: {
      total_current_decisions: total,
      confirmed: 1,
      excluded: 0,
      unreviewed: total - 1,
    },
    decisions: [
      {
        decision_event_id: `evt-${marker(versionId)}-${account}`,
        simulation_session_id: 'sess-1',
        strategy_version_id: versionId,
        as_known_context: {
          instrument_id: 'US:AAPL',
          step_number: 1,
          snapshot_date: '2026-10-01',
          data_cutoff_at: null,
        },
        recorded_action: 'buy',
        quantity: 10,
        rationale: null,
        recorded_at: null,
        label: null,
        revision_count: 0,
        usable_for_training: false,
      },
    ],
    note: `覆核說明 ${marker(versionId)} · ${account}`,
  };
}

function previewBody(versionId: string) {
  return {
    snapshot_id: null,
    status: 'ready',
    strategy_version_id: versionId,
    included_count: 5,
    excluded_count: 0,
    exclusion_breakdown: {},
    exclusion_labels_zh: {},
    class_distribution: {},
    date_range: { start: null, end: null },
    data_cutoff_at: null,
    feature_schema_version: 'v1',
    feature_names: [],
    checksum: `checksum-${marker(versionId)}`,
    can_build: true,
    reasons: [],
    consent: { consent_version: null, in_scope: false },
    note: `預覽說明 ${marker(versionId)}`,
  };
}

function journeyBody(versionId: string, account: string, pending: boolean) {
  return {
    strategy_version_id: versionId,
    generated_at: '2026-10-30T00:00:00Z',
    strategy: {
      title: `策略 ${marker(versionId)}`,
      experiment_id: 'exp-a',
      version_number: 1,
      instruments: ['US:AAPL'],
      period: '2026-01-01..2026-06-30',
      synthetic: true,
    },
    counts: {
      total_current_decisions: 5,
      confirmed: 1,
      excluded: 0,
      unreviewed: 4,
      usable_rows: 1,
      minimum_usable_rows: 3,
      snapshots: 0,
      training_jobs: pending ? 1 : 0,
      model_versions: 0,
    },
    consent: { id: null, in_scope: false, consent_version: null },
    steps: [
      {
        key: 'review',
        title_zh: '決策覆核',
        state: 'in_progress',
        summary_zh: `旅程標記 ${marker(versionId)} · ${account}`,
      },
    ],
    missing: [],
    next_action: pending
      ? { kind: 'wait_worker', label_zh: '等待訓練完成', detail_zh: '背景工作正在處理此策略的訓練。' }
      : { kind: 'review', label_zh: '覆核你的決策', detail_zh: '先確認決策，再建立凍結快照。' },
    worker: {
      state: pending ? 'busy' : 'idle',
      label_zh: pending ? '工作中' : '閒置',
      detail_zh: pending ? '有 1 個待處理工作。' : '沒有待處理工作。',
      pending_jobs: pending ? 1 : 0,
      running_jobs: 0,
    },
    not_claimed: ['不代表模型有效或有利潤。'],
  };
}

function quotaBody() {
  return {
    quota_type: 'ai_usage',
    limit: 10,
    used: 0,
    reserved: 0,
    remaining: 10,
    unlimited: false,
    entitlement_active: true,
    source: 'entitlement',
    expiry_date: null,
    support_tier: null,
  };
}

function eligibilityBody() {
  return {
    capability_status: 'AVAILABLE',
    capability_status_zh: '已提供',
    eligible: false,
    status: 'INSUFFICIENT_DATA',
    reasons: [],
    requirements: {},
    consent: {
      granted: false,
      consent_version: null,
      scope: { strategy_version_ids: [] },
      granted_at: null,
    },
    dataset: {
      total_recorded_events: 0,
      eligible_events: 0,
      excluded: {
        superseded_revisions: 0,
        outside_consent_scope: 0,
        missing_reason: 0,
        missing_snapshot_context: 0,
      },
      distinct_sessions: 0,
      distinct_snapshot_dates: 0,
      action_counts: {},
      feature_coverage: { covered: 0, considered: 0, ratio: null },
      future_leakage_events: 0,
    },
    strategies: [],
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

/** Holds the request open past the panel's deadline without ever answering. */
async function hang(route: Route) {
  await new Promise((resolve) => setTimeout(resolve, 60000));
  await json(route, { detail: 'never answered' }).catch(() => undefined);
}

function versionOf(url: string): string {
  return new URL(url).searchParams.get('strategy_version_id') ?? '';
}

function record(state: State, key: string, route: Route) {
  const request = route.request();
  const url = new URL(request.url());
  state.requests.push({
    method: request.method(),
    path: `${url.pathname}${url.search}`,
    auth: request.headers()['authorization'] ?? null,
  });
  state.counts[key] = (state.counts[key] ?? 0) + 1;
}

async function install(page: Page, state: State) {
  // Registered first so every specific route below wins over it.
  await page.route(ROUTE.catchAll, async (route) => {
    const url = new URL(route.request().url());
    state.unexpected.push(`${route.request().method()} ${url.pathname}${url.search}`);
    await json(route, { detail: 'unexpected request in panel recovery test' }, 404);
  });

  await page.route(ROUTE.experiments, async (route) => {
    record(state, 'experiments', route);
    if (state.experiments === 'hang') return hang(route);
    if (state.experiments === 'reject') {
      return json(route, { detail: '模擬策略版本載入失敗' }, 500);
    }
    return json(route, experimentsBody());
  });

  await page.route(ROUTE.review, async (route) => {
    const versionId = versionOf(route.request().url());
    record(state, `review:${versionId}`, route);
    if (state.review === 'reject') return json(route, { detail: '模擬覆核載入失敗' }, 500);
    return json(route, reviewBody(versionId, state.account));
  });

  await page.route(ROUTE.preview, async (route) => {
    const body = route.request().postDataJSON() as { strategy_version_id?: string };
    const versionId = body.strategy_version_id ?? '';
    record(state, `preview:${versionId}`, route);
    return json(route, previewBody(versionId));
  });

  await page.route(ROUTE.snapshots, async (route) => {
    record(state, 'snapshots', route);
    return json(route, { snapshots: [] });
  });

  await page.route(ROUTE.personalModels, async (route) => {
    record(state, 'personalModels', route);
    return json(route, { models: [] });
  });

  await page.route(ROUTE.journey, async (route) => {
    const versionId = versionOf(route.request().url());
    record(state, `journey:${versionId}`, route);
    if (state.journey === 'hang') return hang(route);
    if (state.journey === 'reject') return json(route, { detail: '模擬旅程載入失敗' }, 500);
    if (state.slowJourneyVersion === versionId) {
      state.slowJourneyVersion = null;
      await new Promise((resolve) => setTimeout(resolve, state.journeyDelayMs));
    }
    return json(route, journeyBody(versionId, state.account, state.workerPending));
  });

  await page.route(ROUTE.trainingJobs, async (route) => {
    record(state, 'trainingJobs', route);
    return json(route, []);
  });

  await page.route(ROUTE.instruments, async (route) => {
    record(state, 'instruments', route);
    return json(route, { instruments: [] });
  });

  await page.route(ROUTE.mlModels, async (route) => {
    record(state, 'mlModels', route);
    return json(route, { models: [] });
  });

  await page.route(ROUTE.mlEntitlement, async (route) => {
    record(state, 'mlEntitlement', route);
    return json(route, quotaBody());
  });

  await page.route(ROUTE.capabilities, async (route) => {
    record(state, 'capabilities', route);
    return json(route, { capabilities: [] });
  });

  await page.route(ROUTE.eligibility, async (route) => {
    record(state, 'eligibility', route);
    return json(route, eligibilityBody());
  });
}

async function open(page: Page, token = TOKEN_A): Promise<void> {
  // The initial token is only seeded when absent so a test can replace it and
  // reload without the init script putting the old identity back.
  await page.addInitScript((value: string) => {
    if (!localStorage.getItem('token')) localStorage.setItem('token', value);
  }, token);
  await page.goto(`${BASE}/training`, { waitUntil: 'domcontentloaded' });
}

const panel = (page: Page) => page.getByTestId('personal-learning-panel');

async function expectLoaded(page: Page, versionId: string) {
  await expect(panel(page)).toBeVisible();
  await expect(page.getByTestId('pl-strategy-select')).toBeVisible();
  await expect(page.getByTestId('pl-review')).toContainText(`覆核說明 ${marker(versionId)}`);
}

test.describe('panel load recovery', () => {
  test.skip(!READY, SKIP_REASON);

  test('a request that never answers ends the loading state with a retry', async ({ page }) => {
    const s = state();
    s.experiments = 'hang';
    await install(page, s);

    await open(page);
    await expect(page.getByTestId('pl-loading')).toBeVisible();
    // Still inside the panel's own deadline: no error yet, and above all the
    // card has not silently stayed on "載入…" forever.
    await page.waitForTimeout(1500);
    await expect(page.getByTestId('pl-loading')).toBeVisible();
    await expect(page.getByTestId('pl-error')).toHaveCount(0);

    await expect(page.getByTestId('pl-error')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('pl-error')).toContainText(
      '載入可用的策略版本在 12 秒內未有回應',
    );
    await expect(page.getByTestId('pl-loading')).toHaveCount(0);
    await expect(page.getByTestId('pl-retry')).toBeVisible();

    // No automatic retry: the hung request is counted once per caller and the
    // count does not grow while the error is on screen.
    const before = s.counts.experiments;
    await page.waitForTimeout(2000);
    expect(s.counts.experiments).toBe(before);
    expect(s.unexpected).toEqual([]);

    s.experiments = 'ok';
    await page.getByTestId('pl-retry').click();
    await expectLoaded(page, V1);
    await expect(page.getByTestId('pl-error')).toHaveCount(0);
  });

  test('a rejected version list is a failed request, not an empty account', async ({ page }) => {
    const s = state();
    s.experiments = 'reject';
    await install(page, s);

    await open(page);
    await expect(page.getByTestId('pl-error')).toBeVisible();
    await expect(page.getByTestId('pl-error')).toContainText('模擬策略版本載入失敗');
    await expect(page.getByTestId('pl-loading')).toHaveCount(0);
    await expect(page.getByTestId('pl-no-strategy')).toHaveCount(0);
    await expect(page.getByTestId('pl-retry')).toBeVisible();

    // Reading a panel must never write anything.
    const writes = s.requests.filter((r) => r.method !== 'GET' && r.method !== 'OPTIONS');
    expect(writes).toEqual([]);

    s.experiments = 'ok';
    await page.getByTestId('pl-retry').click();
    await expectLoaded(page, V1);
    await expect(page.getByTestId('pl-error')).toHaveCount(0);
  });

  test('a rejected stage is named, and retry reruns only that stage', async ({ page }) => {
    const s = state();
    s.journey = 'reject';
    await install(page, s);

    await open(page);
    await expect(page.getByTestId('pl-error')).toBeVisible();
    await expect(page.getByTestId('pl-error')).toContainText('整體進度：模擬旅程載入失敗');
    await expect(page.getByTestId('pl-retry')).toBeVisible();
    // The other stages answered; the failure did not invent an empty strategy.
    await expect(page.getByTestId('pl-no-strategy')).toHaveCount(0);

    const versionsBefore = s.counts.experiments;
    s.journey = 'ok';
    await page.getByTestId('pl-retry').click();
    await expectLoaded(page, V1);
    await expect(page.getByTestId('pl-error')).toHaveCount(0);
    // A strategy-stage retry must not reload the version list (which would
    // discard the user's selection and re-query another endpoint).
    expect(s.counts.experiments).toBe(versionsBefore);
  });

  test('a slow response shows no premature error and settles', async ({ page }) => {
    const s = state();
    s.journeyDelayMs = 3000;
    s.slowJourneyVersion = V1;
    await install(page, s);

    await open(page);
    await expect(page.getByTestId('pl-strategy-loading')).toBeVisible();
    await expect(page.getByTestId('pl-error')).toHaveCount(0);

    await expect(page.getByTestId('pl-review')).toContainText('覆核說明 ALPHA', {
      timeout: 10000,
    });
    await expect(page.getByTestId('pl-strategy-loading')).toHaveCount(0);
    await expect(page.getByTestId('pl-error')).toHaveCount(0);
  });

  test('a late reply for the previous version cannot overwrite the new one', async ({ page }) => {
    const s = state();
    await install(page, s);

    await open(page);
    await expectLoaded(page, V1);
    expect(await page.getByTestId('pl-strategy-select').inputValue()).toBe(V1);

    // A real background refresh of V1 (the consent panel triggers it) is held
    // open, then the user switches to V2 while it is still in flight.
    s.slowJourneyVersion = V1;
    const staleStarted = s.counts[`journey:${V1}`] ?? 0;
    await page.evaluate(() => {
      window.dispatchEvent(new Event('alethra:consent-changed'));
    });
    await expect
      .poll(() => s.counts[`journey:${V1}`] ?? 0, { timeout: 5000 })
      .toBe(staleStarted + 1);

    await page.getByTestId('pl-strategy-select').selectOption(V2);
    await expectLoaded(page, V2);

    // Let the stale V1 reply land; it must be dropped, not applied.
    await page.waitForTimeout(s.journeyDelayMs + 1000);
    await expect(page.getByTestId('pl-review')).toContainText('覆核說明 BETA');
    await expect(page.getByTestId('pl-review')).not.toContainText('覆核說明 ALPHA');
    await expect(page.getByTestId('pl-journey')).toContainText('旅程標記 BETA · a');
    expect(await page.getByTestId('pl-strategy-select').inputValue()).toBe(V2);
  });

  test('an account switch re-queries with the new identity only', async ({ page }) => {
    const s = state();
    await install(page, s);

    await open(page);
    await expect(page.getByTestId('pl-review-row-evt-ALPHA-a')).toBeVisible();

    const mark = s.requests.length;
    await page.evaluate((token: string) => localStorage.setItem('token', token), TOKEN_B);
    s.account = 'b';
    await page.reload({ waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('pl-review')).toContainText('覆核說明 ALPHA · b');
    await expect(page.getByTestId('pl-review-row-evt-ALPHA-b')).toBeVisible();
    await expect(page.getByTestId('pl-review-row-evt-ALPHA-a')).toHaveCount(0);

    const after = s.requests.slice(mark).filter((r) => r.path.startsWith('/api/v1/'));
    expect(after.length).toBeGreaterThan(0);
    expect(after.every((r) => r.auth === `Bearer ${TOKEN_B}`)).toBe(true);
    expect(after.some((r) => r.auth === `Bearer ${TOKEN_A}`)).toBe(false);
  });

  test('retrying keeps an unsubmitted correction note', async ({ page }) => {
    const s = state();
    await install(page, s);

    await open(page);
    await expectLoaded(page, V1);

    await page.getByTestId('pl-correct-evt-ALPHA-a').click();
    await page.getByTestId('pl-correction-note').fill('未提交的更正原因');

    // A background refresh fails; the panel shows an error and a retry.
    s.journey = 'reject';
    await page.evaluate(() => {
      window.dispatchEvent(new Event('alethra:consent-changed'));
    });
    await expect(page.getByTestId('pl-error')).toBeVisible();
    await expect(page.getByTestId('pl-correction-note')).toHaveValue('未提交的更正原因');

    s.journey = 'ok';
    await page.getByTestId('pl-retry').click();
    await expect(page.getByTestId('pl-error')).toHaveCount(0);
    await expect(page.getByTestId('pl-correction-save')).toBeVisible();
    await expect(page.getByTestId('pl-correction-note')).toHaveValue('未提交的更正原因');
    expect(await page.getByTestId('pl-strategy-select').inputValue()).toBe(V1);
  });

  test('background refresh pauses after three failures instead of looping', async ({ page }) => {
    const s = state();
    s.workerPending = true;
    await install(page, s);

    await open(page);
    await expectLoaded(page, V1);

    s.journey = 'reject';
    await expect(page.getByTestId('pl-poll-paused')).toBeVisible({ timeout: 20000 });
    await expect(page.getByTestId('pl-error')).toBeVisible();

    const settled = s.counts[`journey:${V1}`] ?? 0;
    expect(settled).toBeGreaterThanOrEqual(4);
    expect(settled).toBeLessThanOrEqual(6);

    await page.waitForTimeout(4500);
    expect(s.counts[`journey:${V1}`] ?? 0).toBe(settled);

    s.journey = 'ok';
    await page.getByTestId('pl-retry').click();
    await expect(page.getByTestId('pl-poll-paused')).toHaveCount(0);
    await expect(page.getByTestId('pl-error')).toHaveCount(0);
  });
});
