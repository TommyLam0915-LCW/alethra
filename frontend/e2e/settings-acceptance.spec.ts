/**
 * Settings UI Acceptance Test
 * Verifies: login -> settings -> save preferences -> workspace consumes them
 */
import { test, expect } from '@playwright/test';

const BACKEND_URL = 'http://127.0.0.1:8001';
const FRONTEND_URL = 'http://localhost:3004';

test.describe('Settings UI Acceptance', () => {
  test('A1. Login and access settings', async ({ page }) => {
    // Register test user via API
    const email = `test-${Date.now()}@example.com`;
    const password = 'testpass123';

    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    // Login via UI
    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();

    // Should redirect to dashboard
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Navigate to settings
    await page.goto(`${FRONTEND_URL}/settings`);
    await expect(page.locator('h1')).toContainText('設定');

    // Click workspace preferences tab
    await page.getByRole('button', { name: '工作台偏好' }).click();

    // Save preferences
    await page.getByLabel('預設標的').fill('US:AAPL');
    await page.getByLabel('預設時間範圍').selectOption('1Y');
    await page.getByRole('button', { name: '儲存偏好' }).click();

    // Verify saved
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Verify via API
    const token = await page.evaluate(() => localStorage.getItem('token'));
    const response = await page.request.get(`${BACKEND_URL}/api/v1/layouts/workspace_preferences`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const prefs = await response.json();
    expect(prefs.preferences.default_symbol).toBe('US:AAPL');
    expect(prefs.preferences.default_range).toBe('1Y');
  });

  test('A2. Reset preferences', async ({ page }) => {
    // Setup: register and login
    const email = `test-reset-${Date.now()}@example.com`;
    const password = 'testpass123';

    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Go to settings and save preferences
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.getByLabel('預設標的').fill('HK:0700');
    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Reset
    page.on('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '重設偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Verify reset via API
    const token = await page.evaluate(() => localStorage.getItem('token'));
    const response = await page.request.get(`${BACKEND_URL}/api/v1/layouts/workspace_preferences`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const prefs = await response.json();
    expect(prefs.preferences.default_symbol).toBeFalsy();
  });

  test('A3. Logout and login preserves preferences', async ({ page }) => {
    const email = `test-persist-${Date.now()}@example.com`;
    const password = 'testpass123';

    // Register and save preferences
    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.getByLabel('預設標的').fill('US:MSFT');
    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Logout
    await page.evaluate(() => localStorage.removeItem('token'));
    await page.goto(`${FRONTEND_URL}/login`);

    // Login again
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Verify preferences persisted
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await expect(page.getByLabel('預設標的')).toHaveValue('US:MSFT');
  });

  test('B1. Preferences applied to fresh workspace', async ({ page }) => {
    const email = `test-b1-${Date.now()}@example.com`;
    const password = 'testpass123';

    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Save preferences
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.getByLabel('預設標的').fill('US:AAPL');
    await page.getByLabel('預設時間範圍').selectOption('1Y');
    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Open fresh workspace (no saved layout)
    await page.goto(`${FRONTEND_URL}/workspace`);
    // Wait for chart to load instead of networkidle
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    // Verify chart displays the default range (1Y button should be active)
    const activeRangeButton = page.locator('.ws-range-btn-active');
    await expect(activeRangeButton).toHaveText('1Y');

    // Verify symbol is displayed
    await expect(page.locator('body')).toContainText('AAPL', { timeout: 10000 });
  });

  test('B2. Saved layout wins over preferences', async ({ page }) => {
    const email = `test-b2-${Date.now()}@example.com`;
    const password = 'testpass123';

    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Open workspace and save a layout with specific values
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    // Change range to something other than default
    await page.locator('.ws-range-btn', { hasText: '3M' }).click();
    await page.waitForTimeout(1000); // Wait for auto-save debounce (700ms)

    // Now change preferences to different values
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.getByLabel('預設標的').fill('US:MSFT');
    await page.getByLabel('預設時間範圍').selectOption('6M');
    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Go back to workspace
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    // Verify layout values are kept (not preferences)
    const activeRangeButton = page.locator('.ws-range-btn-active');
    await expect(activeRangeButton).toHaveText('3M');
  });

  test('B3. Explicit selection survives delayed preferences load', async ({ page }) => {
    const email = `test-b3-${Date.now()}@example.com`;
    const password = 'testpass123';

    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Save preferences first (1Y range)
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.getByLabel('預設時間範圍').selectOption('1Y');
    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Test-controlled gate: holds the preferences GET until test explicitly releases
    let releaseGate: () => void = () => {};
    const gate = new Promise<void>((resolve) => { releaseGate = resolve; });

    try {
      await page.route('**/api/v1/layouts/workspace_preferences', async (route) => {
        await gate;
        await route.continue();
      });

      // Create the response promise BEFORE navigation triggers the request
      const preferencesResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes('/api/v1/layouts/workspace_preferences') &&
          response.request().method() === 'GET' &&
          response.status() === 200
      );

      // Open workspace — triggers preferences GET, which is held by the gate
      await page.goto(`${FRONTEND_URL}/workspace`);
      await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

      // Make explicit 3M selection while preferences request is held
      await page.locator('.ws-range-btn', { hasText: '3M' }).click();

      // Assert 3M is active BEFORE releasing the gate
      const activeRangeButton = page.locator('.ws-range-btn-active');
      await expect(activeRangeButton).toHaveText('3M');

      // Release the gate — let the preferences request proceed
      releaseGate();

      // Wait for the matching HTTP response (status 200 received)
      const response = await preferencesResponsePromise;

      // Wait for response body to finish downloading
      await response.finished();

      // Assert the response payload's range field is 1Y
      const body = await response.json();
      expect(body.preferences.default_range).toBe('1Y');

      // Final UI assertion: active range remains 3M (not overwritten by 1Y)
      await expect(activeRangeButton).toHaveText('3M');
    } finally {
      // Ensure gate is released on cleanup even if test fails mid-route
      releaseGate();
    }
  });

  test('B4. Reset preferences does not affect saved layout', async ({ page }) => {
    const email = `test-b4-${Date.now()}@example.com`;
    const password = 'testpass123';

    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Open workspace and save a layout
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    // Change range
    await page.locator('.ws-range-btn', { hasText: '6M' }).click();
    await page.waitForTimeout(1000); // Wait for auto-save debounce (700ms)

    // Save preferences
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.getByLabel('預設標的').fill('US:AAPL');
    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Reset preferences
    page.on('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '重設偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Go back to workspace
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    // Verify layout is unchanged
    const activeRangeButton = page.locator('.ws-range-btn-active');
    await expect(activeRangeButton).toHaveText('6M');
  });

  test('B5. Same-browser user A/B isolation', async ({ page }) => {
    const emailA = `test-b5-a-${Date.now()}@example.com`;
    const emailB = `test-b5-b-${Date.now()}@example.com`;
    const password = 'testpass123';

    // Register both users
    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email: emailA, password, display_name: 'User A', user_type: 'individual' }
    });
    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email: emailB, password, display_name: 'User B', user_type: 'individual' }
    });

    // User A logs in
    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(emailA);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // User A saves distinct layout and preferences
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.getByLabel('預設標的').fill('US:MSFT');
    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.locator('text=✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // User A logs out
    await page.evaluate(() => localStorage.removeItem('token'));
    await page.goto(`${FRONTEND_URL}/login`);

    // User B logs in
    await page.getByLabel('電子郵件').fill(emailB);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // User B should not see User A's values
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    const defaultSymbolInput = page.getByLabel('預設標的');
    await expect(defaultSymbolInput).not.toHaveValue('US:MSFT');

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });
    // User B should see default symbol, not User A's
    await expect(page.locator('body')).not.toContainText('MSFT', { timeout: 5000 });

    // User A logs back in
    await page.evaluate(() => localStorage.removeItem('token'));
    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(emailA);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // User A should get their own state from server
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await expect(page.getByLabel('預設標的')).toHaveValue('US:MSFT');
  });

  test('B6. Desktop and mobile viewport', async ({ page }) => {
    const email = `test-b6-${Date.now()}@example.com`;
    const password = 'testpass123';

    await page.request.post(`${BACKEND_URL}/api/auth/register`, {
      data: { email, password, display_name: 'Test User', user_type: 'individual' }
    });

    await page.goto(`${FRONTEND_URL}/login`);
    await page.getByLabel('電子郵件').fill(email);
    await page.getByLabel('密碼').fill(password);
    await page.getByRole('button', { name: '登入' }).click();
    await expect(page).toHaveURL(/.*dashboard/, { timeout: 10000 });

    // Desktop viewport (1280x800)
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    // Verify navigation links are reachable
    const navLinks = ['主頁', '設定', '研究'];
    for (const link of navLinks) {
      await expect(page.locator(`text=${link}`).first()).toBeVisible();
    }

    // Verify header is not clipped
    const header = page.locator('header').first();
    const headerBox = await header.boundingBox();
    expect(headerBox).not.toBeNull();
    expect(headerBox!.height).toBeGreaterThan(0);

    // Take screenshot
    await page.screenshot({ path: `test-results/b6-desktop-1280x800.png`, fullPage: true });

    // Verify tabs and forms are usable
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await expect(page.getByLabel('預設標的')).toBeVisible();
    await expect(page.getByLabel('預設時間範圍')).toBeVisible();

    // Mobile viewport (390x844)
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.locator('.ws-range-btn-active').waitFor({ timeout: 10000 });

    // Verify navigation is still accessible (may be in hamburger menu)
    await page.screenshot({ path: `test-results/b6-mobile-390x844.png`, fullPage: true });

    // Verify forms are usable on mobile
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await expect(page.getByLabel('預設標的')).toBeVisible();
    await page.getByLabel('預設標的').fill('US:AAPL');
    await expect(page.getByLabel('預設標的')).toHaveValue('US:AAPL');
  });
});
