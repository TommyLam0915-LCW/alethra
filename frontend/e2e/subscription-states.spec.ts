/**
 * Subscription Page — Loading/Error State Rendering Tests
 *
 * Tests subscription and entitlement loading/error separation using
 * Playwright network interception. Verifies that HTTP failures do not
 * fabricate "free plan" or "no entitlement" states.
 */

import { test, expect } from '@playwright/test';

const FRONTEND_URL = 'http://localhost:3001';
const MOCK_TOKEN = 'mock-test-token';

const PLANS_RESPONSE = {
  plans: [
    {
      id: 'plan-1',
      name: '基礎方案',
      description: '免費基礎功能',
      monthly_price: 0,
      currency: 'TWD',
      features: { max_experiments: 1 },
    },
    {
      id: 'plan-2',
      name: '專業方案',
      description: '完整研究功能',
      monthly_price: 99900,
      currency: 'TWD',
      features: { max_experiments: 10, ai_assistance: true },
    },
  ],
};

const OP_STATUS_RESPONSE = {
  feature_switches: {},
  mode: 'test',
  data_mode: 'demo',
  broker_mode: 'paper-only',
  ai_mode: 'mock',
};

async function setupAuth(page: any) {
  await page.goto(FRONTEND_URL);
  await page.evaluate((token: string) => {
    localStorage.setItem('token', token);
  }, MOCK_TOKEN);
}

function mockPlans(route: any) {
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(PLANS_RESPONSE),
  });
}

function mockOpStatus(route: any) {
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(OP_STATUS_RESPONSE),
  });
}

test.describe('Subscription Page — Subscription States', () => {
  test('renders active subscription with plan name and status', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          subscription: {
            id: 'sub-1',
            plan_id: 'plan-2',
            status: 'active',
            current_period_start: '2026-10-01T00:00:00Z',
            current_period_end: '2026-11-01T00:00:00Z',
          },
          test_mode: true,
        }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Should show the plan name from the subscription (scoped to current subscription card)
    const currentPlanCard = page.locator('.card').filter({ has: page.locator('h3:has-text("目前方案")') });
    await expect(currentPlanCard.locator('span:has-text("專業方案")').first()).toBeVisible();
    // Should show active status
    await expect(currentPlanCard.locator('text=啟用中')).toBeVisible();
    // Should show billing period
    await expect(currentPlanCard.locator('text=計費週期開始')).toBeVisible();
    // Should NOT show free plan
    await expect(page.locator('text=免費方案')).not.toBeVisible();
    // Should NOT show subscription error
    await expect(page.locator('text=無法載入訂閱資訊')).not.toBeVisible();
  });

  test('renders contract-defined absence (subscription: null) as free plan', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ subscription: null, test_mode: true }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Should show free plan
    const currentPlanCard = page.locator('.card').filter({ has: page.locator('h3:has-text("目前方案")') });
    await expect(currentPlanCard.locator('text=免費方案').first()).toBeVisible();
    // Should show basic tier badge
    await expect(currentPlanCard.locator('text=基礎').first()).toBeVisible();
    // Should NOT show subscription error
    await expect(page.locator('text=無法載入訂閱資訊')).not.toBeVisible();
  });

  test('renders HTTP 500 as subscription error, NOT as free plan', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Should show subscription error message (raw API error)
    await expect(page.locator('text=Internal server error').first()).toBeVisible();
    // Should NOT fabricate free plan from server error
    const currentPlanCard = page.locator('.card').filter({ has: page.locator('h3:has-text("目前方案")') });
    await expect(currentPlanCard.locator('text=免費方案').first()).not.toBeVisible();
    // Should NOT show active subscription
    await expect(currentPlanCard.locator('text=啟用中')).not.toBeVisible();
  });

  test('renders network failure as subscription error, NOT as free plan', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.abort('failed');
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Should show subscription error (any error message from network failure)
    const currentPlanCard = page.locator('.card').filter({ has: page.locator('h3:has-text("目前方案")') });
    const errorBox = currentPlanCard.locator('div[style*="background-color: rgb(248, 215, 218)"]');
    await expect(errorBox).toBeVisible();
    // Should NOT fabricate free plan from network failure
    await expect(currentPlanCard.locator('text=免費方案').first()).not.toBeVisible();
  });
});

test.describe('Subscription Page — Entitlement States', () => {
  test('entitlement 404 is treated as absence, not error', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ subscription: null, test_mode: true }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Entitlement not found' }),
      });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Should show free plan description (no entitlement, no error)
    await expect(page.locator('text=基礎免費方案')).toBeVisible();
    // Should NOT show entitlement error
    await expect(page.locator('text=無法載入配額資訊')).not.toBeVisible();
    // Should NOT show quota section
    await expect(page.locator('text=功能配額')).not.toBeVisible();
  });

  test('entitlement HTTP 500 is treated as error, not absence', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ subscription: null, test_mode: true }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Should show entitlement error (raw API error message)
    const quotaCard = page.locator('.card').filter({ has: page.locator('h3:has-text("功能配額")') });
    await expect(quotaCard.locator('text=Internal server error')).toBeVisible();
    // Free plan description should still be visible when subscription is null
    const currentPlanCard = page.locator('.card').filter({ has: page.locator('h3:has-text("目前方案")') });
    await expect(currentPlanCard.locator('text=基礎免費方案').first()).toBeVisible();
  });
});

test.describe('Subscription Page — Quota Display', () => {
  test('null quota shows as unlimited in both summary and detail', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ subscription: null, test_mode: true }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'ent-1',
          pilot_access: true,
          research_run_limit: null,
          ai_usage_limit: null,
          export_limit: null,
          forward_simulation_limit: null,
          expiry_date: null,
          support_tier: '標準',
        }),
      });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Summary section: null -> 無上限
    await expect(page.locator('text=研究執行次數上限：無上限')).toBeVisible();
    await expect(page.locator('text=AI 使用次數上限：無上限')).toBeVisible();
    await expect(page.locator('text=匯出次數上限：無上限')).toBeVisible();
    await expect(page.locator('text=正向模擬上限：無上限')).toBeVisible();

    // Detail section: null -> 無上限
    await expect(page.locator('text=功能配額')).toBeVisible();
    const quotaCard = page.locator('.card:has-text("功能配額")');
    // Check that 無上限 appears in the quota card (should be 4 times for 4 quota items)
    const unlimitedItems = quotaCard.locator('text=無上限');
    await expect(unlimitedItems.first()).toBeVisible();
    // Should NOT show 無配額 for null values
    await expect(quotaCard.locator('text=無配額')).not.toBeVisible();
  });

  test('zero quota shows as no allowance in both summary and detail', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ subscription: null, test_mode: true }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'ent-2',
          pilot_access: true,
          research_run_limit: 0,
          ai_usage_limit: 0,
          export_limit: 0,
          forward_simulation_limit: 0,
          expiry_date: null,
          support_tier: '限制',
        }),
      });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Summary section: 0 -> 無配額
    await expect(page.locator('text=研究執行次數上限：無配額')).toBeVisible();
    await expect(page.locator('text=AI 使用次數上限：無配額')).toBeVisible();

    // Detail section: 0 -> 無配額（無法使用）
    const quotaCard = page.locator('.card:has-text("功能配額")');
    const noAllowanceItems = quotaCard.locator('text=無配額（無法使用）');
    await expect(noAllowanceItems.first()).toBeVisible();
    // Should NOT show 無上限 for zero values
    await expect(quotaCard.locator('text=無上限')).not.toBeVisible();
  });

  test('positive quota shows numeric value in both summary and detail', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ subscription: null, test_mode: true }),
      });
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'ent-3',
          pilot_access: true,
          research_run_limit: 100,
          ai_usage_limit: 50,
          export_limit: 10,
          forward_simulation_limit: 25,
          expiry_date: '2027-10-15',
          support_tier: '進階',
        }),
      });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // Summary section: positive -> number
    await expect(page.locator('text=研究執行次數上限：100')).toBeVisible();
    await expect(page.locator('text=AI 使用次數上限：50')).toBeVisible();
    await expect(page.locator('text=匯出次數上限：10')).toBeVisible();
    await expect(page.locator('text=正向模擬上限：25')).toBeVisible();

    // Detail section: positive -> number
    const quotaCard = page.locator('.card:has-text("功能配額")');
    await expect(quotaCard.locator('text=100')).toBeVisible();
    await expect(quotaCard.locator('text=50')).toBeVisible();
    // Should NOT show 無上限 or 無配額 for positive values
    await expect(quotaCard.locator('text=無上限')).not.toBeVisible();
    await expect(quotaCard.locator('text=無配額')).not.toBeVisible();
  });
});

test.describe('Subscription Page — Recovery State', () => {
  test('retry after subscription failure clears error and shows data', async ({ page }) => {
    await setupAuth(page);

    let subscriptionCallCount = 0;

    await page.route('**/api/v1/plans', mockPlans);
    await page.route('**/api/v1/operational-status', mockOpStatus);
    await page.route('**/api/v1/subscriptions/me', (route: any) => {
      subscriptionCallCount++;
      if (subscriptionCallCount === 1) {
        // First call: fail
        route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Internal server error' }),
        });
      } else {
        // Subsequent calls: succeed with active subscription
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            subscription: {
              id: 'sub-retry',
              plan_id: 'plan-2',
              status: 'active',
              current_period_start: '2026-10-01T00:00:00Z',
              current_period_end: '2026-11-01T00:00:00Z',
            },
            test_mode: true,
          }),
        });
      }
    });
    await page.route('**/api/v1/pilot/entitlements/me', (route: any) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) });
    });
    // The dashboard visited mid-test fetches the experiments list; unmocked it
    // would 401 against the real backend and trigger the global logout
    // redirect, racing the navigation back to /subscription.
    await page.route('**/api/v1/experiments/', (route: any) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // First load: should show subscription error
    const currentPlanCard = page.locator('.card').filter({ has: page.locator('h3:has-text("目前方案")') });
    await expect(page.locator('text=Internal server error').first()).toBeVisible();
    await expect(currentPlanCard.locator('text=免費方案').first()).not.toBeVisible();

    // Navigate away and back to trigger reload
    await page.goto(`${FRONTEND_URL}/dashboard`);
    await page.goto(`${FRONTEND_URL}/subscription`);
    await page.waitForLoadState('networkidle');

    // After retry: error should be cleared, subscription should show
    await expect(page.locator('text=Internal server error')).not.toBeVisible();
    await expect(currentPlanCard.locator('span:has-text("專業方案")').first()).toBeVisible();
    await expect(currentPlanCard.locator('text=啟用中')).toBeVisible();
  });
});
