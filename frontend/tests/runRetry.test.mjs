// Unit coverage for what the UI does when a "run backtest" request is not answered.
//
// Run with: node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/runRetry.test.mjs
//
// backend/app/api/runs.py bills a research unit per admitted request and
// recognises a retry only through the `Idempotency-Key` header, so the whole
// client-side policy is which key a click sends and how long it is remembered.
// That policy lives in src/lib/runRetry.ts and is tested here without a
// component; backend/tests/test_research_run_admission.py tests what the server
// does with the key once it arrives.
//
// The wiring section at the bottom reads source text. It proves a page goes
// through the functions tested above — nothing in it is rendered-UI evidence.

import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  MAX_IDEMPOTENCY_KEY_LENGTH,
  attemptFor,
  keepAttemptAfter,
  mintRunKey,
  newRunKey,
  wasAnswered,
} from '../src/lib/runRetry.ts';

const V1 = '11111111-1111-4111-8111-111111111111';
const V2 = '22222222-2222-4222-8222-222222222222';

function keyMint(keys) {
  let i = 0;
  return () => {
    const key = keys[Math.min(i, keys.length - 1)];
    i += 1;
    return key;
  };
}

// --- the key itself ---------------------------------------------------------

test('the key the app mints is one the server will accept', () => {
  const first = mintRunKey();
  const second = mintRunKey();
  assert.ok(first.length > 0 && first.length <= MAX_IDEMPOTENCY_KEY_LENGTH, first);
  assert.equal(first, first.trim(), 'a key with padding would be silently stripped');
  assert.doesNotMatch(first, /\s/);
  assert.notEqual(first, second, 'two attempts must not share a key by accident');
});

test('a key the column cannot hold is never sent', () => {
  // The server refuses these with 400 before anything is admitted, and it is
  // right to: a key the database cannot store deduplicates nothing, so sending it
  // would promise the user a retry that is billed anyway.
  assert.equal(newRunKey(() => '   '), null);
  assert.equal(newRunKey(() => ''), null);
  assert.equal(newRunKey(() => 'x'.repeat(MAX_IDEMPOTENCY_KEY_LENGTH + 1)), null);
  assert.equal(newRunKey(() => 42), null);
  assert.equal(newRunKey(() => undefined), null);
  assert.equal(
    newRunKey(() => 'x'.repeat(MAX_IDEMPOTENCY_KEY_LENGTH)),
    'x'.repeat(MAX_IDEMPOTENCY_KEY_LENGTH),
  );
});

test('a runtime with no crypto runs without a key rather than crashing', () => {
  // `crypto.randomUUID` is missing outside a secure origin. The click still
  // works, it just costs a unit if it is retried — which is what every click did
  // before this policy existed, and nothing here pretends otherwise.
  assert.equal(
    newRunKey(() => {
      throw new TypeError('crypto.randomUUID is not a function');
    }),
    null
  );
  const { attempt, reused } = attemptFor(null, V1, () => {
    throw new TypeError('no crypto');
  });
  assert.equal(attempt, null);
  assert.equal(reused, false);
});

// --- which click reuses which key ------------------------------------------

test('a click with nothing stored mints a key for its own version', () => {
  let mints = 0;
  const mint = () => {
    mints += 1;
    return 'attempt-1';
  };
  const { attempt, reused } = attemptFor(null, V1, mint);
  assert.deepEqual(attempt, { versionId: V1, key: 'attempt-1' });
  assert.equal(reused, false);
  assert.equal(mints, 1);
});

test('a click that repeats an unanswered attempt reuses its key and mints nothing', () => {
  const stored = { versionId: V1, key: 'attempt-1' };
  let mints = 0;
  const { attempt, reused } = attemptFor(stored, V1, () => {
    mints += 1;
    return 'attempt-2';
  });
  assert.equal(attempt, stored, 'the same key must go out again');
  assert.equal(reused, true);
  assert.equal(mints, 0);
});

test('a stored key never crosses to another version', () => {
  // The backend scopes the key to the version row, so sending v1's key with a v2
  // request would only ever produce a conflict on a request the user meant.
  const stored = { versionId: V1, key: 'attempt-1' };
  const { attempt, reused } = attemptFor(stored, V2, keyMint(['attempt-2']));
  assert.equal(reused, false);
  assert.deepEqual(attempt, { versionId: V2, key: 'attempt-2' });
});

// --- how long an attempt is remembered -------------------------------------

test('an attempt is forgotten the moment the server answers', () => {
  const attempt = { versionId: V1, key: 'attempt-1' };
  // 201, 200, 409, 429, 500 — all answers. Holding the key past one would make
  // every later click on that version replay a closed run instead of starting
  // the new backtest the user is asking for.
  assert.equal(keepAttemptAfter(attempt, true), null);
});

test('an attempt survives a request that was never answered', () => {
  const attempt = { versionId: V1, key: 'attempt-1' };
  assert.equal(keepAttemptAfter(attempt, false), attempt);
  assert.equal(keepAttemptAfter(null, false), null);
  assert.equal(keepAttemptAfter(null, true), null);
});

test('an HTTP status of any kind is what makes a rejection an answer', () => {
  // `ApiError` is thrown for a response that was read and refused, whatever the
  // status; `fetch` itself rejects with a plain TypeError when no response
  // arrived. The status field is the whole distinction, which is why this file
  // does not import the error class.
  assert.equal(wasAnswered({ status: 409 }), true);
  assert.equal(wasAnswered({ status: 503 }), true);
  assert.equal(wasAnswered({ status: 200 }), true);
  assert.equal(wasAnswered(new TypeError('Failed to fetch')), false);
  assert.equal(wasAnswered(new Error('Unauthorized')), false);
  assert.equal(wasAnswered({ status: '500' }), false);
  assert.equal(wasAnswered(undefined), false);
  assert.equal(wasAnswered(null), false);
  assert.equal(wasAnswered('500'), false);
});

test('a lost response is one attempt, and an answer ends it', () => {
  // The three rules joined in the order a user meets them: click, no response,
  // click again, then a response of any kind.
  const stored = { versionId: V1, key: 'attempt-1' };
  const retry = attemptFor(keepAttemptAfter(stored, false), V1, keyMint(['attempt-2']));
  assert.equal(retry.reused, true);
  assert.equal(retry.attempt.key, 'attempt-1');

  const afterAnswer = attemptFor(keepAttemptAfter(retry.attempt, true), V1, keyMint(['attempt-2']));
  assert.equal(afterAnswer.reused, false, 'a fresh deliberate click is a fresh attempt');
  assert.equal(afterAnswer.attempt.key, 'attempt-2');
});

// --- the other end of the contract -----------------------------------------

const FRONTEND = join(fileURLToPath(new URL('..', import.meta.url)));
const REPO = join(FRONTEND, '..');

function source(path) {
  assert.ok(existsSync(path), `${path} is not in the checkout to read`);
  return readFileSync(path, 'utf8');
}

const model = source(join(REPO, 'backend/app/models/research_run.py'));
const runsApi = source(join(REPO, 'backend/app/api/runs.py'));
const apiLib = source(join(FRONTEND, 'src/lib/api.ts'));
const runRetryLib = source(join(FRONTEND, 'src/lib/runRetry.ts'));
const panel = source(join(FRONTEND, 'src/components/experiments/RevisionPanel.tsx'));
const page = source(join(FRONTEND, 'src/app/experiments/[id]/page.tsx'));

test('the length this client will send is the length the column holds', () => {
  const column = /idempotency_key = Column\(String\((\d+)\), nullable=True\)/.exec(model);
  assert.ok(column, 'the model no longer declares idempotency_key in the shape read here');
  assert.equal(
    Number(column[1]),
    MAX_IDEMPOTENCY_KEY_LENGTH,
    'runRetry.ts and research_runs.idempotency_key disagree; the server would 400 the header',
  );
});

test('the server reads its limit off that column rather than a second copy', () => {
  assert.match(
    runsApi,
    /IDEMPOTENCY_KEY_MAX_LENGTH = ResearchRun\.__table__\.c\.idempotency_key\.type\.length/,
    'a retyped limit would let the two ends drift while this lane still passes',
  );
});

test('the header name sent here is the one the endpoint reads', () => {
  assert.match(runsApi, /request\.headers\.get\("Idempotency-Key"\)/);
  assert.match(
    apiLib,
    /headers: idempotencyKey \? \{ 'Idempotency-Key': idempotencyKey \} : undefined/,
  );
  assert.match(
    apiLib,
    /create: \(versionId: string, idempotencyKey\?: string \| null\) =>/,
    'the client must be able to send no header at all, which is a different request',
  );
});

// --- wiring: the pages must go through the functions tested above -----------

function assertsRunAttemptWiring(sourceText, handle, target) {
  assert.match(
    sourceText,
    /import \{[^}]*attemptFor[^}]*keepAttemptAfter[^}]*wasAnswered[^}]*\} from '@\/lib\/runRetry';/s,
    `${target} does not import the retry policy`,
  );
  assert.match(sourceText, /const unansweredRun = useRef<RunAttempt \| null>\(null\)/);
  assert.match(
    sourceText,
    new RegExp(`attemptFor\\(unansweredRun\\.current, ${handle}, mintRunKey\\)`),
  );
  // From the moment the request leaves, so an in-flight repeat click cannot
  // start a second attempt under a different key.
  assert.match(sourceText, /unansweredRun\.current = attempt;/);
  assert.match(sourceText, /api\.runs\.create\([a-zA-Z.]+, attempt\?\.key \?\? null\)/);
  // Success and every refused status clear it; only a rejection without a
  // status keeps it.
  assert.match(sourceText, /let answered = true;/);
  assert.match(sourceText, /answered = wasAnswered\(err\);/);
  assert.match(sourceText, /unansweredRun\.current = keepAttemptAfter\(attempt, answered\);/);
}

test('the revision panel keys each run it starts and forgets the key when answered', () => {
  assertsRunAttemptWiring(panel, 'version\\.id', 'RevisionPanel.tsx');
});

test('the experiment page keys its run button the same way', () => {
  assertsRunAttemptWiring(page, 'versionId', 'experiments/[id]/page.tsx');
});

test('the run path has one start call per page and it carries the attempt', () => {
  for (const [name, text] of [
    ['RevisionPanel.tsx', panel],
    ['experiments/[id]/page.tsx', page],
  ]) {
    assert.equal(
      (text.match(/api\.runs\.create\(/g) ?? []).length,
      1,
      `${name} has more than one start-run call site, and only one of them would be keyed`,
    );
  }
});

test('a revision request id and a run attempt key stay separate mechanisms', () => {
  // The revision form has always sent `request_id` in its JSON body, because
  // `/revisions` reads the id there; `/versions/{id}/runs` has no body and reads
  // only the header. One number serving both would put two lifetimes on it — the
  // form's id is reset with the form, an attempt has to outlive a dropped fetch.
  assert.match(panel, /request_id: requestId \|\| undefined,/);
  assert.match(panel, /function newRequestId\(\): string/);
  assert.doesNotMatch(runRetryLib, /request_id/);
});
