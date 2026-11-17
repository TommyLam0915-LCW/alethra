'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatMoney, formatNumber, formatPercentFraction, formatPercentPoints } from '@/lib/format';

interface RunComparison {
  run_id: string;
  instrument_id: string;
  interval_minutes: number;
  created_at: string | null;
  total_trades: number;
  wins: number;
  losses: number;
  // A run that has not traded has no win rate, and a run with one trade (or with
  // identical P&L on every trade) has no Sharpe: the API sends null for both and
  // the cell must not read either as a measured zero.
  win_rate: number | null;
  total_pnl: number;
  avg_pnl: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  sharpe: number | null;
  max_drawdown_pct: number;
}

type MetricStem = 'sharpe' | 'pnl' | 'win_rate' | 'max_drawdown';

interface ComparisonResult {
  comparisons: RunComparison[];
  highlights: Partial<Record<`best_${MetricStem}` | `worst_${MetricStem}`, string>>;
  count: number;
}

interface AvailableRun {
  id: string;
  instrument_id: string;
  interval_minutes: number;
  trade_count: number;
  created_at: string;
}

export default function CrossRunComparison() {
  const [availableRuns, setAvailableRuns] = useState<AvailableRun[]>([]);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAvailableRuns = useCallback(async () => {
    try {
      // Fetch recent runs from the journal API
      const data = (await api.journal.trades({ limit: 100 })) as {
        trades: Array<{ run_id: string; instrument_id: string; interval_minutes: number; entry_time: string }>;
      };
      
      // Group by run_id and get metadata
      const runMap = new Map<string, AvailableRun>();
      for (const trade of data.trades) {
        if (!runMap.has(trade.run_id)) {
          runMap.set(trade.run_id, {
            id: trade.run_id,
            instrument_id: trade.instrument_id,
            interval_minutes: trade.interval_minutes,
            trade_count: 0,
            created_at: trade.entry_time,
          });
        }
        runMap.get(trade.run_id)!.trade_count++;
      }
      
      setAvailableRuns(Array.from(runMap.values()).slice(0, 20));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadAvailableRuns();
  }, [loadAvailableRuns]);

  const handleToggleRun = (runId: string) => {
    setSelectedRunIds((prev) => {
      if (prev.includes(runId)) {
        return prev.filter((id) => id !== runId);
      }
      if (prev.length >= 10) {
        return prev;
      }
      return [...prev, runId];
    });
  };

  const handleCompare = async () => {
    if (selectedRunIds.length < 2) {
      setError('Select at least 2 runs to compare');
      return;
    }

    setLoading(true);
    setError(null);
    
    try {
      const result = (await api.journal.compare(selectedRunIds.join(','))) as ComparisonResult;
      setComparison(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleClearSelection = () => {
    setSelectedRunIds([]);
    setComparison(null);
  };

  // Highlights are keyed `best_<stem>` / `worst_<stem>`, and `max_drawdown` is a
  // two-word stem: splitting the best key on `_` asked for `worst_max`, a key the
  // API never sends, so the worst drawdown was never marked. Lower-is-better is
  // decided server side, where the ranking happens.
  const getMetricStyle = (runId: string, stem: MetricStem) => {
    if (!comparison?.highlights) return {};

    const isBest = comparison.highlights[`best_${stem}`] === runId;
    const isWorst = comparison.highlights[`worst_${stem}`] === runId;

    return {
      color: isBest ? 'var(--positive, #36C7A0)' : isWorst ? 'var(--negative, #E2636E)' : 'inherit',
      fontWeight: isBest || isWorst ? 600 : 400,
    };
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

      {/* Run Selection */}
      <div style={{
        padding: '1.5rem',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        border: '1px solid var(--border-subtle)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
            Select Runs to Compare
          </h3>
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            {selectedRunIds.length} / 10 selected
          </span>
        </div>
        
        {availableRuns.length === 0 ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
            No research runs available. Run a study in Strategy Lab first.
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 300, overflowY: 'auto' }}>
              {availableRuns.map((run) => (
                <label
                  key={run.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: '10px 12px',
                    backgroundColor: selectedRunIds.includes(run.id) ? 'rgba(59, 130, 246, 0.08)' : 'var(--surface-2)',
                    borderRadius: 6,
                    border: `1px solid ${selectedRunIds.includes(run.id) ? 'var(--primary)' : 'var(--border-subtle)'}`,
                    cursor: 'pointer',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedRunIds.includes(run.id)}
                    onChange={() => handleToggleRun(run.id)}
                    style={{ width: 16, height: 16, cursor: 'pointer' }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>
                      {run.instrument_id} @ {run.interval_minutes}m
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                      {run.trade_count} trades · {formatDate(run.created_at)}
                    </div>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                    {run.id.slice(0, 8)}
                  </div>
                </label>
              ))}
            </div>
            
            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button
                onClick={() => void handleCompare()}
                disabled={selectedRunIds.length < 2 || loading}
                style={{
                  padding: '8px 16px',
                  backgroundColor: selectedRunIds.length >= 2 ? 'var(--primary)' : 'var(--surface-2)',
                  color: selectedRunIds.length >= 2 ? 'white' : 'var(--text-secondary)',
                  border: 'none',
                  borderRadius: 6,
                  fontSize: 13,
                  fontWeight: 500,
                  cursor: selectedRunIds.length >= 2 && !loading ? 'pointer' : 'not-allowed',
                  opacity: loading ? 0.6 : 1,
                }}
              >
                {loading ? 'Comparing...' : 'Compare Selected'}
              </button>
              
              {selectedRunIds.length > 0 && (
                <button
                  onClick={handleClearSelection}
                  style={{
                    padding: '8px 16px',
                    backgroundColor: 'var(--surface-2)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: 'pointer',
                  }}
                >
                  Clear Selection
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {/* Comparison Results */}
      {comparison && comparison.comparisons.length > 0 && (
        <div style={{
          padding: '1.5rem',
          backgroundColor: 'var(--surface-1)',
          borderRadius: 8,
          border: '1px solid var(--border-subtle)',
        }}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16, margin: '0 0 16px 0' }}>
            Comparison Results
          </h3>
          
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '2px solid var(--border-subtle)' }}>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Run ID</th>
                  <th style={{ textAlign: 'left', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Instrument</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Trades</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Win Rate</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Total PnL</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Sharpe</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Max DD</th>
                </tr>
              </thead>
              <tbody>
                {comparison.comparisons.map((run) => (
                  <tr key={run.run_id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 12px', fontFamily: 'monospace', fontSize: 12, color: 'var(--text-primary)' }}>
                      {run.run_id.slice(0, 8)}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                      {run.instrument_id} @ {run.interval_minutes}m
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                      {run.total_trades}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', ...getMetricStyle(run.run_id, 'win_rate') }}>
                      {formatPercentFraction(run.win_rate)}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', ...getMetricStyle(run.run_id, 'pnl') }}>
                      {formatMoney(run.total_pnl)}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', ...getMetricStyle(run.run_id, 'sharpe') }}>
                      {formatNumber(run.sharpe, 3)}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', ...getMetricStyle(run.run_id, 'max_drawdown') }}>
                      {formatPercentPoints(run.max_drawdown_pct, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
