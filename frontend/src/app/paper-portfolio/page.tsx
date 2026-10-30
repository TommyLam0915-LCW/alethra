'use client';

import { useEffect, useState, type CSSProperties } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import AppShell from '@/components/shell/AppShell';
import { statusPillClass } from '@/lib/status-styles';
import { formatDate, formatDateTime } from '@/lib/format';

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

const STATUS_LABELS: Record<string, string> = {
  active: '進行中',
  paused: '已暫停',
  retired: '已退休',
  completed: '已完成',
};

interface JournalRow {
  record_type: string;
  record_classification: string;
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
        if (!cancelled) setJournalError(err.message || '載入紀錄失敗');
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
      setError(err.message || '載入投資組合失敗');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    const cash = parseFloat(cashInput);
    if (isNaN(cash) || cash <= 0) {
      setError('請輸入有效金額');
      return;
    }
    setCreating(true);
    setError('');
    try {
      const p = await api.paperPortfolio.create({ initial_virtual_cash: cash });
      router.push(`/paper-portfolio/${p.id}`);
    } catch (err: any) {
      setError(err.message || '建立組合失敗');
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
        組合
      </Link>
      <Link href="/paper-portfolio?view=journal" style={tabStyle(view === 'journal')}>
        紀錄與結果
      </Link>
    </div>
  );

  if (view === 'journal') {
    return (
      <AppShell active="journal">
        <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '2rem' }}>
          <h1 style={{ fontSize: '1.8rem', marginBottom: '0.5rem' }}>紀錄與結果</h1>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
            紙上投資組合的委託與成交紀錄；回測結果請到對應實驗查看。
          </p>
          {viewTabs}
          {journalError && (
            <div data-testid="pp-journal-error" style={{ color: 'var(--negative)', marginBottom: '1rem' }}>
              {journalError}
            </div>
          )}
          {journalLoading ? (
            <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              載入中...
            </div>
          ) : journalRows.length === 0 ? (
            <div data-testid="pp-journal-empty" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              尚無任何委託或成交紀錄。
            </div>
          ) : (
            <div style={{ overflowX: 'auto', border: '1px solid var(--border-strong)', borderRadius: '8px' }}>
              <table data-testid="pp-journal-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                <thead>
                  <tr style={{ background: 'var(--surface-2)', textAlign: 'left' }}>
                    {['建立時間', '組合', '類型', '資產', '方向', '狀態', '數量', '成交價', '說明'].map((h) => (
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
                        {row.record_type === 'fill' ? '成交' : '委託'}
                      </td>
                      <td style={{ padding: '0.6rem 0.75rem', fontFamily: 'var(--font-code)' }}>{row.asset_id}</td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>{row.side}</td>
                      <td style={{ padding: '0.6rem 0.75rem' }}>{row.order_status}</td>
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
        <div style={{ padding: '2rem', textAlign: 'center' }}>載入中...</div>
      </AppShell>
    );
  }

  return (
    <AppShell active="portfolios">
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '2rem' }}>
      <div style={{ marginBottom: '1rem' }}>
        <Link href="/dashboard" style={{ color: 'var(--primary)', textDecoration: 'none' }}>
          &larr; 返回我的實驗
        </Link>
      </div>
      {viewTabs}

      <h1 style={{ fontSize: '1.8rem', marginBottom: '0.5rem' }}>模擬投資組合</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
        用於研究與行為記錄的虛擬組合，並非交易遊戲。
      </p>

      <div className="notice notice-warn" style={{ marginBottom: '1.5rem', fontSize: '0.875rem' }}>
        <strong>免責聲明：</strong><br />
        模擬交易記錄基於既定假設的模擬執行，不構成投資建議。
        無法重現真實資金心理、完整市場流動性、部分成交、交易所中斷、市場衝擊或實際券商執行。
      </div>

      <div style={{
        background: 'var(--surface-1)', border: '1px solid var(--border-strong)',
        borderRadius: '8px', padding: '1.5rem', marginBottom: '2rem',
      }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>建立新組合</h2>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'end' }}>
          <div style={{ flex: 1 }}>
            <label htmlFor="pp-new-cash" style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
              初始虛擬資金 (USD)
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
            {creating ? '建立中...' : '建立'}
          </button>
        </div>
        <p data-testid="pp-link-hint" style={{ marginTop: '0.75rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
          在此建立的組合只記錄你手動做出的決策。若要讓<strong>已鎖定策略的規則</strong>自動進出場並寫入委託、成交與日誌，
          請到該實驗的「前進模擬」頁面建立並連結紙上帳戶。
        </p>
        {error && (
          <div data-testid="pp-error" style={{ color: 'var(--negative)', marginTop: '0.5rem', fontSize: '0.875rem' }}>
            {error}
          </div>
        )}
      </div>

      <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>你的組合</h2>

      {portfolios.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-secondary)' }}>
          尚無組合，請在上方建立一個。
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {portfolios.map((p) => {
            const statusLabel = STATUS_LABELS[p.status] || p.status;
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
                      初始：{p.currency} {p.initial_virtual_cash.toLocaleString()}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{
                      fontSize: '1.1rem', fontWeight: 600,
                      color: cashReturnPct >= 0 ? 'var(--positive)' : 'var(--negative)',
                    }}>
                      {cashReturnPct >= 0 ? '+' : ''}{cashReturnPct.toFixed(2)}%
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>現金報酬（不含未實現）</div>
                    <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                      已實現 {p.total_realized_pnl >= 0 ? '+' : ''}{p.total_realized_pnl.toFixed(2)}
                      {' · '}
                      未實現 {hasUnrealized
                        ? `${(p.total_unrealized_pnl as number) >= 0 ? '+' : ''}${(p.total_unrealized_pnl as number).toFixed(2)}`
                        : '無法計算'}
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
