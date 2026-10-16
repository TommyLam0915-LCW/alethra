/**
 * Execution Page — Fill-Source Rendering Tests
 *
 * Tests the four-state fill-source rendering using Playwright network
 * interception to provide deterministic API responses without requiring
 * a live backend.
 */

import { test, expect } from '@playwright/test';

const FRONTEND_URL = 'http://localhost:3001';
const MOCK_TOKEN = 'mock-test-token';

async function setupMockedPage(page: any, intents: any[]) {
  await page.goto(FRONTEND_URL);
  await page.evaluate((token) => {
    localStorage.setItem('token', token);
  }, MOCK_TOKEN);

  await page.route('**/api/v1/execution/intents', (route: any) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(intents),
    });
  });

  await page.goto(`${FRONTEND_URL}/execution`);
  // Wait for the intents list to appear
  await page.waitForSelector('.card:has-text("已成交")', { timeout: 10000 });
}

test.describe('Execution Page — Fill-Source Rendering', () => {
  test('renders simulated fill source', async ({ page }) => {
    const intents = [
      {
        intent_id: 'test-1',
        portfolio_id: 'p1',
        symbol: 'DEMO-ALPHA',
        side: 'BUY',
        quantity: 10,
        status: 'filled',
        order_type: 'MARKET',
        fill_source: 'simulated',
        broker_confirmed_at: null,
        broker_ref: null,
        created_at: '2026-10-15T10:00:00Z',
        updated_at: '2026-10-15T10:00:00Z',
      },
    ];

    await setupMockedPage(page, intents);

    // Assert simulated label is present with yellow badge
    const badge = page.locator('text=模擬成交（離線）');
    await expect(badge).toBeVisible();
    const bgColor = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bgColor).toContain('255, 243, 205'); // yellow

    // Assert reconcile button present
    await expect(page.locator('button:has-text("對帳")')).toBeVisible();

    // Assert misleading alternatives NOT present
    await expect(page.locator('text=券商確認成交')).not.toBeVisible();
    await expect(page.locator('text=券商來源')).not.toBeVisible();
    await expect(page.locator('text=來源不明')).not.toBeVisible();
  });

  test('renders broker-confirmed fill source', async ({ page }) => {
    const intents = [
      {
        intent_id: 'test-2',
        portfolio_id: 'p1',
        symbol: 'DEMO-BETA',
        side: 'SELL',
        quantity: 5,
        status: 'filled',
        order_type: 'LIMIT',
        limit_price: 100.5,
        fill_source: 'broker',
        broker_confirmed_at: '2026-10-15T10:05:00Z',
        broker_ref: 'BROKER-REF-123',
        created_at: '2026-10-15T10:00:00Z',
        updated_at: '2026-10-15T10:05:00Z',
      },
    ];

    await setupMockedPage(page, intents);

    // Assert broker-confirmed label with green badge
    const badge = page.locator('text=券商確認成交');
    await expect(badge).toBeVisible();
    const bgColor = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bgColor).toContain('212, 237, 218'); // green

    // Assert reference label shows broker_ref
    await expect(page.locator('text=BROKER-REF-123')).toBeVisible();

    // Assert misleading alternatives NOT present
    await expect(page.locator('text=模擬成交')).not.toBeVisible();
    await expect(page.locator('text=券商來源')).not.toBeVisible();
    await expect(page.locator('text=來源不明')).not.toBeVisible();
  });

  test('renders broker-incomplete fill source', async ({ page }) => {
    const intents = [
      {
        intent_id: 'test-3',
        portfolio_id: 'p1',
        symbol: 'DEMO-ALPHA',
        side: 'BUY',
        quantity: 20,
        status: 'filled',
        order_type: 'MARKET',
        fill_source: 'broker',
        broker_confirmed_at: null,
        broker_ref: 'PENDING-REF-456',
        created_at: '2026-10-15T10:00:00Z',
        updated_at: '2026-10-15T10:00:00Z',
      },
    ];

    await setupMockedPage(page, intents);

    // Assert broker-incomplete label with orange badge
    const badge = page.locator('text=券商來源（待確認）');
    await expect(badge).toBeVisible();
    const bgColor = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bgColor).toContain('255, 224, 178'); // orange

    // Assert reference label shows broker_ref
    await expect(page.locator('text=PENDING-REF-456')).toBeVisible();

    // Assert misleading alternatives NOT present
    await expect(page.locator('text=模擬成交')).not.toBeVisible();
    await expect(page.locator('text=券商確認成交')).not.toBeVisible();
    await expect(page.locator('text=來源不明')).not.toBeVisible();
  });

  test('renders unknown/null fill source', async ({ page }) => {
    const intents = [
      {
        intent_id: 'test-4',
        portfolio_id: 'p1',
        symbol: 'DEMO-BENCHMARK',
        side: 'BUY',
        quantity: 15,
        status: 'filled',
        order_type: 'MARKET',
        fill_source: null,
        broker_confirmed_at: null,
        broker_ref: null,
        created_at: '2026-10-15T10:00:00Z',
        updated_at: '2026-10-15T10:00:00Z',
      },
    ];

    await setupMockedPage(page, intents);

    // Assert unknown label with gray badge
    const badge = page.locator('text=來源不明');
    await expect(badge).toBeVisible();
    const bgColor = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(bgColor).toContain('226, 227, 229'); // gray

    // Assert reconcile button present
    await expect(page.locator('button:has-text("對帳")')).toBeVisible();

    // Assert misleading alternatives NOT present
    await expect(page.locator('text=模擬成交')).not.toBeVisible();
    await expect(page.locator('text=券商確認成交')).not.toBeVisible();
    await expect(page.locator('text=券商來源')).not.toBeVisible();
  });
});
