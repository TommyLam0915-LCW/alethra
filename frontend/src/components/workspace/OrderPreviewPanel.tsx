'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import {
  ACTION_LABELS,
  label,
  NOT_PROVIDED,
  ORDER_STATUS_LABELS,
  PORTFOLIO_STATUS_LABELS,
  REJECTION_LABELS,
  SESSION_STATUS_LABELS,
} from '@/lib/copy';
import type { ContextPortfolio } from '@/components/shell/WorkspaceContextBar';
import type { BarsResponse, ExecutionIntent, InstrumentDetail } from './types';
import type { ReplaySession } from './ReplayController';
import type { RulesState } from './strategyRules';

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

/** Session responses carry the cursor in one of two shapes depending on rhythm. */
function sessionClock(session: ReplaySession): string {
  const intraday = !!session.timeframe && session.timeframe !== '1d';
  if (intraday && session.current_bar_timestamp) {
    return `${session.current_bar_timestamp.slice(0, 16).replace('T', ' ')} (exchange local time)`;
  }
  return session.current_snapshot_date ?? 'Not advanced yet';
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
      `Pending order submitted, not yet filled (step ${text(decision?.step_number)}, decision basis ` +
      `${text(order.decided_on_cutoff)}). It executes at the open of the first bar in the bound dataset ` +
      'after that basis; until then it is not counted as a holding and does not move cash.'
    );
  }
  if (rejection) {
    return (
      `The decision was recorded, but this order was rejected (${label(REJECTION_LABELS, String(rejection.reason))}); ` +
      'cash and positions were not changed.'
    );
  }
  if (fill) {
    return `Paper order filled and recorded in the session decisions (step ${text(decision?.step_number)}).`;
  }
  return 'Hold recorded: no order submitted, no cash or position change.';
}

/** Where one order stands between submission and settlement, from the backend's own view. */
function executionSummary(execution: Record<string, any> | null): string {
  if (!execution) return NOT_PROVIDED;
  const blockers: string[] = Array.isArray(execution.blockers)
    ? execution.blockers.map((b) => label(REJECTION_LABELS, String(b)))
    : [];
  if (execution.next_window) {
    const window = execution.next_window;
    const paused = blockers.length ? ` (currently blocked: ${blockers.join(', ')})` : '';
    return `Waiting for the ${text(window.date)} open at ${text(window.open)} to execute${paused}`;
  }
  if (execution.waiting?.reason) {
    // The server's prose for this state is not translated, but it also sends the stable
    // blocker code that produced it. Rendering the code keeps the lifecycle line English
    // without rewriting server messages, and leaves the reason traceable.
    const blocked = blockers.length ? ` (${blockers.join(', ')})` : '';
    return (
      `Waiting for an executable bar${blocked}: the bound dataset has no bar after the decision ` +
      'basis, so this order cannot fill and is settled as expired when the session is reviewed, ' +
      'not silently dropped.'
    );
  }
  if (execution.executed) {
    const done = execution.executed;
    return (
      `Filled on ${text(done.execution_date)}; settlement date ` +
      `${done.settlement_date ? String(done.settlement_date) : 'UNKNOWN (the bound dataset has no bar after the fill date)'}`
    );
  }
  if (execution.not_executed_reason) return `Not filled (${text(execution.not_executed_reason)})`;
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
  // with the cursor too: a fill that moved cash must not leave the virtual cash showing
  // the pre-trade figure while the book below already reads filled.
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
      return { kind: 'blocked', reason: 'Paper portfolios have not loaded, so the submission target cannot be decided.' };
    }
    if (!selected) {
      if (portfolios.length === 0) {
        return { kind: 'blocked', reason: 'No paper portfolio exists yet; create one first.' };
      }
      return portfolios.some((p) => p.status === 'active')
        ? { kind: 'blocked', reason: 'More than one active paper portfolio exists and the replay binding is unclear, so none was chosen automatically — select one above.' }
        : {
            kind: 'blocked',
            reason: `None of the ${portfolios.length} portfolios is active (${portfolios
              .map((p) => label(PORTFOLIO_STATUS_LABELS, p.status))
              .reduce<string[]>((acc, value) => (acc.includes(value) ? acc : [...acc, value]), [])
              .join(', ')}), so history is viewable but you must select or create an active portfolio to submit.`,
          };
    }
    if (selected.status !== 'active') {
      return {
        kind: 'blocked',
        reason: `This portfolio is ${label(PORTFOLIO_STATUS_LABELS, selected.status)}: the backend paper-order endpoint returns 400 for retired/completed and risk checks reject paused, so this panel only submits for active portfolios. Portfolio history remains viewable.`,
      };
    }
    if (!selected.simulation_session_id) {
      return { kind: 'intent', reason: 'This portfolio is not bound to a historical replay session, so it uses the controlled executor intent flow.' };
    }
    if (!session || session.id !== selected.simulation_session_id) {
      return {
        kind: 'blocked',
        reason: `This portfolio is bound to session ${selected.simulation_session_id.slice(0, 8)}, but that replay cursor and strategy version are not loaded in the right column, so the fill basis cannot be confirmed and submission is disabled.`,
      };
    }
    if (rules.status !== 'resolved' || rules.versionId !== session.experiment_version_id) {
      return {
        kind: 'blocked',
        reason: `The strategy rules bound to this session are ${NOT_PROVIDED} (version ${session.experiment_version_id.slice(0, 8)}${
          rules.status === 'unresolved' ? `: ${rules.reason}` : rules.status === 'error' ? `: ${rules.message}` : ''
        }), so it cannot be confirmed that this form's instrument matches the active one, and submission is disabled.`,
      };
    }
    const assetId = rules.assetIds[0] ?? null;
    if (!assetId) {
      return {
        kind: 'blocked',
        reason: `The rules' strategy_spec.asset_ids is ${NOT_PROVIDED}, so the fill instrument cannot be confirmed and submission is disabled.`,
      };
    }
    if (assetId !== instrumentId) {
      return {
        kind: 'blocked',
        reason: `The current instrument is ${instrumentId}, but this session's rules bind ${assetId}. manual-decision only fills asset_ids[0], and a cross-instrument submission would misstate the research record, so it is disabled.`,
      };
    }
    if (session.status !== 'pending' && session.status !== 'active') {
      return {
        kind: 'blocked',
        reason: `This session is already ${label(SESSION_STATUS_LABELS, session.status)}; the backend no longer accepts decisions.`,
      };
    }
    return { kind: 'manual', reason: 'Submits to the bound session, using the replay cursor as the fill basis.' };
  }, [portfoliosLoaded, portfolios, selected, session, rules, instrumentId]);

  const isManual = target.kind === 'manual';
  // Reading the lifecycle record is not the same decision as being allowed to write to it:
  // the book stays available for the account's bound session even once submission is disabled.
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
      setError(err instanceof Error ? err.message : 'Operation failed');
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
        setError('Buy quantity must be a whole number of shares of at least 1.');
        return;
      }
    }
    if (rationale.length > 1000) {
      setError(`Decision rationale is ${rationale.length} characters, over the backend limit of 1000.`);
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
      const message = err instanceof Error ? err.message : 'Submission failed';
      if (err instanceof ApiError && err.status === 409) {
        setReview(null);
        if (message.includes('State version conflict')) {
          setError('State version conflict: the session was changed by another action; re-check the latest cursor and submit again.');
        } else if (message.includes('Idempotency key conflict') || message.includes('idempotency_key')) {
          setError(
            'Idempotency key conflict: this key already left an order on this account and the system will not submit a second one. ' +
            'The order list was re-read; treat it as authoritative.'
          );
          loadSessionOrders(review.sessionId);
        } else {
          setError(`Conflict (409): ${message}`);
        }
      } else if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setError(`The backend rejected the submission (${err.status}): ${message}`);
      } else {
        setManualUnknown(
          `Submission outcome unknown (${message}). The decision may already exist on the server; check first, ` +
          'and retry with the same idempotency key if needed — the same key cannot create a second order, and the fill is still decided once by market time.'
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
          ? `Reconciliation: the decision exists (id ${String(hit.id).slice(0, 8)}, step ${text(hit.step_number)}, type ${text(hit.decision_type)}). Do not retry.`
          : `Reconciliation: no decision with this idempotency key (checked ${Array.isArray(rows) ? rows.length : 0} rows); it does not exist yet and it is safe to retry.`
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
      setError(err instanceof Error ? err.message : 'Reconciliation failed');
    } finally {
      setBusy(false);
    }
  };

  const createIntent = async () => {
    if (!portfolioId) {
      setError('No paper portfolio is available; create one first.');
      return;
    }
    if (lotSize === null || lotSize === undefined) {
      setError("This instrument's lot size is unknown, so it cannot be ordered. Confirm the instrument specification first.");
      return;
    }
    if (instrument?.currency && selected && portfolioId === selected.id && selected.currency !== instrument.currency) {
      setError(`The instrument currency (${instrument.currency}) does not match the portfolio currency (${selected.currency}); automatic FX conversion is not supported.`);
      return;
    }
    if (quantity <= 0) {
      setError('Quantity must be greater than 0.');
      return;
    }
    if (orderType === 'LIMIT' && !(parseFloat(limitPrice) > 0)) {
      setError('A limit order needs a valid limit price.');
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
      setNotice('Intent created; it is submitted only after explicit authorization.');
    }
  };

  const authorizeIntent = async () => {
    if (!intent?.authorization_token) return;
    const result = await call(() => api.execution.authorize(intent.intent_id, intent.authorization_token!));
    if (result) {
      setIntent(result as ExecutionIntent);
      setNotice('Authorized. Confirm again before submitting (subject to the safety switch).');
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
      setNotice('Submitted to the controlled executor. Fills appear in the Fills tab.');
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message || 'Submission failed');
      } else {
        setError(err instanceof Error ? err.message : 'Submission failed');
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
      setNotice(`Reconciliation: the intent is still "authorized, not submitted" (${result.status}); the earlier submission was not sent.`);
    } else {
      setNotice(`Reconciliation: the server intent status is ${result.status}.`);
    }
  };

  const cancelIntent = async () => {
    if (!intent) return;
    const result = await call(() => api.execution.cancel(intent.intent_id));
    if (result) {
      setIntent(result as ExecutionIntent);
      setNotice('Intent cancelled.');
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
      setError("This order has no portfolio identifier, so it cannot be cancelled here; cancel it from that paper portfolio's order list.");
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
        `Order ${String(order.id).slice(0, 8)}… is now "${label(ORDER_STATUS_LABELS, String(status))}": the cancellation happened before market execution, so nothing was filled and cash and positions are unchanged.`
      );
      // Only a resolved request gets here: a rejected or failed cancel signals
      // nothing, so ⑧ can never be told to show a cancelled state that did not happen.
      onBookChanged?.();
      await Promise.all([loadSessionOrders(activeSessionId), loadPortfolios(activeSessionId)]);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Cancellation failed';
      setError(
        err instanceof ApiError && err.status === 400
          ? `The backend rejected the cancellation (400): ${message}. Only pending orders (not yet executed by the market) can be cancelled; a filled order must be offset, not erased.`
          : `Cancellation failed: ${message}`
      );
    } finally {
      setCancellingOrderId('');
    }
  };

  const createPortfolio = async () => {
    const result = await call(() => api.paperPortfolio.create({ initial_virtual_cash: 100000 }));
    if (result) {
      await loadPortfolios(activeSessionId);
      setNotice('Paper portfolio created (100,000 USD virtual cash).');
    }
  };

  const intentStatus = intent?.status;
  const fill = manualResult?.fill ?? null;
  const rejection = manualResult?.rejection ?? null;

  return (
    <div className="ws-panel ws-panel-fill" data-testid="order-preview-panel">
      {!hideHeader && <div className="ws-panel-header">Manual paper order</div>}
      <div className="ws-panel-body" style={{ padding: 10 }}>
        {error && (
          <div className="ws-message-error" role="alert" data-testid="order-error">
            {error}
          </div>
        )}
        {notice && (
          <div className="ws-message-ok" role="status">
            {notice}
          </div>
        )}

        <div className="ws-form-row">
          <div className="ws-form-field">
            <label htmlFor="order-portfolio-select">Portfolio</label>
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
                    — Select a paper portfolio —
                  </option>
                  {portfolios.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.currency} ·{' '}
                      {typeof p.current_virtual_cash === 'number'
                        ? p.current_virtual_cash.toLocaleString()
                        : NOT_PROVIDED}
                      {p.status !== 'active'
                        ? ` · ${label(PORTFOLIO_STATUS_LABELS, p.status)}`
                        : ''}
                      {p.simulation_session_id ? ' · Replay' : ''}
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
                    Create paper portfolio
                  </button>
                )}
              </>
            ) : (
              <button className="ws-btn ws-btn-neutral" onClick={createPortfolio} disabled={busy}>
                Create paper portfolio
              </button>
            )}
          </div>
        </div>

        {selected ? (
          <div className="order-account-context" data-testid="order-account-context">
            <span className="order-account-item">
              <span className="order-account-label">Portfolio</span>
              <span className="order-account-value" title={selected.id}>
                {selected.id.slice(0, 8)}
              </span>
            </span>
            <span className="order-account-item">
              <span className="order-account-label">Currency</span>
              <span className="order-account-value">{text(selected.currency)}</span>
            </span>
            <span className="order-account-item">
              <span className="order-account-label">Status</span>
              <span className="order-account-value" title={selected.status}>
                {label(PORTFOLIO_STATUS_LABELS, selected.status)}
              </span>
            </span>
            <span className="order-account-item">
              <span className="order-account-label">Virtual cash</span>
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
              <span className="order-account-label">Buying power</span>
              <span className="order-account-value" data-testid="order-buying-power">
                {NOT_PROVIDED}
              </span>
            </span>
            <p className="order-account-note">
              Buying power is not derived from virtual cash: the server returns no buying-power
              field, so it is shown as not provided. This system is paper-only and connects to no
              live broker.
            </p>
          </div>
        ) : (
          <p className="order-account-note" data-testid="order-account-context">
            No paper portfolio selected — create or select one before submitting a paper order.
          </p>
        )}

        <p className="ws-muted ws-small" data-testid="order-target">
          Submission target:{' '}
          {target.kind === 'manual'
            ? `historical replay session ${session?.id.slice(0, 8)} (cursor ${session ? sessionClock(session) : NOT_PROVIDED})`
            : target.kind === 'intent'
              ? 'controlled executor intent (paper portfolio, not bound to a replay cursor)'
              : 'disabled'}
          {target.kind !== 'intent' ? ` — ${target.reason}` : ''}
        </p>

        {target.kind === 'blocked' && (
          <div className="ws-message-error" role="alert" data-testid="order-blocked">
            Paper order cannot be submitted: {target.reason}
          </div>
        )}

        {isManual ? (
          <>
            <div className="ws-form-row">
              <div className="ws-form-field">
                <label htmlFor="order-action">Action</label>
                <select
                  id="order-action"
                  value={action}
                  onChange={(e) => {
                    setAction(e.target.value as ManualAction);
                  }}
                  data-testid="order-action"
                >
                  <option value="buy">Buy</option>
                  <option value="sell">Sell</option>
                  <option value="hold">Hold (no fill)</option>
                </select>
              </div>
              {action === 'buy' && (
                <div className="ws-form-field">
                  <label htmlFor="order-quantity-input">Quantity (shares)</label>
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
              <label htmlFor="order-rationale">Decision rationale (limit 1000 characters)</label>
              <textarea
                id="order-rationale"
                value={rationale}
                onChange={(e) => setRationale(e.target.value.slice(0, 1200))}
                rows={2}
                data-testid="order-rationale"
              />
            </div>

            <p className="ws-muted ws-small" data-testid="order-manual-semantics">
              Type: market (the backend manual-decision endpoint accepts only buy/sell/hold — no
              limit orders and no partial fills). A sell fills the whole position held by the
              session; a hold does not fill. Fill basis: the open of the next executable bar after
              the cursor (next_open_no_lookahead); the actual price comes from the server response
              and is not shown before submission.
            </p>
          </>
        ) : (
          target.kind === 'intent' && (
            <>
              <div className="ws-form-row">
                <div className="ws-form-field">
                  <label htmlFor="order-side">Side</label>
                  <select id="order-side" value={side} onChange={(e) => setSide(e.target.value as 'BUY' | 'SELL')}>
                    <option value="BUY">Buy</option>
                    <option value="SELL">Sell</option>
                  </select>
                </div>
                <div className="ws-form-field">
                  <label htmlFor="order-lots">Lots ({lotSize === null || lotSize === undefined ? NOT_PROVIDED : lotSize} shares per lot)</label>
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
                  <label htmlFor="order-type">Type</label>
                  <select id="order-type" value={orderType} onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}>
                    <option value="MARKET">Market</option>
                    <option value="LIMIT">Limit</option>
                  </select>
                </div>
                {orderType === 'LIMIT' && (
                  <div className="ws-form-field">
                    <label htmlFor="order-limit-price">Limit price ({instrument?.currency || ''})</label>
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
              <td className="ws-muted">Quantity</td>
              <td data-testid="order-quantity">
                {isManual
                  ? action === 'buy'
                    ? `${manualQuantity} shares`
                    : action === 'sell'
                      ? 'Whole position'
                      : 'No fill'
                  : `${quantity} shares`}
              </td>
            </tr>
            <tr>
              <td className="ws-muted">Current instrument</td>
              <td data-testid="order-symbol">{instrumentId}</td>
            </tr>
            <tr>
              <td className="ws-muted">Reference price (last close before cursor)</td>
              <td data-testid="order-reference">
                {lastClose !== null ? `${lastClose.toFixed(2)} ${instrument?.currency || ''}` : NOT_PROVIDED}
              </td>
            </tr>
            <tr>
              <td className="ws-muted">Estimated amount</td>
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
            {reviewStale ? 'Re-check paper order' : 'Review paper order'}
          </button>
        )}

        {isManual && review && (
          <div data-testid="order-review">
            <div className="ws-panel-header" style={{ marginTop: 6 }}>
              Review paper order{reviewStale ? ' (void)' : ''}
            </div>
            <table className="ws-table" style={{ marginBottom: 8 }}>
              <tbody>
                <tr>
                  <td className="ws-muted">Instrument</td>
                  <td>{instrumentId}</td>
                </tr>
                <tr>
                  <td className="ws-muted">Action</td>
                  <td>{label(ACTION_LABELS, review.action)}</td>
                </tr>
                <tr>
                  <td className="ws-muted">Quantity</td>
                  <td>{review.quantity !== null ? `${review.quantity} shares` : review.action === 'sell' ? 'Whole position' : 'No fill'}</td>
                </tr>
                <tr>
                  <td className="ws-muted">Rationale</td>
                  <td>{review.rationale || NOT_PROVIDED}</td>
                </tr>
                <tr>
                  <td className="ws-muted">Replay cursor</td>
                  <td data-testid="order-review-cursor">{review.cursorLabel}</td>
                </tr>
                <tr>
                  <td className="ws-muted">Idempotency key</td>
                  <td data-testid="order-review-key">{review.idempotency_key.slice(0, 18)}…</td>
                </tr>
              </tbody>
            </table>

            {reviewStale && (
              <div className="ws-message-error" role="alert" data-testid="order-review-stale">
                A field, instrument, portfolio or replay cursor changed: the earlier review is void.
                Choose “Review paper order” again.
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
                {manualPending ? 'Submitting…' : 'Submit paper order'}
              </button>
              {manualUnknown && (
                <button
                  className="ws-btn ws-btn-neutral"
                  onClick={reconcileManual}
                  disabled={busy}
                  data-testid="order-reconcile-manual"
                >
                  Reconcile outcome
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
                Cancel review
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
                <td className="ws-muted">Decision</td>
                <td>
                  {text(manualResult.decision?.id).slice(0, 13)}… · step {text(manualResult.decision?.step_number)} ·{' '}
                  {text(manualResult.decision?.decision_type)}
                </td>
              </tr>
              <tr>
                <td className="ws-muted">Snapshot date</td>
                <td>{text(manualResult.decision?.snapshot_date)}</td>
              </tr>
              <tr>
                <td className="ws-muted">Cash / equity after decision</td>
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
                        <td className="ws-muted">Order</td>
                        <td data-testid="order-manual-order">
                          {text(submitted.order_id).slice(0, 13)}… ·{' '}
                          <span className="ws-status-badge">
                            {label(ORDER_STATUS_LABELS, String(state))}
                          </span>{' '}
                          · {text(submitted.side)} {text(submitted.quantity)} shares ·{' '}
                          {text(submitted.asset_id)} ·{' '}
                          {submitted.actor_type === 'human' ? 'Human decision' : text(submitted.actor_type)}
                        </td>
                      </tr>
                      <tr>
                        <td className="ws-muted">Decision basis / submitted at (UTC)</td>
                        <td data-testid="order-manual-decided">
                          {text(submitted.decided_on_cutoff)} / {text(submitted.order_created_at_utc)}
                        </td>
                      </tr>
                      <tr>
                        <td className="ws-muted">Filled / requested quantity</td>
                        <td data-testid="order-manual-quantities">
                          {text(view?.filled_quantity ?? 0)} / {text(view?.requested_quantity ?? submitted.quantity)}
                          {view?.fill_count !== undefined ? ` · ${text(view.fill_count)} fills` : ''}
                        </td>
                      </tr>
                      <tr>
                        <td className="ws-muted">Lifecycle position</td>
                        <td data-testid="order-manual-execution">{executionSummary(view)}</td>
                      </tr>
                    </>
                  );
                })()}
              {fill && (
                <>
                  <tr>
                    <td className="ws-muted">Fill</td>
                    <td>
                      {text(fill.action)} {text(fill.quantity)} @ {text(fill.price)} ({text(fill.execution_rule)})
                    </td>
                  </tr>
                  <tr>
                    <td className="ws-muted">Fill date</td>
                    <td>{text(fill.execution_date)}</td>
                  </tr>
                  <tr>
                    <td className="ws-muted">Observed date / observed close</td>
                    <td>
                      {text(fill.observed_date)} / {text(fill.observed_close)}
                    </td>
                  </tr>
                  <tr>
                    <td className="ws-muted">Commission / slippage</td>
                    <td>
                      {text(fill.commission)} / {text(fill.slippage)}
                    </td>
                  </tr>
                  <tr>
                    <td className="ws-muted">Cost / net proceeds / gross P&amp;L</td>
                    <td>
                      {text(fill.total_cost)} / {text(fill.net_proceeds)} / {text(fill.gross_pnl)}
                    </td>
                  </tr>
                </>
              )}
              {rejection && (
                <tr>
                  <td className="ws-muted">Rejection</td>
                  <td data-testid="order-manual-rejection">
                    {label(REJECTION_LABELS, String(rejection.reason))}
                    {rejection.required !== undefined ? ` (required ${text(rejection.required)})` : ''}
                    {rejection.available !== undefined ? ` (available ${text(rejection.available)})` : ''}
                    {rejection.current_shares !== undefined ? ` (holding ${text(rejection.current_shares)} shares)` : ''}
                    {rejection.intent_preserved ? '; intent preserved' : ''}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}

        {bookSession && (
          <section style={{ marginTop: 10 }} data-testid="order-session-book">
            <div className="ws-panel-header">Orders in this session (submitted → pending → filled / cancelled)</div>
            <p className="ws-muted ws-small">
              The list is read from the paper account bound to this session and survives a reload.
              A pending order is an intent that has not filled: it is not counted as a holding and
              does not move cash. The fill is decided by market time — when the replay advances to
              the next executable bar, it executes once at that bar&apos;s open.
            </p>
            {!isManual && (
              <p className="ws-muted ws-small" data-testid="order-session-book-readonly">
                This session no longer accepts new decisions ({target.reason}); the order book is
                now read-only — submissions, pending orders, fills, cancellations and expired
                records already left here are still listed one by one and do not disappear when the
                session ends.
              </p>
            )}
            {!sessionOrdersLoaded ? (
              <p className="ws-muted ws-small" data-testid="order-session-book-loading" role="status">
                Loading the order book…
              </p>
            ) : sessionOrders.length === 0 ? (
              <p className="ws-muted ws-small" data-testid="order-session-book-empty">
                No orders in this session yet.
              </p>
            ) : (
              <>
                <table className="ws-table" data-testid="order-session-book-rows">
                  <thead>
                    <tr>
                      <th className="ws-muted">Order</th>
                      <th className="ws-muted">Status</th>
                      <th className="ws-muted">Decision basis / submitted at (UTC)</th>
                      <th className="ws-muted">Filled / requested</th>
                      <th className="ws-muted">Lifecycle position</th>
                      <th className="ws-muted">Actions</th>
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
                            {text(order.side)} {text(order.quantity)} shares · {text(order.asset_id)} ·{' '}
                            {order.actor_type === 'system'
                              ? 'Rule'
                              : order.actor_type === 'human'
                                ? 'Human'
                                : text(order.actor_type)}
                          </td>
                          <td>
                            <span className="ws-status-badge">
                              {label(ORDER_STATUS_LABELS, String(order.status))}
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
                                {cancellingOrderId === String(order.id) ? 'Cancelling…' : 'Cancel order'}
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
                    The order book belongs to account ({text(sessionOrders[0]?.order?.portfolio_id).slice(0, 8)}…)
                    which differs from the account selected in this form ({selected.id.slice(0, 8)}…):
                    cancellation and fills always follow the order book&apos;s account.
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
                Create execution intent
              </button>
            )}

            {intent && (
              <div>
                <table className="ws-table" style={{ marginBottom: 8 }}>
                  <tbody>
                    <tr>
                      <td className="ws-muted">Intent ID</td>
                      <td data-testid="order-intent-id">{intent.intent_id.slice(0, 13)}…</td>
                    </tr>
                    <tr>
                      <td className="ws-muted">Status</td>
                      <td data-testid="order-intent-status">
                        <span className="ws-status-badge">{intentStatus}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
                {intentUnknown && (
                  <div className="ws-message-error" role="alert" data-testid="order-intent-unknown">
                    Submission outcome unknown: the intent may have reached the executor. Reconcile
                    the intent status first rather than resubmitting.
                  </div>
                )}
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {intentStatus === 'pending' && (
                    <button className="ws-btn ws-btn-neutral" onClick={authorizeIntent} disabled={busy}>
                      Authorize
                    </button>
                  )}
                  {intentStatus === 'authorized' && (
                    <button className="ws-btn ws-btn-buy" onClick={submitIntent} disabled={busy} data-testid="order-submit">
                      Submit paper order (executor)
                    </button>
                  )}
                  {intentUnknown && (
                    <button className="ws-btn ws-btn-neutral" onClick={reconcileIntent} disabled={busy} data-testid="order-reconcile-intent">
                      Reconcile intent status
                    </button>
                  )}
                  {['pending', 'authorized', 'submitted'].includes(intentStatus || '') && (
                    <button className="ws-btn ws-btn-neutral" onClick={cancelIntent} disabled={busy}>
                      Cancel
                    </button>
                  )}
                  <Link
                    href="/execution"
                    className="ws-btn ws-btn-neutral"
                    style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
                  >
                    Execution overview
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
