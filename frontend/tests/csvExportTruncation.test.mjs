// P-2b: `/journal/trades.csv` writes at most `CSV_EXPORT_ROW_LIMIT` rows, so a
// filter that matches more returns a file that opens cleanly and is a prefix of
// the answer. Nothing in the body reveals that — the missing rows were never
// written — so the server says it in response headers and the page says it back.
//
// The lane this locks is the one P-2 left open: `Exported N trades` was made
// truthful about the file, and this makes it truthful about the filter.
//
// Run with: node22 --test tests/csvExportTruncation.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const { api } = await import('../src/lib/api.ts');
const {
  CSV_EXPORT_HEADERS,
  csvDataRowCount,
  csvTruncationNotice,
  readCsvExportMeta,
} = await import('../src/lib/csvRecords.ts');

const HEADER = 'run_id,trade_id,instrument_id,net_pnl,pnl_reason_zh\r\n';
const TWO_ROWS = `${HEADER}run-1,t-1,BTC-USD,10.5,目標價達成\r\nrun-1,t-2,ETH-USD,-3.25,訊號反轉\r\n`;

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const panelSource = () => src('../src/components/journal/JournalDashboard.tsx');
const backend = (rel) => src(`../../backend/${rel}`);

async function withExportResponse(responseHeaders, body, fn) {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/csv; charset=utf-8', ...responseHeaders },
    });
  try {
    return await fn();
  } finally {
    globalThis.fetch = original;
  }
}

// --- the counts the server measured are what the client reports --------------

test('a complete export is not read as a truncated one', async () => {
  const result = await withExportResponse(
    {
      [CSV_EXPORT_HEADERS.rowsExported]: '2',
      [CSV_EXPORT_HEADERS.rowsMatched]: '2',
      [CSV_EXPORT_HEADERS.rowLimit]: '100000',
      [CSV_EXPORT_HEADERS.truncated]: 'false',
    },
    TWO_ROWS,
    () => api.journal.tradesCsv({}),
  );

  assert.equal(csvDataRowCount(result.text), 2, 'the body no longer reaches the caller');
  assert.equal(result.meta.rowsExported, 2);
  assert.equal(result.meta.rowsMatched, 2);
  assert.equal(result.meta.truncated, false);
  assert.equal(csvTruncationNotice(result.meta, 2), null);
});

test('a prefix of the filter is announced as one, with the numbers to match', async () => {
  const result = await withExportResponse(
    {
      [CSV_EXPORT_HEADERS.rowsExported]: '2',
      [CSV_EXPORT_HEADERS.rowsMatched]: '148320',
      [CSV_EXPORT_HEADERS.rowLimit]: '100000',
      [CSV_EXPORT_HEADERS.truncated]: 'true',
    },
    TWO_ROWS,
    () => api.journal.tradesCsv({}),
  );

  assert.equal(result.meta.truncated, true);
  const notice = csvTruncationNotice(result.meta, csvDataRowCount(result.text));
  assert.ok(notice, 'the truncated export produced no warning');
  assert.match(notice, /first 2 of the 148320/, notice);
  assert.match(notice, /148318 are missing/, notice);
  assert.match(notice, /Narrow the filters/, notice);
});

test('a response that says nothing about rows is not read as a complete file', async () => {
  // A proxy that strips the headers, or a server from before this fix: the
  // client has no count, so it must not invent one — but it also has no
  // evidence of truncation, so it warns about neither and stays silent.
  const result = await withExportResponse({}, TWO_ROWS, () => api.journal.tradesCsv({}));

  assert.equal(result.meta.rowsExported, null);
  assert.equal(result.meta.rowsMatched, null);
  assert.equal(result.meta.rowLimit, null);
  assert.equal(result.meta.truncated, false);
  assert.equal(csvTruncationNotice(result.meta, 2), null);
});

// --- a header value is a count or it is nothing ------------------------------

test('only a non-negative whole number counts as a row count', () => {
  const read = (value) =>
    readCsvExportMeta((name) =>
      name === CSV_EXPORT_HEADERS.rowsMatched ? value : null,
    ).rowsMatched;

  assert.equal(read('100000'), 100000);
  assert.equal(read('0'), 0);
  assert.equal(read(''), null);
  assert.equal(read('   '), null);
  assert.equal(read('abc'), null);
  assert.equal(read('-1'), null);
  assert.equal(read('1.5'), null);
});

test('truncation is a stated yes, not an inferred maybe', () => {
  // Anything other than the server's own "true" leaves the claim unmade: the
  // flag is the server's judgement over the same filter, not arithmetic here.
  for (const value of ['false', '1', 'yes', '', null]) {
    assert.equal(
      readCsvExportMeta((name) => (name === CSV_EXPORT_HEADERS.truncated ? value : null))
        .truncated,
      false,
      `${String(value)} was read as truncation`,
    );
  }
  assert.equal(
    readCsvExportMeta((name) =>
      name === CSV_EXPORT_HEADERS.truncated ? 'true' : null,
    ).truncated,
    true,
  );
});

test('a stated truncation without a matched count still says what is missing', () => {
  const notice = csvTruncationNotice(
    { rowsExported: null, rowsMatched: null, rowLimit: null, truncated: true },
    5000,
  );
  assert.ok(notice);
  assert.match(notice, /stopped at its row limit/);
  assert.match(notice, /fewer than your filters matched/);
  assert.doesNotMatch(notice, /null/);
});

// --- the page shows it, and the two sides name the same headers --------------

test('the export handler warns after the download, not instead of it', () => {
  const panel = panelSource();
  assert.match(
    panel,
    /import \{[^}]*\bcsvTruncationNotice\b[^}]*\} from '@\/lib\/csvRecords';/,
    'the panel does not import the truncation sentence',
  );
  const start = panel.indexOf('const handleDownloadCsv');
  const end = panel.indexOf('const tabStyle');
  assert.ok(start !== -1 && end > start, 'could not isolate the export handler');
  const exporter = panel.slice(start, end);

  assert.match(exporter, /setExportTruncated\(null\)/, 'a stale warning survives a new export');
  // The panel now downloads two files, one per tab, so each branch is pinned
  // separately: `meta` has to come straight off the awaited response of the
  // endpoint that tab shows, never reconstructed after the fact.
  assert.match(exporter, /const \{ text, meta \} = /, 'the handler drops the export headers');
  assert.match(exporter, /activeTab === 'unified'/, 'the download ignores which tab is open');
  assert.match(
    exporter,
    /\? await api\.journal\.unifiedCsv\(unifiedSource, appliedFilters\)/,
    'the unified branch does not await its own endpoint',
  );
  assert.match(
    exporter,
    /: await api\.journal\.tradesCsv\(appliedFilters\)/,
    'the trades branch does not await its own endpoint',
  );
  assert.match(exporter, /csvTruncationNotice\(meta, rows\)/);
  // The row count in the sentence is the one taken from the file itself.
  assert.ok(
    exporter.indexOf('setExportNotice(') < exporter.indexOf('csvTruncationNotice('),
    'the warning would replace the download claim instead of qualifying it',
  );
});

test('both csv clients hand back the headers, not just the body', () => {
  const client = src('../src/lib/api.ts');

  // A helper that returns `{ text }` alone would still compile at the call
  // site and lose the warning silently, so the promise type is pinned per
  // helper — the panel has two download branches now, not one.
  for (const helper of ['tradesCsv', 'unifiedCsv']) {
    const at = client.indexOf(`${helper}:`);
    assert.notEqual(at, -1, `${helper} is gone from the client`);
    const signature = client.slice(at, client.indexOf('=>', at));
    assert.match(
      signature,
      /Promise<\{ text: string; meta: CsvExportMeta \}>/,
      `${helper} no longer types the headers it returns`,
    );
  }
});

test('the truncation warning is rendered, not only set', () => {
  const panel = panelSource();
  const start = panel.indexOf('exportTruncated &&');
  const end = panel.indexOf('{/* Filter Bar */}');
  assert.ok(start !== -1 && end > start, 'could not isolate the truncation surface');
  const surface = panel.slice(start, end);

  assert.match(surface, /data-testid="journal-export-truncated"/);
  // A file missing rows is not a status the reader can scan past.
  assert.match(surface, /role="alert"/);
  assert.match(surface, /\{exportTruncated\}/);
});

test('the client reads the header names the backend actually sends', () => {
  const declared = backend('app/lib/export_headers.py').toLowerCase();
  const names = Object.values(CSV_EXPORT_HEADERS);
  assert.equal(names.length, 4);
  for (const name of names) {
    assert.ok(declared.includes(`"${name}"`), `no backend header is named ${name}`);
  }

  // Declaring a header is not enough: one missing from the CORS expose list is
  // invisible to the browser, which is the same silence as never sending it.
  const list = declared.slice(declared.indexOf('export_metadata_headers = ['));
  assert.ok(list.length > 0, 'the backend has no expose list to check');
  for (const constant of [
    'EXPORT_ROWS_EXPORTED_HEADER',
    'EXPORT_ROWS_MATCHED_HEADER',
    'EXPORT_ROW_LIMIT_HEADER',
    'EXPORT_TRUNCATED_HEADER',
  ]) {
    assert.ok(
      list.includes(constant.toLowerCase()),
      `${constant} is sent but never exposed to another origin`,
    );
  }
  assert.match(
    backend('app/main.py'),
    /expose_headers=\[REQUEST_ID_HEADER, \*EXPORT_METADATA_HEADERS\]/,
    'CORS does not expose the export metadata list',
  );
});

test('the row limit lives in the service, not in the route', () => {
  const service = backend('app/services/journal_service.py');
  assert.match(service, /CSV_EXPORT_ROW_LIMIT = 100_000/);
  assert.match(service, /limit=CSV_EXPORT_ROW_LIMIT/);

  const route = backend('app/api/journal.py');
  const csvRoute = route.slice(
    route.indexOf('@router.get("/trades.csv")'),
    route.indexOf('GET /journal/filters'),
  );
  assert.ok(csvRoute.length > 0, 'could not isolate the CSV route');
  assert.doesNotMatch(csvRoute, /100_000|100000/, 'the route hardcodes a cap the service owns');
});
