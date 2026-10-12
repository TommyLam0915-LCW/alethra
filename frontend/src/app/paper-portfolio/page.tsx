'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface Portfolio {
  id: string;
  status: string;
  initial_virtual_cash: number;
  current_virtual_cash: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number;
  currency: string;
  created_at: string;
  closed_at: string | null;
}

const STATUS_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  active: { label: 'Active', color: '#155724', bg: '#d4edda' },
  paused: { label: 'Paused', color: '#856404', bg: '#fff3cd' },
  retired: { label: 'Retired', color: '#666', bg: '#f5f5f5' },
  completed: { label: 'Completed', color: '#0c5460', bg: '#d1ecf1' },
};

export default function PaperPortfolioListPage() {
  const router = useRouter();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cashInput, setCashInput] = useState('100000');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadPortfolios();
  }, [router]);

  const loadPortfolios = async () => {
    try {
      const data = await api.paperPortfolio.list();
      setPortfolios(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load portfolios');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    const cash = parseFloat(cashInput);
    if (isNaN(cash) || cash <= 0) {
      setError('Please enter a valid amount');
      return;
    }
    setCreating(true);
    setError('');
    try {
      const p = await api.paperPortfolio.create({ initial_virtual_cash: cash });
      router.push(`/paper-portfolio/${p.id}`);
    } catch (err: any) {
      setError(err.message || 'Failed to create portfolio');
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading...</div>;
  }

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '2rem' }}>
      <div style={{ marginBottom: '2rem' }}>
        <Link href="/dashboard" style={{ color: '#0066cc', textDecoration: 'none' }}>
          &larr; Back to Dashboard
        </Link>
      </div>

      <h1 style={{ fontSize: '1.8rem', marginBottom: '0.5rem' }}>Paper Portfolio</h1>
      <p style={{ color: '#666', marginBottom: '1.5rem' }}>
        Virtual portfolio for research and behavior tracking. Not a trading game.
      </p>

      <div style={{
        background: '#fff3cd', border: '1px solid #ffc107', borderRadius: '8px',
        padding: '1rem', marginBottom: '1.5rem', fontSize: '0.875rem', color: '#856404',
      }}>
        <strong>Disclaimer / 免責聲明：</strong><br />
        Paper trading records simulated execution under stated assumptions.
        It does not reproduce real-money psychology, full market liquidity, partial fills,
        exchange outages, market impact, or actual brokerage execution.<br />
        <strong>提醒：</strong>虛擬交易記錄基於既定假設的模擬執行，不構成投資建議。
        無法重現真實資金心理、完整市場流動性、部分成交、交易所中斷、市場衝擊或實際券商執行。
      </div>

      <div style={{
        background: '#f8f9fa', borderRadius: '8px', padding: '1.5rem', marginBottom: '2rem',
      }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>Create New Portfolio</h2>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'end' }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
              Initial Virtual Cash (USD)
            </label>
            <input
              type="number"
              value={cashInput}
              onChange={(e) => setCashInput(e.target.value)}
              style={{
                width: '100%', padding: '0.5rem', border: '1px solid #ddd',
                borderRadius: '4px', fontSize: '1rem',
              }}
            />
          </div>
          <button
            onClick={handleCreate}
            disabled={creating}
            style={{
              padding: '0.5rem 1.5rem', background: '#0066cc', color: 'white',
              border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '1rem',
              opacity: creating ? 0.6 : 1,
            }}
          >
            {creating ? 'Creating...' : 'Create'}
          </button>
        </div>
        {error && (
          <div style={{ color: '#dc3545', marginTop: '0.5rem', fontSize: '0.875rem' }}>
            {error}
          </div>
        )}
      </div>

      <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>Your Portfolios</h2>

      {portfolios.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '2rem', color: '#666' }}>
          No portfolios yet. Create one above.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {portfolios.map((p) => {
            const statusStyle = STATUS_LABELS[p.status] || STATUS_LABELS.active;
            const pnl = p.total_realized_pnl + p.total_unrealized_pnl;
            const pnlPct = ((p.current_virtual_cash - p.initial_virtual_cash) / p.initial_virtual_cash * 100);
            return (
              <Link
                key={p.id}
                href={`/paper-portfolio/${p.id}`}
                style={{
                  display: 'block', padding: '1.5rem', background: 'white',
                  border: '1px solid #e0e0e0', borderRadius: '8px',
                  textDecoration: 'none', color: 'inherit',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                      <span style={{
                        padding: '0.125rem 0.5rem', borderRadius: '4px',
                        background: statusStyle.bg, color: statusStyle.color,
                        fontSize: '0.75rem', fontWeight: 600,
                      }}>
                        {statusStyle.label}
                      </span>
                      <span style={{ fontSize: '0.75rem', color: '#999' }}>
                        {new Date(p.created_at).toLocaleDateString()}
                      </span>
                    </div>
                    <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>
                      {p.currency} {p.current_virtual_cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </div>
                    <div style={{ fontSize: '0.875rem', color: '#666', marginTop: '0.25rem' }}>
                      Initial: {p.currency} {p.initial_virtual_cash.toLocaleString()}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{
                      fontSize: '1.1rem', fontWeight: 600,
                      color: pnl >= 0 ? '#155724' : '#721c24',
                    }}>
                      {pnl >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                    </div>
                    <div style={{ fontSize: '0.875rem', color: '#666' }}>
                      P&L: {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                    </div>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
