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
        Watchlists
        <div className="ws-panel-header-actions">
          <button
            className="ws-icon-btn"
            title="Add the current symbol to a watchlist"
            aria-label={`Add ${currentSymbol} to a watchlist`}
            onClick={handleAdd}
            disabled={busy}
            data-testid="watchlist-add-current"
          >
            +
          </button>
          {watchlists.length === 0 && (
            <button
              className="ws-icon-btn"
              title="Create a watchlist"
              onClick={() => onCreate()}
              data-testid="watchlist-create"
            >
              New
            </button>
          )}
          {extraActions}
        </div>
      </h3>
      <div className="ws-panel-body">
        {loading && (
          <div className="ws-muted" style={{ padding: 10 }} role="status">
            Loading…
          </div>
        )}
        {!loading && watchlists.length === 0 && (
          <div className="ws-muted" style={{ padding: 10 }}>
            No watchlists yet. Choose “+” to add the current symbol and a default list is created
            automatically.
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
                title="Delete this watchlist"
                aria-label={`Delete watchlist ${wl.name}`}
                onClick={() => onDelete(wl.id)}
              >
                ✕
              </button>
            </div>
            {wl.items.length === 0 && (
              <div className="ws-muted" style={{ padding: '6px 10px' }} data-testid="watchlist-group-empty">
                This list has no symbols yet. Choose “+” to add {currentSymbol}, or pick another
                symbol in the search at the top.
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
                    aria-label={`Select ${item.symbol}`}
                  >
                    <span className="ws-market-badge">{market}</span>
                    <span className="ws-symbol-label">{code}</span>
                  </button>
                  <div className="ws-row-actions">
                    <button
                      className="ws-icon-btn"
                      title="Move up"
                      aria-label={`Move ${item.symbol} up`}
                      onClick={() => onMoveItem(wl.id, item.symbol, -1)}
                      disabled={idx === 0}
                    >
                      ↑
                    </button>
                    <button
                      className="ws-icon-btn"
                      title="Move down"
                      aria-label={`Move ${item.symbol} down`}
                      onClick={() => onMoveItem(wl.id, item.symbol, 1)}
                      disabled={idx === wl.items.length - 1}
                    >
                      ↓
                    </button>
                    <button
                      className="ws-icon-btn"
                      title="Remove"
                      aria-label={`Remove ${item.symbol} from ${wl.name}`}
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
