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
  labelZh: string;
  detailZh: string;
}

const STATUS_VIEWS: Record<string, StatusView> = {
  disabled: {
    tone: 'warn',
    labelZh: '未啟用',
    detailZh: '伺服器未啟用盤中行情通道；不會發出任何上游請求。',
  },
  not_configured: {
    tone: 'bad',
    labelZh: '未設定供應商',
    detailZh: '後端沒有可用的行情供應商設定（例如缺少 API 金鑰）。',
  },
  provider_not_intraday_capable: {
    tone: 'bad',
    labelZh: '供應商不支援盤中',
    detailZh: '目前的 adapter 沒有盤中請求路徑。',
  },
  interval_not_declared: {
    tone: 'warn',
    labelZh: '此週期未宣告',
    detailZh: '供應商 adapter 未宣告此週期可請求。',
  },
  unverified_interval: {
    tone: 'warn',
    labelZh: '此週期未經驗證',
    detailZh: 'adapter 支援不等於你的帳戶可取得此週期；需先以實際帳戶驗證。',
  },
  backoff: {
    tone: 'warn',
    labelZh: '暫停中（退避）',
    detailZh: '上一次失敗後正在退避，時間到之前不會再請求上游。',
  },
  rate_limited: {
    tone: 'warn',
    labelZh: '供應商限流',
    detailZh: '已收到 429，正依照供應商指示退避。',
  },
  budget_exhausted: {
    tone: 'warn',
    labelZh: '已達本機預算',
    detailZh: '此程序的本機請求預算已用完；這是本機保護，不代表供應商配額。',
  },
  provider_unavailable: {
    tone: 'bad',
    labelZh: '供應商未連線',
    detailZh: '供應商目前不可用，僅顯示已儲存資料。',
  },
  symbol_not_mapped: {
    tone: 'bad',
    labelZh: '標的未對應',
    detailZh: '此標的沒有供應商代號對應，未送出任何上游請求。',
  },
  upstream_error: {
    tone: 'bad',
    labelZh: '供應商錯誤',
    detailZh: '上游請求失敗，僅顯示已儲存資料。',
  },
  cache_fresh: {
    tone: 'ok',
    labelZh: '快取內（未重新請求）',
    detailZh: '距離上次成功請求未滿最短更新間隔，因此未再請求上游。',
  },
  new_bar: {
    tone: 'ok',
    labelZh: '已收到新 K 線',
    detailZh: '最新已收盤 K 線比先前儲存的更新。',
  },
  updated: {
    tone: 'ok',
    labelZh: '已更新',
    detailZh: '本次請求更新了既有的未完成或已收盤 K 線。',
  },
  no_change: {
    tone: 'ok',
    labelZh: '無新資料',
    detailZh: '供應商回傳的 K 線與已儲存者相同。',
  },
  no_data: {
    tone: 'warn',
    labelZh: '供應商無資料',
    detailZh: '供應商回傳空結果；未以合成資料替代。',
  },
  read_only: {
    tone: 'ok',
    labelZh: '只讀取（未請求上游）',
    detailZh: '此請求只讀取已儲存資料。',
  },
  idle: {
    tone: 'warn',
    labelZh: '尚未請求',
    detailZh: '此程序尚未對此系列發出上游請求。',
  },
};

export function describeStatus(status: string): StatusView {
  return (
    STATUS_VIEWS[status] ?? {
      tone: 'warn',
      labelZh: status,
      detailZh: '未知狀態；不以成功視之。',
    }
  );
}

/** Human label for the exchange-local session state, incl. unknown calendars. */
export function describeSession(status: string): string {
  switch (status) {
    case 'open':
      return '開市中';
    case 'closed':
      return '已收市';
    case 'closed_weekend':
      return '週末休市';
    case 'closed_holiday':
      return '假日休市';
    case 'unknown_holiday_calendar':
      return '休市狀態未知（無假日曆）';
    default:
      return `狀態未知（${status}）`;
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
  if (!payload.data_as_of) return '—（尚無已收盤 K 線）';
  return payload.data_as_of;
}

/** Gaps and duplicates are reported, not silently repaired. */
export function integritySummary(integrity: IntradayIntegrity): string {
  if (integrity.integrity_ok) return '序列完整';
  const parts: string[] = [];
  if (integrity.missing_intervals > 0) parts.push(`缺 ${integrity.missing_intervals} 根`);
  if (integrity.duplicate_timestamps.length > 0)
    parts.push(`重複 ${integrity.duplicate_timestamps.length} 個`);
  if (integrity.off_grid_timestamps.length > 0)
    parts.push(`非網格 ${integrity.off_grid_timestamps.length} 個`);
  if (integrity.invalid_timestamps.length > 0)
    parts.push(`無效 ${integrity.invalid_timestamps.length} 個`);
  return parts.length > 0 ? parts.join('、') : '序列有異常';
}
