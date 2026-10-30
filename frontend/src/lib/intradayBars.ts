/**
 * Pure helpers for the intraday REST lane.
 *
 * Everything here is deterministic and transport-free so the update rules can
 * be tested without a browser or a provider. The invariants they encode:
 *
 * - the chart never rebuilds while the user is zoomed in unless the window
 *   itself moved, and never jumps back to the right edge on its own;
 * - a response for a previous symbol/interval is never applied;
 * - no poll is scheduled while the tab is hidden or while the backend is in a
 *   backoff/quota window.
 */

export interface IntradayCandle {
  open_time: string;
  close_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  is_final: boolean;
}

export interface IntradayRefreshState {
  status: string;
  attempted_upstream: boolean;
  upstream_requests: number;
  rows_received: number;
  new_completed_bar: boolean;
  retry_after_seconds: number | null;
  error: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_attempt_ok?: boolean;
  last_success_added_bar?: boolean;
  refresh_seconds?: number;
  budget?: IntradayBudget;
}

export interface IntradayBudget {
  hour_used: number;
  day_used: number;
  hour_limit: number;
  day_limit: number;
  scope: string;
}

export interface IntradayMarketState {
  exchange_local_time: string;
  timezone: string;
  regular_session: string | null;
  session_status: string;
  holiday_calendar_complete: boolean;
  expected_last_open: string | null;
  holidays_configured: boolean;
}

export interface IntradayIntegrity {
  fingerprint: string;
  completed_count: number;
  duplicate_timestamps: string[];
  off_grid_timestamps: string[];
  invalid_timestamps: string[];
  gaps: { from: string; to: string }[];
  missing_intervals: number;
  out_of_order_rows: number;
  integrity_ok: boolean;
  last_completed_close_time: string | null;
  stale_seconds: number | null;
  stale_after_seconds: number;
  is_stale: boolean;
}

export interface IntradaySeriesPayload {
  instrument: {
    instrument_id: string;
    symbol: string;
    market: string;
    asset_type: string;
    exchange: string;
    currency: string;
    timezone: string;
    name: string;
  };
  provider_id: string;
  interval: string;
  interval_minutes: number;
  status: string;
  refresh: IntradayRefreshState;
  budget: IntradayBudget;
  market: IntradayMarketState;
  data_as_of: string | null;
  provider_error: string | null;
  data_nature: string;
  is_realtime: boolean;
  realtime_evidence: string;
  data_rights: string;
  data_rights_note_zh: string;
  series: {
    interval: string;
    interval_minutes: number;
    timestamp_semantics: string;
    fetched_at: string;
    completed: IntradayCandle[];
    forming: IntradayCandle | null;
    forming_excluded_from_strategy: boolean;
    integrity: IntradayIntegrity;
  };
  unavailability_policy: string;
}

export interface IntradayCapability {
  lane: string;
  enabled: boolean;
  provider_id: string;
  provider_name: string | null;
  provider_connected: boolean;
  provider_error: string | null;
  adapter_supports_intraday: boolean;
  declared_intervals: string[];
  verified_intervals: string[];
  default_interval: string;
  allowed_instruments: string[];
  refresh_seconds: number;
  output_size: number;
  data_nature: string;
  is_realtime: boolean;
  realtime_evidence: string;
  data_rights: string;
  market_calendar_complete: boolean;
  budget: IntradayBudget;
  min_refresh_seconds: number;
  provider_status?: Record<string, unknown>;
}

/** Chart timestamp: epoch seconds, or null for a value the chart cannot place. */
export function chartTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return null;
  return Math.floor(ms / 1000);
}

/** Closed bars followed by the forming bar, ascending and de-duplicated. */
export function candlesForChart(payload: IntradaySeriesPayload): IntradayCandle[] {
  const rows = [...payload.series.completed];
  if (payload.series.forming) rows.push(payload.series.forming);
  const byTime = new Map<number, IntradayCandle>();
  for (const row of rows) {
    const time = chartTime(row.open_time);
    if (time === null) continue;
    byTime.set(time, row);
  }
  return Array.from(byTime.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, row]) => row);
}

export type ChartUpdatePlan =
  | { mode: 'reset'; times: number[] }
  | { mode: 'incremental'; times: number[] };

/**
 * Decide whether the next payload can be applied with `series.update()` or has
 * to go through `setData()`.
 *
 * `series.update()` can only replace the newest bar or append a newer one, so
 * anything that changes the window start (a sliding history window, a new
 * symbol or interval) needs a full setData. The chart captures and restores the
 * visible range around that call, so a reset does not yank the viewport.
 */
export function planChartUpdate(
  previousTimes: number[],
  next: IntradayCandle[],
  identityChanged: boolean,
): ChartUpdatePlan {
  const times = next
    .map((row) => chartTime(row.open_time))
    .filter((time): time is number => time !== null);
  const unique = Array.from(new Set(times)).sort((a, b) => a - b);

  if (identityChanged || previousTimes.length === 0 || unique.length === 0) {
    return { mode: 'reset', times: unique };
  }
  const previousLast = previousTimes[previousTimes.length - 1];
  if (unique[0] !== previousTimes[0]) {
    // The oldest bar changed: the server window moved, so incremental updates
    // would leave a stale bar behind at the left edge.
    return { mode: 'reset', times: unique };
  }
  if (unique.length < previousTimes.length) {
    return { mode: 'reset', times: unique };
  }
  const tail = unique.filter((time) => time >= previousLast);
  return { mode: 'incremental', times: tail };
}

export interface StatusView {
  tone: 'ok' | 'warn' | 'bad';
  label: string;
  detail: string;
}

const STATUS_VIEWS: Record<string, StatusView> = {
  disabled: {
    tone: 'warn',
    label: 'Not enabled',
    detail: 'The intraday lane is disabled on the server; no upstream requests are sent.',
  },
  not_configured: {
    tone: 'bad',
    label: 'Provider not configured',
    detail: 'No usable market-data provider is configured on the backend (for example, a missing API key).',
  },
  provider_not_intraday_capable: {
    tone: 'bad',
    label: 'Provider has no intraday access',
    detail: 'The current adapter has no intraday request path.',
  },
  interval_not_declared: {
    tone: 'warn',
    label: 'Interval not declared',
    detail: 'The provider adapter does not declare this interval as requestable.',
  },
  unverified_interval: {
    tone: 'warn',
    label: 'Interval not verified',
    detail: 'Adapter support does not mean your account can retrieve this interval; verify it with a real account first.',
  },
  backoff: {
    tone: 'warn',
    label: 'Backing off',
    detail: 'Retrying after a previous failure; no upstream request is sent until the backoff ends.',
  },
  rate_limited: {
    tone: 'warn',
    label: 'Provider rate limit',
    detail: 'A 429 was returned; backing off as the provider instructs.',
  },
  budget_exhausted: {
    tone: 'warn',
    label: 'Local budget reached',
    detail: 'The process request budget is exhausted; this is a local guard, not the provider quota.',
  },
  provider_unavailable: {
    tone: 'bad',
    label: 'Provider unavailable',
    detail: 'The provider is currently unavailable; only stored data is shown.',
  },
  symbol_not_mapped: {
    tone: 'bad',
    label: 'Symbol not mapped',
    detail: 'This symbol has no provider mapping; no upstream request was sent.',
  },
  upstream_error: {
    tone: 'bad',
    label: 'Provider error',
    detail: 'The upstream request failed; only stored data is shown.',
  },
  cache_fresh: {
    tone: 'ok',
    label: 'Cached (no new request)',
    detail: 'Less than the minimum refresh interval has passed since the last successful request, so no upstream request was sent.',
  },
  new_bar: {
    tone: 'ok',
    label: 'New bar received',
    detail: 'The latest completed bar is newer than the stored one.',
  },
  updated: {
    tone: 'ok',
    label: 'Updated',
    detail: 'This request updated an existing forming or completed bar.',
  },
  no_change: {
    tone: 'ok',
    label: 'No new data',
    detail: 'The provider returned the same bars that are already stored.',
  },
  no_data: {
    tone: 'warn',
    label: 'Provider returned no data',
    detail: 'The provider returned an empty result; it was not replaced with synthetic data.',
  },
  read_only: {
    tone: 'ok',
    label: 'Read only (no upstream request)',
    detail: 'This request read stored data only.',
  },
  idle: {
    tone: 'warn',
    label: 'Not requested yet',
    detail: 'This process has not requested this series from the provider.',
  },
};

export function describeStatus(status: string): StatusView {
  return (
    STATUS_VIEWS[status] ?? {
      tone: 'warn',
      label: status,
      detail: 'Unknown status; not treated as success.',
    }
  );
}

/** Human label for the exchange-local session state, incl. unknown calendars. */
export function describeSession(status: string): string {
  switch (status) {
    case 'open':
      return 'Open';
    case 'closed':
      return 'Closed';
    case 'closed_weekend':
      return 'Closed (weekend)';
    case 'closed_holiday':
      return 'Closed (holiday)';
    case 'unknown_holiday_calendar':
      return 'Session unknown (no holiday calendar)';
    default:
      return `Unknown status (${status})`;
  }
}

/**
 * Whether continued polling can change anything. Configuration-class refusals
 * (disabled, missing key, unverified interval, unmapped symbol, exhausted
 * process budget) are frozen until the operator changes the backend config and
 * the user asks to re-check.
 */
export function isPollingAllowed(payload: {
  status: string;
  refresh: { retry_after_seconds: number | null };
}): boolean {
  return ![
    'disabled',
    'not_configured',
    'provider_not_intraday_capable',
    'interval_not_declared',
    'unverified_interval',
    'symbol_not_mapped',
    'budget_exhausted',
  ].includes(payload.status);
}

/**
 * Delay until the next read. Null means "do not schedule".
 *
 * The browser only reads the backend; the backend decides whether that read
 * reaches the provider. Polling faster than the server's minimum refresh would
 * just add load without new data.
 */
export function nextPollDelayMs(input: {
  hidden: boolean;
  paused: boolean;
  refreshSeconds: number;
  status: string;
  retryAfterSeconds: number | null;
}): number | null {
  if (input.hidden || input.paused) return null;
  const base = Math.max(5, Math.floor(input.refreshSeconds || 60)) * 1000;
  if (
    (input.status === 'backoff' || input.status === 'rate_limited') &&
    input.retryAfterSeconds !== null
  ) {
    const wait = Math.max(5, Math.min(input.retryAfterSeconds, 300)) * 1000;
    return Math.max(base, wait);
  }
  if (input.status === 'budget_exhausted') return null;
  return base;
}

/** A response applied only if it still belongs to the active series. */
export function isCurrentSeries(
  responseKey: string,
  activeKey: string,
  requestId: number,
  activeRequestId: number,
): boolean {
  return responseKey === activeKey && requestId === activeRequestId;
}

export function seriesKey(instrumentId: string, interval: string): string {
  return `${instrumentId}|${interval}`;
}

/** Prefer the server's data time; never present the request time as data time. */
export function dataAsOfLabel(payload: IntradaySeriesPayload): string {
  if (!payload.data_as_of) return '— (no completed bars yet)';
  return payload.data_as_of;
}

/** Gaps and duplicates are reported, not silently repaired. */
export function integritySummary(integrity: IntradayIntegrity): string {
  if (integrity.integrity_ok) return 'Series complete';
  const parts: string[] = [];
  if (integrity.missing_intervals > 0) parts.push(`missing ${integrity.missing_intervals}`);
  if (integrity.duplicate_timestamps.length > 0)
    parts.push(`duplicate ${integrity.duplicate_timestamps.length}`);
  if (integrity.off_grid_timestamps.length > 0)
    parts.push(`off-grid ${integrity.off_grid_timestamps.length}`);
  if (integrity.invalid_timestamps.length > 0)
    parts.push(`invalid ${integrity.invalid_timestamps.length}`);
  return parts.length > 0 ? parts.join(', ') : 'Series has issues';
}
