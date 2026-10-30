'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { DataWindow, Instrument } from './types';

const BROWSE_LIMIT = 20;

/** What the provider says about bars for this instrument — never a guess. */
function windowLabel(win: DataWindow | null | undefined): { text: string; missing: boolean } {
  if (!win || win.availability_source !== 'provider_declared') {
    return { text: 'Data period unknown (UNKNOWN)', missing: false };
  }
  if (!win.available_start || !win.available_end) {
    return { text: 'No data available', missing: true };
  }
  return { text: `${win.available_start} to ${win.available_end}`, missing: false };
}

export default function SymbolSearch({ onSelect }: { onSelect: (instrumentId: string) => void }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Instrument[]>([]);
  const [browse, setBrowse] = useState<Instrument[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // The picker must answer "what can I look at?" before the user knows to type.
  useEffect(() => {
    let cancelled = false;
    api.instruments
      .list(undefined, undefined, undefined, BROWSE_LIMIT)
      .then((data) => {
        if (!cancelled) setBrowse(data.instruments || []);
      })
      .catch(() => {
        if (!cancelled) setBrowse([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    // Rows must never outlive the query they came from: a stale list under a new
    // query gets clicked as if it belonged to it.
    setResults([]);
    setActiveIndex(0);
    if (!query.trim()) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const data = await api.instruments.search(query.trim());
        if (!cancelled) setResults((data.instruments || []).slice(0, 8));
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query]);

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  const searching = !!query.trim();
  const items = searching ? results : browse;

  const pick = (instrumentId: string) => {
    onSelect(instrumentId);
    setQuery('');
    setResults([]);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || items.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const item = items[activeIndex];
      if (item) pick(item.instrument_id);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="ws-search-wrap" ref={wrapRef}>
      <input
        className="ws-search-input"
        placeholder="Search symbol or name (US, HK)…"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        aria-label="Search symbols"
        aria-expanded={open}
        role="combobox"
        aria-controls="symbol-search-listbox"
      />
      {open && (
        <div className="ws-search-dropdown" id="symbol-search-listbox" data-testid="symbol-search-dropdown">
          <div className="ws-search-group-label" data-testid="symbol-search-context">
            {searching ? `Search “${query.trim()}”` : `Available symbols (up to ${BROWSE_LIMIT})`}
          </div>
          {searching && loading && <div className="ws-muted" style={{ padding: 10 }}>Searching…</div>}
          {!loading && items.length === 0 && (
            <div className="ws-muted" style={{ padding: 10 }}>No matching symbols</div>
          )}
          {items.map((inst, i) => {
            const win = windowLabel(inst.data_window);
            return (
              <div
                key={inst.instrument_id}
                className={`ws-search-item ${i === activeIndex ? 'ws-search-item-active' : ''}`}
                onClick={() => pick(inst.instrument_id)}
                onMouseEnter={() => setActiveIndex(i)}
                data-testid="symbol-search-item"
              >
                <span className="ws-market-badge">{inst.market}</span>
                <span className="ws-symbol-label">{inst.symbol}</span>
                <span className="ws-small">{inst.name}</span>
                <span
                  className={`ws-muted ws-search-window${win.missing ? ' ws-search-window-missing' : ''}`}
                  data-testid="symbol-search-window"
                >
                  {win.text}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
