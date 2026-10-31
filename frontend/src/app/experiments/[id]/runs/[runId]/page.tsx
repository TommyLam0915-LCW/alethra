'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { describeRunStatus } from '@/lib/runStatus';
import { formatDateTime, formatParamMap, formatRatePercent } from '@/lib/format';
import { ACTOR_LABELS, EXIT_REASON_LABELS, SIDE_LABELS, label } from '@/lib/copy';

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
  ['total_return_pct', 'Total return (%)'],
  ['net_pnl', 'Net P&L'],
  ['max_drawdown_pct', 'Max drawdown (%)'],
  ['total_trades', 'Trades'],
  ['closed_trade_count', 'Closed trades'],
  ['win_rate_pct', 'Win rate (%)'],
  ['profit_factor', 'Profit factor (gross profit / gross loss)'],
  ['average_realized_r', 'Average realized R'],
  ['sharpe_ratio', 'Sharpe ratio'],
  ['annualized_return_pct', 'Annualized return (%)'],
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
  source: 'Policy source',
  stop: 'Stop loss',
  target: 'Take profit',
  same_bar_stop_and_target: 'Stop and target hit in the same bar',
  level_evaluation_start: 'Level evaluation starts',
  time_exit: 'Time-based exit',
  end_of_data: 'End of data',
  undeclared_plan: 'No risk plan declared',
};

// Labels for methodology_json.journal_field_semantics: the per-trade definitions the
// engine stores. Rendering them keeps the R / planned-risk semantics (including that
// realised R is never floored at -1R) visible to the user instead of payload-only.
const JOURNAL_FIELD_LABELS: Record<string, string> = {
  actor: 'Source actor',
  strategy_config_hash: 'Strategy config hash',
  stop_price: 'Stop price',
  target_price: 'Target price',
  planned_risk: 'Planned risk',
  planned_r: 'Planned R',
  realized_pnl: 'Realized P&L',
  realized_r: 'Realized R',
  exit_reason: 'Exit reason',
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
  ['data_provider', 'Data source'],
  ['dataset_checksum', 'Dataset checksum'],
  ['start_date', 'Period start'],
  ['end_date', 'Period end'],
];

const fmt = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number.isFinite(v) ? v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : String(v);
  if (typeof v === 'object') return '—';
  return String(v);
};

// Nested run parameters are small flat maps (signal_parameters, risk_parameters);
// formatParamMap renders them as key=value so two runs can be compared.
const fmtRateWithRaw = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—';
  const percent = formatRatePercent(v as number | string);
  return `${percent} (${String(v)})`;
};

function isMetricStruct(v: unknown): v is UnavailableMetric {
  return typeof v === 'object' && v !== null && ('value' in v || 'available' in v);
}

function fmtMetric(v: unknown): { text: string; reason?: string } {
  if (isMetricStruct(v)) {
    const reason = typeof v.reason === 'string' ? v.reason : undefined;
    if (v.available === false) return { text: 'No data', reason };
    if (v.value === null || v.value === undefined) return { text: 'Not computed', reason };
    return { text: fmt(v.value), reason };
  }
  return { text: fmt(v) };
}

function describeError(e: unknown, fallbackHeading: string): { heading: string; detail: string } {
  if (e instanceof ApiError) {
    if (e.status === 404) return { heading: 'Record not found', detail: e.message };
    if (e.status === 403) return { heading: 'You do not have access to this record', detail: e.message };
    if (e.status === 401) return { heading: 'Your session has expired', detail: e.message };
    return { heading: fallbackHeading, detail: `${e.status} — ${e.message}` };
  }
  return { heading: fallbackHeading, detail: e instanceof Error ? e.message : 'Unknown error' };
}

export default function RunDetailPage() {
  const params = useParams<{ id: string; runId: string }>();
  const experimentId = params?.id ?? '';
  const runId = params?.runId ?? '';
  const [state, setState] = useState<LoadState>({ phase: 'loading' });

  useEffect(() => {
    if (!runId || !experimentId) {
      setState({ phase: 'error', heading: 'Incomplete address', detail: 'The experiment ID or run ID is missing.', backTo: 'dashboard' });
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
        const { heading, detail } = describeError(runRes.reason, 'The run record could not be loaded');
        setState({ phase: 'error', heading, detail, backTo: 'dashboard' });
        return;
      }
      if (listRes.status === 'rejected') {
        const { heading, detail } = describeError(listRes.reason, 'Could not confirm which experiment this run belongs to');
        setState({ phase: 'error', heading, detail, backTo: 'dashboard' });
        return;
      }

      const run = runRes.value as RunDetail;
      const ownedRuns = (listRes.value as RunSummary[]) ?? [];
      if (!ownedRuns.some((r) => r.id === run.id)) {
        setState({
          phase: 'error',
          heading: 'This run does not belong to the experiment in the address',
          detail: 'The run metrics are not rendered, so results from a different experiment cannot be shown here.',
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
        <p style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>Loading…</p>
      </div>
    );
  }

  if (state.phase === 'error') {
    const href = state.backTo === 'experiment' ? `/experiments/${experimentId}` : '/dashboard';
    const label = state.backTo === 'experiment' ? '← Back to experiment' : '← Back to dashboard';
    return (
      <div className="dash-root" style={{ padding: '32px' }}>
        <Link href={href} style={{ color: 'var(--accent-blue)', textDecoration: 'none', fontSize: '13px' }}>
          {label}
        </Link>
        <div className="dash-disclaimer" data-testid="run-detail-error" style={{ marginTop: '16px' }}>
          <strong>{state.heading}:</strong>
          {state.detail}
        </div>
        <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
          Run ID: {runId || '—'}
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
  const capturedParameters = methodology && isStruct(methodology.parameters) ? methodology.parameters : null;

  return (
    <div className="dash-root" data-testid="run-detail-page" style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <Link
          href={`/experiments/${experimentId}`}
          data-testid="run-detail-back"
          style={{ color: 'var(--accent-blue)', textDecoration: 'none', fontSize: '13px' }}
        >
          ← Back to experiment
        </Link>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }} data-testid="run-detail-status">
          Status: {describeRunStatus(run.status, run.started_at).label}
        </span>
      </div>
      {describeRunStatus(run.status, run.started_at).stale && (
        <p style={{ margin: '0 0 16px', fontSize: '12px', color: 'var(--text-muted)' }}>
          No completion record after 30 minutes and no completed_at. This is not a completion, and it is not still running.
        </p>
      )}

      <h1 style={{ margin: '0 0 8px', fontSize: '24px', color: 'var(--text-white)' }}>Run results</h1>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Run identity</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            <div>Run ID</div><div style={{ fontFamily: 'monospace' }}>{run.id}</div>
            <div>Experiment ID</div><div style={{ fontFamily: 'monospace' }}>{experimentId}</div>
            <div>Version ID</div><div style={{ fontFamily: 'monospace' }}>{run.experiment_version_id}</div>
            <div>Config hash</div><div style={{ fontFamily: 'monospace' }}>{run.config_hash}</div>
            <div>Engine version</div><div style={{ fontFamily: 'monospace' }}>{run.engine_version}</div>
            <div>Started</div><div>{run.started_at ? formatDateTime(run.started_at) : '—'}</div>
            <div>Completed</div><div>{run.completed_at ? formatDateTime(run.completed_at) : '—'}</div>
          </div>
        </div>
      </div>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Recorded metrics</h3>
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
              <h4 style={{ margin: '16px 0 8px', fontSize: '13px', color: 'var(--text-muted)' }}>Other metrics</h4>
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
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>P&amp;L cross-check</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              <div>Equity-method net P&amp;L</div>
              <div>{fmt(crossCheck.equity_net_pnl as number)}</div>
              <div>FIFO realized net P&amp;L</div>
              <div>{fmt(crossCheck.realized_net_pnl as number)}</div>
              <div>Difference</div>
              <div>{fmt(crossCheck.difference as number)}</div>
              <div>Result</div>
              <div data-testid="pnl-cross-check-result" style={{ fontWeight: 600 }}>
                {crossCheck.consistent === true ? 'Consistent'
                  : crossCheck.consistent === false ? 'Not consistent — metrics not reconciled'
                  : 'Cannot compare'}
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
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Breakdown by weekday</h3>
            <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px' }}>
              Date semantics: {fmt(weekday.date_semantics)} · Attribution basis: {fmt(weekday.attribution_rule)}
            </div>
            <table data-testid="weekday-breakdown" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                  <th style={{ padding: '4px 8px' }}>Day</th>
                  <th style={{ padding: '4px 8px' }}>Sessions</th>
                  <th style={{ padding: '4px 8px' }}>Closed</th>
                  <th style={{ padding: '4px 8px' }}>Win / loss</th>
                  <th style={{ padding: '4px 8px' }}>Realized P&amp;L</th>
                  <th style={{ padding: '4px 8px' }}>Mean</th>
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
                {fmt(weekday.unattributed_trades)} closed trade(s) could not be matched back to their original purchase cost and are excluded from this breakdown.
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
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Breakdown by time of day</h3>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)' }} data-testid="time-of-day-unavailable">
              {fmtMetric(timeOfDay).text}: {fmt(timeOfDay.reason)}
            </div>
          </div>
        </div>
      )}

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Trade journal (system record)</h3>
          {fills.length === 0 ? (
            <div style={{ fontSize: '13px', color: 'var(--text-muted)' }} data-testid="journal-empty">
              This run has no recorded fills.
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table data-testid="journal-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                    {['Date', 'Side', 'Quantity', 'Execution price', 'Commission', 'Stop', 'Target', 'Planned risk', 'Realized P&L', 'Realized R', 'Exit reason', 'Source', 'Config'].map((h) => (
                      <th key={h} style={{ padding: '4px 8px', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {fills.map((t, i) => (
                    <tr key={`${t.execution_date}-${i}`} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td style={{ padding: '4px 8px', whiteSpace: 'nowrap' }}>{fmt(t.execution_date ?? t.trade_date)}</td>
                      <td style={{ padding: '4px 8px' }}>{t.side ? label(SIDE_LABELS, String(t.side)) : '—'}</td>
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
                        {t.exit_reason ? label(EXIT_REASON_LABELS, t.exit_reason) : '—'}
                      </td>
                      <td style={{ padding: '4px 8px' }} data-testid={`journal-actor-${i}`}
                          title={t.actor ? String(t.actor) : undefined}>
                        {t.actor ? label(ACTOR_LABELS, String(t.actor)) : '—'}
                      </td>
                      <td style={{ padding: '4px 8px', fontFamily: 'monospace' }}>{t.strategy_config_hash ? t.strategy_config_hash.slice(0, 8) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {roundTrips.length > 0 && (
            <>
              <h4 style={{ margin: '16px 0 8px', fontSize: '13px', color: 'var(--text-muted)' }}>FIFO round-trip matching</h4>
              <div style={{ overflowX: 'auto' }}>
                <table data-testid="round-trip-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                      {['Buy date', 'Sell date', 'Shares', 'Cost / share', 'Proceeds / share', 'Realized P&L', 'Planned risk', 'Realized R'].map((h) => (
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
            Stop, target and planned risk are fields the strategy declares. When a template declares no stop, the
            table shows "—" rather than treating the trade as zero risk. "Source" records which layer produced the
            fill: engine fills are the strategy engine, while human and API client entries come from paper orders.
          </div>
        </div>
      </div>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Data source and snapshot</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            {PROVENANCE_KEYS.map(([k, heading]) => (
              <div key={k} style={{ display: 'contents' }}>
                <div>{heading}</div>
                <div style={{ fontFamily: 'monospace' }}>{fmt(resultMeta[k])}</div>
              </div>
            ))}
            <div>Dataset snapshot</div>
            <div style={{ fontFamily: 'monospace' }}>{fmt(run.dataset_snapshot_id)}</div>
            <div>Dataset checksum (field)</div>
            <div style={{ fontFamily: 'monospace' }}>{fmt(run.dataset_checksum)}</div>
          </div>
        </div>
      </div>

      <div className="dash-panel" data-testid="methodology-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Methodology and limitations</h3>
          {methodology ? (
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              {capturedParameters ? (
                <div style={{ marginBottom: '12px' }} data-testid="run-captured-parameters">
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>Parameters this run recorded</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: '4px 16px' }}>
                    <div>Commission rate</div>
                    <div style={{ fontFamily: 'monospace' }}>{fmtRateWithRaw(capturedParameters.commission_rate)}</div>
                    <div>Slippage rate</div>
                    <div style={{ fontFamily: 'monospace' }}>{fmtRateWithRaw(capturedParameters.slippage_rate)}</div>
                    <div>Initial cash</div>
                    <div style={{ fontFamily: 'monospace' }}>{fmt(capturedParameters.initial_cash)}</div>
                    <div>Signal parameters</div>
                    <div style={{ fontFamily: 'monospace' }}>{formatParamMap(capturedParameters.signal_parameters)}</div>
                    <div>Risk plan (stop / target)</div>
                    <div style={{ fontFamily: 'monospace' }}>{formatParamMap(capturedParameters.risk_parameters)}</div>
                    <div>Stop or target declared</div>
                    <div>
                      {capturedParameters.risk_parameters_declared === true
                        ? 'Yes'
                        : capturedParameters.risk_parameters_declared === false
                          ? 'No'
                          : 'Not recorded'}
                    </div>
                  </div>
                  <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                    Copied into this run&apos;s stored methodology when the run was created; not re-read from the
                    experiment&apos;s current version. Two runs with different values here really did use different
                    settings.
                  </p>
                </div>
              ) : (
                <div style={{ marginBottom: '12px' }} data-testid="run-captured-parameters-missing">
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>Parameters this run recorded</div>
                  <div>Not recorded for this run.</div>
                </div>
              )}
              {convention && (
                <div style={{ marginBottom: '12px' }}>
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>Execution assumptions</div>
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
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>Exit policy (stop / target)</div>
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
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>Journal field definitions</div>
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
                  <div style={{ fontWeight: 600, marginBottom: '4px' }}>Strategy rules</div>
                  <ul style={{ margin: 0, paddingLeft: '20px' }}>
                    {strategyRules.map((r, i) => <li key={i}>{String(r)}</li>)}
                  </ul>
                </div>
              )}
              <div>
                <div style={{ fontWeight: 600, marginBottom: '4px' }}>Known limitations</div>
                {Array.isArray(constraints) && constraints.length > 0 ? (
                  <ul style={{ margin: 0, paddingLeft: '20px' }}>
                    {constraints.map((c, i) => <li key={i}>{String(c)}</li>)}
                  </ul>
                ) : (
                  <div>This run has no recorded limitation list.</div>
                )}
              </div>
            </div>
          ) : (
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              This run has no recorded methodology summary (it predates this field), so its execution
              assumptions and limitations cannot be shown here.
            </div>
          )}
        </div>
      </div>

      {run.error_details && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Error details</h3>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
              {fmt(run.error_details)}
            </div>
          </div>
        </div>
      )}

      {Array.isArray(run.warnings) && run.warnings.length > 0 && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Warnings</h3>
            <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              {run.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        </div>
      )}

      <div className="dash-disclaimer" style={{ marginTop: '24px', fontSize: '12px' }}>
        Note: this content comes from a recorded run verified to belong to this experiment&apos;s run history. Some
        fields may be missing. Alethra is a research tool; results are not investment advice.
      </div>
    </div>
  );
}
