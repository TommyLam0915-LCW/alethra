import { test, expect } from '@playwright/test';
import { apiFetch } from '../src/lib/api';

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
});
