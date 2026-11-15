// The revision form's grain capability and change-preview decisions, plus proof
// that RevisionPanel actually calls them.
//
// This is behaviour coverage at the pure boundary the component imports, not
// page-interaction coverage: the repo's node:test lane cannot render JSX, so the
// browser flow stays with e2e/strategy-revision.spec.ts (run separately, with a
// server). The wiring assertions at the bottom are what close that gap for
// "the component uses this helper" without adding a new test framework.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  canonicalSpec,
  diffSpecs,
  isUnsupportedSpec,
  unsupportedRevisionKeys,
} from '../src/lib/revisionSpec.ts';

const BASE = {
  schema_version: '1.0',
  template_type: 'buy_and_hold',
  asset_ids: ['DEMO-ALPHA'],
  frequency: '1d',
  start_date: '2024-01-02',
  end_date: '2024-02-14',
  initial_cash: 10000,
  signal_parameters: {},
  commission_rate: 0.001,
  slippage_rate: 0.001,
  benchmark_id: 'DEMO-BENCHMARK',
  currency: 'USD',
};

const daily = (overrides = {}) => ({ ...BASE, ...overrides });

test('an explicitly declared daily grain stays revisable', () => {
  for (const value of ['1d', ' 1d ', '1D']) {
    const verdict = isUnsupportedSpec(daily({ timeframe: value }));
    assert.equal(verdict.blocked, false, JSON.stringify(value));
    assert.deepEqual(unsupportedRevisionKeys(daily({ timeframe: value })), []);
  }
});

test('a legacy version without the declaration stays revisable', () => {
  const verdict = isUnsupportedSpec(BASE);
  assert.equal(verdict.blocked, false, verdict.reason);
  assert.ok(!('timeframe' in BASE));
});

test('any other timeframe declaration is refused, never downgraded', () => {
  // null and "" are refused as illegal values rather than treated as absent:
  // the guard must not use truthiness to decide.
  for (const value of ['5m', '1h', '4h', '15m', '30m', 'daily', null, '', ' ', 0, 1, true]) {
    const verdict = isUnsupportedSpec(daily({ timeframe: value }));
    assert.equal(verdict.blocked, true, `${JSON.stringify(value)} was accepted`);
    assert.match(verdict.reason, /timeframe/);
    assert.match(verdict.reason, /will not downgrade/);
  }
});

test('a session block is refused whatever it holds, including under 1d', () => {
  for (const session of [{ timeframe: '5m' }, {}, null, '']) {
    assert.deepEqual(
      unsupportedRevisionKeys(daily({ session, timeframe: '1d' })),
      ['session'],
      JSON.stringify(session)
    );
    assert.equal(isUnsupportedSpec(daily({ session, timeframe: '1d' })).blocked, true);
  }
});

test('declaring 1d does not exempt another unsupported field', () => {
  const verdict = isUnsupportedSpec(daily({ timeframe: '1d', frequency: '5m' }));
  assert.equal(verdict.blocked, true);
  assert.match(verdict.reason, /runs at 5m/);

  const both = isUnsupportedSpec({ ...BASE, frequency: '1h', timeframe: '5m' });
  assert.equal(both.blocked, true);
  assert.match(both.reason, /1h/);
});

test('a missing spec is still refused', () => {
  for (const value of [null, undefined, 0, '']) {
    assert.equal(isUnsupportedSpec(value).blocked, true);
  }
});

test('undeclared to declared 1d is a strategy change, not "nothing changed yet"', () => {
  const preview = diffSpecs(BASE, daily({ timeframe: '1d' }));
  assert.deepEqual(preview.fields, ['timeframe']);
  assert.equal(preview.kind, 'strategy_only');
});

test('the same declaration on both sides is not reported as an edit', () => {
  const source = daily({ timeframe: '1d' });
  const draft = { ...source, commission_rate: 0.002 };
  const preview = diffSpecs(source, draft);
  assert.deepEqual(preview.fields, ['commission_rate']);
  assert.equal(preview.kind, 'strategy_only');
});

test('unchanged legacy specs still preview as no change', () => {
  assert.deepEqual(diffSpecs(BASE, { ...BASE }).fields, []);
});

test('the canonical form does not invent a timeframe default', () => {
  // Mirrors the backend serializer, which omits the key when undeclared: adding
  // a default here would make every legacy version look like it declared 1d.
  assert.ok(!('timeframe' in canonicalSpec(BASE)));
  assert.equal(canonicalSpec(daily({ timeframe: '1d' })).timeframe, '1d');
});

test('period edits stay separated from the grain declaration', () => {
  const periodOnly = diffSpecs(BASE, { ...BASE, end_date: '2024-03-01' });
  assert.equal(periodOnly.kind, 'period_only');
  const grainAndPeriod = diffSpecs(BASE, daily({ timeframe: '1d', end_date: '2024-03-01' }));
  assert.equal(grainAndPeriod.kind, 'strategy_and_period');
  assert.deepEqual(grainAndPeriod.fields, ['end_date', 'timeframe']);
});

// --- wiring: the panel must go through the functions tested above -----------

const panelPath = new URL('../src/components/experiments/RevisionPanel.tsx', import.meta.url);
const panel = readFileSync(fileURLToPath(panelPath), 'utf8');

test('RevisionPanel imports the shared revision-spec helpers', () => {
  assert.match(
    panel,
    /import \{[^}]*canonicalSpec[^}]*diffSpecs[^}]*flatten[^}]*isUnsupportedSpec[^}]*\} from '@\/lib\/revisionSpec';/s
  );
  for (const local of ['function isUnsupportedSpec', 'function diffSpecs', 'function canonicalSpec', 'function flatten']) {
    assert.ok(!panel.includes(local), `the panel still defines its own copy of ${local}`);
  }
});

test('the panel drives its UI state from that verdict', () => {
  assert.match(panel, /const unsupported = isUnsupportedSpec\(base\.strategy_spec\);/);
  assert.match(panel, /if \(unsupported\.blocked\) return;/);
  assert.match(panel, /disabled=\{unsupported\.blocked \|\| submitting\}/);
  assert.match(panel, /unsupported\.blocked &&/);
  assert.match(panel, /const preview = diffSpecs\(spec, draftSpec\);/);
});

test('the presence-based rejection that hid legal 1d versions is gone', () => {
  assert.ok(!/if \(spec\.session \|\| spec\.timeframe\)/.test(panel));
});

test('the draft spec carries the declaration forward instead of dropping it', () => {
  // The form has no grain input, so spreading the source spec is what keeps an
  // explicit 1d on the revision; if that spread is removed the panel starts
  // erasing the declaration and this assertion has to fail.
  assert.match(panel, /const draftSpec = \{\s*\.\.\.spec,/s);
  const kept = diffSpecs(daily({ timeframe: '1d' }), { ...daily({ timeframe: '1d' }) });
  assert.deepEqual(kept.fields, []);
});
