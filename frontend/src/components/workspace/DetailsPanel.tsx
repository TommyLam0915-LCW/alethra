'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { ADJUSTMENT_LABELS, DATA_NATURE_LABELS, label } from '@/lib/copy';
import type { InstrumentDetail } from './types';

export default function DetailsPanel({ instrumentId }: { instrumentId: string }) {
  const [detail, setDetail] = useState<InstrumentDetail | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setError('');
    setDetail(null);
    (async () => {
      try {
        const data = await api.instruments.get(instrumentId);
        if (!cancelled) setDetail(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load details');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [instrumentId]);

  return (
    <div className="ws-panel" data-testid="details-panel">
      <div className="ws-panel-header">Instrument details</div>
      <div className="ws-panel-body" style={{ padding: 10 }}>
        {error && (
          <div className="ws-message-error" role="alert">
            {error}
          </div>
        )}
        {!detail && !error && (
          <div className="ws-muted" role="status">
            Loading…
          </div>
        )}
        {detail && (
          <div style={{ display: 'grid', gap: 6 }}>
            <div>
              <div className="ws-symbol-label" style={{ fontSize: 15 }}>
                {detail.symbol}
              </div>
              <div className="ws-small">{detail.name}</div>
            </div>
            <table className="ws-table">
              <tbody>
                <tr>
                  <td className="ws-muted">Market</td>
                  <td>
                    {detail.market} · {detail.exchange}
                  </td>
                </tr>
                <tr>
                  <td className="ws-muted">Currency</td>
                  <td>{detail.currency}</td>
                </tr>
                <tr>
                  <td className="ws-muted">Timezone</td>
                  <td>{detail.timezone}</td>
                </tr>
                <tr>
                  <td className="ws-muted">Lot size</td>
                  <td>
                    {detail.lot_size ?? '—'}
                    {detail.lot_size_verified ? (
                      <span className="ws-status-badge" style={{ marginLeft: 6 }}>
                        Verified
                      </span>
                    ) : (
                      <span className="ws-status-badge" style={{ marginLeft: 6 }}>
                        Unverified
                      </span>
                    )}
                  </td>
                </tr>
                {detail.latest_bar && (
                  <>
                    <tr>
                      <td className="ws-muted">Latest close</td>
                      <td>{detail.latest_bar.close.toFixed(2)}</td>
                    </tr>
                    <tr>
                      <td className="ws-muted">Date</td>
                      <td>{detail.latest_bar.date}</td>
                    </tr>
                    <tr>
                      <td className="ws-muted">Volume</td>
                      <td>{detail.latest_bar.volume?.toLocaleString() ?? '—'}</td>
                    </tr>
                  </>
                )}
                {detail.data_quality && (
                  <tr>
                    <td className="ws-muted">Data nature</td>
                    <td>
                      <span className="ws-nature-badge ws-nature-unknown" style={{ marginRight: 6 }}>
                        {label(DATA_NATURE_LABELS, detail.data_quality.data_nature)}
                      </span>
                      {detail.data_quality.is_stale && (
                        <span className="ws-nature-badge ws-nature-delayed">Stale</span>
                      )}
                    </td>
                  </tr>
                )}
                {detail.data_quality?.adjustment && (
                  <tr>
                    <td className="ws-muted">Adjustment</td>
                    <td>{label(ADJUSTMENT_LABELS, detail.data_quality.adjustment)}</td>
                  </tr>
                )}
                {detail.data_coverage && (
                  <tr>
                    <td className="ws-muted">Available period</td>
                    <td>
                      {detail.data_coverage.availability_source === 'provider_declared' &&
                      detail.data_coverage.available_start &&
                      detail.data_coverage.available_end ? (
                        <>
                          {detail.data_coverage.available_start} – {detail.data_coverage.available_end}
                          <span className="ws-muted"> ({detail.data_coverage.timeframe})</span>
                        </>
                      ) : (
                        <>
                          UNKNOWN
                          <span className="ws-muted"> (the provider did not declare a period)</span>
                        </>
                      )}
                    </td>
                  </tr>
                )}
                <tr>
                  <td className="ws-muted">Data source</td>
                  <td>
                    {detail.data_source} ({detail.feed_mode})
                  </td>
                </tr>
              </tbody>
            </table>
            {(detail.data_quality?.note || detail.data_quality?.note_zh) && (
              <div className="ws-muted">{detail.data_quality.note ?? detail.data_quality.note_zh}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
