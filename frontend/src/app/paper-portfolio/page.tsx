'use client';

import { useEffect, useState, type CSSProperties } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import AppShell from '@/components/shell/AppShell';
import { statusPillClass } from '@/lib/status-styles';
import { formatDate, formatDateTime } from '@/lib/format';
import {
  label,
  ORDER_STATUS_LABELS,
  PORTFOLIO_STATUS_LABELS,
  RECORD_TYPE_LABELS,
  SIDE_LABELS,
} from '@/lib/copy';

interface Portfolio {
  id: string;
  status: string;
  initial_virtual_cash: number;
  current_virtual_cash: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number | null;
  currency: string;
  created_at: string;
  closed_at: string | null;
}

interface JournalRow {
  record_type: string;
  record_classification: string;
  order_state: string;
  position_status: string | null;
  portfolio_id: string;
  currency: string;
  asset_id: string;
  side: string;
  order_status: string;
  quantity: number | null;
  execution_price_before_costs: number | null;
  execution_date: string | null;
  order_created_at_utc: string | null;
  simulated_fill_reason: string | null;
}

export default function PaperPortfolioListPage() {
  const router = useRouter();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cashInput, setCashInput] = useState('100000');
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState<'portfolios' | 'journal'>('portfolios');
  const [journalRows, setJournalRows] = useState<JournalRow[]>([]);
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalError, setJournalError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadPortfolios();
  }, [router]);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('view');
    setView(requested === 'journal' ? 'journal' : 'portfolios');
  }, []);

  useEffect(() => {
    if (view !== 'journal') return;
    let cancelled = false;
    (async () => {
      setJournalLoading(true);
      setJournalError('');
      try {
        const list: Portfolio[] = await api.paperPortfolio.list();
        const packages = await Promise.all(
          list.map((p) => api.paperPortfolio.journalJson(p.id))
        );
        const merged: JournalRow[] = packages.flatMap((pkg: any) =>
          (pkg.records || []).map((r: any) => ({ ...r, currency: pkg.currency }))
        );
        merged.sort((a, b) =>
          String(b.order_created_at_utc ?? '').localeCompare(String(a.order_created_at_utc ?? ''))
        );
        if (!cancelled) setJournalRows(merged.slice(0, 200));
      } catch (err: any) {
        if (!cancelled) setJournalError(err.message || 'The records could not be loaded');
      } finally {
        if (!cancelled) setJournalLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [view]);

  const loadPortfolios = async () => {
    try {
      const data = await api.paperPortfolio.list();
      setPortfolios(data);
    } catch (err: any) {
      setError(err.message || 'The portfolios could not be loaded');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    const cash = parseFloat(cashInput);
    if (isNaN(cash) || cash <= 0) {
      setError('Enter a valid amount');
      return;
    }
    setCreating(true);
    setError('');
    try {
      const p = await api.paperPortfolio.create({ initial_virtual_cash: cash });
      router.push(`/paper-portfolio/${p.id}`);
    } catch (err: any) {
      setError(err.message || 'The portfolio could not be created');
    } finally {
      setCreating(false);
    }
  };

  const tabStyle = (selected: boolean): CSSProperties => ({
    padding: '0.4rem 0.9rem',
    borderRadius: '999px',
    border: `1px solid ${selected ? 'var(--primary)' : 'var(--border-strong)'}`,
    background: selected ? 'rgba(59, 130, 246, 0.16)' : 'transparent',
    color: selected ? 'var(--text-white)' : 'var(--text-secondary)',
    textDecoration: 'none',
    fontSize: '0.875rem',
  });

  const viewTabs = (
    <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem' }}>
      <Link href="/paper-portfolio" style={tabStyle(view === 'portfolios')}>
        Portfolios
      </Link>
      <Link href="/paper-portfolio?view=journal" style={tabStyle(view === 'journal')}>
        Records &amp; results
      </Link>
    </div>
  );

  if (view === 'journal') {
    return (
      <AppShell active="journal">
        <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '2rem' }}>
          <h1 style={{ fontSize: '1.8rem', marginBottom: '0.5rem' }}>Records &amp; results</h1>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
            Orders and fills from your paper portfolios. Backtest results live with the
            experiment that produced them — open that experiment to review them.
          </p>
          {viewTabs}
          {journalError && (
            <div
              data-testid="pp-journal-error"
              role="alert"
              style={{ color: 'var(--negative)', marginBottom: '1rem' }}
            >
              {journalError}
            </div>
          )}
          {journalLoading ? (
            <div
              role="status"
              style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}
            >
              Loading…
            </div>
          ) : journalRows.length === 0 ? (
            <div
              data-testid="pp-journal-empty"
              style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}
            >
              No orders or fills yet.
            </div>
          ) : (
            <div style={{ overflowX: 'auto', border: '1px solid var(--border-strong)', borderRadius: '8px' }}>
              <table data-testid="pp-journal-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                <thead>
                  <tr style={{ background: 'var(--surface-2)', textAlign: 'left' }}>
                    {['Created', 'Portfolio', 'Type', 'Asset', 'Side', 'Status', 'Quantity', 'Fill price', 'Note'].map((h) => (
                      <th key={h} style={{ padding: '0.6rem 0.75rem', color: 'var(--text-secondary)', fontWeight: 500, whiteSpace: 'nowrap' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {journalRows.map((row, idx) => (
                    <tr key={`${row.portfolio_id}-${row.record_type}-${idx}`} style={{ borderTop: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '0.6rem 0.75rem', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                        {row.order_created_at_utc
                          ? formatDateTime(row.order_created_at_utc)
                          : '—'}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem', fontFamily: 'var(--font-code)' }}>
                        <Link href={`/paper-portfolio/${row.portfolio_id}`} style={{ color: 'var(--primary)', textDecoration: 'none' }}>
                          {row.portfolio_id.slice(0, 8)}
                        </Link>
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>
                        {label(RECORD_TYPE_LABELS, row.record_type)}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem', fontFamily: 'var(--font-code)' }}>{row.asset_id}</td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>{label(SIDE_LABELS, row.side)}</td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>{label(ORDER_STATUS_LABELS, row.order_status)}</td>
                      <td style={{ padding: '0.6rem 0.75rem', textAlign: 'right' }}>{row.quantity ?? '—'}</td>
                      <td style={{ padding: '0.6rem 0.75rem', textAlign: 'right', fontFamily: 'var(--font-code)' }}>
                        {row.execution_price_before_costs != null
                          ? `${row.currency} ${row.execution_price_before_costs}`
                          : '—'}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem', color: 'var(--text-secondary)' }}>
                        {row.simulated_fill_reason || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </AppShell>
    );
  }

  if (loading) {
    return (
      <AppShell active="portfolios">
        <div role="status" style={{ padding: '2rem', textAlign: 'center' }}>
          Loading…
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell active="portfolios">
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '2rem' }}>
      <div style={{ marginBottom: '1rem' }}>
        <Link href="/dashboard" style={{ color: 'var(--primary)', textDecoration: 'none' }}>
          &larr; Back to my experiments
        </Link>
      </div>
      {viewTabs}

      <h1 style={{ fontSize: '1.8rem', marginBottom: '0.5rem' }}>Paper portfolios</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
        Virtual portfolios for research and behavioural records — not a trading game.
      </p>

      <div className="notice notice-warn" style={{ marginBottom: '1.5rem', fontSize: '0.875rem' }}>
        <strong>Disclaimer:</strong><br />
        Paper-trading records come from simulated executions under stated assumptions and are
        not investment advice. They cannot reproduce the psychology of real capital, full
        market liquidity, partial fills, exchange outages, market impact, or real broker
        execution.
      </div>

      <div style={{
        background: 'var(--surface-1)', border: '1px solid var(--border-strong)',
        borderRadius: '8px', padding: '1.5rem', marginBottom: '2rem',
      }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>Create a new portfolio</h2>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'end' }}>
          <div style={{ flex: 1 }}>
            <label htmlFor="pp-new-cash" style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
              Initial virtual cash (USD)
            </label>
            <input
              id="pp-new-cash"
              type="number"
              data-testid="pp-new-cash"
              value={cashInput}
              onChange={(e) => setCashInput(e.target.value)}
              style={{
                width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)',
                background: 'var(--surface-2)', color: 'var(--text-primary)',
                borderRadius: '4px', fontSize: '1rem',
              }}
            />
          </div>
          <button
            data-testid="pp-create-portfolio"
            onClick={handleCreate}
            disabled={creating}
            style={{
              padding: '0.5rem 1.5rem', background: 'var(--primary-solid)', color: 'var(--text-white)',
              border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '1rem',
              opacity: creating ? 0.6 : 1,
            }}
          >
            {creating ? 'Creating…' : 'Create'}
          </button>
        </div>
        <p data-testid="pp-link-hint" style={{ marginTop: '0.75rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
          A portfolio created here records only the decisions you make by hand. To let{' '}
          <strong>a locked strategy&apos;s rules</strong> enter and exit automatically — booking
          orders, fills and a decision journal — create and link a paper portfolio from that
          experiment&apos;s Forward simulation page.
        </p>
        {error && (
          <div
            data-testid="pp-error"
            role="alert"
            style={{ color: 'var(--negative)', marginTop: '0.5rem', fontSize: '0.875rem' }}
          >
            {error}
          </div>
        )}
      </div>

      <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>Your portfolios</h2>

      {portfolios.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-secondary)' }}>
          No portfolios yet — create one above.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {portfolios.map((p) => {
            const statusLabel = label(PORTFOLIO_STATUS_LABELS, p.status);
            const hasUnrealized = typeof p.total_unrealized_pnl === 'number';
            const cashReturnPct = ((p.current_virtual_cash - p.initial_virtual_cash) / p.initial_virtual_cash * 100);
            return (
              <Link
                key={p.id}
                href={`/paper-portfolio/${p.id}`}
                style={{
                  display: 'block', padding: '1.5rem', background: 'var(--surface-1)',
                  border: '1px solid var(--border-strong)', borderRadius: '8px',
                  textDecoration: 'none', color: 'inherit',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                      <span className={statusPillClass(p.status)}>
                        {statusLabel}
                      </span>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        {formatDate(p.created_at)}
                      </span>
                    </div>
                    <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>
                      {p.currency} {p.current_virtual_cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </div>
                    <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                      Initial: {p.currency} {p.initial_virtual_cash.toLocaleString()}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{
                      fontSize: '1.1rem', fontWeight: 600,
                      color: cashReturnPct >= 0 ? 'var(--positive)' : 'var(--negative)',
                    }}>
                      {cashReturnPct >= 0 ? '+' : ''}{cashReturnPct.toFixed(2)}%
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Cash return (excl. unrealized)</div>
                    <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                      Realized {p.total_realized_pnl >= 0 ? '+' : ''}{p.total_realized_pnl.toFixed(2)}
                      {' · '}
                      Unrealized {hasUnrealized
                        ? `${(p.total_unrealized_pnl as number) >= 0 ? '+' : ''}${(p.total_unrealized_pnl as number).toFixed(2)}`
                        : 'Cannot be computed'}
                    </div>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
    </AppShell>
  );
}
