'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';
import { statusPillClass } from '@/lib/status-styles';
import { formatDateTime } from '@/lib/format';

interface ExecutionIntent {
  intent_id: string;
  portfolio_id: string;
  symbol: string;
  side: string;
  quantity: number;
  status: string;
  order_type: string;
  limit_price?: number;
  broker_ref?: string;
  authorization_token?: string;
  fill_source?: string | null;
  broker_confirmed_at?: string | null;
  rejection_reason?: string | null;
  created_at: string;
  updated_at: string;
}

const SYMBOLS = ['DEMO-ALPHA', 'DEMO-BETA', 'DEMO-BENCHMARK'];
const SIDES = ['BUY', 'SELL'];
const ORDER_TYPES = ['MARKET', 'LIMIT'];

const STATUS_LABELS: Record<string, string> = {
  pending: '待處理',
  authorized: '已授權',
  submitted: '已送出',
  filled: '已成交',
  cancelled: '已取消',
  rejected: '已拒絕',
  uncertain: '狀態不明',
};

export default function ExecutionPage() {
  const router = useRouter();
  const [intents, setIntents] = useState<ExecutionIntent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create form state
  const [portfolioId, setPortfolioId] = useState('');
  const [symbol, setSymbol] = useState(SYMBOLS[0]);
  const [side, setSide] = useState(SIDES[0]);
  const [quantity, setQuantity] = useState('');
  const [orderType, setOrderType] = useState(ORDER_TYPES[0]);
  const [limitPrice, setLimitPrice] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  // Per-intent action state
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [authTokenInputs, setAuthTokenInputs] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }

    loadIntents();
  }, [router]);

  const loadIntents = async () => {
    try {
      setError(null);
      const data = await api.execution.listIntents();
      setIntents(data);
    } catch (err) {
      console.error('Failed to load intents:', err);
      setError('無法載入執行意圖。請稍後再試。');
    } finally {
      setLoading(false);
    }
  };

  const truncateId = (id: string) => {
    if (id.length <= 12) return id;
    return id.slice(0, 12) + '...';
  };

  const handleCreate = async () => {
    if (!portfolioId.trim()) {
      setCreateError('請輸入投資組合 ID');
      return;
    }
    if (!quantity || Number(quantity) <= 0) {
      setCreateError('請輸入有效數量');
      return;
    }
    if (orderType === 'LIMIT' && (!limitPrice || Number(limitPrice) <= 0)) {
      setCreateError('限價單請輸入有效限價');
      return;
    }

    setCreating(true);
    setCreateError(null);
    setCreatedToken(null);

    try {
      const payload: any = {
        portfolio_id: portfolioId.trim(),
        symbol,
        side,
        quantity: Number(quantity),
        order_type: orderType,
      };
      if (orderType === 'LIMIT' && limitPrice) {
        payload.limit_price = Number(limitPrice);
      }

      const result = await api.execution.createIntent(payload);
      setCreatedToken(result.authorization_token || null);
      setPortfolioId('');
      setQuantity('');
      setLimitPrice('');
      await loadIntents();
    } catch (err: any) {
      console.error('Failed to create intent:', err);
      setCreateError(err.message || '建立意圖失敗');
    } finally {
      setCreating(false);
    }
  };

  const handleAuthorize = async (intentId: string) => {
    const token = authTokenInputs[intentId];
    if (!token || !token.trim()) {
      setActionError('請輸入授權代碼');
      return;
    }

    setActionLoading(`auth-${intentId}`);
    setActionError(null);

    try {
      await api.execution.authorize(intentId, token.trim());
      await loadIntents();
    } catch (err: any) {
      console.error('Failed to authorize:', err);
      setActionError(err.message || '授權失敗');
    } finally {
      setActionLoading(null);
    }
  };

  const handleSubmit = async (intentId: string) => {
    setActionLoading(`submit-${intentId}`);
    setActionError(null);

    try {
      await api.execution.submit(intentId);
      await loadIntents();
    } catch (err: any) {
      console.error('Failed to submit:', err);
      setActionError(err.message || '送出失敗');
    } finally {
      setActionLoading(null);
    }
  };

  const handleCancel = async (intentId: string) => {
    setActionLoading(`cancel-${intentId}`);
    setActionError(null);

    try {
      await api.execution.cancel(intentId);
      await loadIntents();
    } catch (err: any) {
      console.error('Failed to cancel:', err);
      setActionError(err.message || '取消失敗');
    } finally {
      setActionLoading(null);
    }
  };

  const handleReconcile = async (intentId: string) => {
    setActionLoading(`reconcile-${intentId}`);
    setActionError(null);

    try {
      await api.execution.reconcile(intentId);
      await loadIntents();
    } catch (err: any) {
      console.error('Failed to reconcile:', err);
      setActionError(err.message || '對帳失敗');
    } finally {
      setActionLoading(null);
    }
  };

  const renderActions = (intent: ExecutionIntent) => {
    const isLoading = (key: string) => actionLoading === key;

    if (intent.status === 'pending') {
      return (
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="授權代碼"
            value={authTokenInputs[intent.intent_id] || ''}
            onChange={(e) => setAuthTokenInputs({ ...authTokenInputs, [intent.intent_id]: e.target.value })}
            style={{
              padding: '6px 10px',
              border: '1px solid var(--border-strong)',
              borderRadius: '6px',
              fontSize: '13px',
              width: '160px',
            }}
          />
          <button
            className="btn btn-primary"
            onClick={() => handleAuthorize(intent.intent_id)}
            disabled={isLoading(`auth-${intent.intent_id}`)}
            style={{ fontSize: '13px', padding: '6px 14px' }}
          >
            {isLoading(`auth-${intent.intent_id}`) ? '處理中...' : '授權'}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => handleCancel(intent.intent_id)}
            disabled={isLoading(`cancel-${intent.intent_id}`)}
            style={{ fontSize: '13px', padding: '6px 14px' }}
          >
            {isLoading(`cancel-${intent.intent_id}`) ? '處理中...' : '取消'}
          </button>
        </div>
      );
    }

    if (intent.status === 'authorized') {
      return (
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button
            className="btn btn-primary"
            onClick={() => handleSubmit(intent.intent_id)}
            disabled={isLoading(`submit-${intent.intent_id}`)}
            style={{ fontSize: '13px', padding: '6px 14px' }}
          >
            {isLoading(`submit-${intent.intent_id}`) ? '處理中...' : '送出'}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => handleCancel(intent.intent_id)}
            disabled={isLoading(`cancel-${intent.intent_id}`)}
            style={{ fontSize: '13px', padding: '6px 14px' }}
          >
            {isLoading(`cancel-${intent.intent_id}`) ? '處理中...' : '取消'}
          </button>
        </div>
      );
    }

    if (intent.status === 'filled') {
      let fillLabel: string;
      let fillTone: string;
      let refLabel: string;

      if (intent.fill_source === 'simulated') {
        fillLabel = '模擬成交（離線）';
        fillTone = 'warn';
        refLabel = intent.broker_ref || '—';
      } else if (intent.fill_source === 'broker' && intent.broker_confirmed_at) {
        fillLabel = '券商確認成交';
        fillTone = 'ok';
        refLabel = intent.broker_ref || '—';
      } else if (intent.fill_source === 'broker') {
        fillLabel = '券商來源（待確認）';
        fillTone = 'warn';
        refLabel = intent.broker_ref || '待券商確認';
      } else {
        fillLabel = '來源不明';
        fillTone = 'neutral';
        refLabel = intent.broker_ref || '無法取得參考編號';
      }

      return (
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span className={`status-pill status-${fillTone}`}>
            {fillLabel}
          </span>
          <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            參考編號：{refLabel}
          </span>
          <button
            className="btn btn-secondary"
            onClick={() => handleReconcile(intent.intent_id)}
            disabled={isLoading(`reconcile-${intent.intent_id}`)}
            style={{ fontSize: '13px', padding: '6px 14px' }}
          >
            {isLoading(`reconcile-${intent.intent_id}`) ? '處理中...' : '對帳'}
          </button>
        </div>
      );
    }

    // cancelled / rejected / submitted: no actions
    return null;
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1>執行意圖</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/dashboard" className="btn btn-secondary">
            返回我的實驗
          </Link>
        </div>
      </div>

      {/* Offline simulation banner */}
      <div className="disclaimer" role="note">
        <strong>離線模擬模式</strong> — 不會送出真實訂單。所有操作僅供研究模擬使用。
        <br />
        <strong>提醒：</strong>{brand.disclaimer.zhFull}
      </div>

      {/* Create Intent Form */}
      <div className="card" style={{ marginBottom: '30px' }}>
        <h2 style={{ marginBottom: '16px' }}>建立執行意圖</h2>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '12px', marginBottom: '16px' }}>
          <div>
            <label htmlFor="exec-portfolio-id" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              投資組合 ID
            </label>
            <input
              id="exec-portfolio-id"
              type="text"
              value={portfolioId}
              onChange={(e) => setPortfolioId(e.target.value)}
              placeholder="輸入投資組合 ID"
              style={{
                width: '100%',
                padding: '8px 12px',
                border: '1px solid var(--border-strong)',
                borderRadius: '6px',
                fontSize: '14px',
                boxSizing: 'border-box',
              }}
            />
          </div>

          <div>
            <label htmlFor="exec-symbol" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              標的
            </label>
            <select
              id="exec-symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                border: '1px solid var(--border-strong)',
                borderRadius: '6px',
                fontSize: '14px',
                boxSizing: 'border-box',
                backgroundColor: 'var(--surface-1)',
              }}
            >
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="exec-side" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              方向
            </label>
            <select
              id="exec-side"
              value={side}
              onChange={(e) => setSide(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                border: '1px solid var(--border-strong)',
                borderRadius: '6px',
                fontSize: '14px',
                boxSizing: 'border-box',
                backgroundColor: 'var(--surface-1)',
              }}
            >
              {SIDES.map((s) => (
                <option key={s} value={s}>{s === 'BUY' ? '買入' : '賣出'}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="exec-quantity" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              數量
            </label>
            <input
              id="exec-quantity"
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0"
              min="1"
              style={{
                width: '100%',
                padding: '8px 12px',
                border: '1px solid var(--border-strong)',
                borderRadius: '6px',
                fontSize: '14px',
                boxSizing: 'border-box',
              }}
            />
          </div>

          <div>
            <label htmlFor="exec-order-type" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              訂單類型
            </label>
            <select
              id="exec-order-type"
              value={orderType}
              onChange={(e) => setOrderType(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                border: '1px solid var(--border-strong)',
                borderRadius: '6px',
                fontSize: '14px',
                boxSizing: 'border-box',
                backgroundColor: 'var(--surface-1)',
              }}
            >
              {ORDER_TYPES.map((t) => (
                <option key={t} value={t}>{t === 'MARKET' ? '市價單' : '限價單'}</option>
              ))}
            </select>
          </div>

          {orderType === 'LIMIT' && (
            <div>
              <label htmlFor="exec-limit-price" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                限價
              </label>
              <input
                id="exec-limit-price"
                type="number"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                placeholder="0.00"
                min="0"
                step="0.01"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  fontSize: '14px',
                  boxSizing: 'border-box',
                }}
              />
            </div>
          )}
        </div>

        {createError && (
          <div className="notice notice-danger" role="alert">
            {createError}
          </div>
        )}

        {createdToken && (
          <div className="notice notice-ok">
            <strong>意圖已建立！</strong> 請複製以下授權代碼以授權此意圖：
            <code style={{ display: 'block', marginTop: '6px', padding: '8px', backgroundColor: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: '4px', wordBreak: 'break-all', fontFamily: 'monospace', fontSize: '13px' }}>
              {createdToken}
            </code>
          </div>
        )}

        <button
          className="btn btn-primary"
          onClick={handleCreate}
          disabled={creating}
        >
          {creating ? '建立中...' : '建立意圖'}
        </button>
      </div>

      {/* Intents List */}
      <h2 style={{ marginBottom: '16px' }}>意圖列表</h2>

      {actionError && (
        <div className="notice notice-danger" role="alert">
          {actionError}
        </div>
      )}

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中...
        </div>
      ) : error ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          <p style={{ color: 'var(--negative-text)', marginBottom: '16px' }}>{error}</p>
          <button className="btn btn-secondary" onClick={loadIntents}>
            重試
          </button>
        </div>
      ) : intents.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '8px' }}>尚無執行意圖。</p>
          <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>使用上方表單建立你的第一個執行意圖。</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {intents.map((intent) => {
            return (
              <div key={intent.intent_id} className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '12px' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                      <code style={{ fontSize: '13px', color: 'var(--text-muted)' }} title={intent.intent_id}>
                        {truncateId(intent.intent_id)}
                      </code>
                      <span className={statusPillClass(intent.status)}>
                        {STATUS_LABELS[intent.status] || intent.status}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: '20px', fontSize: '14px', color: 'var(--text-primary)' }}>
                      <span><strong>標的:</strong> {intent.symbol}</span>
                      <span><strong>方向:</strong> {intent.side === 'BUY' ? '買入' : '賣出'}</span>
                      <span><strong>數量:</strong> {intent.quantity}</span>
                      <span><strong>類型:</strong> {intent.order_type === 'MARKET' ? '市價單' : '限價單'}</span>
                      {intent.limit_price != null && (
                        <span><strong>限價:</strong> {intent.limit_price}</span>
                      )}
                    </div>
                    <div style={{ marginTop: '6px' }}>
                      <Link
                        href={`/paper-portfolio/${intent.portfolio_id}`}
                        style={{ fontSize: '13px', color: 'var(--primary-text)', textDecoration: 'none' }}
                      >
                        查看投資組合 →
                      </Link>
                    </div>
                    {intent.status === 'rejected' && intent.rejection_reason && (
                      <div style={{ marginTop: '6px', fontSize: '13px', color: 'var(--negative-text)' }}>
                        <strong>拒絕原因：</strong>{intent.rejection_reason}
                      </div>
                    )}
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px' }}>
                      建立於 {formatDateTime(intent.created_at)}
                    </div>
                  </div>
                </div>
                {renderActions(intent)}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
