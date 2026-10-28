'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';

interface RunDetail {
  id: string;
  experiment_version_id: string;
  dataset_snapshot_id?: string | null;
  dataset_checksum?: string | null;
  engine_version: string;
  config_hash: string;
  status: string;
  metrics?: Record<string, unknown> | null;
  warnings?: string[] | null;
  methodology_json?: Record<string, unknown> | null;
  result_json?: Record<string, unknown> | null;
  error_details?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

interface RunSummary {
  id: string;
}

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error'; heading: string; detail: string; backTo: 'experiment' | 'dashboard' }
  | { phase: 'ready'; run: RunDetail };

const METRIC_KEYS: [string, string][] = [
  ['total_return_pct', '總報酬 (%)'],
  ['net_pnl', '淨盈虧'],
  ['max_drawdown_pct', '最大回撤 (%)'],
  ['total_trades', '成交筆數'],
  ['closed_trade_count', '已平倉交易數'],
  ['win_rate_pct', '勝率 (%)'],
  ['profit_factor', '盈虧比（毛利／毛虧）'],
  ['average_realized_r', '平均實現 R'],
  ['sharpe_ratio', 'Sharpe 比率'],
  ['annualized_return_pct', '年化報酬 (%)'],
];

interface UnavailableMetric {
  value?: unknown;
  reason?: string;
  available?: boolean;
  consistent?: boolean | null;
}

interface CrossCheck extends UnavailableMetric {
  equity_net_pnl?: number;
  realized_net_pnl?: number;
  difference?: number;
}

interface WeekdayRow {
  weekday?: string;
  sessions?: number;
  closed_trades?: number;
  wins?: number;
  losses?: number;
  realized_pnl?: number;
  mean_realized_pnl?: number | null;
  win_rate_pct?: number | null;
}

interface WeekdayBreakdown extends UnavailableMetric {
  rows?: WeekdayRow[];
  date_semantics?: string;
  attribution_rule?: string;
  unattributed_trades?: number;
  caveat?: string;
}

const PANEL_METRIC_KEYS = ['pnl_cross_check', 'day_of_week_breakdown', 'time_of_day_breakdown', 'round_trip_summary'];

const EXIT_POLICY_LABELS: Record<string, string> = {
  source: '政策來源',
  stop: '停損',
  target: '停利',
  same_bar_stop_and_target: '同一日同時觸及',
  level_evaluation_start: '檢查起點',
  time_exit: '持有期間出場',
  end_of_data: '資料結束',
  undeclared_plan: '未宣告風險計劃',
};

// Labels for methodology_json.journal_field_semantics: the per-trade definitions the
// engine stores. Rendering them keeps the R / planned-risk semantics (including that
// realised R is never floored at -1R) visible to the user instead of payload-only.
const JOURNAL_FIELD_LABELS: Record<string, string> = {
  actor: '來源身分',
  strategy_config_hash: '策略設定雜湊',
  stop_price: '停損價',
  target_price: '停利價',
  planned_risk: '計劃風險',
  planned_r: '計劃 R',
  realized_pnl: '實現盈虧',
  realized_r: '實現 R',
  exit_reason: '出場原因',
};

const EXIT_REASON_LABELS: Record<string, string> = {
  stop: '停損觸發',
  target: '停利觸發',
  time: '持有期滿',
  signal: '策略訊號',
  end_of_data: '資料結束強制平倉',
};

interface JournalRow {
  execution_date?: string;
  trade_date?: string;
  side?: string;
  symbol?: string;
  quantity?: number;
  execution_price_before_costs?: number;
  commission_amount?: number;
  slippage_amount?: number;
  realized_pnl?: number | null;
  realized_r?: number | null;
  stop_price?: number | null;
  target_price?: number | null;
  planned_risk?: number | null;
  planned_r?: number | null;
  actor?: string;
  strategy_config_hash?: string | null;
  rationale?: string;
  exit_reason?: string | null;
}

interface RoundTripRow {
  entry_date?: string | null;
  exit_date?: string;
  symbol?: string;
  direction?: string;
  shares?: number;
  cost_per_share?: number | null;
  proceeds_per_share?: number;
  realized_pnl?: number | null;
  planned_risk?: number | null;
  realized_r?: number | null;
  note?: string;
}

const PROVENANCE_KEYS: [string, string][] = [
  ['data_provider', '資料來源'],
  ['dataset_checksum', '資料集 checksum'],
  ['start_date', '期間開始'],
  ['end_date', '期間結束'],
];

const fmt = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number.isFinite(v) ? v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : String(v);
  if (typeof v === 'object') return '—';
  return String(v);
};

function isMetricStruct(v: unknown): v is UnavailableMetric {
  return typeof v === 'object' && v !== null && ('value' in v || 'available' in v);
}

function fmtMetric(v: unknown): { text: string; reason?: string } {
  if (isMetricStruct(v)) {
    const reason = typeof v.reason === 'string' ? v.reason : undefined;
    if (v.available === false) return { text: '無數據', reason };
    if (v.value === null || v.value === undefined) return { text: '未計算', reason };
    return { text: fmt(v.value), reason };
  }
  return { text: fmt(v) };
}

function describeError(e: unknown, fallbackHeading: string): { heading: string; detail: string } {
  if (e instanceof ApiError) {
    if (e.status === 404) return { heading: '搵唔到對應記錄', detail: e.message };
    if (e.status === 403) return { heading: '冇權限檢視此記錄', detail: e.message };
    if (e.status === 401) return { heading: '登入狀態已失效', detail: e.message };
    return { heading: fallbackHeading, detail: `${e.status} — ${e.message}` };
  }
  return { heading: fallbackHeading, detail: e instanceof Error ? e.message : '未知錯誤' };
}

export default function RunDetailPage() {
  const params = useParams<{ id: string; runId: string }>();
  const experimentId = params?.id ?? '';
  const runId = params?.runId ?? '';
  const [state, setState] = useState<LoadState>({ phase: 'loading' });

  useEffect(() => {
    if (!runId || !experimentId) {
      setState({ phase: 'error', heading: '網址唔完整', detail: '缺少實驗 ID 或 run ID。', backTo: 'dashboard' });
      return;
    }
    let cancelled = false;
    (async () => {
      const [runRes, listRes] = await Promise.allSettled([
        api.runs.get(runId),
        api.runs.list(experimentId),
      ]);
      if (cancelled) return;

      if (runRes.status === 'rejected') {
        const { heading, detail } = describeError(runRes.reason, '載入執行記錄失敗');
        setState({ phase: 'error', heading, detail, backTo: 'dashboard' });
        return;
      }
      if (listRes.status === 'rejected') {
        const { heading, detail } = describeError(listRes.reason, '無法確認此 run 所屬嘅實驗');
        setState({ phase: 'error', heading, detail, backTo: 'dashboard' });
        return;
      }

      const run = runRes.value as RunDetail;
      const ownedRuns = (listRes.value as RunSummary[]) ?? [];
      if (!ownedRuns.some((r) => r.id === run.id)) {
        setState({
          phase: 'error',
          heading: '呢個 run 唔屬於網址所指嘅實驗',
          detail: '為避免展示唔對應嘅結果，本頁已停止渲染該 run 嘅指標。',
          backTo: 'dashboard',
        });
        return;
      }
      setState({ phase: 'ready', run });
    })();
    return () => {
      cancelled = true;
    };
  }, [experimentId, runId]);

  if (state.phase === 'loading') {
    return (
      <div className="dash-root" style={{ padding: '32px' }}>
        <p style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>載入中…</p>
      </div>
    );
  }

  if (state.phase === 'error') {
    const href = state.backTo === 'experiment' ? `/experiments/${experimentId}` : '/dashboard';
    const label = state.backTo === 'experiment' ? '← 返回實驗' : '← 返回控制台';
    return (
      <div className="dash-root" style={{ padding: '32px' }}>
        <Link href={href} style={{ color: 'var(--accent-blue)', textDecoration: 'none', fontSize: '13px' }}>
          {label}
        </Link>
        <div className="dash-disclaimer" data-testid="run-detail-error" style={{ marginTop: '16px' }}>
          <strong>{state.heading}：</strong>
          {state.detail}
        </div>
        <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
          Run ID：{runId || '—'}
        </div>
      </div>
    );
  }

  const run = state.run;
  const recorded = run.metrics ?? {};
  const resultMeta = (run.result_json ?? {}) as Record<string, unknown>;
  const isStruct = (v: unknown): v is Record<string, unknown> =>
    typeof v === 'object' && v !== null && !Array.isArray(v);
  const flatExtraCount = Object.keys(recorded).filter(
    (k) => !METRIC_KEYS.some(([m]) => m === k) && !PANEL_METRIC_KEYS.includes(k)
  ).length;
  const crossCheck = isStruct(recorded.pnl_cross_check) ? (recorded.pnl_cross_check as CrossCheck) : null;
  const weekday = isStruct(recorded.day_of_week_breakdown) ? (recorded.day_of_week_breakdown as WeekdayBreakdown) : null;
  const timeOfDay = isStruct(recorded.time_of_day_breakdown) ? (recorded.time_of_day_breakdown as UnavailableMetric) : null;
  const fills = Array.isArray(resultMeta.transactions) ? (resultMeta.transactions as JournalRow[]) : [];
  const roundTrips = Array.isArray(resultMeta.round_trips) ? (resultMeta.round_trips as RoundTripRow[]) : [];
  const methodology = isStruct(run.methodology_json) ? run.methodology_json : null;
  const convention = methodology && isStruct(methodology.execution_convention) ? methodology.execution_convention : null;
  const exitPolicy = methodology && isStruct(methodology.exit_policy) ? methodology.exit_policy : null;
  const journalFields = methodology && isStruct(methodology.journal_field_semantics) ? methodology.journal_field_semantics : null;
  const strategyRules = methodology && Array.isArray(methodology.strategy_rules) ? methodology.strategy_rules : null;
  const constraints = methodology && Array.isArray(methodology.constraints) ? methodology.constraints : null;

  return (
    <div className="dash-root" data-testid="run-detail-page" style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <Link
          href={`/experiments/${experimentId}`}
          data-testid="run-detail-back"
          style={{ color: 'var(--accent-blue)', textDecoration: 'none', fontSize: '13px' }}
        >
          ← 返回實驗
        </Link>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>狀態：{run.status}</span>
      </div>

      <h1 style={{ margin: '0 0 8px', fontSize: '24px', color: 'var(--text-white)' }}>執行結果</h1>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>執行身分</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            <div>Run ID</div><div style={{ fontFamily: 'monospace' }}>{run.id}</div>
            <div>實驗 ID</div><div style={{ fontFamily: 'monospace' }}>{experimentId}</div>
            <div>Version ID</div><div style={{ fontFamily: 'monospace' }}>{run.experiment_version_id}</div>
            <div>Config hash</div><div style={{ fontFamily: 'monospace' }}>{run.config_hash}</div>
            <div>Engine version</div><div style={{ fontFamily: 'monospace' }}>{run.engine_version}</div>
            <div>開始時間</div><div>{run.started_at ? new Date(run.started_at).toLocaleString('zh-Hant') : '—'}</div>
            <div>完成時間</div><div>{run.completed_at ? new Date(run.completed_at).toLocaleString('zh-Hant') : '—'}</div>
          </div>
        </div>
      </div>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>已記錄指標</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            {METRIC_KEYS.map(([k, label]) => {
              const { text, reason } = fmtMetric(recorded[k]);
              return (
                <div key={k} style={{ display: 'contents' }}>
                  <div>{label}</div>
                  <div>
                    <span
                      style={{ fontWeight: 600, color: reason ? 'var(--text-muted)' : 'var(--text-white)' }}
                      title={reason}
                      data-testid={`metric-${k}`}
                    >
                      {text}
                    </span>
                    {reason && (
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>{reason}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {flatExtraCount > 0 && (
            <>
              <h4 style={{ margin: '16px 0 8px', fontSize: '13px', color: 'var(--text-muted)' }}>其他指標</h4>
              <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: '6px 16px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                {Object.entries(recorded)
                  .filter(([k]) => !METRIC_KEYS.some(([m]) => m === k) && !PANEL_METRIC_KEYS.includes(k))
                  .map(([k, v]) => {
                    const { text, reason } = fmtMetric(v);
                    return (
                      <div key={k} style={{ display: 'contents' }}>
                        <div style={{ fontFamily: 'monospace' }}>{k}</div>
                        <div title={reason}>{text}{reason ? ` — ${reason}` : ''}</div>
                      </div>
                    );
                  })}
              </div>
            </>
          )}
        </div>
      </div>

      {crossCheck && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>盈虧交叉核對</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              <div>權益法淨盈虧</div>
              <div>{fmt(crossCheck.equity_net_pnl as number)}</div>
              <div>FIFO 實現淨盈虧</div>
              <div>{fmt(crossCheck.realized_net_pnl as number)}</div>
              <div>差異</div>
              <div>{fmt(crossCheck.difference as number)}</div>
              <div>結果</div>
              <div data-testid="pnl-cross-check-result" style={{ fontWeight: 600 }}>
                {crossCheck.consistent === true ? '兩邊吻合'
                  : crossCheck.consistent === false ? '唔吻合 — 指標未對帳'
                  : '無法比較'}
              </div>
            </div>
            {typeof crossCheck.reason === 'string' && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>{crossCheck.reason}</div>
            )}
          </div>
        </div>
      )}

      {weekday && Array.isArray(weekday.rows) && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>按交易日分佈</h3>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px' }}>
              日期語義：{fmt(weekday.date_semantics)} · 歸屬基礎：{fmt(weekday.attribution_rule)}
            </div>
            <table data-testid="weekday-breakdown" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                  <th style={{ padding: '4px 8px' }}>日</th>
                  <th style={{ padding: '4px 8px' }}>交易日數</th>
                  <th style={{ padding: '4px 8px' }}>已平倉</th>
                  <th style={{ padding: '4px 8px' }}>勝/負</th>
                  <th style={{ padding: '4px 8px' }}>實現盈虧</th>
                  <th style={{ padding: '4px 8px' }}>平均</th>
                </tr>
              </thead>
              <tbody>
                {weekday.rows.map((r, i) => (
                  <tr key={`${r.weekday}-${i}`} style={{ borderTop: '1px solid var(--border-color)' }}>
                    <td style={{ padding: '4px 8px' }}>{fmt(r.weekday)}</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(r.sessions)}</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(r.closed_trades)}</td>
                    <td style={{ padding: '4px 8px' }}>{`${fmt(r.wins)}/${fmt(r.losses)}`}</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(r.realized_pnl)}</td>
                    <td style={{ padding: '4px 8px' }}>{fmt(r.mean_realized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(weekday.unattributed_trades as number) > 0 && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
                {fmt(weekday.unattributed_trades)} 筆平倉無法拉回原買入成本，已排除喺分佈之外。
              </div>
            )}
            {typeof weekday.caveat === 'string' && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>{weekday.caveat}</div>
            )}
          </div>
        </div>
      )}

      {timeOfDay && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>按日內時段分佈</h3>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)' }} data-testid="time-of-day-unavailable">
              {fmtMetric(timeOfDay).text}：{fmt(timeOfDay.reason)}
            </div>
          </div>
        </div>
      )}

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>成交日記（系統記錄）</h3>
          {fills.length === 0 ? (
            <div style={{ fontSize: '13px', color: 'var(--text-muted)' }} data-testid="journal-empty">
              呢個 run 冇成交記錄。
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table data-testid="journal-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                    {['日期', '方向', '數量', '成交價', '手續費', '止損', '目標', '計劃風險', '實現盈虧', '實現 R', '出場原因', '來源', 'Config'].map((h) => (
                      <th key={h} style={{ padding: '4px 8px', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {fills.map((t, i) => (
                    <tr key={`${t.execution_date}-${i}`} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td style={{ padding: '4px 8px', whiteSpace: 'nowrap' }}>{fmt(t.execution_date ?? t.trade_date)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.side)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.quantity)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.execution_price_before_costs)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.commission_amount)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.stop_price)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.target_price)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.planned_risk)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.realized_pnl)}</td>
                      <td style={{ padding: '4px 8px' }}>{fmt(t.realized_r)}</td>
                      <td style={{ padding: '4px 8px', whiteSpace: 'nowrap' }} data-testid={`journal-exit-reason-${i}`}
                          title={t.exit_reason ? String(t.exit_reason) : undefined}>
                        {t.exit_reason ? (EXIT_REASON_LABELS[t.exit_reason] ?? t.exit_reason) : '—'}
                      </td>
                      <td style={{ padding: '4px 8px' }} data-testid={`journal-actor-${i}`}>{fmt(t.actor)}</td>
                      <td style={{ padding: '4px 8px', fontFamily: 'monospace' }}>{t.strategy_config_hash ? t.strategy_config_hash.slice(0, 8) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {roundTrips.length > 0 && (
            <>
              <h4 style={{ margin: '16px 0 8px', fontSize: '13px', color: 'var(--text-muted)' }}>FIFO 平倉配對</h4>
              <div style={{ overflowX: 'auto' }}>
                <table data-testid="round-trip-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                      {['買入日', '賣出日', '股數', '成本/股', '收入/股', '實現盈虧', '計劃風險', '實現 R'].map((h) => (
                        <th key={h} style={{ padding: '4px 8px', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {roundTrips.map((rt, i) => (
                      <tr key={`${rt.exit_date}-${i}`} style={{ borderTop: '1px solid var(--border-color)' }}
                          title={rt.note}>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.entry_date)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.exit_date)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.shares)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.cost_per_share)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.proceeds_per_share)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.realized_pnl)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.planned_risk)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(rt.realized_r)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <div style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-muted)' }}>
            止損、目標、計劃風險屬策略規範聲明嘅欄位；模板冇聲明止損時顯示「—」，而唔係當作零風險交易。
            「來源」記錄成交由邊個層面產生（引擎成交為 strategy；human 同 api_key 出現喺紙上交易訂單）。
          </div>
        </div>
      </div>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>資料來源與快照</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            {PROVENANCE_KEYS.map(([k, label]) => (
              <div key={k} style={{ display: 'contents' }}>
                <div>{label}</div>
                <div style={{ fontFamily: 'monospace' }}>{fmt(resultMeta[k])}</div>
              </div>
            ))}
            <div>Dataset snapshot</div>
            <div style={{ fontFamily: 'monospace' }}>{fmt(run.dataset_snapshot_id)}</div>
            <div>Dataset checksum (欄位)</div>
            <div style={{ fontFamily: 'monospace' }}>{fmt(run.dataset_checksum)}</div>
          </div>
        </div>
      </div>

      <div className="dash-panel" data-testid="methodology-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>方法與限制</h3>
          {methodology ? (
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              {convention && (
                <div style={{ marginBottom: '12px' }}>
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>成交假設</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '4px 16px' }}>
                    {Object.entries(convention).map(([k, v]) => (
                      <div key={k} style={{ display: 'contents' }}>
                        <div style={{ fontFamily: 'monospace' }}>{k}</div>
                        <div style={{ fontFamily: 'monospace' }}>{String(v)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {exitPolicy && (
                <div style={{ marginBottom: '12px' }} data-testid="exit-policy-block">
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>出場政策（停損／停利）</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: '4px 16px' }}>
                    {Object.entries(exitPolicy).map(([k, v]) => (
                      <div key={k} style={{ display: 'contents' }}>
                        <div style={{ fontWeight: 500 }}>{EXIT_POLICY_LABELS[k] ?? k}</div>
                        <div>{String(v)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {journalFields && (
                <div style={{ marginBottom: '12px' }} data-testid="journal-field-semantics-block">
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>交易日誌欄位定義</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: '4px 16px' }}>
                    {Object.entries(journalFields).map(([k, v]) => (
                      <div key={k} style={{ display: 'contents' }}>
                        <div style={{ fontWeight: 500 }}>{JOURNAL_FIELD_LABELS[k] ?? k}</div>
                        <div>{String(v)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {Array.isArray(strategyRules) && strategyRules.length > 0 && (
                <div style={{ marginBottom: '12px' }}>
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>策略規則</div>
                  <ul style={{ margin: 0, paddingLeft: '20px' }}>
                    {strategyRules.map((r, i) => <li key={i}>{String(r)}</li>)}
                  </ul>
                </div>
              )}
              <div>
                <div style={{ fontWeight: 600, marginBottom: '4px' }}>已知限制</div>
                {Array.isArray(constraints) && constraints.length > 0 ? (
                  <ul style={{ margin: 0, paddingLeft: '20px' }}>
                    {constraints.map((c, i) => <li key={i}>{String(c)}</li>)}
                  </ul>
                ) : (
                  <div>此 run 未記錄限制清單。</div>
                )}
              </div>
            </div>
          ) : (
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              此 run 未記錄方法摘要（早於此欄位存在），因此無法在此顯示其成交假設與限制。
            </div>
          )}
        </div>
      </div>

      {run.error_details && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>錯誤詳情</h3>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
              {fmt(run.error_details)}
            </div>
          </div>
        </div>
      )}

      {Array.isArray(run.warnings) && run.warnings.length > 0 && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>警告</h3>
            <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              {run.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        </div>
      )}

      <div className="dash-disclaimer" style={{ marginTop: '24px', fontSize: '12px' }}>
        提醒：以上內容來自已記錄嘅 run，並已核實該 run 屬於此實驗嘅執行歷史。部分欄位可能缺失。本系統係研究工具，結果唔構成投資建議。
      </div>
    </div>
  );
}
