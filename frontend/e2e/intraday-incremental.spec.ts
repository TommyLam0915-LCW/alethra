import { test, expect } from '@playwright/test';
import {
  candlesForChart,
  chartTime,
  dataAsOfLabel,
  describeSession,
  describeStatus,
  integritySummary,
  isCurrentSeries,
  isPollingAllowed,
  nextPollDelayMs,
  planChartUpdate,
  seriesKey,
  type IntradayCandle,
  type IntradaySeriesPayload,
} from '../src/lib/intradayBars';

// Pure helpers for incremental intraday updates. No server, auth or browser is
// involved: these assertions are about the update rules themselves.

function candle(minute: number, overrides: Partial<IntradayCandle> = {}): IntradayCandle {
  const open = `2026-10-01T14:${String(minute).padStart(2, '0')}:00+00:00`;
  const close = `2026-10-01T14:${String(minute + 1).padStart(2, '0')}:00+00:00`;
  return {
    open_time: open,
    close_time: close,
    open: '100',
    high: '101',
    low: '99',
    close: '100.5',
    volume: '1000',
    is_final: true,
    ...overrides,
  };
}

function payload(completed: IntradayCandle[], forming: IntradayCandle | null = null): IntradaySeriesPayload {
  return {
    instrument: {
      instrument_id: 'US:AAPL',
      symbol: 'AAPL',
      market: 'US',
      asset_type: 'stock',
      exchange: 'NASDAQ',
      currency: 'USD',
      timezone: 'America/New_York',
      name: 'Apple',
    },
    provider_id: 'twelvedata',
    interval: '1min',
    interval_minutes: 1,
    status: 'updated',
    refresh: {
      status: 'updated',
      attempted_upstream: true,
      upstream_requests: 1,
      rows_received: completed.length,
      new_completed_bar: false,
      retry_after_seconds: null,
      error: null,
      last_attempt_at: '2026-10-01T14:06:00+00:00',
      last_success_at: '2026-10-01T14:06:00+00:00',
      last_success_added_bar: false,
      refresh_seconds: 60,
    },
    budget: { hour_used: 1, day_used: 1, hour_limit: 60, day_limit: 400, scope: 'process' },
    market: {
      exchange_local_time: '2026-10-01T10:06:00-04:00',
      timezone: 'America/New_York',
      regular_session: 'regular',
      session_status: 'open',
      holiday_calendar_complete: false,
      expected_last_open: '2026-10-01T14:05:00',
      holidays_configured: false,
    },
    data_as_of: '2026-10-01T14:06:00+00:00',
    provider_error: null,
    data_nature: 'unknown',
    is_realtime: false,
    realtime_evidence: 'none_verified',
    data_rights: 'INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION',
    data_rights_note_zh: '內部測試',
    series: {
      interval: '1min',
      interval_minutes: 1,
      timestamp_semantics: 'UTC open time',
      fetched_at: '2026-10-01T14:06:00+00:00',
      completed,
      forming,
      forming_excluded_from_strategy: true,
      integrity: {
        fingerprint: 'f',
        completed_count: completed.length,
        duplicate_timestamps: [],
        off_grid_timestamps: [],
        invalid_timestamps: [],
        gaps: [],
        missing_intervals: 0,
        out_of_order_rows: 0,
        integrity_ok: true,
        last_completed_close_time: '2026-10-01T14:06:00+00:00',
        stale_seconds: 0,
        stale_after_seconds: 180,
        is_stale: false,
      },
    },
    unavailability_policy: 'no synthesis',
  };
}

test.describe('intraday bar ordering and merging', () => {
  test('deduplicates duplicate timestamps keeping the last row and sorts ascending', () => {
    const rows = candlesForChart(
      payload([
        candle(3),
        { ...candle(2), close: '101', is_final: true },
        candle(2),
        candle(1),
      ]),
    );
    expect(rows.map((r) => chartTime(r.open_time))).toEqual([
      chartTime(candle(1).open_time),
      chartTime(candle(2).open_time),
      chartTime(candle(3).open_time),
    ]);
    expect(rows[1].close).toBe('100.5');
  });

  test('ignores rows with an unparseable timestamp instead of placing them at epoch 0', () => {
    const rows = candlesForChart(
      payload([{ ...candle(1), open_time: 'not-a-time' }, candle(2)]),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].open_time).toBe(candle(2).open_time);
  });

  test('the forming bar is appended after completed bars but stays labelled non-final', () => {
    const rows = candlesForChart(payload([candle(1), candle(2)], candle(3, { is_final: false })));
    expect(rows.map((r) => r.is_final)).toEqual([true, true, false]);
  });
});

test.describe('incremental chart update planning', () => {
  test('reuses update() when the same bar is revised and a new bar appended', () => {
    const previous = [chartTime(candle(1).open_time)!, chartTime(candle(2).open_time)!];
    const plan = planChartUpdate(previous, [candle(1), candle(2), candle(3)], false);
    expect(plan.mode).toBe('incremental');
    expect(plan.times).toEqual([chartTime(candle(2).open_time), chartTime(candle(3).open_time)]);
  });

  test('falls back to setData when the history window slides', () => {
    const previous = [chartTime(candle(1).open_time)!, chartTime(candle(2).open_time)!];
    const plan = planChartUpdate(previous, [candle(2), candle(3)], false);
    expect(plan.mode).toBe('reset');
  });

  test('identity change always resets, so a new symbol cannot inherit old candles', () => {
    const previous = [chartTime(candle(1).open_time)!];
    const plan = planChartUpdate(previous, [candle(5)], true);
    expect(plan.mode).toBe('reset');
    expect(plan.times).toEqual([chartTime(candle(5).open_time)]);
  });

  test('empty payload clears the chart', () => {
    const plan = planChartUpdate([1, 2], [], false);
    expect(plan).toEqual({ mode: 'reset', times: [] });
  });
});

test.describe('request identity and polling bounds', () => {
  test('a response for a previous symbol/interval or request is rejected', () => {
    const key = seriesKey('US:AAPL', '1min');
    expect(isCurrentSeries(key, key, 4, 4)).toBe(true);
    expect(isCurrentSeries(seriesKey('US:MSFT', '1min'), key, 4, 4)).toBe(false);
    expect(isCurrentSeries(key, key, 3, 4)).toBe(false);
  });

  test('hidden tab or paused user schedules nothing', () => {
    const base = { refreshSeconds: 60, status: 'updated', retryAfterSeconds: null };
    expect(nextPollDelayMs({ ...base, hidden: true, paused: false })).toBeNull();
    expect(nextPollDelayMs({ ...base, hidden: false, paused: true })).toBeNull();
    expect(nextPollDelayMs({ ...base, hidden: false, paused: false })).toBe(60000);
  });

  test('a provider retry hint extends the next read instead of hammering', () => {
    const delay = nextPollDelayMs({
      hidden: false,
      paused: false,
      refreshSeconds: 60,
      status: 'rate_limited',
      retryAfterSeconds: 120,
    });
    expect(delay).toBe(120000);
  });

  test('no tight retry when the retry hint is missing or below the floor', () => {
    const delay = nextPollDelayMs({
      hidden: false,
      paused: false,
      refreshSeconds: 60,
      status: 'backoff',
      retryAfterSeconds: 1,
    });
    expect(delay).toBe(60000);
  });

  test('configuration refusals and exhausted budget stop polling until re-check', () => {
    for (const status of [
      'disabled',
      'not_configured',
      'provider_not_intraday_capable',
      'interval_not_declared',
      'unverified_interval',
      'symbol_not_mapped',
      'budget_exhausted',
    ]) {
      expect(isPollingAllowed({ status, refresh: { retry_after_seconds: null } })).toBe(false);
    }
    expect(isPollingAllowed({ status: 'updated', refresh: { retry_after_seconds: null } })).toBe(true);
    expect(
      isPollingAllowed({ status: 'rate_limited', refresh: { retry_after_seconds: 120 } }),
    ).toBe(true);
    expect(nextPollDelayMs({
      hidden: false,
      paused: false,
      refreshSeconds: 60,
      status: 'budget_exhausted',
      retryAfterSeconds: null,
    })).toBeNull();
  });
});

test.describe('honest status labels', () => {
  test('data time is never replaced by fetch time', () => {
    const p = payload([candle(1)]);
    expect(dataAsOfLabel(p)).toBe('2026-10-01T14:06:00+00:00');
    expect(p.refresh.last_attempt_at).toBe('2026-10-01T14:06:00+00:00');
    expect(dataAsOfLabel({ ...p, data_as_of: null })).toContain('尚無已收盤');
  });

  test('unverified interval is reported as a permission gap, not a success', () => {
    const view = describeStatus('unverified_interval');
    expect(view.tone).toBe('warn');
    expect(view.detailZh).toContain('實際帳戶驗證');
    expect(describeStatus('cache_fresh').labelZh).toContain('未重新請求');
  });

  test('unknown holiday calendar is surfaced instead of guessing open/closed', () => {
    expect(describeSession('unknown_holiday_calendar')).toBe('休市狀態未知（無假日曆）');
    expect(describeSession('closed_holiday')).toBe('假日休市');
    expect(describeSession('closed_weekend')).toBe('週末休市');
  });

  test('integrity gaps are reported, not silently repaired', () => {
    const p = payload([candle(1), candle(2)]);
    const summary = integritySummary({
      ...p.series.integrity,
      integrity_ok: false,
      missing_intervals: 2,
      duplicate_timestamps: ['2026-10-01T14:03:00+00:00'],
    });
    expect(summary).toContain('缺 2 根');
    expect(summary).toContain('重複 1 個');
  });
});
