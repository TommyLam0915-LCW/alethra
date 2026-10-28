'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
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

/** Tabs whose rows carry an instrument, and so can be scoped to 目前標的. */
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
      setExportError(e instanceof Error ? e.message : '匯出失敗');
    } finally {
      setExporting(false);
    }
  }, [data.portfolio]);

  const datasetError = (key: DatasetKey, name: string) =>
    data.errors[key] ? (
      <div className="ws-message-error" role="alert" data-testid={`evidence-error-${key}`}>
        {name}無法載入：{data.errors[key]}
      </div>
    ) : null;

  const scopeBar = SCOPABLE.includes(active) && (
    <div className="ws-evidence-scope" data-testid="evidence-scope">
      <span className="ctx-label">標的範圍</span>
      <button
        type="button"
        className={`ws-tab ${scope === 'all' ? 'ws-tab-active' : ''}`}
        aria-pressed={scope === 'all'}
        onClick={() => setScope('all')}
        data-testid="evidence-scope-all"
      >
        全部標的
      </button>
      <button
        type="button"
        className={`ws-tab ${scope === 'current' ? 'ws-tab-active' : ''}`}
        aria-pressed={scope === 'current'}
        onClick={() => setScope('current')}
        data-testid="evidence-scope-current"
      >
        目前標的（{symbol}）
      </button>
      {scope === 'current' && (
        <button
          type="button"
          className="ws-btn ws-btn-neutral"
          onClick={() => setScope('all')}
          data-testid="evidence-scope-clear"
        >
          清除篩選
        </button>
      )}
      <span className="ws-small" data-testid="evidence-scope-note">
        客戶端篩選，只作用於本面板已載入的紀錄，不向後端查詢。
      </span>
    </div>
  );

  const noPortfolio = data.portfolio === null;

  const emptyForScope = (kind: string, total: number) => (
    <div className="ws-evidence-empty" data-testid={`evidence-empty-${kind}`}>
      <p className="ws-muted">
        目前標的（{symbol}）在本面板已載入的 {total} 筆{kind}之中沒有紀錄。
      </p>
      <button
        type="button"
        className="ws-btn ws-btn-neutral"
        onClick={() => setScope('all')}
        data-testid={`evidence-empty-clear-${kind}`}
      >
        改看全部標的
      </button>
    </div>
  );

  const positionsBody = noPortfolio ? (
    <NoPortfolio resolution={data.resolution} />
  ) : (
    <>
      {datasetError('positions', '持倉')}
      {scopedPositions.length === 0 ? (
        scope === 'current' && data.positions.length > 0 ? (
          emptyForScope('持倉', data.positions.length)
        ) : (
          <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-positions">
            此組合目前沒有持倉紀錄。
          </p>
        )
      ) : (
        <table className="ws-table" data-testid="evidence-positions-table">
          <thead>
            <tr>
              <th>標的</th>
              <th>數量</th>
              <th>平均成本</th>
              <th>已實現損益</th>
              <th>未實現損益</th>
              <th>狀態</th>
              <th>開倉時間</th>
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
      {datasetError('orders', '模擬訂單')}
      <div className="ws-evidence-section">模擬訂單（paper orders）</div>
      {scopedOrders.length === 0 ? (
        scope === 'current' && data.orders.length > 0 ? (
          emptyForScope('訂單', data.orders.length)
        ) : (
          <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-orders">
            此組合目前沒有模擬訂單紀錄。
          </p>
        )
      ) : (
        <table className="ws-table" data-testid="evidence-orders-table">
          <thead>
            <tr>
              <th>標的</th>
              <th>來源</th>
              <th>方向</th>
              <th>數量</th>
              <th>訂單狀態</th>
              <th>下單時間（UTC）</th>
              <th>成交／回放時間</th>
              <th>淨現金變動</th>
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

      <div className="ws-evidence-section">模擬成交（fills）</div>
      {datasetError('journal', '成交紀錄')}
      {scopedJournal.filter((r) => r.record_type === 'fill').length === 0 ? (
        <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-fills">
          {data.journal.length === 0
            ? '此組合沒有可顯示的成交紀錄（日誌端點回傳 0 列）。'
            : '目前範圍內沒有已成交紀錄。'}
        </p>
      ) : (
        <table className="ws-table" data-testid="evidence-fills-table">
          <thead>
            <tr>
              <th>標的</th>
              <th>方向</th>
              <th>數量</th>
              <th>成交價（成本前）</th>
              <th>滑點</th>
              <th>手續費</th>
              <th>淨現金變動</th>
              <th>成交日</th>
              <th>K 線時間</th>
              <th>成交原因</th>
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

      <div className="ws-evidence-section">執行意向（execution intents）</div>
      {execution.error && (
        <div className="ws-message-error" role="alert" data-testid="evidence-error-intents">
          執行意向無法載入：{execution.error}
        </div>
      )}
      {execution.loading && scopedIntents.length === 0 ? (
        <p className="ws-muted" style={{ padding: 10 }}>載入中…</p>
      ) : scopedIntents.length === 0 ? (
        <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-intents">
          {execution.intents.length === 0 ? '沒有執行意向。' : '目前範圍內沒有執行意向。'}
        </p>
      ) : (
        <table className="ws-table" data-testid="evidence-intents-table">
          <thead>
            <tr>
              <th>標的</th>
              <th>方向</th>
              <th>數量</th>
              <th>狀態</th>
              <th>成交回報筆數</th>
              <th>建立時間（UTC）</th>
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
      {datasetError('journal', '自動交易日誌')}
      <div className="ws-evidence-toolbar">
        <button
          type="button"
          className="ws-btn ws-btn-accent"
          onClick={() => void downloadCsv()}
          disabled={exporting}
          data-testid="evidence-export-csv"
        >
          {exporting ? '匯出中…' : '匯出整個組合（CSV）'}
        </button>
        <span className="ws-small" data-testid="evidence-export-scope-note">
          匯出範圍：整個組合
          {data.portfolio ? `（${data.portfolio.id.slice(0, 8)}…）` : ''}的全部訂單與成交，
          不受上方「標的範圍」篩選影響。
        </span>
        {exportError && (
          <div className="ws-message-error" role="alert" data-testid="evidence-export-error">
            {exportError}
          </div>
        )}
      </div>
      {scopedJournal.length === 0 ? (
        scope === 'current' && data.journal.length > 0 ? (
          emptyForScope('日誌紀錄', data.journal.length)
        ) : (
          <p className="ws-muted" style={{ padding: 10 }} data-testid="evidence-no-journal">
            此組合尚未產生日誌紀錄。後端對每筆成交各列一列，未成交的訂單也各保留一列。
          </p>
        )
      ) : (
        <table className="ws-table" data-testid="evidence-journal-table">
          <thead>
            <tr>
              <th>紀錄性質</th>
              <th>標的</th>
              <th>來源</th>
              <th>方向</th>
              <th>訂單狀態</th>
              <th>決策</th>
              <th>回放截止</th>
              <th>成交日</th>
              <th>K 線時間</th>
              <th>下單時間（UTC）</th>
              <th>淨現金變動</th>
              <th>成交原因</th>
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
    <section className="ws-panel ws-panel-fill ws-evidence-dock" data-testid="evidence-dock" aria-label="證據面板">
      {!hideHeader && <div className="ws-panel-header">證據面板</div>}
      <div
        className="ws-tabs"
        role="tablist"
        aria-label="證據面板分頁"
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
          title="重新載入證據"
          aria-label="重新載入證據"
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
              證據所屬組合{' '}
              <span className="numeric" data-testid="evidence-portfolio-id">
                {data.portfolio.id}
              </span>
              （{label(PORTFOLIO_STATUS_LABELS, data.portfolio.status)}）：{data.resolution.reason}
            </>
          ) : (
            <>組合解析：{data.resolution.reason}</>
          )}
        </p>
        {data.loading && (
          <p className="ws-muted" style={{ padding: 8 }} data-testid="evidence-loading">
            載入中…
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
  position: '持倉明細',
  order: '模擬訂單明細',
  journal: '日誌紀錄明細',
  intent: '執行意向明細',
};

function NoPortfolio({ resolution }: { resolution: EvidenceData['resolution'] }) {
  return (
    <div style={{ padding: 12 }}>
      <div className="ws-symbol-label">沒有可顯示的證據</div>
      <p className="ws-muted" data-testid="evidence-no-portfolio">
        {resolution.reason}
      </p>
      <Link
        href="/paper-portfolio"
        className="ws-btn ws-btn-neutral"
        style={{ textDecoration: 'none', display: 'inline-block' }}
      >
        前往投資組合
      </Link>
    </div>
  );
}

/**
 * The 結果 tab lists only what the portfolio endpoints actually compute. There is
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
          <ResRow label="初始虛擬資金" value={fmtMoney(p.initial_virtual_cash, p.currency)} testId="results-initial-cash" />
          <ResRow label="目前現金" value={fmtMoney(p.current_virtual_cash, p.currency)} testId="results-cash" />
          <ResRow
            label="現金變動（不含未實現）"
            value={
              cashOnlyPct === null
                ? CANNOT_COMPUTE
                : `${cashOnlyPct >= 0 ? '+' : '−'}${Math.abs(cashOnlyPct).toFixed(2)}%`
            }
            testId="results-cash-return"
          />
          <ResRow label="已實現損益" value={fmtSignedMoney(p.total_realized_pnl, p.currency)} testId="results-realized-pnl" />
          <ResRow
            label="未實現損益"
            value={fmtUnrealized(p.total_unrealized_pnl, p.currency)}
            testId="results-unrealized-pnl"
          />
          <ResRow
            label="估值依據"
            value={
              valuation
                ? `${fmtScalar(valuation.provider)} · ${fmtScalar(valuation.mode_label_zh ?? valuation.mode)} · 資料截至 ${fmtScalar(valuation.data_as_of)}`
                : NOT_PROVIDED
            }
            testId="results-valuation-basis"
          />
          {valuation?.unpriced_assets && valuation.unpriced_assets.length > 0 && (
            <ResRow label="無法估值的資產" value={valuation.unpriced_assets.join('、')} testId="results-unpriced-assets" />
          )}
          <ResRow label="持有中部位" value={String(openCount)} testId="results-open-positions" />
          <ResRow label="模擬訂單數" value={String(data.orders.length)} testId="results-order-count" />
          <ResRow label="成交筆數" value={String(fillCount)} testId="results-fill-count" />
          <ResRow label="決策紀錄數" value={String(data.decisions.length)} testId="results-decision-count" />
        </tbody>
      </table>

      <div className="ws-evidence-gap" data-testid="evidence-results-gap" role="note">
        <div className="ws-symbol-label">此面板不提供的項目</div>
        <p className="ws-muted">
          後端沒有組合層級的成果端點：沒有淨值曲線、沒有期間報酬率、沒有 Sharpe、最大回撤或勝率可查詢，
          因此以上只列出 paper-portfolio 明細與 positions、orders、journal、decisions 其實回傳的欄位。
          「現金變動」由初始與目前現金兩欄在瀏覽器相除得出，只計現金、未含未實現損益，不是總報酬率。
          實驗回測指標屬於策略版本與其 run，不是這個組合的實際績效，因此不在此顯示。
        </p>
        <Link href={`/paper-portfolio/${p.id}`} data-testid="evidence-results-link">
          組合完整頁面
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
      部分資料未载入：{failed.map(([key, message]) => `${key}（${message}）`).join('、')}
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
                沒有實驗。
              </p>
            ) : (
              <table className="ws-table" data-testid="evidence-research-table">
                <thead>
                  <tr>
                    <th>標題</th>
                    <th>狀態</th>
                    <th>更新時間</th>
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
                沒有模型。
              </p>
            ) : (
              <table className="ws-table" data-testid="evidence-models-table">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>類型</th>
                    <th>建立時間</th>
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
              沒有系統活動紀錄。
            </p>
          ) : (
            <table className="ws-table" data-testid="evidence-log-table">
              <thead>
                <tr>
                  <th>時間</th>
                  <th>事件</th>
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
      setState({ loading: false, error: e instanceof Error ? e.message : '載入失敗', body: null });
    }
  }, [kind]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.loading) {
    return (
      <p className="ws-muted" style={{ padding: 10 }} data-testid={`evidence-registry-loading-${kind}`}>
        載入中…
      </p>
    );
  }
  if (state.error) {
    return (
      <div className="ws-message-error" role="alert" style={{ margin: 10 }} data-testid={`evidence-registry-error-${kind}`}>
        載入失敗：{state.error}
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
        <DrawerHeading title="持倉明細" id={p.id} />
        <DRow label="標的" value={p.asset_id} testId="drawer-position-asset" />
        <DRow label="組合 id" value={p.portfolio_id} testId="drawer-position-portfolio" />
        <DRow label="數量" value={fmtQuantity(p.quantity)} testId="drawer-position-qty" />
        <DRow label="平均成本" value={fmtMoney(p.average_entry_price, currency)} testId="drawer-position-cost" />
        <DRow label="已實現損益" value={fmtSignedMoney(p.realized_pnl, currency)} testId="drawer-position-realized" />
        <DRow label="未實現損益" value={fmtUnrealized(p.unrealized_pnl, currency)} testId="drawer-position-unrealized" />
        <DRow label="狀態" value={label(POSITION_STATUS_LABELS, p.status)} testId="drawer-position-status" />
        <DRow label="開倉時間" value={fmtInstant(p.opened_at)} testId="drawer-position-opened" />
        <DRow
          label="平倉時間"
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
        <DrawerHeading title="模擬訂單明細" id={o.id} />
        <DRow label="標的" value={o.asset_id} testId="drawer-order-asset" />
        <DRow label="來源（actor_type）" value={label(ACTOR_LABELS, o.actor_type)} testId="drawer-order-actor" />
        <DRow label="方向" value={label(SIDE_LABELS, o.side)} testId="drawer-order-side" />
        <DRow label="數量" value={fmtQuantity(o.quantity)} testId="drawer-order-qty" />
        <DRow label="訂單類型" value={fmtScalar(o.order_type)} testId="drawer-order-type" />
        <DRow label="訂單狀態" value={label(ORDER_STATUS_LABELS, o.status)} testId="drawer-order-status" />
        <DRow label="風控檢查" value={fmtScalar(o.risk_check_status)} testId="drawer-order-risk" />
        <DRow
          label="下單時間（created_at）"
          value={fmtInstant(o.created_at)}
          testId="drawer-order-created"
        />
        <DRow label="定價／回放截止" value={fmtScalar(o.data_cutoff_at)} testId="drawer-order-cutoff" />
        <DRow
          label="策略或模型版本"
          value={o.model_or_strategy_version_id ?? NOT_PROVIDED}
          testId="drawer-order-version"
        />
        <DRow label="決策 id" value={o.decision_id ?? NOT_PROVIDED} testId="drawer-order-decision" />
        <DRow label="冪等鍵" value={o.idempotency_key ?? NOT_PROVIDED} testId="drawer-order-idempotency" />
        <DRow label="理由" value={o.rationale ?? NOT_PROVIDED} testId="drawer-order-rationale" />
        <div className="ws-evidence-section">成交（{row.fills.length} 筆）</div>
        {row.fills.length === 0 ? (
          <p className="ws-muted" data-testid="drawer-order-no-fills">
            此訂單在已載入的日誌中沒有成交列。
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
                成交日 {f.execution_date ?? NOT_PROVIDED}
                {f.bar_timestamp ? ` · K 線 ${f.bar_timestamp.replace('T', ' ')}` : ` · K 線時間${NOT_APPLICABLE}`}
              </div>
              <div className="ws-small">
                觀察價 {fmtMoney(f.observed_price, f.currency)} · 成交價 {fmtMoney(f.execution_price_before_costs, f.currency)}
              </div>
              <div className="ws-small">
                滑點 {fmtMoney(f.slippage_amount, f.currency)} · 手續費 {fmtMoney(f.commission_amount, f.currency)} · 淨現金{' '}
                {fmtSignedMoney(f.net_cash_change, f.currency)}
              </div>
              <div className="ws-small">成交原因 {fmtScalar(f.simulated_fill_reason)}</div>
              <div className="ws-small">出場／結果 {fmtScalar(f.outcome_status)}</div>
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
        <DrawerHeading title="執行意向明細" id={i.intent_id} />
        <DRow label="標的" value={i.symbol} testId="drawer-intent-symbol" />
        <DRow label="方向" value={label(SIDE_LABELS, i.side)} testId="drawer-intent-side" />
        <DRow label="數量" value={fmtQuantity(i.quantity)} testId="drawer-intent-qty" />
        <DRow label="狀態" value={label(INTENT_STATUS_LABELS, i.status)} testId="drawer-intent-status" />
        <DRow label="訂單類型" value={fmtScalar(i.order_type)} testId="drawer-intent-type" />
        <DRow label="限價" value={fmtMoney(i.limit_price, currency)} testId="drawer-intent-limit" />
        <DRow label="組合 id" value={i.portfolio_id} testId="drawer-intent-portfolio" />
        <DRow label="建立時間" value={fmtInstant(i.created_at)} testId="drawer-intent-created" />
        <DRow label="更新時間" value={fmtInstant(i.updated_at)} testId="drawer-intent-updated" />
        <DRow
          label="成交回報"
          value={`本面板已取回 ${row.fills} 筆；逐筆明細以 GET /execution/intents/{id}/fills 查詢。`}
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
        title={r.record_type === 'fill' ? '成交日誌明細' : '未成交訂單日誌明細'}
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
        欄位語意取自後端 journal?format=json 的 column_semantics；顯示為「未提供」的欄位代表後端回傳 null，
        而不是零。
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
        <span className="ctx-label">紀錄 id</span>{' '}
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
