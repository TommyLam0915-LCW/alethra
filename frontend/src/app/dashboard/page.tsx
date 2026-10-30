'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { describeRunStatus } from '@/lib/runStatus';
import AppShell from '@/components/shell/AppShell';
import { formatDate } from '@/lib/format';
import { EXPERIMENT_STATUS_LABELS, label } from '@/lib/copy';

interface LatestRun {
  id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  total_return_pct: number | null;
  max_drawdown_pct: number | null;
  total_trades: number | null;
  trading_days: number | null;
}

interface Experiment {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  latest_run?: LatestRun | null;
}

type StatusFilter = 'all' | 'draft' | 'exploring' | 'locked' | 'archived';

function LatestRunCell({ run }: { run: LatestRun | null }) {
  if (!run) {
    return <span className="research-table-result none">Not tested</span>;
  }

  if (run.status !== 'completed' || run.total_return_pct == null) {
    const view = describeRunStatus(run.status, run.started_at);
    return (
      <div>
        <span
          className="research-table-result none"
          data-testid={view.stale ? 'latest-run-stale' : undefined}
          title={view.detail}
        >
          {view.label}
        </span>
        {run.completed_at && (
          <div className="research-table-meta">
            {formatDate(run.completed_at)}
          </div>
        )}
        {view.stale && (
          <div className="research-table-meta">
            Over 30 minutes without completing — not currently running
          </div>
        )}
      </div>
    );
  }

  const pct = run.total_return_pct;
  return (
    <div data-testid="latest-run-cell">
      <span className={`research-table-result ${pct >= 0 ? 'pos' : 'neg'}`}>
        {pct >= 0 ? '+' : ''}
        {pct.toFixed(2)}%
      </span>
      <div className="research-table-meta">
        {run.completed_at ? `${formatDate(run.completed_at)} · ` : ''}
        {run.trading_days != null
          ? `${run.trading_days} trading day${run.trading_days === 1 ? '' : 's'}`
          : `${run.total_trades ?? 0} trade${(run.total_trades ?? 0) === 1 ? '' : 's'}`}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  useEffect(() => {
    loadExperiments();
  }, []);

  const loadExperiments = async () => {
    setLoading(true);
    try {
      const data = await api.experiments.list();
      setExperiments(data);
      setLoadError(null);
    } catch (err) {
      // A failed load previously rendered as "no experiments yet", which read
      // as an empty account instead of a failure.
      console.error('Failed to load experiments:', err);
      setLoadError('Could not load experiments. Check your connection and try again.');
    } finally {
      setLoading(false);
    }
  };

  const getStatusLabel = (status: string) => label(EXPERIMENT_STATUS_LABELS, status);

  const getNextAction = (exp: Experiment) => {
    if (exp.status === 'draft') return { label: 'Continue research', href: `/experiments/${exp.id}` };
    if (exp.status === 'exploring') return { label: 'View results', href: `/experiments/${exp.id}` };
    if (exp.status === 'locked') return { label: 'View report', href: `/experiments/${exp.id}` };
    return { label: 'View details', href: `/experiments/${exp.id}` };
  };

  const getFilteredExperiments = () => {
    if (statusFilter === 'all') return experiments;
    return experiments.filter((exp) => exp.status === statusFilter);
  };

  const getSortedExperiments = () => {
    return [...getFilteredExperiments()]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  };

  if (loading) {
    return (
      <AppShell active="strategies">
        <div className="research-loading" role="status">Loading…</div>
      </AppShell>
    );
  }

  return (
    <AppShell active="strategies">
      <div className="research-root">
        {/* Page header: concise title + one primary action */}
        <div className="research-header">
          <h1 className="research-title">Experiments</h1>
          <Link href="/experiments/new" className="research-primary-action">
            New experiment
          </Link>
        </div>

        {/* Compact filter */}
        <div className="research-filter-bar">
          <div className="research-filter-group">
            <button
              className={`research-filter-btn${statusFilter === 'all' ? ' active' : ''}`}
              aria-pressed={statusFilter === 'all'}
              onClick={() => setStatusFilter('all')}
            >
              All ({experiments.length})
            </button>
            <button
              className={`research-filter-btn${statusFilter === 'draft' ? ' active' : ''}`}
              aria-pressed={statusFilter === 'draft'}
              onClick={() => setStatusFilter('draft')}
            >
              Draft ({experiments.filter(e => e.status === 'draft').length})
            </button>
            <button
              className={`research-filter-btn${statusFilter === 'exploring' ? ' active' : ''}`}
              aria-pressed={statusFilter === 'exploring'}
              onClick={() => setStatusFilter('exploring')}
            >
              Exploring ({experiments.filter(e => e.status === 'exploring').length})
            </button>
            <button
              className={`research-filter-btn${statusFilter === 'locked' ? ' active' : ''}`}
              aria-pressed={statusFilter === 'locked'}
              onClick={() => setStatusFilter('locked')}
            >
              Locked ({experiments.filter(e => e.status === 'locked').length})
            </button>
          </div>
        </div>

        {/* Experiment list */}
        <div className="research-list">
          {loadError && (
            <div
              className="alert alert-warning"
              role="alert"
              data-testid="dashboard-load-error"
              style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}
            >
              <span>{loadError}</span>
              <button type="button" className="research-filter-btn" onClick={loadExperiments}>
                Retry
              </button>
            </div>
          )}
          {!loadError && (getSortedExperiments().length === 0 ? (
            <div className="research-empty" data-testid="dashboard-empty-state">
              <h3>No experiments yet</h3>
              <p>
                Create your first experiment to record an investment hypothesis, track test
                results, and keep the research evidence.
              </p>
              <Link href="/experiments/new" className="research-empty-action">
                Create your first experiment
              </Link>
            </div>
          ) : (
            <table className="research-table-grid">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Latest run</th>
                  <th scope="col">Status</th>
                  <th scope="col">Updated</th>
                  <th scope="col" aria-label="Actions"></th>
                </tr>
              </thead>
              <tbody>
                {getSortedExperiments().map((exp) => {
                  const nextAction = getNextAction(exp);
                  return (
                    <tr key={exp.id} data-testid="dashboard-experiment-row">
                      <td>
                        <Link href={nextAction.href} className="research-table-name">
                          {exp.title}
                        </Link>
                        <div className="research-table-meta">
                          Created {formatDate(exp.created_at)}
                        </div>
                      </td>
                      <td>
                        <LatestRunCell run={exp.latest_run || null} />
                      </td>
                      <td>
                        <span className={`research-status ${exp.status}`}>
                          {getStatusLabel(exp.status)}
                        </span>
                      </td>
                      <td className="research-table-date">
                        {formatDate(exp.updated_at)}
                      </td>
                      <td>
                        <Link href={nextAction.href} className="research-table-action">
                          {nextAction.label} →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ))}
        </div>

        {/* The redesign dropped the always-visible disclaimer this page had;
            every other result surface still carries one, so keep it here too. */}
        <div className="dash-disclaimer" style={{ marginTop: '24px', marginBottom: 0 }}>
          <strong>Note:</strong> This is a research tool. Results are for research only; they do
          not predict future performance and are not investment advice.
        </div>
      </div>
    </AppShell>
  );
}
