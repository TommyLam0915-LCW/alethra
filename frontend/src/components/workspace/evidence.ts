'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { FillRecord } from './types';
import type { ReplaySession } from './ReplayController';

/**
 * Data layer for container ⑧ (Evidence Dock).
 *
 * Everything here is read from the owner's own paper-portfolio endpoints. Three
 * presentation constants keep the design brief's distinction between a value the
 * API never sent, a value that cannot be computed, and a value that does not
 * apply to the record — none of them may collapse into `0.00`.
 */
export const NOT_PROVIDED = '未提供';
export const CANNOT_COMPUTE = '無法計算';
export const NOT_APPLICABLE = '不適用';

/** How the dock came to show this portfolio — shown to the user, never implicit. */
export type PortfolioResolution =
  | { kind: 'chosen'; reason: string }
  | { kind: 'session-bound'; reason: string }
  | { kind: 'active-single'; reason: string }
  | { kind: 'none'; reason: string };

/**
 * The portfolio the rest of the workspace is already speaking for — the order
 * form's current selection. The dock follows it instead of resolving its own, so
 * one screen cannot name two books.
 */
export interface PortfolioChoice {
  id: string;
  reason: string;
}

export interface ValuationBasis {
  mode?: string | null;
  mode_label_zh?: string | null;
  provider?: string | null;
  data_as_of?: string | null;
  unpriced_assets?: string[] | null;
  note?: string | null;
}

export interface PortfolioRow {
  id: string;
  status: string;
  currency: string;
  simulation_session_id: string | null;
  initial_virtual_cash: number;
  current_virtual_cash: number;
  total_realized_pnl: number | null;
  total_unrealized_pnl: number | null;
  valuation?: ValuationBasis | null;
  created_at?: string | null;
}

export interface PositionRow {
  id: string;
  portfolio_id: string;
  asset_id: string;
  quantity: number;
  average_entry_price: number | null;
  opened_at: string | null;
  closed_at: string | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  status: string;
}

export interface OrderRow {
  id: string;
  portfolio_id: string;
  decision_id: string | null;
  asset_id: string;
  side: string;
  quantity: number | null;
  order_type: string | null;
  created_at: string | null;
  status: string;
  model_or_strategy_version_id: string | null;
  data_cutoff_at: string | null;
  rationale: string | null;
  risk_check_status: string | null;
  actor_type: string;
  idempotency_key: string | null;
}

/** One journal row per executed fill, plus one per order that never filled. */
export interface JournalRecord {
  record_type: string;
  record_classification: string;
  portfolio_id: string;
  currency: string;
  order_id: string;
  fill_id: string | null;
  decision_id: string | null;
  asset_id: string;
  side: string | null;
  order_type: string | null;
  order_status: string | null;
  actor_type: string | null;
  decision_type: string | null;
  outcome_status: string | null;
  data_cutoff_at: string | null;
  execution_date: string | null;
  bar_timestamp: string | null;
  order_created_at_utc: string | null;
  observed_price: number | null;
  execution_price_before_costs: number | null;
  slippage_amount: number | null;
  commission_amount: number | null;
  quantity: number | null;
  gross_amount: number | null;
  net_cash_change: number | null;
  simulated_fill_reason: string | null;
  rationale: string | null;
  [key: string]: unknown;
}

export interface JournalResponse {
  portfolio_id: string;
  currency: string;
  columns: string[];
  column_semantics?: Record<string, string>;
  row_count: number;
  records: JournalRecord[];
}

export interface DecisionRow {
  id: string;
  actor_type: string;
  decision_type: string;
  decision_time: string | null;
  data_cutoff_at: string | null;
  rationale: string | null;
  expected_outcome: string | null;
  self_reported_confidence: string | null;
  outcome_status: string | null;
  risk_flags: Record<string, unknown> | null;
}

export type DatasetKey = 'portfolio' | 'positions' | 'orders' | 'journal' | 'decisions' | 'intents';

export interface EvidenceData {
  portfolio: PortfolioRow | null;
  resolution: PortfolioResolution;
  positions: PositionRow[];
  orders: OrderRow[];
  journal: JournalRecord[];
  journalColumns: string[];
  columnSemantics: Record<string, string>;
  decisions: DecisionRow[];
  loading: boolean;
  /** Endpoint-level failures, keyed by dataset, shown per tab instead of silently empty. */
  errors: Partial<Record<DatasetKey, string>>;
  reload: () => Promise<void>;
}

const EMPTY: Omit<EvidenceData, 'reload'> = {
  portfolio: null,
  resolution: { kind: 'none', reason: '尚未查詢組合清單。' },
  positions: [],
  orders: [],
  journal: [],
  journalColumns: [],
  columnSemantics: {},
  decisions: [],
  loading: false,
  errors: {},
};

const asList = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);

/**
 * Resolve which portfolio the dock speaks for, and tell the user how that was
 * decided. The workspace's current choice wins — including a retired one, whose
 * history stays viewable — because the strip, the order form and this dock must
 * not name three different books. A replay-bound portfolio comes next. What is
 * never done is picking "the first active one" on the user's behalf.
 */
export function resolvePortfolio(
  list: PortfolioRow[],
  sessionId: string | null | undefined,
  chosen?: PortfolioChoice | null
): { portfolio: PortfolioRow | null; resolution: PortfolioResolution } {
  if (chosen) {
    const picked = list.find((p) => p.id === chosen.id) ?? null;
    if (picked) return { portfolio: picked, resolution: { kind: 'chosen', reason: chosen.reason } };
    return {
      portfolio: null,
      resolution: {
        kind: 'none',
        reason: `已選擇的組合 ${chosen.id.slice(0, 8)} 不在清單中，因此未顯示任何組合的證據；未擅自改選其他組合。`,
      },
    };
  }
  if (sessionId) {
    const bound = list.find((p) => p.simulation_session_id === sessionId) ?? null;
    if (bound) {
      return {
        portfolio: bound,
        resolution: {
          kind: 'session-bound',
          reason: `此組合的 simulation_session_id 等於目前回放工作階段 ${sessionId.slice(0, 8)}。`,
        },
      };
    }
    return {
      portfolio: null,
      resolution: {
        kind: 'none',
        reason: `${list.length} 個組合沒有任何一個的 simulation_session_id 等於目前工作階段 ${sessionId.slice(0, 8)}，因此沒有綁定此回放階段的證據可顯示。`,
      },
    };
  }
  const active = list.filter((p) => p.status === 'active');
  if (active.length === 1) {
    return {
      portfolio: active[0],
      resolution: { kind: 'active-single', reason: '目前沒有回放工作階段；此帳戶只有一個進行中的組合，故顯示它。' },
    };
  }
  if (active.length > 1) {
    return {
      portfolio: null,
      resolution: {
        kind: 'none',
        reason: `${active.length} 個進行中的組合且無回放綁定，不自動選第一個 — 請在「手動模擬單」的組合選單中選擇要查看哪一個。`,
      },
    };
  }
  return {
    portfolio: null,
    resolution: {
      kind: 'none',
      reason:
        list.length === 0
          ? '尚未建立模擬投資組合 — 可在「手動模擬單」建立一個。'
          : `此帳戶 ${list.length} 個組合均非進行中；在「手動模擬單」選取其中一個即可查看其歷史紀錄（唯讀，不可送單）。`,
    },
  };
}

/**
 * Load the dock's evidence for one portfolio. Each dataset degrades on its own:
 * a failed `positions` call must not blank the orders journal.
 */
export function usePortfolioEvidence(
  session: ReplaySession | null,
  chosen?: PortfolioChoice | null,
  revision?: number
): EvidenceData {
  const [data, setData] = useState<Omit<EvidenceData, 'reload'>>(EMPTY);
  const sessionId = session?.id ?? null;
  // Depend on the fields, not the object: callers build it inline per render.
  const chosenId = chosen?.id ?? null;
  const chosenReason = chosen?.reason ?? null;
  // The replay clock is what executes a pending order, so every step onto a new bar
  // has to re-read this account: a fill, a settlement or a cancelled order changes
  // positions, orders and the journal without changing the session or the portfolio.
  // state_version covers the other half — a manual submission records a decision and
  // bumps it without moving the daily cursor, so the accepted order would otherwise
  // stay out of ⑧ until the user pressed 重新載入. These are dependencies without
  // appearing in the body below, on purpose.
  const cursorDate = session?.current_snapshot_date ?? null;
  const cursorBar = session?.current_bar_timestamp ?? null;
  const stateVersion = session?.state_version ?? null;
  // A cancel moves neither clock: no new bar, and no new decision to bump
  // state_version. The mutation site therefore names the revision it invalidates.
  const invalidationRevision = revision ?? 0;

  // Switching portfolios (or any other dependency) starts a new load while the
  // previous one may still be in flight. Only the newest load may publish: a slow
  // earlier book must not paint over the book the user is now looking at.
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = (requestSeq.current += 1);
    const superseded = () => seq !== requestSeq.current;
    const errors: EvidenceData['errors'] = {};
    setData((prev) => ({ ...prev, loading: true, errors }));
    try {
      const portfolios = asList<PortfolioRow>(await api.paperPortfolio.list());
      if (superseded()) return;
      const { portfolio, resolution } = resolvePortfolio(
        portfolios,
        sessionId,
        chosenId ? { id: chosenId, reason: chosenReason ?? '' } : null
      );
      if (!portfolio) {
        setData({ ...EMPTY, resolution, loading: false, errors });
        return;
      }

      const [detail, positions, orders, journal, decisions] = await Promise.all([
        api.paperPortfolio.get(portfolio.id).catch((e: Error) => {
          errors.portfolio = e.message;
          return null;
        }),
        api.paperPortfolio.positions(portfolio.id).then(asList<PositionRow>).catch((e: Error) => {
          errors.positions = e.message;
          return [] as PositionRow[];
        }),
        api.paperPortfolio.orders(portfolio.id).then(asList<OrderRow>).catch((e: Error) => {
          errors.orders = e.message;
          return [] as OrderRow[];
        }),
        api.paperPortfolio.journalJson(portfolio.id).catch((e: Error) => {
          errors.journal = e.message;
          return null;
        }),
        api.paperPortfolio.decisions(portfolio.id).then(asList<DecisionRow>).catch((e: Error) => {
          errors.decisions = e.message;
          return [] as DecisionRow[];
        }),
      ]);

      const book: PortfolioRow = (detail as PortfolioRow | null) ?? portfolio;
      const response = journal as JournalResponse | null;
      if (superseded()) return;

      setData({
        portfolio: book,
        resolution,
        positions,
        orders,
        journal: response?.records ? asList<JournalRecord>(response.records) : [],
        journalColumns: response?.columns ? asList<string>(response.columns) : [],
        columnSemantics: response?.column_semantics ?? {},
        decisions,
        loading: false,
        errors,
      });
    } catch (e) {
      // A failed older load must not replace the newer book's evidence either.
      if (superseded()) return;
      setData({
        ...EMPTY,
        resolution: {
          kind: 'none',
          reason: e instanceof Error ? `組合清單無法載入：${e.message}` : '組合清單無法載入。',
        },
        loading: false,
        errors: { portfolio: e instanceof Error ? e.message : '組合清單無法載入' },
      });
    }
  }, [sessionId, chosenId, chosenReason, cursorDate, cursorBar, stateVersion, invalidationRevision]);

  useEffect(() => {
    void load();
  }, [load]);

  return { ...data, reload: load };
}

/**
 * Execution-agent intents and their fills. They are a different book from the
 * paper orders (an intent is an authorization-gated request), so they load only
 * when the 訂單與成交 tab is opened, and their fills are fetched for a bounded
 * prefix because the API only exposes them one intent at a time.
 */
export function useExecutionEvidence(enabled: boolean) {
  const [intents, setIntents] = useState<import('./types').ExecutionIntent[]>([]);
  const [fills, setFills] = useState<Record<string, FillRecord[]>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const list = asList<import('./types').ExecutionIntent>(await api.execution.listIntents());
        if (cancelled) return;
        setIntents(list);
        const entries = await Promise.all(
          list.slice(0, 10).map((i) =>
            api.execution
              .listFills(i.intent_id)
              .then((rows: FillRecord[]) => [i.intent_id, asList<FillRecord>(rows)] as const)
              .catch(() => [i.intent_id, [] as FillRecord[]] as const)
          )
        );
        if (cancelled) return;
        setFills(Object.fromEntries(entries));
        setError('');
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '執行意向無法載入');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return { intents, fills, error, loading };
}

export type Scope = 'all' | 'current';

/** Client-side scope filter: it never claims to be a server query. */
export function inScope(assetId: string | null | undefined, scope: Scope, symbol: string): boolean {
  if (scope === 'all') return true;
  return !!assetId && assetId === symbol;
}

/* ── labels ─────────────────────────────────────────────────────────── */

export const ACTOR_LABELS: Record<string, string> = {
  system: '系統規則',
  human: '本人',
  ai_draft: 'AI 草稿',
  human_approved_ai_draft: '本人核准 AI 草稿',
  api_key: 'API 程式',
  engine: '策略引擎',
  user: '本人',
};

export const ORDER_STATUS_LABELS: Record<string, string> = {
  pending: '待成交',
  filled: '已成交',
  partially_filled: '部分成交',
  cancelled: '已取消',
  canceled: '已取消',
  rejected: '已拒絕',
};

export const POSITION_STATUS_LABELS: Record<string, string> = {
  open: '持有中',
  closed: '已平倉',
};

export const INTENT_STATUS_LABELS: Record<string, string> = {
  pending: '待授權',
  authorized: '已授權',
  submitted: '已送出',
  filled: '已成交',
  partially_filled: '部分成交',
  cancelled: '已取消',
  canceled: '已取消',
  rejected: '已拒絕',
  uncertain: '結果不明',
};

export const SIDE_LABELS: Record<string, string> = { BUY: '買入', SELL: '賣出' };

export const RECORD_TYPE_LABELS: Record<string, string> = {
  fill: '成交紀錄',
  order: '未成交訂單',
};

export const DECISION_TYPE_LABELS: Record<string, string> = {
  simulated_entry: '模擬進場',
  simulated_exit: '模擬出場',
  no_action: '未行動',
  blocked_action: '被阻擋',
  entry: '進場',
  exit: '出場',
};

export const PORTFOLIO_STATUS_LABELS: Record<string, string> = {
  active: '進行中',
  paused: '已暫停',
  retired: '已停用',
  completed: '已完成',
};

export const label = (map: Record<string, string>, value: string | null | undefined): string => {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  return map[value] ?? value;
};

/* ── formatters: null is never printed as a number ───────────────────── */

export function fmtMoney(value: unknown, currency: string | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return NOT_PROVIDED;
  const amount = value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${amount} ${currency}` : amount;
}

export function fmtSignedMoney(value: unknown, currency: string | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return NOT_PROVIDED;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  const amount = Math.abs(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${sign}${amount}${currency ? ` ${currency}` : ''}`;
}

export function fmtScalar(value: unknown): string {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    return entries.length === 0 ? '（空）' : entries.map(([k, v]) => `${k}=${String(v)}`).join('、');
  }
  return String(value);
}

export function fmtQuantity(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return NOT_PROVIDED;
  return value.toLocaleString(undefined, { maximumFractionDigits: 8 });
}

/**
 * A naive ISO instant is printed as the backend sent it. Re-localising it would
 * imply a market timezone the record does not carry, so only a readable split of
 * date and time is applied.
 */
export function fmtInstant(value: string | null | undefined): string {
  if (!value) return NOT_PROVIDED;
  return value.replace('T', ' ').replace(/Z$/, ' UTC');
}

/** Unrealized P&L is only ever computable when the backend priced the position. */
export function fmtUnrealized(value: unknown, currency: string | null | undefined): string {
  if (value === null || value === undefined) return CANNOT_COMPUTE;
  return fmtSignedMoney(value, currency);
}
