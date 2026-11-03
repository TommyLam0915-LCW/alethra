'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import {
  ATTEMPT_STATUS_LABELS,
  ROBUSTNESS_KIND_LABELS,
  ROBUSTNESS_ROLE_LABELS,
  ROBUSTNESS_STUDY_STATUS_LABELS,
  label,
} from '@/lib/copy';

interface StrategyVersion {
  id: string;
  version_number: number;
  strategy_spec: any;
  locked_at: string | null;
}

interface Props {
  experimentId: string;
  currentVersionId: string | null;
  versions: StrategyVersion[];
  onChanged: () => Promise<void> | void;
}

interface PlanCell {
  cell_key: string;
  label: string;
  role: string;
  overrides: Record<string, any>;
  period_start: string;
  period_end: string;
}

interface Plan {
  kind: string;
  version_id: string;
  version_number: number;
  template_type: string;
  source_period: { start_date: string; end_date: string };
  calendar: {
    source: string;
    providers: string[];
    natures: Record<string, string>;
    first_date: string;
    last_date: string;
    bars: number;
  };
  required_bars: number;
  required_bars_rule?: string;
  estimated_attempts: number;
  estimated_backtests?: number;
  authorization_required?: number;
  max_attempts: number;
  config: Record<string, number>;
  cells: PlanCell[];
  selection_rule: string;
  disclosures: string[];
  quota: {
    required: number;
    estimated_attempts?: number;
    sufficient: boolean;
    unlimited?: boolean;
    limit?: number | null;
    used?: number | null;
    remaining?: number | null;
  };
  ready: boolean;
}

interface Attempt {
  id: string;
  cell_key: string;
  label: string;
  role: string;
  overrides: Record<string, any>;
  period_start: string;
  period_end: string;
  run_id: string | null;
  status: string;
  metrics: any;
  dataset_checksum: string | null;
  data_identity: any;
  error_details: string | null;
  completed_at: string | null;
}

interface Aggregate {
  scope: string;
  attempts_total: number;
  counts: Record<string, number>;
  return_stats: {
    count: number;
    median: number;
    mean: number;
    min: number;
    max: number;
    spread: number;
    positive_count: number;
  } | null;
  drawdown_median: number | null;
  best_attempt: { cell_key: string; label: string; total_return_pct: number } | null;
  worst_attempt: { cell_key: string; label: string; total_return_pct: number } | null;
  selection_rule: string;
  note: string;
  folds:
    | {
        fold: string | null;
        period: { start_date: string; end_date: string };
        selected: any;
        selected_from: string | null;
        status: string;
        total_return_pct: number | null;
      }[]
    | null;
}

interface ReplayableCell {
  cell_key: string;
  label: string | null;
  role: string | null;
  period_start: string | null;
  period_end: string | null;
}

interface Study {
  id: string;
  version_id: string;
  kind: string;
  status: string;
  config: Record<string, number>;
  plan: Plan;
  aggregate: Aggregate;
  authorization?: {
    unit: string;
    required: number;
    estimated_attempts: number;
    state: string;
    charge_on_next_run: boolean;
    reservation_id: string | null;
    replayable_cells: ReplayableCell[];
  };
  replayable_cells?: ReplayableCell[];
  error_details: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  attempts: Attempt[];
}

const KINDS = Object.entries(ROBUSTNESS_KIND_LABELS).map(([value, text]) => ({
  value,
  label: text,
}));

const KIND_LABEL = ROBUSTNESS_KIND_LABELS;
const STATUS_LABEL = ROBUSTNESS_STUDY_STATUS_LABELS;
const ROLE_LABEL = ROBUSTNESS_ROLE_LABELS;

const th: React.CSSProperties = {
  padding: '6px 10px',
  textAlign: 'right',
  borderBottom: '2px solid var(--border-strong)',
  whiteSpace: 'nowrap',
  fontSize: '12px',
  color: 'var(--text-muted)',
};
const td: React.CSSProperties = {
  padding: '5px 10px',
  textAlign: 'right',
  borderBottom: '1px solid var(--border-strong)',
  whiteSpace: 'nowrap',
  fontSize: '13px',
};

function pct(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return `${value.toFixed(2)}%`;
}

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return fallback;
}

export default function RobustnessPanel({
  experimentId,
  currentVersionId,
  versions,
  onChanged,
}: Props) {
  const dailyVersions = useMemo(
    () =>
      versions.filter((v) => {
        const frequency = String(v.strategy_spec?.frequency ?? '1d');
        return frequency === '1d';
      }),
    [versions]
  );

  const [versionId, setVersionId] = useState(
    currentVersionId ?? dailyVersions[0]?.id ?? ''
  );
  const [kind, setKind] = useState('cost_scenarios');
  const [slices, setSlices] = useState('3');
  const [folds, setFolds] = useState('2');
  const [maxAttempts, setMaxAttempts] = useState('12');

  const [plan, setPlan] = useState<Plan | null>(null);
  const [planError, setPlanError] = useState('');
  const [planLoading, setPlanLoading] = useState(false);

  const [studies, setStudies] = useState<Study[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');

  const selected = studies.find((s) => s.id === selectedId) ?? studies[0] ?? null;

  const refreshStudies = async (preferId?: string) => {
    const list: Study[] = await api.robustness.list(experimentId);
    setStudies(list);
    setSelectedId((prev) => {
      const wanted = preferId ?? prev;
      if (wanted && list.some((s) => s.id === wanted)) return wanted;
      return list[0]?.id ?? null;
    });
  };

  useEffect(() => {
    refreshStudies().catch(() => setActionError('Could not load robustness studies'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId]);

  const requestBody = () => ({
    version_id: versionId,
    kind,
    config: {
      slices: Number(slices) || 3,
      folds: Number(folds) || 2,
      max_attempts: Number(maxAttempts) || 12,
    },
  });

  const handlePlan = async () => {
    if (!versionId) return;
    setPlanLoading(true);
    setPlanError('');
    setPlan(null);
    setNotice('');
    try {
      const result = await api.robustness.plan(experimentId, requestBody());
      setPlan(result);
    } catch (err) {
      setPlanError(describeError(err, 'Could not build the plan'));
    } finally {
      setPlanLoading(false);
    }
  };

  const handleStart = async () => {
    if (!versionId) return;
    setBusy(true);
    setPlanError('');
    setActionError('');
    setNotice('');
    try {
      const study = await api.robustness.create(experimentId, {
        ...requestBody(),
        request_id: newRequestId(),
      });
      await refreshStudies(study.id);
      setNotice(
        `Study created (${KIND_LABEL[study.kind] ?? study.kind}) with ${study.plan.estimated_attempts} attempts. Backtests only start when you select "Run study".`
      );
    } catch (err) {
      setPlanError(describeError(err, 'Could not create the study'));
    } finally {
      setBusy(false);
    }
  };

  const afterStudyChange = async (studyId: string) => {
    await refreshStudies(studyId);
    await onChanged();
  };

  const handleRun = async (study: Study) => {
    setBusy(true);
    setActionError('');
    setNotice('');
    try {
      const detail = await api.robustness.run(study.id);
      await afterStudyChange(detail.id);
      setNotice(
        `Study run: ${detail.aggregate.counts.completed} attempts completed, ` +
          `${detail.aggregate.counts.failed} failed, ${detail.aggregate.counts.skipped} skipped.`
      );
    } catch (err) {
      setActionError(describeError(err, 'Running the study failed'));
      await refreshStudies(study.id).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async (study: Study) => {
    setBusy(true);
    setActionError('');
    setNotice('');
    try {
      await api.robustness.cancel(study.id);
      await afterStudyChange(study.id);
      setNotice('Study cancelled; completed attempts and results are kept.');
    } catch (err) {
      setActionError(describeError(err, 'Cancelling the study failed'));
    } finally {
      setBusy(false);
    }
  };

  const canRun =
    selected != null &&
    ['planned', 'partial', 'failed', 'running'].includes(selected.status);
  const canCancel =
    selected != null && ['planned', 'partial', 'failed', 'running'].includes(selected.status);

  return (
    <div className="card" style={{ marginBottom: '20px' }} data-testid="robustness-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '12px',
        }}
      >
        <h3 style={{ margin: 0 }}>Advanced studies and robustness</h3>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Each attempt is a real backtest; the whole study uses one research-run
          authorization
        </span>
      </div>

      <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: 0 }}>
        Preview the plan first (number of attempts, data calendar, whether one
        authorization unit is available), then create and run it. The whole study is
        charged as one research-run unit when it starts; finishing a partial study
        does not charge again. A study does not modify the strategy version. Every
        attempt is listed, including failed and skipped ones, and the best single
        result is not treated as overall robustness.
      </p>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
          marginBottom: '12px',
        }}
      >
        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          Strategy version
          <select
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={versionId}
            onChange={(e) => {
              setVersionId(e.target.value);
              setPlan(null);
              setPlanError('');
            }}
            data-testid="robustness-version"
          >
            {dailyVersions.length === 0 && <option value="">No daily versions</option>}
            {dailyVersions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_number}
                {v.id === currentVersionId ? ' (current)' : ''}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          Study kind
          <select
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setPlan(null);
              setPlanError('');
            }}
            data-testid="robustness-kind"
          >
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          Slices (2–4)
          <input
            type="number"
            min={2}
            max={4}
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={slices}
            onChange={(e) => setSlices(e.target.value)}
            data-testid="robustness-slices"
          />
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          Folds (2–3)
          <input
            type="number"
            min={2}
            max={3}
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={folds}
            onChange={(e) => setFolds(e.target.value)}
            data-testid="robustness-folds"
          />
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          Attempt cap (1–24)
          <input
            type="number"
            min={1}
            max={24}
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={maxAttempts}
            onChange={(e) => setMaxAttempts(e.target.value)}
            data-testid="robustness-max-attempts"
          />
        </label>
      </div>

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
        <button
          type="button"
          className="btn"
          style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
          onClick={handlePlan}
          disabled={planLoading || busy || !versionId}
          data-testid="robustness-plan"
        >
          {planLoading ? 'Calculating…' : 'Preview plan'}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '13px' }}
          onClick={handleStart}
          disabled={busy || !versionId || !plan?.ready}
          title={plan?.ready ? '' : 'Preview the plan and confirm quota is sufficient first'}
          data-testid="robustness-start"
        >
          {busy ? 'Working…' : 'Create study'}
        </button>
      </div>

      {planError && (
        <div
          role="alert"
          data-testid="robustness-plan-error"
          style={{
            padding: '10px 12px',
            backgroundColor: 'rgba(239,68,68,0.12)',
            color: 'var(--negative)',
            borderRadius: '6px',
            fontSize: '13px',
            marginBottom: '12px',
            whiteSpace: 'pre-wrap',
          }}
        >
          {planError}
        </div>
      )}

      {notice && (
        <p
          role="status"
          data-testid="robustness-notice"
          style={{ color: 'var(--positive)', fontSize: '13px', margin: '0 0 12px' }}
        >
          {notice}
        </p>
      )}

      {actionError && (
        <p role="alert" data-testid="robustness-action-error" style={{ color: 'var(--negative)', fontSize: '13px', margin: '0 0 12px' }}>
          {actionError}
        </p>
      )}

      {plan && (
        <div
          style={{ padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', marginBottom: '16px' }}
          data-testid="robustness-plan-preview"
        >
          <div style={{ fontSize: '13px', marginBottom: '8px' }} data-testid="robustness-estimated">
            <strong>{KIND_LABEL[plan.kind] ?? plan.kind}</strong> · v{plan.version_number} ·{' '}
            estimated <strong>{plan.estimated_attempts}</strong> backtests (cap{' '}
            {plan.max_attempts}) under <strong>{plan.authorization_required ?? 1}</strong>{' '}
            research-run authorization · each slice needs at least {plan.required_bars}{' '}
            trading days
            {plan.required_bars_rule ? (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                Warm-up rule: {plan.required_bars_rule}
              </div>
            ) : null}
          </div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '8px' }}>
            Data calendar:{' '}
            {plan.calendar.source === 'stored'
              ? 'stored market data'
              : 'built-in synthetic demo data (not real market prices)'}
            {' · '}
            {plan.calendar.first_date} → {plan.calendar.last_date} ({plan.calendar.bars}{' '}
            trading days)
            {' · Providers: '}
            {plan.calendar.providers.join(', ') || '—'}
          </div>
          <div style={{ fontSize: '13px', marginBottom: '8px' }} data-testid="robustness-quota">
            Quota: this study uses {plan.quota.required} research-run authorization for{' '}
            {plan.quota.estimated_attempts ?? plan.estimated_attempts} backtests
            {plan.quota.unlimited
              ? '; the current plan is unlimited.'
              : `; used ${plan.quota.used ?? '—'} / limit ${plan.quota.limit ?? '—'}, ${
                  plan.quota.remaining ?? '—'
                } remaining.`}
            {plan.ready ? (
              <span style={{ color: 'var(--positive)' }}>
                {' '}
                Quota is sufficient, so you can create the study.
              </span>
            ) : (
              <span style={{ color: 'var(--negative)' }}>
                {' '}
                Quota is insufficient; the backend will refuse to create the study and no
                backtest will run.
              </span>
            )}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
            Selection rule: {plan.selection_rule}
          </div>
          <ul style={{ margin: '6px 0 0 18px', padding: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
            {plan.disclosures.map((line, i) => (
              <li key={i} style={{ marginBottom: '3px' }}>
                {line}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ marginBottom: '16px' }} data-testid="robustness-studies">
        <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '6px' }}>
          Studies for this experiment ({studies.length})
        </div>
        {studies.length === 0 ? (
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
            No robustness studies yet. Preview the plan first, then create the study.
          </p>
        ) : (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {studies.map((s) => (
              <button
                key={s.id}
                type="button"
                className="btn"
                onClick={() => {
                  setSelectedId(s.id);
                  setActionError('');
                  setNotice('');
                }}
                data-testid={`robustness-study-${s.id}`}
                style={{
                  fontSize: '12px',
                  backgroundColor: s.id === selected?.id ? 'rgba(59,130,246,0.16)' : 'var(--surface-2)',
                  border: s.id === selected?.id ? '1px solid var(--primary)' : '1px solid var(--border-strong)',
                  textAlign: 'left',
                }}
              >
                {KIND_LABEL[s.kind] ?? s.kind}
                <br />
                <span style={{ color: 'var(--text-secondary)' }}>
                  {STATUS_LABEL[s.status] ?? s.status} · {s.attempts.length}/{s.plan.estimated_attempts} attempts
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <div data-testid="robustness-detail">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: '8px',
              marginBottom: '10px',
            }}
          >
            <div style={{ fontSize: '14px' }}>
              <strong>{KIND_LABEL[selected.kind] ?? selected.kind}</strong> · v
              {selected.plan.version_number} · Status: {STATUS_LABEL[selected.status] ?? selected.status}
              {selected.error_details && (
                <span style={{ color: 'var(--negative)' }}> · {selected.error_details}</span>
              )}
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              {canRun && (
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ fontSize: '13px' }}
                  onClick={() => handleRun(selected)}
                  disabled={busy}
                  data-testid="robustness-run"
                >
                  {selected.status === 'running'
                    ? 'Continue unfinished attempts'
                    : selected.attempts.some((a) => a.status === 'completed')
                    ? 'Continue / re-run unfinished attempts'
                    : 'Run study'}
                </button>
              )}
              {canCancel && (
                <button
                  type="button"
                  className="btn"
                  style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
                  onClick={() => handleCancel(selected)}
                  disabled={busy}
                  data-testid="robustness-cancel"
                >
                  Cancel study
                </button>
              )}
            </div>
          </div>

          {selected.authorization && (
            <div
              data-testid="robustness-authorization"
              style={{
                fontSize: '12px',
                color: 'var(--text-secondary)',
                margin: '0 0 10px',
                padding: '8px 10px',
                backgroundColor: 'var(--surface-2)',
                borderRadius: '6px',
              }}
            >
              Authorization: {selected.authorization.required} research-run unit for{' '}
              {selected.authorization.estimated_attempts} backtests ·{' '}
              {selected.authorization.state === 'charged'
                ? 'already charged; re-running or finishing is free'
                : selected.authorization.state === 'closed'
                ? 'study closed without being charged'
                : 'not charged yet; the unit is taken when the study starts'}
              .
              <div style={{ marginTop: '4px' }} data-testid="robustness-replayable">
                {selected.replayable_cells && selected.replayable_cells.length > 0 ? (
                  <>
                    A run would execute {selected.replayable_cells.length} cell
                    {selected.replayable_cells.length === 1 ? '' : 's'} that are not yet
                    completed: {selected.replayable_cells.map((c) => c.label ?? c.cell_key).join(' · ')}
                  </>
                ) : (
                  'No cells remain to run; every planned cell is completed.'
                )}
              </div>
            </div>
          )}

          {selected.attempts.length === 0 ? (
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: '0 0 12px' }}>
              No attempts have run yet. Selecting &ldquo;Run study&rdquo; backtests cell by
              cell, and every attempt uses the source version&apos;s rules.
            </p>
          ) : (
            <div style={{ overflowX: 'auto' }} data-testid="robustness-attempts">
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ ...th, textAlign: 'left' }}>Attempt</th>
                    <th style={th}>Role</th>
                    <th style={th}>Period</th>
                    <th style={th}>Setting differences</th>
                    <th style={th}>Status</th>
                    <th style={th}>Total return</th>
                    <th style={th}>Max drawdown</th>
                    <th style={th}>Trades</th>
                    <th style={th}>Data</th>
                    <th style={th}>Run</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.attempts.map((a) => {
                    const identity = a.data_identity ?? {};
                    const natures = identity.data_nature
                      ? Object.values(identity.data_nature).join('/')
                      : '—';
                    const overrides =
                      Object.keys(a.overrides ?? {}).length === 0
                        ? 'Original settings'
                        : Object.entries(a.overrides)
                            .filter(([k]) => k !== 'start_date' && k !== 'end_date')
                            .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                            .join(', ') || 'Period only';
                    return (
                      <tr key={a.id} data-testid={`robustness-attempt-${a.cell_key}`}>
                        <td style={{ ...td, textAlign: 'left' }}>{a.label}</td>
                        <td style={td}>{ROLE_LABEL[a.role] ?? a.role}</td>
                        <td style={td}>
                          {a.period_start} → {a.period_end}
                        </td>
                        <td style={{ ...td, textAlign: 'left', maxWidth: '240px', whiteSpace: 'normal' }}>
                          {overrides}
                        </td>
                        <td style={td}>
                          {label(ATTEMPT_STATUS_LABELS, a.status)}
                          {a.error_details ? `: ${a.error_details}` : ''}
                        </td>
                        <td style={td}>{pct(a.metrics?.total_return_pct)}</td>
                        <td style={td}>{pct(a.metrics?.max_drawdown_pct)}</td>
                        <td style={td}>
                          {typeof a.metrics?.total_trades === 'number' ? a.metrics.total_trades : '—'}
                        </td>
                        <td style={{ ...td, textAlign: 'left', fontSize: '12px', color: 'var(--text-secondary)' }}>
                          {identity.data_source_used ?? '—'} / {natures}
                          {a.dataset_checksum ? (
                            <span title={a.dataset_checksum}>
                              {' '}
                              · {a.dataset_checksum.slice(0, 8)}
                            </span>
                          ) : null}
                        </td>
                        <td style={td}>
                          {a.run_id ? (
                            <Link href={`/experiments/${experimentId}/runs/${a.run_id}`} style={{ fontSize: '12px' }}>
                              View
                            </Link>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div
            style={{ marginTop: '12px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}
            data-testid="robustness-aggregate"
          >
            <div style={{ fontSize: '13px', fontWeight: 'bold', marginBottom: '6px' }}>
              Aggregate (scope:{' '}
              {selected.aggregate.scope === 'measurement_only'
                ? 'test-window measurements only'
                : 'all attempts'}
              )
            </div>
            {selected.aggregate.return_stats ? (
              <div style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                {selected.aggregate.return_stats.count} completed · median{' '}
                {pct(selected.aggregate.return_stats.median)} · mean{' '}
                {pct(selected.aggregate.return_stats.mean)} · worst{' '}
                {pct(selected.aggregate.return_stats.min)} · best{' '}
                {pct(selected.aggregate.return_stats.max)} · spread{' '}
                {pct(selected.aggregate.return_stats.spread)} · positive{' '}
                {selected.aggregate.return_stats.positive_count} /{' '}
                {selected.aggregate.return_stats.count} · median drawdown{' '}
                {pct(selected.aggregate.drawdown_median)}
              </div>
            ) : (
              <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                No completed attempts, so nothing can be aggregated.
              </div>
            )}
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '6px' }}>
              Attempt status: completed {selected.aggregate.counts.completed ?? 0} · failed{' '}
              {selected.aggregate.counts.failed ?? 0} · skipped{' '}
              {selected.aggregate.counts.skipped ?? 0} · not run{' '}
              {selected.aggregate.counts.pending ?? 0}
            </div>
            {selected.aggregate.best_attempt && (
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                Best single result: {selected.aggregate.best_attempt.label} (
                {pct(selected.aggregate.best_attempt.total_return_pct)}); worst:{' '}
                {selected.aggregate.worst_attempt?.label} (
                {pct(selected.aggregate.worst_attempt?.total_return_pct)})
              </div>
            )}
            <div style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '6px' }}>
              {selected.aggregate.note}
            </div>
            {selected.aggregate.folds && selected.aggregate.folds.length > 0 && (
              <div style={{ marginTop: '8px' }}>
                <div style={{ fontSize: '12px', fontWeight: 'bold', marginBottom: '4px' }}>Per-fold results</div>
                <ul style={{ margin: '0 0 0 18px', padding: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
                  {selected.aggregate.folds.map((fold, i) => (
                    <li key={i}>
                      Fold {fold.fold} test period {fold.period.start_date} → {fold.period.end_date}:{' '}
                      {fold.status === 'completed'
                        ? `return ${pct(fold.total_return_pct)}`
                        : label(ATTEMPT_STATUS_LABELS, fold.status)}
                      {fold.selected ? ` · selected parameters ${JSON.stringify(fold.selected)}` : ''}
                      {fold.selected_from ? ` (from ${fold.selected_from})` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
