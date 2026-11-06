'use client';

import { useEffect, useState, type CSSProperties } from 'react';
import { api } from '@/lib/api';
import type { JournalFilters } from './JournalDashboard';

interface Props {
  filters: JournalFilters;
}

interface HeatmapResponse {
  rows: string[];
  cols: string[];
  cells: (string | null)[][];
}

type MetricKey = 'net_pnl' | 'win_rate' | 'sharpe' | 'trade_count';

const METRIC_OPTIONS: { value: MetricKey; label: string }[] = [
  { value: 'net_pnl', label: 'Net P&L' },
  { value: 'win_rate', label: 'Win Rate %' },
  { value: 'sharpe', label: 'Sharpe Ratio' },
  { value: 'trade_count', label: 'Trade Count' },
];

const inputStyle: CSSProperties = {
  padding: '7px 10px',
  border: '1px solid var(--border-subtle)',
  borderRadius: 4,
  backgroundColor: 'var(--surface-2)',
  color: 'var(--text-primary)',
  fontSize: 13,
};

function cellBgColor(value: string | null, metric: MetricKey): string {
  if (value === null) return 'transparent';
  const num = Number(value);
  if (!Number.isFinite(num)) return 'transparent';

  let intensity: number;
  let isPositive: boolean;

  if (metric === 'net_pnl' || metric === 'sharpe') {
    isPositive = num >= 0;
    const absVal = Math.abs(num);
    const maxScale = metric === 'sharpe' ? 3 : Math.max(absVal, 1);
    intensity = Math.min(1, absVal / maxScale);
  } else if (metric === 'win_rate') {
    isPositive = true;
    intensity = Math.min(1, num / 100);
  } else {
    isPositive = true;
    intensity = Math.min(1, num / 20);
  }

  const alpha = 0.15 + intensity * 0.55;
  if (metric === 'trade_count') {
    return `rgba(79, 141, 247, ${alpha})`;
  }
  return isPositive
    ? `rgba(54, 199, 160, ${alpha})`
    : `rgba(226, 99, 110, ${alpha})`;
}

function formatCellValue(value: string | null, metric: MetricKey): string {
  if (value === null) return '--';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;

  switch (metric) {
    case 'net_pnl':
      return `$${num.toFixed(0)}`;
    case 'win_rate':
      return `${num.toFixed(0)}%`;
    case 'sharpe':
      return num.toFixed(2);
    case 'trade_count':
      return String(Math.round(num));
    default:
      return value;
  }
}

export default function SkillHeatmap({ filters }: Props) {
  const [metric, setMetric] = useState<MetricKey>('net_pnl');
  const [data, setData] = useState<HeatmapResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const result = (await api.journal.skillHeatmap(metric, filters)) as HeatmapResponse;
        if (!cancelled) setData(result);
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to load heatmap');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [metric, filters]);

  if (loading) {
    return (
      <div role="status" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading heatmap...
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" style={{ color: 'var(--negative)', padding: '1rem', fontSize: 13 }}>
        {error}
      </div>
    );
  }

  if (!data || data.rows.length === 0 || data.cols.length === 0) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        No data available for the current filters.
      </div>
    );
  }

  return (
    <div style={{
      border: '1px solid var(--border-subtle)',
      borderRadius: 6,
      padding: 16,
      backgroundColor: 'var(--surface-1)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>Skill Heatmap</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Metric:</label>
          <select
            value={metric}
            onChange={(e) => setMetric(e.target.value as MetricKey)}
            style={inputStyle}
          >
            {METRIC_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 400 }}>
          <thead>
            <tr>
              <th style={{
                padding: '8px 12px',
                fontSize: 11,
                fontWeight: 500,
                color: 'var(--text-secondary)',
                textAlign: 'left',
                borderBottom: '1px solid var(--border-subtle)',
                position: 'sticky',
                left: 0,
                backgroundColor: 'var(--surface-1)',
                zIndex: 1,
              }}>
                Skill / Instrument
              </th>
              {data.cols.map((col) => (
                <th key={col} style={{
                  padding: '8px 12px',
                  fontSize: 11,
                  fontWeight: 500,
                  color: 'var(--text-secondary)',
                  textAlign: 'center',
                  borderBottom: '1px solid var(--border-subtle)',
                  whiteSpace: 'nowrap',
                }}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((rowLabel, rowIdx) => (
              <tr key={rowLabel}>
                <td style={{
                  padding: '8px 12px',
                  fontSize: 12,
                  fontWeight: 500,
                  color: 'var(--text-primary)',
                  borderBottom: '1px solid var(--border-subtle)',
                  whiteSpace: 'nowrap',
                  position: 'sticky',
                  left: 0,
                  backgroundColor: 'var(--surface-1)',
                  zIndex: 1,
                }}>
                  {rowLabel}
                </td>
                {data.cols.map((_, colIdx) => {
                  const cellValue = data.cells[rowIdx]?.[colIdx] ?? null;
                  return (
                    <td key={colIdx} style={{
                      padding: '8px 12px',
                      fontSize: 12,
                      textAlign: 'center',
                      borderBottom: '1px solid var(--border-subtle)',
                      backgroundColor: cellBgColor(cellValue, metric),
                      fontVariantNumeric: 'tabular-nums',
                      color: 'var(--text-primary)',
                    }}>
                      {formatCellValue(cellValue, metric)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'flex', gap: 16, marginTop: 12, fontSize: 11, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ display: 'inline-block', width: 12, height: 12, borderRadius: 2, backgroundColor: 'rgba(54, 199, 160, 0.5)' }} />
          Positive
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ display: 'inline-block', width: 12, height: 12, borderRadius: 2, backgroundColor: 'rgba(226, 99, 110, 0.5)' }} />
          Negative
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ display: 'inline-block', width: 12, height: 12, borderRadius: 2, backgroundColor: 'transparent', border: '1px solid var(--border-subtle)' }} />
          No data
        </div>
      </div>
    </div>
  );
}
