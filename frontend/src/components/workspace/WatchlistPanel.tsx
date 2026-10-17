'use client';

import { useState } from 'react';
import type { Watchlist } from './types';

interface Props {
  watchlists: Watchlist[];
  currentSymbol: string;
  loading: boolean;
  onSelectSymbol: (symbol: string) => void;
  onAddCurrent: () => void;
  onRemoveItem: (watchlistId: string, symbol: string) => void;
  onMoveItem: (watchlistId: string, symbol: string, direction: -1 | 1) => void;
  onCreate: (name?: string) => void;
  onDelete: (watchlistId: string) => void;
}

export default function WatchlistPanel({
  watchlists,
  currentSymbol,
  loading,
  onSelectSymbol,
  onAddCurrent,
  onRemoveItem,
  onMoveItem,
  onCreate,
  onDelete,
}: Props) {
  const [busy, setBusy] = useState(false);

  const handleAdd = async () => {
    setBusy(true);
    try {
      await onAddCurrent();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ws-panel" data-testid="watchlist-panel">
      <div className="ws-panel-header">
        觀察列表
        <div className="ws-panel-header-actions">
          <button
            className="ws-icon-btn"
            title="將當前代號加入觀察列表"
            onClick={handleAdd}
            disabled={busy}
            data-testid="watchlist-add-current"
          >
            ＋
          </button>
          {watchlists.length === 0 && (
            <button
              className="ws-icon-btn"
              title="建立觀察列表"
              onClick={() => onCreate()}
              data-testid="watchlist-create"
            >
              新增
            </button>
          )}
        </div>
      </div>
      <div className="ws-panel-body">
        {loading && <div className="ws-muted" style={{ padding: 10 }}>載入中…</div>}
        {!loading && watchlists.length === 0 && (
          <div className="ws-muted" style={{ padding: 10 }}>
            還沒有觀察列表。按「＋」將當前代號加入，會自動建立預設列表。
          </div>
        )}
        {watchlists.map((wl) => (
          <div key={wl.id} style={{ borderBottom: '1px solid #2a2e39' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                padding: '5px 10px',
                background: '#232733',
              }}
            >
              <span className="ws-small" style={{ fontWeight: 600 }}>
                {wl.name}
              </span>
              <span className="ws-muted" style={{ marginLeft: 6 }}>
                {wl.items.length}
              </span>
              <button
                className="ws-icon-btn"
                style={{ marginLeft: 'auto' }}
                title="刪除此觀察列表"
                onClick={() => onDelete(wl.id)}
              >
                ✕
              </button>
            </div>
            {wl.items.length === 0 && (
              <div className="ws-muted" style={{ padding: '6px 10px' }}>（空的）</div>
            )}
            {wl.items.map((item, idx) => {
              const market = item.symbol.split(':')[0];
              return (
                <div
                  key={item.symbol}
                  className={`ws-symbol-row ${item.symbol === currentSymbol ? 'ws-symbol-row-active' : ''}`}
                  onClick={() => onSelectSymbol(item.symbol)}
                >
                  <span className="ws-market-badge">{market}</span>
                  <span className="ws-symbol-label">{item.symbol.split(':')[1]}</span>
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 2 }}>
                    <button
                      className="ws-icon-btn"
                      title="上移"
                      onClick={(e) => {
                        e.stopPropagation();
                        onMoveItem(wl.id, item.symbol, -1);
                      }}
                      disabled={idx === 0}
                    >
                      ↑
                    </button>
                    <button
                      className="ws-icon-btn"
                      title="下移"
                      onClick={(e) => {
                        e.stopPropagation();
                        onMoveItem(wl.id, item.symbol, 1);
                      }}
                      disabled={idx === wl.items.length - 1}
                    >
                      ↓
                    </button>
                    <button
                      className="ws-icon-btn"
                      title="移除"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemoveItem(wl.id, item.symbol);
                      }}
                    >
                      ✕
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
