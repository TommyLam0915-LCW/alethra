/**
 * Pure helpers for the Kraken read-only crypto lane.
 *
 * These encode the rules the page must not improvise at runtime:
 *
 * - a forming candle is display-only and is never merged into completed bars;
 * - a repeated read with the same candles is a heartbeat, not a new trade;
 * - a completed candle is only appended once its close_time has passed;
 * - polling is bounded (hidden tab / user pause / failure backoff / venue hint);
 * - a response for a previous instrument or interval is rejected.
 *
 * No transport here, so the rules can be tested without a browser or a venue.
 */

import {
  chartTime,
  integritySummary,
  isCurrentSeries,
  seriesKey,
  type IntradayIntegrity,
} from './intradayBars';

export interface CryptoCandle {
  open_time: string;
  close_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string | null;
  trade_count: number | null;
  is_final: boolean;
}

export type CryptoIntegrity = IntradayIntegrity;

export interface CryptoInstrument {
  instrument_id: string;
  exchange: string;
  market: string;
  asset_type: string;
  pair: string;
  base_asset: string;
  quote_asset: string;
  currency: string;
  timezone: string;
  price_precision: number;
  quantity_precision: number;
  min_quantity: string | null;
  min_notional: string | null;
  name: string;
  read_only: boolean;
  orders_supported: boolean;
}

export interface CryptoRetention {
  max_bars_per_request: number;
  returned_completed_count: number;
  oldest_returned_open_time: string | null;
  older_history_retrievable: boolean;
  note: string;
  note_zh: string;
}

export interface CryptoRefreshHint {
  suggested_seconds: number;
  minimum_seconds: number;
  note: string;
}

export interface CryptoSeriesPayload {
  instrument: CryptoInstrument;
  interval: string | null;
  interval_minutes: number;
  mode: 'live_realtime' | 'stale' | 'disconnected';
  mode_label_zh: string;
  timestamp_semantics: string;
  fetched_at: string;
  data_as_of: string | null;
  completed: CryptoCandle[];
  forming: CryptoCandle | null;
  forming_excluded_from_strategy: boolean;
  integrity: CryptoIntegrity;
  provenance: {
    provider_id: string;
    provider_name: string;
    feed_mode: string;
    data_nature: string;
    read_only: boolean;
    redistribution: string;
    terms_note_zh: string;
  };
  retention: CryptoRetention;
  refresh: CryptoRefreshHint;
  unavailability_policy: string;
}

export interface CryptoIntervalOption {
  label: string;
  minutes: number;
}

export interface CryptoOverview {
  lane: string;
  stage: string;
  read_only: boolean;
  orders_supported: boolean;
  instruments: CryptoInstrument[];
  intervals: CryptoIntervalOption[];
  default_interval: string;
  limits: {
    max_bars_per_request: number;
    max_stored_rows: number;
    older_history_retrievable: boolean;
    note: string;
    note_zh: string;
  };
  venues: {
    exchange: string;
    instrument_id: string;
    pair: string;
    quote_asset: string;
    feed: string;
  }[];
  redistribution: string;
  terms_note_zh: string;
  provider: Record<string, unknown>;
}

/** Rows in chart order: completed ascending, then the forming candle last. */
export function candlesForCryptoChart(payload: CryptoSeriesPayload): CryptoCandle[] {
  const rows = [...payload.completed];
  if (payload.forming) rows.push(payload.forming);
  const byTime = new Map<number, CryptoCandle>();
  for (const row of rows) {
    const time = chartTime(row.open_time);
    if (time === null) continue;
    byTime.set(time, row);
  }
  return Array.from(byTime.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, row]) => row);
}

export interface NewBarResult {
  isNew: boolean;
  open_time: string | null;
  added: number;
  /** True when only the forming bar moved: a heartbeat, not a new trade. */
  heartbeatOnly: boolean;
}

/**
 * Compare the completed bars of a fresh response with the ones already shown.
 *
 * The first response establishes a baseline and is never called a "new bar".
 * A moved forming candle alone never counts as a new bar.
 */
export function detectNewCompletedBar(
  previousCompletedOpenTimes: string[] | null,
  payload: CryptoSeriesPayload,
): NewBarResult {
  const next = payload.completed.map((c) => c.open_time);
  if (previousCompletedOpenTimes === null) {
    return { isNew: false, open_time: null, added: 0, heartbeatOnly: false };
  }
  const seen = new Set(previousCompletedOpenTimes);
  const added = next.filter((t) => !seen.has(t));
  if (added.length === 0) {
    return {
      isNew: false,
      open_time: null,
      added: 0,
      heartbeatOnly: Boolean(payload.forming),
    };
  }
  return {
    isNew: true,
    open_time: added[added.length - 1],
    added: added.length,
    heartbeatOnly: false,
  };
}

export type CryptoConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'stale'
  | 'paused'
  | 'budget_limited'
  | 'unavailable';

export interface ConnectionView {
  tone: 'ok' | 'warn' | 'bad';
  label: string;
  detail: string;
}

const CONNECTION_VIEWS: Record<CryptoConnectionState, ConnectionView> = {
  idle: {
    tone: 'warn',
    label: 'Waiting to connect',
    detail: 'No request has been sent yet.',
  },
  connecting: {
    tone: 'warn',
    label: 'Connecting',
    detail: 'Requesting the most recent candles from the venue.',
  },
  connected: {
    tone: 'ok',
    label: 'Connected',
    detail: 'Venue data is arriving. A refresh is not a trade; only a newly completed candle is a new bar.',
  },
  reconnecting: {
    tone: 'warn',
    label: 'Reconnecting',
    detail: 'The previous read failed. Retrying with bounded backoff; old prices stay visible and are labelled.',
  },
  stale: {
    tone: 'warn',
    label: 'Data stale',
    detail: 'The newest completed candle is older than expected for this interval.',
  },
  paused: {
    tone: 'warn',
    label: 'Paused',
    detail: 'Updates are paused by you. The chart stays exactly as it was.',
  },
  budget_limited: {
    tone: 'warn',
    label: 'Updates paused (local budget)',
    detail:
      'The local upstream request budget is spent. No further venue reads are sent until the budget window resets or an operator raises it; stored candles stay readable, and "Check now" issues one bounded read.',
  },
  unavailable: {
    tone: 'bad',
    label: 'Source unavailable',
    detail: 'The venue did not answer. No prices are invented to replace it.',
  },
};

export function describeConnection(state: CryptoConnectionState): ConnectionView {
  return CONNECTION_VIEWS[state];
}

/** Human label for the payload's own mode, used next to the connection state. */
export function describeMode(mode: CryptoSeriesPayload['mode']): ConnectionView {
  switch (mode) {
    case 'live_realtime':
      return {
        tone: 'ok',
        label: 'Venue data',
        detail: 'Served from the exchange public market-data feed.',
      };
    case 'stale':
      return {
        tone: 'warn',
        label: 'Venue data (stale)',
        detail: 'The feed answered, but the newest candle is older than this interval expects.',
      };
    default:
      return {
        tone: 'bad',
        label: 'Venue unreachable',
        detail: 'The venue is not currently available to this process.',
      };
  }
}

export const MAX_BACKOFF_MS = 120_000;
export const MIN_POLL_SECONDS = 15;

/**
 * Delay until the next bounded read. Null means "schedule nothing".
 *
 * Failure count doubles the wait up to 120 s; a venue retry hint can only make
 * it longer. Jitter is injectable so tests stay deterministic.
 */
export function nextPollDelayMs(input: {
  hidden: boolean;
  paused: boolean;
  suggestedSeconds: number;
  minimumSeconds?: number;
  failureCount: number;
  retryAfterSeconds?: number | null;
  random?: () => number;
}): number | null {
  if (input.hidden || input.paused) return null;

  const floor = Math.max(MIN_POLL_SECONDS, input.minimumSeconds ?? MIN_POLL_SECONDS);
  const baseMs = Math.max(floor, Math.floor(input.suggestedSeconds || floor)) * 1000;
  const failures = Math.max(0, Math.min(Math.floor(input.failureCount), 3));
  let delay = failures > 0 ? Math.min(MAX_BACKOFF_MS, baseMs * 2 ** failures) : baseMs;

  if (input.retryAfterSeconds != null) {
    const hint = Math.max(MIN_POLL_SECONDS, Math.min(input.retryAfterSeconds, 300)) * 1000;
    delay = Math.max(delay, hint);
  }

  const random = input.random ?? Math.random;
  const jitter = delay * 0.2 * random();
  return Math.round(Math.min(MAX_BACKOFF_MS, delay + jitter));
}

/** Status tone for a refresh that was refused by the venue. */
export function describeVenueRefusal(status: number | null, reason?: string | null): string {
  if (status === 429 || reason === 'venue_rate_limited') {
    return 'The venue is rate limiting this process. Updates will slow down, not bypass the limit.';
  }
  if (reason === 'venue_unreachable') {
    return 'The venue could not be reached. Showing the last received candles only.';
  }
  if (reason === 'venue_rejected_request') {
    return 'The venue rejected the request. No substitute prices are shown.';
  }
  if (reason === 'budget_exhausted') {
    return 'The local upstream request budget is spent, so no new venue read was sent. This is a local guard, not a venue limit; stored candles stay readable and automatic polling is paused.';
  }
  return 'The market-data request failed. No substitute prices are shown.';
}

export function isCurrentCryptoSeries(
  responseKey: string,
  activeKey: string,
  requestId: number,
  activeRequestId: number,
): boolean {
  return isCurrentSeries(responseKey, activeKey, requestId, activeRequestId);
}

export function cryptoSeriesKey(instrumentId: string, interval: string): string {
  return seriesKey(instrumentId, interval);
}

/** Server data time; never replaced by the fetch time. */
export function cryptoDataAsOfLabel(payload: CryptoSeriesPayload | null): string {
  if (!payload?.data_as_of) return '— (no completed candles yet)';
  return payload.data_as_of;
}

export function cryptoIntegritySummary(integrity: CryptoIntegrity): string {
  return integritySummary(integrity);
}

/** Retention is stated, not implied: the venue is not a full history database. */
export function retentionSummary(retention: CryptoRetention | null | undefined): string {
  if (!retention) return 'Retention limit unknown';
  const parts = [`Up to ${retention.max_bars_per_request} most recent bars per request`];
  if (retention.oldest_returned_open_time) {
    parts.push(`oldest returned ${retention.oldest_returned_open_time}`);
  }
  if (!retention.older_history_retrievable) parts.push('older history not retrievable from this source');
  return parts.join('; ');
}

/** The price headline: completed close preferred, forming close clearly labelled. */
export function latestPrice(payload: CryptoSeriesPayload | null): {
  value: string | null;
  source: 'completed' | 'forming' | 'none';
  open_time: string | null;
} {
  if (!payload) return { value: null, source: 'none', open_time: null };
  const last = payload.completed[payload.completed.length - 1];
  if (last) {
    return { value: last.close, source: 'completed', open_time: last.open_time };
  }
  if (payload.forming) {
    return { value: payload.forming.close, source: 'forming', open_time: payload.forming.open_time };
  }
  return { value: null, source: 'none', open_time: null };
}
