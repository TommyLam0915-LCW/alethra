'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { BarsResponse, ExecutionIntent, InstrumentDetail } from './types';

interface PaperPortfolio {
  id: string;
  currency: string;
  current_virtual_cash: number;
  status: string;
}

interface Props {
  instrument: InstrumentDetail | null;
  bars: BarsResponse | null;
  instrumentId: string;
}

export default function OrderPreviewPanel({ instrument, bars, instrumentId }: Props) {
  const [portfolios, setPortfolios] = useState<PaperPortfolio[]>([]);
  const [portfolioId, setPortfolioId] = useState('');
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [lots, setLots] = useState('1');
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET');
  const [limitPrice, setLimitPrice] = useState('');
  const [intent, setIntent] = useState<ExecutionIntent | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const loadPortfolios = useCallback(async () => {
    try {
      const data = await api.paperPortfolio.list();
      const list = (data || []).filter((p: PaperPortfolio) => p.status === 'active');
      setPortfolios(list);
      setPortfolioId((prev) => (prev && list.some((p: PaperPortfolio) => p.id === prev) ? prev : list[0]?.id || ''));
    } catch {
      // panel shows create button when list fails to load
    }
  }, []);

  useEffect(() => {
    loadPortfolios();
  }, [loadPortfolios]);

  useEffect(() => {
    setIntent(null);
    setError('');
    setNotice('');
  }, [instrumentId]);

  const lotSize = instrument?.lot_size || 1;
  const quantity = Math.max(0, Math.floor((parseFloat(lots) || 0) * lotSize));
  const lastClose = bars && bars.bars.length > 0 ? bars.bars[bars.bars.length - 1].close : null;
  const estimate = lastClose !== null ? quantity * lastClose : null;

  const call = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失敗');
      return null;
    } finally {
      setBusy(false);
    }
  };

  const createIntent = async () => {
    if (!portfolioId) {
      setError('沒有可用的模擬投資組合，請先建立。');
      return;
    }
    if (quantity <= 0) {
      setError('數量必須大於 0。');
      return;
    }
    if (orderType === 'LIMIT' && !(parseFloat(limitPrice) > 0)) {
      setError('限價單需要有效限價。');
      return;
    }
    const result = await call(() =>
      api.execution.createIntent({
        portfolio_id: portfolioId,
        symbol: instrumentId,
        side,
        quantity,
        order_type: orderType,
        limit_price: orderType === 'LIMIT' ? parseFloat(limitPrice) : undefined,
        idempotency_key: `ws-${instrumentId}-${Date.now()}`,
      })
    );
    if (result) {
      setIntent(result as ExecutionIntent);
      setNotice('意向已建立，需明確授權後才會提交。');
    }
  };

  const authorizeIntent = async () => {
    if (!intent?.authorization_token) return;
    const result = await call(() => api.execution.authorize(intent.intent_id, intent.authorization_token!));
    if (result) {
      setIntent(result as ExecutionIntent);
      setNotice('已授權。提交前請再次確認（受安全開關限制）。');
    }
  };

  const submitIntent = async () => {
    if (!intent) return;
    const result = await call(() => api.execution.submit(intent.intent_id));
    if (result) {
      setIntent(result as ExecutionIntent);
      setNotice('已提交受控執行器。成交會顯示在「成交」分頁。');
    }
  };

  const cancelIntent = async () => {
    if (!intent) return;
    const result = await call(() => api.execution.cancel(intent.intent_id));
    if (result) {
      setIntent(result as ExecutionIntent);
      setNotice('意向已取消。');
    }
  };

  const createPortfolio = async () => {
    const result = await call(() => api.paperPortfolio.create({ initial_virtual_cash: 100000 }));
    if (result) {
      await loadPortfolios();
      setNotice('已建立模擬投資組合（100,000 USD 虛擬資金）。');
    }
  };

  const intentStatus = intent?.status;

  return (
    <div className="ws-panel" data-testid="order-preview-panel">
      <div className="ws-panel-header">下單預覽（意向）</div>
      <div className="ws-panel-body" style={{ padding: 10 }}>
        {error && <div className="ws-message-error" data-testid="order-error">{error}</div>}
        {notice && <div className="ws-message-ok">{notice}</div>}

        <div className="ws-form-row">
          <div className="ws-form-field">
            <label>組合</label>
            {portfolios.length > 0 ? (
              <select value={portfolioId} onChange={(e) => setPortfolioId(e.target.value)}>
                {portfolios.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.currency} · {p.current_virtual_cash.toLocaleString()}
                  </option>
                ))}
              </select>
            ) : (
              <button className="ws-btn ws-btn-neutral" onClick={createPortfolio} disabled={busy}>
                建立模擬組合
              </button>
            )}
          </div>
        </div>

        <div className="ws-form-row">
          <div className="ws-form-field">
            <label>方向</label>
            <select value={side} onChange={(e) => setSide(e.target.value as 'BUY' | 'SELL')}>
              <option value="BUY">買入</option>
              <option value="SELL">賣出</option>
            </select>
          </div>
          <div className="ws-form-field">
            <label>手數（每手 {lotSize} 股）</label>
            <input
              type="number"
              min="1"
              step="1"
              value={lots}
              onChange={(e) => setLots(e.target.value)}
              data-testid="order-lots"
            />
          </div>
        </div>

        <div className="ws-form-row">
          <div className="ws-form-field">
            <label>類型</label>
            <select value={orderType} onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}>
              <option value="MARKET">市價</option>
              <option value="LIMIT">限價</option>
            </select>
          </div>
          {orderType === 'LIMIT' && (
            <div className="ws-form-field">
              <label>限價（{instrument?.currency || ''}）</label>
              <input
                type="number"
                min="0"
                step="0.01"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
              />
            </div>
          )}
        </div>

        <table className="ws-table" style={{ marginBottom: 10 }}>
          <tbody>
            <tr>
              <td className="ws-muted">數量</td>
              <td data-testid="order-quantity">{quantity} 股</td>
            </tr>
            <tr>
              <td className="ws-muted">參考價</td>
              <td>{lastClose !== null ? `${lastClose.toFixed(2)} ${instrument?.currency || ''}` : '—'}</td>
            </tr>
            <tr>
              <td className="ws-muted">預估金額</td>
              <td data-testid="order-estimate">
                {estimate !== null ? `${estimate.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${instrument?.currency || ''}` : '—'}
              </td>
            </tr>
          </tbody>
        </table>

        {!intent && (
          <button
            className={`ws-btn ${side === 'BUY' ? 'ws-btn-buy' : 'ws-btn-sell'}`}
            onClick={createIntent}
            disabled={busy || !portfolioId}
            data-testid="order-create-intent"
          >
            建立執行意向
          </button>
        )}

        {intent && (
          <div>
            <table className="ws-table" style={{ marginBottom: 8 }}>
              <tbody>
                <tr>
                  <td className="ws-muted">意向 ID</td>
                  <td data-testid="order-intent-id">{intent.intent_id.slice(0, 13)}…</td>
                </tr>
                <tr>
                  <td className="ws-muted">狀態</td>
                  <td data-testid="order-intent-status">
                    <span className="ws-status-badge">{intentStatus}</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {intentStatus === 'draft' && (
                <button className="ws-btn ws-btn-neutral" onClick={authorizeIntent} disabled={busy}>
                  確認授權
                </button>
              )}
              {intentStatus === 'authorized' && (
                <button className="ws-btn ws-btn-buy" onClick={submitIntent} disabled={busy} data-testid="order-submit">
                  提交執行器
                </button>
              )}
              {['draft', 'authorized', 'submitted'].includes(intentStatus || '') && (
                <button className="ws-btn ws-btn-neutral" onClick={cancelIntent} disabled={busy}>
                  取消
                </button>
              )}
              <Link href="/execution" className="ws-btn ws-btn-neutral" style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}>
                執行總覽
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
