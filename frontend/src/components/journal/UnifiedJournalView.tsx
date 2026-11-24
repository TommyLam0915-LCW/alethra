'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
  FILL_PNL_NOTE,
  unifiedDateLabel,
  unifiedPnlNote,
  unifiedReason,
} from '@/lib/journalUnifiedCells';
import { formatMoney, formatNumber, formatPercentFraction } from '@/lib/format';

/** The nine controls the filter bar drives, all optional. */
export interface UnifiedFilterRequest {
  instrument_id?: string;
  rule_id?: string;
  rule_family?: string;
  interval_minutes?: number;
  date_from?: string;
  date_to?: string;
  pnl_min?: string;
  pnl_max?: string;
  exit_reason?: string;
}

interface UnifiedTrade {
  source: 'research' | 'paper';
  id: string;
  run_id: string | null;
  order_id: string | null;
  decision_id: string | null;
  instrument_id: string;
  rule_id: string | null;
  rule_family: string | null;
  decision_type: string | null;
  side: string | null;
  order_type: string | null;
  // 'bar' means an instant was observed; 'day' means the record knows its
  // calendar day and nothing more. Reading a day as a midnight invents a time.
  time_precision: 'bar' | 'day';
  entry_time: string | null;
  execution_date: string | null;
  exit_time: string | null;
  entry_price: string | null;
  exit_price: string | null;
  observed_price: string | null;
  quantity: string | null;
  gross_pnl: string | null;
  commission: string | null;
  slippage_cost: string | null;
  total_cost: string | null;
  net_cash_change: string | null;
  net_pnl: string | null;
  pnl_status: string | null;
  cash_after: string | null;
  exit_id: string | null;
  exit_reason: string | null;
  outcome: string | null;
  bars_held: number | null;
  interval_minutes: number | null;
  mfe_pct: string | null;
  mae_pct: string | null;
  fill_reason: string | null;
  created_at: string | null;
}

interface UnifiedSummary {
  total_count: number;
  // Null when the leg is out of the requested scope, which is not the same
  // claim as zero rows in it.
  research_count: number | null;
  paper_count: number | null;
  research_total_pnl: string | null;
  research_wins: number | null;
  // A proportion, not a percent: 0.625 means 62.5%. None when no research trade
  // matched, because an empty denominator has no win rate.
  research_win_rate: number | null;
}

interface PageScope {
  returned: number;
  limit: number;
  offset: number;
  order: string;
}

interface FilterScope {
  research: string[];
  paper: string[];
  ignored_for_paper: string[];
}

interface FiltersApplied {
  source: string;
  instrument_id: string | null;
  rule_id: string | null;
  rule_family: string | null;
  interval_minutes: number | null;
  date_from: string | null;
  date_to: string | null;
  pnl_min: string | null;
  pnl_max: string | null;
  exit_reason: string | null;
}

interface UnifiedResponse {
  trades: UnifiedTrade[];
  summary: UnifiedSummary;
  page: PageScope;
  filter_scope: FilterScope;
  limitations: string[];
  filters_applied: FiltersApplied;
}

// The endpoint returns money as fixed-point text so no digit is lost in
// transit; the three cells whose value depends on which ledger a row came from
// are decided in `@/lib/journalUnifiedCells`, next to the reasons why.

export default function UnifiedJournalView({
  filters = {},
  source,
  onSourceChange,
}: {
  filters?: UnifiedFilterRequest;
  source: 'all' | 'research' | 'paper';
  onSourceChange: (source: 'all' | 'research' | 'paper') => void;
}) {
  const [data, setData] = useState<UnifiedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const trades = data?.trades ?? [];
  const summary = data?.summary ?? null;
  const page = data?.page ?? null;
  const filterScope = data?.filter_scope ?? null;
  const limitations = data?.limitations ?? [];

  const loadTrades = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      // The same nine controls the other tabs honour are sent here. Before this
      // the endpoint was called with only `source`, so the filter bar visibly
      // did nothing on this tab.
      setData((await api.journal.unified(source, filters)) as UnifiedResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [source, filters]);

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

  const getPnlColor = (pnl: number | string | null | undefined) => {
    if (pnl === undefined || pnl === null || pnl === '') return 'var(--text-secondary)';
    const amount = typeof pnl === 'number' ? pnl : Number(pnl);
    if (!Number.isFinite(amount)) return 'var(--text-secondary)';
    return amount >= 0 ? 'var(--positive, #36C7A0)' : 'var(--negative, #E2636E)';
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
          {(['all', 'research', 'paper'] as const).map((option) => (
            <button
              key={option}
              onClick={() => onSourceChange(option)}
              style={{
                padding: '6px 14px',
                backgroundColor: source === option ? 'var(--primary)' : 'var(--surface-2)',
                color: source === option ? 'white' : 'var(--text-primary)',
                border: source === option ? 'none' : '1px solid var(--border-strong)',
                borderRadius: 6,
                fontSize: 13,
                fontWeight: 500,
                cursor: 'pointer',
                textTransform: 'capitalize',
              }}
            >
              {option}
            </button>
          ))}
        </div>
        
        {summary && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 16, fontSize: 13 }}>
            <span style={{ color: 'var(--text-secondary)' }}>
              Total: <strong style={{ color: 'var(--text-primary)' }}>{summary.total_count}</strong>
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              Research:{' '}
              <strong style={{ color: 'var(--primary, #3B82F6)' }}>
                {summary.research_count ?? '—'}
              </strong>
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              Paper: <strong style={{ color: '#A855F7' }}>{summary.paper_count ?? '—'}</strong>
            </span>
          </div>
        )}
      </div>

      {/* Count scope */}
      {/* The counts above are the whole filtered population; the table below is
          one page of it. Saying only "Total: 1,096" over 200 rows invites the
          reader to think the page is the population. */}
      {summary && page && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: -8 }}>
          Showing {page.returned} of {summary.total_count} records matching this filter
          {page.returned < summary.total_count
            ? ' — the CSV export covers the whole range, this page does not.'
            : '.'}
        </div>
      )}

      {/* What the filters did not reach */}
      {filterScope && filterScope.ignored_for_paper.length > 0 && (
        <div style={{
          padding: '0.75rem 1.5rem',
          backgroundColor: 'var(--surface-1)',
          borderRadius: 8,
          border: '1px solid var(--border-subtle)',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>
            Filter scope
          </div>
          {limitations.map((line) => (
            <div key={line} style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {line}
            </div>
          ))}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Ignored for the paper leg:
            </span>
            {filterScope.ignored_for_paper.map((name) => (
              <span
                key={name}
                style={{
                  padding: '2px 8px',
                  borderRadius: 4,
                  fontSize: 11,
                  fontFamily: 'monospace',
                  backgroundColor: 'var(--surface-2)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-strong)',
                }}
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Summary Stats */}
      {summary && (summary.research_count ?? 0) > 0 && (
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
              {formatMoney(summary.research_total_pnl)}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Research Win Rate
            </div>
            <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-primary)' }}>
              {formatPercentFraction(summary.research_win_rate)}
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
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Side</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Entry</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Exit</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>PnL</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Cash Δ</th>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Reason</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade, idx) => {
                  const reason = unifiedReason(trade);
                  return (
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
                    {/* A daily fill knows its day and no clock time. Its old cell
                        read entry_time, which is null there, so every such row
                        showed a dash where the record does have a date. */}
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                      {unifiedDateLabel(trade)}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                      {trade.instrument_id}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                      {trade.rule_id ?? '—'}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-secondary)', fontSize: 12 }}>
                      {trade.side ?? '—'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                      {formatNumber(trade.entry_price, 2)}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                      {formatNumber(trade.exit_price, 2)}
                    </td>
                    <td
                      style={{ padding: '10px 12px', textAlign: 'right', color: getPnlColor(trade.net_pnl), fontWeight: 500, fontFamily: 'monospace' }}
                      title={unifiedPnlNote(trade) ?? undefined}
                    >
                      {formatMoney(trade.net_pnl)}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: getPnlColor(trade.net_cash_change), fontFamily: 'monospace' }}>
                      {formatMoney(trade.net_cash_change)}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-secondary)', fontSize: 12 }}>
                      {reason.kind === 'fill' ? (
                        <>
                          <span style={{ opacity: 0.6, fontSize: 10, textTransform: 'uppercase' }}>
                            fill ·{' '}
                          </span>
                          {reason.text}
                        </>
                      ) : (
                        reason.text
                      )}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
