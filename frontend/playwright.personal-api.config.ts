import { defineConfig, devices } from '@playwright/test';

// Isolated runner for the §6 personal-API trade-visibility check.
// The stack is created by ./start_e2e_research_flow.sh, so this config never
// starts or stops a server.
//
// The spec registers a one-off account and types its generated password into the
// login form, so traces stay off: a trace stores DOM snapshots that would carry
// the value. Failure diagnostics come from the assertion messages instead.
const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';

export default defineConfig({
  testDir: './e2e',
  testMatch: /personal-api-trade-visibility\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results/personal-api',
  timeout: 120000,
  use: {
    baseURL: FRONTEND_BASE,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
