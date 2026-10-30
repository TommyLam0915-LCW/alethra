'use client';

import {
  ADJUSTMENT_LABELS,
  DATA_NATURE_LABELS,
  MARKET_STATUS_LABELS,
  PRICE_MODE_LABELS,
  label,
} from '@/lib/copy';
import type { BarsResponse, DataCoverage, InstrumentDetail } from './types';

const NATURE_CLASSES: Record<string, string> = {
  synthetic: 'ws-nature-badge ws-nature-synthetic',
  live: 'ws-nature-badge ws-nature-live',
  delayed: 'ws-nature-badge ws-nature-delayed',
  unknown: 'ws-nature-badge ws-nature-unknown',
};

const MODE_CLASSES: Record<string, string> = {
  live_realtime: 'ws-nature-badge ws-nature-live',
  delayed: 'ws-nature-badge ws-nature-delayed',
  historical_replay: 'ws-nature-badge ws-nature-unknown',
  synthetic_fixture: 'ws-nature-badge ws-nature-synthetic',
  unknown: 'ws-nature-badge ws-nature-unknown',
};

const STATUS_CLASSES: Record<string, string> = {
  open: 'ws-nature-badge ws-nature-live',
  closed: 'ws-nature-badge ws-nature-unknown',
  stale: 'ws-nature-badge ws-nature-synthetic',
  no_data: 'ws-nature-badge ws-nature-unknown',
  disconnected: 'ws-nature-badge ws-nature-delayed',
};

interface Props {
  /** The price view being shown; the instrument detail is the fallback while bars load or fail. */
  data: BarsResponse | InstrumentDetail | null;
  loading: boolean;
  /**
   * Coverage from the instrument detail. A price view carries no coverage of its
   * own, so an empty chart could not name the period that does have data without
   * this.
   */
  coverage?: DataCoverage | null;
}

export default function InstrumentHeader({ data, loading, coverage }: Props) {
  const bars = data && 'bars' in data ? data : null;
  const detail = data && 'latest_bar' in data ? data : null;
  const lastBar = bars ? bars.bars[bars.bars.length - 1] ?? null : (detail?.latest_bar ?? null);
  const nature = data?.data_quality?.data_nature;
  const isStale = data?.data_quality?.is_stale;
  const provider = bars?.provider ?? detail?.data_source;
  const asOf = data?.data_as_of || lastBar?.date;
  const receivedUtc = (bars?.series_received_at || detail?.received_at || '').slice(11, 16);

  const cov = coverage ?? detail?.data_coverage ?? null;
  const declaredWindow =
    cov && cov.availability_source === 'provider_declared' && cov.available_start && cov.available_end
      ? `${cov.available_start} – ${cov.available_end}`
      : null;
  const periodLabel =
    bars && bars.bars.length === 0 && declaredWindow
      ? `No data in this range; available period ${declaredWindow}`
      : `Data as of ${asOf || '—'}`;

  const natureLabel = nature ? label(DATA_NATURE_LABELS, nature) : null;
  const modeLabel = data?.mode ? label(PRICE_MODE_LABELS, data.mode) : null;
  const statusLabel = data?.market_status
    ? label(MARKET_STATUS_LABELS, data.market_status)
    : null;
  // The same word twice in one row reads as a rendering bug, not as emphasis:
  // synthetic fixtures report both nature and price mode as "Synthetic".
  // The stale badge is never suppressed: data freshness is a separate fact from
  // market status, even when both happen to be labelled "Stale".
  const showModeBadge = !!modeLabel && modeLabel !== natureLabel;
  const showStaleBadge = !!isStale;

  const statusTooltip = data?.market_status
    ? [
        data.market_status_reason,
        data.current_session ? `Current session ${data.current_session}` : null,
        data.next_regular_open ? `Next regular session ${data.next_regular_open}` : null,
        data.holiday_calendar_populated === false ? 'Holiday calendar not loaded' : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : undefined;

  return (
    <div className="ws-instrument-header" data-testid="instrument-header">
      <div className="ws-instrument-identity" data-testid="chart-legend">
        <h2 className="ws-instrument-title">
          {data ? `${data.symbol} · ${data.name}` : loading ? 'Loading…' : '—'}
        </h2>
        {nature && (
          <span className={NATURE_CLASSES[nature] || NATURE_CLASSES.unknown} data-testid="chart-nature-badge">
            {natureLabel}
          </span>
        )}
        {data?.mode && showModeBadge && (
          <span
            className={MODE_CLASSES[data.mode] || MODE_CLASSES.unknown}
            data-testid="chart-mode-badge"
            title={data.timestamp_semantics ?? undefined}
          >
            {modeLabel}
          </span>
        )}
        {data?.market_status && (
          <span
            className={STATUS_CLASSES[data.market_status] || STATUS_CLASSES.stale}
            data-testid="chart-market-status-badge"
            title={statusTooltip}
          >
            {statusLabel}
          </span>
        )}
        {showStaleBadge && (
          <span className="ws-nature-badge ws-nature-delayed" data-testid="chart-stale-badge">
            Stale
          </span>
        )}
        <span className="ws-instrument-price" style={{ marginLeft: 'auto' }} data-testid="instrument-last-price">
          {lastBar ? (
            <>
              {lastBar.close.toFixed(2)} {data?.currency}
            </>
          ) : (
            <span className="ws-muted">No quote</span>
          )}
        </span>
      </div>
      {data && (
        <div className="ws-muted ws-instrument-meta" data-testid="chart-meta-line">
          {bars ? `${bars.bar_count} bars · ` : ''}
          {data.timeframe || '1d'} · {data.exchange} ({data.timezone})
          {provider ? ` · Provider ${provider}` : ''} · {periodLabel}
          {receivedUtc ? ` · Fetched ${receivedUtc} UTC` : ''}
          {data.data_quality?.adjustment
            ? ` · Adjustment: ${label(ADJUSTMENT_LABELS, data.data_quality.adjustment)}`
            : ''}
        </div>
      )}
    </div>
  );
}
