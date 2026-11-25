import { defineConfig, devices } from '@playwright/test';

// The specs navigate by this base, so the config must probe and serve the same
// one; hardcoding a port made the suite unrunnable against any other stack.
const FRONTEND_URL = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3001';

// `playwright install` has never been run here, so the bundled chromium is
// absent and every spec fails before the first assertion. A machine that
// already has Chrome can pass E2E_BROWSER_CHANNEL=chrome and drive the suite
// with no download; unset means the default bundled browser, unchanged.
const BROWSER_CHANNEL = process.env.E2E_BROWSER_CHANNEL;

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
    channel: BROWSER_CHANNEL,
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
