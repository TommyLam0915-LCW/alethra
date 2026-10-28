'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import type { ContextPortfolio } from '@/components/shell/WorkspaceContextBar';
import type { BarsResponse, ExecutionIntent, InstrumentDetail } from './types';
import type { ReplaySession } from './ReplayController';
import { NOT_PROVIDED, type RulesState } from './strategyRules';

interface PaperPortfolio {
  id: string;
  currency: string;
  current_virtual_cash: number;
  status: string;
  simulation_session_id: string | null;
}

interface Props {
  instrument: InstrumentDetail | null;
  bars: BarsResponse | null;
  instrumentId: string;
  /** The replay session the workspace loaded, or null when there is none. */
  session: ReplaySession | null;
  /** Rules resolved for that session — the only way to know what it actually trades. */
  rules: RulesState;
  onSessionChange?: (session: ReplaySession) => void;
  /** Reports the account this form would submit for, so the context strip cannot drift from it. */
  onPortfolioChange?: (portfolio: ContextPortfolio | null) => void;
  /** Signals a write that moved neither replay clock (a cancelled pending order), so ⑧ re-reads. */
  onBookChanged?: () => void;
  /** When mounted under a tab strip that already names it, drop the own header. */
  hideHeader?: boolean;
}

type ManualAction = 'buy' | 'sell' | 'hold';

interface ManualReview {
  /** Everything the submitted decision depends on; any change invalidates the review. */
  stamp: string;
  sessionId: string;
  action: ManualAction;
  quantity: number | null;
  rationale: string;
  idempotency_key: string;
  cursorLabel: string;
}

interface ManualResult {
  decision: Record<string, any>;
  fill: Record<string, any> | null;
  rejection: Record<string, any> | null;
  /** A submitted BUY/SELL is an intent: the pending order and the window it waits for. */
  order: Record<string, any> | null;
  execution: Record<string, any> | null;
  idempotency_key: string;
}

const ACTION_LABELS: Record<ManualAction, string> = {
  buy: '買入',
  sell: '賣出',
  hold: '觀望',
};

const REJECTION_LABELS: Record<string, string> = {
  already_in_position: '工作階段已有持倉',
  insufficient_cash: '虛擬現金不足',
  no_position_to_sell: '沒有可賣出的持倉',
  quantity_exceeds_position: '賣出數量超過現有持股',
  awaiting_execution_window: '等待可執行窗口',
  no_market_clock: '沒有綁定的模擬帳戶',
  no_quote_on_window: '視窗內沒有可執行報價',
  portfolio_paused: '帳戶已暫停',
  instrument_not_allowed: '標的不在風控允許清單',
  daily_entry_limit: '已達當日進場次數上限',
  position_limit: '超過單一持倉上限',
  risk_price_unavailable: '缺少決策價格，未能計算風控',
};

/** Mirrors backend OrderStatus. `pending` must never read as executed. */
const ORDER_STATUS_LABELS: Record<string, string> = {
  pending: '掛單待執行',
  filled: '已成交',
  cancelled: '已取消',
  rejected: '已拒絕',
};

const SESSION_STATUS_LABELS: Record<string, string> = {
  pending: '待開始',
  active: '進行中',
  paused: '已暫停',
  completed: '已完成',
  retired: '已停用',
};

/** Mirrors backend PortfolioStatus; non-active portfolios stay viewable but are not orderable here. */
const PORTFOLIO_STATUS_LABELS: Record<string, string> = {
  active: '進行中',
  paused: '已暫停',
  retired: '已停用',
  completed: '已完成',
};

/** Session responses carry the cursor in one of two shapes depending on rhythm. */
function sessionClock(session: ReplaySession): string {
  const intraday = !!session.timeframe && session.timeframe !== '1d';
  if (intraday && session.current_bar_timestamp) {
    return `${session.current_bar_timestamp.slice(0, 16).replace('T', ' ')}（交易所當地時間）`;
  }
  return session.current_snapshot_date ?? '尚未推進';
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** One sentence on what a submission did — an intent is not a holding. */
function manualNotice(
  decision: Record<string, any> | null,
  order: Record<string, any> | null,
  rejection: Record<string, any> | null,
  fill: Record<string, any> | null,
): string {
  if (order) {
    return (
      `掛單已提交，尚未成交（步驟 ${text(decision?.step_number)}，決策基準 ` +
      `${text(order.decided_on_cutoff)}）。它會在綁定的數據集裡第一個晚於該基準的 K 線以開盤價執行；` +
      '在此之前不計入持股，也不變動現金。'
    );
  }
  if (rejection) {
    return (
      `決策已記錄，但這張掛單被拒絕（${REJECTION_LABELS[String(rejection.reason)] ?? text(rejection.reason)}），` +
      '未變動現金或部位。'
    );
  }
  if (fill) {
    return `模擬單已成交並記錄於工作階段決策（步驟 ${text(decision?.step_number)}）。`;
  }
  return '觀望已記錄：未提交掛單，未變動現金或部位。';
}

/** Where one order stands between 提交 and 結算, from the backend's own view. */
function executionSummary(execution: Record<string, any> | null): string {
  if (!execution) return NOT_PROVIDED;
  const blockers: string[] = Array.isArray(execution.blockers)
    ? execution.blockers.map((b) => REJECTION_LABELS[String(b)] ?? String(b))
    : [];
  if (execution.next_window) {
    const window = execution.next_window;
    const paused = blockers.length ? `（目前受阻：${blockers.join('、')}）` : '';
    return `等待 ${text(window.date)} 開盤 ${text(window.open)} 執行${paused}`;
  }
  if (execution.waiting?.reason) return String(execution.waiting.reason);
  if (execution.executed) {
    const done = execution.executed;
    return (
      `已於 ${text(done.execution_date)} 成交；結算日 ` +
      `${done.settlement_date ? String(done.settlement_date) : 'UNKNOWN（綁定的數據集在成交日後沒有 bar）'}`
    );
  }
  if (execution.not_executed_reason) return `未成交（${text(execution.not_executed_reason)}）`;
  return text(execution.state);
}

export default function OrderPreviewPanel({
  instrument,
  bars,
  instrumentId,
  session,
  rules,
  onSessionChange,
  onPortfolioChange,
  onBookChanged,
  hideHeader,
}: Props) {
  const [portfolios, setPortfolios] = useState<PaperPortfolio[]>([]);
  const [portfolioId, setPortfolioId] = useState('');
  const [portfoliosLoaded, setPortfoliosLoaded] = useState(false);
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [lots, setLots] = useState('1');
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET');
  const [limitPrice, setLimitPrice] = useState('');
  const [intent, setIntent] = useState<ExecutionIntent | null>(null);
  const [intentUnknown, setIntentUnknown] = useState(false);
  const [action, setAction] = useState<ManualAction>('buy');
  const [quantityText, setQuantityText] = useState('1');
  const [rationale, setRationale] = useState('');
  const [review, setReview] = useState<ManualReview | null>(null);
  const [manualResult, setManualResult] = useState<ManualResult | null>(null);
  const [manualPending, setManualPending] = useState(false);
  const [manualUnknown, setManualUnknown] = useState<string>('');
  const [reconcileNote, setReconcileNote] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [sessionOrders, setSessionOrders] = useState<Record<string, any>[]>([]);
  const [sessionOrdersLoaded, setSessionOrdersLoaded] = useState(false);
  const [cancellingOrderId, setCancellingOrderId] = useState('');

  /** The session's own intents, so a pending order survives a reload. */
  const loadSessionOrders = useCallback(async (sessionId: string | null) => {
    if (!sessionId) {
      setSessionOrders([]);
      setSessionOrdersLoaded(false);
      return;
    }
    try {
      const data = (await api.simulations.sessionOrders(sessionId)) as Record<string, any>[];
      setSessionOrders(Array.isArray(data) ? data : []);
    } catch {
      // The list is a view, not the source of truth: say so instead of blanking it.
      setSessionOrders([]);
    } finally {
      setSessionOrdersLoaded(true);
    }
  }, []);

  /** `activeSessionId` is the session id string, stable across cursor advances. */
  const loadPortfolios = useCallback(async (activeSessionId: string | null = null) => {
    try {
      const data = (await api.paperPortfolio.list()) as PaperPortfolio[];
      const list = Array.isArray(data) ? data : [];
      setPortfolios(list);
      // A prior explicit pick wins; otherwise only an unambiguous active candidate
      // is pre-filled — never "the first row" as a stand-in for the user's account.
      const orderable = list.filter((p) => p.status === 'active');
      const boundToSession = activeSessionId
        ? orderable.filter((p) => p.simulation_session_id === activeSessionId)
        : [];
      const autoPick =
        orderable.length === 1
          ? orderable[0].id
          : boundToSession.length === 1
            ? boundToSession[0].id
            : '';
      setPortfolioId((prev) => (prev && list.some((p) => p.id === prev) ? prev : autoPick));
    } catch {
      // panel shows create button when the list cannot be read
      setPortfolios([]);
      setPortfolioId('');
    } finally {
      setPortfoliosLoaded(true);
    }
  }, []);

  const activeSessionId = session?.id ?? null;

  // Advancing the clock is what executes a pending intent, so the balances are re-read
  // with the cursor too: a fill that moved cash must not leave 「虛擬現金」 showing the
  // pre-trade figure while the book below already reads 已成交.
  useEffect(() => {
    loadPortfolios(activeSessionId);
  }, [loadPortfolios, activeSessionId, session?.current_snapshot_date, session?.current_bar_timestamp]);

  // Advancing the clock is what executes a pending intent, so the order book is
  // re-read with the cursor as well as with the session.
  useEffect(() => {
    loadSessionOrders(activeSessionId);
  }, [loadSessionOrders, activeSessionId, session?.current_snapshot_date, session?.current_bar_timestamp]);

  const selected = portfolios.find((p) => p.id === portfolioId) ?? null;

  // The strip and the form must name the same account, so the form reports its own.
  const selectionForContext = useMemo<ContextPortfolio | null>(
    () =>
      selected
        ? {
            id: selected.id,
            currency: selected.currency,
            current_virtual_cash: selected.current_virtual_cash,
            status: selected.status,
            simulation_session_id: selected.simulation_session_id ?? null,
          }
        : null,
    [selected],
  );

  useEffect(() => {
    onPortfolioChange?.(selectionForContext);
  }, [onPortfolioChange, selectionForContext]);

  // Which endpoint a simulated order can honestly go to, given what the API supports.
  const target = useMemo<{ kind: 'intent' | 'manual' | 'blocked'; reason: string }>(() => {
    if (!portfoliosLoaded) {
      return { kind: 'blocked', reason: '尚未讀取模擬投資組合，無法決定提交目標。' };
    }
    if (!selected) {
      if (portfolios.length === 0) {
        return { kind: 'blocked', reason: '尚未建立模擬投資組合，請先建立。' };
      }
      return portfolios.some((p) => p.status === 'active')
        ? { kind: 'blocked', reason: '有多個進行中的模擬組合且回放綁定不明確，不自動代選 — 請於上方選擇。' }
        : {
            kind: 'blocked',
            reason: `現有 ${portfolios.length} 個組合均非進行中（${portfolios
              .map((p) => PORTFOLIO_STATUS_LABELS[p.status] ?? p.status)
              .reduce<string[]>((acc, label) => (acc.includes(label) ? acc : [...acc, label]), [])
              .join('、')}），可查看歷史但需選擇或建立進行中的組合才能提交。`,
          };
    }
    if (selected.status !== 'active') {
      return {
        kind: 'blocked',
        reason: `此組合狀態為「${PORTFOLIO_STATUS_LABELS[selected.status] ?? selected.status}」：後端模擬單端點對 retired/completed 回傳 400，paused 由風險檢查拒絕，因此本面板只對進行中的組合提交。組合歷史仍可檢視。`,
      };
    }
    if (!selected.simulation_session_id) {
      return { kind: 'intent', reason: '此組合未綁定歷史回放工作階段，走受控執行器意向流程。' };
    }
    if (!session || session.id !== selected.simulation_session_id) {
      return {
        kind: 'blocked',
        reason: `此組合綁定工作階段 ${selected.simulation_session_id.slice(0, 8)}，但其回放游標與策略版本未在右欄載入，無法確認成交基準，因此停用提交。`,
      };
    }
    if (rules.status !== 'resolved' || rules.versionId !== session.experiment_version_id) {
      return {
        kind: 'blocked',
        reason: `工作階段綁定的策略規則${NOT_PROVIDED}（版本 ${session.experiment_version_id.slice(0, 8)}${
          rules.status === 'unresolved' ? `：${rules.reason}` : rules.status === 'error' ? `：${rules.message}` : ''
        }），無法確認此表單提交的標的是否等於目前標的，因此停用提交。`,
      };
    }
    const assetId = rules.assetIds[0] ?? null;
    if (!assetId) {
      return {
        kind: 'blocked',
        reason: `規則的 strategy_spec.asset_ids ${NOT_PROVIDED}，無法確認成交標的，因此停用提交。`,
      };
    }
    if (assetId !== instrumentId) {
      return {
        kind: 'blocked',
        reason: `目前標的為 ${instrumentId}，但此工作階段的規則綁定 ${assetId}。manual-decision 只對 asset_ids[0] 成交，跨標的提交會誤導研究紀錄，因此停用。`,
      };
    }
    if (session.status !== 'pending' && session.status !== 'active') {
      return {
        kind: 'blocked',
        reason: `此工作階段已${SESSION_STATUS_LABELS[session.status] ?? session.status}，後端不再接受決策。`,
      };
    }
    return { kind: 'manual', reason: '以回放游標為成交基準，提交給綁定的工作階段。' };
  }, [portfoliosLoaded, portfolios, selected, session, rules, instrumentId]);

  const isManual = target.kind === 'manual';
  // Reading the lifecycle record is not the same decision as being allowed to write to it:
  // the book stays available for the account's bound session even once 提交 is 停用.
  const bookSession =
    selected && session && session.id === selected.simulation_session_id ? session : null;
  const manualQuantity = Math.floor(parseFloat(quantityText) || 0);
  // Every input the submitted decision depends on, flattened to one comparable value.
  // The server cursor is part of it, so an advance elsewhere invalidates the review.
  const signature = JSON.stringify([
    portfolioId,
    instrumentId,
    action,
    session?.id ?? null,
    session?.current_snapshot_date ?? null,
    session?.current_bar_timestamp ?? null,
    session?.status ?? null,
    action === 'buy' ? manualQuantity : null,
    rationale.trim(),
  ]);
  const reviewStale = !!review && review.stamp !== signature;

  useEffect(() => {
    setIntent(null);
    setIntentUnknown(false);
    setError('');
    setNotice('');
    setReview(null);
    setManualResult(null);
    setManualUnknown('');
    setReconcileNote('');
  }, [instrumentId]);

  const lotSize = instrument?.lot_size;
  const quantity =
    lotSize !== null && lotSize !== undefined
      ? Math.max(0, Math.floor((parseFloat(lots) || 0) * lotSize))
      : 0;
  const lastClose = bars && bars.bars.length > 0 ? bars.bars[bars.bars.length - 1].close : null;
  const estimate = lastClose !== null ? quantity * lastClose : null;
  const referenceValue =
    lastClose !== null
      ? isManual
        ? manualQuantity * lastClose
        : estimate
      : null;

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

  const buildReview = () => {
    if (!session) return;
    setError('');
    setReconcileNote('');
    if (action === 'buy') {
      if (!Number.isFinite(manualQuantity) || manualQuantity < 1) {
        setError('買入數量必須為不小於 1 的整數股。');
        return;
      }
    }
    if (rationale.length > 1000) {
      setError(`決策理由長度 ${rationale.length} 超過後端上限 1000 字元。`);
      return;
    }
    setManualUnknown('');
    setReview({
      stamp: signature,
      sessionId: session.id,
      action,
      quantity: action === 'buy' ? manualQuantity : null,
      rationale: rationale.trim(),
      // Generated once per review: a retry of the same review reuses this key, so a
      // double click or a re-send after an unknown outcome cannot create two decisions.
      idempotency_key: `ws-${session.id.slice(0, 8)}-${Date.now()}`,
      cursorLabel: sessionClock(session),
    });
  };

  const submitManual = async () => {
    if (!review || reviewStale) return;
    setManualPending(true);
    setError('');
    setNotice('');
    setManualUnknown('');
    try {
      const payload: Parameters<typeof api.simulations.manualDecision>[1] = {
        action: review.action,
        idempotency_key: review.idempotency_key,
        rationale: review.rationale || undefined,
      };
      if (review.quantity !== null) payload.quantity = review.quantity;
      if (typeof session?.state_version === 'number') payload.expected_version = session.state_version;
      const res = (await api.simulations.manualDecision(review.sessionId, payload)) as {
        session: ReplaySession;
        decision: Record<string, any>;
        fill: Record<string, any> | null;
        rejection: Record<string, any> | null;
        order: Record<string, any> | null;
        execution: Record<string, any> | null;
      };
      setManualResult({
        decision: res.decision,
        fill: res.fill ?? null,
        rejection: res.rejection ?? null,
        order: res.order ?? null,
        execution: res.execution ?? null,
        idempotency_key: review.idempotency_key,
      });
      setNotice(
        manualNotice(res.decision, res.order ?? null, res.rejection ?? null, res.fill ?? null)
      );
      if (res.session?.id === session?.id) onSessionChange?.(res.session);
      loadSessionOrders(review.sessionId);
      setReview(null);
      setRationale('');
    } catch (err) {
      const message = err instanceof Error ? err.message : '提交失敗';
      if (err instanceof ApiError && err.status === 409) {
        setReview(null);
        if (message.includes('State version conflict')) {
          setError('狀態版本衝突：工作階段已被其他操作變更，請依最新游標重新檢視後再提交。');
        } else if (message.includes('Idempotency key conflict') || message.includes('idempotency_key')) {
          setError(
            '冪等鍵衝突：同一把 key 已在這個帳戶留下一張掛單，系統不會提交第二張。' +
            '已重新讀取掛單列表，請以它為準。'
          );
          loadSessionOrders(review.sessionId);
        } else {
          setError(`衝突（409）：${message}`);
        }
      } else if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setError(`後端拒絕提交（${err.status}）：${message}`);
      } else {
        setManualUnknown(
          `提交結果未知（${message}）。決策可能已在伺服器成立，請先核對；` +
          '必要時以同一冪等鍵重試 — 同一把 key 不會產生第二張掛單，成交仍由市場時間決定一次。'
        );
      }
    } finally {
      setManualPending(false);
    }
  };

  const reconcileManual = async () => {
    if (!review) return;
    setBusy(true);
    setError('');
    try {
      const [rows, fresh] = await Promise.all([
        api.simulations.decisions(review.sessionId) as Promise<any[]>,
        api.simulations.get(review.sessionId) as Promise<ReplaySession>,
      ]);
      if (fresh && fresh.id === session?.id) onSessionChange?.(fresh);
      const hit = Array.isArray(rows)
        ? rows.find((d) => d?.decision_detail?.idempotency_key === review.idempotency_key)
        : null;
      setReconcileNote(
        hit
          ? `核對結果：決策已成立（id ${String(hit.id).slice(0, 8)}、步驟 ${text(hit.step_number)}、類型 ${text(hit.decision_type)}）。請不要重試。`
          : `核對結果：查無此冪等鍵的決策（已比對 ${Array.isArray(rows) ? rows.length : 0} 筆），尚未成立，可以安全重試。`
      );
      if (hit) {
        const detail = hit.decision_detail ?? {};
        setManualResult({
          decision: hit,
          fill: detail.fill ?? null,
          rejection: detail.rejection ?? null,
          order: detail.order ?? null,
          execution: detail.execution ?? null,
          idempotency_key: review.idempotency_key,
        });
        setNotice(manualNotice(hit, detail.order ?? null, detail.rejection ?? null, detail.fill ?? null));
        setReview(null);
        setManualUnknown('');
        loadSessionOrders(review.sessionId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '核對失敗');
    } finally {
      setBusy(false);
    }
  };

  const createIntent = async () => {
    if (!portfolioId) {
      setError('沒有可用的模擬投資組合，請先建立。');
      return;
    }
    if (lotSize === null || lotSize === undefined) {
      setError('此商品每手數量未知，無法下單。請先確認商品規格。');
      return;
    }
    if (instrument?.currency && selected && portfolioId === selected.id && selected.currency !== instrument.currency) {
      setError(`商品幣別（${instrument.currency}）與投資組合幣別（${selected.currency}）不符，不支援自動換匯。`);
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
    try {
      setBusy(true);
      setError('');
      setIntentUnknown(false);
      const result = (await api.execution.submit(intent.intent_id)) as ExecutionIntent;
      setIntent(result);
      setNotice('已提交受控執行器。成交會顯示在「成交」分頁。');
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message || '提交失敗');
      } else {
        setError(err instanceof Error ? err.message : '提交失敗');
        setIntentUnknown(true);
      }
    } finally {
      setBusy(false);
    }
  };

  const reconcileIntent = async () => {
    if (!intent) return;
    const result = (await call(() => api.execution.getIntent(intent.intent_id))) as ExecutionIntent | null;
    if (!result) return;
    setIntent(result);
    setIntentUnknown(false);
    if (result.status === 'authorized') {
      setNotice(`核對結果：意向仍是「已授權、未提交」（${result.status}），先前那次提交沒有送出去。`);
    } else {
      setNotice(`核對結果：伺服器意向狀態為 ${result.status}。`);
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

  /**
   * Withdraw a still-pending intent. The account comes from the order row the
   * server returned, not from whatever portfolio this form currently shows: a
   * cancel must hit the book the order actually lives on.
   */
  const cancelSessionOrder = async (row: Record<string, any>) => {
    const order = row?.order ?? {};
    if (!order.id || !order.portfolio_id) {
      setError('此掛單缺少所屬組合識別，無法在本面板取消；請至該模擬組合的掛單列表操作。');
      return;
    }
    setCancellingOrderId(String(order.id));
    setError('');
    try {
      const res = (await api.paperPortfolio.cancelOrder(order.portfolio_id, order.id)) as {
        order: Record<string, any>;
        execution: Record<string, any> | null;
      };
      const status = res?.order?.status ?? order.status;
      setNotice(
        `掛單 ${String(order.id).slice(0, 8)}… 現為「${
          ORDER_STATUS_LABELS[String(status)] ?? text(status)
        }」：取消發生在市場執行之前，因此沒有成交，也不影響現金或持股。`
      );
      // Only a resolved request gets here: a rejected or failed cancel signals
      // nothing, so ⑧ can never be told to show a cancelled state that did not happen.
      onBookChanged?.();
      await Promise.all([loadSessionOrders(activeSessionId), loadPortfolios(activeSessionId)]);
    } catch (err) {
      const message = err instanceof Error ? err.message : '取消失敗';
      setError(
        err instanceof ApiError && err.status === 400
          ? `後端拒絕取消（400）：${message}。只有 pending（尚未被市場執行）的掛單可以取消；已成交的單要對沖，不是抹除。`
          : `取消失敗：${message}`
      );
    } finally {
      setCancellingOrderId('');
    }
  };

  const createPortfolio = async () => {
    const result = await call(() => api.paperPortfolio.create({ initial_virtual_cash: 100000 }));
    if (result) {
      await loadPortfolios(activeSessionId);
      setNotice('已建立模擬投資組合（100,000 USD 虛擬資金）。');
    }
  };

  const intentStatus = intent?.status;
  const fill = manualResult?.fill ?? null;
  const rejection = manualResult?.rejection ?? null;

  return (
    <div className="ws-panel ws-panel-fill" data-testid="order-preview-panel">
      {!hideHeader && <div className="ws-panel-header">手動模擬單</div>}
      <div className="ws-panel-body" style={{ padding: 10 }}>
        {error && (
          <div className="ws-message-error" role="alert" data-testid="order-error">
            {error}
          </div>
        )}
        {notice && <div className="ws-message-ok">{notice}</div>}

        <div className="ws-form-row">
          <div className="ws-form-field">
            <label htmlFor="order-portfolio-select">組合</label>
            {portfolios.length > 0 ? (
              <>
                <select
                  id="order-portfolio-select"
                  value={portfolioId}
                  onChange={(e) => {
                    setPortfolioId(e.target.value);
                    setReview(null);
                    setManualResult(null);
                    setManualUnknown('');
                    setReconcileNote('');
                    setIntent(null);
                  }}
                >
                  <option value="" disabled>
                    — 請選擇模擬組合 —
                  </option>
                  {portfolios.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.currency} ·{' '}
                      {typeof p.current_virtual_cash === 'number'
                        ? p.current_virtual_cash.toLocaleString()
                        : NOT_PROVIDED}
                      {p.status !== 'active'
                        ? ` · ${PORTFOLIO_STATUS_LABELS[p.status] ?? p.status}`
                        : ''}
                      {p.simulation_session_id ? ' · 回放' : ''}
                    </option>
                  ))}
                </select>
                {!portfolios.some((p) => p.status === 'active') && (
                  <button
                    className="ws-btn ws-btn-neutral"
                    onClick={createPortfolio}
                    disabled={busy}
                    data-testid="order-create-portfolio"
                  >
                    建立模擬組合
                  </button>
                )}
              </>
            ) : (
              <button className="ws-btn ws-btn-neutral" onClick={createPortfolio} disabled={busy}>
                建立模擬組合
              </button>
            )}
          </div>
        </div>

        {selected ? (
          <div className="order-account-context" data-testid="order-account-context">
            <span className="order-account-item">
              <span className="order-account-label">組合</span>
              <span className="order-account-value" title={selected.id}>
                {selected.id.slice(0, 8)}
              </span>
            </span>
            <span className="order-account-item">
              <span className="order-account-label">貨幣</span>
              <span className="order-account-value">{text(selected.currency)}</span>
            </span>
            <span className="order-account-item">
              <span className="order-account-label">狀態</span>
              <span className="order-account-value" title={selected.status}>
                {PORTFOLIO_STATUS_LABELS[selected.status] ?? selected.status}
              </span>
            </span>
            <span className="order-account-item">
              <span className="order-account-label">虛擬現金</span>
              <span className="order-account-value">
                {typeof selected.current_virtual_cash === 'number'
                  ? `${selected.currency} ${selected.current_virtual_cash.toLocaleString(
                      undefined,
                      { minimumFractionDigits: 2, maximumFractionDigits: 2 },
                    )}`
                  : NOT_PROVIDED}
              </span>
            </span>
            <span className="order-account-item">
              <span className="order-account-label">購買力</span>
              <span className="order-account-value" data-testid="order-buying-power">
                {NOT_PROVIDED}
              </span>
            </span>
            <p className="order-account-note">
              購買力不由虛擬現金推算：伺服器未回傳購買力欄位，因此如實顯示未提供。本系統只進行紙上模擬，不連接實盤經紀。
            </p>
          </div>
        ) : (
          <p className="order-account-note" data-testid="order-account-context">
            未選擇模擬組合 — 送出模擬單前需先建立或選擇組合。
          </p>
        )}

        <p className="ws-muted ws-small" data-testid="order-target">
          提交目標：
          {target.kind === 'manual'
            ? `歷史回放工作階段 ${session?.id.slice(0, 8)}（游標 ${session ? sessionClock(session) : NOT_PROVIDED}）`
            : target.kind === 'intent'
              ? '受控執行器意向（模擬組合，未綁定回放游標）'
              : '停用'}
          {target.kind !== 'intent' ? ` — ${target.reason}` : ''}
        </p>

        {target.kind === 'blocked' && (
          <div className="ws-message-error" role="alert" data-testid="order-blocked">
            無法提交模擬單：{target.reason}
          </div>
        )}

        {isManual ? (
          <>
            <div className="ws-form-row">
              <div className="ws-form-field">
                <label htmlFor="order-action">動作</label>
                <select
                  id="order-action"
                  value={action}
                  onChange={(e) => {
                    setAction(e.target.value as ManualAction);
                  }}
                  data-testid="order-action"
                >
                  <option value="buy">買入</option>
                  <option value="sell">賣出</option>
                  <option value="hold">觀望（不成交）</option>
                </select>
              </div>
              {action === 'buy' && (
                <div className="ws-form-field">
                  <label htmlFor="order-quantity-input">數量（股）</label>
                  <input
                    id="order-quantity-input"
                    type="number"
                    min="1"
                    step="1"
                    value={quantityText}
                    onChange={(e) => setQuantityText(e.target.value)}
                    data-testid="order-quantity-input"
                  />
                </div>
              )}
            </div>

            <div className="ws-form-field" style={{ marginBottom: 8 }}>
              <label htmlFor="order-rationale">決策理由（上限 1000 字元）</label>
              <textarea
                id="order-rationale"
                value={rationale}
                onChange={(e) => setRationale(e.target.value.slice(0, 1200))}
                rows={2}
                data-testid="order-rationale"
              />
            </div>

            <p className="ws-muted ws-small" data-testid="order-manual-semantics">
              類型：市價（後端 manual-decision 只接受 buy/sell/hold，沒有限價、沒有部分成交）。賣出以工作階段全部持股成交；觀望不成交。
              成交基準：游標後的下一根可執行 K 線開盤價（next_open_no_lookahead），實際價格由伺服器回覆，不在提交前顯示。
            </p>
          </>
        ) : (
          target.kind === 'intent' && (
            <>
              <div className="ws-form-row">
                <div className="ws-form-field">
                  <label htmlFor="order-side">方向</label>
                  <select id="order-side" value={side} onChange={(e) => setSide(e.target.value as 'BUY' | 'SELL')}>
                    <option value="BUY">買入</option>
                    <option value="SELL">賣出</option>
                  </select>
                </div>
                <div className="ws-form-field">
                  <label htmlFor="order-lots">手數（每手 {lotSize === null || lotSize === undefined ? NOT_PROVIDED : lotSize} 股）</label>
                  <input
                    id="order-lots"
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
                  <label htmlFor="order-type">類型</label>
                  <select id="order-type" value={orderType} onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}>
                    <option value="MARKET">市價</option>
                    <option value="LIMIT">限價</option>
                  </select>
                </div>
                {orderType === 'LIMIT' && (
                  <div className="ws-form-field">
                    <label htmlFor="order-limit-price">限價（{instrument?.currency || ''}）</label>
                    <input
                      id="order-limit-price"
                      type="number"
                      min="0"
                      step="0.01"
                      value={limitPrice}
                      onChange={(e) => setLimitPrice(e.target.value)}
                    />
                  </div>
                )}
              </div>
            </>
          )
        )}

        <table className="ws-table" style={{ marginBottom: 10 }}>
          <tbody>
            <tr>
              <td className="ws-muted">數量</td>
              <td data-testid="order-quantity">
                {isManual
                  ? action === 'buy'
                    ? `${manualQuantity} 股`
                    : action === 'sell'
                      ? '全部持股'
                      : '不成交'
                  : `${quantity} 股`}
              </td>
            </tr>
            <tr>
              <td className="ws-muted">目前標的</td>
              <td data-testid="order-symbol">{instrumentId}</td>
            </tr>
            <tr>
              <td className="ws-muted">參考價（游標前最後收盤）</td>
              <td data-testid="order-reference">
                {lastClose !== null ? `${lastClose.toFixed(2)} ${instrument?.currency || ''}` : NOT_PROVIDED}
              </td>
            </tr>
            <tr>
              <td className="ws-muted">預估金額</td>
              <td data-testid="order-estimate">
                {referenceValue !== null
                  ? `${referenceValue.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${instrument?.currency || ''}`
                  : NOT_PROVIDED}
              </td>
            </tr>
          </tbody>
        </table>

        {isManual && (!review || reviewStale) && (
          <button
            className="ws-btn ws-btn-accent"
            onClick={buildReview}
            disabled={busy || target.kind !== 'manual'}
            data-testid="order-build-review"
          >
            {reviewStale ? '重新檢視模擬單' : '檢視模擬單'}
          </button>
        )}

        {isManual && review && (
          <div data-testid="order-review">
            <div className="ws-panel-header" style={{ marginTop: 6 }}>
              檢視模擬單{reviewStale ? '（已作廢）' : ''}
            </div>
            <table className="ws-table" style={{ marginBottom: 8 }}>
              <tbody>
                <tr>
                  <td className="ws-muted">標的</td>
                  <td>{instrumentId}</td>
                </tr>
                <tr>
                  <td className="ws-muted">動作</td>
                  <td>{ACTION_LABELS[review.action]}</td>
                </tr>
                <tr>
                  <td className="ws-muted">數量</td>
                  <td>{review.quantity !== null ? `${review.quantity} 股` : review.action === 'sell' ? '全部持股' : '不成交'}</td>
                </tr>
                <tr>
                  <td className="ws-muted">理由</td>
                  <td>{review.rationale || NOT_PROVIDED}</td>
                </tr>
                <tr>
                  <td className="ws-muted">回放游標</td>
                  <td data-testid="order-review-cursor">{review.cursorLabel}</td>
                </tr>
                <tr>
                  <td className="ws-muted">冪等鍵</td>
                  <td data-testid="order-review-key">{review.idempotency_key.slice(0, 18)}…</td>
                </tr>
              </tbody>
            </table>

            {reviewStale && (
              <div className="ws-message-error" role="alert" data-testid="order-review-stale">
                欄位、標的、組合或回放游標已變更：先前的檢視已作廢，請重新「檢視模擬單」。
              </div>
            )}
            {manualUnknown && (
              <div className="ws-message-error" role="alert" data-testid="order-unknown">
                {manualUnknown}
              </div>
            )}

            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button
                className="ws-btn ws-btn-buy"
                onClick={submitManual}
                disabled={busy || manualPending || reviewStale}
                data-testid="order-submit-manual"
              >
                {manualPending ? '提交中…' : '提交模擬單'}
              </button>
              {manualUnknown && (
                <button
                  className="ws-btn ws-btn-neutral"
                  onClick={reconcileManual}
                  disabled={busy}
                  data-testid="order-reconcile-manual"
                >
                  核對結果
                </button>
              )}
              <button
                className="ws-btn ws-btn-neutral"
                onClick={() => {
                  setReview(null);
                  setManualUnknown('');
                  setReconcileNote('');
                }}
                disabled={busy}
                data-testid="order-cancel-review"
              >
                取消檢視
              </button>
            </div>
          </div>
        )}

        {/* Panel level on purpose: a successful reconciliation clears the review, and
            a verdict that says "the decision stands, do not retry" must not disappear
            with the block it refers to. */}
        {isManual && reconcileNote && (
          <p className="ws-muted ws-small" data-testid="order-reconcile">
            {reconcileNote}
          </p>
        )}

        {manualResult && (
          <table className="ws-table" style={{ marginTop: 8 }} data-testid="order-manual-result">
            <tbody>
              <tr>
                <td className="ws-muted">決策</td>
                <td>
                  {text(manualResult.decision?.id).slice(0, 13)}… · 步驟 {text(manualResult.decision?.step_number)} ·{' '}
                  {text(manualResult.decision?.decision_type)}
                </td>
              </tr>
              <tr>
                <td className="ws-muted">快照日</td>
                <td>{text(manualResult.decision?.snapshot_date)}</td>
              </tr>
              <tr>
                <td className="ws-muted">決策後現金／權益</td>
                <td>
                  {text(manualResult.decision?.paper_cash)} / {text(manualResult.decision?.paper_equity)}
                </td>
              </tr>
              {manualResult.order &&
                (() => {
                  const submitted = manualResult.order;
                  // The book is re-read after every advance, so it reports where the
                  // order stands NOW; the submit response only knew the moment it was
                  // accepted, and a receipt must not be mistaken for current state.
                  const live =
                    sessionOrders.find((row) => row?.order?.id === submitted.order_id) ?? null;
                  const view = (live?.execution ?? manualResult.execution) as Record<string, any> | null;
                  const state = live?.order?.status ?? submitted.status;
                  return (
                    <>
                      <tr>
                        <td className="ws-muted">掛單</td>
                        <td data-testid="order-manual-order">
                          {text(submitted.order_id).slice(0, 13)}… ·{' '}
                          <span className="ws-status-badge">
                            {ORDER_STATUS_LABELS[String(state)] ?? text(state)}
                          </span>{' '}
                          · {text(submitted.side)} {text(submitted.quantity)} 股 ·{' '}
                          {text(submitted.asset_id)} ·{' '}
                          {submitted.actor_type === 'human' ? '人工決策' : text(submitted.actor_type)}
                        </td>
                      </tr>
                      <tr>
                        <td className="ws-muted">決策基準／下單時間（UTC）</td>
                        <td data-testid="order-manual-decided">
                          {text(submitted.decided_on_cutoff)} / {text(submitted.order_created_at_utc)}
                        </td>
                      </tr>
                      <tr>
                        <td className="ws-muted">已成交／請求數量</td>
                        <td data-testid="order-manual-quantities">
                          {text(view?.filled_quantity ?? 0)} / {text(view?.requested_quantity ?? submitted.quantity)}
                          {view?.fill_count !== undefined ? ` · 成交筆數 ${text(view.fill_count)}` : ''}
                        </td>
                      </tr>
                      <tr>
                        <td className="ws-muted">生命週期位置</td>
                        <td data-testid="order-manual-execution">{executionSummary(view)}</td>
                      </tr>
                    </>
                  );
                })()}
              {fill && (
                <>
                  <tr>
                    <td className="ws-muted">成交</td>
                    <td>
                      {text(fill.action)} {text(fill.quantity)} @ {text(fill.price)}（{text(fill.execution_rule)}）
                    </td>
                  </tr>
                  <tr>
                    <td className="ws-muted">成交日</td>
                    <td>{text(fill.execution_date)}</td>
                  </tr>
                  <tr>
                    <td className="ws-muted">觀察日／觀察收盤</td>
                    <td>
                      {text(fill.observed_date)} / {text(fill.observed_close)}
                    </td>
                  </tr>
                  <tr>
                    <td className="ws-muted">手續費／滑點</td>
                    <td>
                      {text(fill.commission)} / {text(fill.slippage)}
                    </td>
                  </tr>
                  <tr>
                    <td className="ws-muted">成本／淨額／毛損益</td>
                    <td>
                      {text(fill.total_cost)} / {text(fill.net_proceeds)} / {text(fill.gross_pnl)}
                    </td>
                  </tr>
                </>
              )}
              {rejection && (
                <tr>
                  <td className="ws-muted">拒絕</td>
                  <td data-testid="order-manual-rejection">
                    {REJECTION_LABELS[String(rejection.reason)] ?? text(rejection.reason)}
                    {rejection.required !== undefined ? `（需要 ${text(rejection.required)}）` : ''}
                    {rejection.available !== undefined ? `（可用 ${text(rejection.available)}）` : ''}
                    {rejection.current_shares !== undefined ? `（現有 ${text(rejection.current_shares)} 股）` : ''}
                    {rejection.intent_preserved ? '；意圖已保留' : ''}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}

        {bookSession && (
          <section style={{ marginTop: 10 }} data-testid="order-session-book">
            <div className="ws-panel-header">本節掛單簿（提交 → 待執行 → 成交／取消）</div>
            <p className="ws-muted ws-small">
              清單由工作階段綁定的模擬帳戶讀取，重新整理後仍在。pending 是尚未成交的意圖：不計入持股、不動現金；
              成交由市場時間決定 — 推進重放走到下一根可執行 K 線時，才以該 bar 的開盤價執行一次。
            </p>
            {!isManual && (
              <p className="ws-muted ws-small" data-testid="order-session-book-readonly">
                此工作階段已不再接受新決策（{target.reason}）；掛單簿改用只讀模式 —
                已經留下的提交、待執行、成交、取消或過期紀錄仍然逐筆列在外面，不會因為結束而消失。
              </p>
            )}
            {!sessionOrdersLoaded ? (
              <p className="ws-muted ws-small" data-testid="order-session-book-loading">
                掛單簿讀取中…
              </p>
            ) : sessionOrders.length === 0 ? (
              <p className="ws-muted ws-small" data-testid="order-session-book-empty">
                本節尚無掛單。
              </p>
            ) : (
              <>
                <table className="ws-table" data-testid="order-session-book-rows">
                  <thead>
                    <tr>
                      <th className="ws-muted">掛單</th>
                      <th className="ws-muted">狀態</th>
                      <th className="ws-muted">決策基準／下單時間（UTC）</th>
                      <th className="ws-muted">已成交／請求</th>
                      <th className="ws-muted">生命週期位置</th>
                      <th className="ws-muted">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessionOrders.map((row) => {
                      const order = row?.order ?? {};
                      const execution = (row?.execution ?? null) as Record<string, any> | null;
                      const shortId = String(order.id ?? '').slice(0, 8);
                      return (
                        <tr key={String(order.id)} data-testid={`order-book-row-${shortId}`}>
                          <td title={text(order.id)}>
                            {text(order.side)} {text(order.quantity)} 股 · {text(order.asset_id)} ·{' '}
                            {order.actor_type === 'system'
                              ? '規則'
                              : order.actor_type === 'human'
                                ? '人工'
                                : text(order.actor_type)}
                          </td>
                          <td>
                            <span className="ws-status-badge">
                              {ORDER_STATUS_LABELS[String(order.status)] ?? text(order.status)}
                            </span>
                          </td>
                          <td>
                            {text(execution?.decided_on_cutoff ?? order.data_cutoff_at)} /{' '}
                            {text(execution?.order_created_at_utc ?? order.created_at)}
                          </td>
                          <td>
                            {text(execution?.filled_quantity ?? 0)} /{' '}
                            {text(execution?.requested_quantity ?? order.quantity)}
                          </td>
                          <td className="ws-small">{executionSummary(execution)}</td>
                          <td>
                            {order.status === 'pending' && (
                              <button
                                className="ws-btn ws-btn-neutral"
                                onClick={() => cancelSessionOrder(row)}
                                disabled={cancellingOrderId !== ''}
                                data-testid={`order-book-cancel-${shortId}`}
                              >
                                {cancellingOrderId === String(order.id) ? '取消中…' : '取消掛單'}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {selected && String(sessionOrders[0]?.order?.portfolio_id ?? '') !== selected.id && (
                  <div className="ws-message-error" role="alert" data-testid="order-book-account-mismatch">
                    掛單簿所屬帳戶（{text(sessionOrders[0]?.order?.portfolio_id).slice(0, 8)}…）與本表單目前選擇的帳戶（
                    {selected.id.slice(0, 8)}…）不同：取消與成交一律以掛單簿帳戶為準。
                  </div>
                )}
              </>
            )}
          </section>
        )}

        {!isManual && target.kind === 'intent' && (
          <>
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
                {intentUnknown && (
                  <div className="ws-message-error" role="alert" data-testid="order-intent-unknown">
                    提交結果未知：意向可能已送達執行器。請先「核對意向狀態」，不要直接重複提交。
                  </div>
                )}
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {intentStatus === 'pending' && (
                    <button className="ws-btn ws-btn-neutral" onClick={authorizeIntent} disabled={busy}>
                      確認授權
                    </button>
                  )}
                  {intentStatus === 'authorized' && (
                    <button className="ws-btn ws-btn-buy" onClick={submitIntent} disabled={busy} data-testid="order-submit">
                      提交模擬單（執行器）
                    </button>
                  )}
                  {intentUnknown && (
                    <button className="ws-btn ws-btn-neutral" onClick={reconcileIntent} disabled={busy} data-testid="order-reconcile-intent">
                      核對意向狀態
                    </button>
                  )}
                  {['pending', 'authorized', 'submitted'].includes(intentStatus || '') && (
                    <button className="ws-btn ws-btn-neutral" onClick={cancelIntent} disabled={busy}>
                      取消
                    </button>
                  )}
                  <Link
                    href="/execution"
                    className="ws-btn ws-btn-neutral"
                    style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
                  >
                    執行總覽
                  </Link>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
