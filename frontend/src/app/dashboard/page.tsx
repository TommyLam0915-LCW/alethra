'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { describeRunStatus } from '@/lib/runStatus';
import AppShell from '@/components/shell/AppShell';
import { formatDate } from '@/lib/format';

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
    return <span className="research-table-result none">未測試</span>;
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
            已超過 30 分鐘未完成，非目前執行中
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
        {run.trading_days != null ? `${run.trading_days} 個交易日` : `${run.total_trades ?? 0} 筆交易`}
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
      setLoadError('無法載入實驗列表。請檢查連線後重試。');
    } finally {
      setLoading(false);
    }
  };

  const getStatusLabel = (status: string) => {
    const labels: Record<string, string> = {
      draft: '草稿',
      exploring: '探索中',
      locked: '已鎖定',
      archived: '已封存',
    };
    return labels[status] || status;
  };

  const getNextAction = (exp: Experiment) => {
    if (exp.status === 'draft') return { label: '繼續研究', href: `/experiments/${exp.id}` };
    if (exp.status === 'exploring') return { label: '查看結果', href: `/experiments/${exp.id}` };
    if (exp.status === 'locked') return { label: '查看報告', href: `/experiments/${exp.id}` };
    return { label: '查看詳情', href: `/experiments/${exp.id}` };
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
        <div className="research-loading">載入中...</div>
      </AppShell>
    );
  }

  return (
    <AppShell active="strategies">
      <div className="research-root">
        {/* Page header: concise title + one primary action */}
        <div className="research-header">
          <h1 className="research-title">策略與實驗</h1>
          <Link href="/experiments/new" className="research-primary-action">
            建立新實驗
          </Link>
        </div>

        {/* Compact filter */}
        <div className="research-filter-bar">
          <div className="research-filter-group">
            <button
              className={`research-filter-btn${statusFilter === 'all' ? ' active' : ''}`}
              onClick={() => setStatusFilter('all')}
            >
              全部 ({experiments.length})
            </button>
            <button
              className={`research-filter-btn${statusFilter === 'draft' ? ' active' : ''}`}
              onClick={() => setStatusFilter('draft')}
            >
              草稿 ({experiments.filter(e => e.status === 'draft').length})
            </button>
            <button
              className={`research-filter-btn${statusFilter === 'exploring' ? ' active' : ''}`}
              onClick={() => setStatusFilter('exploring')}
            >
              探索中 ({experiments.filter(e => e.status === 'exploring').length})
            </button>
            <button
              className={`research-filter-btn${statusFilter === 'locked' ? ' active' : ''}`}
              onClick={() => setStatusFilter('locked')}
            >
              已鎖定 ({experiments.filter(e => e.status === 'locked').length})
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
                重試
              </button>
            </div>
          )}
          {!loadError && (getSortedExperiments().length === 0 ? (
            <div className="research-empty">
              <h3>還沒有任何實驗</h3>
              <p>建立第一個實驗，記錄你的投資假設，追蹤測試結果，保存研究證據。</p>
              <Link href="/experiments/new" className="research-empty-action">
                建立第一個實驗
              </Link>
            </div>
          ) : (
            <table className="research-table-grid">
              <thead>
                <tr>
                  <th>名稱</th>
                  <th>最近測試</th>
                  <th>狀態</th>
                  <th>更新</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {getSortedExperiments().map((exp) => {
                  const nextAction = getNextAction(exp);
                  return (
                    <tr key={exp.id}>
                      <td>
                        <Link href={nextAction.href} className="research-table-name">
                          {exp.title}
                        </Link>
                        <div className="research-table-meta">
                          建於 {formatDate(exp.created_at)}
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
          <strong>提醒：</strong>本系統是研究工具，所有結果僅供研究參考，不代表未來績效，不構成投資建議。
        </div>
      </div>
    </AppShell>
  );
}
