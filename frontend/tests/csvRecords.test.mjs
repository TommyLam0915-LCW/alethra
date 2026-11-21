// `/journal/trades.csv` can answer a successful request with a file that contains
// only the header line, and the Export button has to tell those two outcomes apart
// instead of announcing a download that carried no trades.
//
// Run with: node22 --test tests/csvRecords.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { csvDataRowCount } from '../src/lib/csvRecords.ts';

const HEADER = 'run_id,net_pnl,pnl_reason_zh\r\n';

test('a header-only body exports no rows', () => {
  // Exactly what the endpoint sends for a filter that matches nothing.
  assert.equal(csvDataRowCount(HEADER), 0);
});

test('an empty body exports no rows and is not read as a negative count', () => {
  assert.equal(csvDataRowCount(''), 0);
  assert.equal(csvDataRowCount('\r\n'), 0);
});

test('data rows are counted one per record', () => {
  const body = `${HEADER}r1,1.5,止損\r\nr2,-2.0,訊號反轉\r\n`;
  assert.equal(csvDataRowCount(body), 2);
});

test('a body without a trailing terminator still counts its last record', () => {
  assert.equal(csvDataRowCount(`${HEADER}r1,1.5,ok`), 1);
});

test('a newline inside a quoted reason cell is not a new record', () => {
  // csv.QUOTE_MINIMAL quotes any field containing a line break, so line counting
  // would report two records where the file holds one trade.
  const body = `${HEADER}r1,1.5,"部分提前出場\r\n其餘持有到期满"\r\n`;
  assert.equal(csvDataRowCount(body), 1);
});

test('escaped quotes inside a cell do not end the cell early', () => {
  const body = `${HEADER}r1,1.5,"報價寫成 ""1.5"" 而非 1.5"\r\nr2,-1,plain\r\n`;
  assert.equal(csvDataRowCount(body), 2);
});

test('a quoted field holding a bare line feed is not a new record', () => {
  const body = `${HEADER}r1,1.5,"第一行\n第二行"\r\n`;
  assert.equal(csvDataRowCount(body), 1);
});

// --- wiring: the export handler must use this count, and must not swallow failures --

const componentPath = new URL(
  '../src/components/journal/JournalDashboard.tsx',
  import.meta.url,
);
const readPanel = () => readFileSync(fileURLToPath(componentPath), 'utf8');

// Each case reads the slice it needs, so a missing surface fails its own case
// instead of taking the whole file down before the counting contract runs.
function slice(source, from, to, what) {
  const start = source.indexOf(from);
  const end = source.indexOf(to);
  assert.ok(start !== -1 && end > start, `could not isolate ${what} in the panel`);
  return source.slice(start, end);
}

test('the export handler decides what happened from the file it received', () => {
  const panel = readPanel();
  // The counter has to come from this module; which names travel alongside it
  // is not part of the contract (P-2b added the truncation sentence here).
  assert.match(
    panel,
    /import \{[^}]*\bcsvDataRowCount\b[^}]*\} from '@\/lib\/csvRecords';/,
    'the panel does not import the shared counter',
  );
  const exporter = slice(panel, 'const handleDownloadCsv', 'const tabStyle', 'the export handler');
  assert.match(exporter, /csvDataRowCount\(text\)/, 'the export path never counts rows');
  assert.match(exporter, /setExportNotice\(/, 'a header-only export stays unannounced');
});

test('the export failure reason reaches the user instead of an empty catch', () => {
  const exporter = slice(
    readPanel(),
    'const handleDownloadCsv',
    'const tabStyle',
    'the export handler',
  );
  assert.match(exporter, /setExportError\(refusalFrom\(err\)\)/);
  // `catch {}` and `catch { // comment }` both discard the reason a person needs.
  const afterCatch = exporter.slice(exporter.indexOf('catch'));
  const withoutComments = afterCatch.replace(/\/\/[^\n]*/g, '');
  assert.ok(
    /setExportError/.test(withoutComments.slice(withoutComments.indexOf('{'), 120)),
    'the catch discards the error',
  );
});

test('the export surfaces are rendered, not just set', () => {
  const panel = readPanel();
  const errorSurface = slice(panel, 'exportError &&', 'exportNotice &&', 'the export error surface');
  assert.match(errorSurface, /role="alert"/, 'the export error is not announced to AT');
  assert.match(errorSurface, /\{exportError\}/);
  assert.match(panel, /\{exportNotice\}/, 'the export notice is set but never rendered');
});

test('the button cannot be clicked into a second concurrent export', () => {
  assert.match(
    readPanel(),
    /disabled=\{exporting\}/,
    'the export button stays live while a download is in flight',
  );
});
