import { spawnSync } from 'child_process';
import crypto from 'crypto';
import path from 'path';
import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for the C-round first-use fixes:
 *   - the empty account gets concrete entries instead of a dead end,
 *   - the consent panel does not pre-select the scope or the acknowledgement,
 *   - granting consent refreshes the sibling journey panel without a reload,
 *   - an unlinked paper account reads as "not linked", never as a -100% loss,
 *   - the journey's next-step anchors exist on the page,
 *   - a queued training job survives a reload and blocks duplicate submits.
 *
 * DATA SETUP vs UI UNDER TEST:
 *   - beforeAll runs the tracked seed backend/scripts/seed_candidate_scenarios.py
 *     against the DISPOSABLE candidate database only. The seed refuses any
 *     DATABASE_URL that is not postgresql containing "alethra_personal_e2e",
 *     and it is create-if-missing so a participant's progress is never cleared.
 *   - It writes the synthetic thin fixture (1 locked version, 5 decisions, no
 *     consent) and nothing else; every product mutation below is a real UI
 *     gesture (login, checkbox, grant, revoke, navigation).
 *   - The job-restore test routes the training-jobs and snapshot-list responses
 *     to a synthetic pending job instead of creating jobs or models, so no
 *     training data is produced by the suite.
 *   - The spec is repeatable: any consent left by an earlier run is revoked
 *     through the UI first, and revoked again at the end so the account is
 *     back in its pre-consent first-use state.
 *   - Waits are expect()/waitForURL on server state; there are no fixed sleeps.
 *
 * Required environment (never the shared runtime):
 *   E2E_FIRST_USE_DB_URL        postgresql URL of a disposable alethra_personal_e2e* DB
 *   E2E_FIRST_USE_EMPTY_EMAIL / E2E_FIRST_USE_EMPTY_PASSWORD
 *   E2E_FIRST_USE_THIN_EMAIL  / E2E_FIRST_USE_THIN_PASSWORD
 *   E2E_FIRST_USE_PYTHON        interpreter that can import app.* (default python3)
 *   E2E_FRONTEND_BASE           default http://127.0.0.1:3100
 *
 * The file skips itself when the DB URL is absent or not disposable, so it can
 * never touch the shared acceptance runtime.
 */

const DB_URL = process.env.E2E_FIRST_USE_DB_URL ?? '';
const READY = /^postgresql/.test(DB_URL) && DB_URL.includes('alethra_personal_e2e');
const SKIP_REASON =
  'requires E2E_FIRST_USE_DB_URL pointing at a disposable alethra_personal_e2e* database';

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const BACKEND_DIR = path.join(REPO_ROOT, 'backend');
const PYTHON = process.env.E2E_FIRST_USE_PYTHON ?? 'python3';

const EMPTY = {
  email: process.env.E2E_FIRST_USE_EMPTY_EMAIL ?? '',
  password: process.env.E2E_FIRST_USE_EMPTY_PASSWORD ?? '',
};
const THIN = {
  email: process.env.E2E_FIRST_USE_THIN_EMAIL ?? '',
  password: process.env.E2E_FIRST_USE_THIN_PASSWORD ?? '',
};

test.beforeAll(() => {
  if (!READY) return;
  const seed = spawnSync(PYTHON, ['scripts/seed_candidate_scenarios.py'], {
    cwd: BACKEND_DIR,
    env: {
      ...process.env,
      DATABASE_URL: DB_URL,
      E2E_EMPTY_EMAIL: EMPTY.email,
      E2E_EMPTY_PASSWORD: EMPTY.password,
      E2E_THIN_EMAIL: THIN.email,
      E2E_THIN_PASSWORD: THIN.password,
      PYTHONPATH: BACKEND_DIR,
    },
    encoding: 'utf-8',
  });
  if (seed.status !== 0) {
    throw new Error(`first-use seed failed: ${seed.stderr || seed.stdout}`);
  }
});

async function login(page: Page, email: string, password: string) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  const zone = page.locator('.login-form-zone');
  await zone.waitFor({ state: 'visible' });
  await page.waitForLoadState('networkidle').catch(() => {});
  const emailInput = zone.locator('input[type="email"]');
  const passwordInput = zone.locator('input[type="password"]');
  // Next dev can finish hydrating after the inputs exist and wipe an early
  // keystroke, so fill until the controlled values actually stick.
  await expect(async () => {
    await emailInput.fill(email);
    await passwordInput.fill(password);
    expect(await emailInput.inputValue()).toBe(email);
    expect(await passwordInput.inputValue()).toBe(password);
  }).toPass({ timeout: 15000 });
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30000 });
}

async function openTraining(page: Page) {
  await page.goto('/training', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-testid="personalization-panel"]')).toBeVisible();
  await expect(page.locator('[data-testid="personalization-eligibility"]')).toBeVisible();
}

const NAMESPACE_URL = '6ba7b811-9dad-11d1-80b4-00c04fd430c8';

function uuid5(namespace: string, name: string): string {
  const ns = Buffer.from(namespace.replace(/-/g, ''), 'hex');
  const hash = crypto
    .createHash('sha1')
    .update(Buffer.concat([ns, Buffer.from(name, 'utf8')]))
    .digest();
  const bytes = Buffer.from(hash.subarray(0, 16));
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// Same derivation as seed_candidate_scenarios.py: uuid5(URL, "…:thin:version").
const THIN_VERSION_ID = uuid5(NAMESPACE_URL, 'alethra-candidate-first-use:thin:version');

test('empty account: concrete entries, no fake progress', async ({ page }) => {
  test.skip(!READY, SKIP_REASON);
  await login(page, EMPTY.email, EMPTY.password);

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('No experiments yet')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Create your first experiment' })).toHaveAttribute(
    'href',
    '/experiments/new',
  );

  await openTraining(page);
  await expect(page.locator('[data-testid="pl-no-strategy"]')).toBeVisible();
  await expect(page.locator('[data-testid="pl-no-strategy-link"]')).toHaveAttribute(
    'href',
    '/experiments/new',
  );
  await expect(page.locator('[data-testid="personalization-no-strategies-link"]')).toHaveAttribute(
    'href',
    '/experiments/new',
  );

  const status = page.locator('[data-testid="personalization-status"]');
  await expect(status).toBeVisible();
  await expect(status).not.toContainText('已達建議資料充分性門檻');
  // No consent and no decisions is a training blocker, not a sufficiency gap.
  await expect(status).toContainText('未達可訓練條件（見下方原因）');
  await expect(status).not.toContainText('已拒絕訓練');
  await expect(page.locator('[data-testid="pl-journey-worker"]')).toHaveCount(0);

  await page.locator('[data-testid="pl-no-strategy-link"]').click();
  await page.waitForURL((url) => url.pathname === '/experiments/new', { timeout: 30000 });
  await expect(page.locator('form, [data-testid="experiment-form"]').first()).toBeVisible();
});

test('thin account: consent is opt-in and the journey self-syncs', async ({ page }) => {
  test.skip(!READY, SKIP_REASON);
  await login(page, THIN.email, THIN.password);
  await openTraining(page);
  await expect(page.locator('[data-testid="pl-journey"]')).toBeVisible();

  // Missing consent is a training blocker; the badge must name the reason area
  // instead of claiming a blanket refusal.
  const eligibilityBadge = page.locator('[data-testid="personalization-status"]');
  await expect(eligibilityBadge).toContainText('未達可訓練條件（見下方原因）');
  await expect(eligibilityBadge).not.toContainText('已拒絕訓練');

  // Repeatable across runs: revoke a grant left by an earlier run via the UI.
  const revoke = page.locator('[data-testid="personalization-revoke"]');
  if ((await revoke.count()) > 0) {
    await revoke.click();
    await expect(page.locator('[data-testid="personalization-consent-state"]')).toHaveCount(0);
  }

  const nextAction = page.locator('[data-testid="pl-journey-next-action"]');
  await expect(nextAction).toContainText('同意');
  await expect(page.locator('[data-testid="pl-journey-next-link"]')).toHaveAttribute(
    'href',
    '#personalization-eligibility',
  );

  const scope = page.locator('[data-testid^="personalization-scope-"]');
  await expect(scope).toHaveCount(1);
  await expect(scope).not.toBeChecked();
  const ack = page.locator('[data-testid="personalization-acknowledge"]');
  await expect(ack).not.toBeChecked();
  await expect(page.locator('[data-testid="personalization-grant"]')).toBeDisabled();

  await scope.check();
  await ack.check();
  await expect(page.locator('[data-testid="personalization-grant"]')).toBeEnabled();
  await page.locator('[data-testid="personalization-grant"]').click();
  await expect(page.locator('[data-testid="personalization-consent-state"]')).toBeVisible();

  // The journey panel is a sibling; it must reflect the new step in place.
  await expect(nextAction).toContainText('補足');
  await expect(page.locator('[data-testid="pl-journey-next-link"]')).toHaveAttribute(
    'href',
    /^\/experiments\/[0-9a-f-]{36}\/simulation$/,
  );

  // An unlinked paper account must not render as a total loss.
  await page.locator('[data-testid="pl-journey-next-link"]').click();
  await page.waitForURL(/\/simulation$/, { timeout: 30000 });
  const equity = page.locator('[data-testid="sim-paper-equity"]');
  await expect(equity).toContainText('未連結紙上帳戶');
  await expect(equity).not.toContainText('-100.00%');

  // The page anchors every next-step link points at must exist.
  await page.goto('/training', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-testid="pl-journey"]')).toBeVisible();
  await expect(page.locator('[data-testid="pl-journey-next-action"]')).toContainText('補足');
  await expect(page.locator('#pl-preview')).toHaveCount(1);
  await expect(page.locator('#pl-training')).toHaveCount(1);

  // Leave the account in its pre-consent first-use state.
  await page.locator('[data-testid="personalization-revoke"]').click();
  await expect(page.locator('[data-testid="personalization-consent-state"]')).toHaveCount(0);
  await expect(nextAction).toContainText('同意');
});

test('data-sufficiency shortfall is not described as a refusal or a failed model', async ({
  page,
}) => {
  test.skip(!READY, SKIP_REASON);

  // Route-level fixture: the eligibility response fails only a recommended
  // data-sufficiency bar while the snapshot builder would still accept the
  // dataset. The panel must not claim the system refused training or that the
  // model failed a quality test, and no account is mutated.
  await page.route('**/api/v1/ml/personalization/eligibility', (route) =>
    route.fulfill({
      json: {
        capability_status: 'ENGINEERING_CHAIN_READY',
        capability_status_zh: 'route fixture — 建議資料充分性未達',
        eligible: false,
        status: 'NOT_ELIGIBLE',
        reasons: [
          {
            code: 'INSUFFICIENT_DISTINCT_SESSIONS',
            message_zh: '工作階段數量未達建議資料充分性門檻。',
            measured: 3,
            requirement: 30,
          },
        ],
        requirements: {},
        consent: {
          granted: true,
          consent_version: 'personalization-training-v1',
          scope: { strategy_version_ids: [THIN_VERSION_ID] },
          granted_at: null,
        },
        dataset: {
          total_recorded_events: 205,
          eligible_events: 205,
          excluded: {
            superseded_revisions: 0,
            outside_consent_scope: 0,
            missing_reason: 0,
            missing_snapshot_context: 0,
          },
          distinct_sessions: 3,
          distinct_snapshot_dates: 3,
          action_counts: { buy: 100, sell: 105 },
          feature_coverage: { covered: 205, considered: 205, ratio: 1 },
          future_leakage_events: 0,
        },
        strategies: [],
      },
    }),
  );

  await login(page, THIN.email, THIN.password);
  await openTraining(page);

  const badge = page.locator('[data-testid="personalization-status"]');
  await expect(badge).toContainText('尚未達建議資料充分性門檻');
  await expect(badge).not.toContainText('已拒絕訓練');
  await expect(badge).not.toContainText('已達建議資料充分性門檻');
  await expect(page.getByText('未達不代表模型已被判定失敗')).toBeVisible();
});

test('thin account: a queued job is restored after reload and blocks duplicate training', async ({
  page,
}) => {
  test.skip(!READY, SKIP_REASON);

  // Route-level fixture: no job or model is created in the database. The thin
  // account already has a version, so the panel's journey calls stay real.
  const jobId = '11111111-1111-4111-8111-111111111111';
  const snapshotId = '22222222-2222-4222-8222-222222222222';
  const pendingJob = {
    id: jobId,
    user_id: null,
    experiment_id: null,
    status: 'pending',
    parameter_grid: {},
    strategy_spec_snapshot: { strategy_version_id: THIN_VERSION_ID },
    results: null,
    best_params: null,
    error_details: null,
    created_at: new Date().toISOString(),
    completed_at: null,
    cancelled_at: null,
  };
  const snapshot = {
    snapshot_id: snapshotId,
    status: 'ready',
    strategy_version_id: THIN_VERSION_ID,
    included_count: 200,
    excluded_count: 0,
    exclusion_breakdown: {},
    exclusion_labels_zh: {},
    class_distribution: {},
    date_range: { start: null, end: null },
    data_cutoff_at: null,
    feature_schema_version: 'test-fixture',
    feature_names: [],
    checksum: 'route-fixture-checksum',
    created_at: null,
  };

  await page.route(/\/api\/v1\/ml\/personal\/snapshots\?strategy_version_id=/, (route) =>
    route.fulfill({ json: { snapshots: [snapshot] } }),
  );
  await page.route('**/api/v1/training/jobs', (route) => route.fulfill({ json: [pendingJob] }));
  await page.route(`**/api/v1/training/jobs/${jobId}`, (route) =>
    route.fulfill({ json: pendingJob }),
  );

  await login(page, THIN.email, THIN.password);
  await openTraining(page);

  const status = page.locator('[data-testid="pl-train-status"]');
  await expect(status).toBeVisible();
  await expect(status).toContainText('11111111');
  await expect(status).toContainText('排隊中');
  await expect(page.locator('[data-testid="pl-train-cancel"]')).toBeVisible();
  await expect(page.locator('[data-testid="pl-snapshot-select"]')).toHaveValue(snapshotId);
  // A ready snapshot alone would arm the button; a queued job must keep it shut.
  await expect(page.locator('[data-testid="pl-train-start"]')).toBeDisabled();

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-testid="pl-journey"]')).toBeVisible();
  await expect(page.locator('[data-testid="pl-train-status"]')).toContainText('排隊中');
  await expect(page.locator('[data-testid="pl-train-cancel"]')).toBeVisible();
  await expect(page.locator('[data-testid="pl-train-start"]')).toBeDisabled();
});
