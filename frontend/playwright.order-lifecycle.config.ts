import { defineConfig, devices } from '@playwright/test';

// Isolated runner for the order-lifecycle journey (#396). The stack is created by
// ./start_e2e_research_flow.sh, which also runs backend/scripts/seed_e2e_order_lifecycle.py,
// so this config never starts, stops or rebuilds a server.
// A second pass on the SAME live stack must re-run that seed first (it is idempotent):
// the previous pass leaves a completed session on the seeded version, and the journey
// opens by creating the one and only session there.
const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';

export default defineConfig({
  testDir: './e2e',
  testMatch: /order-lifecycle-flow\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // One test creates a session, links its account, opens /workspace and picks both the
  // plotted symbol and the traded account — that does not fit the 30s default. This is a
  // budget, not a wait: every step still resolves on its own locator or response.
  timeout: 120_000,
  reporter: [['list']],
  outputDir: 'test-results/order-lifecycle',
  use: {
    baseURL: FRONTEND_BASE,
    // The workspace puts ⑥/⑦/⑧ side by side above 1280px; below that the watchlist
    // collapses into a drawer and the journey would be clicking a different layout.
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
