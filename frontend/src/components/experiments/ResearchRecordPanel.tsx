'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import {
  ACTION_LABELS,
  ATTEMPT_STATUS_LABELS,
  DECISION_TYPE_LABELS,
  HUMAN_EXECUTION_STATUS_LABELS,
  ROBUSTNESS_KIND_LABELS,
  ROBUSTNESS_STUDY_STATUS_LABELS,
  SESSION_STATUS_LABELS,
  label,
} from '@/lib/copy';

interface RecordCoverage {
  versions: number;
  revisions: number;
  runs: number;
  completed_runs: number;
  robustness_studies: number;
  robustness_attempts: number;
  saved_comparisons: number;
  simulation_sessions: number;
  simulation_decisions: number;
  human_decisions: number;
  notes: number;
  events: number;
  excluded: string[];
}

interface RevisionEntry {
  version_id: string;
  version_number: number;
  base_version_id: string | null;
  base_version_number: number | null;
  change_reason: string | null;
  change_kind: string | null;
  changed_fields: string[];
  expectation: string | null;
  outcome: string | null;
  met_expectation: boolean | null;
  outcome_recorded_at: string | null;
}

interface AttemptEntry {
  id: string;
  cell_key: string;
  label: string;
  role: string;
  status: string;
  run_id: string | null;
  error_details: string | null;
}

interface StudyEntry {
  id: string;
  kind: string;
  status: string;
  aggregate: any;
  error_details: string | null;
  attempts: AttemptEntry[];
}

interface SavedComparisonEntry {
  id: string;
  run_a_id: string;
  run_b_id: string;
  run_a_version_number: number | null;
  run_b_version_number: number | null;
  purpose: string;
  notes: string | null;
  updated_at: string | null;
}

interface DecisionEntry {
  id: string;
  step_number: number;
  snapshot_date: string;
  decision_type: string;
  action: string | null;
  rationale: string | null;
  reflection: string | null;
}

interface HumanDecisionEntry {
  id: string;
  action: string;
  quantity: number | null;
  rationale: string | null;
  execution_status: string;
  server_snapshot_date: string;
}

interface SessionEntry {
  id: string;
  version_number: number | null;
  status: string;
  timeframe: string;
  data_cutoff_at: string;
  current_snapshot_date: string | null;
  observed_days: number | null;
  decisions: DecisionEntry[];
  human_decisions: HumanDecisionEntry[];
}

interface ExperimentRecord {
  schema_version: string;
  generated_at: string;
  coverage: RecordCoverage;
  decision_review: {
    revisions: RevisionEntry[];
    robustness_studies: StudyEntry[];
    saved_comparisons: SavedComparisonEntry[];
    simulation_sessions: SessionEntry[];
  };
}

interface Props {
  experimentId: string;
  refreshToken?: number;
}

const KIND_LABEL = ROBUSTNESS_KIND_LABELS;
const STUDY_STATUS_LABEL = ROBUSTNESS_STUDY_STATUS_LABELS;

const CHANGE_KIND_LABEL: Record<string, string> = {
  strategy_only: 'Strategy settings changed',
  period_only: 'Research period only',
  strategy_and_period: 'Strategy settings and research period changed',
};

const EXCLUDED_LABEL: Record<string, string> = {
  'runs.result_json.transactions': 'per-trade transactions',
  'runs.result_json.round_trips': 'round-trip trades',
  'runs.result_json.daily_equity_curve': 'daily equity curve',
  'runs.result_json.daily_accounting': 'daily accounting records',
};

function outcomeLabel(entry: RevisionEntry): { text: string; color: string } {
  if (entry.met_expectation === true) return { text: 'Matched expectation', color: 'var(--positive)' };
  if (entry.met_expectation === false)
    return { text: 'Did not match expectation', color: 'var(--warning)' };
  return { text: 'No conclusion recorded', color: 'var(--text-secondary)' };
}

function CountChip({ label, value, testId }: { label: string; value: number; testId: string }) {
  return (
    <div
      data-testid={testId}
      style={{
        padding: '8px 12px',
        backgroundColor: 'var(--surface-2)',
        border: '1px solid var(--border-strong)',
        borderRadius: '6px',
        minWidth: '96px',
      }}
    >
      <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{label}</div>
      <div style={{ fontSize: '18px', fontWeight: 600 }}>{value}</div>
    </div>
  );
}

export default function ResearchRecordPanel({ experimentId, refreshToken = 0 }: Props) {
  const [record, setRecord] = useState<ExperimentRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.experiments.record(experimentId);
        if (!cancelled) {
          setRecord(data);
          setError('');
        }
      } catch (err: any) {
        if (!cancelled) setError(err?.message || 'Could not load the research record');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [experimentId, refreshToken]);

  const handleDownload = async () => {
    setDownloading(true);
    setNotice('');
    setError('');
    try {
      const data = await api.experiments.export(experimentId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `alethra-record-${experimentId.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      const sections = data?.decision_review ? Object.keys(data.decision_review).length : 0;
      setNotice(
        `Downloaded the full research record (${sections} decision review sections, ${data?.coverage?.events ?? 0} events).`
      );
    } catch (err: any) {
      setError(err?.message || 'Export failed');
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <div className="card" style={{ marginBottom: '20px' }} data-testid="record-panel">
        <h3 style={{ marginTop: 0 }}>Research record overview</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '13px' }}>Loading the research record…</p>
      </div>
    );
  }

  if (!record) {
    return (
      <div className="card" style={{ marginBottom: '20px' }} data-testid="record-panel">
        <h3 style={{ marginTop: 0 }}>Research record overview</h3>
        <p role="alert" data-testid="record-error" style={{ color: 'var(--negative)', fontSize: '13px' }}>
          {error || 'Could not load the research record'}
        </p>
      </div>
    );
  }

  const { coverage, decision_review: review } = record;
  const rationaleDecisions = review.simulation_sessions.flatMap((session) =>
    session.decisions
      .filter((decision) => decision.rationale || decision.reflection)
      .map((decision) => ({ session, decision }))
  );

  return (
    <div className="card" style={{ marginBottom: '20px' }} data-testid="record-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '12px',
          flexWrap: 'wrap',
          marginBottom: '8px',
        }}
      >
        <h3 style={{ margin: 0 }}>Research record overview</h3>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '13px' }}
          onClick={handleDownload}
          disabled={downloading}
          data-testid="record-download"
        >
          {downloading ? 'Exporting…' : 'Download full research record'}
        </button>
      </div>

      <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: 0 }}>
        This collects the evidence a decision review needs: revisions and their
        conclusions, every robustness attempt, the reasons recorded for saved
        comparisons, and the rationale and reflections behind simulated decisions.
        Missing sections show 0 and are never filled in automatically.
      </p>

      {notice && (
        <p
          role="status"
          data-testid="record-notice"
          style={{ color: 'var(--positive)', fontSize: '13px', margin: '0 0 12px' }}
        >
          {notice}
        </p>
      )}
      {error && (
        <p
          role="alert"
          data-testid="record-error"
          style={{ color: 'var(--negative)', fontSize: '13px', margin: '0 0 12px' }}
        >
          {error}
        </p>
      )}

      <div
        data-testid="record-counts"
        style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}
      >
        <CountChip label="Versions" value={coverage.versions} testId="record-count-versions" />
        <CountChip label="Revisions" value={coverage.revisions} testId="record-count-revisions" />
        <CountChip label="Runs" value={coverage.runs} testId="record-count-runs" />
        <CountChip
          label="Robustness studies"
          value={coverage.robustness_studies}
          testId="record-count-studies"
        />
        <CountChip
          label="Robustness attempts"
          value={coverage.robustness_attempts}
          testId="record-count-attempts"
        />
        <CountChip
          label="Saved comparisons"
          value={coverage.saved_comparisons}
          testId="record-count-comparisons"
        />
        <CountChip
          label="Simulated decisions"
          value={coverage.simulation_decisions}
          testId="record-count-decisions"
        />
        <CountChip
          label="Manual intents"
          value={coverage.human_decisions}
          testId="record-count-human"
        />
        <CountChip label="Notes" value={coverage.notes} testId="record-count-notes" />
        <CountChip label="Events" value={coverage.events} testId="record-count-events" />
      </div>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>Revision conclusions</h4>
        {review.revisions.length === 0 ? (
          <p data-testid="record-revisions-empty" style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
            No revisions yet, so there are no change conclusions to review.
          </p>
        ) : (
          <div data-testid="record-revisions" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px' }}>Version</th>
                  <th style={{ padding: '6px 8px' }}>Source</th>
                  <th style={{ padding: '6px 8px' }}>Change</th>
                  <th style={{ padding: '6px 8px' }}>Reason</th>
                  <th style={{ padding: '6px 8px' }}>Expectation</th>
                  <th style={{ padding: '6px 8px' }}>Conclusion</th>
                </tr>
              </thead>
              <tbody>
                {review.revisions.map((entry) => {
                  const outcome = outcomeLabel(entry);
                  return (
                    <tr
                      key={entry.version_id}
                      data-testid={`record-revision-${entry.version_id}`}
                      style={{ borderBottom: '1px solid var(--surface-2)', verticalAlign: 'top' }}
                    >
                      <td style={{ padding: '6px 8px', fontWeight: 500 }}>
                        v{entry.version_number}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-secondary)' }}>
                        {entry.base_version_number != null ? `v${entry.base_version_number}` : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-secondary)' }}>
                        {entry.change_kind ? CHANGE_KIND_LABEL[entry.change_kind] ?? entry.change_kind : '—'}
                        {entry.changed_fields.length > 0 && (
                          <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                            {entry.changed_fields.join(', ')}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-muted)' }}>
                        {entry.change_reason || '—'}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-muted)' }}>
                        {entry.expectation || '—'}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        <div style={{ color: outcome.color, fontWeight: 500 }}>{outcome.text}</div>
                        {entry.outcome && (
                          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{entry.outcome}</div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>Robustness studies</h4>
        {review.robustness_studies.length === 0 ? (
          <p data-testid="record-studies-empty" style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
            No robustness study has run yet; a single backtest is not robustness evidence.
          </p>
        ) : (
          <div data-testid="record-studies" style={{ display: 'grid', gap: '8px' }}>
            {review.robustness_studies.map((study) => {
              const counts = study.aggregate?.counts ?? {};
              return (
                <div
                  key={study.id}
                  data-testid={`record-study-${study.id}`}
                  style={{
                    padding: '10px 12px',
                    backgroundColor: 'var(--surface-2)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: '6px',
                    fontSize: '13px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
                    <strong>{KIND_LABEL[study.kind] ?? study.kind}</strong>
                    <span style={{ color: 'var(--text-muted)' }}>
                      {STUDY_STATUS_LABEL[study.status] ?? study.status}
                    </span>
                  </div>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '4px' }}>
                    Attempts {study.attempts.length} (completed {counts.completed ?? 0}, failed{' '}
                    {counts.failed ?? 0}, skipped {counts.skipped ?? 0})
                    {study.aggregate?.return_stats
                      ? ` · median return ${study.aggregate.return_stats.median}%`
                      : ''}
                  </div>
                  {study.attempts.length > 0 && (
                    <div style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '4px' }}>
                      {study.attempts
                        .map(
                          (attempt) =>
                            `${attempt.label} (${label(ATTEMPT_STATUS_LABELS, attempt.status)})`
                        )
                        .join(', ')}
                    </div>
                  )}
                  {study.error_details && (
                    <div style={{ color: 'var(--warning)', fontSize: '12px', marginTop: '4px' }}>
                      {study.error_details}
                    </div>
                  )}
                </div>
              );
            })}
            <p style={{ color: 'var(--text-muted)', fontSize: '12px', margin: 0 }}>
              The best single attempt is not robustness evidence: read the median, the worst
              result, the spread and the failure count together.
            </p>
          </div>
        )}
      </section>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>Saved comparisons</h4>
        {review.saved_comparisons.length === 0 ? (
          <p
            data-testid="record-comparisons-empty"
            style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}
          >
            No comparisons saved yet; without a recorded reason it is hard to review later why
            a judgement was made.
          </p>
        ) : (
          <div data-testid="record-comparisons" style={{ display: 'grid', gap: '8px' }}>
            {review.saved_comparisons.map((entry) => (
              <Link
                key={entry.id}
                href={`/experiments/${experimentId}/runs/compare?saved_comparison_id=${entry.id}`}
                data-testid={`record-comparison-${entry.id}`}
                style={{
                  padding: '10px 12px',
                  backgroundColor: 'var(--surface-2)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  textDecoration: 'none',
                  color: 'inherit',
                  fontSize: '13px',
                }}
              >
                <div style={{ fontWeight: 500 }}>{entry.purpose}</div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '2px' }}>
                  {entry.run_a_version_number != null ? `v${entry.run_a_version_number}` : '—'} →{' '}
                  {entry.run_b_version_number != null ? `v${entry.run_b_version_number}` : '—'}
                  {entry.notes ? ` · ${entry.notes}` : ''}
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>Simulated decisions and reflections</h4>
        {review.simulation_sessions.length === 0 ? (
          <p data-testid="record-sessions-empty" style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
            No forward simulation has run yet, so there are no decisions to review.
          </p>
        ) : (
          <div data-testid="record-sessions" style={{ display: 'grid', gap: '8px' }}>
            {review.simulation_sessions.map((session) => (
              <div
                key={session.id}
                data-testid={`record-session-${session.id}`}
                style={{
                  padding: '10px 12px',
                  backgroundColor: 'var(--surface-2)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  fontSize: '13px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
                  <strong>
                    {session.version_number != null ? `v${session.version_number}` : '—'} ·{' '}
                    {session.timeframe === '1d' ? 'Daily' : session.timeframe}
                  </strong>
                  <span style={{ color: 'var(--text-muted)' }}>
                    {label(SESSION_STATUS_LABELS, session.status)} · data as of{' '}
                    {session.data_cutoff_at}
                  </span>
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '2px' }}>
                  Decisions {session.decisions.length}, manual intents{' '}
                  {session.human_decisions.length}
                  {session.current_snapshot_date
                    ? ` · observed to ${session.current_snapshot_date}`
                    : ''}
                </div>
                {session.human_decisions.map((human) => (
                  <div
                    key={human.id}
                    data-testid={`record-human-${human.id}`}
                    style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-muted)' }}
                  >
                    Manual intent: {label(ACTION_LABELS, human.action)}
                    {human.quantity != null ? ` ${human.quantity}` : ''} (
                    {label(HUMAN_EXECUTION_STATUS_LABELS, human.execution_status)})
                    {human.rationale ? ` · Rationale: ${human.rationale}` : ''}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {rationaleDecisions.length > 0 && (
          <div data-testid="record-decision-notes" style={{ marginTop: '8px', display: 'grid', gap: '6px' }}>
            {rationaleDecisions.map(({ decision }) => (
              <div
                key={decision.id}
                data-testid={`record-decision-${decision.id}`}
                style={{
                  padding: '8px 12px',
                  backgroundColor: 'var(--surface-1)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  fontSize: '12px',
                  color: 'var(--text-muted)',
                }}
              >
                <span style={{ fontWeight: 500 }}>
                  {decision.snapshot_date} ·{' '}
                  {decision.action
                    ? label(ACTION_LABELS, decision.action)
                    : label(DECISION_TYPE_LABELS, decision.decision_type)}
                </span>
                {decision.rationale && <span> · Rationale: {decision.rationale}</span>}
                {decision.reflection && (
                  <div style={{ color: 'var(--text-secondary)', marginTop: '2px' }}>
                    Reflection: {decision.reflection}
                  </div>
                )}
              </div>
            ))}
            <p style={{ color: 'var(--text-muted)', fontSize: '11px', margin: 0 }}>
              Only decisions with a recorded rationale or reflection are listed; full decision
              details are in the export.
            </p>
          </div>
        )}
      </section>

      <div
        data-testid="record-scope"
        style={{
          padding: '10px 12px',
          backgroundColor: 'var(--surface-2)',
          borderRadius: '6px',
          fontSize: '12px',
          color: 'var(--text-muted)',
        }}
      >
        The export ({record.schema_version}) contains the research method, versions, revision
        conclusions, robustness studies and attempts, saved comparisons, simulated decisions and
        events.
        {coverage.excluded.length > 0 && (
          <>
            {' '}The following are deliberately excluded from the record:{' '}
            {coverage.excluded
              .map((key) => EXCLUDED_LABEL[key] ?? key)
              .join(', ')}
            . Open a single run's detail page when you need them.
          </>
        )}
        <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
          This is an organised record of the research process, not investment advice; the record
          does not rewrite data that has already happened.
        </div>
      </div>
    </div>
  );
}
