import { test, expect } from '@playwright/test';
import {
  MAX_BACKOFF_MS,
  MIN_POLL_SECONDS,
  candlesForCryptoChart,
  cryptoDataAsOfLabel,
  cryptoIntegritySummary,
  cryptoSeriesKey,
  describeConnection,
  describeMode,
  describeVenueRefusal,
  detectNewCompletedBar,
  isCurrentCryptoSeries,
  latestPrice,
  nextPollDelayMs,
  retentionSummary,
  type CryptoCandle,
  type CryptoInstrument,
  type CryptoIntegrity,
  type CryptoSeriesPayload,
} from '../src/lib/cryptoBars';

// Offline contract for the Kraken read-only lane. No browser, no network:
// these are the rules the crypto page must not improvise at runtime.

const INSTRUMENT: CryptoInstrument = {
  instrument_id: 'CRYPTO:KRAKEN:BTC-USD',
  exchange: 'kraken',
  market: 'CRYPTO',
  asset_type: 'CRYPTO',
  pair: 'BTC/USD',
  base_asset: 'BTC',
  quote_asset: 'USD',
  currency: 'USD',
  timezone: 'UTC',
  price_precision: 2,
  quantity_precision: 8,
  min_quantity: '0.0001',
  min_notional: null,
  name: 'Bitcoin / US Dollar',
  read_only: true,
  orders_supported: false,
};

const INTEGRITY: CryptoIntegrity = {
  fingerprint: 'f'.repeat(64),
  completed_count: 0,
  duplicate_timestamps: [],
  off_grid_timestamps: [],
  invalid_timestamps: [],
  gaps: [],
  missing_intervals: 0,
  out_of_order_rows: 0,
  integrity_ok: true,
  last_completed_close_time: null,
  stale_seconds: null,
  stale_after_seconds: 120,
  is_stale: false,
};

function iso(minute: number, second = '00'): string {
  const stamp = new Date(Date.UTC(2026, 9, 31, 9, minute, Number(second)));
  return stamp.toISOString();
}

function candle(
  minute: number,
  overrides: Partial<CryptoCandle> = {},
): CryptoCandle {
  const openTime = iso(minute);
  const closeTime = new Date(Date.parse(openTime) + 60_000).toISOString();
  return {
    open_time: openTime,
    close_time: closeTime,
    open: '64000.00',
    high: '64100.00',
    low: '63900.00',
    close: '64050.00',
    volume: '0.12345678',
    trade_count: 42,
    is_final: true,
    ...overrides,
  };
}

function payload(
  overrides: Partial<CryptoSeriesPayload> = {},
): CryptoSeriesPayload {
  const completed = overrides.completed ?? [candle(1), candle(2)];
  const base: CryptoSeriesPayload = {
    instrument: INSTRUMENT,
    interval: '1m',
    interval_minutes: 1,
    mode: 'live_realtime',
    mode_label_zh: '即時',
    timestamp_semantics: 'open_time is the UTC candle open; fetch time is separate.',
    fetched_at: '2026-10-31T09:03:10.000Z',
    data_as_of: completed.length > 0 ? completed[completed.length - 1].close_time : null,
    completed,
    forming: null,
    forming_excluded_from_strategy: true,
    integrity: { ...INTEGRITY, completed_count: completed.length },
    provenance: {
      provider_id: 'kraken',
      provider_name: 'Kraken (public REST)',
      feed_mode: 'live',
      data_nature: 'live',
      read_only: true,
      redistribution: 'NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION',
      terms_note_zh: '僅供內部研究，未取得對外再分發授權。',
    },
    retention: {
      max_bars_per_request: 720,
      returned_completed_count: completed.length,
      oldest_returned_open_time: completed[0]?.open_time ?? null,
      older_history_retrievable: false,
      note: 'Kraken returns only the most recent candles; older history is not retrievable.',
      note_zh: '僅返回最近 K 線，無法取得更早歷史。',
    },
    refresh: {
      suggested_seconds: 15,
      minimum_seconds: 15,
      note: 'Read again after the next candle closes.',
    },
    unavailability_policy: 'No substitute prices are produced.',
  };
  return { ...base, ...overrides, completed };
}

test.describe('crypto candles: forming vs completed', () => {
  test('completed bars chart unchanged and the forming candle is appended last', () => {
    const completed = [candle(1), candle(2)];
    const forming = candle(3, { is_final: false });
    const rows = candlesForCryptoChart(payload({ completed, forming }));

    expect(rows.map((r) => r.open_time)).toEqual([iso(1), iso(2), iso(3)]);
    expect(rows[2].is_final).toBe(false);
    expect(completed).toHaveLength(2);
  });

  test('a forming update for an existing timestamp never appends a duplicate', () => {
    const completed = [candle(1), candle(2)];
    const forming = candle(2, { is_final: false, close: '64099.00' });
    const rows = candlesForCryptoChart(payload({ completed, forming }));

    expect(rows).toHaveLength(2);
    expect(rows[1].is_final).toBe(false);
    expect(rows[1].close).toBe('64099.00');
    expect(completed[1].is_final).toBe(true);
  });

  test('out-of-order and duplicate completed rows are ordered and collapsed', () => {
    const rows = candlesForCryptoChart(
      payload({ completed: [candle(3), candle(1), candle(2), candle(1)] }),
    );
    expect(rows.map((r) => r.open_time)).toEqual([iso(1), iso(2), iso(3)]);
  });

  test('no completed bars and no forming bar yields an empty series', () => {
    expect(candlesForCryptoChart(payload({ completed: [], forming: null }))).toEqual([]);
  });
});

test.describe('crypto new-bar detection', () => {
  test('the first response is a baseline, never a new bar', () => {
    const result = detectNewCompletedBar(null, payload());
    expect(result).toEqual({ isNew: false, open_time: null, added: 0, heartbeatOnly: false });
  });

  test('a moved forming candle alone is a heartbeat', () => {
    const previous = [iso(1), iso(2)];
    const result = detectNewCompletedBar(
      previous,
      payload({ completed: [candle(1), candle(2)], forming: candle(3, { is_final: false }) }),
    );
    expect(result.isNew).toBe(false);
    expect(result.added).toBe(0);
    expect(result.heartbeatOnly).toBe(true);
  });

  test('a completed candle that was not shown before is a new bar', () => {
    const previous = [iso(1), iso(2)];
    const result = detectNewCompletedBar(
      previous,
      payload({ completed: [candle(1), candle(2), candle(3)] }),
    );
    expect(result.isNew).toBe(true);
    expect(result.added).toBe(1);
    expect(result.open_time).toBe(iso(3));
  });

  test('a slower client that missed bars reports the newest added one and the count', () => {
    const previous = [iso(1)];
    const result = detectNewCompletedBar(
      previous,
      payload({ completed: [candle(1), candle(2), candle(3)] }),
    );
    expect(result.isNew).toBe(true);
    expect(result.added).toBe(2);
    expect(result.open_time).toBe(iso(3));
  });

  test('an identical response with no forming candle is not a heartbeat either', () => {
    const result = detectNewCompletedBar([iso(1), iso(2)], payload());
    expect(result.heartbeatOnly).toBe(false);
    expect(result.isNew).toBe(false);
  });
});

test.describe('crypto series identity', () => {
  test('pair and interval are both part of the series identity', () => {
    expect(cryptoSeriesKey('CRYPTO:KRAKEN:BTC-USD', '1m')).not.toBe(
      cryptoSeriesKey('CRYPTO:KRAKEN:ETH-USD', '1m'),
    );
    expect(cryptoSeriesKey('CRYPTO:KRAKEN:BTC-USD', '1m')).not.toBe(
      cryptoSeriesKey('CRYPTO:KRAKEN:BTC-USD', '5m'),
    );
  });

  test('a response for a previous pair or a superseded request is rejected', () => {
    const activeKey = cryptoSeriesKey('CRYPTO:KRAKEN:BTC-USD', '5m');
    const oldKey = cryptoSeriesKey('CRYPTO:KRAKEN:BTC-USD', '1m');

    expect(isCurrentCryptoSeries(oldKey, activeKey, 4, 4)).toBe(false);
    expect(isCurrentCryptoSeries(activeKey, activeKey, 3, 4)).toBe(false);
    expect(isCurrentCryptoSeries(activeKey, activeKey, 4, 4)).toBe(true);
  });
});

test.describe('crypto bounded polling', () => {
  const fixedRandom = () => 0.5;

  test('hidden or paused schedules nothing', () => {
    expect(
      nextPollDelayMs({ hidden: true, paused: false, suggestedSeconds: 15, failureCount: 0 }),
    ).toBeNull();
    expect(
      nextPollDelayMs({ hidden: false, paused: true, suggestedSeconds: 15, failureCount: 0 }),
    ).toBeNull();
  });

  test('the server hint is the floor and jitter stays within 20 percent', () => {
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 15,
        failureCount: 0,
        random: fixedRandom,
      }),
    ).toBe(16_500);
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 0,
        failureCount: 0,
        random: () => 0,
      }),
    ).toBe(MIN_POLL_SECONDS * 1000);
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 5,
        minimumSeconds: 60,
        failureCount: 0,
        random: () => 0,
      }),
    ).toBe(60_000);
  });

  test('failures back off exponentially and stop at the cap', () => {
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 15,
        failureCount: 1,
        random: () => 0,
      }),
    ).toBe(30_000);
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 15,
        failureCount: 3,
        random: () => 0,
      }),
    ).toBe(MAX_BACKOFF_MS);
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 15,
        failureCount: 9,
        random: () => 0.999,
      }),
    ).toBe(MAX_BACKOFF_MS);
  });

  test('a venue retry hint can lengthen but never shorten the wait', () => {
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 15,
        failureCount: 0,
        retryAfterSeconds: 120,
        random: () => 0,
      }),
    ).toBe(120_000);
    expect(
      nextPollDelayMs({
        hidden: false,
        paused: false,
        suggestedSeconds: 120,
        failureCount: 0,
        retryAfterSeconds: 15,
        random: () => 0,
      }),
    ).toBe(120_000);
  });
});

test.describe('crypto retention and data time', () => {
  test('retention states the 720-bar window and that older history is unavailable', () => {
    const summary = retentionSummary(payload().retention);
    expect(summary).toContain('720');
    expect(summary).toContain('older history not retrievable');
  });

  test('retention is honest when only a partial window was returned', () => {
    const summary = retentionSummary({
      ...payload().retention,
      returned_completed_count: 3,
      oldest_returned_open_time: iso(1),
    });
    expect(summary).toContain(iso(1));
  });

  test('unknown retention is labelled, not guessed', () => {
    expect(retentionSummary(null)).toBe('Retention limit unknown');
  });

  test('data as of comes from the server, never from the fetch time', () => {
    const withData = payload();
    expect(cryptoDataAsOfLabel(withData)).toBe(withData.data_as_of);
    expect(cryptoDataAsOfLabel(withData)).not.toBe(withData.fetched_at);
    expect(cryptoDataAsOfLabel(payload({ completed: [], data_as_of: null }))).toBe(
      '— (no completed candles yet)',
    );
  });
});

test.describe('crypto price and volume precision', () => {
  test('decimal strings survive chart conversion without float rounding', () => {
    const rows = candlesForCryptoChart(
      payload({
        completed: [
          candle(1, { close: '64210.10', volume: '0.00012345' }),
          candle(2, { close: '64210.20', volume: null }),
        ],
      }),
    );
    expect(rows[0].close).toBe('64210.10');
    expect(rows[0].volume).toBe('0.00012345');
    expect(rows[1].volume).toBeNull();
  });

  test('the quote asset stays the venue USD pair, not a substitute stablecoin', () => {
    const series = payload();
    expect(series.instrument.pair).toBe('BTC/USD');
    expect(series.instrument.quote_asset).toBe('USD');
  });

  test('the headline prefers a completed close and labels a forming-only price', () => {
    const completed = payload();
    expect(latestPrice(completed)).toEqual({
      value: '64050.00',
      source: 'completed',
      open_time: iso(2),
    });

    const formingOnly = latestPrice(
      payload({ completed: [], forming: candle(3, { is_final: false, close: '64077.00' }) }),
    );
    expect(formingOnly.source).toBe('forming');
    expect(formingOnly.value).toBe('64077.00');

    expect(latestPrice(null)).toEqual({ value: null, source: 'none', open_time: null });
  });
});

test.describe('crypto connection and refusal copy', () => {
  test('every connection state has a distinct label', () => {
    const states = [
      'idle',
      'connecting',
      'connected',
      'reconnecting',
      'stale',
      'paused',
      'unavailable',
    ] as const;
    const labels = states.map((state) => describeConnection(state).label);
    expect(new Set(labels).size).toBe(states.length);
    expect(describeConnection('paused').detail).toContain('chart stays exactly as it was');
  });

  test('modes map to their provenance without upgrading stale data', () => {
    expect(describeMode('live_realtime').tone).toBe('ok');
    expect(describeMode('stale').label).toContain('stale');
    expect(describeMode('disconnected').tone).toBe('bad');
  });

  test('venue refusals never claim substitute prices', () => {
    expect(describeVenueRefusal(429)).toContain('rate limiting');
    expect(describeVenueRefusal(null, 'venue_unreachable')).toContain('last received candles only');
    expect(describeVenueRefusal(null, 'venue_rejected_request')).toContain('No substitute prices');
    expect(describeVenueRefusal(500)).toContain('No substitute prices');
  });

  test('gaps and duplicates are reported, not repaired', () => {
    expect(cryptoIntegritySummary(INTEGRITY)).toBe('Series complete');
    const broken = cryptoIntegritySummary({
      ...INTEGRITY,
      integrity_ok: false,
      missing_intervals: 2,
      duplicate_timestamps: [iso(1)],
      gaps: [{ from: iso(1), to: iso(3) }],
    });
    expect(broken).toContain('missing 2');
    expect(broken).toContain('duplicate 1');
  });
});
