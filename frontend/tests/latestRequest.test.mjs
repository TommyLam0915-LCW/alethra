// Guards the "old request overwrites new state" class (D-2 / §二-B).
//
// The loader shape below is copied from the production handlers it protects,
// e.g. src/app/training/page.tsx:237-248 (a 4-second poll) and
// src/app/dashboard/page.tsx:90-104 (a Retry button): begin a token, await the
// request, write state, and clear the loading flag in `finally`. The tests
// resolve two overlapping loads in the opposite order to the one they were
// issued, which is what an HTTP response is allowed to do.
//
// Run with: node --test tests/latestRequest.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const { createRequestGuard } = await import('../src/lib/latestRequest.ts');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// The production handler shape. `guard: null` is the pre-fix code, kept so the
// same test file shows both the defect and the repair.
function makeLoader(guard, request, state) {
  return async function load() {
    const token = guard ? guard.begin() : 0;
    state.loading = true;
    try {
      const data = await request();
      if (!guard || guard.isCurrent(token)) {
        state.data = data;
        state.writes.push(token);
      }
    } catch (err) {
      if (!guard || guard.isCurrent(token)) {
        state.error = err.message;
        state.writes.push(token);
      }
    } finally {
      if (!guard || guard.isCurrent(token)) state.loading = false;
    }
  };
}

test('guard: every begin() makes only the newest token current', () => {
  const guard = createRequestGuard();
  const first = guard.begin();
  const second = guard.begin();
  assert.notEqual(first, second);
  assert.equal(guard.isCurrent(second), true);
  assert.equal(guard.isCurrent(first), false);
});

test('guard: a fresh guard has no request to discard', () => {
  const guard = createRequestGuard();
  assert.equal(guard.isCurrent(guard.begin()), true);
});

test('out-of-order responses: the newest answer keeps the state', async () => {
  const queue = [deferred(), deferred()];
  let call = 0;
  const guard = createRequestGuard();
  const state = { data: null, error: null, loading: false, writes: [] };
  const load = makeLoader(guard, () => queue[call++].promise, state);

  const firstRun = load();
  const secondRun = load();
  queue[1].resolve('newest');
  await secondRun;
  queue[0].resolve('oldest');
  await firstRun;

  assert.equal(state.data, 'newest');
  assert.deepEqual(state.writes, [2], 'the discarded response must not write at all');
  assert.equal(state.loading, false);
});

test('without the guard, the oldest response is the one that lands', async () => {
  const queue = [deferred(), deferred()];
  let call = 0;
  const state = { data: null, error: null, loading: false, writes: [] };
  const load = makeLoader(null, () => queue[call++].promise, state);

  const firstRun = load();
  const secondRun = load();
  queue[1].resolve('newest');
  await secondRun;
  queue[0].resolve('oldest');
  await firstRun;

  assert.equal(state.data, 'oldest');
});

test('an obsolete rejection cannot write the error state', async () => {
  const queue = [deferred(), deferred()];
  let call = 0;
  const guard = createRequestGuard();
  const state = { data: null, error: null, loading: false, writes: [] };
  const load = makeLoader(guard, () => queue[call++].promise, state);

  const firstRun = load();
  const secondRun = load();
  queue[1].resolve('newest');
  await secondRun;
  queue[0].reject(new Error('timeout'));
  await firstRun;

  assert.equal(state.data, 'newest');
  assert.equal(state.error, null);
});

test('an obsolete response cannot clear the loading flag of the newest one', async () => {
  const queue = [deferred(), deferred()];
  let call = 0;
  const guard = createRequestGuard();
  const state = { data: null, error: null, loading: false, writes: [] };
  const load = makeLoader(guard, () => queue[call++].promise, state);

  const firstRun = load();
  const secondRun = load();
  queue[0].resolve('oldest');
  await firstRun;

  // The older request finished, but a newer one is still in flight: the UI
  // must stay in the loading state.
  assert.equal(state.loading, true);
  assert.equal(state.data, null);

  queue[1].resolve('newest');
  await secondRun;
  assert.equal(state.loading, false);
  assert.equal(state.data, 'newest');
});

test('without the guard, the older response clears the spinner early', async () => {
  const queue = [deferred(), deferred()];
  let call = 0;
  const state = { data: null, error: null, loading: false, writes: [] };
  const load = makeLoader(null, () => queue[call++].promise, state);

  const firstRun = load();
  const secondRun = load();
  queue[0].resolve('oldest');
  await firstRun;
  assert.equal(state.loading, false);

  queue[1].resolve('newest');
  await secondRun;
});

test('three overlapping loads: only the last request issued may write', async () => {
  const queue = [deferred(), deferred(), deferred()];
  let call = 0;
  const guard = createRequestGuard();
  const state = { data: null, error: null, loading: false, writes: [] };
  const load = makeLoader(guard, () => queue[call++].promise, state);

  const runs = [load(), load(), load()];
  queue[0].resolve('first');
  queue[2].resolve('third');
  queue[1].resolve('second');
  await Promise.all(runs);

  assert.equal(state.data, 'third');
  assert.deepEqual(state.writes, [3]);
});

test('sequential loads still write: the guard never blocks the normal path', async () => {
  const answers = ['a', 'b', 'c'];
  let call = 0;
  const guard = createRequestGuard();
  const state = { data: null, error: null, loading: false, writes: [] };
  const load = makeLoader(guard, async () => answers[call++], state);

  for (const expected of answers) {
    await load();
    assert.equal(state.data, expected);
    assert.equal(state.loading, false);
  }
  assert.deepEqual(state.writes, [1, 2, 3], 'none of three sequential loads is obsolete');
});

test('two component instances do not share a guard', async () => {
  const a = createRequestGuard();
  const b = createRequestGuard();
  const tokenA = a.begin();
  b.begin();
  b.begin();
  assert.equal(a.isCurrent(tokenA), true, 'instance B activity must not invalidate instance A');
});
