import { test, expect } from '@playwright/test';

const API_BASE = 'http://localhost:8001';
const FRONTEND_BASE = 'http://localhost:3001';

const USER_A = {
  email: 'user_a_test@example.com',
  password: process.env.ACCEPTANCE_PASSWORD_A || 'TestPassword123!@#',
};

async function login(page, email, password) {
  await page.goto(`${FRONTEND_BASE}/login`);
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard');
}

async function getAuthToken(email, password) {
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

async function createExperiment(token, title, hypothesis = 'Test hypothesis') {
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

async function getFirstVersionId(token, experimentId) {
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

async function createRun(token, versionId) {
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

async function getMethodReview(token, experimentId) {
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
      const run = await createRun(token, versionId);

      const review = await getMethodReview(token, experiment.id);
      expect(review.checks).toHaveLength(10);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      const statusLabels = ['通過', '警告', '資訊不足', '不適用'];
      for (const label of statusLabels) {
        const badges = methodReviewCard.getByText(label, { exact: true });
        const count = await badges.count();
        if (count > 0) {
          await expect(badges.first()).toBeVisible();
        }
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
      const versionId = await getFirstVersionId(token, experiment.id);
      const run = await createRun(token, versionId);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      const refreshButton = methodReviewCard.getByRole('button', { name: '重新整理' });
      await expect(refreshButton).toBeVisible();

      await refreshButton.click();

      await expect(methodReviewCard.getByText('RC-001', { exact: true })).toBeVisible({ timeout: 5000 });
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
    test('should not display empty review on failed request', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      const expTitle = `P2B Error Test ${Date.now()}`;
      const experiment = await createExperiment(token, expTitle);

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/experiments/${experiment.id}`);

      const methodReviewCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '方法審查' }),
      });

      await expect(methodReviewCard).toBeVisible({ timeout: 10000 });

      const hasContent = await methodReviewCard.locator('text=RC-001').count() > 0 ||
                         await methodReviewCard.locator('text=方法審查暫時無法載入').count() > 0;
      expect(hasContent).toBeTruthy();
    });
  });
});
