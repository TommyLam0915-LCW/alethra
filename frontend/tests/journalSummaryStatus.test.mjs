// The summary banner at the top of /journal decides one sentence from three fields,
// so the sentence is a contract: it may only claim what the numbers measure.
//
// Run with: node22 --test tests/journalSummaryStatus.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { classifySummaryStatus } from '../src/lib/journalSummaryStatus.ts';

// What `/journal/trades` actually answers for an empty filter
// (journal_service.py, the `total_trades == 0` early return): an additive "0"
// for the sum and null for both ratios.
const EMPTY_FILTER = { total_trades: 0, total_pnl: '0', win_rate: null };

test('an empty filter gets no performance verdict', () => {
  const status = classifySummaryStatus(EMPTY_FILTER);
  assert.equal(status.tone, 'neutral');
  assert.match(status.message, /No trades/);
  for (const claim of ['profitable', 'negative', 'break-even']) {
    assert.ok(!status.message.toLowerCase().includes(claim), `claimed "${claim}" from nothing`);
  }
});

test('a measured net of exactly zero is break-even, not slightly negative', () => {
  const status = classifySummaryStatus({ total_trades: 4, total_pnl: '0.00', win_rate: '50.00' });
  assert.equal(status.tone, 'neutral');
  assert.equal(status.message, 'Your strategies are break-even so far');
});

test('profit is split on the win rate the same way the banner used to split it', () => {
  assert.deepEqual(classifySummaryStatus({ total_trades: 9, total_pnl: '120.50', win_rate: '55.00' }), {
    message: 'Your strategies are profitable',
    tone: 'positive',
  });
  assert.deepEqual(classifySummaryStatus({ total_trades: 9, total_pnl: '120.50', win_rate: '40.00' }), {
    message: 'Your strategies are profitable, but win rate could improve',
    tone: 'positive',
  });
});

test('the loss wording keeps the -1000 boundary on the same side', () => {
  assert.equal(
    classifySummaryStatus({ total_trades: 9, total_pnl: '-250.00', win_rate: '20.00' }).message,
    'Your strategies are slightly negative',
  );
  assert.equal(
    classifySummaryStatus({ total_trades: 9, total_pnl: '-5000.00', win_rate: '20.00' }).message,
    'Your strategies are losing money',
  );
  // -1000 is not "< -1000": the boundary case keeps the milder sentence.
  assert.equal(
    classifySummaryStatus({ total_trades: 9, total_pnl: '-1000.00', win_rate: '20.00' }).message,
    'Your strategies are slightly negative',
  );
});

test('a null win rate is not read as 0%', () => {
  // Not a response the endpoint produces (null ratios only come with 0 trades);
  // it is what `win_rate: string | null` allows, and the banner must not turn it
  // into "win rate could improve".
  assert.deepEqual(classifySummaryStatus({ total_trades: 3, total_pnl: '10.00', win_rate: null }), {
    message: 'Your strategies are profitable',
    tone: 'positive',
  });
});

test('an unreadable number fails closed instead of into a sign', () => {
  // The fetch body is cast to these types, not validated, so a missing or
  // non-numeric key has to land on "not available" rather than on a verdict:
  // Number(undefined) is NaN and every comparison against NaN is false, which in
  // the baseline if-chain meant the final else branch -- "slightly negative", red.
  const unreadable = [
    { total_trades: undefined, total_pnl: undefined, win_rate: undefined },
    { total_trades: 5, total_pnl: undefined, win_rate: '50.00' },
    { total_trades: 5, total_pnl: 'n/a', win_rate: '50.00' },
  ];
  for (const summary of unreadable) {
    const status = classifySummaryStatus(summary);
    assert.equal(status.tone, 'neutral', JSON.stringify(summary));
    assert.match(status.message, /No trades|not available/, JSON.stringify(summary));
  }
  assert.deepEqual(classifySummaryStatus({ total_trades: 5, total_pnl: '10.00', win_rate: undefined }), {
    message: 'Your strategies are profitable',
    tone: 'positive',
  });
});

// --- no drift: every measured case keeps the sentence the panel rendered -------

// Transcribed from `git show b08418f2:frontend/src/components/journal/JournalDashboard.tsx`
// lines 176-194 — the two Number() locals (:176, :177) and the four-branch if-chain
// (:182-:194). Re-derived with `git show b08418f2:… | sed -n '176,194p'`, not carried.
function baselineStatus(summary) {
  const totalPnl = Number(summary.total_pnl);
  const winRate = Number(summary.win_rate);
  if (totalPnl > 0 && winRate > 50) return { message: 'Your strategies are profitable', tone: 'positive' };
  if (totalPnl > 0) {
    return { message: 'Your strategies are profitable, but win rate could improve', tone: 'positive' };
  }
  if (totalPnl < -1000) return { message: 'Your strategies are losing money', tone: 'negative' };
  return { message: 'Your strategies are slightly negative', tone: 'negative' };
}

test('every measured summary keeps its sentence; only the unmeasured ones change', () => {
  const measured = [];
  for (const total_pnl of ['0', '0.00', '0.01', '-0.01', '5', '-5', '-1000', '-1000.01', '9999', '-9999']) {
    for (const win_rate of ['0.00', '49.99', '50.00', '50.01', '100.00']) {
      measured.push({ total_trades: 12, total_pnl, win_rate });
    }
  }
  assert.equal(measured.length, 50);

  const diverging = measured.filter((summary) => {
    const before = baselineStatus(summary);
    const after = classifySummaryStatus(summary);
    return before.message !== after.message || before.tone !== after.tone;
  });

  // The only intended divergence: a measured total of exactly zero used to be
  // called "slightly negative" in red.
  for (const summary of diverging) {
    assert.equal(Number(summary.total_pnl), 0, `drifted on a non-zero total: ${summary.total_pnl}`);
    assert.equal(classifySummaryStatus(summary).tone, 'neutral');
  }
  assert.equal(diverging.length, 10); // the two spellings of zero, across five win rates

  // And the empty filter, which is not a measured case: the baseline fell through
  // to the red "slightly negative" branch because Number(null) is 0.
  assert.deepEqual(baselineStatus(EMPTY_FILTER), {
    message: 'Your strategies are slightly negative',
    tone: 'negative',
  });
});

// --- wiring: the panel must render this verdict, not its own if-chain ----------

const panelPath = new URL('../src/components/journal/JournalDashboard.tsx', import.meta.url);
const panel = readFileSync(fileURLToPath(panelPath), 'utf8');

const between = (from, to) => {
  const start = panel.indexOf(from);
  const end = panel.indexOf(to);
  assert.ok(start !== -1 && end > start, `could not isolate ${from} in the panel`);
  return panel.slice(start, end);
};

const banner = between('function PerformanceSummary', 'function PerformanceInsights');
const insights = between('function PerformanceInsights', 'function EmptyState');

test('the banner renders the shared verdict, not an inline coercion', () => {
  assert.match(panel, /import \{ classifySummaryStatus \} from '@\/lib\/journalSummaryStatus';/);
  assert.match(banner, /const status = classifySummaryStatus\(summary\);/);
  assert.match(banner, /\{status\.message\}/);
  assert.ok(!banner.includes('const winRate = Number(summary.win_rate);'), 'the banner still coerces the ratio');
  assert.ok(!/let status: string;/.test(banner), 'the banner still decides its sentence inline');
});

test('the one remaining win-rate coercion is unreachable without the count gate', () => {
  // Insight 4 reads `Number(summary.win_rate)`, which is 0 for the null the endpoint
  // only ever sends together with total_trades === 0. That pairing is what keeps it
  // honest, so the guard has to stay in front of every render of that local.
  const coerce = insights.indexOf('const winRate = Number(summary.win_rate);');
  assert.ok(coerce !== -1, 'Insight 4 no longer reads the summary win rate');
  const section = insights.slice(coerce, insights.indexOf('// Insight 5'));
  const gate = section.indexOf('if (totalTrades < 30)');
  const firstRender = section.indexOf('winRate.toFixed(');
  assert.ok(gate !== -1, 'the >= 30 trades evidence gate was removed');
  assert.ok(firstRender > gate, 'win rate is rendered before the trade-count gate');
});
