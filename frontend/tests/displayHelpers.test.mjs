// Unit coverage for display/translation helpers added in the UX round.
//
// Run with: node --test tests/displayHelpers.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';

import { formatRatePercent } from '../src/lib/format.ts';
import { explainMlFailure, describeStoredJobFailure } from '../src/lib/mlFailureMessages.ts';

test('rate: a stored fraction is shown as a percentage of trade value', () => {
  assert.equal(formatRatePercent(0.001), '0.10%');
  assert.equal(formatRatePercent(0.0025), '0.25%');
  assert.equal(formatRatePercent(0), '0.00%');
});

test('rate: sub-0.01% keeps three decimals so 0.001 vs 0.002 does not collapse', () => {
  assert.equal(formatRatePercent(0.0001), '0.010%');
  assert.equal(formatRatePercent(0.00001), '0.001%');
});

test('rate: numeric strings from JSON are accepted', () => {
  assert.equal(formatRatePercent('0.001'), '0.10%');
});

test('rate: missing or non-numeric input is shown as absent, never as 0%', () => {
  assert.equal(formatRatePercent(null), '—');
  assert.equal(formatRatePercent(undefined), '—');
  assert.equal(formatRatePercent(''), '—');
  assert.equal(formatRatePercent('not-a-number'), 'not-a-number');
});

test('ml failure: the backend 60-bar message names the actual bar count and next step', () => {
  const hint = explainMlFailure(
    'Insufficient data for AAPL: got 12 bars, need at least 60 for ML training.',
  );
  assert.match(hint ?? '', /12 根日線/);
  assert.match(hint ?? '', /至少需要 60 根/);
  assert.match(hint ?? '', /市場資料/);
});

test('ml failure: the 30-feature-row message keeps the backend threshold', () => {
  const hint = explainMlFailure('Need at least 30 valid feature rows, got 18');
  assert.match(hint ?? '', /18 筆/);
  assert.match(hint ?? '', /至少需要 30 筆/);
});

test('ml failure: the temporal-split message reports the real split and the 10/5/5 minimum', () => {
  const hint = explainMlFailure('Temporal split too small: train=8, val=3, test=4');
  assert.match(hint ?? '', /8 \/ 3 \/ 4/);
  assert.match(hint ?? '', /10 \/ 5 \/ 5/);
});

test('ml failure: an unknown date range tells the user to load data first', () => {
  const hint = explainMlFailure(
    'No known date range for AAPL: nothing stored and provider reports no range.',
  );
  assert.match(hint ?? '', /日期範圍/);
  assert.match(hint ?? '', /載入/);
});

test('ml failure: prediction needs 30 bars and reports the current count', () => {
  const hint = explainMlFailure('Need at least 30 bars for prediction, got 9');
  assert.match(hint ?? '', /9 根/);
  assert.match(hint ?? '', /30 根日線/);
});

test('ml failure: unrelated backend errors are left to the caller unchanged', () => {
  assert.equal(explainMlFailure('experiment not found'), null);
  assert.equal(explainMlFailure(''), null);
});

test('stored job failure: a verified data-insufficiency message keeps its explanation', () => {
  const stored = describeStoredJobFailure(
    'Insufficient data for AAPL: got 12 bars, need at least 60 for ML training.',
  );
  assert.ok(stored);
  assert.match(stored.detail, /12 bars/);
  assert.match(stored.explanation ?? '', /至少需要 60 根/);
});

test('stored job failure: backend-authored sweep and cancellation messages are shown verbatim', () => {
  const sweep = describeStoredJobFailure(
    'No valid backtest results: all parameter combinations failed or produced errors',
  );
  assert.deepEqual(sweep, {
    detail: 'No valid backtest results: all parameter combinations failed or produced errors',
    explanation: null,
  });
  const cancelled = describeStoredJobFailure('Job cancelled by user');
  assert.deepEqual(cancelled, { detail: 'Job cancelled by user', explanation: null });
});

test('stored job failure: arbitrary exception text is withheld from the customer', () => {
  assert.equal(
    describeStoredJobFailure('Traceback: relation "training_jobs" does not exist'),
    null,
  );
  assert.equal(describeStoredJobFailure('simulated training crash'), null);
  assert.equal(describeStoredJobFailure(''), null);
  assert.equal(describeStoredJobFailure(null), null);
});
