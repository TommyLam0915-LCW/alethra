// The annualization basis panel has exactly three states, and each one is a
// promise to the reader: a usable basis shows its window and method, a refused
// basis shows why, and an old run says no basis was recorded rather than
// implying a figure. A fourth behaviour is required by construction: with
// available=true the panel must never say "Not computed".
//
// Run with: node --test tests/annualizationBasis.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const { describeBasis, fmtStamp } = await import('../src/lib/annualizationBasis.ts');

const AVAILABLE = {
  available: true,
  method_version: 'annualization-2026-11-10.1',
  frequency: 'daily',
  market: 'CRYPTO',
  interval_minutes: 1440,
  observations_per_year: 365.25,
  trading_days_per_year: 365.25,
  bars_per_trading_day: 1,
  session_minutes_per_day: 1440,
  elapsed_years: 1.968515,
  first_observation: '2024-11-13',
  last_observation: '2026-11-02',
  observation_count: 720,
  expected_observations: 720,
  coverage_pct: 100,
  calendar_source: 'app/providers/instruments.py',
  frequency_source: 'calendar',
  reason: null,
};

test('available: the panel names the method, market, window and cadence', () => {
  const view = describeBasis(AVAILABLE, {
    annualized_return_pct: { value: 42.2075, reason: null },
    annualized_volatility_pct: 57.3345,
  });
  assert.equal(view.state, 'available');
  assert.equal(view.heading, '可用');
  const rows = Object.fromEntries(view.rows.map((r) => [r.testid, r]));
  assert.equal(rows['annualization-basis-method'].value, 'annualization-2026-11-10.1');
  assert.equal(rows['annualization-basis-market'].value, 'CRYPTO');
  assert.equal(rows['annualization-basis-window'].value, '2024-11-13 → 2026-11-02');
  // Numbers follow the page's four-decimal display rule; the record keeps six,
  // so the panel shows 1.9685 for a stored elapsed_years of 1.968515.
  assert.equal(AVAILABLE.elapsed_years, 1.968515);
  assert.equal(rows['annualization-basis-elapsed'].value, '1.9685');
  assert.equal(rows['annualization-basis-frequency'].value, '每日 · 1440 分鐘／根');
  assert.equal(rows['annualization-basis-rate'].value, '365.25');
  assert.equal(rows['annualization-basis-coverage'].value, '720 筆 / 預期 720');
});

test('available: no field of the view says "Not computed"', () => {
  const view = describeBasis(AVAILABLE, { annualized_return_pct: 42.2075 });
  const serialized = JSON.stringify(view);
  assert.ok(!serialized.includes('Not computed'), serialized);
  assert.ok(!serialized.includes('未記錄'), serialized);
});

test('available: an absent derived figure is unknown, never "Not computed"', () => {
  const view = describeBasis(AVAILABLE, {
    annualized_return_pct: { value: null, reason: 'the sample spans 0.4 years, below the 0.95-year minimum' },
  });
  const [ret] = view.derived;
  assert.equal(ret.testid, 'metric-annualized_return_pct');
  assert.equal(ret.value, '—');
  assert.match(ret.reason, /below the 0.95-year minimum/);
  assert.ok(!JSON.stringify(view).includes('Not computed'));
});

test('derived figures carry the method and cadence source, not a bare number', () => {
  const view = describeBasis(AVAILABLE, { annualized_volatility_pct: 57.3345 });
  const vol = view.derived.find((d) => d.testid === 'metric-annualized_volatility_pct');
  assert.equal(vol.value, '57.3345');
  assert.equal(vol.note, 'derived · annualization-2026-11-10.1 · app/providers/instruments.py 日曆');
  const measured = describeBasis({ ...AVAILABLE, frequency_source: 'measured' }, {});
  assert.match(measured.derived[0].note, /由樣本身期量測/);
});

test('unavailable: the refusal reason reaches the reader and no figure is invented', () => {
  const basis = {
    available: false,
    method_version: 'annualization-2026-11-10.1',
    frequency: 'daily',
    market: 'CRYPTO',
    interval_minutes: 1440,
    observations_per_year: null,
    elapsed_years: 0.4,
    observation_count: 146,
    coverage_pct: null,
    calendar_source: 'app/providers/instruments.py',
    frequency_source: 'calendar',
    reason: 'the sample spans 0.4 years, below the 0.95-year minimum for annualization',
  };
  const view = describeBasis(basis, {
    annualized_return_pct: { value: null, reason: basis.reason },
  });
  assert.equal(view.state, 'unavailable');
  assert.equal(view.heading, '不可用');
  assert.equal(view.reason, basis.reason);
  assert.equal(view.derived[0].value, '—');
  assert.ok(!JSON.stringify(view).includes('Not computed'));
});

test('unavailable without a stated reason says so instead of staying silent', () => {
  const view = describeBasis({ available: false }, {});
  assert.equal(view.state, 'unavailable');
  assert.equal(view.reason, '引擎未提供理由');
});

test('an old run with no basis block records the absence, not a value', () => {
  for (const absent of [null, undefined]) {
    const view = describeBasis(absent, { total_return_pct: 12.5 });
    assert.equal(view.state, 'unrecorded');
    assert.equal(view.heading, '未記錄年化基準');
    assert.deepEqual(view.rows, []);
    assert.deepEqual(view.derived, []);
    assert.equal(view.reason, undefined);
  }
});

test('a metrics payload predating the contract behaves like an old run', () => {
  // Historical runs stored annualized_return_pct as a bare number and no basis
  // at all; the panel must not turn that number into an implied basis.
  const view = describeBasis(undefined, { annualized_return_pct: 27.4561 });
  assert.equal(view.state, 'unrecorded');
  assert.equal(view.derived.length, 0);
});

test('a daily window prints as dates and an intraday window keeps its clock time', () => {
  assert.equal(fmtStamp('2024-11-13'), '2024-11-13');
  assert.equal(fmtStamp('2024-03-01T14:00:00+00:00'), '2024-03-01 14:00');
  assert.equal(fmtStamp(null), '—');
  const view = describeBasis({ ...AVAILABLE, first_observation: null, last_observation: null }, {});
  const window = view.rows.find((r) => r.testid === 'annualization-basis-window');
  assert.equal(window.value, '未記錄樣本起訖');
});
