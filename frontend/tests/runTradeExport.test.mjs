// Unit coverage for what the UI does with a run's per-fill export.
//
// Run with: node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/runTradeExport.test.mjs
// The two basis strings and the download filename below are the server's,
// copied from backend/app/services/run_trades.py (BASIS_WITH_TRADES,
// BASIS_WITHOUT_TRADES) and from the Content-Disposition header in
// backend/app/api/runs.py. backend/tests/test_run_trades_export.py reads this
// file and pins both ends, so a rename on one side fails on the other.
//
// The wiring section at the bottom reads the page's source text. It proves the
// page goes through the functions tested above — nothing in it is
// rendered-UI evidence.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  EXPORT_BASIS_WITHOUT_TRADES,
  EXPORT_BASIS_WITH_TRADES,
  describeExportHeadline,
  exportFileName,
} from '../src/lib/runExport.ts';

const PAGE_PATH = '../src/app/experiments/[id]/runs/[runId]/page.tsx';
const API_PATH = '../src/lib/api.ts';

const RUN_ID = '7d1a2b3c-4455-6677-8899-001122334455';

function summary(overrides = {}) {
  return {
    run_id: RUN_ID,
    experiment_id: '3c2b1a7d-5544-6677-8899-aabbccddeeff',
    experiment_version_id: '11223344-5566-7788-99aa-bbccddeeff00',
    version_number: 1,
    owner_id: '9a8b7c6d-1122-3344-5566-778899aabbcc',
    status: 'completed',
    export_basis: EXPORT_BASIS_WITH_TRADES,
    basis_note: 'Every fill this run recorded is listed.',
    trade_count: 3,
    columns: ['transaction_id'],
    provenance: {},
    trades: [],
    ...overrides,
  };
}

function interfaceKeys(text, name) {
  const block = text.match(new RegExp(`interface ${name} \\{([^}]*)\\}`));
  assert.ok(block, `src/lib/runExport.ts must declare interface ${name}`);
  return block[1]
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('/') && !line.startsWith('*'))
    .map((line) => line.split(':')[0].trim());
}

test('the two bases the server can answer with are named exactly', () => {
  assert.equal(EXPORT_BASIS_WITH_TRADES, 'completed_with_trades');
  assert.equal(EXPORT_BASIS_WITHOUT_TRADES, 'completed_without_trades');
  assert.notEqual(EXPORT_BASIS_WITH_TRADES, EXPORT_BASIS_WITHOUT_TRADES);
});

test('the download name is the one the server puts in its header', () => {
  assert.equal(exportFileName(RUN_ID), `run-${RUN_ID}-trades.csv`);
});

test('a run that filled legs is counted, not described away', () => {
  assert.equal(describeExportHeadline(summary({ trade_count: 3 })), '3 filled leg(s) recorded.');
});

test('a run that filled nothing says so, and does not say it failed', () => {
  const line = describeExportHeadline(
    summary({ export_basis: EXPORT_BASIS_WITHOUT_TRADES, trade_count: 0 }),
  );
  assert.match(line, /zero fills/);
  assert.doesNotMatch(line, /fail|cancel|error/i, 'an empty export is not a failure');
});

test('an unknown basis is reported as unknown instead of being given a meaning', () => {
  const line = describeExportHeadline(summary({ export_basis: 'completed_in_the_future', trade_count: 7 }));
  assert.match(line, /unrecognised basis \(completed_in_the_future\)/);
  assert.match(line, /7 row\(s\)/);
  assert.doesNotMatch(line, /zero fills/);
});

// ── wiring ─────────────────────────────────────────────────────────────────

const api = readFileSync(new URL(API_PATH, import.meta.url), 'utf8');
const page = readFileSync(new URL(PAGE_PATH, import.meta.url), 'utf8');
const EXPORT_LIB = readFileSync(new URL('../src/lib/runExport.ts', import.meta.url), 'utf8');

test('the client calls the two export routes, and takes the CSV as text', () => {
  assert.match(api, /trades: \(runId: string\) => apiFetch\(`\/api\/v1\/runs\/\$\{runId\}\/trades`\)/);
  assert.match(
    api,
    /tradesCsv: \(runId: string\) => apiFetchText\(`\/api\/v1\/runs\/\$\{runId\}\/trades\.csv`\)/,
    'the CSV must not be forced through .json()',
  );
});

test('the export panel asks the server before it offers a file', () => {
  assert.match(page, /const \[exportSummary, setExportSummary\] = useState<RunTradesExport \| null>\(null\)/);
  assert.match(page, /await api\.runs\.trades\(runId\)/);
  assert.match(page, /await api\.runs\.tradesCsv\(runId\)/);
  assert.match(page, /data-testid="run-export-check"/);
  assert.match(page, /onClick=\{checkExport\}/);
  // The download only appears once the server has said what the export means.
  assert.match(page, /\{exportSummary && \(\s*<button[\s\S]{0,400}?data-testid="run-export-download"/);
  assert.match(page, /onClick=\{downloadExport\}/);
});

test('the panel shows the server\'s own note instead of paraphrasing it', () => {
  assert.match(page, /data-testid="run-export-basis"/);
  assert.match(page, /\{describeExportHeadline\(exportSummary\)\}/);
  assert.match(page, /\{exportSummary\.basis_note\}/, 'the basis sentence is the server\'s');
  // The count is never re-derived here from result_json: the export route says it.
  assert.doesNotMatch(page, /exportSummary\.trade_count === 0 \?/);
});

test('a refused export is shown, not swallowed', () => {
  assert.match(page, /role="alert"[\s\S]{0,120}?data-testid="run-export-error"/);
  assert.match(page, /setExportError\(`\$\{heading\}: \$\{detail\}`\)/);
  assert.match(page, /'This run has no fills to export'/);
});

test('a saved file is confirmed with the name the server chose', () => {
  assert.match(page, /const filename = exportFileName\(runId\)/);
  assert.match(page, /anchor\.download = filename/);
  assert.match(page, /Saved \$\{filename\}/);
});

test('the TypeScript model declares exactly the fields the route returns', () => {
  // The same list backend/tests/test_run_trades_export.py pins against the live
  // JSON, from the other end: a field renamed on one side fails a lane, not a
  // silently empty column in a file somebody else opens later.
  const expected = [
    'run_id', 'experiment_id', 'experiment_version_id', 'version_number',
    'owner_id', 'status', 'export_basis', 'basis_note', 'trade_count',
    'columns', 'provenance', 'trades',
  ];
  assert.deepEqual(
    interfaceKeys(EXPORT_LIB, 'RunTradesExport').sort(),
    [...expected].sort(),
  );
});
