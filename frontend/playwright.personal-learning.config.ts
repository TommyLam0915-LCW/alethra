import { defineConfig, devices } from '@playwright/test';

const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://127.0.0.1:3002';

export default defineConfig({
  testDir: './e2e',
  testMatch: /personal-strategy-learning\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results/personal-learning',
  // The journey trains two models through a separate worker process, so the
  // default 30s test budget is not enough.
  timeout: 600000,
  use: {
    baseURL: FRONTEND_BASE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
