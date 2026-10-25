import { defineConfig, devices } from '@playwright/test';

// Isolated runner for the research-flow regression. The stack is created by
// ./start_e2e_research_flow.sh, so this config never starts or stops a server.
const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';

export default defineConfig({
  testDir: './e2e',
  testMatch: /unified-research-flow\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results/research-flow',
  use: {
    baseURL: FRONTEND_BASE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
