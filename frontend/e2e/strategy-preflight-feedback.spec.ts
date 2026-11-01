import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked browser acceptance for the two 2026-11-01 read-only surfaces:
 * the strategy template catalogue/preflight and the feedback draft.
 *
 * Not run in the implementation round (no stack was started). When run, it
 * targets the same env-overridable stack as the other specs and expects the
 * fixture account below to exist.
 */

const API_BASE = process.env.E2E_BACKEND_BASE ?? 'http://localhost:8001';
const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3001';

const USER = {
  email: process.env.E2E_RESEARCH_EMAIL || 'user_a_test@example.com',
  password: process.env.ACCEPTANCE_PASSWORD_A || 'TestPassword123!@#',
};

async function login(page: Page) {
  await page.goto(`${FRONTEND_BASE}/login`);
  await page.fill('input[type="email"]', USER.email);
  await page.fill('input[type="password"]', USER.password);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard');
}

function mutatingRequests(page: Page) {
  const requests: string[] = [];
  page.on('request', (request) => {
    const method = request.method().toUpperCase();
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      requests.push(`${method} ${new URL(request.url()).pathname}`);
    }
  });
  return requests;
}

test.describe('strategy template catalogue and preflight', () => {
  test('catalogue shows only runnable templates and marks the rest unavailable', async ({ page }) => {
    await login(page);
    await page.goto(`${FRONTEND_BASE}/strategy-templates`);

    const catalogue = page.getByTestId('template-catalog');
    await expect(catalogue.getByTestId('template-card-buy_and_hold')).toBeVisible();
    await expect(catalogue.getByTestId('template-card-sma_crossover')).toBeVisible();
    await expect(catalogue.getByTestId('template-card-price_change_fixed_hold')).toBeVisible();

    const unsupported = page.getByTestId('unsupported-templates');
    await expect(unsupported.getByTestId('unsupported-mean_reversion_zscore')).toContainText(
      'No mean-reversion engine is implemented',
    );
    // Unsupported capabilities must not offer a creation control.
    await expect(unsupported.getByRole('button')).toHaveCount(0);
    await expect(unsupported.getByRole('link')).toHaveCount(0);
  });

  test('invalid parameters are reported and a valid preflight creates nothing', async ({ page }) => {
    await login(page);
    const requests = mutatingRequests(page);
    await page.goto(`${FRONTEND_BASE}/strategy-templates`);

    const panel = page.getByTestId('preflight-panel');
    await panel.getByTestId('preflight-template').selectOption('sma_crossover');
    const editor = panel.getByTestId('preflight-spec');
    await expect(editor).toHaveValue(/"short_window": 10/);

    const spec = JSON.parse(await editor.inputValue());
    spec.signal_parameters = { short_window: 30, long_window: 10 };
    await editor.fill(JSON.stringify(spec, null, 2));
    await panel.getByTestId('preflight-submit').click();

    const invalid = page.getByTestId('preflight-result');
    await expect(invalid).toHaveAttribute('data-status', 'invalid');
    await expect(invalid.getByTestId('preflight-error-signal_parameters')).toContainText(
      'short_window must be less than long_window',
    );

    await panel.getByRole('button', { name: 'Reset to example' }).click();
    await panel.getByTestId('preflight-submit').click();

    const valid = page.getByTestId('preflight-result');
    await expect(valid).toHaveAttribute('data-status', 'valid');
    await expect(valid.getByTestId('preflight-status')).toHaveText('Matches the current contract');
    await expect(valid.getByTestId('preflight-continue')).toHaveAttribute('href', '/experiments/new');

    // Preflight success must not navigate, create or run anything.
    await expect(page).toHaveURL(/\/strategy-templates$/);
    expect(requests.filter((entry) => entry.includes('/experiments'))).toEqual([]);
    expect(requests.filter((entry) => entry.includes('/runs'))).toEqual([]);
    expect(
      requests.filter((entry) => entry === 'POST /api/v1/strategy-specs/preflight').length,
    ).toBeGreaterThan(0);
  });
});

test.describe('feedback draft', () => {
  test('preview is labelled unsubmitted and copy/export sends nothing', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await login(page);
    const requests = mutatingRequests(page);
    await page.goto(`${FRONTEND_BASE}/feedback`);

    await expect(page.getByTestId('feedback-status')).toContainText('Draft only — not submitted');

    const section = page.getByRole('region', { name: 'Feedback draft fields' });
    await section.getByTestId('feedback-category').selectOption('strategy_setup');
    await section.getByTestId('feedback-page').fill('Experiment settings');
    await section.getByTestId('feedback-steps').fill('Entered a 0.0005 commission.');
    await section.getByTestId('feedback-expected').fill('The value is kept.');
    await section.getByTestId('feedback-actual').fill('The form rejected it.');
    await section.getByTestId('feedback-reference').fill('req-123');

    const preview = page.getByTestId('feedback-preview');
    await expect(preview).toContainText('not submitted');
    await expect(preview).toContainText('Entered a 0.0005 commission.');
    await expect(preview).toContainText('req-123');
    await expect(preview).not.toContainText(/ticket #\d+/i);

    await page.getByTestId('feedback-copy').click();
    await expect(page.getByTestId('feedback-copied')).toContainText('still has not been submitted');

    const downloadPromise = page.waitForEvent('download');
    await page.getByTestId('feedback-download').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/^feedback-draft-\d{4}-\d{2}-\d{2}\.md$/);

    expect(requests).toEqual([]);
  });
});
