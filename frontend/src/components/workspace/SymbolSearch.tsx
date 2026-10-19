'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { Instrument } from './types';

export default function SymbolSearch({ onSelect }: { onSelect: (instrumentId: string) => void }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Instrument[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const data = await api.instruments.search(query.trim());
        setResults((data.instruments || []).slice(0, 8));
        setActiveIndex(0);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => clearTimeout(t);
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

  const pick = (instrumentId: string) => {
    onSelect(instrumentId);
    setQuery('');
    setResults([]);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const item = results[activeIndex];
      if (item) pick(item.instrument_id);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="ws-search-wrap" ref={wrapRef}>
      <input
        className="ws-search-input"
        placeholder="搜尋代號 / 名稱（US、HK）…"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        aria-label="搜尋代號"
      />
      {open && query.trim() && (
        <div className="ws-search-dropdown">
          {loading && <div className="ws-muted" style={{ padding: 10 }}>搜尋中…</div>}
          {!loading && results.length === 0 && (
            <div className="ws-muted" style={{ padding: 10 }}>沒有符合的代號</div>
          )}
          {results.map((inst, i) => (
            <div
              key={inst.instrument_id}
              className={`ws-search-item ${i === activeIndex ? 'ws-search-item-active' : ''}`}
              onClick={() => pick(inst.instrument_id)}
              onMouseEnter={() => setActiveIndex(i)}
            >
              <span className="ws-market-badge">{inst.market}</span>
              <span className="ws-symbol-label">{inst.symbol}</span>
              <span className="ws-small">{inst.name}</span>
              <span className="ws-muted" style={{ marginLeft: 'auto' }}>{inst.currency}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
