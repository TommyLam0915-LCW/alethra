'use client';

import { useEffect, useState, type CSSProperties } from 'react';
import { api } from '@/lib/api';
import type { JournalFilters } from './JournalDashboard';

interface Props {
  filters: JournalFilters;
}

interface SkillData {
  rule_id: string;
  rule_label_zh: string;
  rule_family: string;
  total_pnl: string;
  trade_count: number;
  win_count: number;
  win_rate: string;
  avg_trade_pnl: string;
  expectancy: string;
  sharpe_ratio: string | null;
  max_drawdown_pct: string;
  profit_factor: string | null;
}

interface AttributionResponse {
  skills: SkillData[];
  cross_instrument: any[];
  cross_interval: any[];
}

const cellStyle: CSSProperties = {
  padding: '7px 10px',
  borderBottom: '1px solid var(--border-subtle)',
  fontSize: 12,
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
  verticalAlign: 'top',
};

const headCellStyle: CSSProperties = {
  ...cellStyle,
  color: 'var(--text-secondary)',
  fontWeight: 500,
};

function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '--';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  return `$${num.toFixed(2)}`;
}

function valueColor(value: string | null | undefined): string | undefined {
  if (value === null || value === undefined || value === '') return undefined;
  const num = Number(value);
  if (!Number.isFinite(num)) return undefined;
  if (num > 0) return 'var(--positive, #36C7A0)';
  if (num < 0) return 'var(--negative, #E2636E)';
  return undefined;
}

export default function SkillAttributionPanel({ filters }: Props) {
  const [data, setData] = useState<AttributionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const result = (await api.journal.skillAttribution(filters)) as AttributionResponse;
        if (!cancelled) setData(result);
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to load skill attribution');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [filters]);

  if (loading) {
    return (
      <div role="status" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading skill attribution...
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

  if (!data || data.skills.length === 0) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        No skill data available for the current filters.
      </div>
    );
  }

  // Find max absolute P&L for bar chart scaling
  const maxAbsPnl = Math.max(...data.skills.map((s) => Math.abs(Number(s.total_pnl))), 1);

  return (
    <div>
      {/* Attribution Table */}
      <div style={{ overflowX: 'auto', border: '1px solid var(--border-subtle)', borderRadius: 6, marginBottom: 20 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 800 }}>
          <thead>
            <tr>
              <th style={{ ...headCellStyle, textAlign: 'left' }}>Skill</th>
              <th style={{ ...headCellStyle, textAlign: 'left' }}>Family</th>
              <th style={headCellStyle}>Total P&L</th>
              <th style={headCellStyle}>Trades</th>
              <th style={headCellStyle}>Win Rate</th>
              <th style={headCellStyle}>Avg P&L</th>
              <th style={headCellStyle}>Sharpe</th>
              <th style={headCellStyle}>Profit Factor</th>
              <th style={headCellStyle}>Max DD</th>
            </tr>
          </thead>
          <tbody>
            {data.skills.map((skill) => (
              <tr key={skill.rule_id}>
                <td style={{ ...cellStyle, textAlign: 'left', fontWeight: 500 }}>
                  {skill.rule_label_zh ?? skill.rule_id}
                </td>
                <td style={{ ...cellStyle, textAlign: 'left', color: 'var(--text-secondary)', fontSize: 11 }}>
                  {skill.rule_family}
                </td>
                <td style={{ ...cellStyle, fontWeight: 600, color: valueColor(skill.total_pnl) }}>
                  {formatMoney(skill.total_pnl)}
                </td>
                <td style={cellStyle}>{skill.trade_count}</td>
                <td style={cellStyle}>{Number(skill.win_rate).toFixed(1)}%</td>
                <td style={{ ...cellStyle, color: valueColor(skill.avg_trade_pnl) }}>
                  {formatMoney(skill.avg_trade_pnl)}
                </td>
                <td style={{ ...cellStyle, color: valueColor(skill.sharpe_ratio) }}>
                  {skill.sharpe_ratio !== null ? Number(skill.sharpe_ratio).toFixed(2) : '--'}
                </td>
                <td style={cellStyle}>
                  {skill.profit_factor !== null ? Number(skill.profit_factor).toFixed(2) : '--'}
                </td>
                <td style={{ ...cellStyle, color: 'var(--negative, #E2636E)' }}>
                  {Number(skill.max_drawdown_pct).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Horizontal Bar Chart */}
      <div style={{
        border: '1px solid var(--border-subtle)',
        borderRadius: 6,
        padding: 16,
        backgroundColor: 'var(--surface-1)',
      }}>
        <h3 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600 }}>Skills by Total P&L</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {data.skills.map((skill) => {
            const pnl = Number(skill.total_pnl);
            const widthPct = Math.min(100, (Math.abs(pnl) / maxAbsPnl) * 100);
            const isPositive = pnl >= 0;
            const barColor = isPositive ? 'var(--positive, #36C7A0)' : 'var(--negative, #E2636E)';

            return (
              <div key={skill.rule_id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ width: 120, fontSize: 12, color: 'var(--text-secondary)', textAlign: 'right', flexShrink: 0 }}>
                  {skill.rule_label_zh ?? skill.rule_id}
                </div>
                <div style={{ flex: 1, height: 20, backgroundColor: 'var(--surface-2)', borderRadius: 3, overflow: 'hidden', position: 'relative' }}>
                  <div style={{
                    width: `${widthPct}%`,
                    height: '100%',
                    backgroundColor: barColor,
                    borderRadius: 3,
                    opacity: 0.8,
                    transition: 'width 0.3s ease',
                  }} />
                </div>
                <div style={{ width: 80, fontSize: 12, fontWeight: 600, color: barColor, textAlign: 'right', flexShrink: 0 }}>
                  {formatMoney(skill.total_pnl)}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
