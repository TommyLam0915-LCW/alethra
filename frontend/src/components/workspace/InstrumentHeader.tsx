'use client';

import type { BarsResponse, DataCoverage, InstrumentDetail } from './types';

const NATURE_LABELS: Record<string, string> = {
  synthetic: '模擬數據',
  live: '即時數據',
  delayed: '延遲數據',
  unknown: '數據性質未知',
};

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

const ADJUSTMENT_LABELS: Record<string, string> = {
  adjusted: '復權：已調整',
  unadjusted: '復權：未調整',
  not_applicable: '復權：不適用',
  unknown: '復權狀態未知',
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
      ? `${cov.available_start} 至 ${cov.available_end}`
      : null;
  const periodLabel =
    bars && bars.bars.length === 0 && declaredWindow
      ? `此範圍沒有數據，可用期間 ${declaredWindow}`
      : `數據截至 ${asOf || '—'}`;

  const statusTooltip = data?.market_status
    ? [
        data.market_status_reason,
        data.current_session ? `目前時段 ${data.current_session}` : null,
        data.next_regular_open ? `下次常規交易 ${data.next_regular_open}` : null,
        data.holiday_calendar_populated === false ? '假期日曆未載入' : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : undefined;

  return (
    <div className="ws-instrument-header" data-testid="instrument-header">
      <div className="ws-instrument-identity" data-testid="chart-legend">
        <h2 className="ws-instrument-title">
          {data ? `${data.symbol} · ${data.name}` : loading ? '載入中…' : '—'}
        </h2>
        {nature && (
          <span className={NATURE_CLASSES[nature] || NATURE_CLASSES.unknown} data-testid="chart-nature-badge">
            {NATURE_LABELS[nature] || nature}
          </span>
        )}
        {data?.mode && (
          <span
            className={MODE_CLASSES[data.mode] || MODE_CLASSES.unknown}
            data-testid="chart-mode-badge"
            title={data.timestamp_semantics ?? undefined}
          >
            {data.mode_label_zh || data.mode}
          </span>
        )}
        {data?.market_status && (
          <span
            className={STATUS_CLASSES[data.market_status] || STATUS_CLASSES.stale}
            data-testid="chart-market-status-badge"
            title={statusTooltip}
          >
            {data.market_status_label_zh || data.market_status}
          </span>
        )}
        {isStale && (
          <span className="ws-nature-badge ws-nature-delayed" data-testid="chart-stale-badge">
            陳舊
          </span>
        )}
        <span className="ws-instrument-price" style={{ marginLeft: 'auto' }} data-testid="instrument-last-price">
          {lastBar ? (
            <>
              {lastBar.close.toFixed(2)} {data?.currency}
            </>
          ) : (
            <span className="ws-muted">無報價</span>
          )}
        </span>
      </div>
      {data && (
        <div className="ws-muted ws-instrument-meta" data-testid="chart-meta-line">
          {bars ? `${bars.bar_count} 條 · ` : ''}
          {data.timeframe || '1d'} · {data.exchange}（{data.timezone}）
          {provider ? ` · 供應商 ${provider}` : ''} · {periodLabel}
          {receivedUtc ? ` · 擷取於 ${receivedUtc} UTC` : ''}
          {data.data_quality?.adjustment
            ? ` · ${ADJUSTMENT_LABELS[data.data_quality.adjustment] || data.data_quality.adjustment}`
            : ''}
        </div>
      )}
    </div>
  );
}
