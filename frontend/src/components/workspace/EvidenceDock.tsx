'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { PRICE_MODE_LABELS } from '@/lib/copy';
import EvidenceDrawer from './EvidenceDrawer';
import type { ReplaySession } from './ReplayController';
import {
  ACTOR_LABELS,
  CANNOT_COMPUTE,
  DECISION_TYPE_LABELS,
  INTENT_STATUS_LABELS,
  NOT_APPLICABLE,
  NOT_PROVIDED,
  ORDER_STATUS_LABELS,
  PORTFOLIO_STATUS_LABELS,
  POSITION_STATUS_LABELS,
  RECORD_TYPE_LABELS,
  SIDE_LABELS,
  fmtInstant,
  fmtMoney,
  fmtQuantity,
  fmtScalar,
  fmtSignedMoney,
  fmtUnrealized,
  inScope,
  label,
  useExecutionEvidence,
  usePortfolioEvidence,
  type DatasetKey,
  type EvidenceData,
  type JournalRecord,
  type OrderRow,
  type PortfolioChoice,
  type PositionRow,
  type Scope,
} from './evidence';
import {
  EVIDENCE_TABS as TABS,
  type EvidenceTabKey,
  type ExecutionIntent,
} from './types';

/** Tabs whose rows carry an instrument, and so can be scoped to the current instrument. */
const SCOPABLE: EvidenceTabKey[] = ['positions', 'orders-fills', 'journal'];

type Row =
  | { kind: 'position'; id: string; position: PositionRow }
  | { kind: 'order'; id: string; order: OrderRow; fills: JournalRecord[] }
  | { kind: 'journal'; id: string; record: JournalRecord }
  | { kind: 'intent'; id: string; intent: ExecutionIntent; fills: number };

/**
 * Container ⑧ — the evidence dock. It answers one question: what did this
 * workspace actually record, and where did that record come from. Every value is a
 * field the portfolio endpoints return; anything they do not return is labelled,
 * never estimated, and no backtest metric is allowed to stand in for this
 * portfolio's own result.
 */
export default function EvidenceDock({
  symbol,
  session,
  chosen,
  revision,
  tab: active,
  onTabChange,
  hideHeader,
}: {
  symbol: string;
  session: ReplaySession | null;
  /** The portfolio the order form currently speaks for; the dock follows it. */
  chosen?: PortfolioChoice | null;
  /** Bumped by the shell when an action changed this account outside both clocks. */
  revision?: number;
  tab: EvidenceTabKey;
  onTabChange: (tab: EvidenceTabKey) => void;
  hideHeader?: boolean;
}) {
  const data = usePortfolioEvidence(session, chosen, revision);
  const execution = useExecutionEvidence(active === 'orders-fills');
  const [scope, setScope] = useState<Scope>('all');
  const [open, setOpen] = useState<Row | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const triggerRef = useRef<HTMLElement | null>(null);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const moveFocus = (delta: number) => {
    const index = TABS.findIndex((t) => t.key === active);
    const next = TABS[(index + delta + TABS.length) % TABS.length];
    onTabChange(next.key);
    tabRefs.current[next.key]?.focus();
  };

  const currency = data.portfolio?.currency ?? null;

  const scopedPositions = useMemo(
    () => data.positions.filter((p) => inScope(p.asset_id, scope, symbol)),
    [data.positions, scope, symbol]
  );
  const scopedOrders = useMemo(
    () => data.orders.filter((o) => inScope(o.asset_id, scope, symbol)),
    [data.orders, scope, symbol]
  );
  const scopedJournal = useMemo(
    () => data.journal.filter((r) => inScope(r.asset_id, scope, symbol)),
    [data.journal, scope, symbol]
  );
  const scopedIntents = useMemo(
    () => execution.intents.filter((i) => inScope(i.symbol, scope, symbol)),
    [execution.intents, scope, symbol]
  );

  const fillsByOrder = useMemo(() => {
    const map: Record<string, JournalRecord[]> = {};
    for (const r of data.journal) {
      if (r.record_type !== 'fill' || !r.order_id) continue;
      (map[r.order_id] ??= []).push(r);
    }
    return map;
  }, [data.journal]);

  // A refetch must not leave an open drawer describing the book as it was before
  // the change the user just made, so re-point it at the fresh record with the
  // same id. A record the fresh read does not contain keeps its current detail —
  // blanking something the user is reading would be its own wrong state.
  useEffect(() => {
    setOpen((current) => {
      if (!current) return current;
      if (current.kind === 'order') {
        const fresh = data.orders.find((o) => o.id === current.id);
        return fresh ? { ...current, order: fresh, fills: fillsByOrder[current.id] ?? [] } : current;
      }
      if (current.kind === 'position') {
        const fresh = data.positions.find((p) => p.id === current.id);
        return fresh ? { ...current, position: fresh } : current;
      }
      return current;
    });
  }, [data.orders, data.positions, fillsByOrder]);

  const openRow = (row: Row, el: HTMLElement) => {
    triggerRef.current = el;
    setOpen(row);
  };

  const downloadCsv = useCallback(async () => {
    if (!data.portfolio) return;
    const portfolioId = data.portfolio.id;
    setExporting(true);
    setExportError('');
    try {
      const text = await api.paperPortfolio.journalCsv(portfolioId);
      const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `trade-journal-${portfolioId}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  }, [data.portfolio]);

  const datasetError = (key: DatasetKey, name: string) =>
    data.errors[key] ? (
      <div className="ws-message-error" role="alert" data-testid={`evidence-error-${key}`}>
        {name} could not be loaded: {data.errors[key]}
      </div>
    ) : null;

  const scopeBar = SCOPABLE.includes(active) && (
    <div className="ws-evidence-scope" data-testid="evidence-scope">
      <span className="ctx-label">Instrument scope</span>
      <button
        type="button"
        className={`ws-tab ${scope === 'all' ? 'ws-tab-active' : ''}`}
        aria-pressed={scope === 'all'}
        onClick={() => setScope('all')}
        data-testid="evidence-scope-all"
      >
        All instruments
      </button>
      <button
        type="button"
        className={`ws-tab ${scope === 'current' ? 'ws-tab-active' : ''}`}
        aria-pressed={scope === 'current'}
        onClick={() => setScope('current')}
        data-testid="evidence-scope-current"
      >
        Current instrument ({symbol})
      </button>
      {scope === 'current' && (
        <button
          type="button"
          className="ws-btn ws-btn-neutral"
          onClick={() => setScope('all')}
          data-testid="evidence-scope-clear"
        >
          Clear filter
        </button>
      )}
      <span className="ws-small" data-testid="evidence-scope-note">
        Client-side filter over the records this panel has already loaded; it does not query the
        backend.
      </span>
    </div>
  );

  const noPortfolio = data.portfolio === null;

  const emptyForScope = (kind: 'positions' | 'orders' | 'journal', noun: string, total: number) => (
    <div className="ws-evidence-empty" data-testid={`evidence-empty-${kind}`}>
      <p className="ws-muted">
        No {noun} for the current instrument ({symbol}) among the {total} {noun} this panel has
        loaded.
      </p>
      <button
        type="button"
        className="ws-btn ws-btn-neutral"
        onClick={() => setScope('all')}
        data-testid={`evidence-empty-clear-${kind}`}
      >
        Show all instruments
      </button>
    </div>
  );

  const positionsBody = noPortfolio ? (
    <NoPortfolio resolution={data.resolution} />
  ) : (
    <>
      {datasetError('positions', 'Positions')}
      {scopedPositions.length === 0 ? (
        scope === 'current' && data.positions.length > 0 ? (
          emptyForScope('positions', 'positions', data.positions.length)
        ) : (
          <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-positions">
            No positions recorded for this portfolio yet.
          </p>
        )
      ) : (
        <table className="ws-table" data-testid="evidence-positions-table">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Quantity</th>
              <th>Average cost</th>
              <th>Realized P&amp;L</th>
              <th>Unrealized P&amp;L</th>
              <th>Status</th>
              <th>Opened</th>
            </tr>
          </thead>
          <tbody>
            {scopedPositions.map((p) => (
              <tr key={p.id}>
                <td>
                  <button
                    type="button"
                    className="ws-link-btn"
                    onClick={(e) => openRow({ kind: 'position', id: p.id, position: p }, e.currentTarget)}
                    data-testid={`evidence-position-${p.id}`}
                  >
                    {p.asset_id}
                  </button>
                </td>
                <td className="numeric">{fmtQuantity(p.quantity)}</td>
                <td className="numeric">{fmtMoney(p.average_entry_price, currency)}</td>
                <td className="numeric">{fmtSignedMoney(p.realized_pnl, currency)}</td>
                <td className="numeric">{fmtUnrealized(p.unrealized_pnl, currency)}</td>
                <td>{label(POSITION_STATUS_LABELS, p.status)}</td>
                <td>{fmtInstant(p.opened_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );

  const ordersBody = noPortfolio ? (
    <NoPortfolio resolution={data.resolution} />
  ) : (
    <>
      {datasetError('orders', 'Paper orders')}
      <div className="ws-evidence-section">Paper orders</div>
      {scopedOrders.length === 0 ? (
        scope === 'current' && data.orders.length > 0 ? (
          emptyForScope('orders', 'orders', data.orders.length)
        ) : (
          <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-orders">
            No paper orders recorded for this portfolio yet.
          </p>
        )
      ) : (
        <table className="ws-table" data-testid="evidence-orders-table">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Actor</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Order status</th>
              <th>Submitted (UTC)</th>
              <th>Filled · replay time</th>
              <th>Net cash change</th>
            </tr>
          </thead>
          <tbody>
            {scopedOrders.map((o) => {
              const fills = fillsByOrder[o.id] ?? [];
              const priced = fills.filter((f) => typeof f.net_cash_change === 'number');
              const net = priced.length === 0 ? null : priced.reduce((s, f) => s + (f.net_cash_change as number), 0);
              const exec = fills[0]?.execution_date ?? null;
              const bar = fills[0]?.bar_timestamp ?? null;
              return (
                <tr key={o.id}>
                  <td>
                    <button
                      type="button"
                      className="ws-link-btn"
                      onClick={(e) => openRow({ kind: 'order', id: o.id, order: o, fills }, e.currentTarget)}
                      data-testid={`evidence-order-${o.id}`}
                    >
                      {o.asset_id}
                    </button>
                  </td>
                  <td>{label(ACTOR_LABELS, o.actor_type)}</td>
                  <td>{label(SIDE_LABELS, o.side)}</td>
                  <td className="numeric">{fmtQuantity(o.quantity)}</td>
                  <td>{label(ORDER_STATUS_LABELS, o.status)}</td>
                  <td>{fmtInstant(o.created_at)}</td>
                  <td data-testid={`evidence-order-exec-time-${o.id}`}>
                    {exec ? `${exec}${bar ? ` · ${bar.replace('T', ' ')}` : ''}` : NOT_APPLICABLE}
                  </td>
                  <td className="numeric">{fmtSignedMoney(net, currency)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div className="ws-evidence-section">Fills</div>
      {datasetError('journal', 'Fills')}
      {scopedJournal.filter((r) => r.record_type === 'fill').length === 0 ? (
        <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-fills">
          {data.journal.length === 0
            ? 'This portfolio has no fills to show (the journal endpoint returned 0 rows).'
            : 'No fills in the current scope.'}
        </p>
      ) : (
        <table className="ws-table" data-testid="evidence-fills-table">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Fill price (before costs)</th>
              <th>Slippage</th>
              <th>Commission</th>
              <th>Net cash change</th>
              <th>Fill date</th>
              <th>Bar time</th>
              <th>Fill reason</th>
            </tr>
          </thead>
          <tbody>
            {scopedJournal
              .filter((r) => r.record_type === 'fill')
              .map((r, idx) => {
                const rowKey = r.fill_id ?? r.order_id ?? `fill-${idx}`;
                return (
                <tr key={r.fill_id ?? `fill-${idx}`}>
                  <td>
                    <button
                      type="button"
                      className="ws-link-btn"
                      onClick={(e) =>
                        openRow(
                          { kind: 'journal', id: r.fill_id ?? r.order_id ?? `fill-${idx}`, record: r },
                          e.currentTarget
                        )
                      }
                      data-testid={`evidence-fill-${rowKey}`}
                    >
                      {r.asset_id}
                    </button>
                  </td>
                  <td>{label(SIDE_LABELS, r.side)}</td>
                  <td className="numeric">{fmtQuantity(r.quantity)}</td>
                  <td className="numeric">{fmtMoney(r.execution_price_before_costs, r.currency)}</td>
                  <td className="numeric">{fmtMoney(r.slippage_amount, r.currency)}</td>
                  <td className="numeric">{fmtMoney(r.commission_amount, r.currency)}</td>
                  <td className="numeric">{fmtSignedMoney(r.net_cash_change, r.currency)}</td>
                  <td>{r.execution_date ?? NOT_PROVIDED}</td>
                  <td data-testid={`evidence-fill-bar-time-${rowKey}`}>
                    {r.bar_timestamp ? r.bar_timestamp.replace('T', ' ') : NOT_APPLICABLE}
                  </td>
                  <td>{fmtScalar(r.simulated_fill_reason)}</td>
                </tr>
                );
              })}
          </tbody>
        </table>
      )}

      <div className="ws-evidence-section">Execution intents</div>
      {execution.error && (
        <div className="ws-message-error" role="alert" data-testid="evidence-error-intents">
          Execution intents could not be loaded: {execution.error}
        </div>
      )}
      {execution.loading && scopedIntents.length === 0 ? (
        <p className="ws-muted" style={{ padding: 10 }} role="status">
          Loading…
        </p>
      ) : scopedIntents.length === 0 ? (
        <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-intents">
          {execution.intents.length === 0
            ? 'No execution intents.'
            : 'No execution intents in the current scope.'}
        </p>
      ) : (
        <table className="ws-table" data-testid="evidence-intents-table">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Status</th>
              <th>Fill reports</th>
              <th>Created (UTC)</th>
            </tr>
          </thead>
          <tbody>
            {scopedIntents.map((i) => (
              <tr key={i.intent_id}>
                <td>
                  <button
                    type="button"
                    className="ws-link-btn"
                    onClick={(e) =>
                      openRow(
                        { kind: 'intent', id: i.intent_id, intent: i, fills: (execution.fills[i.intent_id] ?? []).length },
                        e.currentTarget
                      )
                    }
                    data-testid={`evidence-intent-${i.intent_id}`}
                  >
                    {i.symbol}
                  </button>
                </td>
                <td>{label(SIDE_LABELS, i.side)}</td>
                <td className="numeric">{fmtQuantity(i.quantity)}</td>
                <td>{label(INTENT_STATUS_LABELS, i.status)}</td>
                <td className="numeric">{(execution.fills[i.intent_id] ?? []).length}</td>
                <td>{fmtInstant(i.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );

  const journalBody = noPortfolio ? (
    <NoPortfolio resolution={data.resolution} />
  ) : (
    <>
      {datasetError('journal', 'Automated journal')}
      <div className="ws-evidence-toolbar">
        <button
          type="button"
          className="ws-btn ws-btn-accent"
          onClick={() => void downloadCsv()}
          disabled={exporting}
          data-testid="evidence-export-csv"
        >
          {exporting ? 'Exporting…' : 'Export whole portfolio (CSV)'}
        </button>
        <span className="ws-small" data-testid="evidence-export-scope-note">
          Export scope: every order and fill in the whole portfolio
          {data.portfolio ? ` (${data.portfolio.id.slice(0, 8)}…)` : ''}, ignoring the “Instrument
          scope” filter above.
        </span>
        {exportError && (
          <div className="ws-message-error" role="alert" data-testid="evidence-export-error">
            {exportError}
          </div>
        )}
      </div>
      {scopedJournal.length === 0 ? (
        scope === 'current' && data.journal.length > 0 ? (
          emptyForScope('journal', 'journal records', data.journal.length)
        ) : (
          <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-journal">
            This portfolio has no journal records yet. The backend writes one row per fill, and
            keeps a row for orders that never filled.
          </p>
        )
      ) : (
        <table className="ws-table" data-testid="evidence-journal-table">
          <thead>
            <tr>
              <th>Record type</th>
              <th>Instrument</th>
              <th>Actor</th>
              <th>Side</th>
              <th>Order status</th>
              <th>Decision</th>
              <th>Replay cut-off</th>
              <th>Fill date</th>
              <th>Bar time</th>
              <th>Submitted (UTC)</th>
              <th>Net cash change</th>
              <th>Fill reason</th>
            </tr>
          </thead>
          <tbody>
            {scopedJournal.map((r, idx) => {
              const rowKey = r.fill_id ?? r.order_id ?? `row-${idx}`;
              return (
              <tr key={`${r.record_type}-${r.fill_id ?? r.order_id}-${idx}`}>
                <td>{label(RECORD_TYPE_LABELS, r.record_type)}</td>
                <td>
                  <button
                    type="button"
                    className="ws-link-btn"
                    onClick={(e) =>
                      openRow(
                        {
                          kind: 'journal',
                          id: r.fill_id ?? r.order_id ?? `row-${idx}`,
                          record: r,
                        },
                        e.currentTarget
                      )
                    }
                    data-testid={`evidence-journal-row-${rowKey}`}
                  >
                    {r.asset_id ?? NOT_PROVIDED}
                  </button>
                </td>
                <td>{label(ACTOR_LABELS, r.actor_type)}</td>
                <td>{label(SIDE_LABELS, r.side)}</td>
                <td>{label(ORDER_STATUS_LABELS, r.order_status)}</td>
                <td>{label(DECISION_TYPE_LABELS, r.decision_type)}</td>
                <td>{r.data_cutoff_at ?? NOT_PROVIDED}</td>
                <td>{r.execution_date ?? (r.record_type === 'order' ? NOT_APPLICABLE : NOT_PROVIDED)}</td>
                <td>{r.bar_timestamp ? r.bar_timestamp.replace('T', ' ') : NOT_APPLICABLE}</td>
                <td>{r.order_created_at_utc ? fmtInstant(r.order_created_at_utc) : NOT_PROVIDED}</td>
                <td className="numeric">{fmtSignedMoney(r.net_cash_change, r.currency)}</td>
                <td>{fmtScalar(r.simulated_fill_reason)}</td>
              </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );

  const resultsBody = noPortfolio ? <NoPortfolio resolution={data.resolution} /> : <ResultsBody data={data} />;

  const bodies: Record<EvidenceTabKey, ReactNode> = {
    positions: positionsBody,
    'orders-fills': ordersBody,
    journal: journalBody,
    results: resultsBody,
    research: <RegistryBody kind="research" />,
    models: <RegistryBody kind="models" />,
    log: <RegistryBody kind="log" />,
  };

  return (
    <section className="ws-panel ws-panel-fill ws-evidence-dock" data-testid="evidence-dock" aria-label="Evidence panel">
      {!hideHeader && <div className="ws-panel-header">Evidence panel</div>}
      <div
        className="ws-tabs"
        role="tablist"
        aria-label="Evidence panel tabs"
        data-testid="evidence-tabs"
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') {
            e.preventDefault();
            moveFocus(1);
          } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            moveFocus(-1);
          } else if (e.key === 'Home') {
            e.preventDefault();
            onTabChange(TABS[0].key);
            tabRefs.current[TABS[0].key]?.focus();
          } else if (e.key === 'End') {
            const last = TABS[TABS.length - 1].key;
            e.preventDefault();
            onTabChange(last);
            tabRefs.current[last]?.focus();
          }
        }}
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            ref={(el) => {
              tabRefs.current[t.key] = el;
            }}
            type="button"
            role="tab"
            id={`tab-${t.key}`}
            aria-selected={active === t.key}
            aria-controls={`evidence-tabpanel-${t.key}`}
            tabIndex={active === t.key ? 0 : -1}
            className={`ws-tab ${active === t.key ? 'ws-tab-active' : ''}`}
            onClick={() => onTabChange(t.key)}
            data-testid={`tab-${t.key}`}
          >
            {t.label}
          </button>
        ))}
        <button
          type="button"
          className="ws-icon-btn"
          style={{ marginLeft: 'auto', alignSelf: 'center' }}
          title="Reload evidence"
          aria-label="Reload evidence"
          onClick={() => void data.reload()}
          data-testid="evidence-reload"
        >
          ⟳
        </button>
      </div>

      <div className="ws-panel-body" style={{ position: 'relative' }}>
        <p className="ws-small" data-testid="evidence-resolution" style={{ padding: '8px 10px 0' }}>
          {data.portfolio ? (
            <>
              Evidence portfolio{' '}
              <span className="numeric" data-testid="evidence-portfolio-id">
                {data.portfolio.id}
              </span>{' '}
              ({label(PORTFOLIO_STATUS_LABELS, data.portfolio.status)}): {data.resolution.reason}
            </>
          ) : (
            <>Portfolio resolution: {data.resolution.reason}</>
          )}
        </p>
        {data.loading && (
          <p className="ws-muted" style={{ padding: 8 }} role="status" data-testid="evidence-loading">
            Loading…
          </p>
        )}
        {scopeBar}
        <div
          role="tabpanel"
          id={`evidence-tabpanel-${active}`}
          aria-labelledby={`tab-${active}`}
          data-testid={`evidence-tabpanel-${active}`}
        >
          {bodies[active]}
        </div>
      </div>

      {open && (
        <EvidenceDrawer
          testId="evidence-drawer"
          ariaLabel={`${DRAWER_TITLES[open.kind]} ${open.id}`}
          onClose={() => setOpen(null)}
          returnFocusRef={triggerRef}
        >
          <DrawerBody row={open} currency={currency} semantics={data.columnSemantics} columns={data.journalColumns} />
        </EvidenceDrawer>
      )}
    </section>
  );
}

const DRAWER_TITLES: Record<Row['kind'], string> = {
  position: 'Position details',
  order: 'Paper order details',
  journal: 'Journal record details',
  intent: 'Execution intent details',
};

function NoPortfolio({ resolution }: { resolution: EvidenceData['resolution'] }) {
  return (
    <div style={{ padding: 12 }}>
      <div className="ws-symbol-label">No evidence to show</div>
      <p className="ws-muted" data-testid="evidence-no-portfolio">
        {resolution.reason}
      </p>
      <Link
        href="/paper-portfolio"
        className="ws-btn ws-btn-neutral"
        style={{ textDecoration: 'none', display: 'inline-block' }}
      >
        Go to paper portfolios
      </Link>
    </div>
  );
}

/**
 * The Results tab lists only what the portfolio endpoints actually compute. There is
 * no portfolio-level results endpoint, so no equity curve, period return, Sharpe
 * or drawdown is offered; an experiment run's backtest metrics belong to the
 * version, not to this book, and are deliberately absent.
 */
function ResultsBody({ data }: { data: EvidenceData }) {
  const p = data.portfolio;
  if (!p) return null;
  const cash = typeof p.current_virtual_cash === 'number' ? p.current_virtual_cash : null;
  const initial = typeof p.initial_virtual_cash === 'number' && p.initial_virtual_cash !== 0 ? p.initial_virtual_cash : null;
  const cashOnlyPct = cash !== null && initial !== null ? ((cash - initial) / initial) * 100 : null;
  const valuation = p.valuation ?? null;
  const openCount = data.positions.filter((x) => x.status === 'open').length;
  const fillCount = data.journal.filter((x) => x.record_type === 'fill').length;

  return (
    <div style={{ padding: 10 }}>
      {datasetNote(data)}
      <table className="ws-table" data-testid="evidence-results-table">
        <tbody>
          <ResRow label="Initial virtual cash" value={fmtMoney(p.initial_virtual_cash, p.currency)} testId="results-initial-cash" />
          <ResRow label="Current cash" value={fmtMoney(p.current_virtual_cash, p.currency)} testId="results-cash" />
          <ResRow
            label="Cash change (excluding unrealized P&L)"
            value={
              cashOnlyPct === null
                ? CANNOT_COMPUTE
                : `${cashOnlyPct >= 0 ? '+' : '−'}${Math.abs(cashOnlyPct).toFixed(2)}%`
            }
            testId="results-cash-return"
          />
          <ResRow label="Realized P&L" value={fmtSignedMoney(p.total_realized_pnl, p.currency)} testId="results-realized-pnl" />
          <ResRow
            label="Unrealized P&L"
            value={fmtUnrealized(p.total_unrealized_pnl, p.currency)}
            testId="results-unrealized-pnl"
          />
          <ResRow
            label="Valuation basis"
            value={
              valuation
                ? `${fmtScalar(valuation.provider)} · ${
                    valuation.mode ? label(PRICE_MODE_LABELS, valuation.mode) : NOT_PROVIDED
                  } · data as of ${fmtScalar(valuation.data_as_of)}`
                : NOT_PROVIDED
            }
            testId="results-valuation-basis"
          />
          {valuation?.unpriced_assets && valuation.unpriced_assets.length > 0 && (
            <ResRow
              label="Unpriced assets"
              value={valuation.unpriced_assets.join(', ')}
              testId="results-unpriced-assets"
            />
          )}
          <ResRow label="Open positions" value={String(openCount)} testId="results-open-positions" />
          <ResRow label="Paper order count" value={String(data.orders.length)} testId="results-order-count" />
          <ResRow label="Fill count" value={String(fillCount)} testId="results-fill-count" />
          <ResRow label="Decision record count" value={String(data.decisions.length)} testId="results-decision-count" />
        </tbody>
      </table>

      <div className="ws-evidence-gap" data-testid="evidence-results-gap" role="note">
        <div className="ws-symbol-label">What this panel does not provide</div>
        <p className="ws-muted">
          The backend has no portfolio-level results endpoint: there is no equity curve, period
          return, Sharpe ratio, maximum drawdown or win rate to query, so the rows above only list
          what the paper-portfolio detail and the positions, orders, journal and decisions endpoints
          actually return. “Cash change” is computed in the browser from the initial and current
          cash columns; it counts cash only, excludes unrealized P&L, and is not a total return.
          Experiment backtest metrics belong to the strategy version and its run, not to this
          portfolio&apos;s actual performance, so they are not shown here.
        </p>
        <Link href={`/paper-portfolio/${p.id}`} data-testid="evidence-results-link">
          Full portfolio page
        </Link>
      </div>
    </div>
  );
}

function datasetNote(data: EvidenceData) {
  const failed = Object.entries(data.errors).filter(([, message]) => !!message);
  if (failed.length === 0) return null;
  return (
    <div className="ws-message-error" role="alert" data-testid="evidence-results-errors">
      Some data could not be loaded:{' '}
      {failed.map(([key, message]) => `${key} (${message})`).join(', ')}
    </div>
  );
}

function ResRow({ label: text, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <tr>
      <td className="ws-muted">{text}</td>
      <td data-testid={testId}>{value}</td>
    </tr>
  );
}

/** Registry tabs kept from the previous panel: experiments, models, account events. */
function RegistryBody({ kind }: { kind: 'research' | 'models' | 'log' }) {
  const [state, setState] = useState<{ loading: boolean; error: string; body: ReactNode }>({
    loading: true,
    error: '',
    body: null,
  });

  const load = useCallback(async () => {
    setState({ loading: true, error: '', body: null });
    try {
      if (kind === 'research') {
        const rows = await api.experiments.list();
        const list: { id: string; title: string; status: string; updated_at: string }[] = Array.isArray(rows) ? rows : [];
        setState({
          loading: false,
          error: '',
          body:
            list.length === 0 ? (
              <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-research">
                No experiments.
              </p>
            ) : (
              <table className="ws-table" data-testid="evidence-research-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {list.slice(0, 30).map((e) => (
                    <tr key={e.id}>
                      <td>
                        <Link href={`/experiments/${e.id}`}>{e.title}</Link>
                      </td>
                      <td>{e.status}</td>
                      <td>{fmtInstant(e.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ),
        });
        return;
      }
      if (kind === 'models') {
        const response = await api.ml.listModels();
        const list: {
          id?: string;
          model_id?: string;
          name?: string;
          model_type?: string;
          algorithm?: string;
          created_at?: string;
          trained_at?: string;
        }[] = response?.models ?? [];
        setState({
          loading: false,
          error: '',
          body:
            list.length === 0 ? (
              <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-models">
                No models.
              </p>
            ) : (
              <table className="ws-table" data-testid="evidence-models-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Type</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {list.slice(0, 30).map((m) => (
                    <tr key={m.model_id ?? m.id}>
                      <td>
                        <Link href="/models">{m.name ?? (m.model_id ?? m.id ?? '').slice(0, 8)}</Link>
                      </td>
                      <td>{fmtScalar(m.model_type ?? m.algorithm)}</td>
                      <td>{fmtInstant(m.trained_at ?? m.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ),
        });
        return;
      }
      const events = await api.pilot.getMyUsageEvents();
      const list: { id: string; event_type: string; created_at: string }[] = Array.isArray(events) ? events : [];
      setState({
        loading: false,
        error: '',
        body:
          list.length === 0 ? (
            <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-log">
              No system activity records.
            </p>
          ) : (
            <table className="ws-table" data-testid="evidence-log-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                </tr>
              </thead>
              <tbody>
                {list.slice(0, 50).map((e) => (
                  <tr key={e.id}>
                    <td>{fmtInstant(e.created_at)}</td>
                    <td>{e.event_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ),
      });
    } catch (e) {
      setState({ loading: false, error: e instanceof Error ? e.message : 'Load failed', body: null });
    }
  }, [kind]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.loading) {
    return (
      <p className="ws-muted" style={{ padding: 10 }} role="status" data-testid={`evidence-registry-loading-${kind}`}>
        Loading…
      </p>
    );
  }
  if (state.error) {
    return (
      <div className="ws-message-error" role="alert" style={{ margin: 10 }} data-testid={`evidence-registry-error-${kind}`}>
        Could not load: {state.error}
      </div>
    );
  }
  return <>{state.body}</>;
}

/* ── drawer content ───────────────────────────────────────────────── */

function DrawerBody({
  row,
  currency,
  semantics,
  columns,
}: {
  row: Row;
  currency: string | null;
  semantics: Record<string, string>;
  columns: string[];
}) {
  if (row.kind === 'position') {
    const p = row.position;
    return (
      <>
        <DrawerHeading title="Position details" id={p.id} />
        <DRow label="Instrument" value={p.asset_id} testId="drawer-position-asset" />
        <DRow label="Portfolio id" value={p.portfolio_id} testId="drawer-position-portfolio" />
        <DRow label="Quantity" value={fmtQuantity(p.quantity)} testId="drawer-position-qty" />
        <DRow label="Average cost" value={fmtMoney(p.average_entry_price, currency)} testId="drawer-position-cost" />
        <DRow label="Realized P&L" value={fmtSignedMoney(p.realized_pnl, currency)} testId="drawer-position-realized" />
        <DRow label="Unrealized P&L" value={fmtUnrealized(p.unrealized_pnl, currency)} testId="drawer-position-unrealized" />
        <DRow label="Status" value={label(POSITION_STATUS_LABELS, p.status)} testId="drawer-position-status" />
        <DRow label="Opened" value={fmtInstant(p.opened_at)} testId="drawer-position-opened" />
        <DRow
          label="Closed"
          value={p.closed_at ? fmtInstant(p.closed_at) : NOT_APPLICABLE}
          testId="drawer-position-closed"
        />
      </>
    );
  }

  if (row.kind === 'order') {
    const o = row.order;
    return (
      <>
        <DrawerHeading title="Paper order details" id={o.id} />
        <DRow label="Instrument" value={o.asset_id} testId="drawer-order-asset" />
        <DRow label="Actor (actor_type)" value={label(ACTOR_LABELS, o.actor_type)} testId="drawer-order-actor" />
        <DRow label="Side" value={label(SIDE_LABELS, o.side)} testId="drawer-order-side" />
        <DRow label="Quantity" value={fmtQuantity(o.quantity)} testId="drawer-order-qty" />
        <DRow label="Order type" value={fmtScalar(o.order_type)} testId="drawer-order-type" />
        <DRow label="Order status" value={label(ORDER_STATUS_LABELS, o.status)} testId="drawer-order-status" />
        <DRow label="Risk check" value={fmtScalar(o.risk_check_status)} testId="drawer-order-risk" />
        <DRow
          label="Submitted (created_at)"
          value={fmtInstant(o.created_at)}
          testId="drawer-order-created"
        />
        <DRow label="Pricing / replay cut-off" value={fmtScalar(o.data_cutoff_at)} testId="drawer-order-cutoff" />
        <DRow
          label="Strategy or model version"
          value={o.model_or_strategy_version_id ?? NOT_PROVIDED}
          testId="drawer-order-version"
        />
        <DRow label="Decision id" value={o.decision_id ?? NOT_PROVIDED} testId="drawer-order-decision" />
        <DRow label="Idempotency key" value={o.idempotency_key ?? NOT_PROVIDED} testId="drawer-order-idempotency" />
        <DRow label="Rationale" value={o.rationale ?? NOT_PROVIDED} testId="drawer-order-rationale" />
        <div className="ws-evidence-section">Fills ({row.fills.length})</div>
        {row.fills.length === 0 ? (
          <p className="ws-muted" data-testid="drawer-order-no-fills">
            This order has no fill rows in the loaded journal.
          </p>
        ) : (
          row.fills.map((f, idx) => (
            <div key={f.fill_id ?? idx} className="ws-evidence-subrow" data-testid={`drawer-order-fill-${idx}`}>
              <div className="ws-small">
                fill id{' '}
                <span className="numeric" data-testid={`drawer-order-fill-id-${idx}`}>
                  {f.fill_id ?? NOT_PROVIDED}
                </span>
              </div>
              <div className="ws-small">
                Fill date {f.execution_date ?? NOT_PROVIDED}
                {f.bar_timestamp ? ` · Bar ${f.bar_timestamp.replace('T', ' ')}` : ` · Bar time ${NOT_APPLICABLE}`}
              </div>
              <div className="ws-small">
                Observed price {fmtMoney(f.observed_price, f.currency)} · Fill price{' '}
                {fmtMoney(f.execution_price_before_costs, f.currency)}
              </div>
              <div className="ws-small">
                Slippage {fmtMoney(f.slippage_amount, f.currency)} · Commission{' '}
                {fmtMoney(f.commission_amount, f.currency)} · Net cash{' '}
                {fmtSignedMoney(f.net_cash_change, f.currency)}
              </div>
              <div className="ws-small">Fill reason {fmtScalar(f.simulated_fill_reason)}</div>
              <div className="ws-small">Exit / outcome {fmtScalar(f.outcome_status)}</div>
            </div>
          ))
        )}
      </>
    );
  }

  if (row.kind === 'intent') {
    const i = row.intent;
    return (
      <>
        <DrawerHeading title="Execution intent details" id={i.intent_id} />
        <DRow label="Instrument" value={i.symbol} testId="drawer-intent-symbol" />
        <DRow label="Side" value={label(SIDE_LABELS, i.side)} testId="drawer-intent-side" />
        <DRow label="Quantity" value={fmtQuantity(i.quantity)} testId="drawer-intent-qty" />
        <DRow label="Status" value={label(INTENT_STATUS_LABELS, i.status)} testId="drawer-intent-status" />
        <DRow label="Order type" value={fmtScalar(i.order_type)} testId="drawer-intent-type" />
        <DRow label="Limit price" value={fmtMoney(i.limit_price, currency)} testId="drawer-intent-limit" />
        <DRow label="Portfolio id" value={i.portfolio_id} testId="drawer-intent-portfolio" />
        <DRow label="Created" value={fmtInstant(i.created_at)} testId="drawer-intent-created" />
        <DRow label="Updated" value={fmtInstant(i.updated_at)} testId="drawer-intent-updated" />
        <DRow
          label="Fill reports"
          value={`This panel retrieved ${row.fills}; per-fill details come from GET /execution/intents/{id}/fills.`}
          testId="drawer-intent-fills"
        />
      </>
    );
  }

  const r = row.record;
  const keys = columns.length > 0 ? columns : Object.keys(r);
  return (
    <>
      <DrawerHeading
        title={r.record_type === 'fill' ? 'Fill journal details' : 'Unfilled order journal details'}
        id={r.fill_id ?? r.order_id ?? row.id}
      />
      {keys.map((key) => {
        const raw = r[key];
        const missing = raw === null || raw === undefined || raw === '';
        const shown = missing
          ? key === 'bar_timestamp' || r.record_type === 'order' && /execution|price|amount|cost|slippage|commission|quantity|net_cash/.test(key)
            ? NOT_APPLICABLE
            : NOT_PROVIDED
          : typeof raw === 'number'
            ? /price|amount|change/.test(key)
              ? fmtSignedMoney(raw, r.currency)
              : String(raw)
            : String(raw);
        return (
          <DRow key={key} label={key} value={shown} testId={`drawer-journal-${key}`} note={semantics[key]} />
        );
      })}
      <p className="ws-small" data-testid="drawer-journal-semantics">
        Column meanings come from the backend journal?format=json column_semantics; a field shown as
        “Not provided” means the backend returned null, not zero.
      </p>
    </>
  );
}

function DrawerHeading({ title, id }: { title: string; id: string }) {
  return (
    <div className="ws-evidence-drawer-head">
      <div className="ws-symbol-label" data-testid="evidence-drawer-title">
        {title}
      </div>
      <div className="ws-small">
        <span className="ctx-label">Record id</span>{' '}
        <span className="numeric" data-testid="evidence-drawer-record-id">
          {id}
        </span>
      </div>
    </div>
  );
}

function DRow({ label: text, value, testId, note }: { label: string; value: string; testId: string; note?: string }) {
  return (
    <div className="ws-evidence-drawer-row" title={note}>
      <span className="ctx-label">{text}</span>
      <span className="ctx-value" data-testid={testId}>
        {value}
      </span>
    </div>
  );
}
