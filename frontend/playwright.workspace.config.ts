import { defineConfig, devices } from '@playwright/test';

// Isolated runner for the interception-based workspace shell spec.
// The host `next dev` server on :3099 is already up, so this config never
// starts or stops a server — the shared playwright.config.ts still points at
// the dockerised :3001 stack used by CI.
const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';
// Workers inherit this, and the spec reads it at module scope, so it must be set
// here rather than through the config-level `env` option.
process.env.E2E_FRONTEND_BASE = FRONTEND_BASE;

export default defineConfig({
  testDir: './e2e',
  testMatch: /workspace\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results/workspace',
  timeout: 120000,
  use: {
    baseURL: FRONTEND_BASE,
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
