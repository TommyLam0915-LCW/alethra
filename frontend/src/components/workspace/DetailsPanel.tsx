'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { InstrumentDetail } from './types';

const NATURE_LABELS: Record<string, string> = {
  synthetic: '模擬數據',
  live: '即時數據',
  delayed: '延遲數據',
  unknown: '未知',
};

const STALE_LABELS: Record<string, string> = {
  not_applicable: '不適用',
  unadjusted: '未調整',
  adjusted: '已調整',
  unknown: '未知',
};

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
        if (!cancelled) setError(err instanceof Error ? err.message : '載入失敗');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [instrumentId]);

  return (
    <div className="ws-panel" data-testid="details-panel">
      <div className="ws-panel-header">代號詳情</div>
      <div className="ws-panel-body" style={{ padding: 10 }}>
        {error && <div className="ws-message-error">{error}</div>}
        {!detail && !error && <div className="ws-muted">載入中…</div>}
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
                  <td className="ws-muted">市場</td>
                  <td>
                    {detail.market} · {detail.exchange}
                  </td>
                </tr>
                <tr>
                  <td className="ws-muted">貨幣</td>
                  <td>{detail.currency}</td>
                </tr>
                <tr>
                  <td className="ws-muted">時區</td>
                  <td>{detail.timezone}</td>
                </tr>
                <tr>
                  <td className="ws-muted">每手股數</td>
                  <td>
                    {detail.lot_size ?? '—'}
                    {detail.lot_size_verified ? (
                      <span className="ws-status-badge" style={{ marginLeft: 6 }}>
                        已驗證
                      </span>
                    ) : (
                      <span className="ws-status-badge" style={{ marginLeft: 6 }}>
                        未驗證
                      </span>
                    )}
                  </td>
                </tr>
                {detail.latest_bar && (
                  <>
                    <tr>
                      <td className="ws-muted">最新收市</td>
                      <td>{detail.latest_bar.close.toFixed(2)}</td>
                    </tr>
                    <tr>
                      <td className="ws-muted">日期</td>
                      <td>{detail.latest_bar.date}</td>
                    </tr>
                    <tr>
                      <td className="ws-muted">成交量</td>
                      <td>{detail.latest_bar.volume?.toLocaleString() ?? '—'}</td>
                    </tr>
                  </>
                )}
                {detail.data_quality && (
                  <tr>
                    <td className="ws-muted">數據性質</td>
                    <td>
                      <span className="ws-nature-badge ws-nature-unknown" style={{ marginRight: 6 }}>
                        {NATURE_LABELS[detail.data_quality.data_nature] || detail.data_quality.data_nature}
                      </span>
                      {detail.data_quality.is_stale && (
                        <span className="ws-nature-badge ws-nature-delayed">陳舊</span>
                      )}
                    </td>
                  </tr>
                )}
                {detail.data_quality?.adjustment && (
                  <tr>
                    <td className="ws-muted">復權</td>
                    <td>{STALE_LABELS[detail.data_quality.adjustment] || detail.data_quality.adjustment}</td>
                  </tr>
                )}
                <tr>
                  <td className="ws-muted">數據源</td>
                  <td>
                    {detail.data_source}（{detail.feed_mode}）
                  </td>
                </tr>
              </tbody>
            </table>
            {detail.data_quality?.note_zh && (
              <div className="ws-muted">{detail.data_quality.note_zh}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
