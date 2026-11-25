'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError, api } from '@/lib/api';

interface SchedulerStatus {
  // What the deployment opted into, and what this worker is actually doing.
  // They differ whenever the flag is off, or when one of several API workers
  // started the service and another did not.
  enabled: boolean;
  running: boolean;
  interval_hours: number;
  timezone: string;
  job_type: string;
  next_run: string | null;
}

interface CollectionJob {
  job_id: string;
  user_id: string;
  status: string;
}

interface CollectionTriggerResult {
  // Named reason when nothing ran: kill_switch_active, no_accounts_served, …
  skipped: string | null;
  accounts: number;
  jobs: CollectionJob[];
  completed_at: string;
}

interface CollectionTick {
  id: string;
  user_id: string;
  status: string;
  scope: string | null;
  window: string | null;
  total_combos: number | null;
  studied: number | null;
  skipped: number | null;
  failed: number | null;
  errors: string[];
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

const SKIPPED_REASONS: Record<string, string> = {
  kill_switch_active:
    'The training kill switch is active, so no research quota was spent.',
  kill_switch_unreadable:
    'The kill switch could not be read, so the cycle was refused rather than guessed at.',
  no_accounts_served:
    'No account with an open skill account was found, so there was nothing to serve.',
  tick_error:
    'The cycle failed before any record could be written. See the server log.',
};

function errorDetail(err: unknown): { message: string; status: number } | null {
  if (!(err instanceof ApiError)) {
    return { message: err instanceof Error ? err.message : String(err), status: 0 };
  }
  const detail = err.detail as
    | { error?: string; message?: string; status?: SchedulerStatus }
    | undefined;
  if (detail && typeof detail.message === 'string') {
    return { message: detail.message, status: err.status };
  }
  return { message: err.message, status: err.status };
}

export default function AutoCollectionStatus() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [history, setHistory] = useState<CollectionTick[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      setStatus((await api.admin.collectionStatus()) as SchedulerStatus);
    } catch (err) {
      setError(errorDetail(err)?.message ?? String(err));
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setHistory((await api.admin.collectionHistory(20)) as CollectionTick[]);
    } catch {
      // Non-critical: the status card still tells the operator what is true.
    }
  }, []);

  useEffect(() => {
    void Promise.all([loadStatus(), loadHistory()]).finally(() => setLoading(false));
  }, [loadStatus, loadHistory]);

  const handleStart = async () => {
    setActionLoading('start');
    setError(null);
    setNotice(null);
    try {
      setStatus((await api.admin.collectionStart()) as SchedulerStatus);
      setNotice('The interval service is running in this process.');
    } catch (err) {
      const info = errorDetail(err);
      if (info?.status === 409) {
        // A POST cannot grant the deployment's opt-in. Say so instead of
        // leaving the operator thinking the button had been ignored.
        setNotice(info.message);
        await loadStatus();
      } else {
        setError(info?.message ?? String(err));
      }
    } finally {
      setActionLoading(null);
    }
  };

  const handleStop = async () => {
    setActionLoading('stop');
    setError(null);
    setNotice(null);
    try {
      setStatus((await api.admin.collectionStop()) as SchedulerStatus);
      setNotice(
        'Stopped. A cycle already in flight finishes and records its own result.',
      );
    } catch (err) {
      setError(errorDetail(err)?.message ?? String(err));
    } finally {
      setActionLoading(null);
    }
  };

  const handleTrigger = async () => {
    setActionLoading('trigger');
    setError(null);
    setNotice(null);
    try {
      const result = (await api.admin.collectionTrigger()) as CollectionTriggerResult;
      if (result.skipped) {
        setNotice(
          `Nothing was collected: ${SKIPPED_REASONS[result.skipped] ?? result.skipped}`,
        );
      } else {
        const failed = result.jobs.filter((job) => job.status !== 'completed').length;
        setNotice(
          failed === 0
            ? `Collected for ${result.accounts} account${result.accounts === 1 ? '' : 's'}.`
            : `Collected for ${result.accounts} accounts, ${failed} did not complete — see Recent Collection Ticks.`,
        );
      }
      await loadHistory();
    } catch (err) {
      setError(errorDetail(err)?.message ?? String(err));
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading...
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {error && (
        <div role="alert" style={{
          border: '1px solid var(--negative)',
          backgroundColor: 'rgba(226,99,110,0.1)',
          borderRadius: 6,
          padding: 12,
          fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {notice && !error && (
        <div role="status" style={{
          border: '1px solid var(--border-strong)',
          backgroundColor: 'var(--surface-2)',
          borderRadius: 6,
          padding: 12,
          fontSize: 13,
        }}>
          {notice}
        </div>
      )}

      {/* Scheduler Status Card */}
      <div style={{
        padding: '1.5rem',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        border: '1px solid var(--border-subtle)',
      }}>
        <h3 style={{ margin: '0 0 16px 0', fontSize: 16, fontWeight: 600 }}>
          Interval Data Collection
        </h3>

        {status && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Configured</span>
              <span style={{
                fontSize: 14,
                fontWeight: 600,
                color: status.enabled ? 'var(--positive, #36C7A0)' : 'var(--text-secondary)',
                padding: '4px 12px',
                backgroundColor: status.enabled ? 'rgba(54, 199, 160, 0.1)' : 'var(--surface-2)',
                borderRadius: 4,
              }}>
                {status.enabled ? 'Opted in' : 'Not opted in'}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>This worker</span>
              <span style={{
                fontSize: 14,
                fontWeight: 600,
                color: status.running ? 'var(--positive, #36C7A0)' : 'var(--text-primary)',
                padding: '4px 12px',
                backgroundColor: status.running ? 'rgba(54, 199, 160, 0.1)' : 'var(--surface-2)',
                borderRadius: 4,
              }}>
                {status.running ? 'Running' : 'Stopped'}
              </span>
            </div>

            {!status.enabled && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                AUTO_COLLECTION_ENABLED is false for this deployment, so no
                background cycle is scheduled here and the start button below
                cannot change that — it would spend market-data quota the
                deployment declined.
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Interval</span>
              <span style={{ fontSize: 14, fontWeight: 500 }}>
                Every {status.interval_hours} hours ({status.timezone})
              </span>
            </div>

            {status.running && status.next_run && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Next cycle</span>
                <span style={{ fontSize: 14, fontWeight: 500 }}>
                  {new Date(status.next_run).toLocaleString()}
                </span>
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              {!status.running ? (
                <button
                  onClick={() => void handleStart()}
                  disabled={actionLoading === 'start'}
                  style={{
                    padding: '8px 16px',
                    backgroundColor: 'var(--primary)',
                    color: 'white',
                    border: 'none',
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: actionLoading === 'start' ? 'not-allowed' : 'pointer',
                    opacity: actionLoading === 'start' ? 0.6 : 1,
                  }}
                >
                  {actionLoading === 'start' ? 'Starting...' : 'Start In This Worker'}
                </button>
              ) : (
                <button
                  onClick={() => void handleStop()}
                  disabled={actionLoading === 'stop'}
                  style={{
                    padding: '8px 16px',
                    backgroundColor: 'var(--negative, #E2636E)',
                    color: 'white',
                    border: 'none',
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: actionLoading === 'stop' ? 'not-allowed' : 'pointer',
                    opacity: actionLoading === 'stop' ? 0.6 : 1,
                  }}
                >
                  {actionLoading === 'stop' ? 'Stopping...' : 'Stop In This Worker'}
                </button>
              )}

              <button
                onClick={() => void handleTrigger()}
                disabled={actionLoading === 'trigger'}
                style={{
                  padding: '8px 16px',
                  backgroundColor: 'var(--surface-2)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 6,
                  fontSize: 13,
                  fontWeight: 500,
                  cursor: actionLoading === 'trigger' ? 'not-allowed' : 'pointer',
                  opacity: actionLoading === 'trigger' ? 0.6 : 1,
                }}
              >
                {actionLoading === 'trigger' ? 'Collecting...' : 'Run One Cycle Now'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Recorded ticks */}
      <div style={{
        padding: '1.5rem',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        border: '1px solid var(--border-subtle)',
      }}>
        <h3 style={{ margin: '0 0 16px 0', fontSize: 16, fontWeight: 600 }}>
          Recent Collection Ticks
        </h3>

        {history.length === 0 ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
            No collection cycle has been recorded yet. Each cycle studies the
            instrument grid once per account and leaves one row here, including
            the ones that were refused or skipped.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Window</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Trigger</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Account</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Result</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Studied / Skipped / Failed</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Completed</th>
                </tr>
              </thead>
              <tbody>
                {history.map((tick) => (
                  <tr key={tick.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '8px 12px', color: 'var(--text-primary)' }}>
                      {tick.window ?? 'manual request'}
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-secondary)' }}>
                      {tick.scope === 'interval' ? 'Interval' : 'Manual'}
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-secondary)' }} title={tick.user_id}>
                      {tick.user_id.slice(0, 8)}…
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{
                        fontWeight: 600,
                        color: tick.status === 'completed'
                          ? 'var(--positive, #36C7A0)'
                          : tick.status === 'partial'
                            ? 'var(--warning, #E8A33D)'
                            : 'var(--negative, #E2636E)',
                      }}>
                        {tick.status}
                      </span>
                      {tick.errors.length > 0 && (
                        <div
                          title={tick.errors.join('\n')}
                          style={{ color: 'var(--text-secondary)', fontSize: 12, marginTop: 2 }}
                        >
                          {tick.errors[0]}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 500, color: 'var(--text-primary)' }}>
                      {tick.studied ?? '—'} / {tick.skipped ?? '—'} / {tick.failed ?? '—'}
                      {tick.total_combos != null && (
                        <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>
                          {' '}(of {tick.total_combos})
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                      {tick.completed_at ? new Date(tick.completed_at).toLocaleString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
