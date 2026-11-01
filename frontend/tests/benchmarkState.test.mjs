// Unit coverage for the run-detail benchmark state (FB-13).
//
// Run with: node --test tests/benchmarkState.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BENCHMARK_METRIC_KEYS,
  describeBenchmark,
  describeRecordedBenchmark,
} from '../src/lib/benchmarkState.ts';

test('a recorded benchmark return needs no explanation', () => {
  const state = describeBenchmark({
    metrics: { benchmark_total_return_pct: 5.5, excess_return_pct: 3.5 },
    warnings: [],
    methodologyParameters: { benchmark_id: 'DEMO-BENCHMARK' },
  });
  assert.deepEqual(state, { recorded: true });
});

test('a run with no benchmark requested says so instead of showing a bare dash', () => {
  const state = describeBenchmark({
    metrics: { benchmark_total_return_pct: null, excess_return_pct: null },
    warnings: [],
    methodologyParameters: { benchmark_id: null },
  });
  assert.equal(state.recorded, false);
  assert.match(state.missingReason ?? '', /no comparison series/i);
});

test('a requested benchmark whose return is absent is named', () => {
  const state = describeBenchmark({
    metrics: { benchmark_total_return_pct: null, excess_return_pct: null },
    warnings: [],
    methodologyParameters: { benchmark_id: 'DEMO-BENCHMARK' },
  });
  assert.equal(state.recorded, false);
  assert.match(state.missingReason ?? '', /DEMO-BENCHMARK/);
  assert.match(state.missingReason ?? '', /no benchmark return was computed/i);
});

test('the engine warning wins because it states the reason, not just the gap', () => {
  const state = describeBenchmark({
    metrics: null,
    warnings: ['Short sample period: only 12 trading days.', 'Benchmark DEMO-BENCHMARK unavailable for this date range.'],
    methodologyParameters: { benchmark_id: 'DEMO-BENCHMARK' },
  });
  assert.equal(state.missingReason, 'Benchmark DEMO-BENCHMARK unavailable for this date range.');
});

test('a run stored before the field existed is not described as "none requested"', () => {
  const state = describeBenchmark({
    metrics: { benchmark_total_return_pct: null },
    warnings: [],
    methodologyParameters: { commission_rate: 0.001 },
  });
  assert.equal(state.recorded, false);
  assert.match(state.missingReason ?? '', /does not record whether a benchmark was requested/i);
  assert.doesNotMatch(state.missingReason ?? '', /no comparison series/i);
});

test('the stored request distinguishes an id, an explicit none, and no record', () => {
  assert.equal(describeRecordedBenchmark({ benchmark_id: 'DEMO-BENCHMARK' }), 'DEMO-BENCHMARK');
  assert.equal(describeRecordedBenchmark({ benchmark_id: null }), 'None requested');
  assert.equal(describeRecordedBenchmark({ commission_rate: 0.001 }), 'Not recorded');
  assert.equal(describeRecordedBenchmark(null), 'Not recorded');
});

test('the two benchmark metric keys are the ones the page renders', () => {
  assert.deepEqual(BENCHMARK_METRIC_KEYS, ['benchmark_total_return_pct', 'excess_return_pct']);
});
