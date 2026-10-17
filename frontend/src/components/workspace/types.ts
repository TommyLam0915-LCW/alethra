export interface Instrument {
  instrument_id: string;
  symbol: string;
  name: string;
  market: string;
  exchange: string;
  asset_type: string;
  currency: string;
  timezone: string;
  lot_size: number | null;
  lot_size_verified: boolean;
}

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface DataQuality {
  data_nature: string;
  adjustment: string;
  timezone: string | null;
  note: string | null;
  note_zh: string | null;
  latest_bar_date?: string | null;
  bar_age_days?: number | null;
  is_stale?: boolean;
  stale_threshold_days?: number;
}

export interface BarsResponse {
  instrument_id: string;
  symbol: string;
  name: string;
  currency: string;
  provider: string;
  feed_mode: string;
  start_date: string;
  end_date: string;
  bar_count: number;
  data_quality: DataQuality | null;
  bars: Bar[];
  disclaimer: string;
  disclaimer_zh: string;
}

export interface InstrumentDetail extends Instrument {
  latest_bar: Bar | null;
  data_quality: DataQuality | null;
  data_source: string;
  feed_mode: string;
  provider_mappings?: { provider_id: string; provider_symbol: string }[];
}

export interface WatchlistItem {
  symbol: string;
  position: number;
  added_at: string;
}

export interface Watchlist {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  items: WatchlistItem[];
}

export interface ExecutionIntent {
  intent_id: string;
  portfolio_id: string;
  symbol: string;
  side: string;
  quantity: number;
  order_type: string;
  limit_price: number | null;
  status: string;
  authorization_token?: string | null;
  created_at: string;
  updated_at: string;
}

export interface FillRecord {
  id: string;
  intent_id: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  commission?: number;
  fill_time?: string;
  filled_at?: string;
  created_at: string;
}

export type BottomTabKey = 'research' | 'models' | 'positions' | 'orders' | 'fills' | 'log';

export interface WorkspaceLayout {
  version: 1;
  symbol: string;
  bottomTab: BottomTabKey;
  range: '3M' | '6M' | '1Y' | 'ALL';
  panels: {
    left: { size: number; collapsed: boolean };
    bottom: { size: number; collapsed: boolean };
    right: { size: number; collapsed: boolean };
    rightAI: { size: number; collapsed: boolean };
  };
}

export const DEFAULT_LAYOUT: WorkspaceLayout = {
  version: 1,
  symbol: 'US:AAPL',
  bottomTab: 'research',
  range: '1Y',
  panels: {
    left: { size: 16, collapsed: false },
    bottom: { size: 30, collapsed: false },
    right: { size: 24, collapsed: false },
    rightAI: { size: 42, collapsed: false },
  },
};

const LAYOUT_STORAGE_KEY = 'workspace-layout-cache';

export function mergeLayout(saved: unknown): WorkspaceLayout {
  const layout: WorkspaceLayout = { ...DEFAULT_LAYOUT, panels: { ...DEFAULT_LAYOUT.panels } };
  if (!saved || typeof saved !== 'object') return layout;
  const s = saved as Partial<WorkspaceLayout>;
  if (typeof s.symbol === 'string' && s.symbol) layout.symbol = s.symbol;
  if (s.bottomTab && ['research', 'models', 'positions', 'orders', 'fills', 'log'].includes(s.bottomTab)) {
    layout.bottomTab = s.bottomTab;
  }
  if (s.range && ['3M', '6M', '1Y', 'ALL'].includes(s.range)) {
    layout.range = s.range;
  }
  if (s.panels && typeof s.panels === 'object') {
    for (const key of ['left', 'bottom', 'right', 'rightAI'] as const) {
      const p = (s.panels as Record<string, { size?: number; collapsed?: boolean } | undefined>)[key];
      if (p && typeof p === 'object') {
        if (typeof p.size === 'number' && p.size > 3 && p.size < 90) layout.panels[key].size = p.size;
        if (typeof p.collapsed === 'boolean') layout.panels[key].collapsed = p.collapsed;
      }
    }
  }
  return layout;
}

export function readCachedLayout(): WorkspaceLayout | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return null;
    return mergeLayout(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function writeCachedLayout(layout: WorkspaceLayout) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // storage full/unavailable — server persistence still applies
  }
}

export function rangeToStart(range: WorkspaceLayout['range']): string {
  const now = new Date();
  if (range === 'ALL') return '1900-01-01';
  const days = range === '3M' ? 92 : range === '6M' ? 184 : 366;
  const start = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
  return start.toISOString().slice(0, 10);
}

export function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}
