'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { statusPillClass } from '@/lib/status-styles';
import AppShell from '@/components/shell/AppShell';
import { formatDate, formatDateTime } from '@/lib/format';
import {
  ACTOR_LABELS,
  CONFIDENCE_LABELS,
  DECISION_TYPE_LABELS,
  EXIT_REASON_LABELS,
  ORDER_STATUS_LABELS,
  OUTCOME_STATUS_LABELS,
  PORTFOLIO_STATUS_LABELS,
  POSITION_STATUS_LABELS,
  PRICE_MODE_LABELS,
  REJECTION_LABELS,
  SIDE_LABELS,
  label,
} from '@/lib/copy';

interface ValuationBasis {
  mode: string;
  mode_label_zh: string;
  provider: string;
  data_as_of: string | null;
  unpriced_assets: string[];
  note: string;
}

interface Portfolio {
  id: string;
  status: string;
  simulation_session_id: string | null;
  initial_virtual_cash: number;
  current_virtual_cash: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number | null;
  valuation?: ValuationBasis | null;
  currency: string;
  created_at: string;
}

interface Position {
  id: string;
  asset_id: string;
  quantity: number;
  average_entry_price: number;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  status: string;
  opened_at: string;
  closed_at: string | null;
}

interface Order {
  id: string;
  decision_id: string | null;
  asset_id: string;
  side: string;
  quantity: number;
  status: string;
  rationale: string | null;
  created_at: string;
  actor_type: string;
  data_cutoff_at: string | null;
}

interface Decision {
  id: string;
  actor_type: string;
  decision_type: string;
  decision_time: string;
  rationale: string | null;
  expected_outcome: string | null;
  expected_timeframe_days: number | null;
  self_reported_confidence: string;
  falsification_condition: string | null;
  outcome_status: string;
  risk_flags: any;
}

/** Accent per decision type; the label text comes from the shared map. */
const DECISION_TYPE_STYLES: Record<string, { color: string; bg: string }> = {
  no_action: { color: 'var(--text-secondary)', bg: 'var(--surface-2)' },
  entry: { color: 'var(--positive)', bg: 'rgba(34,197,94,0.14)' },
  exit: { color: 'var(--negative)', bg: 'rgba(239,68,68,0.12)' },
  pause: { color: 'var(--warning)', bg: 'rgba(245,158,11,0.14)' },
  retire: { color: 'var(--text-secondary)', bg: 'var(--surface-2)' },
  override_request: { color: 'var(--warning)', bg: 'rgba(245,158,11,0.14)' },
};

const TAB_LABELS: Record<string, string> = {
  overview: 'Overview',
  positions: 'Positions',
  orders: 'Orders',
  decisions: 'Decisions',
};

export default function PaperPortfolioDetailPage() {
  const router = useRouter();
  const params = useParams();
  const portfolioId = params.id as string;

  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  // When this portfolio is linked to a simulation session, surface that session's
  // timeframe/provider so an intraday synthetic replay is labelled as such here too.
  const [linkedSession, setLinkedSession] = useState<{ timeframe: string | null; provider_id: string | null } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<'overview' | 'positions' | 'orders' | 'decisions'>('overview');

  const [orderForm, setOrderForm] = useState({
    // Empty on purpose: this lane only accepts DEMO-* synthetic assets, so the
    // choice must be made deliberately rather than arriving pre-filled.
    asset_id: '',
    side: 'BUY',
    quantity: '100',
    rationale: '',
    expected_outcome: '',
    expected_timeframe_days: '',
    self_reported_confidence: 'medium',
    falsification_condition: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  // One key per submission attempt: reused when the user retries the very same
  // order after a lost response, cleared as soon as any field changes.
  const [pendingIdempotencyKey, setPendingIdempotencyKey] = useState<string | null>(null);

  const setOrderField = (field: keyof typeof orderForm, value: string) => {
    setOrderForm((prev) => ({ ...prev, [field]: value }));
    setPendingIdempotencyKey(null);
  };

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadData();
  }, [portfolioId, router]);

  const loadData = async () => {
    try {
      const [p, pos, ord, dec] = await Promise.all([
        api.paperPortfolio.get(portfolioId),
        api.paperPortfolio.positions(portfolioId),
        api.paperPortfolio.orders(portfolioId),
        api.paperPortfolio.decisions(portfolioId),
      ]);
      setPortfolio(p);
      setPositions(pos);
      setOrders(ord);
      setDecisions(dec);
      // Clear a banner left over from an earlier failed load; callers that need
      // to report a refusal set their message after this resolves.
      setError('');
      if (p.simulation_session_id) {
        try {
          const s = await api.simulations.get(p.simulation_session_id);
          setLinkedSession({ timeframe: s.timeframe ?? null, provider_id: s.provider_id ?? null });
        } catch {
          setLinkedSession(null);
        }
      }
    } catch (err: any) {
      setError(err.message || 'The portfolio could not be loaded');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateOrder = async () => {
    if (!portfolio) return;
    setSubmitting(true);
    setError('');
    const idempotencyKey = pendingIdempotencyKey
      ?? `ui-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    if (!pendingIdempotencyKey) setPendingIdempotencyKey(idempotencyKey);
    try {
      await api.paperPortfolio.createOrder(portfolioId, {
        asset_id: orderForm.asset_id,
        side: orderForm.side,
        quantity: parseFloat(orderForm.quantity),
        rationale: orderForm.rationale || undefined,
        expected_outcome: orderForm.expected_outcome || undefined,
        expected_timeframe_days: orderForm.expected_timeframe_days ? parseInt(orderForm.expected_timeframe_days) : undefined,
        self_reported_confidence: orderForm.self_reported_confidence,
        falsification_condition: orderForm.falsification_condition || undefined,
        idempotency_key: idempotencyKey,
      });
      await loadData();
      setPendingIdempotencyKey(null);
      setOrderForm({ ...orderForm, rationale: '', expected_outcome: '', falsification_condition: '' });
    } catch (err: any) {
      // A refused order is still persisted as a rejected row, so reload first:
      // otherwise the refusal never reaches the orders journal until a manual
      // page reload. Setting the message afterwards keeps it authoritative.
      await loadData();
      setError(err.message || 'The order could not be created');
    } finally {
      setSubmitting(false);
    }
  };

  const handlePause = async () => {
    try {
      const p = await api.paperPortfolio.pause(portfolioId);
      setPortfolio(p);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleResume = async () => {
    try {
      const p = await api.paperPortfolio.resume(portfolioId);
      setPortfolio(p);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleRetire = async () => {
    if (
      !confirm(
        'Retire this portfolio? This cannot be undone and the portfolio can no longer be changed. ' +
          'Retiring only changes status: no position is sold, no order is placed, and holdings and cash stay as they are.',
      )
    )
      return;
    try {
      const p = await api.paperPortfolio.retire(portfolioId);
      setPortfolio(p);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDownloadCsv = async () => {
    setDownloading(true);
    try {
      const text = await api.paperPortfolio.journalCsv(portfolioId);
      const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `trade-journal-${portfolioId}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <AppShell active="portfolios">
        <div role="status" aria-live="polite" style={{ padding: '2rem', textAlign: 'center' }}>Loading…</div>
      </AppShell>
    );
  }

  if (!portfolio) {
    return (
      <AppShell active="portfolios">
        <div style={{ padding: '2rem' }}>Portfolio not found</div>
      </AppShell>
    );
  }

  const unrealized = portfolio.total_unrealized_pnl;
  const hasUnrealized = typeof unrealized === 'number';
  // Cash-only return: open positions are not liquidated, so this is not a total return.
  const cashReturnPct = ((portfolio.current_virtual_cash - portfolio.initial_virtual_cash) / portfolio.initial_virtual_cash * 100);
  const valuation = portfolio.valuation ?? null;
  const openPositions = positions.filter(p => p.status === 'open');
  const closedPositions = positions.filter(p => p.status === 'closed');

  return (
    <AppShell active="portfolios">
    <div style={{ maxWidth: '1000px', margin: '0 auto', padding: '2rem' }}>
      <div style={{ marginBottom: '1.5rem' }}>
        <Link href="/paper-portfolio" style={{ color: 'var(--primary)', textDecoration: 'none' }}>
          &larr; Back to portfolios
        </Link>
      </div>

      <div style={{
        background: 'rgba(245, 158, 11, 0.12)', border: '1px solid var(--warning)', borderRadius: '8px',
        padding: '0.75rem', marginBottom: '1.5rem', fontSize: '0.8125rem', color: 'var(--text-primary)',
      }}>
        <strong>Disclaimer:</strong>{' '}
        Paper-trading records come from simulated executions under stated assumptions and are not
        investment advice. They cannot reproduce the psychology of real capital, full market
        liquidity, partial fills, exchange outages, market impact, or real broker execution.
      </div>

      {linkedSession && linkedSession.timeframe && linkedSession.timeframe !== '1d' && (
        <div
          data-testid="pp-intraday-badge"
          style={{
            background: 'rgba(139, 92, 246, 0.14)', border: '1px solid var(--purple)', borderRadius: '8px',
            padding: '0.75rem', marginBottom: '1.5rem', fontSize: '0.8125rem', color: 'var(--text-primary)',
          }}
        >
          <strong>Synthetic data · Historical replay · Not a live feed</strong>
          <span style={{ marginLeft: '8px' }}>
            This portfolio is linked to an intraday replay session (timeframe
            <span data-testid="pp-intraday-timeframe"> {linkedSession.timeframe} </span>
            {linkedSession.provider_id && (
              <>· source <span data-testid="pp-intraday-provider">{linkedSession.provider_id}</span></>
            )}
            ). The records below are simulated fills from a bar-by-bar historical replay — not a
            live feed and not real trading.
          </span>
        </div>
      )}

      {error && (
        <div data-testid="pp-error" role="alert" style={{
          background: 'rgba(239, 68, 68, 0.12)', border: '1px solid var(--negative)', borderRadius: '8px',
          padding: '0.75rem', marginBottom: '1rem', color: 'var(--text-primary)', fontSize: '0.875rem',
        }}>
          {error}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '1.5rem' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <h1 style={{ fontSize: '1.5rem', margin: 0 }}>Paper portfolio</h1>
            <span className={statusPillClass(portfolio.status)} style={{ padding: '0.25rem 0.75rem', fontSize: '0.75rem' }}>
              {label(PORTFOLIO_STATUS_LABELS, portfolio.status)}
            </span>
          </div>
          <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
            Created {formatDate(portfolio.created_at)}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            onClick={handleDownloadCsv}
            disabled={downloading}
            data-testid="pp-download-csv"
            style={{
              padding: '0.375rem 0.75rem', background: 'rgba(59,130,246,0.16)', color: 'var(--primary-text)',
              border: '1px solid var(--primary)', borderRadius: '4px',
              cursor: downloading ? 'default' : 'pointer', fontSize: '0.8125rem',
              opacity: downloading ? 0.6 : 1,
            }}>
            {downloading ? 'Exporting…' : 'Download trade journal CSV'}
          </button>
          {portfolio.status === 'active' && (
            <>
              <button onClick={handlePause} style={{
                padding: '0.375rem 0.75rem', background: 'rgba(245,158,11,0.14)', color: 'var(--warning)',
                border: '1px solid rgba(245,158,11,0.34)', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8125rem',
              }}>
                Pause
              </button>
              <button onClick={handleRetire} style={{
                padding: '0.375rem 0.75rem', background: 'rgba(239,68,68,0.12)', color: 'var(--negative)',
                border: '1px solid rgba(239,68,68,0.34)', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8125rem',
              }}>
                Retire
              </button>
            </>
          )}
          {portfolio.status === 'paused' && (
            <button onClick={handleResume} style={{
              padding: '0.375rem 0.75rem', background: 'rgba(34,197,94,0.14)', color: 'var(--positive)',
              border: '1px solid rgba(34,197,94,0.34)', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8125rem',
            }}>
              Resume
            </button>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
        <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Cash</div>
          <div data-testid="pp-cash" style={{ fontSize: '1.25rem', fontWeight: 600 }}>
            {portfolio.currency} {portfolio.current_virtual_cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </div>
        </div>
        <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Cash return (excl. unrealized)</div>
          <div data-testid="pp-cash-return" style={{ fontSize: '1.25rem', fontWeight: 600, color: cashReturnPct >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
            {cashReturnPct >= 0 ? '+' : ''}{cashReturnPct.toFixed(2)}%
          </div>
        </div>
        <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Open positions</div>
          <div data-testid="pp-open-position-count" style={{ fontSize: '1.25rem', fontWeight: 600 }}>{openPositions.length}</div>
        </div>
        <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Decisions</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{decisions.length}</div>
        </div>
      </div>

      <div
        data-testid="pp-pnl-breakdown"
        style={{
          background: 'var(--surface-1)', border: '1px solid var(--border-strong)', borderRadius: '8px',
          padding: '0.75rem 1rem', marginBottom: '1rem', fontSize: '0.8125rem',
        }}
      >
        <span data-testid="pp-realized-pnl">
          Realized P&amp;L {portfolio.currency} {portfolio.total_realized_pnl.toFixed(2)}
        </span>
        <span style={{ color: 'var(--text-muted)', margin: '0 0.5rem' }}>|</span>
        <span data-testid="pp-unrealized-pnl">
          Unrealized P&amp;L {hasUnrealized
            ? `${portfolio.currency} ${(unrealized as number).toFixed(2)}`
            : 'Cannot be computed (no usable price)'}
        </span>
      </div>

      {valuation && (
        <div
          data-testid="pp-valuation-basis"
          style={{
            background: 'var(--surface-1)', border: '1px solid var(--border-strong)', borderRadius: '8px',
            padding: '0.75rem 1rem', marginBottom: '1.5rem', fontSize: '0.75rem', color: 'var(--text-secondary)',
          }}
        >
          <strong>Valuation basis:</strong>{' '}
          <span data-testid="pp-valuation-provider">{valuation.provider}</span>{' '}
          (<span data-testid="pp-valuation-mode">{label(PRICE_MODE_LABELS, valuation.mode)}</span>)
          {' · '}Data as of <span data-testid="pp-valuation-as-of">{valuation.data_as_of ?? '—'}</span>
          <br />
          {valuation.mode === 'synthetic_fixture'
            ? 'Marked at built-in synthetic demo closing prices — not a live feed. '
            : ''}
          Realized and unrealized P&amp;L are net of commission; slippage is already inside the
          fill price.
          {valuation.unpriced_assets.length > 0
            ? ` No usable price for ${valuation.unpriced_assets.join(', ')}, so unrealized P&L cannot be computed.`
            : openPositions.length === 0
              ? ' No open positions, so unrealized P&L is 0.'
              : ''}
        </div>
      )}

      <div role="tablist" aria-label="Portfolio views" style={{ display: 'flex', gap: '0', marginBottom: '1.5rem', borderBottom: '1px solid var(--border-strong)' }}>
        {(['overview', 'positions', 'orders', 'decisions'] as const).map((tab) => (
          <button
            key={tab}
            id={`pp-tab-${tab}`}
            role="tab"
            aria-selected={activeTab === tab}
            aria-controls={`pp-panel-${tab}`}
            data-testid={`pp-tab-${tab}`}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '0.75rem 1.5rem', background: 'none', border: 'none',
              borderBottom: activeTab === tab ? '2px solid var(--primary)' : '2px solid transparent',
              color: activeTab === tab ? 'var(--primary)' : 'var(--text-secondary)',
              fontWeight: activeTab === tab ? 600 : 400,
              cursor: 'pointer', fontSize: '0.9375rem', marginBottom: '-2px',
            }}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div role="tabpanel" id="pp-panel-overview" aria-labelledby="pp-tab-overview">
          {portfolio.status !== 'retired' && portfolio.status !== 'completed' && (
            <div style={{
              background: 'var(--surface-1)', border: '1px solid var(--border-strong)',
              borderRadius: '8px', padding: '1.5rem', marginBottom: '1.5rem',
            }}>
              <h2 style={{ fontSize: '1.1rem', marginBottom: '1rem' }}>Add a simulated order (demo data lane)</h2>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
                <div>
                  <label htmlFor="pp-asset-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>Asset</label>
                  <select
                    id="pp-asset-input"
                    data-testid="pp-asset"
                    value={orderForm.asset_id}
                    onChange={(e) => setOrderField('asset_id', e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px' }}
                  >
                    <option value="" disabled>— Select an asset —</option>
                    <option value="DEMO-ALPHA">DEMO-ALPHA (demo data, synthetic)</option>
                    <option value="DEMO-BETA">DEMO-BETA (demo data, synthetic)</option>
                    <option value="DEMO-BENCHMARK">DEMO-BENCHMARK (demo data, synthetic)</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="pp-side-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>Side</label>
                  <select
                    id="pp-side-input"
                    data-testid="pp-side"
                    value={orderForm.side}
                    onChange={(e) => setOrderField('side', e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px' }}
                  >
                    <option value="BUY">{SIDE_LABELS.BUY}</option>
                    <option value="SELL">{SIDE_LABELS.SELL}</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="pp-quantity-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>Quantity</label>
                  <input
                    id="pp-quantity-input"
                    type="number"
                    data-testid="pp-quantity"
                    value={orderForm.quantity}
                    onChange={(e) => setOrderField('quantity', e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px' }}
                  />
                </div>
                <div>
                  <label htmlFor="pp-confidence-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>Confidence</label>
                  <select
                    id="pp-confidence-input"
                    data-testid="pp-confidence"
                    value={orderForm.self_reported_confidence}
                    onChange={(e) => setOrderField('self_reported_confidence', e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px' }}
                  >
                    <option value="low">{CONFIDENCE_LABELS.low}</option>
                    <option value="medium">{CONFIDENCE_LABELS.medium}</option>
                    <option value="high">{CONFIDENCE_LABELS.high}</option>
                  </select>
                </div>
                <div style={{ gridColumn: '1 / -1' }}>
                  <label htmlFor="pp-rationale-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>
                    Rationale <span style={{ color: 'var(--negative)' }}>*</span>
                  </label>
                  <textarea
                    id="pp-rationale-input"
                    data-testid="pp-rationale"
                    value={orderForm.rationale}
                    onChange={(e) => setOrderField('rationale', e.target.value)}
                    rows={2}
                    aria-required="true"
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px', resize: 'vertical' }}
                    placeholder="Why are you making this decision?"
                  />
                </div>
                <div style={{ gridColumn: '1 / -1' }}>
                  <label htmlFor="pp-expected-outcome-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>
                    Expected outcome <span style={{ color: 'var(--negative)' }}>*</span>
                  </label>
                  <textarea
                    id="pp-expected-outcome-input"
                    data-testid="pp-expected-outcome"
                    value={orderForm.expected_outcome}
                    onChange={(e) => setOrderField('expected_outcome', e.target.value)}
                    rows={2}
                    aria-required="true"
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px', resize: 'vertical' }}
                    placeholder="What do you expect to happen?"
                  />
                </div>
                <div>
                  <label htmlFor="pp-timeframe-days-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>Timeframe (days)</label>
                  <input
                    id="pp-timeframe-days-input"
                    type="number"
                    data-testid="pp-timeframe-days"
                    value={orderForm.expected_timeframe_days}
                    onChange={(e) => setOrderField('expected_timeframe_days', e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px' }}
                    placeholder="e.g. 30"
                  />
                </div>
                <div>
                  <label htmlFor="pp-falsification-input" style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>Falsification condition</label>
                  <input
                    id="pp-falsification-input"
                    type="text"
                    data-testid="pp-falsification"
                    value={orderForm.falsification_condition}
                    onChange={(e) => setOrderField('falsification_condition', e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid var(--border-strong)', background: 'var(--surface-2)', color: 'var(--text-primary)', borderRadius: '4px' }}
                    placeholder="What would prove this decision wrong?"
                  />
                </div>
              </div>
              <p style={{ margin: '1rem 0 0', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
                This lane accepts DEMO-* demo assets only, and an order fills at the demo price as
                soon as it is submitted — this is demo data, not a real quote. For real instruments
                (for example US:AAPL), submit a manual simulated order from the workspace: that lane
                books a pending order that fills on the next executable bar, keeping decision time
                and execution time separate.
              </p>
              <button
                data-testid="pp-submit-order"
                onClick={handleCreateOrder}
                disabled={
                  submitting || !orderForm.asset_id || !orderForm.rationale || !orderForm.expected_outcome
                }
                style={{
                  marginTop: '1rem', padding: '0.5rem 1.5rem', background: 'var(--primary-solid)', color: 'var(--text-white)',
                  border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '0.9375rem',
                  opacity: (submitting || !orderForm.asset_id || !orderForm.rationale || !orderForm.expected_outcome) ? 0.5 : 1,
                }}
              >
                {submitting ? 'Placing…' : 'Place order'}
              </button>
            </div>
          )}

          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Open positions</h2>
          {openPositions.length === 0 ? (
            <div style={{ padding: '1rem', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>No open positions yet.</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '1.5rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>Asset</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Quantity</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Average cost</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Unrealized P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((pos) => (
                  <tr key={pos.id} data-testid={`pp-open-position-${pos.asset_id}`} style={{ borderBottom: '1px solid var(--surface-2)' }}>
                    <td style={{ padding: '0.5rem', fontWeight: 500 }}>{pos.asset_id}</td>
                    <td data-testid={`pp-open-position-qty-${pos.asset_id}`} style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.quantity}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.average_entry_price.toFixed(4)}</td>
                    <td
                      data-testid={`pp-open-position-unrealized-${pos.asset_id}`}
                      title={pos.unrealized_pnl === null ? 'No usable price, so unrealized P&L cannot be computed' : undefined}
                      style={{
                        padding: '0.5rem', textAlign: 'right',
                        color: pos.unrealized_pnl === null
                          ? 'var(--text-secondary)'
                          : (pos.unrealized_pnl >= 0 ? 'var(--positive)' : 'var(--negative)'),
                      }}
                    >
                      {pos.unrealized_pnl === null ? '—' : pos.unrealized_pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {activeTab === 'positions' && (
        <div role="tabpanel" id="pp-panel-positions" aria-labelledby="pp-tab-positions">
          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>All positions</h2>
          {positions.length === 0 ? (
            <div style={{ padding: '1rem', color: 'var(--text-secondary)' }}>No positions yet.</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>Asset</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Quantity</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Average cost</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Realized P&amp;L</th>
                  <th style={{ textAlign: 'center', padding: '0.5rem', fontSize: '0.8125rem' }}>Status</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Opened</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => (
                  <tr key={pos.id} style={{ borderBottom: '1px solid var(--surface-2)' }}>
                    <td style={{ padding: '0.5rem', fontWeight: 500 }}>{pos.asset_id}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.quantity}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.average_entry_price.toFixed(4)}</td>
                    <td style={{
                      padding: '0.5rem', textAlign: 'right',
                      color: (pos.realized_pnl || 0) >= 0 ? 'var(--positive)' : 'var(--negative)',
                    }}>
                      {pos.realized_pnl !== null ? pos.realized_pnl.toFixed(2) : '-'}
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                      <span style={{
                        padding: '0.125rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem',
                        background: pos.status === 'open' ? 'rgba(34,197,94,0.14)' : 'var(--surface-2)',
                        color: pos.status === 'open' ? 'var(--positive)' : 'var(--text-secondary)',
                      }}>
                        {label(POSITION_STATUS_LABELS, pos.status)}
                      </span>
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
                      {formatDate(pos.opened_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {activeTab === 'orders' && (
        <div role="tabpanel" id="pp-panel-orders" aria-labelledby="pp-tab-orders">
          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Orders</h2>
          {orders.length === 0 ? (
            <div style={{ padding: '1rem', color: 'var(--text-secondary)' }}>No orders yet.</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>Asset</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>Source</th>
                  <th style={{ textAlign: 'center', padding: '0.5rem', fontSize: '0.8125rem' }}>Side</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Quantity</th>
                  <th style={{ textAlign: 'center', padding: '0.5rem', fontSize: '0.8125rem' }}>Status</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>Rationale</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>Placed</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} data-testid={`pp-order-${o.asset_id}-${o.status}`} style={{ borderBottom: '1px solid var(--surface-2)' }}>
                    <td data-testid="pp-order-asset" style={{ padding: '0.5rem', fontWeight: 500 }}>{o.asset_id}</td>
                    <td data-testid="pp-order-actor" style={{ padding: '0.5rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
                      {label(ACTOR_LABELS, o.actor_type)}
                      {o.data_cutoff_at && (
                        <span data-testid="pp-order-cutoff" style={{ display: 'block', fontSize: '0.6875rem', color: 'var(--text-muted)' }}>
                          {o.actor_type === 'system'
                            ? 'Replay date'
                            : o.actor_type === 'api_key'
                              ? 'Pricing date'
                              : 'Decision cut-off'}{' '}
                          {o.data_cutoff_at}
                        </span>
                      )}
                    </td>
                    <td data-testid="pp-order-side" style={{
                      padding: '0.5rem', textAlign: 'center',
                      color: o.side === 'BUY' ? 'var(--positive)' : 'var(--negative)',
                      fontWeight: 600, fontSize: '0.8125rem',
                    }}>
                      {label(SIDE_LABELS, o.side)}
                    </td>
                    <td data-testid="pp-order-qty" style={{ padding: '0.5rem', textAlign: 'right' }}>{o.quantity}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                      <span data-testid="pp-order-status" className={statusPillClass(o.status)}>
                        {label(ORDER_STATUS_LABELS, o.status)}
                      </span>
                    </td>
                    <td style={{ padding: '0.5rem', fontSize: '0.8125rem', color: 'var(--text-secondary)', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {o.rationale || '-'}
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
                      {formatDate(o.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {activeTab === 'decisions' && (
        <div role="tabpanel" id="pp-panel-decisions" aria-labelledby="pp-tab-decisions">
          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Decisions</h2>
          {decisions.length === 0 ? (
            <div style={{ padding: '1rem', color: 'var(--text-secondary)' }}>No decisions recorded yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {decisions.map((d) => {
                const typeStyle = DECISION_TYPE_STYLES[d.decision_type] || DECISION_TYPE_STYLES.no_action;
                const linkedOrder = orders
                  .filter((o) => o.decision_id === d.id)
                  .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] ?? null;
                return (
                  <div key={d.id} style={{
                    padding: '1rem', background: 'var(--surface-1)', border: '1px solid var(--border-strong)',
                    borderRadius: '8px', borderLeft: `4px solid ${typeStyle.color}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '0.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span style={{
                          padding: '0.125rem 0.5rem', borderRadius: '4px',
                          background: typeStyle.bg, color: typeStyle.color,
                          fontSize: '0.75rem', fontWeight: 600,
                        }}>
                          {label(DECISION_TYPE_LABELS, d.decision_type)}
                        </span>
                        <span data-testid="pp-decision-actor" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          {label(ACTOR_LABELS, d.actor_type)}
                        </span>
                        {d.risk_flags?.auto_execution && (
                          <span data-testid="pp-decision-auto" style={{
                            padding: '0.125rem 0.5rem', borderRadius: '4px',
                            background: 'rgba(59,130,246,0.16)', color: 'var(--text-secondary)',
                            fontSize: '0.6875rem', fontWeight: 600,
                          }}>
                            Automatic execution
                          </span>
                        )}
                        {d.risk_flags?.is_override && (
                          <span style={{
                            padding: '0.125rem 0.5rem', borderRadius: '4px',
                            background: 'rgba(245,158,11,0.14)', color: 'var(--warning)',
                            fontSize: '0.6875rem', fontWeight: 600,
                          }}>
                            Outside the locked strategy
                          </span>
                        )}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span style={{
                          fontSize: '0.6875rem', padding: '0.125rem 0.375rem', borderRadius: '4px',
                          background: 'rgba(255,255,255,0.07)', color: 'var(--text-secondary)',
                        }}>
                          {label(CONFIDENCE_LABELS, d.self_reported_confidence)}
                        </span>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          {formatDateTime(d.decision_time)}
                        </span>
                      </div>
                    </div>
                    {d.risk_flags?.blocked_reason && (
                      <div data-testid="pp-decision-blocked-reason" style={{
                        fontSize: '0.8125rem', marginBottom: '0.5rem', padding: '0.5rem 0.75rem',
                        background: 'rgba(245, 158, 11, 0.12)', color: 'var(--warning)', borderRadius: '4px',
                      }}>
                        <strong>Not executed:</strong>{' '}
                        {label(REJECTION_LABELS, d.risk_flags.blocked_reason)}
                      </div>
                    )}
                    {d.risk_flags?.exit_reason && !d.risk_flags?.blocked_reason && (
                      <div data-testid="pp-decision-exit-reason" style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
                        <strong>Exit reason:</strong>{' '}
                        {label(EXIT_REASON_LABELS, d.risk_flags.exit_reason)}
                      </div>
                    )}
                    {d.rationale && (
                      <div style={{ fontSize: '0.875rem', marginBottom: '0.5rem' }}>{d.rationale}</div>
                    )}
                    {d.expected_outcome && (
                      <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
                        <strong>Expected:</strong> {d.expected_outcome}
                        {d.expected_timeframe_days && ` (${d.expected_timeframe_days}-day horizon)`}
                      </div>
                    )}
                    {d.falsification_condition && (
                      <div style={{ fontSize: '0.8125rem', color: 'var(--warning)' }}>
                        <strong>Falsified if:</strong> {d.falsification_condition}
                      </div>
                    )}
                    <div data-testid="pp-decision-outcome" style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      Outcome recorded at submission: {label(OUTCOME_STATUS_LABELS, d.outcome_status)}
                      {' '}(the field captured then, not the current order status)
                    </div>
                    {linkedOrder ? (
                      <div data-testid="pp-decision-linked-order" style={{ marginTop: '0.25rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                        Current order status: {label(ORDER_STATUS_LABELS, linkedOrder.status)}
                        {' '}(order created {formatDate(linkedOrder.created_at)})
                      </div>
                    ) : (
                      <div data-testid="pp-decision-no-order" style={{ marginTop: '0.25rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        No linked paper order record
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
    </AppShell>
  );
}
