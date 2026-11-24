// G3-5: the Unified tab is one table over two ledgers, and the ruling for this
// group is 「Unified 篩選、匯出、計數使用一致範圍；不適用的控制要移除或明示，不留假篩選」.
//
// Three separate claims used to be false on this tab. The fetch sent only
// `source`, so every control in the filter bar moved the other tabs and not
// this one. The export button wrote `/journal/trades.csv`, which is the
// research leg alone, so the file never contained the paper rows the table had
// just shown. And the Date cell read `entry_time`, which a daily paper fill
// does not have — `paper_fills.bar_timestamp` is nullable because the daily
// path never samples an instant — so those rows rendered as a dash and looked
// like rows the filter had lost.
//
// The scope arithmetic itself is the server's and is pinned by
// `backend/tests/test_journal_unified_scope.py`. What is pinned here is the
// client half: the controls actually leave the browser, the download goes to
// the endpoint that matches the tab, and a cell says the grain the record was
// written at instead of inventing one.
//
// Run with: node22 --test tests/journalUnifiedScope.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const { api } = await import('../src/lib/api.ts');
const {
  FILL_PNL_NOTE,
  FILL_PNL_STATUS,
  unifiedDateLabel,
  unifiedPnlNote,
  unifiedReason,
} = await import('../src/lib/journalUnifiedCells.ts');

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const backend = (rel) => src(`../../backend/${rel}`);

const NINE = [
  'instrument_id',
  'rule_id',
  'rule_family',
  'interval_minutes',
  'date_from',
  'date_to',
  'pnl_min',
  'pnl_max',
  'exit_reason',
];

const ALL_NINE = {
  instrument_id: 'CRYPTO:KRAKEN:BTC-USD',
  rule_id: 'breakout_trend',
  rule_family: 'trend',
  interval_minutes: 1440,
  date_from: '2026-08-01',
  date_to: '2026-10-01',
  pnl_min: '-50',
  pnl_max: '500',
  exit_reason: 'stop_loss',
};

async function captureRequest(fn) {
  const original = globalThis.fetch;
  let requested = null;
  globalThis.fetch = async (input) => {
    requested = String(typeof input === 'string' ? input : input.url);
    return new Response(JSON.stringify({ trades: [], summary: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  try {
    await fn();
  } finally {
    globalThis.fetch = original;
  }
  assert.ok(requested, 'no request was made, so nothing about its scope is known');
  return requested;
}

function paramsOf(url) {
  const query = url.slice(url.indexOf('?') + 1);
  return new URLSearchParams(query);
}

// --- the filter bar reaches this tab ----------------------------------------

test('the unified page request carries all nine controls', async () => {
  const url = await captureRequest(() => api.journal.unified('all', ALL_NINE));
  const params = paramsOf(url);

  assert.ok(url.includes('/api/v1/journal/unified?'), url);
  assert.equal(params.get('source'), 'all');
  for (const name of NINE) {
    assert.equal(
      params.get(name),
      String(ALL_NINE[name]),
      `${name} never left the browser, so the server cannot filter on it`,
    );
  }
});

test('an unset control is absent, not empty', async () => {
  const url = await captureRequest(() => api.journal.unified('paper', { rule_id: '', rule_family: undefined }));
  const params = paramsOf(url);

  assert.equal(params.get('source'), 'paper');
  assert.ok(!params.has('rule_id'), 'an empty rule_id asks the server for the empty string');
  assert.ok(!params.has('rule_family'), 'an undefined family became a filter value');
});

// --- the download matches the tab it was pressed from ----------------------

test('the unified export is a different endpoint from the trades export', async () => {
  const url = await captureRequest(() => api.journal.unifiedCsv('all', ALL_NINE));

  assert.ok(url.includes('/api/v1/journal/unified.csv'), url);
  assert.ok(!url.includes('/journal/trades.csv'), url);
  // The research-only file is the defect: it cannot contain the paper rows the
  // table showed, so the export has to be the merged one.
  const tradesUrl = await captureRequest(() => api.journal.tradesCsv(ALL_NINE));
  assert.ok(tradesUrl.includes('/journal/trades.csv'), tradesUrl);
});

test('the unified export sends the same scope as the page it exported', async () => {
  const pageUrl = await captureRequest(() => api.journal.unified('research', ALL_NINE));
  const csvUrl = await captureRequest(() => api.journal.unifiedCsv('research', ALL_NINE));

  const page = paramsOf(pageUrl);
  const csv = paramsOf(csvUrl);
  assert.equal(csv.get('source'), page.get('source'));
  for (const name of NINE) {
    assert.equal(csv.get(name), page.get(name), `${name} filtered the page but not the file`);
  }
});

// --- the page and the export are chosen by the tab, not hardcoded ----------

test('the dashboard routes the export button by the active tab', () => {
  const panel = src('../src/components/journal/JournalDashboard.tsx');

  assert.match(panel, /activeTab === 'unified'[\s\S]{0,200}unifiedCsv/, 'no tab-aware export');
  assert.match(panel, /journal\.unifiedCsv\(unifiedSource/, 'the export ignores the source the tab shows');
  // The panel has to be told which source is selected, or the file and the
  // table disagree the moment the user leaves "all".
  assert.match(panel, /source=\{unifiedSource\}/, 'the source lives inside the panel only');
  assert.match(panel, /filters=\{appliedFilters\}/, 'the filter bar still does not reach the unified tab');
});

// --- a cell states the grain the record was written at ---------------------

test('a daily fill shows its day instead of a dash', () => {
  const daily = { time_precision: 'day', entry_time: null, execution_date: '2026-08-01' };
  const label = unifiedDateLabel(daily);

  assert.equal(label, '2026/08/01 · day');
  // The pre-fix expression was formatDate(entry_time), which is '—' here.
  assert.notEqual(label, '—');
});

test('a day-grain row never grows a clock time', () => {
  const label = unifiedDateLabel({
    time_precision: 'day',
    entry_time: null,
    execution_date: '2026-08-01',
  });

  // A midnight the platform never observed would be a fabricated instant.
  assert.ok(!/\d{2}:\d{2}/.test(label), `day-grain row invented a time: ${label}`);
});

test('a bar row keeps the instant and claims no day-only label', () => {
  const label = unifiedDateLabel({
    time_precision: 'bar',
    entry_time: '2026-08-01T15:30:00',
    execution_date: '2026-08-01',
  });

  assert.match(label, /^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}$/, label);
  assert.ok(!label.includes('day'), `an observed instant was labelled as a bare day: ${label}`);
});

test('a row with no time at all is a dash, not a guess', () => {
  assert.equal(unifiedDateLabel({ time_precision: 'bar', entry_time: null, execution_date: null }), '—');
  // A day label needs the day; precision alone is not a date.
  assert.equal(unifiedDateLabel({ time_precision: 'day', entry_time: null, execution_date: null }), '—');
});

// --- an absent P&L is explained, not filled in ----------------------------

test('a fill explains its empty P&L instead of showing a number', () => {
  const fill = { pnl_status: FILL_PNL_STATUS, net_pnl: null };

  assert.equal(unifiedPnlNote(fill), FILL_PNL_NOTE);
  assert.ok(!/[\d$]/.test(FILL_PNL_NOTE), 'the note carries an amount, which is the fabrication');
});

test('a settled research row gets no note', () => {
  assert.equal(unifiedPnlNote({ pnl_status: 'settled', net_pnl: '150.5' }), null);
  // Nothing said about why is not the same as a reason: an unlabelled absence
  // must not borrow the fill's explanation.
  assert.equal(unifiedPnlNote({ pnl_status: null, net_pnl: null }), null);
});

test('the client status literal is the one the service writes', () => {
  const service = backend('app/services/journal_service.py');

  assert.ok(
    service.includes(`"${FILL_PNL_STATUS}"`),
    `journal_service.py no longer writes ${FILL_PNL_STATUS}; the note is dead text`,
  );
});

// --- the two reasons are two facts ---------------------------------------

test('exit reason and fill reason are labelled as different things', () => {
  assert.deepEqual(unifiedReason({ exit_reason: 'stop_loss', fill_reason: null }), {
    kind: 'exit',
    text: 'stop_loss',
  });
  const fill = unifiedReason({ exit_reason: null, fill_reason: 'market_at_close' });
  assert.equal(fill.kind, 'fill');
  assert.equal(fill.text, 'market_at_close');
  assert.deepEqual(unifiedReason({ exit_reason: null, fill_reason: null }), {
    kind: 'none',
    text: '—',
  });
  // A research trade has no fill, and a fill has no exit signal. If both ever
  // arrive on one row, the column must not silently prefer one at random.
  assert.equal(unifiedReason({ exit_reason: 'stop_loss', fill_reason: 'market_at_close' }).kind, 'exit');
});
