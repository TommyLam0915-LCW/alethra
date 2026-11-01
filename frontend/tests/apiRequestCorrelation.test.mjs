// Exercises the real API client and the real login classifier against stubbed
// HTTP responses: the server's correlation id must reach the user without any
// automatic resend, and a normal 401 must stay a credential rejection.
//
// Run with: node --test tests/apiRequestCorrelation.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const { ApiError, api } = await import('../src/lib/api.ts');
const { classifyLoginError } = await import('../src/lib/userErrors.ts');

async function withFetch(responder, fn) {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (url, init) => {
    calls += 1;
    return responder(String(url), init);
  };
  try {
    const result = await fn();
    return { result, calls };
  } finally {
    globalThis.fetch = original;
  }
}

function jsonResponse(status, body, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

const credentials = { email: 'research@example.com', password: 'correct-horse' };

test('login 500: the body id is shown and the request is not repeated', async () => {
  const { calls } = await withFetch(
    () =>
      jsonResponse(
        500,
        {
          detail: 'The server hit an unexpected error.',
          code: 'internal_error',
          request_id: 'body-id-123',
        },
        { 'X-Request-ID': 'header-id-456' },
      ),
    async () => {
      await assert.rejects(
        () => api.auth.login(credentials),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 500);
          assert.equal(err.requestId, 'body-id-123');
          const shown = classifyLoginError(err);
          assert.equal(shown.kind, 'service_unavailable');
          assert.equal(shown.reference, 'body-id-123');
          assert.match(shown.message, /不是你的帳密問題/);
          return true;
        },
      );
    },
  );
  assert.equal(calls, 1);
});

test('login 500: the header id is used when the body omits request_id', async () => {
  await withFetch(
    () => jsonResponse(503, { detail: 'Database unavailable.' }, { 'X-Request-ID': 'hdr-789' }),
    async () => {
      await assert.rejects(
        () => api.auth.login(credentials),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.requestId, 'hdr-789');
          assert.equal(classifyLoginError(err).reference, 'hdr-789');
          return true;
        },
      );
    },
  );
});

test('login 500: no supplied id means no reference is displayed', async () => {
  await withFetch(
    () => jsonResponse(500, { detail: 'The server hit an unexpected error.' }),
    async () => {
      await assert.rejects(
        () => api.auth.login(credentials),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.requestId, undefined);
          const shown = classifyLoginError(err);
          assert.equal(shown.kind, 'service_unavailable');
          assert.equal(shown.reference, undefined);
          return true;
        },
      );
    },
  );
});

test('login 401: wrong credentials stay a credential rejection, not a service error', async () => {
  const { calls } = await withFetch(
    () => jsonResponse(401, { detail: 'Incorrect email or password' }),
    async () => {
      await assert.rejects(
        () => api.auth.login(credentials),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 401);
          assert.equal(err.requestId, undefined);
          const shown = classifyLoginError(err);
          assert.equal(shown.kind, 'invalid_credentials');
          assert.equal(shown.reference, undefined);
          assert.match(shown.message, /電郵或密碼不正確/);
          assert.doesNotMatch(shown.message, /Incorrect email or password/);
          return true;
        },
      );
    },
  );
  assert.equal(calls, 1);
});

test('login 401: a supplied correlation id is shown, and the login is not resent', async () => {
  const { calls, result } = await withFetch(
    () =>
      jsonResponse(
        401,
        { detail: 'Incorrect email or password' },
        { 'X-Request-ID': 'hdr-401-abc' },
      ),
    async () => {
      let shown;
      await assert.rejects(
        () => api.auth.login(credentials),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 401);
          assert.equal(err.requestId, 'hdr-401-abc');
          shown = classifyLoginError(err);
          return true;
        },
      );
      return shown;
    },
  );
  assert.equal(result.kind, 'invalid_credentials');
  assert.equal(result.reference, 'hdr-401-abc');
  assert.equal(calls, 1);
});

test('login 422: the existing validation shape still reaches the user', async () => {
  await withFetch(
    () =>
      jsonResponse(422, {
        detail: [{ loc: ['body', 'password'], msg: 'too short', type: 'value_error' }],
      }),
    async () => {
      await assert.rejects(
        () => api.auth.login(credentials),
        (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 422);
          const shown = classifyLoginError(err);
          assert.equal(shown.kind, 'validation');
          assert.match(shown.message, /body\.password: too short/);
          return true;
        },
      );
    },
  );
});
