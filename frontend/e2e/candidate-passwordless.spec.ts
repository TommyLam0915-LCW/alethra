import { test, expect, type Page } from '@playwright/test';

/**
 * Tracked regression for the local password-free candidate entry.
 *
 * WHAT THIS PROVES
 *   - with the candidate profile's PASSWORDLESS mode ON, the homepage offers an
 *     explicit, clearly labelled local-test entry that mints one short-lived
 *     session for the configured account and lands in the authed workspace,
 *     with a persistent "local test mode / synthetic data" label;
 *   - opening the homepage or the ordinary sign-in page never mints a session
 *     on its own: development access requires an explicit click;
 *   - the identity is stable across reload (same server-issued subject);
 *   - the scenario switcher can only move between the roles the backend
 *     allowlists, and refuses an unlisted role;
 *   - switching scenarios replaces the stored session, and every request in
 *     the reloaded document carries the new identity (no cached old token);
 *   - an expired test session is renewed once in place, so the page (and any
 *     unsaved input) survives, with exactly one renewal POST;
 *   - when the mode is turned off mid-session the test banner disappears while
 *     the already-issued token keeps working until its own expiry;
 *   - an expired session that cannot be renewed returns to sign-in with a
 *     single renewal attempt (no retry loop) and no leftover token/marker;
 *   - the ordinary email/password login remains available;
 *   - the panel states it is a development-only test entry, not a customer
 *     sign-in method;
 *   - registration cannot be submitted until the risk disclosure at /legal is
 *     acknowledged, and leaving register mode clears that acknowledgement;
 *   - with the mode OFF the homepage stays public, no session is minted, and
 *     the login page shows no candidate panel.
 *
 * WHAT THIS DOES NOT PROVE
 *   - remote peers are refused: a Playwright client is always loopback, so the
 *     TCP-peer guard is proven by backend tests, not here;
 *   - production config refusal: also a backend settings test;
 *   - model/signal efficacy: out of scope.
 *
 * GATING
 *   Runs only when explicitly pointed at a loopback candidate stack:
 *     E2E_CANDIDATE_PASSWORDLESS=on   stacked with the profile mode enabled
 *     E2E_CANDIDATE_PASSWORDLESS=off  the same stack after disabling the mode
 *   API_BASE is optional and only used for the direct allowlist checks.
 */

const BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3001';
const MODE = process.env.E2E_CANDIDATE_PASSWORDLESS ?? '';
const API_BASE = process.env.E2E_API_BASE ?? '';
const host = (() => {
  try {
    return new URL(BASE).hostname;
  } catch {
    return '';
  }
})();
const LOOPBACK = host === '127.0.0.1' || host === 'localhost' || host === '[::1]' || host === '::1';
const READY = LOOPBACK && (MODE === 'on' || MODE === 'off');
const SKIP =
  'set E2E_CANDIDATE_PASSWORDLESS=on|off and point E2E_FRONTEND_BASE at a loopback candidate frontend';

interface Claims {
  sub?: string;
  exp?: number;
  role?: string;
  cand?: string;
}

function decodeClaims(token: string | null): Claims | null {
  if (!token) return null;
  const raw = token.split('.')[1];
  if (!raw) return null;
  let part = raw.replace(/-/g, '+').replace(/_/g, '/');
  while (part.length % 4) part += '=';
  try {
    return JSON.parse(Buffer.from(part, 'base64').toString('utf8'));
  } catch {
    return null;
  }
}

async function readClaims(page: Page): Promise<Claims | null> {
  const token = await page.evaluate(() => localStorage.getItem('token'));
  return decodeClaims(token);
}

async function enterFromHomepage(page: Page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByTestId('candidate-entry-cta').click();
  await page.waitForURL((url) => url.pathname === '/dashboard', { timeout: 30000 });
  await expect(page.getByTestId('candidate-mode-bar')).toBeVisible();
}

test('homepage offers an explicit test entry that mints one session', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  const sessionPosts: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/auth/candidate-session')) {
      sessionPosts.push(request.url());
    }
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const cta = page.getByTestId('candidate-entry-cta');
  await expect(cta).toBeVisible();
  await expect(page.getByTestId('candidate-entry-note')).toContainText('synthetic data');
  expect(sessionPosts).toHaveLength(0);
  expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();

  await cta.click();
  await page.waitForURL((url) => url.pathname === '/dashboard', { timeout: 30000 });
  await expect(page.getByTestId('candidate-mode-bar')).toBeVisible();

  expect(sessionPosts).toHaveLength(1);
  await expect(page.getByTestId('candidate-mode-label')).toContainText('本機測試模式');
  await expect(page.getByTestId('candidate-mode-bar')).toContainText('合成資料');

  const claims = await readClaims(page);
  expect(claims).not.toBeNull();
  expect(claims!.cand).toBe('local_passwordless');
  expect(typeof claims!.sub).toBe('string');
  expect(claims!.sub!.length).toBeGreaterThan(0);
  expect(typeof claims!.role).toBe('string');
  expect(claims!.role!.length).toBeGreaterThan(0);

  const modeRaw = await page.evaluate(() => localStorage.getItem('candidate_mode'));
  expect(modeRaw).toContain('本機測試模式');
});

test('opening the ordinary sign-in page mints no candidate session', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  const sessionPosts: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/auth/candidate-session')) {
      sessionPosts.push(request.url());
    }
  });

  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('candidate-entry-panel')).toBeVisible();
  await expect(page.locator('.login-form-zone input[type="email"]')).toBeVisible();
  await page.waitForLoadState('networkidle');

  expect(sessionPosts).toHaveLength(0);
  expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('candidate_mode'))).toBeNull();
  expect(new URL(page.url()).pathname).toBe('/login');
});

test('reload keeps the same candidate identity', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await enterFromHomepage(page);
  const before = await readClaims(page);
  expect(before?.sub).toBeTruthy();

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('candidate-mode-bar')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/dashboard');

  const after = await readClaims(page);
  expect(after?.sub).toBe(before!.sub);
  expect(after?.exp).toBe(before!.exp);
  // A reload must not silently mint a second session.
  await expect(page.getByTestId('candidate-mode-label')).toContainText('本機測試模式');
});

test('scenario switch moves only between configured accounts', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await enterFromHomepage(page);
  const select = page.getByTestId('candidate-role-select');
  if ((await select.count()) === 0) {
    test.skip(true, 'this candidate profile lists a single allowlisted account');
  }

  const options = await select
    .locator('option')
    .evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value));
  const current = await select.inputValue();
  const target = options.find((role) => role !== current);
  expect(target).toBeTruthy();

  const before = await readClaims(page);
  // The switcher writes the new role and then reloads the document, so wait for
  // the reload's load event rather than polling localStorage: the storage value
  // is written slightly before the old execution context is destroyed.
  await page.waitForLoadState('load');
  const reloaded = page.waitForEvent('load', { timeout: 30000 });
  await select.selectOption(target!);
  await reloaded;

  await expect(page.getByTestId('candidate-mode-bar')).toBeVisible();
  const after = await readClaims(page);
  expect(after?.role).toBe(target);
  expect(after?.sub).not.toBe(before?.sub);
  await expect(page.getByTestId('candidate-mode-label')).toContainText('本機測試模式');
});

test('scenario switch leaves no trace of the previous account', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await enterFromHomepage(page);
  const select = page.getByTestId('candidate-role-select');
  if ((await select.count()) === 0) {
    test.skip(true, 'this candidate profile lists a single allowlisted account');
  }

  const options = await select
    .locator('option')
    .evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value));
  const current = await select.inputValue();
  const target = options.find((role) => role !== current);
  expect(target).toBeTruthy();

  const oldToken = await page.evaluate(() => localStorage.getItem('token'));
  const before = decodeClaims(oldToken);
  expect(before?.sub).toBeTruthy();

  await page.waitForLoadState('load');
  const reloaded = page.waitForEvent('load', { timeout: 30000 });
  await select.selectOption(target!);
  await reloaded;
  await page.waitForURL((url) => url.pathname === '/dashboard', { timeout: 30000 });
  await expect(page.getByTestId('candidate-mode-bar')).toBeVisible();

  const after = await readClaims(page);
  expect(after?.sub).not.toBe(before?.sub);
  expect(after?.role).toBe(target);

  // Reload once more with the new identity in place and check the headers the
  // fresh document actually sends: every authenticated call must carry the new
  // token, and the previous account's token must survive nowhere in storage.
  const bearers: string[] = [];
  page.on('request', (request) => {
    const header = request.headers()['authorization'];
    if (header?.startsWith('Bearer ')) bearers.push(header.slice(7));
  });
  await page.waitForLoadState('load');
  const second = page.waitForEvent('load', { timeout: 30000 });
  await page.reload();
  await second;
  await page.waitForURL((url) => url.pathname === '/dashboard', { timeout: 30000 });
  await expect.poll(() => bearers.length, { timeout: 15000 }).toBeGreaterThan(0);

  for (const bearer of bearers) {
    expect(bearer).not.toBe(oldToken);
    expect(decodeClaims(bearer)?.sub).toBe(after?.sub);
  }
  const stored = await page.evaluate(() => JSON.stringify(localStorage));
  expect(stored).not.toContain(oldToken!);
  await expect(page.getByTestId('candidate-mode-label')).toContainText('本機測試模式');
});

test('expired test session renews once in place and keeps unsaved input', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await enterFromHomepage(page);
  await page.goto('/workspace', { waitUntil: 'domcontentloaded' });
  const search = page.getByRole('combobox', { name: '搜尋代號' });
  await expect(search).toBeVisible();
  await expect(page.getByTestId('evidence-dock')).toBeVisible();
  const before = await readClaims(page);
  expect(before?.sub).toBeTruthy();

  const renewals: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/auth/candidate-session')) {
      renewals.push(request.url());
    }
  });

  // Compose unsaved input before the session dies: the search box is component
  // state, so any hard navigation would drop it.
  await search.fill('AAPL');
  await expect(search).toHaveValue('AAPL');

  // A syntactically valid but unverifiable token expires the session without
  // waiting 30 minutes; the page itself must not be reloaded or redirected.
  await page.evaluate(() => {
    localStorage.setItem('token', 'expired.invalid.token');
    (window as unknown as { __pageMarker?: string }).__pageMarker = 'alive';
  });

  // The instruments catalogue is a public endpoint, so typing proves nothing
  // about the session. The dock's reload button is a real authenticated read:
  // against the dead token it must renew once and retry, in place.
  const retried = page.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/paper-portfolio') &&
      response.request().method() === 'GET' &&
      response.status() === 200,
    { timeout: 20000 },
  );
  await page.getByTestId('evidence-reload').click();
  await retried;

  expect(new URL(page.url()).pathname).toBe('/workspace');
  expect(await page.evaluate(() => (window as unknown as { __pageMarker?: string }).__pageMarker)).toBe(
    'alive',
  );
  await expect(search).toHaveValue('AAPL');
  expect(renewals).toHaveLength(1);

  const after = await readClaims(page);
  expect(after?.sub).toBe(before?.sub);
  expect(after?.role).toBe(before?.role);
  expect(after?.cand).toBe('local_passwordless');

  // No retry loop: once the renewed session is in place, nothing renews again.
  await page.waitForTimeout(1500);
  expect(renewals).toHaveLength(1);
});

test('mode turned off mid-session clears the banner but keeps the session', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await enterFromHomepage(page);
  const token = await page.evaluate(() => localStorage.getItem('token'));
  expect(token).toBeTruthy();

  // The profile switched off while this session is still live: the status
  // endpoint reports disabled, but the token is an ordinary JWT that remains
  // valid until its own expiry.
  await page.route('**/api/auth/candidate-session', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          enabled: false,
          synthetic: true,
          label_zh: null,
          session_minutes: null,
          accounts: [],
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('app-bar')).toBeVisible();
  await expect(page.getByTestId('candidate-mode-bar')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('candidate_mode'))).toBeNull();
  expect(new URL(page.url()).pathname).toBe('/dashboard');
  expect(await page.evaluate(() => localStorage.getItem('token'))).toBe(token);
  await expect(page.getByTestId('app-logout')).toBeVisible();
});

test('expired session that cannot renew returns to sign-in once', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await enterFromHomepage(page);

  const renewals: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/auth/candidate-session')) {
      renewals.push(request.url());
    }
  });
  await page.route('**/api/auth/candidate-session', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Candidate test mode is not enabled' }),
      });
      return;
    }
    await route.continue();
  });

  await page.evaluate(() => localStorage.setItem('token', 'expired.invalid.token'));
  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  await page.waitForURL((url) => url.pathname === '/login', { timeout: 30000 });

  await expect(page.locator('.login-form-zone input[type="email"]')).toBeVisible();
  expect(renewals).toHaveLength(1);
  expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('candidate_mode'))).toBeNull();
});

test('ordinary login stays available after logging out of a candidate session', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await enterFromHomepage(page);
  await page.getByTestId('app-logout').click();
  await page.waitForURL((url) => url.pathname === '/login', { timeout: 30000 });

  const zone = page.locator('.login-form-zone');
  await expect(zone.locator('input[type="email"]')).toBeVisible();
  await expect(zone.locator('input[type="password"]')).toBeVisible();
  await expect(zone.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  // The password-free panel is additive; it must not replace the form.
  await expect(page.getByTestId('candidate-entry-panel')).toBeVisible();
});

test('running backend refuses roles outside the allowlist', async ({ request }) => {
  test.skip(!READY || MODE !== 'on' || !API_BASE, `${SKIP}; also needs E2E_API_BASE for direct checks`);

  const unknown = await request.post(`${API_BASE}/api/auth/candidate-session`, {
    data: { role: 'admin' },
  });
  expect(unknown.status()).toBe(400);

  const extra = await request.post(`${API_BASE}/api/auth/candidate-session`, {
    data: { role: 'owner', user_id: '00000000-0000-0000-0000-000000000000' },
  });
  expect(extra.status()).toBe(422);

  const status = await request.get(`${API_BASE}/api/auth/candidate-session`);
  expect(status.ok()).toBeTruthy();
  const body = await status.json();
  expect(body.enabled).toBe(true);
  expect(Array.isArray(body.accounts)).toBeTruthy();
  for (const account of body.accounts) {
    expect(Object.keys(account).sort()).toEqual(['label_zh', 'role']);
  }
});

test('mode off: homepage stays public and mints no session', async ({ page }) => {
  test.skip(!READY || MODE !== 'off', SKIP);

  const posts: string[] = [];
  const statusSeen = page.waitForResponse(
    (response) =>
      response.url().includes('/api/auth/candidate-session') &&
      response.request().method() === 'GET',
    { timeout: 30000 },
  );
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/auth/candidate-session')) {
      posts.push(request.url());
    }
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  const statusResponse = await statusSeen;
  expect(statusResponse.ok()).toBeTruthy();
  expect((await statusResponse.json()).enabled).toBe(false);

  await expect(
    page.getByRole('navigation', { name: 'Account' }).getByRole('link', { name: 'Sign in' })
  ).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/');
  expect(posts).toHaveLength(0);
  await expect(page.getByTestId('candidate-entry-cta')).toHaveCount(0);
  await expect(page.getByTestId('candidate-mode-bar')).toHaveCount(0);

  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.login-form-zone input[type="email"]')).toBeVisible();
  await expect(page.getByTestId('candidate-entry-panel')).toHaveCount(0);

  if (API_BASE) {
    const refused = await page.request.post(`${API_BASE}/api/auth/candidate-session`, {
      data: { role: 'owner' },
    });
    expect(refused.status()).toBe(404);
  }
});

test('the password-free panel states it is a development-only test entry', async ({ page }) => {
  test.skip(!READY || MODE !== 'on', SKIP);

  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('candidate-entry-panel')).toBeVisible();
  const scope = page.getByTestId('candidate-entry-scope');
  await expect(scope).toContainText('Development-only test entry');
  await expect(scope).toContainText('not a customer sign-in method');
});

test('registration is blocked until the risk disclosure is acknowledged', async ({ page }) => {
  test.skip(!READY, SKIP);

  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  const form = page.locator('.login-form-zone');
  const acknowledgement = form.getByTestId('risk-acknowledgement');
  const submit = form.getByRole('button', { name: 'Create account', exact: true });
  const toRegister = form.getByRole('button', { name: 'No account? Create one' });

  // Dev mode serves HTML before React hydrates, and a click issued in that
  // window is dropped. Retry the toggle until the client state actually
  // changes instead of sleeping for a guessed hydration delay.
  await expect(async () => {
    if ((await acknowledgement.count()) === 0) await toRegister.click();
    await expect(acknowledgement).toHaveCount(1);
  }).toPass({ timeout: 20_000 });

  await expect(acknowledgement).not.toBeChecked();
  await expect(submit).toBeDisabled();
  await expect(form.getByRole('link', { name: 'risk disclosure' })).toHaveAttribute('href', '/legal');

  await acknowledgement.check();
  await expect(submit).toBeEnabled();

  // Leaving register mode clears the acknowledgement; it must not carry over.
  await form.getByRole('button', { name: 'Already have an account? Sign in' }).click();
  await expect(acknowledgement).toHaveCount(0);
  await toRegister.click();
  await expect(acknowledgement).toHaveCount(1);
  await expect(acknowledgement).not.toBeChecked();
  await expect(submit).toBeDisabled();
});
