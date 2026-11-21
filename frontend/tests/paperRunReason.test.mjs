// P-4: `paper_trading_runs.failure_reason` is nullable and written by one branch
// of one statement, so an empty field is not one fact but two, and only the
// status can tell them apart. A 'cancelled' run was asked to stop and the
// terminal UPDATE sets the reason to NULL on purpose
// (backend/app/services/paper_trading.py:1086); a 'failed' run with nothing in
// the field died before revision f2a3b4c5d6e7 added the column, and no backfill
// writes into those rows. Rendering the second as "no reason recorded" is true;
// rendering the first as anything at all would invent a reason for a run that
// was not a failure.
//
// The reason text itself reuses P-3b's provenance gate rather than a second
// one: `renderStoredFailure` keeps backend-authored literals unchanged and
// marks everything else, which is the contract
// `tests/storedFailureProvenance.test.mjs` already ties to the writers.
//
// Run with: node22 --test tests/paperRunReason.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';

// `paperRunReason.ts` imports its sibling extensionlessly, which is how the
// sources under src/lib/ are written and what the resolver below maps.
register('./tsResolveLoader.mjs', import.meta.url);

const { NO_REASON_RECORDED, failureReasonText } = await import('../src/lib/paperRunReason.ts');
const { UNVERIFIED_REASON_SUFFIX } = await import('../src/lib/mlFailureMessages.ts');

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const backend = (rel) => src(`../../backend/${rel}`);

// --- status decides what an empty field means ---------------------------------

test('a cancelled run shows nothing: its reason was set to NULL on purpose', () => {
  assert.equal(failureReasonText('cancelled', null), '');
  // Even a stale value is not shown for a run the user stopped.
  assert.equal(failureReasonText('cancelled', 'leftover text'), '');
});

test('completed and running and queued runs show nothing', () => {
  for (const status of ['completed', 'running', 'queued', 'stopped', 'pausing']) {
    assert.equal(failureReasonText(status, null), '', `${status} invented a reason`);
    assert.equal(failureReasonText(status, 'some text'), '', `${status} echoed stored text`);
  }
});

test('a failed run with no recorded reason says the column was added later', () => {
  for (const raw of [null, undefined, '', '   ']) {
    assert.equal(
      failureReasonText('failed', raw),
      NO_REASON_RECORDED,
      `empty value ${JSON.stringify(raw)} rendered as a real reason`,
    );
  }
  // The sentence must not claim the platform withheld something.
  assert.ok(!NO_REASON_RECORDED.includes('未提供'), NO_REASON_RECORDED);
});

// --- a recorded reason goes through the provenance gate -----------------------

test('a failed run shows its scrubbed reason with provenance marked', () => {
  const stored = "connection to server at <path> failed: couldn't connect to host";
  const rendered = failureReasonText('failed', stored);
  assert.ok(rendered.includes(stored), 'the recorded reason disappeared from the page');
  assert.ok(
    rendered.includes(UNVERIFIED_REASON_SUFFIX),
    'a sentence the platform never wrote is presented as if it had',
  );
});

test('a blank-after-scrubbing reason falls back instead of rendering nothing', () => {
  // `safe_error_message()` can legitimately return '' for an exception with no
  // message, and `renderStoredFailure('')` is ''. A silent 'failed' cell is the
  // failure this unit exists to prevent.
  assert.equal(failureReasonText('failed', ''), NO_REASON_RECORDED);
});

// --- wiring: both surfaces read the field through the renderer ----------------

const COMPONENT = '../src/components/journal/PaperTradingDashboard.tsx';

test('both run surfaces call the renderer', () => {
  const body = src(COMPONENT);
  assert.match(body, /from '@\/lib\/paperRunReason'/, 'the component does not import the gate');
  const calls = body.match(/failureReasonText\(run\.status, run\.failure_reason\)/g) || [];
  assert.equal(
    calls.length,
    2,
    `expected one call in the run card and one in the history table, got ${calls.length}`,
  );
});

test('no surface interpolates run.failure_reason as display text', () => {
  const body = src(COMPONENT);
  const raw = /\{\s*run\.failure_reason\s*\}/.exec(body);
  assert.equal(raw && raw[0], null, `${COMPONENT} renders ${raw && raw[0]} directly`);
});

test('the client type declares the field as nullable, not absent', () => {
  const body = src(COMPONENT);
  assert.match(
    body,
    /failure_reason: string \| null;/,
    'an optional or non-null type would let a missing key read as a real value',
  );
});

// --- the producer and the reader declare the same contract --------------------

test('the service writes the reason only on the failed branch of one statement', () => {
  const service = backend('app/services/paper_trading.py');
  assert.match(
    service,
    /failure_reason = CASE\s*\n\s*WHEN cancel_requested = TRUE THEN NULL\s*\n\s*ELSE :reason/,
    'the terminal UPDATE no longer NULLs the reason for a cancelled run',
  );
  assert.match(
    service,
    /"reason": safe_error_message\(exc\)/,
    'the reason no longer comes from the scrubbing helper',
  );
});

test('the acquisition statement clears a previous attempt\'s reason', () => {
  const service = backend('app/services/paper_trading.py');
  assert.match(
    service,
    /SET status = 'running', failure_reason = NULL/,
    'a re-run would still report why the earlier attempt died',
  );
});

test('both read endpoints carry the field the component reads', () => {
  const api = backend('app/api/paper_trading.py');
  assert.match(api, /failure_reason: Optional\[str\] = None/);
  assert.match(api, /"failure_reason": r\.failure_reason/);
  const service = backend('app/services/paper_trading.py');
  assert.match(service, /"failure_reason": run\.failure_reason/);
});

test('the column is nullable Text in both the model and the migration', () => {
  const model = backend('app/models/paper_trading_run.py');
  assert.match(model, /failure_reason = Column\(Text, nullable=True\)/);
  const migration = backend(
    'migrations/versions/f2a3b4c5d6e7_add_paper_trading_run_failure_reason.py',
  );
  // Two different claims would be made by the two DDLs: NOT NULL would break
  // every pre-migration row, and String(64) would truncate the 200-character
  // bound safe_error_message() writes up to.
  assert.match(
    migration,
    /sa\.Column\('failure_reason', sa\.Text\(\), nullable=True\)/,
    'the migration does not add a nullable Text column',
  );
});
