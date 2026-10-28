import { defineConfig, devices } from '@playwright/test';

// Isolated runner for the TIER B replay regression: the browser talks to the real
// local backend and its real stored dataset, with no request interception. The stack is
// created by ./start_e2e_research_flow.sh, so this config never starts or stops a server.
// Credentials must come from the generated git-ignored file (E2E_CRED_FILE).
const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';
process.env.E2E_FRONTEND_BASE = FRONTEND_BASE;

// Credential hygiene, specific to this tier: it types the REAL generated password, and on
// a failed test Playwright captures `page.ariaSnapshot({ mode: 'ai' })` into
// error-context.md, which reproduces a password field's value in cleartext (a login
// failure did exactly that here). 1.64 offers no `snapshot` use-option; the capture is
// gated by PLAYWRIGHT_NO_COPY_PROMPT inside the test fixture, so it is set for this
// runner only. Screenshots are unaffected (the browser renders the field masked).
// Re-verify after a Playwright upgrade with a deliberately wrong E2E_PASSWORD and
// `grep -c` for the injected value under test-results/replay-backend.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1';

export default defineConfig({
  testDir: './e2e',
  testMatch: /workspace-replay-backend\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results/replay-backend',
  timeout: 90_000,
  use: {
    baseURL: FRONTEND_BASE,
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
