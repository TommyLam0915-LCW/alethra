'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { describeRunStatus } from '@/lib/runStatus';
import { brand } from '@/lib/brand';
import { statusPillClass } from '@/lib/status-styles';
import RevisionPanel from '@/components/experiments/RevisionPanel';
import RobustnessPanel from '@/components/experiments/RobustnessPanel';
import ResearchRecordPanel from '@/components/experiments/ResearchRecordPanel';
import { formatDate, formatDateTime, formatRatePercent } from '@/lib/format';
import { DATA_NATURE_LABELS, label } from '@/lib/copy';

interface Experiment {
  id: string;
  title: string;
  hypothesis: string | null;
  falsification_conditions: string | null;
  status: string;
  created_at: string;
  current_version_id: string | null;
  continued_from_experiment_id: string | null;
  continued_from_version_id: string | null;
  scope: Record<string, any> | null;
}

interface Run {
  id: string;
  experiment_version_id: string;
  status: string;
  metrics: any;
  warnings: string[] | null;
  config_hash: string;
  dataset_checksum: string | null;
  methodology_json: any;
  result_json: any;
  started_at: string | null;
  completed_at: string | null;
}

interface TimelineEvent {
  id: string;
  entity_type: string;
  entity_id: string;
  event_type: string;
  actor_type: string;
  payload_json: any;
  recorded_before_outcome: boolean;
  rules_locked: boolean;
  data_cutoff_at: string | null;
  created_at: string;
}

interface Note {
  id: string;
  content: string;
  author_id: string;
  created_at: string;
}

interface Version {
  id: string;
  version_number: number;
  config_hash: string | null;
  change_reason: string | null;
  created_at: string;
  strategy_spec: any;
  assumptions?: any;
  locked_at: string | null;
}

interface DiffEntry {
  field: string;
  old_value: any;
  new_value: any;
}

interface SavedComparisonSummary {
  id: string;
  run_a_id: string;
  run_b_id: string;
  run_a_experiment_id: string;
  run_b_experiment_id: string;
  run_a_experiment_version_id: string;
  run_b_experiment_version_id: string;
  scope: 'same_experiment' | 'cross_experiment';
  purpose: string;
  updated_at: string;
}

interface CheckResult {
  rule_id: string;
  rule_version: string;
  status: string;
  evidence: Record<string, any>;
  explanation: string;
  limitations: string[];
}

interface MethodReview {
  rule_set_version: string;
  experiment_id: string;
  version_id: string | null;
  run_id: string | null;
  run_selection: string;
  config_hash_match: boolean | null;
  completed_at_available: boolean | null;
  generated_at: string;
  checks: CheckResult[];
}

const thStyle: React.CSSProperties = { padding: '6px 10px', textAlign: 'right' as const, borderBottom: '2px solid var(--border-strong)', whiteSpace: 'nowrap' as const };
const tdStyle: React.CSSProperties = { padding: '5px 10px', textAlign: 'right' as const, borderBottom: '1px solid var(--border-strong)', whiteSpace: 'nowrap' as const };

const EVENT_LABELS: Record<string, string> = {
  experiment_created: 'Experiment created',
  hypothesis_updated: 'Hypothesis updated',
  falsification_condition_added: 'Falsification conditions updated',
  dataset_selected: 'Dataset selected',
  strategy_template_selected: 'Strategy template selected',
  parameter_changed: 'Parameter changed',
  strategy_version_created: 'New version created',
  research_run_started: 'Backtest started',
  research_run_completed: 'Backtest completed',
  research_run_failed: 'Backtest failed',
  warning_generated: 'Warning raised',
  strategy_locked: 'Strategy locked',
  research_note_added: 'Research note added',
  revision_outcome_recorded: 'Revision outcome recorded',
  robustness_study_started: 'Robustness study started',
  robustness_study_completed: 'Robustness study completed',
  robustness_study_stopped: 'Robustness study stopped',
  export_generated: 'Export generated',
};

export default function ExperimentDetailPage() {
  const router = useRouter();
  const params = useParams();
  const experimentId = params.id as string;

  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [locking, setLocking] = useState(false);
  const [continuingVersionId, setContinuingVersionId] = useState<string | null>(null);
  const [showLockConfirm, setShowLockConfirm] = useState(false);
  const [error, setError] = useState('');
  const [noteText, setNoteText] = useState('');
  const [showTimeline, setShowTimeline] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [diffResult, setDiffResult] = useState<{ diffs: DiffEntry[]; version_a_number: number; version_b_number: number } | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [methodReview, setMethodReview] = useState<MethodReview | null>(null);
  const [methodReviewLoading, setMethodReviewLoading] = useState(false);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const [savedComparisons, setSavedComparisons] = useState<SavedComparisonSummary[]>([]);
  const [dataVersion, setDataVersion] = useState(0);
  // A failed initial load is not an empty experiment. Keep the reason so the page
  // can tell "you do not have this research" apart from "the service is down",
  // and offer a retry instead of silently rendering a not-found screen.
  const [loadFailure, setLoadFailure] = useState<{ kind: 'not_found' | 'forbidden' | 'unavailable'; status: number | null } | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadData();
  }, [experimentId, router]);

  const loadData = async () => {
    try {
      const [exp, runsData, versionsData, timelineData, notesData, reviewData, savedComparisonsData] = await Promise.all([
        api.experiments.get(experimentId),
        api.runs.list(experimentId),
        api.experiments.versions(experimentId),
        api.timeline.get(experimentId),
        api.notes.list(experimentId),
        api.experiments.methodReview(experimentId).catch(() => null),
        api.savedComparisons.list(experimentId).catch(() => []),
      ]);
      setExperiment(exp);
      setRuns(runsData);
      setVersions(versionsData);
      setTimeline(timelineData);
      setNotes(notesData);
      setMethodReview(reviewData);
      setSavedComparisons(savedComparisonsData);
      setDataVersion((v) => v + 1);
      // A reload that succeeded must not keep showing an earlier load failure.
      setError('');
      setLoadFailure(null);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : null;
      if (experiment) {
        // The research is already on screen. A failed refresh keeps the visible
        // data and says so, instead of replacing the page with an error screen.
        setError('Could not refresh this experiment. The data shown may be out of date.');
        return;
      }
      if (status === 404) {
        setLoadFailure({ kind: 'not_found', status });
      } else if (status === 403) {
        setLoadFailure({ kind: 'forbidden', status });
      } else {
        setLoadFailure({ kind: 'unavailable', status });
      }
      setError('');
    } finally {
      setLoading(false);
    }
  };

  const retryLoad = () => {
    setLoading(true);
    setLoadFailure(null);
    loadData();
  };

  const handleRunBacktest = async () => {
    if (!experiment?.current_version_id) {
      setError('No version is available to run.');
      return;
    }
    setRunning(true);
    setError('');

    try {
      const run = await api.runs.create(experiment.current_version_id);
      setRuns([run, ...runs]);
      const [timelineData, notesData] = await Promise.all([
        api.timeline.get(experimentId),
        api.notes.list(experimentId),
      ]);
      setTimeline(timelineData);
    } catch (err: any) {
      setError(err.message || 'The backtest could not be started.');
    } finally {
      setRunning(false);
    }
  };

  /**
   * Freezing the current version is the product's only gate into replay: the
   * backend refuses to create a simulation session on an unlocked version, and a
   * locked experiment can no longer be edited — new parameters need a new version.
   * The dedicated lock endpoint carries no strategy fields, so a lock can never
   * smuggle in a parameter change.
   */
  const handleLock = async () => {
    if (!experiment) return;
    if (!experiment.current_version_id) {
      setError('This experiment has no strategy version to lock yet.');
      return;
    }
    setLocking(true);
    setError('');
    try {
      await api.experiments.lockVersion(experimentId, experiment.current_version_id, {
        confirm: true,
      });
      setShowLockConfirm(false);
      await loadData();
    } catch (err: any) {
      setError(err.message || 'The strategy could not be locked.');
    } finally {
      setLocking(false);
    }
  };

  /**
   * A revision of a locked experiment is a lineage record and cannot become the
   * current version, so it cannot be locked or replayed in place. Continuing
   * copies it into a new draft experiment whose version 1 is lockable, and the
   * source experiment is left untouched.
   */
  const handleContinueVersion = async (versionId: string) => {
    setContinuingVersionId(versionId);
    setError('');
    try {
      const continuation = await api.experiments.continueVersion(experimentId, versionId);
      router.push(`/experiments/${continuation.id}`);
    } catch (err: any) {
      setError(err.message || 'The research could not be continued in a new experiment.');
      setContinuingVersionId(null);
    }
  };

  const handleAddNote = async () => {
    if (!noteText.trim()) return;
    try {
      const note = await api.notes.create(experimentId, { content: noteText.trim() });
      setNotes([note, ...notes]);
      setNoteText('');
      const timelineData = await api.timeline.get(experimentId);
      setTimeline(timelineData);
    } catch (err: any) {
      setError(err.message || 'The note could not be added.');
    }
  };

  const handleCompare = async (v1Id: string, v2Id: string) => {
    setDiffLoading(true);
    setDiffResult(null);
    try {
      const result = await api.versions.compare(v1Id, v2Id);
      setDiffResult(result);
    } catch (err: any) {
      setError(err.message || 'The versions could not be compared.');
    } finally {
      setDiffLoading(false);
    }
  };

  const handleExport = async () => {
    try {
      const data = await api.experiments.export(experimentId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `alethra-export-${experimentId.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err.message || 'The export could not be generated.');
    }
  };

  const handleRefreshReview = async () => {
    setMethodReviewLoading(true);
    try {
      const reviewData = await api.experiments.methodReview(experimentId);
      setMethodReview(reviewData);
    } catch (err: any) {
      setError(err.message || 'The method review could not be loaded.');
    } finally {
      setMethodReviewLoading(false);
    }
  };

  const STATUS_STYLE: Record<string, { bg: string; color: string; label: string }> = {
    PASS: { bg: 'rgba(34,197,94,0.14)', color: 'var(--positive)', label: 'Pass' },
    WARNING: { bg: 'rgba(245,158,11,0.14)', color: 'var(--warning)', label: 'Warning' },
    INSUFFICIENT_INFORMATION: { bg: 'var(--surface-2)', color: 'var(--text-secondary)', label: 'Insufficient information' },
    NOT_APPLICABLE: { bg: 'var(--surface-2)', color: 'var(--text-secondary)', label: 'Not applicable' },
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px' }} role="status" aria-live="polite">Loading…</div>;
  }

  if (loadFailure) {
    const isMissing = loadFailure.kind === 'not_found';
    const isForbidden = loadFailure.kind === 'forbidden';
    return (
      <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px', maxWidth: '640px' }}>
        <h1 style={{ marginBottom: '12px' }} data-testid="experiment-load-error-title">
          {isMissing
            ? 'Experiment not found'
            : isForbidden
              ? 'You do not have access to this experiment'
              : 'This experiment could not be loaded'}
        </h1>
        <p role="alert" data-testid="experiment-load-error" style={{ color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          {isMissing
            ? 'This experiment does not exist, or it belongs to another account. This failed load did not change your research content — check the link or go back to your experiment list.'
            : isForbidden
              ? 'This account cannot open this experiment. This failed load did not change your research content. If you expected access, contact the account owner.'
              : `Alethra could not reach the research service${loadFailure.status ? ` (server response ${loadFailure.status})` : ' (no response from the server)'}. This is a loading failure, not an empty experiment, and this failed load did not change your research content. Check your connection and try again.`}
        </p>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '20px' }}>
          {!isMissing && !isForbidden && (
            <button
              type="button"
              className="btn btn-primary"
              data-testid="experiment-load-retry"
              onClick={retryLoad}
            >
              Try again
            </button>
          )}
          <Link
            href="/dashboard"
            className="btn"
            style={{ backgroundColor: 'var(--surface-2)', textDecoration: 'none' }}
          >
            Back to my experiments
          </Link>
        </div>
      </div>
    );
  }

  if (!experiment) {
    return <div className="container" style={{ paddingTop: '40px' }}>Experiment not found.</div>;
  }

  const latestRun = runs.find(r => r.status === 'completed');
  const currentVersion = versions.find((v) => v.id === experiment.current_version_id) ?? null;
  // Crypto instruments are priced from venue bars stored on this server, so the
  // page must not call every backtest "sample data".
  const isVenueDataExperiment = Array.isArray(currentVersion?.strategy_spec?.asset_ids)
    && currentVersion.strategy_spec.asset_ids.some(
      (id: unknown) => typeof id === 'string' && id.startsWith('CRYPTO:')
    );
  const isLocked = experiment.status === 'locked';
  // An experiment can carry the locked status while its current version has no
  // lock stamp (legacy rows). Replay needs the version stamp, so offer the lock
  // action in that case instead of a dead end.
  const currentVersionLocked = Boolean(currentVersion?.locked_at);

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href="/dashboard" style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          Back to my experiments
        </Link>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '30px' }}>
        <div style={{ minWidth: 0, flex: '1 1 260px' }}>
          <h1 style={{ marginBottom: '8px', overflowWrap: 'anywhere' }}>{experiment.title}</h1>
          <p style={{ color: 'var(--text-secondary)' }}>
            Created {formatDate(experiment.created_at)}
          </p>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
          {experiment.status === 'locked' && (
            <Link href={`/experiments/${experimentId}/simulation`} data-testid="exp-simulation-link">
              <button className="btn btn-primary" style={{ fontSize: '13px' }}>
                Forward simulation
              </button>
            </Link>
          )}
          {(!isLocked || !currentVersionLocked) && (
            <button
              onClick={() => setShowLockConfirm((open) => !open)}
              className="btn"
              data-testid="exp-lock-btn"
              style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
            >
              {showLockConfirm ? 'Cancel lock' : 'Lock strategy'}
            </button>
          )}
          {isLocked && experiment.current_version_id && (
            <button
              onClick={() => handleContinueVersion(experiment.current_version_id!)}
              className="btn"
              data-testid="exp-continue-current"
              disabled={continuingVersionId !== null}
              style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
            >
              {continuingVersionId === experiment.current_version_id
                ? 'Continuing…'
                : 'Continue in new experiment'}
            </button>
          )}
          <button onClick={handleExport} className="btn" style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}>
            Export
          </button>
          <span
            className={statusPillClass(experiment.status)}
            style={{ padding: '6px 16px', fontSize: '14px' }}
          >
            {experiment.status === 'draft' ? 'Draft' : experiment.status === 'locked' ? 'Locked' : experiment.status}
          </span>
        </div>
      </div>

      {error && <div className="alert alert-warning">{error}</div>}

      {experiment.hypothesis && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <h3 style={{ marginBottom: '12px' }}>Research hypothesis</h3>
          <p style={{ color: 'var(--text-secondary)' }}>{experiment.hypothesis}</p>
          {experiment.falsification_conditions && (
            <>
              <h4 style={{ marginTop: '16px', marginBottom: '8px', fontSize: '14px' }}>Falsification conditions</h4>
              <p style={{ color: 'var(--text-secondary)' }}>{experiment.falsification_conditions}</p>
            </>
          )}
        </div>
      )}

      {(!isLocked || !currentVersionLocked) && showLockConfirm && (
        <div className="card" style={{ marginBottom: '20px', borderLeft: '3px solid var(--primary)' }}>
          <h3 style={{ marginTop: 0, marginBottom: '8px' }}>
            {currentVersion ? `Lock version ${currentVersion.version_number}` : 'No version to lock yet'}
          </h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px', marginTop: 0, marginBottom: '16px' }}>
            Locking freezes this version&apos;s strategy parameters and data cutoff. Changing parameters requires a new
            version. Forward simulation, the replay cursor and paper fills all follow this version.
          </p>
          {isLocked && !currentVersionLocked && currentVersion && (
            <p
              data-testid="exp-lock-repair-note"
              style={{ color: 'var(--warning)', fontSize: '13px', marginTop: 0, marginBottom: '16px' }}
            >
              This experiment is marked locked, but version {currentVersion.version_number} has no lock stamp, so a
              replay session cannot start yet. Confirming freezes it now; the lock time is recorded as the moment you
              confirm, never backdated.
            </p>
          )}

          {currentVersion ? (
            <div
              data-testid="exp-lock-facts"
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: '12px',
                marginBottom: '16px',
                fontSize: '13px',
              }}
            >
              <div>
                <div style={{ color: 'var(--text-muted)' }}>Instruments</div>
                <strong>{(currentVersion.strategy_spec?.asset_ids ?? []).join(', ') || 'Not provided'}</strong>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)' }}>Template</div>
                <strong>{currentVersion.strategy_spec?.template_type ?? 'Not provided'}</strong>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)' }}>Period</div>
                <strong>
                  {currentVersion.strategy_spec?.start_date ?? '?'} to {currentVersion.strategy_spec?.end_date ?? '?'}
                </strong>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)' }}>Data cutoff</div>
                <strong>{currentVersion.strategy_spec?.end_date ?? 'Not provided'}</strong>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)' }}>Starting cash</div>
                <strong>
                  {currentVersion.strategy_spec?.initial_cash ?? 'Not provided'} {currentVersion.strategy_spec?.currency ?? ''}
                </strong>
              </div>
              <div>
                <div style={{ color: 'var(--text-muted)' }}>Commission / slippage (per fill)</div>
                <strong>
                  {formatRatePercent(currentVersion.strategy_spec?.commission_rate)} /{' '}
                  {formatRatePercent(currentVersion.strategy_spec?.slippage_rate)}
                </strong>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                  Share of trade value; 0.10% means 0.001 in the raw setting
                </div>
              </div>
            </div>
          ) : (
            <p style={{ color: 'var(--warning)', fontSize: '13px' }}>
              This experiment has no strategy version to lock yet. Create an experiment or change a parameter to
              produce one.
            </p>
          )}

          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={handleLock}
              className="btn btn-primary"
              data-testid="exp-lock-confirm"
              disabled={locking || !currentVersion}
            >
              {locking ? 'Locking…' : 'Confirm lock'}
            </button>
            <button
              onClick={() => setShowLockConfirm(false)}
              className="btn"
              data-testid="exp-lock-cancel"
              style={{ backgroundColor: 'var(--surface-2)' }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {experiment.current_version_id && versions.length > 0 && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <h3 style={{ margin: 0 }}>Current strategy version</h3>
            <button
              onClick={() => setShowVersions(!showVersions)}
              className="btn"
              style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
            >
              {showVersions ? 'Hide version history' : `View all versions (${versions.length})`}
            </button>
          </div>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px', margin: 0 }}>
            Version {versions.find(v => v.id === experiment.current_version_id)?.version_number || versions.length}
            {versions.find(v => v.id === experiment.current_version_id)?.change_reason && (
              <> · Reason for change: {versions.find(v => v.id === experiment.current_version_id)?.change_reason}</>
            )}
          </p>
          {experiment.continued_from_experiment_id && (
            <p data-testid="exp-lineage-note" style={{ color: 'var(--text-muted)', fontSize: '13px', marginTop: '8px' }}>
              Continued from{' '}
              <Link
                href={`/experiments/${experiment.continued_from_experiment_id}`}
                style={{ color: 'var(--primary-text)' }}
              >
                another experiment
              </Link>
              ; the source experiment was not changed.
            </p>
          )}
          {showVersions && (
            <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-strong)', paddingTop: '16px' }}>
              <p style={{ color: 'var(--text-muted)', fontSize: '13px', marginTop: 0, marginBottom: '12px' }}>
                Continuing a version copies it into a new draft experiment whose first version can be locked,
                simulated and replayed. A revision of a locked experiment cannot be locked in place, and this
                experiment is never modified by continuing it.
              </p>
              {versions.map((v, i) => (
                <div key={v.id} style={{ padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', marginBottom: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                    <div>
                      <strong>Version {v.version_number}</strong>
                      {v.change_reason && <span style={{ marginLeft: '8px', color: 'var(--text-secondary)' }}>· {v.change_reason}</span>}
                      <span style={{ marginLeft: '8px', fontSize: '12px', color: v.locked_at ? 'var(--positive)' : 'var(--text-muted)' }}>
                        {v.locked_at ? '· locked' : '· not locked'}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        {formatDate(v.created_at)}
                      </span>
                      {(v.id !== experiment.current_version_id || isLocked) && (
                        <button
                          type="button"
                          className="btn"
                          data-testid={`exp-continue-version-${v.version_number}`}
                          onClick={() => handleContinueVersion(v.id)}
                          disabled={continuingVersionId !== null}
                          style={{ backgroundColor: 'var(--surface-3, var(--surface-2))', fontSize: '13px' }}
                        >
                          {continuingVersionId === v.id ? 'Continuing…' : 'Continue in new experiment'}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {versions.length > 0 && (
        <RevisionPanel
          experimentId={experimentId}
          experimentStatus={experiment.status}
          currentVersionId={experiment.current_version_id}
          versions={versions}
          runs={runs}
          onChanged={loadData}
        />
      )}

      {versions.length > 0 && (
        <RobustnessPanel
          experimentId={experimentId}
          currentVersionId={experiment.current_version_id}
          versions={versions}
          onChanged={loadData}
        />
      )}

      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2 style={{ margin: 0 }}>Backtest results</h2>
          <button
            onClick={handleRunBacktest}
            className="btn btn-primary"
            disabled={running || !experiment.current_version_id}
          >
            {running ? brand.ui.experiment.running : brand.ui.experiment.runBacktest}
          </button>
        </div>

        {!latestRun ? (
          <div style={{ textAlign: 'center', padding: '40px' }}>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '12px' }}>
              No backtest has been run yet
            </p>
            <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>
              Use the button above to start testing your hypothesis.<br />
              {isVenueDataExperiment
                ? 'Backtests read the completed venue bars stored on this server; results are for research only.'
                : 'Backtests run on historical sample data; results are for research only.'}
            </p>
          </div>
        ) : (
          <div>
            {latestRun.warnings && latestRun.warnings.length > 0 && (
              <div className="alert alert-warning" style={{ marginBottom: '16px' }}>
                <strong>Warnings:</strong>
                <ul style={{ marginTop: '8px', marginLeft: '20px' }}>
                  {latestRun.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
              {latestRun.metrics?.total_return_pct != null && (
                <div style={{ padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Total return</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{latestRun.metrics.total_return_pct.toFixed(2)}%</div>
                </div>
              )}
              {latestRun.metrics?.final_equity != null && (
                <div style={{ padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Final equity</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>${latestRun.metrics.final_equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
                </div>
              )}
              {latestRun.metrics?.max_drawdown_pct != null && (
                <div style={{ padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Max drawdown</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{latestRun.metrics.max_drawdown_pct.toFixed(2)}%</div>
                </div>
              )}
              {latestRun.metrics?.total_trades != null && (
                <div style={{ padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Trades</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{latestRun.metrics.total_trades}</div>
                </div>
              )}
            </div>

            {latestRun.methodology_json && (
              <div style={{ marginTop: '16px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', fontSize: '13px' }}>
                <strong>Method summary:</strong>
                <span style={{ marginLeft: '8px', color: 'var(--text-muted)' }}>
                  Engine v{latestRun.methodology_json.engine_version} |
                  Template: {latestRun.methodology_json.template_type}
                </span>
              </div>
            )}

            {latestRun.config_hash && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
                config_hash: {latestRun.config_hash.slice(0, 16)}...
                {latestRun.dataset_checksum && <> | dataset: {latestRun.dataset_checksum.slice(0, 16)}...</>}
              </div>
            )}

            {latestRun.result_json?.data_provider && (
              <div style={{ marginTop: '12px', padding: '10px', backgroundColor: 'rgba(34,197,94,0.14)', borderRadius: '6px', fontSize: '13px' }}>
                <strong>Data source:</strong>
                <span style={{ marginLeft: '8px' }}>
                  {latestRun.result_json.data_provider}
                  {latestRun.result_json.feed_mode && ` (${latestRun.result_json.feed_mode})`}
                  {latestRun.result_json.data_source_used && ` — ${latestRun.result_json.data_source_used}`}
                </span>
                {latestRun.result_json.data_nature && (
                  <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
                    Data nature:{' '}
                    {Object.entries(latestRun.result_json.data_nature)
                      .map(([k, v]) => `${label(DATA_NATURE_LABELS, String(v))} (${k})`)
                      .join(', ')}
                  </div>
                )}
              </div>
            )}

            {latestRun.result_json?.daily_accounting?.length > 0 && (
              <div style={{ marginTop: '16px' }}>
                <h3 style={{ fontSize: '16px', marginBottom: '8px' }}>Daily accounting</h3>
                <div style={{ maxHeight: '400px', overflow: 'auto', border: '1px solid var(--border-strong)', borderRadius: '6px' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                    <thead style={{ position: 'sticky', top: 0, backgroundColor: 'var(--surface-2)', zIndex: 1 }}>
                      <tr>
                        <th style={thStyle}>Date</th>
                        <th style={thStyle}>Cash</th>
                        <th style={thStyle}>Shares</th>
                        <th style={thStyle}>Close</th>
                        <th style={thStyle}>Position value</th>
                        <th style={thStyle}>Equity</th>
                        <th style={thStyle}>Trades</th>
                      </tr>
                    </thead>
                    <tbody>
                      {latestRun.result_json.daily_accounting.map((day: any, i: number) => (
                        <tr key={i} style={{ backgroundColor: i % 2 === 0 ? 'var(--text-white)' : 'var(--surface-2)' }}>
                          <td style={tdStyle}>{day.date}</td>
                          <td style={tdStyle}>${day.cash.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}</td>
                          <td style={tdStyle}>{day.shares}</td>
                          <td style={tdStyle}>${day.close_price.toFixed(2)}</td>
                          <td style={tdStyle}>${day.position_value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}</td>
                          <td style={{ ...tdStyle, fontWeight: 'bold' }}>${day.equity.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}</td>
                          <td style={tdStyle}>
                            {day.transactions?.map((t: any, j: number) => (
                              <span key={j} style={{ display: 'inline-block', padding: '1px 6px', borderRadius: '3px', fontSize: '11px', marginRight: '4px', backgroundColor: t.side === 'BUY' ? 'rgba(34,197,94,0.14)' : 'rgba(239,68,68,0.12)', color: t.side === 'BUY' ? 'var(--positive)' : 'var(--negative)' }}>
                                {t.side === 'BUY' ? 'Buy' : 'Sell'} {t.quantity}@${t.execution_price.toFixed(2)}
                              </span>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div style={{ marginTop: '16px', padding: '12px', backgroundColor: 'rgba(245,158,11,0.14)', borderRadius: '6px', fontSize: '14px' }}>
              {isVenueDataExperiment ? (
                <>
                  <strong>Note:</strong> These results come from venue bars already stored on this server — internal
                  research and simulated fills only, not cleared for external redistribution. They do not predict
                  future performance. Alethra is a research tool, not investment advice.
                </>
              ) : (
                <>
                  <strong>Note:</strong> These results come from historical sample data and do not predict future
                  performance. Alethra is a research tool, not investment advice.
                </>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Workflow Continuity */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <h3 style={{ marginBottom: '16px' }}>Research log</h3>
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
          <button
            onClick={() => setShowTimeline(!showTimeline)}
            className="btn"
            style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
          >
            {showTimeline ? 'Hide timeline' : `View timeline (${timeline.length} events)`}
          </button>
          <Link href={`/experiments/${experimentId}/simulation`} className="btn" style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px', textDecoration: 'none' }}>
            Forward simulation →
          </Link>
        </div>

        {showTimeline && timeline.length > 0 && (
          <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-strong)', paddingTop: '16px' }}>
            {timeline.slice(0, 10).map((event) => (
              <div key={event.id} style={{ padding: '10px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', marginBottom: '8px', fontSize: '13px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <strong>{EVENT_LABELS[event.event_type] || event.event_type}</strong>
                    {event.payload_json?.summary && (
                      <span style={{ marginLeft: '8px', color: 'var(--text-secondary)' }}>· {event.payload_json.summary}</span>
                    )}
                  </div>
                  <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                    {formatDateTime(event.created_at)}
                  </span>
                </div>
              </div>
            ))}
            {timeline.length > 10 && (
              <p style={{ color: 'var(--text-muted)', fontSize: '12px', textAlign: 'center', marginTop: '12px' }}>
                Showing the latest 10 of {timeline.length} events
              </p>
            )}
          </div>
        )}

        {showTimeline && timeline.length === 0 && (
          <div style={{ marginTop: '16px', textAlign: 'center', padding: '20px' }}>
            <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No timeline events yet</p>
          </div>
        )}
      </div>

      {/* Method Review */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2>Method review</h2>
          <button
            onClick={handleRefreshReview}
            className="btn"
            style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
            disabled={methodReviewLoading}
          >
            {methodReviewLoading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {!methodReview ? (
          <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '20px' }}>
            The method review could not be loaded right now.
          </p>
        ) : (
          <div>
            <div style={{ display: 'flex', gap: '16px', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '16px', flexWrap: 'wrap' }}>
              <span>Rule set v{methodReview.rule_set_version}</span>
              {methodReview.run_id && (
                <span>Reviewed run: {methodReview.run_id.slice(0, 8)}...</span>
              )}
              {methodReview.version_id && (
                <span>Version: {methodReview.version_id.slice(0, 8)}...</span>
              )}
              {!methodReview.run_id && (
                <span style={{ color: 'var(--warning)' }}>No completed backtest run yet</span>
              )}
              <span>Generated {formatDateTime(methodReview.generated_at)}</span>
            </div>

            <div style={{ padding: '10px 12px', backgroundColor: 'rgba(245,158,11,0.10)', borderRadius: '6px', fontSize: '12px', color: 'var(--warning)', marginBottom: '12px', lineHeight: '1.6' }}>
              <strong>Data source note:</strong> RC-001 and RC-002 check the experiment&apos;s current text (hypothesis,
              original idea, falsification conditions). Those fields stay editable, so the reviewed text may differ
              from what it was when the backtest ran. This is not a snapshot of the historical text.
            </div>

            {methodReview.completed_at_available === false && (
              <div style={{ padding: '10px 12px', backgroundColor: 'rgba(245,158,11,0.14)', borderRadius: '6px', fontSize: '12px', color: 'var(--warning)', marginBottom: '12px', lineHeight: '1.6' }}>
                <strong>Timestamp warning:</strong> The selected run has no completion timestamp. Runs are ordered
                by ID rather than by time, so &ldquo;latest&rdquo; does not mean most recent.
              </div>
            )}

            <div style={{ display: 'grid', gap: '12px' }}>
              {methodReview.checks.map((check) => {
                const style = STATUS_STYLE[check.status] || STATUS_STYLE.INSUFFICIENT_INFORMATION;
                return (
                  <div key={check.rule_id} style={{
                    padding: '16px',
                    backgroundColor: 'var(--surface-2)',
                    borderRadius: '6px',
                    borderLeft: `4px solid ${style.color}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                      <span style={{ fontWeight: '600', fontSize: '14px' }}>{check.rule_id}</span>
                      <span style={{
                        padding: '2px 10px',
                        borderRadius: '12px',
                        fontSize: '12px',
                        fontWeight: '500',
                        backgroundColor: style.bg,
                        color: style.color,
                      }}>
                        {style.label}
                      </span>
                    </div>
                    <div style={{ fontSize: '13px', color: 'var(--text-primary)', whiteSpace: 'pre-line', lineHeight: '1.6' }}>
                      {check.explanation}
                    </div>
                    {check.limitations.length > 0 && (
                      <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
                        {check.limitations.map((lim, i) => (
                          <div key={i}>• {lim}</div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div style={{ marginTop: '16px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', fontSize: '13px', color: 'var(--text-muted)' }}>
              <strong>Disclaimer:</strong> The method review is a deterministic rule check, not investment advice. A
              PASS does not mean the strategy is profitable or suitable for live trading.{' '}
              {isVenueDataExperiment
                ? 'This experiment priced venue bars stored on this server; the method review does not verify their quality.'
                : 'This system uses sample data, not live market data.'}
            </div>
          </div>
        )}
      </div>

      {/* Research Notes */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <h3 style={{ marginBottom: '16px' }}>Research notes</h3>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          <input
            type="text"
            className="input"
            aria-label="Research note"
            placeholder="Write down your research thinking…"
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddNote()}
            style={{ flex: 1 }}
          />
          <button onClick={handleAddNote} className="btn btn-primary" disabled={!noteText.trim()}>
            Add
          </button>
        </div>
        {notes.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No notes yet</p>
        ) : (
          <div style={{ display: 'grid', gap: '8px' }}>
            {notes.map((note) => (
              <div key={note.id} style={{ padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', borderLeft: '3px solid var(--primary)' }}>
                <p style={{ fontSize: '14px', marginBottom: '4px' }}>{note.content}</p>
                <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  {formatDateTime(note.created_at)}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Version History */}
      {versions.length > 0 && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <h3>Version history ({versions.length})</h3>
            <button
              onClick={() => setShowVersions(!showVersions)}
              className="btn"
              style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
            >
              {showVersions ? 'Collapse' : 'Expand'}
            </button>
          </div>
          {showVersions && (
            <div>
              <div style={{ display: 'grid', gap: '8px', marginBottom: '16px' }}>
                {versions.map((v) => (
                  <div key={v.id} style={{
                    padding: '12px',
                    backgroundColor: v.id === experiment.current_version_id ? 'rgba(59,130,246,0.16)' : 'var(--surface-2)',
                    borderRadius: '6px',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}>
                    <div>
                      <div style={{ fontSize: '14px', fontWeight: '500' }}>
                        v{v.version_number}
                        {v.id === experiment.current_version_id && (
                          <span style={{ marginLeft: '8px', fontSize: '12px', color: 'var(--primary-text)' }}>(current)</span>
                        )}
                      </div>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {formatDateTime(v.created_at)}
                        {v.change_reason && <> — {v.change_reason}</>}
                      </div>
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      {v.config_hash ? v.config_hash.slice(0, 12) + '...' : ''}
                    </div>
                  </div>
                ))}
              </div>
              {versions.length >= 2 && (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <select id="compare-a" className="input" style={{ flex: 1, fontSize: '13px' }} defaultValue={versions[versions.length - 1]?.id}>
                    {versions.map(v => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
                  </select>
                  <span style={{ color: 'var(--text-muted)' }}>vs</span>
                  <select id="compare-b" className="input" style={{ flex: 1, fontSize: '13px' }} defaultValue={versions[0]?.id}>
                    {versions.map(v => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
                  </select>
                  <button
                    className="btn btn-primary"
                    style={{ fontSize: '13px' }}
                    onClick={() => {
                      const a = (document.getElementById('compare-a') as HTMLSelectElement)?.value;
                      const b = (document.getElementById('compare-b') as HTMLSelectElement)?.value;
                      if (a && b && a !== b) handleCompare(a, b);
                    }}
                  >
                    Compare
                  </button>
                </div>
              )}
              {diffLoading && <p style={{ marginTop: '12px', color: 'var(--text-secondary)' }}>Comparing…</p>}
              {diffResult && (
                <div style={{ marginTop: '12px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                  <h4 style={{ marginBottom: '8px', fontSize: '14px' }}>
                    v{diffResult.version_a_number} → v{diffResult.version_b_number} differences
                  </h4>
                  {diffResult.diffs.length === 0 ? (
                    <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>No differences</p>
                  ) : (
                    <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                          <th style={{ textAlign: 'left', padding: '4px 8px' }}>Field</th>
                          <th style={{ textAlign: 'left', padding: '4px 8px' }}>Old value</th>
                          <th style={{ textAlign: 'left', padding: '4px 8px' }}>New value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {diffResult.diffs.map((d, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid var(--border-strong)' }}>
                            <td style={{ padding: '4px 8px', fontWeight: '500' }}>{d.field}</td>
                            <td style={{ padding: '4px 8px', color: 'var(--negative)' }}>{JSON.stringify(d.old_value)}</td>
                            <td style={{ padding: '4px 8px', color: 'var(--positive)' }}>{JSON.stringify(d.new_value)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Research Timeline */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3>Research timeline ({timeline.length} events)</h3>
          <button
            onClick={() => setShowTimeline(!showTimeline)}
            className="btn"
            style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
            data-testid="exp-timeline-toggle"
          >
            {showTimeline ? 'Collapse' : 'Expand'}
          </button>
        </div>
        {showTimeline && (
          <div>
            {timeline.length === 0 ? (
              <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No events yet</p>
            ) : (
              <div style={{ display: 'grid', gap: '4px' }}>
                {timeline.map((evt) => (
                  <div key={evt.id} style={{
                    padding: '8px 12px',
                    backgroundColor: evt.recorded_before_outcome ? 'var(--surface-2)' : 'rgba(245,158,11,0.10)',
                    borderRadius: '4px',
                    borderLeft: `3px solid ${evt.actor_type === 'system' ? 'var(--warning)' : evt.rules_locked ? 'var(--primary)' : 'var(--primary-text)'}`,
                    fontSize: '13px',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontWeight: '500' }}>
                        {EVENT_LABELS[evt.event_type] || evt.event_type}
                      </span>
                      <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                        {formatDateTime(evt.created_at)}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: '8px', marginTop: '2px', fontSize: '11px', color: 'var(--text-muted)' }}>
                      <span>{evt.entity_type}</span>
                      {evt.actor_type === 'system' && <span style={{ color: 'var(--warning)' }}>system</span>}
                      {evt.rules_locked && <span style={{ color: 'var(--primary)' }}>locked</span>}
                      {!evt.recorded_before_outcome && <span style={{ color: 'var(--warning)' }}>post-outcome</span>}
                      {evt.data_cutoff_at && <span>cutoff: {evt.data_cutoff_at}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <ResearchRecordPanel experimentId={experimentId} refreshToken={dataVersion} />

      {/* Saved Comparisons */}
      {savedComparisons.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0, marginBottom: '16px' }}>Saved comparisons</h3>
          <div style={{ display: 'grid', gap: '8px' }}>
            {savedComparisons.map((saved) => {
              const outsideSide = saved.run_a_experiment_id !== experimentId
                ? 'Run A'
                : saved.run_b_experiment_id !== experimentId
                  ? 'Run B'
                  : null;
              const outsideExperimentId = outsideSide === 'Run A'
                ? saved.run_a_experiment_id
                : saved.run_b_experiment_id;
              return (
                <Link
                  key={saved.id}
                  href={`/experiments/${experimentId}/runs/compare?saved_comparison_id=${saved.id}`}
                  data-testid="saved-comparison"
                  style={{
                    padding: '12px',
                    backgroundColor: 'var(--surface-2)',
                    borderRadius: '6px',
                    textDecoration: 'none',
                    color: 'inherit',
                    border: '1px solid var(--border-strong)',
                  }}
                >
                  <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '4px' }}>
                    {saved.purpose}
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    Run A: {saved.run_a_id.slice(0, 8)}... → Run B: {saved.run_b_id.slice(0, 8)}...
                  </div>
                  {saved.scope === 'cross_experiment' && outsideSide && (
                    <div
                      data-testid="saved-comparison-cross-experiment"
                      style={{ fontSize: '11px', color: 'var(--warning)', marginTop: '4px' }}
                    >
                      Cross-experiment: {outsideSide} belongs to experiment{' '}
                      {outsideExperimentId.slice(0, 8)}..., not the one this page shows.
                      Their conditions may differ; open it to see what is comparable.
                    </div>
                  )}
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                    Updated {formatDateTime(saved.updated_at)}
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      {/* Run History */}
      {runs.length > 0 && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <h3 style={{ margin: 0 }}>Run history</h3>
            {selectedRuns.length === 2 && (
              <Link
                href={`/experiments/${experimentId}/runs/compare?run_a=${selectedRuns[0]}&run_b=${selectedRuns[1]}`}
                className="btn btn-primary"
                style={{ fontSize: '13px', textDecoration: 'none' }}
              >
                Compare selected runs
              </Link>
            )}
            {selectedRuns.length > 0 && selectedRuns.length !== 2 && (
              <button
                className="btn"
                style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
                onClick={() => setSelectedRuns([])}
              >
                Clear selection ({selectedRuns.length}/2)
              </button>
            )}
          </div>
          <div style={{ display: 'grid', gap: '8px' }}>
            {runs.map((run) => {
              const isSelected = selectedRuns.includes(run.id);
              const selectionFull = selectedRuns.length >= 2 && !isSelected;
              return (
                <div
                  key={run.id}
                  data-testid={`run-row-${run.id}`}
                  style={{
                    padding: '12px',
                    backgroundColor: isSelected ? 'rgba(59,130,246,0.16)' : 'var(--surface-2)',
                    borderRadius: '6px',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    border: isSelected ? '2px solid var(--primary-text)' : '2px solid transparent',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <input
                      type="checkbox"
                      data-testid={`run-select-${run.id}`}
                      aria-label={`Select run ${run.id.slice(0, 8)} for comparison`}
                      checked={isSelected}
                      disabled={selectionFull}
                      onChange={() => {
                        setSelectedRuns((prev) => {
                          if (prev.includes(run.id)) return prev.filter((id) => id !== run.id);
                          if (prev.length >= 2) return prev;
                          return [...prev, run.id];
                        });
                      }}
                      style={{ cursor: 'pointer' }}
                    />
                    <div>
                      <div style={{ fontSize: '14px', fontWeight: 500 }} data-testid={`run-status-${run.id}`}>
                        {describeRunStatus(run.status, run.started_at).label}
                      </div>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {run.started_at && formatDateTime(run.started_at)}
                      </div>
                      {describeRunStatus(run.status, run.started_at).stale && (
                        <div style={{ fontSize: '12px', color: 'var(--warning)' }}>
                          Still marked running after 30 minutes — treated as not in progress
                        </div>
                      )}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    {run.metrics?.total_return_pct != null && (
                      <div style={{ fontSize: '14px', fontWeight: 'bold' }}>
                        {run.metrics.total_return_pct.toFixed(2)}%
                      </div>
                    )}
                    <Link
                      href={`/experiments/${experimentId}/runs/${run.id}`}
                      data-testid={`run-detail-link-${run.id}`}
                      aria-label={`View results for run ${run.id.slice(0, 8)}`}
                      className="btn"
                      style={{
                        backgroundColor: 'var(--surface-2)',
                        fontSize: '12px',
                        padding: '6px 10px',
                        textDecoration: 'none',
                      }}
                    >
                      View results
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
          {selectedRuns.length === 2 && (
            <p style={{ marginTop: '12px', fontSize: '13px', color: 'var(--primary-text)' }}>
              Two runs selected. Use the button above to compare them.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
