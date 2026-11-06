'use client';

import { useMemo, useState, type CSSProperties } from 'react';
import type { JournalTrade } from './JournalDashboard';

interface Props {
  trades: JournalTrade[];
  totalCount: number;
}

const PAGE_SIZE = 50;

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
  cursor: 'pointer',
  userSelect: 'none',
};

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '--';
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value);
  if (!match) return value;
  return `${match[1]} ${match[2]}`;
}

function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '--';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  return num.toFixed(2);
}

function formatPct(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '--';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  return `${num.toFixed(2)}%`;
}

function pnlColor(value: string | null | undefined): string | undefined {
  if (value === null || value === undefined || value === '') return undefined;
  const num = Number(value);
  if (!Number.isFinite(num)) return undefined;
  if (num > 0) return 'var(--positive, #36C7A0)';
  if (num < 0) return 'var(--negative, #E2636E)';
  return undefined;
}

type SortKey = 'entry_time' | 'instrument_id' | 'rule_label_zh' | 'direction' | 'entry_price' | 'exit_price' | 'net_pnl' | 'mfe_pct' | 'mae_pct' | 'exit_reason';
type SortDir = 'asc' | 'desc';

export default function JournalTradesTable({ trades, totalCount }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('entry_time');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [page, setPage] = useState(0);

  const sortedTrades = useMemo(() => {
    const sorted = [...trades];
    sorted.sort((a, b) => {
      let va: string | number | null = null;
      let vb: string | number | null = null;

      switch (sortKey) {
        case 'entry_time':
          va = a.entry_time ?? '';
          vb = b.entry_time ?? '';
          break;
        case 'instrument_id':
          va = a.instrument_id ?? '';
          vb = b.instrument_id ?? '';
          break;
        case 'rule_label_zh':
          va = a.rule_label_zh ?? '';
          vb = b.rule_label_zh ?? '';
          break;
        case 'direction':
          va = a.direction ?? '';
          vb = b.direction ?? '';
          break;
        case 'entry_price':
          va = a.entry_price ? Number(a.entry_price) : 0;
          vb = b.entry_price ? Number(b.entry_price) : 0;
          break;
        case 'exit_price':
          va = a.exit_price ? Number(a.exit_price) : 0;
          vb = b.exit_price ? Number(b.exit_price) : 0;
          break;
        case 'net_pnl':
          va = a.net_pnl ? Number(a.net_pnl) : 0;
          vb = b.net_pnl ? Number(b.net_pnl) : 0;
          break;
        case 'mfe_pct':
          va = a.mfe_pct ? Number(a.mfe_pct) : 0;
          vb = b.mfe_pct ? Number(b.mfe_pct) : 0;
          break;
        case 'mae_pct':
          va = a.mae_pct ? Number(a.mae_pct) : 0;
          vb = b.mae_pct ? Number(b.mae_pct) : 0;
          break;
        case 'exit_reason':
          va = a.exit_reason ?? '';
          vb = b.exit_reason ?? '';
          break;
      }

      let cmp = 0;
      if (typeof va === 'number' && typeof vb === 'number') {
        cmp = va - vb;
      } else {
        cmp = String(va ?? '').localeCompare(String(vb ?? ''));
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return sorted;
  }, [trades, sortKey, sortDir]);

  const pagedTrades = sortedTrades.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
    setPage(0);
  };

  const sortIndicator = (key: SortKey) => {
    if (sortKey !== key) return '';
    return sortDir === 'asc' ? ' \u25B2' : ' \u25BC';
  };

  if (trades.length === 0) {
    return (
      <div
        role="status"
        style={{
          padding: '2rem',
          textAlign: 'center',
          color: 'var(--text-secondary)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 6,
        }}
      >
        No trades match the current filters.
      </div>
    );
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }} aria-live="polite">
        Showing {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, totalCount)} of {totalCount} trades
      </div>
      <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', border: '1px solid var(--border-subtle)', borderRadius: 6, marginBottom: 4 }}>
        <table role="table" aria-label="Trading journal" style={{ width: '100%', borderCollapse: 'collapse', minWidth: 800 }}>
          <thead>
            <tr>
              <th scope="col" style={{ ...headCellStyle, textAlign: 'left' }} onClick={() => handleSort('entry_time')}>
                Date{sortIndicator('entry_time')}
              </th>
              <th scope="col" style={{ ...headCellStyle, textAlign: 'left' }} onClick={() => handleSort('instrument_id')}>
                Instrument{sortIndicator('instrument_id')}
              </th>
              <th scope="col" style={{ ...headCellStyle, textAlign: 'left' }} onClick={() => handleSort('rule_label_zh')}>
                Skill{sortIndicator('rule_label_zh')}
              </th>
              <th scope="col" style={{ ...headCellStyle, textAlign: 'left' }} onClick={() => handleSort('direction')}>
                Dir{sortIndicator('direction')}
              </th>
              <th scope="col" style={headCellStyle} onClick={() => handleSort('entry_price')}>
                Entry{sortIndicator('entry_price')}
              </th>
              <th scope="col" style={headCellStyle} onClick={() => handleSort('exit_price')}>
                Exit{sortIndicator('exit_price')}
              </th>
              <th scope="col" style={headCellStyle} onClick={() => handleSort('net_pnl')}>
                Net P&L{sortIndicator('net_pnl')}
              </th>
              <th scope="col" style={headCellStyle} onClick={() => handleSort('mfe_pct')}>
                MFE%{sortIndicator('mfe_pct')}
              </th>
              <th scope="col" style={headCellStyle} onClick={() => handleSort('mae_pct')}>
                MAE%{sortIndicator('mae_pct')}
              </th>
              <th scope="col" style={{ ...headCellStyle, textAlign: 'left' }} onClick={() => handleSort('exit_reason')}>
                Exit Reason{sortIndicator('exit_reason')}
              </th>
            </tr>
          </thead>
          <tbody>
            {pagedTrades.map((trade) => (
              <tr key={trade.trade_id}>
                <td style={{ ...cellStyle, textAlign: 'left' }}>
                  {formatDateTime(trade.entry_time)}
                </td>
                <td style={{ ...cellStyle, textAlign: 'left', fontFamily: 'var(--font-code)', fontSize: 11 }}>
                  {trade.instrument_id}
                </td>
                <td style={{ ...cellStyle, textAlign: 'left' }}>
                  {trade.rule_label_zh ?? trade.rule_id}
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                    {trade.rule_family}
                  </div>
                </td>
                <td style={{ ...cellStyle, textAlign: 'left' }}>
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      padding: '2px 8px',
                      borderRadius: 4,
                      backgroundColor:
                        trade.direction === 'long'
                          ? 'color-mix(in srgb, var(--positive) 15%, transparent)'
                          : 'color-mix(in srgb, var(--negative) 15%, transparent)',
                      color:
                        trade.direction === 'long'
                          ? 'var(--positive, #36C7A0)'
                          : 'var(--negative, #E2636E)',
                      fontSize: 12,
                      fontWeight: 500,
                    }}
                  >
                    <span aria-hidden="true">
                      {trade.direction === 'long' ? '\u2191' : '\u2193'}
                    </span>
                    {trade.direction === 'long' ? 'Long' : 'Short'}
                  </span>
                </td>
                <td style={cellStyle}>{formatMoney(trade.entry_price)}</td>
                <td style={cellStyle}>{formatMoney(trade.exit_price)}</td>
                <td
                  style={{
                    ...cellStyle,
                    fontWeight: 600,
                    color: pnlColor(trade.net_pnl),
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    justifyContent: 'flex-end',
                  }}
                  aria-label={`Net P&L ${formatMoney(trade.net_pnl)} ${Number(trade.net_pnl) > 0 ? 'profit' : Number(trade.net_pnl) < 0 ? 'loss' : 'break even'}`}
                >
                  <span style={{ fontSize: 14 }} aria-hidden="true">
                    {Number(trade.net_pnl) > 0
                      ? '\u2191'
                      : Number(trade.net_pnl) < 0
                        ? '\u2193'
                        : '\u2014'}
                  </span>
                  ${formatMoney(trade.net_pnl)}
                </td>
                <td style={cellStyle}>{formatPct(trade.mfe_pct)}</td>
                <td style={cellStyle}>{formatPct(trade.mae_pct)}</td>
                <td style={{ ...cellStyle, textAlign: 'left' }}>
                  {trade.exit_reason}
                  {trade.exit_label_zh && (
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      {trade.exit_label_zh}
                    </div>
                  )}
                  {trade.outcome && (
                    <span
                      style={{
                        display: 'inline-block',
                        marginLeft: 8,
                        fontSize: 10,
                        fontWeight: 600,
                        padding: '1px 6px',
                        borderRadius: 3,
                        backgroundColor:
                          trade.outcome === 'win'
                            ? 'color-mix(in srgb, var(--positive) 15%, transparent)'
                            : 'color-mix(in srgb, var(--negative) 15%, transparent)',
                        color:
                          trade.outcome === 'win'
                            ? 'var(--positive, #36C7A0)'
                            : 'var(--negative, #E2636E)',
                        letterSpacing: '0.03em',
                        verticalAlign: 'middle',
                      }}
                      aria-label={`Outcome: ${trade.outcome}`}
                    >
                      {trade.outcome === 'win' ? 'WIN' : 'LOSS'}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile scroll hint */}
      <div
        className="mobile-scroll-hint"
        style={{
          display: 'none',
          fontSize: 12,
          color: 'var(--seed-muted, var(--text-secondary))',
          textAlign: 'center',
          padding: '8px 0',
        }}
      >
        ← Scroll to see all columns →
      </div>
      <style>{`
        @media (max-width: 768px) {
          .mobile-scroll-hint { display: block !important; }
        }
      `}</style>

      {/* Pagination */}
      {totalPages > 1 && (
        <div role="navigation" aria-label="Table pagination" style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12 }}>
          <button
            type="button"
            aria-label="Go to previous page of trades"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
            style={{
              padding: '4px 12px',
              borderRadius: 4,
              border: '1px solid var(--border-strong)',
              background: 'transparent',
              color: page === 0 ? 'var(--text-muted)' : 'var(--text-primary)',
              fontSize: 12,
              cursor: page === 0 ? 'default' : 'pointer',
              opacity: page === 0 ? 0.5 : 1,
            }}
          >
            Previous
          </button>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Page {page + 1} of {totalPages}
          </span>
          <button
            type="button"
            aria-label="Go to next page of trades"
            disabled={page >= totalPages - 1}
            onClick={() => setPage(page + 1)}
            style={{
              padding: '4px 12px',
              borderRadius: 4,
              border: '1px solid var(--border-strong)',
              background: 'transparent',
              color: page >= totalPages - 1 ? 'var(--text-muted)' : 'var(--text-primary)',
              fontSize: 12,
              cursor: page >= totalPages - 1 ? 'default' : 'pointer',
              opacity: page >= totalPages - 1 ? 0.5 : 1,
            }}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
