'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
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
  asset_id: string;
  side: string;
  quantity: number;
  status: string;
  rationale: string | null;
  created_at: string;
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

const STATUS_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  active: { label: '進行中', color: '#155724', bg: '#d4edda' },
  paused: { label: '已暫停', color: '#856404', bg: '#fff3cd' },
  retired: { label: '已退休', color: '#666', bg: '#f5f5f5' },
  completed: { label: '已完成', color: '#0c5460', bg: '#d1ecf1' },
};

const DECISION_TYPE_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  no_action: { label: '未行動', color: '#666', bg: '#f5f5f5' },
  entry: { label: '進場', color: '#155724', bg: '#d4edda' },
  exit: { label: '出場', color: '#721c24', bg: '#f8d7da' },
  pause: { label: '暫停', color: '#856404', bg: '#fff3cd' },
  retire: { label: '退休', color: '#666', bg: '#e2e3e5' },
  override_request: { label: '覆寫', color: '#856404', bg: '#fff3cd' },
};

const CONFIDENCE_LABELS: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  not_provided: '未提供',
};

const SIDE_LABELS: Record<string, string> = {
  BUY: '買入',
  SELL: '賣出',
};

const TAB_LABELS: Record<string, string> = {
  overview: '概覽',
  positions: '部位',
  orders: '委託',
  decisions: '決策',
};

export default function PaperPortfolioDetailPage() {
  const router = useRouter();
  const params = useParams();
  const portfolioId = params.id as string;

  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<'overview' | 'positions' | 'orders' | 'decisions'>('overview');

  const [orderForm, setOrderForm] = useState({
    asset_id: 'DEMO-ALPHA',
    side: 'BUY',
    quantity: '100',
    rationale: '',
    expected_outcome: '',
    expected_timeframe_days: '',
    self_reported_confidence: 'medium',
    falsification_condition: '',
  });
  const [submitting, setSubmitting] = useState(false);

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
    } catch (err: any) {
      setError(err.message || '載入失敗');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateOrder = async () => {
    if (!portfolio) return;
    setSubmitting(true);
    setError('');
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
      });
      await loadData();
      setOrderForm({ ...orderForm, rationale: '', expected_outcome: '', falsification_condition: '' });
    } catch (err: any) {
      setError(err.message || '建立委託失敗');
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
    if (!confirm('確定要退休此組合嗎？退休後將無法修改。')) return;
    try {
      const p = await api.paperPortfolio.retire(portfolioId);
      setPortfolio(p);
    } catch (err: any) {
      setError(err.message);
    }
  };

  if (loading) {
    return <div style={{ padding: '2rem', textAlign: 'center' }}>載入中...</div>;
  }

  if (!portfolio) {
    return <div style={{ padding: '2rem' }}>組合不存在</div>;
  }

  const statusStyle = STATUS_LABELS[portfolio.status] || STATUS_LABELS.active;
  const pnl = portfolio.total_realized_pnl + portfolio.total_unrealized_pnl;
  const pnlPct = ((portfolio.current_virtual_cash - portfolio.initial_virtual_cash) / portfolio.initial_virtual_cash * 100);
  const openPositions = positions.filter(p => p.status === 'open');
  const closedPositions = positions.filter(p => p.status === 'closed');

  return (
    <div style={{ maxWidth: '1000px', margin: '0 auto', padding: '2rem' }}>
      <div style={{ marginBottom: '1.5rem' }}>
        <Link href="/paper-portfolio" style={{ color: '#0066cc', textDecoration: 'none' }}>
          &larr; 返回組合列表
        </Link>
      </div>

      <div style={{
        background: '#fff3cd', border: '1px solid #ffc107', borderRadius: '8px',
        padding: '0.75rem', marginBottom: '1.5rem', fontSize: '0.8125rem', color: '#856404',
      }}>
        <strong>免責聲明：</strong><br />
        模擬交易記錄基於既定假設的模擬執行，不構成投資建議。
        無法重現真實資金心理、完整市場流動性、部分成交、交易所中斷、市場衝擊或實際券商執行。<br />
        <strong>提醒：</strong>虛擬交易記錄基於既定假設的模擬執行，不構成投資建議。
        無法重現真實資金心理、完整市場流動性、部分成交、交易所中斷、市場衝擊或實際券商執行。
      </div>

      {error && (
        <div style={{
          background: '#f8d7da', border: '1px solid #f5c6cb', borderRadius: '8px',
          padding: '0.75rem', marginBottom: '1rem', color: '#721c24', fontSize: '0.875rem',
        }}>
          {error}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '1.5rem' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <h1 style={{ fontSize: '1.5rem', margin: 0 }}>模擬投資組合</h1>
            <span style={{
              padding: '0.25rem 0.75rem', borderRadius: '4px',
              background: statusStyle.bg, color: statusStyle.color,
              fontSize: '0.75rem', fontWeight: 600,
            }}>
              {statusStyle.label}
            </span>
          </div>
          <div style={{ fontSize: '0.875rem', color: '#666' }}>
            建立於 {new Date(portfolio.created_at).toLocaleDateString('zh-Hant')}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {portfolio.status === 'active' && (
            <>
              <button onClick={handlePause} style={{
                padding: '0.375rem 0.75rem', background: '#fff3cd', color: '#856404',
                border: '1px solid #ffc107', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8125rem',
              }}>
                暫停
              </button>
              <button onClick={handleRetire} style={{
                padding: '0.375rem 0.75rem', background: '#f8d7da', color: '#721c24',
                border: '1px solid #f5c6cb', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8125rem',
              }}>
                退休
              </button>
            </>
          )}
          {portfolio.status === 'paused' && (
            <button onClick={handleResume} style={{
              padding: '0.375rem 0.75rem', background: '#d4edda', color: '#155724',
              border: '1px solid #c3e6cb', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8125rem',
            }}>
              恢復
            </button>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem', marginBottom: '2rem' }}>
        <div style={{ background: 'white', border: '1px solid #e0e0e0', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: '#666', marginBottom: '0.25rem' }}>現金</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>
            {portfolio.currency} {portfolio.current_virtual_cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}
          </div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e0e0e0', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: '#666', marginBottom: '0.25rem' }}>報酬</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 600, color: pnl >= 0 ? '#155724' : '#721c24' }}>
            {pnl >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
          </div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e0e0e0', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: '#666', marginBottom: '0.25rem' }}>持有部位</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{openPositions.length}</div>
        </div>
        <div style={{ background: 'white', border: '1px solid #e0e0e0', borderRadius: '8px', padding: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: '#666', marginBottom: '0.25rem' }}>決策次數</div>
          <div style={{ fontSize: '1.25rem', fontWeight: 600 }}>{decisions.length}</div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '0', marginBottom: '1.5rem', borderBottom: '2px solid #e0e0e0' }}>
        {(['overview', 'positions', 'orders', 'decisions'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '0.75rem 1.5rem', background: 'none', border: 'none',
              borderBottom: activeTab === tab ? '2px solid #0066cc' : '2px solid transparent',
              color: activeTab === tab ? '#0066cc' : '#666',
              fontWeight: activeTab === tab ? 600 : 400,
              cursor: 'pointer', fontSize: '0.9375rem', marginBottom: '-2px',
            }}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div>
          {portfolio.status !== 'retired' && portfolio.status !== 'completed' && (
            <div style={{
              background: '#f8f9fa', borderRadius: '8px', padding: '1.5rem', marginBottom: '1.5rem',
            }}>
              <h2 style={{ fontSize: '1.1rem', marginBottom: '1rem' }}>新增模擬委託</h2>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>標的</label>
                  <select
                    value={orderForm.asset_id}
                    onChange={(e) => setOrderForm({ ...orderForm, asset_id: e.target.value })}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px' }}
                  >
                    <option value="DEMO-ALPHA">DEMO-ALPHA</option>
                    <option value="DEMO-BETA">DEMO-BETA</option>
                    <option value="DEMO-BENCHMARK">DEMO-BENCHMARK</option>
                  </select>
                </div>
                <div>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>方向</label>
                  <select
                    value={orderForm.side}
                    onChange={(e) => setOrderForm({ ...orderForm, side: e.target.value })}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px' }}
                  >
                    <option value="BUY">買入</option>
                    <option value="SELL">賣出</option>
                  </select>
                </div>
                <div>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>數量</label>
                  <input
                    type="number"
                    value={orderForm.quantity}
                    onChange={(e) => setOrderForm({ ...orderForm, quantity: e.target.value })}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>信心程度</label>
                  <select
                    value={orderForm.self_reported_confidence}
                    onChange={(e) => setOrderForm({ ...orderForm, self_reported_confidence: e.target.value })}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px' }}
                  >
                    <option value="low">低</option>
                    <option value="medium">中</option>
                    <option value="high">高</option>
                  </select>
                </div>
                <div style={{ gridColumn: '1 / -1' }}>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>
                    理由 <span style={{ color: '#dc3545' }}>*</span>
                  </label>
                  <textarea
                    value={orderForm.rationale}
                    onChange={(e) => setOrderForm({ ...orderForm, rationale: e.target.value })}
                    rows={2}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px', resize: 'vertical' }}
                    placeholder="為什麼做這個決定？"
                  />
                </div>
                <div style={{ gridColumn: '1 / -1' }}>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>
                    預期結果 <span style={{ color: '#dc3545' }}>*</span>
                  </label>
                  <textarea
                    value={orderForm.expected_outcome}
                    onChange={(e) => setOrderForm({ ...orderForm, expected_outcome: e.target.value })}
                    rows={2}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px', resize: 'vertical' }}
                    placeholder="你預期會發生什麼？"
                  />
                </div>
                <div>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>時間範圍（天）</label>
                  <input
                    type="number"
                    value={orderForm.expected_timeframe_days}
                    onChange={(e) => setOrderForm({ ...orderForm, expected_timeframe_days: e.target.value })}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px' }}
                    placeholder="例如 30"
                  />
                </div>
                <div>
                  <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.8125rem' }}>推翻條件</label>
                  <input
                    type="text"
                    value={orderForm.falsification_condition}
                    onChange={(e) => setOrderForm({ ...orderForm, falsification_condition: e.target.value })}
                    style={{ width: '100%', padding: '0.5rem', border: '1px solid #ddd', borderRadius: '4px' }}
                    placeholder="什麼情況能證明這個決定是錯的？"
                  />
                </div>
              </div>
              <button
                onClick={handleCreateOrder}
                disabled={submitting || !orderForm.rationale || !orderForm.expected_outcome}
                style={{
                  marginTop: '1rem', padding: '0.5rem 1.5rem', background: '#0066cc', color: 'white',
                  border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '0.9375rem',
                  opacity: (submitting || !orderForm.rationale || !orderForm.expected_outcome) ? 0.5 : 1,
                }}
              >
                {submitting ? '送出中...' : '送出委託'}
              </button>
            </div>
          )}

          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>持有部位</h2>
          {openPositions.length === 0 ? (
            <div style={{ padding: '1rem', color: '#666', fontSize: '0.875rem' }}>尚無持有部位</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '1.5rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e0e0e0' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>標的</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>數量</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>平均成本</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>未實現損益</th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((pos) => (
                  <tr key={pos.id} style={{ borderBottom: '1px solid #f0f0f0' }}>
                    <td style={{ padding: '0.5rem', fontWeight: 500 }}>{pos.asset_id}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.quantity}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.average_entry_price.toFixed(4)}</td>
                    <td style={{
                      padding: '0.5rem', textAlign: 'right',
                      color: (pos.unrealized_pnl || 0) >= 0 ? '#155724' : '#721c24',
                    }}>
                      {(pos.unrealized_pnl || 0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {activeTab === 'positions' && (
        <div>
          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>所有部位</h2>
          {positions.length === 0 ? (
            <div style={{ padding: '1rem', color: '#666' }}>尚無部位</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e0e0e0' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>標的</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>數量</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>平均成本</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>已實現損益</th>
                  <th style={{ textAlign: 'center', padding: '0.5rem', fontSize: '0.8125rem' }}>狀態</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>建立日期</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => (
                  <tr key={pos.id} style={{ borderBottom: '1px solid #f0f0f0' }}>
                    <td style={{ padding: '0.5rem', fontWeight: 500 }}>{pos.asset_id}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.quantity}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{pos.average_entry_price.toFixed(4)}</td>
                    <td style={{
                      padding: '0.5rem', textAlign: 'right',
                      color: (pos.realized_pnl || 0) >= 0 ? '#155724' : '#721c24',
                    }}>
                      {pos.realized_pnl !== null ? pos.realized_pnl.toFixed(2) : '-'}
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                      <span style={{
                        padding: '0.125rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem',
                        background: pos.status === 'open' ? '#d4edda' : '#f5f5f5',
                        color: pos.status === 'open' ? '#155724' : '#666',
                      }}>
                        {pos.status === 'open' ? '持有中' : '已關閉'}
                      </span>
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.8125rem', color: '#666' }}>
                      {new Date(pos.opened_at).toLocaleDateString('zh-Hant')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {activeTab === 'orders' && (
        <div>
          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>委託記錄</h2>
          {orders.length === 0 ? (
            <div style={{ padding: '1rem', color: '#666' }}>尚無委託</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e0e0e0' }}>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>標的</th>
                  <th style={{ textAlign: 'center', padding: '0.5rem', fontSize: '0.8125rem' }}>方向</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>數量</th>
                  <th style={{ textAlign: 'center', padding: '0.5rem', fontSize: '0.8125rem' }}>狀態</th>
                  <th style={{ textAlign: 'left', padding: '0.5rem', fontSize: '0.8125rem' }}>理由</th>
                  <th style={{ textAlign: 'right', padding: '0.5rem', fontSize: '0.8125rem' }}>日期</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} style={{ borderBottom: '1px solid #f0f0f0' }}>
                    <td style={{ padding: '0.5rem', fontWeight: 500 }}>{o.asset_id}</td>
                    <td style={{
                      padding: '0.5rem', textAlign: 'center',
                      color: o.side === 'BUY' ? '#155724' : '#721c24',
                      fontWeight: 600, fontSize: '0.8125rem',
                    }}>
                      {SIDE_LABELS[o.side] || o.side}
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>{o.quantity}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                      <span style={{
                        padding: '0.125rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem',
                        background: o.status === 'filled' ? '#d4edda' : o.status === 'rejected' ? '#f8d7da' : '#f5f5f5',
                        color: o.status === 'filled' ? '#155724' : o.status === 'rejected' ? '#721c24' : '#666',
                      }}>
                        {o.status === 'filled' ? '已成交' : o.status === 'rejected' ? '已拒絕' : o.status}
                      </span>
                    </td>
                    <td style={{ padding: '0.5rem', fontSize: '0.8125rem', color: '#666', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {o.rationale || '-'}
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'right', fontSize: '0.8125rem', color: '#666' }}>
                      {new Date(o.created_at).toLocaleDateString('zh-Hant')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {activeTab === 'decisions' && (
        <div>
          <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>決策記錄</h2>
          {decisions.length === 0 ? (
            <div style={{ padding: '1rem', color: '#666' }}>尚無決策記錄</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {decisions.map((d) => {
                const typeStyle = DECISION_TYPE_LABELS[d.decision_type] || DECISION_TYPE_LABELS.no_action;
                return (
                  <div key={d.id} style={{
                    padding: '1rem', background: 'white', border: '1px solid #e0e0e0',
                    borderRadius: '8px', borderLeft: `4px solid ${typeStyle.color}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '0.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span style={{
                          padding: '0.125rem 0.5rem', borderRadius: '4px',
                          background: typeStyle.bg, color: typeStyle.color,
                          fontSize: '0.75rem', fontWeight: 600,
                        }}>
                          {typeStyle.label}
                        </span>
                        <span style={{ fontSize: '0.75rem', color: '#999' }}>
                          {d.actor_type}
                        </span>
                        {d.risk_flags?.is_override && (
                          <span style={{
                            padding: '0.125rem 0.5rem', borderRadius: '4px',
                            background: '#fff3cd', color: '#856404',
                            fontSize: '0.6875rem', fontWeight: 600,
                          }}>
                            超出鎖定策略
                          </span>
                        )}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span style={{
                          fontSize: '0.6875rem', padding: '0.125rem 0.375rem', borderRadius: '4px',
                          background: '#e9ecef', color: '#666',
                        }}>
                          {CONFIDENCE_LABELS[d.self_reported_confidence] || d.self_reported_confidence}
                        </span>
                        <span style={{ fontSize: '0.75rem', color: '#999' }}>
                          {new Date(d.decision_time).toLocaleString('zh-Hant')}
                        </span>
                      </div>
                    </div>
                    {d.rationale && (
                      <div style={{ fontSize: '0.875rem', marginBottom: '0.5rem' }}>{d.rationale}</div>
                    )}
                    {d.expected_outcome && (
                      <div style={{ fontSize: '0.8125rem', color: '#666', marginBottom: '0.25rem' }}>
                        <strong>預期：</strong>{d.expected_outcome}
                        {d.expected_timeframe_days && `（${d.expected_timeframe_days} 天）`}
                      </div>
                    )}
                    {d.falsification_condition && (
                      <div style={{ fontSize: '0.8125rem', color: '#856404' }}>
                        <strong>推翻條件：</strong>{d.falsification_condition}
                      </div>
                    )}
                    <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: '#999' }}>
                      結果：{d.outcome_status}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
