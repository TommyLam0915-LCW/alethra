export interface DataWindow {
  timeframe: string;
  available_start: string | null;
  available_end: string | null;
  availability_source: 'provider_declared' | 'unknown';
}

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
  data_window?: DataWindow | null;
}

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  /** Bar start in the exchange timezone, e.g. 2024-01-15T00:00:00-05:00. */
  market_timestamp?: string | null;
  /** Bar granularity the timestamp is meaningful at; 'unknown' if unparsable. */
  timestamp_precision?: string;
  /** When this server obtained the bar (UTC). */
  received_at?: string | null;
}

/** How a price view should be read: what it is, and what the market is doing. */
export interface PriceViewMeta {
  timeframe?: string;
  timestamp_semantics?: string | null;
  mode?: 'live_realtime' | 'delayed' | 'historical_replay' | 'synthetic_fixture' | 'unknown';
  mode_label_zh?: string;
  data_nature?: string;
  is_realtime_capable?: boolean;
  data_as_of?: string | null;
  market_status?: 'open' | 'closed' | 'stale' | 'no_data' | 'disconnected';
  market_status_label_zh?: string;
  market_status_reason?: string;
  current_session?: string | null;
  is_regular_session?: boolean | null;
  next_session?: string | null;
  next_session_open?: string | null;
  next_regular_open?: string | null;
  exchange_local_time?: string;
  holiday_calendar_populated?: boolean;
}

export interface BarsResponse extends PriceViewMeta {
  instrument_id: string;
  symbol: string;
  name: string;
  market: string;
  exchange: string;
  currency: string;
  timezone: string;
  provider: string;
  feed_mode: string;
  start_date: string;
  end_date: string;
  bar_count: number;
  series_received_at?: string;
  data_quality: DataQuality | null;
  bars: Bar[];
  disclaimer: string;
  disclaimer_zh: string;
}

export interface InstrumentDetail extends Instrument, PriceViewMeta {
  latest_bar: Bar | null;
  data_quality: DataQuality | null;
  data_coverage: DataCoverage | null;
  data_source: string;
  feed_mode: string;
  received_at?: string;
  provider_mappings?: { provider_id: string; provider_symbol: string }[];
}

/**
 * Where an instrument's bars actually are. `available_*` is what the provider
 * declares it can serve and is only non-null when `availability_source` is
 * 'provider_declared'; `stored_*` is what this server has already cached. They
 * are different questions — cached rows are not proof that nothing else exists.
 */
export interface DataCoverage {
  timeframe: string;
  provider_id: string;
  available_start: string | null;
  available_end: string | null;
  availability_source: 'provider_declared' | 'unknown';
  stored_start: string | null;
  stored_end: string | null;
  stored_bars: number;
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

/**
 * Container ⑧ (evidence dock) tabs. `WorkspaceLayout.bottomTab` keeps its stored
 * name and its old values: a layout saved before the dock merged the order and
 * fill tabs migrates on read instead of losing the user's choice.
 */
export type EvidenceTabKey =
  | 'positions'
  | 'orders-fills'
  | 'journal'
  | 'results'
  | 'research'
  | 'models'
  | 'log';

export const EVIDENCE_TABS: { key: EvidenceTabKey; label: string }[] = [
  { key: 'positions', label: '持倉' },
  { key: 'orders-fills', label: '訂單與成交' },
  { key: 'journal', label: '自動交易日誌' },
  { key: 'results', label: '結果' },
  { key: 'research', label: '研究' },
  { key: 'models', label: '模型' },
  { key: 'log', label: '系統紀錄' },
];

const LEGACY_TAB_ALIASES: Record<string, EvidenceTabKey> = {
  orders: 'orders-fills',
  fills: 'orders-fills',
};

export function normalizeEvidenceTab(value: string | null | undefined): EvidenceTabKey {
  if (!value) return 'positions';
  const mapped = LEGACY_TAB_ALIASES[value] ?? value;
  return EVIDENCE_TABS.some((t) => t.key === mapped) ? (mapped as EvidenceTabKey) : 'positions';
}

export interface WorkspaceLayout {
  version: 1;
  symbol: string;
  bottomTab: EvidenceTabKey;
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
  bottomTab: 'positions',
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
  const layout: WorkspaceLayout = {
    ...DEFAULT_LAYOUT,
    panels: {
      left: { ...DEFAULT_LAYOUT.panels.left },
      bottom: { ...DEFAULT_LAYOUT.panels.bottom },
      right: { ...DEFAULT_LAYOUT.panels.right },
      rightAI: { ...DEFAULT_LAYOUT.panels.rightAI },
    },
  };
  if (!saved || typeof saved !== 'object') return layout;
  const s = saved as Partial<WorkspaceLayout>;
  if (typeof s.symbol === 'string' && s.symbol) layout.symbol = s.symbol;
  layout.bottomTab = normalizeEvidenceTab(s.bottomTab);
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

export function rangeToStart(range: WorkspaceLayout['range'], anchor?: string | null): string {
  if (range === 'ALL') return '1900-01-01';
  const days = range === '3M' ? 92 : range === '6M' ? 184 : 366;
  // A replay cursor must anchor the window itself: anchoring on today would put
  // the start after the cursor and the clipped request would come back empty.
  const base = anchor ? new Date(`${anchor}T00:00:00Z`) : new Date();
  if (Number.isNaN(base.getTime())) return '1900-01-01';
  const start = new Date(base.getTime() - days * 24 * 60 * 60 * 1000);
  return start.toISOString().slice(0, 10);
}

export function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}
