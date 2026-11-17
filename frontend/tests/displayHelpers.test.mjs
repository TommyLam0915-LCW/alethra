// Unit coverage for display/translation helpers added in the UX round.
//
// Run with: node --test tests/displayHelpers.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatParamMap,
  formatPercentFraction,
  formatPercentPoints,
  formatRatePercent,
} from '../src/lib/format.ts';
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

test('params: a stored run parameter map renders as comparable key=value pairs', () => {
  assert.equal(
    formatParamMap({ short_window: 5, long_window: 10 }),
    'short_window=5, long_window=10',
  );
  assert.equal(formatParamMap({ stop_pct: 0.05 }), 'stop_pct=0.05');
});

test('params: an undeclared block is distinguished from no record at all', () => {
  assert.equal(formatParamMap({}), '— (none declared)');
  assert.equal(formatParamMap(null), '—');
  assert.equal(formatParamMap(undefined), '—');
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

// Timestamp rendering. The backend writes naive UTC (app/lib/timeutil.py
// utcnow() drops tzinfo), so an offset-less string from an API is a UTC
// instant, not local wall clock. TZ is pinned to UTC+14 so a naive string
// parsed as local and one parsed as UTC cannot produce the same answer.
process.env.TZ = 'Pacific/Kiritimati';

test('datetime: a naive UTC timestamp from the API is read as UTC', () => {
  assert.equal(formatDateTime('2026-11-16T03:21:44.123456'), '2026/11/16 17:21');
  assert.equal(formatDateTime('2026-11-15T23:21:44'), '2026/11/16 13:21');
});

test('datetime: an explicit offset is honoured instead of assumed', () => {
  assert.equal(formatDateTime('2026-11-16T03:21:44Z'), '2026/11/16 17:21');
  assert.equal(formatDateTime('2026-11-16T03:21:44+08:00'), '2026/11/16 09:21');
});

test('datetime: a Date object is rendered from its own instant', () => {
  assert.equal(formatDateTime(new Date('2026-11-16T03:21:44Z')), '2026/11/16 17:21');
});

test('datetime: empty and unparseable values still render the dash', () => {
  assert.equal(formatDateTime(null), '—');
  assert.equal(formatDateTime(''), '—');
  assert.equal(formatDateTime('not a date'), '—');
});

// Numeric absence. The API sends `null` for a measure no writer recorded
// (`app/lib/measurement.py`), and JavaScript cannot tell that apart from a
// measurement of zero unless the formatter branches: `Number(null)` is 0,
// `null * 100` is 0, `null / 100` is 0, and `null >= 0` is true.
test('money: a measured amount keeps its two decimals, negative included', () => {
  assert.equal(formatMoney(781.75), '$781.75');
  assert.equal(formatMoney('781.75'), '$781.75');
  assert.equal(formatMoney(0), '$0.00');
  assert.equal(formatMoney(-42.1), '-$42.10');
});

test('money: an unmeasured amount is absent, not a break-even $0.00', () => {
  assert.equal(formatMoney(null), '—');
  assert.equal(formatMoney(undefined), '—');
  assert.equal(formatMoney(''), '—');
});

test('number: decimals, prefix and unit scale are applied in one place', () => {
  assert.equal(formatNumber(2.5), '2.50');
  assert.equal(formatNumber(2.5, 3), '2.500');
  assert.equal(formatNumber(12, 0, '$'), '$12');
  // The fraction the API sends for a proportion becomes points here, once.
  assert.equal(formatNumber(0.625, 1, '', 100), '62.5');
});

test('number: a non-numeric string is echoed rather than turned into zero', () => {
  assert.equal(formatNumber('not-a-number'), 'not-a-number');
  assert.equal(formatNumber(null), '—');
});

test('percent points: the scale the API already applied is not applied again', () => {
  assert.equal(formatPercentPoints(62.5), '62.5%');
  assert.equal(formatPercentPoints('60.00'), '60.0%');
  assert.equal(formatPercentPoints(62.5, 2), '62.50%');
  // A skill that won none of its trades *has* a win rate: 0.0% is the measurement.
  assert.equal(formatPercentPoints(0), '0.0%');
});

test('percent points: an unmeasured rate is absent, not a 0.0% that lost everything', () => {
  assert.equal(formatPercentPoints(null), '—');
  assert.equal(formatPercentPoints(undefined), '—');
  assert.equal(formatPercentPoints(''), '—');
});

test('percent fraction: the scale the API left as a proportion is applied once', () => {
  // `/journal/compare` and `/journal/unified` send win rate as 0-1 (`ratio()` in
  // app/lib/measurement.py); `/journal/summary` sends the same quantity in points.
  // Both render as "50.0%" -- each helper multiplies only the scale its endpoint
  // actually omitted.
  assert.equal(formatPercentFraction(0.625), '62.5%');
  assert.equal(formatPercentFraction('0.6250'), '62.5%');
  assert.equal(formatPercentFraction(0.5), '50.0%');
  assert.equal(formatPercentPoints(50), '50.0%');
  assert.equal(formatPercentFraction(0.625, 2), '62.50%');
  // A rate the endpoint measured as zero is still a rate.
  assert.equal(formatPercentFraction(0), '0.0%');
});

test('percent fraction: an empty denominator is absent, not a 0.0% win rate', () => {
  assert.equal(formatPercentFraction(null), '—');
  assert.equal(formatPercentFraction(undefined), '—');
  assert.equal(formatPercentFraction(''), '—');
});

// --- wiring: the summary cards must apply the above to their indicators ------
//
// The node:test lane cannot render JSX, so this checks the source structure of
// the component that owns those two indicators: the number formatting already
// has '--' for absence, but the check mark, the plus/minus sign and the aria
// verdicts are separate expressions and each one re-derives the sign from the
// same nullable field. The predicates are what `Number(null) === 0` corrupts.

const cardsPath = new URL('../src/components/journal/JournalSummaryCards.tsx', import.meta.url);
const cards = readFileSync(fileURLToPath(cardsPath), 'utf8');

test('summary cards: the ratio locals keep absence instead of folding it into zero', () => {
  // `== null` and not `=== null`: the response body is cast rather than validated, so
  // a key can arrive missing, and Number(undefined) is NaN -- NaN >= 50 and NaN >= 0
  // are both false, which draws the warning glyph and the minus sign for a number
  // that was never read.
  assert.match(
    cards,
    /const avgPnlNum = summary\.avg_trade_pnl == null \? null : Number\(summary\.avg_trade_pnl\);/
  );
  assert.match(
    cards,
    /const winRateNum = summary\.win_rate == null \? null : Number\(summary\.win_rate\);/
  );
  // The coercion that started this: an unguarded Number() of a nullable field.
  assert.ok(!/const avgPnlNum = Number\(summary\.avg_trade_pnl\);/.test(cards));
  assert.ok(!/const winRateNum = Number\(summary\.win_rate\);/.test(cards));
});

test('summary cards: no indicator glyph is drawn for an unmeasured ratio', () => {
  assert.match(cards, /\{winRateNum !== null && \(\s*<span/s);
  assert.match(cards, /\{avgPnlNum !== null && \(\s*<span/s);
});

test('summary cards: the accessible name says "not measured" rather than a verdict', () => {
  assert.match(cards, /'Win rate not measured'/);
  assert.match(cards, /'Average trade P&L not measured'/);
});
