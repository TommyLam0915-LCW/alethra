import { defineConfig, devices } from '@playwright/test';

const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://127.0.0.1:3100';

export default defineConfig({
  testDir: './e2e',
  testMatch: /first-use-journey\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results/first-use',
  timeout: 180000,
  use: {
    baseURL: FRONTEND_BASE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
