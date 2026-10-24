/**
 * Settings UI Acceptance Tests
 *
 * Verifies the settings page functionality:
 * - A1: Login and access settings, save workspace preferences
 * - A2: Reset preferences
 * - A3: Logout and login preserves preferences
 */

import { test, expect } from '@playwright/test';

const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:8001';
const FRONTEND_URL = process.env.FRONTEND_URL || 'http://localhost:3004';
const TEST_EMAIL = 'test@example.com';
const TEST_PASSWORD = 'testpassword123';

test.describe('Settings UI Acceptance', () => {
  test.beforeEach(async ({ page }) => {
    // Set longer timeout for authentication flows
    test.setTimeout(60000);
  });

  test('A1: Login and access settings, save preferences', async ({ page }) => {
    // Navigate to login page
    await page.goto(`${FRONTEND_URL}/login`);

    // Fill login form - use type selectors since labels aren't properly associated
    await page.locator('input[type="email"]').fill(TEST_EMAIL);
    await page.locator('input[type="password"]').fill(TEST_PASSWORD);

    // Click login button
    await page.getByRole('button', { name: /登入|處理中/ }).click();

    // Wait for login to complete (token should be set in localStorage)
    await page.waitForTimeout(2000);

    // Navigate to settings
    await page.goto(`${FRONTEND_URL}/settings`);

    // Wait for settings page to load
    await page.waitForLoadState('networkidle');

    // Click on "工作台偏好" tab
    await page.getByRole('button', { name: '工作台偏好' }).click();

    // Wait for preferences to load
    await page.waitForTimeout(1000);

    // Fill in default symbol
    const symbolInput = page.locator('input[type="text"]');
    await symbolInput.clear();
    await symbolInput.fill('US:AAPL');

    // Select default range
    const rangeSelect = page.locator('select');
    await rangeSelect.selectOption('1Y');

    // Save preferences
    await page.getByRole('button', { name: '儲存偏好' }).click();

    // Wait for save confirmation
    await expect(page.getByText('✓ 已儲存')).toBeVisible({ timeout: 5000 });
  });

  test('A2: Reset preferences', async ({ page }) => {
    // Login first
    await page.goto(`${FRONTEND_URL}/login`);
    await page.locator('input[type="email"]').fill(TEST_EMAIL);
    await page.locator('input[type="password"]').fill(TEST_PASSWORD);
    await page.getByRole('button', { name: /登入|處理中/ }).click();
    await page.waitForTimeout(2000);

    // Navigate to settings
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.waitForLoadState('networkidle');

    // Click on "工作台偏好" tab
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.waitForTimeout(1000);

    // Handle confirm dialog
    page.once('dialog', async (dialog) => {
      expect(dialog.message()).toContain('確定要重設工作台偏好嗎');
      await dialog.accept();
    });

    // Click reset button
    await page.getByRole('button', { name: '重設偏好' }).click();

    // Wait for reset confirmation
    await expect(page.getByText('✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Verify fields are cleared/reset
    const symbolInput = page.locator('input[type="text"]');
    await expect(symbolInput).toHaveValue('');

    const rangeSelect = page.locator('select');
    await expect(rangeSelect).toHaveValue('6M');
  });

  test('A3: Logout and login preserves preferences', async ({ page }) => {
    // Login
    await page.goto(`${FRONTEND_URL}/login`);
    await page.locator('input[type="email"]').fill(TEST_EMAIL);
    await page.locator('input[type="password"]').fill(TEST_PASSWORD);
    await page.getByRole('button', { name: /登入|處理中/ }).click();
    await page.waitForTimeout(2000);

    // Navigate to settings and save a specific preference
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.waitForTimeout(1000);

    const symbolInput = page.locator('input[type="text"]');
    await symbolInput.clear();
    await symbolInput.fill('HK:0700');

    const rangeSelect = page.locator('select');
    await rangeSelect.selectOption('3M');

    await page.getByRole('button', { name: '儲存偏好' }).click();
    await expect(page.getByText('✓ 已儲存')).toBeVisible({ timeout: 5000 });

    // Logout by clearing localStorage
    await page.evaluate(() => {
      localStorage.clear();
    });

    // Navigate to login
    await page.goto(`${FRONTEND_URL}/login`);

    // Login again
    await page.locator('input[type="email"]').fill(TEST_EMAIL);
    await page.locator('input[type="password"]').fill(TEST_PASSWORD);
    await page.getByRole('button', { name: /登入|處理中/ }).click();
    await page.waitForTimeout(2000);

    // Navigate back to settings
    await page.goto(`${FRONTEND_URL}/settings`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: '工作台偏好' }).click();
    await page.waitForTimeout(1000);

    // Verify preferences are preserved
    await expect(page.locator('input[type="text"]')).toHaveValue('HK:0700');
    await expect(page.locator('select')).toHaveValue('3M');
  });
});
