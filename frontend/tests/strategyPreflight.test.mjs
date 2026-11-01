// Pure coverage for the read-only template catalogue and preflight helpers.
// This is not page-interaction coverage; the tracked Playwright spec covers the
// browser flow separately.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PREFLIGHT_STATUS_COPY,
  buildDefaultSpec,
  formatConstraint,
  parseSpecText,
} from '../src/lib/strategyPreflight.ts';

test('default specs carry the parameters each template requires', () => {
  assert.deepEqual(buildDefaultSpec('buy_and_hold').signal_parameters, {});
  assert.deepEqual(buildDefaultSpec('sma_crossover').signal_parameters, {
    short_window: 10,
    long_window: 30,
  });
  assert.deepEqual(buildDefaultSpec('price_change_fixed_hold').signal_parameters, {
    lookback_days: 5,
    threshold_pct: 0.05,
    holding_days: 10,
  });
  assert.equal(buildDefaultSpec('sma_crossover').frequency, '1d');
});

test('default specs are copies, not shared references', () => {
  const first = buildDefaultSpec('sma_crossover');
  first.signal_parameters.short_window = 99;
  assert.equal(buildDefaultSpec('sma_crossover').signal_parameters.short_window, 10);
});

test('parseSpecText accepts a JSON object', () => {
  const result = parseSpecText('{"template_type":"buy_and_hold"}');
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.value.template_type, 'buy_and_hold');
});

test('parseSpecText rejects empty input, invalid JSON and non-objects', () => {
  assert.equal(parseSpecText('   ').ok, false);
  assert.equal(parseSpecText('{oops}').ok, false);
  assert.equal(parseSpecText('[1,2,3]').ok, false);
  assert.equal(parseSpecText('null').ok, false);
});

test('status copy never upgrades a contract match into a performance claim', () => {
  const valid = PREFLIGHT_STATUS_COPY.valid;
  assert.match(valid.label, /contract/i);
  assert.doesNotMatch(valid.label, /profitable|verified strategy|approved to trade/i);
  assert.match(valid.detail, /does not show/i);

  assert.match(PREFLIGHT_STATUS_COPY.invalid.detail, /Nothing has been created/i);
  assert.match(PREFLIGHT_STATUS_COPY.unsupported.detail, /cannot run/i);
});

test('formatConstraint prints the schema constraints', () => {
  assert.equal(formatConstraint({ name: 'x', type: 'string', required: true }), 'string');
  assert.equal(
    formatConstraint({
      name: 'frequency',
      type: 'string',
      required: false,
      constraints: { pattern: '^1d$' },
    }),
    'string (pattern ^1d$)',
  );
});
