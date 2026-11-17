// One class, one file: the platform writes naive UTC and the API serialises it
// without an offset, so anything that turns such a string into an instant —
// rendered date/time, the stale-run gate, the chart axis — must not let
// JavaScript read it as local wall clock.
//
// Run with: node --test tests/apiTimestamp.test.mjs
// Needs Node >= 22.18 for the TypeScript type stripping the other files use.

process.env.TZ = 'Pacific/Kiritimati'; // UTC+14, no DST: the largest offset a client can have

import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';

// runStatus.ts and intradayBars.ts reach format.ts through an extensionless
// relative import, which only the bundler resolves; the shared loader maps it.
register('./tsResolveLoader.mjs', import.meta.url);

const { formatDate, parseApiTimestamp } = await import('../src/lib/format.ts');
const { describeRunStatus } = await import('../src/lib/runStatus.ts');
const { chartTime } = await import('../src/lib/intradayBars.ts');

const UTC_PLUS_14_MS = 14 * 60 * 60 * 1000;

test('reader: an offset-less API timestamp is the UTC instant, not local', () => {
  const parsed = parseApiTimestamp('2026-11-16T03:21:44.123456');
  assert.equal(parsed.getTime(), Date.UTC(2026, 10, 16, 3, 21, 44, 123));
});

test('reader: a written offset or Z is honoured instead of assumed', () => {
  assert.equal(
    parseApiTimestamp('2026-11-16T03:21:44Z').getTime(),
    Date.UTC(2026, 10, 16, 3, 21, 44),
  );
  // 03:21+08:00 is 19:21 UTC the previous day.
  assert.equal(
    parseApiTimestamp('2026-11-16T03:21:44+08:00').getTime(),
    Date.UTC(2026, 10, 15, 19, 21, 44),
  );
});

test('reader: a bare calendar date stays at UTC midnight and is not shifted', () => {
  assert.equal(parseApiTimestamp('2026-11-16').getTime(), Date.UTC(2026, 10, 16));
});

test('reader: a Date object is taken as its own instant', () => {
  const date = new Date(Date.UTC(2026, 10, 16, 3, 21, 44));
  assert.equal(parseApiTimestamp(date).getTime(), date.getTime());
});

test('reader: absent, empty and unparseable values are null, never Invalid Date', () => {
  assert.equal(parseApiTimestamp(null), null);
  assert.equal(parseApiTimestamp(undefined), null);
  assert.equal(parseApiTimestamp(''), null);
  assert.equal(parseApiTimestamp('not a date'), null);
  assert.equal(parseApiTimestamp(new Date('not a date')), null);
});

test('date: a late-UTC timestamp renders the day it belongs to in local terms', () => {
  // 2026-11-15T23:21Z is 2026-11-16 13:21 at UTC+14; reading it as local would
  // have rendered 11/15 and mis-filed the trade under the previous day.
  assert.equal(formatDate('2026-11-15T23:21:44'), '2026/11/16');
  assert.equal(formatDate('2026-11-16T03:21:44'), '2026/11/16');
  // A bare date is a calendar day and must not move with the offset.
  assert.equal(formatDate('2026-11-16'), '2026/11/16');
});

test('stale gate: a run started five minutes ago is not called interrupted', () => {
  const now = Date.UTC(2026, 10, 16, 10, 0, 0);
  // Read as local, this string is 14h05m before `now` and the 30-minute gate
  // fires on a run that just started.
  const view = describeRunStatus('running', '2026-11-16T09:55:00', now);
  assert.equal(view.stale, false, JSON.stringify(view));
});

test('stale gate: a run started over thirty minutes ago is still flagged', () => {
  const now = Date.UTC(2026, 10, 16, 10, 0, 0);
  const view = describeRunStatus('running', '2026-11-16T09:00:00', now);
  assert.equal(view.stale, true);
  assert.equal(view.label, 'Running (possibly interrupted)');
});

test('stale gate: an unparseable start time cannot be turned into proof of staleness', () => {
  const now = Date.UTC(2026, 10, 16, 10, 0, 0);
  assert.equal(describeRunStatus('running', null, now).stale, false);
  assert.equal(describeRunStatus('running', 'not a date', now).stale, false);
});

test('chart axis: a naive bar open_time lands on its UTC second', () => {
  assert.equal(
    chartTime('2026-11-16T03:21:44'),
    Math.floor(Date.UTC(2026, 10, 16, 3, 21, 44) / 1000),
  );
  // The local reading is exactly the UTC offset away; assert it is not that.
  assert.notEqual(
    chartTime('2026-11-16T03:21:44'),
    Math.floor((Date.UTC(2026, 10, 16, 3, 21, 44) - UTC_PLUS_14_MS) / 1000),
  );
});

test('chart axis: absent and unparseable bar times are still rejected', () => {
  assert.equal(chartTime(null), null);
  assert.equal(chartTime(''), null);
  assert.equal(chartTime('not a date'), null);
});
