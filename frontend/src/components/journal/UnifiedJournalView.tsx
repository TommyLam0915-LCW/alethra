'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface UnifiedTrade {
  source: 'research' | 'paper';
  id: string;
  run_id?: string;
  order_id?: string;
  instrument_id: string;
  rule_id?: string;
  exit_id?: string;
  entry_time?: string;
  exit_time?: string;
  filled_at?: string;
  entry_price?: number;
  exit_price?: number;
  price?: number;
  quantity?: number;
  side?: string;
  net_pnl?: number;
  exit_reason?: string;
  bars_held?: number;
  interval_minutes?: number;
}

interface UnifiedSummary {
  total_count: number;
  research_count: number;
  paper_count: number;
  research_total_pnl: number;
  research_win_rate: number;
}

interface FiltersApplied {
  source: string;
  instrument_id?: string;
  rule_id?: string;
  date_from?: string;
  date_to?: string;
  pnl_min?: number;
  pnl_max?: number;
}

export default function UnifiedJournalView() {
  const [trades, setTrades] = useState<UnifiedTrade[]>([]);
  const [summary, setSummary] = useState<UnifiedSummary | null>(null);
  const [filters, setFilters] = useState<FiltersApplied | null>(null);
  const [sourceFilter, setSourceFilter] = useState<'all' | 'research' | 'paper'>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTrades = useCallback(async () => {
    setLoading(true);
    setError(null);
    
    try {
      const data = (await api.journal.unified(sourceFilter)) as {
        trades: UnifiedTrade[];
        summary: UnifiedSummary;
        filters_applied: FiltersApplied;
      };
      
      setTrades(data.trades);
      setSummary(data.summary);
      setFilters(data.filters_applied);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [sourceFilter]);

  useEffect(() => {
    void loadTrades();
  }, [loadTrades]);

  const getSourceBadgeStyle = (source: 'research' | 'paper') => {
    if (source === 'research') {
      return {
        backgroundColor: 'rgba(59, 130, 246, 0.12)',
        color: 'var(--primary, #3B82F6)',
        border: '1px solid rgba(59, 130, 246, 0.3)',
      };
    }
    return {
      backgroundColor: 'rgba(168, 85, 247, 0.12)',
      color: '#A855F7',
      border: '1px solid rgba(168, 85, 247, 0.3)',
    };
  };

  const getPnlColor = (pnl: number | undefined) => {
    if (pnl === undefined || pnl === null) return 'var(--text-secondary)';
    return pnl >= 0 ? 'var(--positive, #36C7A0)' : 'var(--negative, #E2636E)';
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {error && (
        <div role="alert" style={{
          border: '1px solid var(--negative)',
          backgroundColor: 'rgba(226,99,110,0.1)',
          borderRadius: 6,
          padding: 12,
          fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {/* Source Filter */}
      <div style={{
        padding: '1rem 1.5rem',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        border: '1px solid var(--border-subtle)',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}>
        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)' }}>
          Source:
        </span>
        <div style={{ display: 'flex', gap: 8 }}>
          {(['all', 'research', 'paper'] as const).map((source) => (
            <button
              key={source}
              onClick={() => setSourceFilter(source)}
              style={{
                padding: '6px 14px',
                backgroundColor: sourceFilter === source ? 'var(--primary)' : 'var(--surface-2)',
                color: sourceFilter === source ? 'white' : 'var(--text-primary)',
                border: sourceFilter === source ? 'none' : '1px solid var(--border-strong)',
                borderRadius: 6,
                fontSize: 13,
                fontWeight: 500,
                cursor: 'pointer',
                textTransform: 'capitalize',
              }}
            >
              {source}
            </button>
          ))}
        </div>
        
        {summary && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 16, fontSize: 13 }}>
            <span style={{ color: 'var(--text-secondary)' }}>
              Total: <strong style={{ color: 'var(--text-primary)' }}>{summary.total_count}</strong>
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              Research: <strong style={{ color: 'var(--primary, #3B82F6)' }}>{summary.research_count}</strong>
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              Paper: <strong style={{ color: '#A855F7' }}>{summary.paper_count}</strong>
            </span>
          </div>
        )}
      </div>

      {/* Summary Stats */}
      {summary && summary.research_count > 0 && (
        <div style={{
          padding: '1rem 1.5rem',
          backgroundColor: 'var(--surface-1)',
          borderRadius: 8,
          border: '1px solid var(--border-subtle)',
          display: 'flex',
          gap: 24,
        }}>
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Research Total PnL
            </div>
            <div style={{ fontSize: 18, fontWeight: 600, color: getPnlColor(summary.research_total_pnl) }}>
              ${summary.research_total_pnl.toFixed(2)}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Research Win Rate
            </div>
            <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-primary)' }}>
              {(summary.research_win_rate * 100).toFixed(1)}%
            </div>
          </div>
        </div>
      )}

      {/* Trades Table */}
      <div style={{
        padding: '1.5rem',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        border: '1px solid var(--border-subtle)',
      }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16, margin: '0 0 16px 0' }}>
          All Trades
        </h3>
        
        {loading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            Loading...
          </div>
        ) : trades.length === 0 ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
            No trades found. Try adjusting your filters or run some research/paper trades.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '2px solid var(--border-subtle)' }}>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Source</th>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Date</th>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Instrument</th>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Skill</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Entry</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Exit</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>PnL</th>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Exit Reason</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade, idx) => (
                  <tr key={`${trade.source}-${trade.id}-${idx}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 12px' }}>
                      <span style={{
                        display: 'inline-block',
                        padding: '3px 8px',
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 600,
                        textTransform: 'uppercase',
                        ...getSourceBadgeStyle(trade.source),
                      }}>
                        {trade.source}
                      </span>
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                      {trade.entry_time 
                        ? new Date(trade.entry_time).toLocaleDateString()
                        : trade.filled_at
                        ? new Date(trade.filled_at).toLocaleDateString()
                        : 'N/A'}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                      {trade.instrument_id}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                      {trade.rule_id || trade.side || 'N/A'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                      {trade.entry_price?.toFixed(2) || trade.price?.toFixed(2) || 'N/A'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                      {trade.exit_price?.toFixed(2) || 'N/A'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: getPnlColor(trade.net_pnl), fontWeight: 500, fontFamily: 'monospace' }}>
                      {trade.net_pnl !== undefined ? `$${trade.net_pnl.toFixed(2)}` : 'N/A'}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-secondary)', fontSize: 12 }}>
                      {trade.exit_reason || 'N/A'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
