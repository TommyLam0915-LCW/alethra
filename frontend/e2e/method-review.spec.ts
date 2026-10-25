import { test, expect, type Page } from '@playwright/test';

const API_BASE = 'http://localhost:8001';
const FRONTEND_BASE = 'http://localhost:3001';

const USER_A = {
  email: 'user_a_test@example.com',
  password: process.env.ACCEPTANCE_PASSWORD_A || 'TestPassword123!@#',
};

async function login(page: Page, email: string, password: string) {
  await page.goto(`${FRONTEND_BASE}/login`);
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard');
}

async function getAuthToken(email: string, password: string) {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(`Login failed: ${response.status}`);
  }
  const data = await response.json();
  return data.access_token;
}

async function createExperiment(token: string, title: string, hypothesis = 'Test hypothesis') {
  const response = await fetch(`${API_BASE}/api/v1/experiments/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({
      title,
      hypothesis,
      falsification_conditions: 'Test falsification criteria',
      strategy_spec: {
        template_type: 'buy_and_hold',
        asset_ids: ['DEMO-ALPHA'],
        start_date: '2024-01-01',
        end_date: '2024-12-31',
        initial_cash: 100000,
        commission_rate: 0.001,
        slippage_rate: 0.0005,
        benchmark_id: 'DEMO-BENCHMARK',
      },
    }),
  });
  if (!response.ok) {
    throw new Error(`Experiment creation failed: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function getFirstVersionId(token: string, experimentId: string) {
  const response = await fetch(`${API_BASE}/api/v1/experiments/${experimentId}/versions`, {
    headers: {
      'Authorization': `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    throw new Error(`Version fetch failed: ${response.status}`);
  }
  const versions = await response.json();
  if (!versions || versions.length === 0) {
    throw new Error('No versions found for experiment');
  }
  return versions[0].id;
}

async function createRun(token: string, versionId: string) {
  const response = await fetch(`${API_BASE}/api/v1/versions/${versionId}/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    throw new Error(`Run creation failed: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function getMethodReview(token: string, experimentId: string) {
  const response = await fetch(`${API_BASE}/api/v1/experiments/${experimentId}/method-review`, {
    headers: {
      'Authorization': `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    throw new Error(`Method review fetch failed: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

test.describe('Phase 2B — Method Review Browser Acceptance', () => {
  test.describe('A. Method Review Display with Completed Run', () => {
    test('should display method review section with all 10 rules', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Test Exp ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);
      const versionId = await getFirstVersionId(token, experiment.id);
      const run = await createRun(token, versionId);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      await expect(methodReviewCard.getByText('規則集 v1.0')).toBeVisible();

      const ruleIds = ['RC-001', 'RC-002', 'RC-003', 'RC-004', 'RC-005',
                       'RC-006', 'RC-007', 'RC-008', 'RC-009', 'RC-010'];
      for (const ruleId of ruleIds) {
        await expect(methodReviewCard.getByText(ruleId, { exact: true })).toBeVisible();
      }

      await expect(methodReviewCard.getByText(/聲明：/)).toBeVisible();
    });

    test('should display status badges with correct labels', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Status Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);
      const versionId = await getFirstVersionId(token, experiment.id);
      await createRun(token, versionId);

      const review = await getMethodReview(token, experiment.id);
      expect(review.checks).toHaveLength(10);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      const STATUS_LABELS: Record<string, string> = {
        'PASS': '通過',
        'WARNING': '警告',
        'INSUFFICIENT_INFORMATION': '資訊不足',
        'NOT_APPLICABLE': '不適用',
      };

      for (const check of review.checks) {
        const expectedLabel = STATUS_LABELS[check.status];
        expect(expectedLabel, `Unknown status ${check.status}`).toBeDefined();

        const ruleIdSpan = methodReviewCard.getByText(check.rule_id, { exact: true });
        await expect(ruleIdSpan).toBeVisible({ timeout: 5000 });
        const flexHeader = ruleIdSpan.locator('..');
        await expect(flexHeader.getByText(expectedLabel, { exact: true })).toBeVisible();
      }
    });

    test('should display reviewed run and version identity', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Identity Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);
      const versionId = await getFirstVersionId(token, experiment.id);
      const run = await createRun(token, versionId);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      await expect(methodReviewCard.getByText(/已审查執行:|已審查執行:/)).toBeVisible();
      await expect(methodReviewCard.getByText(/版本:/)).toBeVisible();
      await expect(methodReviewCard.getByText(/產生於/)).toBeVisible();
    });
    test('should display provenance disclosure for current-text checks', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Provenance Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);
      const versionId = await getFirstVersionId(token, experiment.id);
      await createRun(token, versionId);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      await expect(methodReviewCard.getByText(/資料來源說明/)).toBeVisible();
      await expect(methodReviewCard.getByText(/RC-001 及 RC-002/)).toBeVisible();
      await expect(methodReviewCard.getByText(/並非歷史文字快照/)).toBeVisible();
    });

    test('should display timestamp fallback warning when completed_at is missing', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Timestamp Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);

      const syntheticReview = {
        rule_set_version: '1.0',
        experiment_id: experiment.id,
        version_id: '00000000-0000-0000-0000-000000000001',
        run_id: '00000000-0000-0000-0000-000000000002',
        run_selection: 'latest_completed_by_completed_at_then_id',
        config_hash_match: true,
        completed_at_available: false,
        generated_at: new Date().toISOString(),
        checks: [
          { rule_id: 'RC-001', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-002', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-003', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: valid.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-004', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: configured.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-005', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: trades present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-006', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: benchmark present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-007', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: complete.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-008', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: version present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-009', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: costs nonzero.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          { rule_id: 'RC-010', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: hashes match.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
        ],
      };

      let intercepted = false;
      await page.route(`**/api/v1/experiments/${experiment.id}/method-review`, async (route) => {
        intercepted = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(syntheticReview),
        });
      });

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      expect(intercepted, 'method-review endpoint must be intercepted').toBe(true);

      await expect(methodReviewCard.getByText(/時間戳提醒/)).toBeVisible({ timeout: 5000 });
      await expect(methodReviewCard.getByText(/依據 ID 而非時間先後/)).toBeVisible();
    });
  });

  test.describe('B. No Completed Run State', () => {
    test('should display understandable message when no completed run exists', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B No Run Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      await expect(methodReviewCard.getByText('尚無已完成的回測執行')).toBeVisible();

      const rc001 = methodReviewCard.getByText('RC-001', { exact: true });
      await expect(rc001).toBeVisible();

      const rc005 = methodReviewCard.getByText('RC-005', { exact: true });
      await expect(rc005).toBeVisible();
    });
  });

  test.describe('C. Refresh Behavior', () => {
    test('should refresh method review on button click', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Refresh Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);

      function makeReview(rc001Status: string, generatedAt: string) {
        return {
          rule_set_version: '1.0',
          experiment_id: experiment.id,
          version_id: '00000000-0000-0000-0000-000000000001',
          run_id: '00000000-0000-0000-0000-000000000002',
          run_selection: 'latest_completed_by_completed_at_then_id',
          config_hash_match: true,
          completed_at_available: true,
          generated_at: generatedAt,
          checks: [
            { rule_id: 'RC-001', rule_version: '1.0', status: rc001Status, evidence: {}, explanation: `A. Observation: ${rc001Status}.\nB. Interpretation: ok.\nC. Limitation: none.`, limitations: [] },
            { rule_id: 'RC-002', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-003', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: valid.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-004', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: configured.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-005', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: trades present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-006', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: benchmark present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-007', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: complete.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-008', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: version present.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-009', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: costs nonzero.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
            { rule_id: 'RC-010', rule_version: '1.0', status: 'PASS', evidence: {}, explanation: 'A. Observation: hashes match.\nB. Interpretation: ok.\nC. Limitation: none.', limitations: [] },
          ],
        };
      }

      let phase: 'initial' | 'refreshed' = 'initial';
      let requestCount = 0;
      let intercepted = false;
      await page.route(`**/api/v1/experiments/${experiment.id}/method-review`, async (route) => {
        intercepted = true;
        requestCount++;
        if (phase === 'initial') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(makeReview('PASS', '2026-01-01T00:00:00Z')),
          });
        } else {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(makeReview('INSUFFICIENT_INFORMATION', '2026-01-01T01:00:00Z')),
          });
        }
      });

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });
      expect(intercepted, 'method-review endpoint must be intercepted').toBe(true);

      await methodReviewCard.scrollIntoViewIfNeeded();

      // Find the RC-001 status label: span with "RC-001" → parent flex header div
      const rc001Span = methodReviewCard.getByText('RC-001', { exact: true });
      await expect(rc001Span).toBeVisible({ timeout: 5000 });
      const rc001Header = rc001Span.locator('..');
      await expect(rc001Header.getByText('通過', { exact: true })).toBeVisible({ timeout: 5000 });

      // Switch fixture to refreshed phase, then trigger refresh
      phase = 'refreshed';
      const preRefreshCount = requestCount;

      const refreshButton = methodReviewCard.getByRole('button', { name: '重新整理' });
      await expect(refreshButton).toBeVisible();
      await refreshButton.click();

      await expect.poll(() => requestCount, {
        message: 'refresh should trigger additional request(s)',
        timeout: 5000,
      }).toBeGreaterThan(preRefreshCount);

      await expect(rc001Header.getByText('資訊不足', { exact: true })).toBeVisible({ timeout: 5000 });

      await expect(refreshButton).toBeVisible();
      await expect(refreshButton).toHaveText('重新整理');
      await expect(refreshButton).toBeEnabled();
    });
  });

  test.describe('D. No Side Effects', () => {
    test('should not create experiment, run, or version when loading method review', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B No Side Effect Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);

      const initialExpResponse = await fetch(`${API_BASE}/api/v1/experiments/`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const initialExperiments = await initialExpResponse.json();
      const initialCount = initialExperiments.length;

      const initialVersionsResponse = await fetch(`${API_BASE}/api/v1/experiments/${experiment.id}/versions`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const initialVersions = await initialVersionsResponse.json();
      const initialVersionCount = initialVersions.length;

      const initialRunsResponse = await fetch(`${API_BASE}/api/v1/experiments/${experiment.id}/runs`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const initialRuns = await initialRunsResponse.json();
      const initialRunCount = initialRuns.length;

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });
      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      await page.waitForTimeout(1000);

      const finalExpResponse = await fetch(`${API_BASE}/api/v1/experiments/`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const finalExperiments = await finalExpResponse.json();
      expect(finalExperiments.length).toBe(initialCount);

      const finalVersionsResponse = await fetch(`${API_BASE}/api/v1/experiments/${experiment.id}/versions`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const finalVersions = await finalVersionsResponse.json();
      expect(finalVersions.length).toBe(initialVersionCount);

      const finalRunsResponse = await fetch(`${API_BASE}/api/v1/experiments/${experiment.id}/runs`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const finalRuns = await finalRunsResponse.json();
      expect(finalRuns.length).toBe(initialRunCount);
    });
  });

  test.describe('E. Error Handling', () => {
    test('should display error fallback when method-review request fails', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Error Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);

      let intercepted = false;
      await page.route(`**/api/v1/experiments/${experiment.id}/method-review`, async (route) => {
        intercepted = true;
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Internal server error' }),
        });
      });

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      expect(intercepted, 'method-review endpoint must be intercepted').toBe(true);

      await expect(methodReviewCard.getByText('方法審查暫時無法載入')).toBeVisible({ timeout: 5000 });

      const rc001 = methodReviewCard.getByText('RC-001', { exact: true });
      await expect(rc001).toHaveCount(0);

      const rc005 = methodReviewCard.getByText('RC-005', { exact: true });
      await expect(rc005).toHaveCount(0);

      await expect(page.getByRole('heading', { name: '回測結果' })).toBeVisible();
    });
  });
});
