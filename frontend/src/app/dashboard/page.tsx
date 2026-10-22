'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

interface Experiment {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
}

interface DashboardStats {
  totalExperiments: number;
  activeExperiments: number;
  lockedExperiments: number;
  draftExperiments: number;
}

export default function DashboardPage() {
  const router = useRouter();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [stats, setStats] = useState<DashboardStats>({
    totalExperiments: 0,
    activeExperiments: 0,
    lockedExperiments: 0,
    draftExperiments: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }

    loadDashboard();
  }, [router]);

  const loadDashboard = async () => {
    try {
      const data = await api.experiments.list();
      setExperiments(data);

      // Calculate stats
      const totalExperiments = data.length;
      const activeExperiments = data.filter((e: Experiment) => e.status === 'exploring').length;
      const lockedExperiments = data.filter((e: Experiment) => e.status === 'locked').length;
      const draftExperiments = data.filter((e: Experiment) => e.status === 'draft').length;

      setStats({
        totalExperiments,
        activeExperiments,
        lockedExperiments,
        draftExperiments,
      });
    } catch (err) {
      console.error('Failed to load dashboard:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    router.push('/');
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

  const getRecentExperiments = () => {
    return [...experiments]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 5);
  };

  if (loading) {
    return (
      <div className="dash-root">
        <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text-muted)' }}>
          載入中...
        </div>
      </div>
    );
  }

  return (
    <div className="dash-root">
      {/* Header */}
      <div className="dash-header">
        <div className="dash-header-left">
          <h1>{brand.ui.dashboard.titleZh}</h1>
          <p>你的投資想法，值得被認真檢驗。</p>
        </div>
        <div className="dash-header-right">
          <Link href="/workspace" className="btn btn-primary" data-testid="nav-workspace">
            進入工作台
          </Link>
          <Link href="/experiments/new" className="btn btn-secondary">
            建立新實驗
          </Link>
          <button onClick={handleLogout} className="btn btn-secondary">
            {brand.ui.auth.logoutZh}
          </button>
        </div>
      </div>

      {/* Disclaimer */}
      <div className="dash-disclaimer">
        <strong>提醒：</strong>本系統是研究工具，所有結果僅供研究參考，不代表未來績效，不構成投資建議。
      </div>

      {/* Stats Grid */}
      <div className="dash-stats-grid">
        <div className="dash-stat-card">
          <div className="dash-stat-label">總實驗數</div>
          <div className="dash-stat-value">{stats.totalExperiments}</div>
          <div className="dash-stat-change neutral">All experiments</div>
        </div>

        <div className="dash-stat-card">
          <div className="dash-stat-label">進行中</div>
          <div className="dash-stat-value">{stats.activeExperiments}</div>
          <div className="dash-stat-change positive">
            {stats.activeExperiments > 0 ? '↑ Active' : '— No active'}
          </div>
        </div>

        <div className="dash-stat-card">
          <div className="dash-stat-label">已鎖定</div>
          <div className="dash-stat-value">{stats.lockedExperiments}</div>
          <div className="dash-stat-change neutral">
            {stats.lockedExperiments > 0 ? '✓ Locked' : '— None'}
          </div>
        </div>

        <div className="dash-stat-card">
          <div className="dash-stat-label">草稿</div>
          <div className="dash-stat-value">{stats.draftExperiments}</div>
          <div className="dash-stat-change neutral">
            {stats.draftExperiments > 0 ? '✎ Drafts' : '— None'}
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="dash-quick-actions">
        <Link href="/experiments/new" className="dash-quick-action-btn">
          <div className="dash-quick-action-icon">➕</div>
          <div className="dash-quick-action-label">建立新實驗</div>
        </Link>

        <Link href="/workspace" className="dash-quick-action-btn">
          <div className="dash-quick-action-icon">🔬</div>
          <div className="dash-quick-action-label">進入工作台</div>
        </Link>

        <Link href="/research-drafts" className="dash-quick-action-btn">
          <div className="dash-quick-action-icon">📝</div>
          <div className="dash-quick-action-label">研究草稿</div>
        </Link>

        <Link href="/market-data" className="dash-quick-action-btn">
          <div className="dash-quick-action-icon">📊</div>
          <div className="dash-quick-action-label">市場數據</div>
        </Link>

        <Link href="/models" className="dash-quick-action-btn">
          <div className="dash-quick-action-icon">🤖</div>
          <div className="dash-quick-action-label">模型研究</div>
        </Link>

        <Link href="/api-keys" className="dash-quick-action-btn">
          <div className="dash-quick-action-icon">🔑</div>
          <div className="dash-quick-action-label">API 金鑰</div>
        </Link>
      </div>

      {/* Main Grid */}
      <div className="dash-main-grid">
        {/* Recent Experiments Panel */}
        <div className="dash-panel">
          <div className="dash-panel-header">
            <div className="dash-panel-title">最近實驗</div>
            <div className="dash-panel-actions">
              <Link href="/experiments/new" className="btn btn-primary" style={{ padding: '6px 12px', fontSize: '12px' }}>
                建立新實驗
              </Link>
            </div>
          </div>
          <div className="dash-panel-body">
            {experiments.length === 0 ? (
              <div className="dash-empty-state">
                <h3>還沒有任何實驗</h3>
                <p>
                  建立第一個實驗，記錄你的投資假設，<br />
                  追蹤測試結果，保存研究證據。
                </p>
                <Link href="/experiments/new" className="btn btn-primary">
                  建立第一個實驗
                </Link>
              </div>
            ) : (
              <div className="dash-experiment-list">
                {getRecentExperiments().map((exp) => {
                  const nextAction = getNextAction(exp);
                  return (
                    <Link
                      key={exp.id}
                      href={nextAction.href}
                      className="dash-experiment-item"
                    >
                      <div className="dash-experiment-info">
                        <div className="dash-experiment-title">{exp.title}</div>
                        <div className="dash-experiment-meta">
                          <span>建立於 {new Date(exp.created_at).toLocaleDateString('zh-Hant')}</span>
                          {exp.updated_at !== exp.created_at && (
                            <>
                              <span>·</span>
                              <span>最後活動 {new Date(exp.updated_at).toLocaleDateString('zh-Hant')}</span>
                            </>
                          )}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                        <span className={`dash-experiment-status ${exp.status}`}>
                          {getStatusLabel(exp.status)}
                        </span>
                        <span style={{ color: 'var(--accent-blue)', fontSize: '13px' }}>
                          {nextAction.label} →
                        </span>
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Activity Feed Panel */}
        <div className="dash-panel">
          <div className="dash-panel-header">
            <div className="dash-panel-title">最近活動</div>
          </div>
          <div className="dash-panel-body">
            {experiments.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-muted)' }}>
                尚無活動
              </div>
            ) : (
              <div className="dash-activity-feed">
                {getRecentExperiments().slice(0, 5).map((exp) => (
                  <div key={exp.id} className="dash-activity-item">
                    <div className="dash-activity-icon">
                      {exp.status === 'draft' ? '✎' : exp.status === 'exploring' ? '🔬' : '✓'}
                    </div>
                    <div className="dash-activity-content">
                      <div className="dash-activity-title">
                        {exp.title}
                      </div>
                      <div className="dash-activity-time">
                        {new Date(exp.updated_at).toLocaleDateString('zh-Hant')} · {getStatusLabel(exp.status)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* All Experiments Link */}
      {experiments.length > 5 && (
        <div style={{ textAlign: 'center', marginTop: '24px' }}>
          <Link href="/experiments" className="btn btn-secondary" style={{ padding: '12px 32px' }}>
            查看所有實驗 ({experiments.length})
          </Link>
        </div>
      )}
    </div>
  );
}
