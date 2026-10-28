import { defineConfig, devices } from '@playwright/test';

// The specs navigate by this base, so the config must probe and serve the same
// one; hardcoding a port made the suite unrunnable against any other stack.
const FRONTEND_URL = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3001';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['list']
  ],
  use: {
    baseURL: FRONTEND_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'echo "Using existing acceptance stack"',
    url: FRONTEND_URL,
    reuseExistingServer: true,
  },
});
