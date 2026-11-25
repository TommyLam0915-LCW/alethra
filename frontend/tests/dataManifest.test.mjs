// Unit coverage for the data-manifest helpers the authoring page renders.
//
// Run with: node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dataManifest.test.mjs
//
// The status, role and checksum literals below are copied from
// backend/app/api/data_manifest.py (_roles at :257, the overall verdict at the
// end of build_data_manifest) and backend/app/engine/backtest.py
// (:: _compute_stored_checksum). backend/tests/test_data_manifest_contract.py
// pins the server side of each of those, so this file and that one are the two
// ends of the same contract.
//
// The wiring section at the bottom reads the frontend's source text. It proves
// that a page goes through the entry tested above — nothing in it is
// rendered-UI evidence.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  MANIFEST_STATUS_COPY,
  MULTIPLE_PROVIDERS,
  ROLE_LEGEND,
  STATUS_TONE_COLOUR,
  UNKNOWN,
  display,
  displayChecksum,
  isStale,
  manifestStatusCopy,
  previewDates,
  providerCandidates,
  roleLegend,
  specFingerprint,
  statusToneColour,
} from '../src/lib/dataManifest.ts';

const FULL_CHECKSUM =
  '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08';

test('the four verdicts the server can return all have copy', () => {
  // Copied from build_data_manifest: "blocked" / "partial" / "ready", plus
  // "invalid_spec" from the contract-gate branch. A fifth token would render
  // through the unknown branch below, but the pair should move together.
  assert.deepEqual(Object.keys(MANIFEST_STATUS_COPY).sort(), [
    'blocked',
    'invalid_spec',
    'partial',
    'ready',
  ]);
});

test('a ready verdict is labelled as a statement about rows, not permission', () => {
  const ready = manifestStatusCopy('ready');
  assert.equal(ready.tone, 'ok');
  assert.match(ready.detail, /not permission to run/i);
  // 「不把來源 hash 相符當成市場真實性獨立證明」: the green line has to carry its
  // own denial of market reality, because it is the one verdict a user reads as
  // "this is good".
  assert.match(ready.detail, /not a claim that the prices are real/i);
});

test('an unrecognised verdict cannot inherit the success colour', () => {
  const unknown = manifestStatusCopy('green_light');
  assert.notEqual(unknown.tone, 'ok');
  assert.match(unknown.label, /green_light/, 'the token must be visible, not swallowed');
  assert.match(unknown.detail, /makes no claim/i);
});

test('every status tone has a colour that is an actually-defined variable', () => {
  for (const tone of ['ok', 'warn', 'muted']) {
    assert.match(statusToneColour(tone), /^var\(--[a-z-]+\)$/);
    // `var(--success)` / `var(--danger)` are not declared in globals.css, so a
    // map that named them painted with a fallback the rest of the app ignores.
    assert.doesNotMatch(statusToneColour(tone), /--(success|danger)\b/);
  }
  assert.deepEqual(Object.keys(STATUS_TONE_COLOUR).sort(), ['muted', 'ok', 'warn']);
});

test('the three roles a row can carry are each explained', () => {
  assert.deepEqual(Object.keys(ROLE_LEGEND).sort(), ['benchmark', 'loaded_not_traded', 'traded']);
  assert.match(roleLegend('benchmark'), /warning/i);
  assert.match(roleLegend('timekeeper'), /not described by this frontend build/i);
});

test('a value the server omitted displays as UNKNOWN, a known zero does not', () => {
  assert.equal(display(null), UNKNOWN);
  assert.equal(display(undefined), UNKNOWN);
  assert.equal(display('   '), UNKNOWN);
  assert.equal(display(NaN), UNKNOWN);
  assert.equal(display(0), '0', 'zero bars is a count, not a gap in the record');
  assert.equal(display(54), '54');
  assert.equal(display(false), 'no');
  assert.equal(display('synthetic'), 'synthetic');
});

test('a checksum is displayed whole or reported as absent', () => {
  assert.equal(displayChecksum(FULL_CHECKSUM), FULL_CHECKSUM, 'truncating a checksum breaks comparison');
  assert.match(displayChecksum(null), /no dataset was read/i);
  assert.match(displayChecksum(''), /empty checksum/i);
});

test('a truncated date preview states its total instead of implying an end', () => {
  const dates = Array.from({ length: 54 }, (_, i) => `2024-01-${String((i % 31) + 1).padStart(2, '0')}`);
  const preview = previewDates(dates);
  assert.equal(preview.total, 54);
  assert.equal(preview.shown.length, 5);
  assert.equal(preview.hidden, 49);
  assert.deepEqual(previewDates(null), { shown: [], hidden: 0, total: 0 });
  assert.equal(previewDates(['2024-01-02'], 5).hidden, 0);
});

test('the fingerprint ignores only the approval stamp', () => {
  const base = { template_type: 'buy_and_hold', asset_ids: ['US:AAPL'], start_date: '2024-01-02' };
  assert.equal(
    specFingerprint({ ...base, user_approved_at: '2026-11-16T00:00:00.000Z' }),
    specFingerprint({ ...base, user_approved_at: '2020-01-01T00:00:00.000Z' }),
    'the stamp changes on every render; including it would make every verdict stale on sight',
  );
  assert.notEqual(specFingerprint(base), specFingerprint({ ...base, start_date: '2024-01-03' }));
  assert.notEqual(specFingerprint(base), specFingerprint({ ...base, template_type: 'sma_crossover' }));
});

test('the fingerprint reads content, not key order or object identity', () => {
  const a = { risk_parameters: { stop_price: 100, target_price: 200 }, template_type: 'buy_and_hold' };
  const b = { template_type: 'buy_and_hold', risk_parameters: { target_price: 200, stop_price: 100 } };
  assert.equal(specFingerprint(a), specFingerprint(b));
  assert.equal(specFingerprint(a), specFingerprint(structuredClone(a)));
  // A field never filled in and a field explicitly cleared describe the same run.
  assert.equal(
    specFingerprint({ template_type: 'buy_and_hold', benchmark_id: undefined }),
    specFingerprint({ template_type: 'buy_and_hold' }),
  );
});

test('binding the check to a provider is a different question', () => {
  const spec = { template_type: 'buy_and_hold' };
  assert.notEqual(specFingerprint(spec, null), specFingerprint(spec, 'synthetic'));
  assert.equal(specFingerprint(spec, undefined), specFingerprint(spec, null));
});

test('a verdict is never stale before it has been checked', () => {
  assert.equal(isStale(null, 'abc'), false, 'unchecked is not stale');
  assert.equal(isStale('abc', 'abc'), false);
  assert.equal(isStale('abc', 'abd'), true);
});

test('the verdict only goes stale when the content it described changed', () => {
  // The provider binding is part of the question asked, so re-binding with the
  // spec untouched must still invalidate the previous verdict.
  const spec = { template_type: 'buy_and_hold', start_date: '2024-01-01' };
  assert.equal(isStale(specFingerprint(spec, null), specFingerprint(spec, null)), false);
  assert.equal(isStale(specFingerprint(spec, 'kraken'), specFingerprint(spec, 'twelvedata')), true);
});

// --- provider candidates: built from the response, never from a guessed list ---

function manifestWith(blockers, instruments) {
  return { blockers, instruments };
}

function blocker(code, instrumentId = null) {
  return { code, instrument_id: instrumentId, message: `${code} message`, remedy: null };
}

function row(instrumentId, status, providerIds) {
  return { instrument_id: instrumentId, role: 'traded', status, provider_ids: providerIds };
}

test('the multiple-providers code is the server token, spelled as it emits it', () => {
  // backend/app/api/data_manifest.py:53 names this blocker; the panel's control
  // appears only on an exact match, so a drifted string means a dead end.
  assert.equal(MULTIPLE_PROVIDERS, 'multiple_providers');
});

test('candidates are empty when nothing was refused or nothing has been checked', () => {
  assert.deepEqual(providerCandidates(null), []);
  assert.deepEqual(providerCandidates(manifestWith([], [])), []);
  assert.deepEqual(
    providerCandidates(
      manifestWith(
        [blocker('absent_from_storage', 'BTC-USD')],
        [row('BTC-USD', 'absent', [])],
      ),
    ),
    [],
    'a blocker the binder cannot fix must not open the binder',
  );
});

test('the refusal is not instrument-scoped, so candidates come from the ambiguous rows', () => {
  // data_manifest.py:422-434 emits this blocker with instrument_id=None; reading
  // the instrument off the blocker would find nothing and the remedy would be
  // words the user cannot act on.
  const manifest = manifestWith(
    [blocker(MULTIPLE_PROVIDERS)],
    [
      row('ETH-BTC', 'stored', ['demo']),
      row('BTC-USD', 'ambiguous', ['twelvedata', 'kraken']),
    ],
  );
  assert.deepEqual(providerCandidates(manifest), ['kraken', 'twelvedata']);
});

test('a refusal cannot invent a provider the response did not list', () => {
  assert.deepEqual(
    providerCandidates(manifestWith([blocker(MULTIPLE_PROVIDERS)], [])),
    [],
    'refused with no rows named — there is nothing to offer',
  );
  assert.deepEqual(
    providerCandidates(manifestWith([blocker(MULTIPLE_PROVIDERS)], [row('BTC-USD', 'ambiguous', [])])),
    [],
  );
  assert.deepEqual(
    providerCandidates(
      manifestWith(
        [blocker(MULTIPLE_PROVIDERS)],
        [row('BTC-USD', 'ambiguous', ['kraken', 'kraken']), row('ETH-USD', 'ambiguous', ['kraken'])],
      ),
    ),
    ['kraken'],
    'the same provider refusing twice is one button',
  );
  assert.deepEqual(
    providerCandidates(
      manifestWith(
        [blocker(MULTIPLE_PROVIDERS)],
        [row('BTC-USD', 'ambiguous', ['kraken']), row('SPY', 'stored', ['twelvedata'])],
      ),
    ),
    ['kraken'],
    'a stored row is not a candidate, even when it names a provider',
  );
});

// --- wiring: source text, not rendered UI -------------------------------------

const API = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const PANEL = readFileSync(
  new URL('../src/components/experiments/DataManifestPanel.tsx', import.meta.url),
  'utf8',
);
const PAGE = readFileSync(
  new URL('../src/app/experiments/new/page.tsx', import.meta.url),
  'utf8',
);

test('the client posts the spec to the manifest route and can bind a provider', () => {
  assert.match(API, /dataManifest: \(spec: Record<string, unknown>, providerId\?: string \| null\)/);
  assert.match(API, /\/api\/v1\/strategy-specs\/data-manifest/);
  assert.match(API, /provider_id=\$\{encodeURIComponent\(providerId\)\}/);
  assert.match(API, /method: 'POST', body: JSON\.stringify\(spec\)/);
});

test('the page checks and creates from one builder, so the verdict cannot describe a different spec', () => {
  assert.match(PAGE, /const buildStrategySpec = \(\) => \(/);
  assert.match(PAGE, /<DataManifestPanel spec=\{buildStrategySpec\(\)\} assetLabel=\{assetId\} \/>/);
  assert.match(PAGE, /strategy_spec: \{\s*\.\.\.buildStrategySpec\(\),\s*user_approved_at: new Date\(\)\.toISOString\(\),/);
  // The old form built its own literal inside handleSubmit; a second copy is
  // how a checked verdict and a created experiment start disagreeing.
  assert.equal(
    (PAGE.match(/schema_version: '1\.0'/g) ?? []).length,
    1,
    'exactly one strategy_spec literal may exist in the page',
  );
});

test('the panel states the server disclaimers instead of paraphrasing them', () => {
  assert.match(PANEL, /\{manifest\.scope_note\}/);
  assert.match(PANEL, /manifest\.cannot_prove\.map/);
  assert.match(PANEL, /\{manifest\.checksum_basis\}/);
  assert.match(PANEL, /Object\.entries\(manifest\.run_behavior/);
  assert.match(PANEL, /\{issue\.message\}/);
  assert.match(PANEL, /Remedy: \{issue\.remedy\}/);
  // The verdict line must come from the status token, and a changed parameter
  // set must mark the displayed verdict as describing something else.
  assert.match(PANEL, /statusToneColour\(statusCopy\.tone\)/);
  assert.match(PANEL, /data-testid="exp-manifest-stale"/);
  assert.match(PANEL, /isStale\(checkedFingerprint, fingerprint\)/);
  assert.match(PANEL, /data-testid="exp-manifest-checksum"/);
  assert.match(PANEL, /data-testid="exp-manifest-check"/);
});

test('the panel cannot resolve a request out of order or claim a check it did not make', () => {
  assert.match(PANEL, /const sequence = useRef\(0\)/);
  assert.match(PANEL, /if \(token !== sequence\.current\) return;/);
  // No verdict text before the first response: the section says "Not checked".
  assert.match(PANEL, /data-testid="exp-manifest-unchecked"/);
  assert.doesNotMatch(PANEL, /exp-manifest-status"[^>]*>\s*ready/i);
});

test('a two-provider blocker is not left as advice the user cannot follow', () => {
  // The remedy line names provider_id; the control that applies it is built from
  // providerCandidates, so it exists only when the server emitted that blocker.
  assert.match(PANEL, /const candidates = providerCandidates\(manifest\)/);
  assert.match(PANEL, /candidates\.length > 0/);
  assert.match(PANEL, /data-testid=\{`exp-manifest-bind-\$\{candidate\}`\}/);
  assert.match(PANEL, /data-testid="exp-manifest-bind-none"/);
  assert.match(PANEL, /onClick=\{\(\) => check\(candidate\)\}/);
  assert.match(PANEL, /onClick=\{\(\) => check\(null\)\}/);
  // The binding participates in the fingerprint and in the request, so a bound
  // verdict cannot be presented as current for an unbound question.
  assert.match(PANEL, /specFingerprint\(spec, boundProvider\)/);
  assert.match(PANEL, /const requestedFingerprint = specFingerprint\(spec, provider\)/);
  assert.match(PANEL, /api\.strategyTemplates\.dataManifest\(spec, provider\)/);
  // onClick={check} would pass the click event as the provider.
  assert.doesNotMatch(PANEL, /onClick=\{check\}/);
});

const MANIFEST_LIB = readFileSync(new URL('../src/lib/dataManifest.ts', import.meta.url), 'utf8');

/** The field names declared on an exported interface, in source order. */
function interfaceKeys(source, name) {
  const block = new RegExp(`export interface ${name} \\{([\\s\\S]*?)\\n\\}`).exec(source);
  assert.ok(block, `src/lib/dataManifest.ts must declare interface ${name}`);
  return block[1]
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('/') && !line.startsWith('*'))
    .map((line) => line.split(':')[0].trim());
}

test('the TypeScript models declare exactly the fields the route returns', () => {
  // The same lists backend/tests/test_data_manifest_contract.py pins against the
  // live JSON (its _CONTRACT_KEYS), from the other end: a field renamed on one
  // side fails one of the two lanes, not silently.
  const expected = {
    GrainFact: ['interval_minutes', 'label', 'bar_count'],
    CalendarFact: [
      'market', 'reason', 'coverage_start', 'coverage_end',
      'expected_known_open_sessions', 'sessions_the_calendar_cannot_judge',
      'missing_known_open_sessions', 'missing_known_open_count',
      'bars_on_days_the_calendar_closes', 'note',
    ],
    ManifestInstrument: [
      'instrument_id', 'role', 'status', 'source', 'provider_id',
      'provider_ids', 'data_nature', 'bar_count', 'first_bar_date',
      'last_bar_date', 'grains', 'duplicate_dates', 'calendar',
    ],
    ManifestIssue: ['code', 'instrument_id', 'message', 'remedy'],
    DataManifest: [
      'status', 'template_type', 'window', 'provider_id_bound', 'instruments',
      'dataset_checksum', 'checksum_basis', 'bars_loaded', 'blockers',
      'warnings', 'run_behavior', 'engine_basis', 'scope_note', 'cannot_prove',
    ],
  };
  for (const [name, keys] of Object.entries(expected)) {
    assert.deepEqual(interfaceKeys(MANIFEST_LIB, name).sort(), [...keys].sort(), name);
  }
});
