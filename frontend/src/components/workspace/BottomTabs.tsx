'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { BottomTabKey, ExecutionIntent, FillRecord } from './types';

const TAB_LABELS: Record<BottomTabKey, string> = {
  research: '研究',
  models: '模型',
  positions: '持倉',
  orders: '訂單',
  fills: '成交',
  log: '紀錄',
};

const EXPERIMENT_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  exploring: '探索中',
  locked: '已鎖定',
  archived: '已封存',
};

export default function BottomTabs({
  active,
  onChange,
}: {
  active: BottomTabKey;
  onChange: (tab: BottomTabKey) => void;
}) {
  const [rows, setRows] = useState<Record<string, JSX.Element | null>>({});
  const [loading, setLoading] = useState(false);

  const loadTab = useCallback(async (tab: BottomTabKey) => {
    setLoading(true);
    try {
      if (tab === 'research') {
        const data = await api.experiments.list();
        setRows((prev) => ({
          ...prev,
          research: data.length === 0 ? emptyHint('沒有實驗', '到「研究」頁建立第一個實驗。', '/experiments/new') : (
            <table className="ws-table">
              <thead>
                <tr>
                  <th>標題</th>
                  <th>狀態</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                {data.slice(0, 30).map((e: { id: string; title: string; status: string; updated_at: string }) => (
                  <tr key={e.id}>
                    <td>
                      <Link href={`/experiments/${e.id}`}>{e.title}</Link>
                    </td>
                    <td>{EXPERIMENT_STATUS_LABELS[e.status] || e.status}</td>
                    <td>{new Date(e.updated_at).toLocaleString('zh-Hant')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ),
        }));
      } else if (tab === 'models') {
        const response = await api.ml.listModels();
        const models = response?.models || [];
        setRows((prev) => ({
          ...prev,
          models: models.length === 0 ? emptyHint('沒有模型', '到「訓練」頁開始第一次訓練。', '/training') : (
            <table className="ws-table">
              <thead>
                <tr>
                  <th>模型</th>
                  <th>類型</th>
                  <th>建立</th>
                </tr>
              </thead>
              <tbody>
                {models.slice(0, 30).map((m: { model_id?: string; id?: string; name?: string; model_type?: string; created_at?: string; trained_at?: string; algorithm?: string }) => (
                  <tr key={m.model_id || m.id}>
                    <td>
                      <Link href="/models">{m.name || (m.model_id || m.id || '').slice(0, 8)}</Link>
                    </td>
                    <td>{m.model_type || m.algorithm || '—'}</td>
                    <td>{(m.trained_at || m.created_at) ? new Date(m.trained_at || m.created_at).toLocaleString('zh-Hant') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ),
        }));
      } else if (tab === 'positions') {
        const portfolios = await api.paperPortfolio.list();
        const active = (portfolios || []).filter((p: { status: string }) => p.status === 'active');
        if (active.length === 0) {
          setRows((prev) => ({ ...prev, positions: emptyHint('沒有模擬組合', '在右側「下單預覽」建立模擬組合。', '/paper-portfolio') }));
          return;
        }
        const positions = await api.paperPortfolio.positions(active[0].id);
        setRows((prev) => ({
          ...prev,
          positions: !positions || positions.length === 0 ? emptyHint('沒有持倉', '通過右側「下單預覽」建立第一筆意向。', '/paper-portfolio') : (
            <table className="ws-table">
              <thead>
                <tr>
                  <th>資產</th>
                  <th>數量</th>
                  <th>均價</th>
                  <th>狀態</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p: { id: string; asset_id: string; quantity: number; average_price?: number; status: string }) => (
                  <tr key={p.id}>
                    <td>{p.asset_id}</td>
                    <td>{p.quantity}</td>
                    <td>{p.average_price != null ? p.average_price.toFixed(2) : '—'}</td>
                    <td>{p.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ),
        }));
      } else if (tab === 'orders') {
        const intents = await api.execution.listIntents();
        setRows((prev) => ({
          ...prev,
          orders: !intents || intents.length === 0 ? emptyHint('沒有執行意向', '在右側「下單預覽」建立意向。', '/execution') : (
            <table className="ws-table">
              <thead>
                <tr>
                  <th>代號</th>
                  <th>方向</th>
                  <th>數量</th>
                  <th>狀態</th>
                  <th>時間</th>
                </tr>
              </thead>
              <tbody>
                {intents.slice(0, 30).map((i: ExecutionIntent) => (
                  <tr key={i.intent_id}>
                    <td>{i.symbol}</td>
                    <td style={{ color: i.side === 'BUY' ? '#089981' : '#f23645' }}>{i.side === 'BUY' ? '買' : '賣'}</td>
                    <td>{i.quantity}</td>
                    <td>{i.status}</td>
                    <td>{new Date(i.created_at).toLocaleString('zh-Hant')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ),
        }));
      } else if (tab === 'fills') {
        const intents = (await api.execution.listIntents()) as ExecutionIntent[];
        const recent = (intents || []).slice(0, 10);
        const fillLists = await Promise.all(
          recent.map((i) =>
            api.execution
              .listFills(i.intent_id)
              .then((fills: FillRecord[]) => (fills || []).map((f) => ({ ...f, symbol: i.symbol })))
              .catch(() => [])
          )
        );
        const fills = fillLists.flat();
        setRows((prev) => ({
          ...prev,
          fills: fills.length === 0 ? emptyHint('沒有成交', '成交會在執行器回報後出現。', '/execution') : (
            <table className="ws-table">
              <thead>
                <tr>
                  <th>代號</th>
                  <th>方向</th>
                  <th>數量</th>
                  <th>價格</th>
                  <th>時間</th>
                </tr>
              </thead>
              <tbody>
                {fills.map((f) => (
                  <tr key={f.id}>
                    <td>{f.symbol}</td>
                    <td style={{ color: f.side === 'BUY' ? '#089981' : '#f23645' }}>{f.side === 'BUY' ? '買' : '賣'}</td>
                    <td>{f.quantity}</td>
                    <td>{f.price?.toFixed(2)}</td>
                    <td>{new Date(f.created_at).toLocaleString('zh-Hant')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ),
        }));
      } else if (tab === 'log') {
        const events = await api.pilot.getMyUsageEvents();
        setRows((prev) => ({
          ...prev,
          log: !events || events.length === 0 ? (
            <div className="ws-muted" style={{ padding: 10 }}>
              沒有系統活動紀錄。
            </div>
          ) : (
            <table className="ws-table">
              <thead>
                <tr>
                  <th>時間</th>
                  <th>事件</th>
                </tr>
              </thead>
              <tbody>
                {events.slice(0, 50).map((e: { id: string; event_type: string; created_at: string }) => (
                  <tr key={e.id}>
                    <td>{new Date(e.created_at).toLocaleString('zh-Hant')}</td>
                    <td>{e.event_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ),
        }));
      }
    } catch {
      setRows((prev) => ({
        ...prev,
        [tab]: (
          <div className="ws-message-error" style={{ margin: 10 }}>
            載入失敗，請重試。
          </div>
        ),
      }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTab(active);
  }, [active, loadTab]);

  return (
    <div className="ws-panel" data-testid="bottom-tabs">
      <div className="ws-tabs">
        {(Object.keys(TAB_LABELS) as BottomTabKey[]).map((tab) => (
          <button
            key={tab}
            className={`ws-tab ${tab === active ? 'ws-tab-active' : ''}`}
            onClick={() => onChange(tab)}
            data-testid={`tab-${tab}`}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
        <button
          className="ws-icon-btn"
          style={{ marginLeft: 'auto', alignSelf: 'center' }}
          title="重新載入"
          onClick={() => loadTab(active)}
        >
          ⟳
        </button>
      </div>
      <div className="ws-panel-body" style={{ position: 'relative' }}>
        {loading && <div className="ws-muted" style={{ padding: 8 }}>載入中…</div>}
        {rows[active]}
      </div>
    </div>
  );
}

function emptyHint(title: string, message: string, href: string) {
  return (
    <div style={{ padding: 14 }}>
      <div className="ws-symbol-label" style={{ marginBottom: 4 }}>
        {title}
      </div>
      <div className="ws-muted" style={{ marginBottom: 8 }}>{message}</div>
      <Link href={href} className="ws-btn ws-btn-neutral" style={{ textDecoration: 'none', display: 'inline-block' }}>
        前往
      </Link>
    </div>
  );
}
