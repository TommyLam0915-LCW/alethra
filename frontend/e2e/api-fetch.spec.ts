import { test, expect } from '@playwright/test';
import { apiFetch, apiFetchText, ApiError } from '../src/lib/api';

// Regression coverage for apiFetch using the existing Playwright runner
// (no additional test framework). global.fetch is stubbed; no server or
// browser page is involved.

const originalFetch = global.fetch;

test.afterEach(() => {
  global.fetch = originalFetch;
});

test.describe('API client regression (apiFetch)', () => {
  test('resolves without JSON parsing on 204 No Content', async () => {
    global.fetch = (async () => new Response(null, { status: 204 })) as typeof fetch;
    await expect(
      apiFetch('/api/v1/research-drafts/00000000-0000-0000-0000-000000000000', { method: 'DELETE' })
    ).resolves.toBeNull();
  });

  test('parses and returns JSON on success', async () => {
    global.fetch = (async () =>
      new Response(JSON.stringify({ id: 'abc', title: 'T' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    await expect(apiFetch('/api/v1/research-drafts/abc')).resolves.toEqual({ id: 'abc', title: 'T' });
  });

  test('throws the API error detail on non-success responses', async () => {
    global.fetch = (async () =>
      new Response(JSON.stringify({ detail: 'Draft not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    await expect(apiFetch('/api/v1/research-drafts/missing')).rejects.toThrow('Draft not found');
  });

  test('does not hide malformed JSON from a JSON-returning response', async () => {
    global.fetch = (async () =>
      new Response('<html>not json</html>', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    await expect(apiFetch('/api/v1/research-drafts/broken')).rejects.toThrow();
  });

  test('surfaces a structured detail object instead of "[object Object]"', async () => {
    global.fetch = (async () =>
      new Response(
        JSON.stringify({
          detail: {
            error: 'idempotency_key_conflict',
            message: 'idempotency_key was already used with a different order',
            existing_order_id: 'abc',
          },
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } }
      )) as typeof fetch;
    try {
      await apiFetch('/api/v1/paper-portfolio/1/orders', { method: 'POST' });
      throw new Error('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).message).toBe(
        'idempotency_key was already used with a different order'
      );
      expect((err as ApiError).message).not.toContain('[object Object]');
    }
  });

  test('surfaces the first validation message from a detail array', async () => {
    global.fetch = (async () =>
      new Response(
        JSON.stringify({
          detail: [{ loc: ['body', 'quantity'], msg: 'Input should be greater than 0' }],
        }),
        { status: 422, headers: { 'Content-Type': 'application/json' } }
      )) as typeof fetch;
    await expect(apiFetch('/api/v1/paper-portfolio/1/orders', { method: 'POST' })).rejects.toThrow(
      'body.quantity: Input should be greater than 0'
    );
  });
});

test.describe('ApiError class', () => {
  test('includes HTTP status code', async () => {
    global.fetch = (async () =>
      new Response(JSON.stringify({ detail: 'Not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    try {
      await apiFetch('/api/v1/test');
      throw new Error('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(404);
      expect((err as ApiError).message).toBe('Not found');
    }
  });

  test('distinguishes 404 from 500', async () => {
    global.fetch = (async () =>
      new Response(JSON.stringify({ detail: 'Internal error' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    try {
      await apiFetch('/api/v1/test');
      throw new Error('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(500);
    }
  });

  test('is an instance of Error for backward compatibility', async () => {
    global.fetch = (async () =>
      new Response(JSON.stringify({ detail: 'Bad request' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch;
    try {
      await apiFetch('/api/v1/test');
      throw new Error('Should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(Error);
      expect(err).toBeInstanceOf(ApiError);
    }
  });
});

// ---------------------------------------------------------------------------
// Session-expiry handling in the shared client (candidate mode)
//
// The password-free test session length is a configuration value (the profile
// currently sets 30 minutes); whatever the configured length, an expiry
// surfaces as a 401. These tests pin the client contract: renew the same
// allowlisted role once and retry, never renew on credential endpoints, and
// fall back to sign-in with a single attempt when renewal is refused. They
// stub fetch and the browser storage globals, so no server is needed.
// ---------------------------------------------------------------------------

interface FetchCall {
  url: string;
  method: string;
  auth: string | null;
}

interface FakeBrowser {
  store: Map<string, string>;
  location: { href: string };
}

const CANDIDATE_MARKER = JSON.stringify({
  label_zh: '本機測試模式 · 合成資料',
  session_minutes: 30,
  role: 'owner',
  accounts: [{ role: 'owner', label_zh: '主要測試帳戶' }],
});

function installBrowser(initial: Record<string, string> = {}): FakeBrowser {
  const store = new Map(Object.entries(initial));
  const location = { href: '/workspace' };
  const target = globalThis as unknown as {
    window?: { location: { href: string } };
    localStorage?: {
      getItem(key: string): string | null;
      setItem(key: string, value: string): void;
      removeItem(key: string): void;
    };
  };
  target.window = { location };
  target.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    setItem: (key, value) => {
      store.set(key, value);
    },
    removeItem: (key) => {
      store.delete(key);
    },
  };
  return { store, location };
}

function stubFetch(
  handler: (url: string, init?: RequestInit) => Response | Promise<Response>
): FetchCall[] {
  const calls: FetchCall[] = [];
  global.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init?.method ?? 'GET').toUpperCase();
    const headers = new Headers(init?.headers);
    calls.push({ url, method, auth: headers.get('authorization') });
    return handler(url, init);
  }) as typeof fetch;
  return calls;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const renewalCalls = (calls: FetchCall[]) =>
  calls.filter((call) => call.url.includes('/api/auth/candidate-session'));

function restoreGlobals() {
  const target = globalThis as unknown as { window?: unknown; localStorage?: unknown };
  delete target.window;
  delete target.localStorage;
}

test.describe('session expiry in candidate mode', () => {
  test.afterEach(() => {
    restoreGlobals();
  });

  test('renews once and retries the same request with the new token', async () => {
    const browser = installBrowser({ token: 'expired-old', candidate_mode: CANDIDATE_MARKER });
    const calls = stubFetch((url, init) => {
      const auth = new Headers(init?.headers).get('authorization');
      if (url.includes('/api/auth/candidate-session')) {
        expect(init?.method).toBe('POST');
        expect(auth).toBeNull();
        return json({
          access_token: 'fresh-token',
          token_type: 'bearer',
          expires_in: 1800,
          role: 'owner',
          mode_label_zh: '本機測試模式 · 合成資料',
          user: { id: 'user-1', email: 'candidate.owner@example.com' },
        });
      }
      if (auth === 'Bearer expired-old') return json({ detail: 'expired' }, 401);
      if (auth === 'Bearer fresh-token') return json({ ok: true });
      return json({ detail: 'unexpected' }, 500);
    });

    await expect(apiFetch('/api/v1/experiments/')).resolves.toEqual({ ok: true });

    expect(calls).toHaveLength(3);
    expect(calls[0].auth).toBe('Bearer expired-old');
    expect(renewalCalls(calls)).toHaveLength(1);
    expect(calls[2].auth).toBe('Bearer fresh-token');
    expect(browser.store.get('token')).toBe('fresh-token');
    expect(browser.location.href).toBe('/workspace');
    expect(JSON.parse(browser.store.get('candidate_mode')!).role).toBe('owner');
  });

  test('walks the CSV download path through the same renewal', async () => {
    installBrowser({ token: 'expired-old', candidate_mode: CANDIDATE_MARKER });
    const calls = stubFetch((url, init) => {
      const auth = new Headers(init?.headers).get('authorization');
      if (url.includes('/api/auth/candidate-session')) {
        return json({ access_token: 'fresh-token', role: 'owner', mode_label_zh: '' });
      }
      if (auth === 'Bearer expired-old') return json({ detail: 'expired' }, 401);
      return new Response('timestamp,pnl\n2026-10-30,0\n', { status: 200 });
    });

    await expect(apiFetchText('/api/v1/paper-portfolio/p1/journal?format=csv')).resolves.toContain(
      'timestamp,pnl'
    );
    expect(renewalCalls(calls)).toHaveLength(1);
    expect(calls).toHaveLength(3);
  });

  test('never renews on the credential endpoints themselves', async () => {
    installBrowser({ token: 'expired-old', candidate_mode: CANDIDATE_MARKER });
    const calls = stubFetch(() => json({ detail: 'mode is off' }, 401));

    await expect(apiFetch('/api/auth/candidate-session', { method: 'POST' })).rejects.toThrow(
      'Unauthorized'
    );
    // The only candidate-session request is the caller's own; renewal would
    // recurse into the same endpoint and is therefore excluded.
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe('POST');
  });

  test('does not renew when no local candidate marker exists', async () => {
    const browser = installBrowser({ token: 'expired-old' });
    const calls = stubFetch(() => json({ detail: 'expired' }, 401));

    await expect(apiFetch('/api/v1/experiments/')).rejects.toThrow('Unauthorized');
    expect(renewalCalls(calls)).toHaveLength(0);
    expect(calls).toHaveLength(1);
    // A normal expired session still returns to sign-in and is cleared.
    expect(browser.location.href).toBe('/login');
    expect(browser.store.has('token')).toBe(false);
  });

  // Keep this last: a refused renewal starts the client's 5-second cooldown,
  // which intentionally suppresses further renewal attempts.
  test('a refused renewal redirects once and does not retry in the cooldown window', async () => {
    const browser = installBrowser({ token: 'expired-old', candidate_mode: CANDIDATE_MARKER });
    const calls = stubFetch((url) => {
      if (url.includes('/api/auth/candidate-session')) return json({ detail: 'Not Found' }, 404);
      return json({ detail: 'expired' }, 401);
    });

    await expect(apiFetch('/api/v1/experiments/')).rejects.toThrow('Unauthorized');
    expect(calls).toHaveLength(2);
    expect(renewalCalls(calls)).toHaveLength(1);
    expect(browser.location.href).toBe('/login');
    expect(browser.store.has('token')).toBe(false);
    expect(browser.store.has('candidate_mode')).toBe(false);

    // A queued second request in the same burst must not mint another attempt.
    await expect(apiFetch('/api/v1/watchlists')).rejects.toThrow('Unauthorized');
    expect(renewalCalls(calls)).toHaveLength(1);
    expect(calls).toHaveLength(3);
  });
});
