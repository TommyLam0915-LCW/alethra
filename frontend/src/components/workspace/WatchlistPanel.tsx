'use client';

import { useState, type ReactNode } from 'react';
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
  extraActions?: ReactNode;
  className?: string;
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
  extraActions,
  className,
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
    <div className={`ws-panel${className ? ` ${className}` : ''}`} data-testid="watchlist-panel">
      <h3 className="ws-panel-header">
        觀察列表
        <div className="ws-panel-header-actions">
          <button
            className="ws-icon-btn"
            title="將當前代號加入觀察列表"
            aria-label={`將 ${currentSymbol} 加入觀察列表`}
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
          {extraActions}
        </div>
      </h3>
      <div className="ws-panel-body">
        {loading && <div className="ws-muted" style={{ padding: 10 }}>載入中…</div>}
        {!loading && watchlists.length === 0 && (
          <div className="ws-muted" style={{ padding: 10 }}>
            還沒有觀察列表。按「＋」將當前代號加入，會自動建立預設列表。
          </div>
        )}
        {watchlists.map((wl) => (
          <div key={wl.id}>
            <div className="ws-watchlist-group">
              <span className="ws-small" style={{ fontWeight: 600 }}>
                {wl.name}
              </span>
              <span className="ws-muted">{wl.items.length}</span>
              <button
                className="ws-icon-btn"
                style={{ marginLeft: 'auto' }}
                title="刪除此觀察列表"
                aria-label={`刪除觀察列表 ${wl.name}`}
                onClick={() => onDelete(wl.id)}
              >
                ✕
              </button>
            </div>
            {wl.items.length === 0 && (
              <div className="ws-muted" style={{ padding: '6px 10px' }} data-testid="watchlist-group-empty">
                這個列表還沒有代號。按「＋」加入 {currentSymbol}，或以頂部搜尋框選取其他符號。
              </div>
            )}
            {wl.items.map((item, idx) => {
              const market = item.symbol.split(':')[0];
              const code = item.symbol.split(':')[1];
              const active = item.symbol === currentSymbol;
              return (
                <div
                  key={item.symbol}
                  className={`ws-symbol-row ${active ? 'ws-symbol-row-active' : ''}`}
                >
                  <button
                    className="ws-symbol-pick"
                    onClick={() => onSelectSymbol(item.symbol)}
                    aria-current={active ? 'true' : undefined}
                    aria-label={`選取標的 ${item.symbol}`}
                  >
                    <span className="ws-market-badge">{market}</span>
                    <span className="ws-symbol-label">{code}</span>
                  </button>
                  <div className="ws-row-actions">
                    <button
                      className="ws-icon-btn"
                      title="上移"
                      aria-label={`上移 ${item.symbol}`}
                      onClick={() => onMoveItem(wl.id, item.symbol, -1)}
                      disabled={idx === 0}
                    >
                      ↑
                    </button>
                    <button
                      className="ws-icon-btn"
                      title="下移"
                      aria-label={`下移 ${item.symbol}`}
                      onClick={() => onMoveItem(wl.id, item.symbol, 1)}
                      disabled={idx === wl.items.length - 1}
                    >
                      ↓
                    </button>
                    <button
                      className="ws-icon-btn"
                      title="移除"
                      aria-label={`從 ${wl.name} 移除 ${item.symbol}`}
                      onClick={() => onRemoveItem(wl.id, item.symbol)}
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
