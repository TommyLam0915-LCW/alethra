// `/inference` tells a person "失敗原因見下方紀錄" when a run comes back failed, so the
// reason the server already recorded has to be readable in the run card.
//
// The producer side is not the gap: `backend/app/api/inference.py` returns `error_details`
// on both `GET /runs/{run_id}` and `GET /runs`, and `backend/app/services/inference.py`
// writes it on every failure path. The consumer dropped the field.
//
// Run with: node22 --test tests/inferenceFailureSurface.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describeStoredJobFailure } from '../src/lib/mlFailureMessages.ts';

const pagePath = new URL('../src/app/inference/page.tsx', import.meta.url);
const readPage = () => readFileSync(fileURLToPath(pagePath), 'utf8');

function slice(source, from, to, what) {
  const start = source.indexOf(from);
  const end = source.indexOf(to);
  assert.ok(start !== -1 && end > start, `could not isolate ${what} in the page`);
  return source.slice(start, end);
}

// --- the gate the page shares with /training -------------------------------------

test('the inference refusal the backend writes verbatim is shown', () => {
  // Produced at backend/app/services/inference.py:204 for a future external reference.
  const stored = describeStoredJobFailure('External model references are not yet executable');
  assert.ok(stored, 'the page would hide a reason the backend authored itself');
  assert.equal(stored.detail, 'External model references are not yet executable');
});

test('an exception message from a failed inference run is still not echoed', () => {
  // `run.error_details = safe_error_message(e)` stores whatever an exception carried.
  assert.equal(
    describeStoredJobFailure('safe_error_message output: no such column'),
    null,
    'arbitrary stored text is repeated to the customer',
  );
});

// --- wiring: the page reads the field and renders it ------------------------------

test('the client type carries the field the endpoint sends', () => {
  const iface = slice(readPage(), 'interface InferenceRun {', 'interface ModelRecord {', 'the run interface');
  assert.match(iface, /error_details:\s*string \| null;/);
});

test('a failed run shows its reason in the card itself', () => {
  const page = readPage();
  // The slice between the status pill and the expansion gate is what a collapsed card shows.
  const collapsed = slice(page, 'statusPillClass(run.status)', '{expandedRunId === run.id && (', 'the always-visible card tail');
  assert.match(collapsed, /run\.status === 'failed'/, 'a failed run renders nothing until expanded');
  assert.match(collapsed, /run\.error_details/, 'the reason is never read');
  assert.match(collapsed, /data-testid="inference-run-failure"/);
});

test('the reason goes through the shared gate, not a raw echo', () => {
  const page = readPage();
  assert.match(
    page,
    /import \{ describeStoredJobFailure \} from '@\/lib\/mlFailureMessages';/,
    'the page does not import the shared gate',
  );
  const collapsed = slice(page, 'statusPillClass(run.status)', '{expandedRunId === run.id && (', 'the always-visible card tail');
  assert.match(collapsed, /describeStoredJobFailure\(run\.error_details\)/);
  assert.match(
    collapsed,
    /不顯示原始錯誤內容|未記錄失敗原因/,
    'an unrecognised stored reason is displayed as raw text',
  );
});

test('the card says when a failed run has no recorded reason', () => {
  const page = readPage();
  const collapsed = slice(page, 'statusPillClass(run.status)', '{expandedRunId === run.id && (', 'the always-visible card tail');
  assert.match(collapsed, /尚未記錄/, 'a failed run with a null reason renders as if it succeeded');
});
