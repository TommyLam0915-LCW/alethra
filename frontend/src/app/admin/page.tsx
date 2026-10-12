'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface DashboardData {
  funnel: {
    total_users: number;
    applications_submitted: number;
    applications_approved: number;
    first_experiment_created: number;
    first_run_completed: number;
    strategy_locked: number;
    forward_simulation_started: number;
    export_generated: number;
    support_request_created: number;
  };
  activation: {
    total_signed_up: number;
    activated_at_least_one_experiment: number;
    activation_rate: number;
    completed_first_run: number;
    completion_rate: number;
  };
  cost_summary: {
    total_cost: number;
    total_actual: number;
    total_estimate: number;
    cost_per_user: number;
    cost_per_active_user: number;
    breakdown_by_type: Record<string, number>;
  };
  disclaimer: string;
}

interface HypothesesData {
  hypotheses: Array<{
    id: string;
    statement: string;
    status: string;
    evidence_criteria: string | null;
    evidence_notes: string | null;
    created_at: string;
  }>;
  total: number;
  by_status: Record<string, number>;
  disclaimer: string;
}

const STATUS_LABELS: Record<string, string> = {
  untested: '未測試',
  evidence_emerging: '證據浮現中',
  insufficient_evidence: '證據不足',
  contradicted: '已被反駁',
};

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  untested: { bg: '#e2e3e5', fg: '#383d41' },
  evidence_emerging: { bg: '#fff3cd', fg: '#856404' },
  insufficient_evidence: { bg: '#e2e3e5', fg: '#666' },
  contradicted: { bg: '#f8d7da', fg: '#721c24' },
};

export default function AdminDashboardPage() {
  const router = useRouter();
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [hypotheses, setHypotheses] = useState<HypothesesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<'funnel' | 'hypotheses'>('funnel');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadData();
  }, [router]);

  const loadData = async () => {
    try {
      const [dashData, hypData] = await Promise.all([
        api.pilot.getAdminDashboard(),
        api.pilot.getHypothesesDashboard(),
      ]);
      setDashboard(dashData);
      setHypotheses(hypData);
    } catch (err: any) {
      setError(err.message || 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="container" style={{ paddingTop: '40px', textAlign: 'center' }}>
        載入中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="container" style={{ paddingTop: '40px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h1>管理員儀表板</h1>
          <Link href="/dashboard" className="btn btn-secondary">返回主控台</Link>
        </div>
        <div style={{ padding: '20px', backgroundColor: '#f8d7da', color: '#721c24', borderRadius: '8px' }}>
          {error.includes('403') || error.includes('Admin')
            ? '需要管理員權限才能訪問此頁面。'
            : `載入失敗：${error}`}
        </div>
      </div>
    );
  }

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1>管理員驗證儀表板</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/dashboard" className="btn btn-secondary">返回主控台</Link>
        </div>
      </div>

      <div className="disclaimer" style={{ marginBottom: '24px' }}>
        <strong>內部文件：</strong>此儀表板僅供管理員查看。所有數據為內部試點指標，不代表商業承諾。
        <br />
        <em>{dashboard?.disclaimer}</em>
      </div>

      <div style={{ display: 'flex', gap: '8px', marginBottom: '24px' }}>
        <button
          className={`btn ${activeTab === 'funnel' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveTab('funnel')}
        >
          試點漏斗
        </button>
        <button
          className={`btn ${activeTab === 'hypotheses' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setActiveTab('hypotheses')}
        >
          產品假設
        </button>
      </div>

      {activeTab === 'funnel' && dashboard && (
        <>
          <h2 style={{ marginBottom: '16px' }}>Pilot 漏斗</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '32px' }}>
            <FunnelCard label="總註冊用戶" value={dashboard.funnel.total_users} />
            <FunnelCard label="已提交申請" value={dashboard.funnel.applications_submitted} />
            <FunnelCard label="已批准申請" value={dashboard.funnel.applications_approved} />
            <FunnelCard label="建立首個實驗" value={dashboard.funnel.first_experiment_created} />
            <FunnelCard label="完成首次回測" value={dashboard.funnel.first_run_completed} />
            <FunnelCard label="策略已鎖定" value={dashboard.funnel.strategy_locked} />
            <FunnelCard label="啟動歷史重播模擬" value={dashboard.funnel.forward_simulation_started} />
            <FunnelCard label="已產生匯出" value={dashboard.funnel.export_generated} />
            <FunnelCard label="支援請求" value={dashboard.funnel.support_request_created} />
          </div>

          <h2 style={{ marginBottom: '16px' }}>啟動指標</h2>
          <div className="card" style={{ marginBottom: '32px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>總註冊</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>{dashboard.activation.total_signed_up}</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>已啟動（至少一個實驗）</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>{dashboard.activation.activated_at_least_one_experiment}</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>啟動率</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>{(dashboard.activation.activation_rate * 100).toFixed(1)}%</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>完成首次回測</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>{dashboard.activation.completed_first_run}</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>完成率</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>{(dashboard.activation.completion_rate * 100).toFixed(1)}%</div>
              </div>
            </div>
          </div>

          <h2 style={{ marginBottom: '16px' }}>成本摘要</h2>
          <div className="card" style={{ marginBottom: '32px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '20px' }}>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>總成本</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>${dashboard.cost_summary.total_cost.toFixed(2)}</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>實際成本</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>${dashboard.cost_summary.total_actual.toFixed(2)}</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>估算成本</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>${dashboard.cost_summary.total_estimate.toFixed(2)}</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>每用戶成本</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>${dashboard.cost_summary.cost_per_user.toFixed(2)}</div>
              </div>
              <div>
                <div style={{ fontSize: '12px', color: '#666' }}>每活躍用戶成本</div>
                <div style={{ fontSize: '24px', fontWeight: 600 }}>${dashboard.cost_summary.cost_per_active_user.toFixed(2)}</div>
              </div>
            </div>

            {Object.keys(dashboard.cost_summary.breakdown_by_type).length > 0 && (
              <div>
                <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#666' }}>按類型分佈</h4>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {Object.entries(dashboard.cost_summary.breakdown_by_type).map(([type, amount]) => (
                    <span key={type} style={{
                      padding: '4px 12px',
                      backgroundColor: '#f0f0f0',
                      borderRadius: '12px',
                      fontSize: '13px',
                    }}>
                      {type}: ${Number(amount).toFixed(2)}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {activeTab === 'hypotheses' && hypotheses && (
        <>
          <h2 style={{ marginBottom: '16px' }}>產品假設追蹤</h2>

          <div className="disclaimer" style={{ marginBottom: '20px' }}>
            <em>{hypotheses.disclaimer}</em>
            <br />
            沒有任何假設可在沒有預定義標準的情況下被標記為「已證明」。
          </div>

          <div style={{ display: 'flex', gap: '12px', marginBottom: '24px', flexWrap: 'wrap' }}>
            {Object.entries(hypotheses.by_status).map(([status, count]) => (
              <div key={status} style={{
                padding: '8px 16px',
                borderRadius: '8px',
                backgroundColor: STATUS_COLORS[status]?.bg || '#eaeaea',
                color: STATUS_COLORS[status]?.fg || '#666',
              }}>
                <span style={{ fontSize: '13px' }}>{STATUS_LABELS[status] || status}</span>
                <span style={{ marginLeft: '8px', fontWeight: 600 }}>{count}</span>
              </div>
            ))}
          </div>

          {hypotheses.hypotheses.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
              尚無產品假設。請透過 API 建立假設。
            </div>
          ) : (
            <div style={{ display: 'grid', gap: '12px' }}>
              {hypotheses.hypotheses.map(h => (
                <div key={h.id} className="card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '8px' }}>
                    <p style={{ fontWeight: 500, flex: 1, marginRight: '12px' }}>{h.statement}</p>
                    <span style={{
                      padding: '4px 12px',
                      borderRadius: '12px',
                      fontSize: '12px',
                      fontWeight: 600,
                      backgroundColor: STATUS_COLORS[h.status]?.bg || '#eaeaea',
                      color: STATUS_COLORS[h.status]?.fg || '#666',
                      whiteSpace: 'nowrap',
                    }}>
                      {STATUS_LABELS[h.status] || h.status}
                    </span>
                  </div>
                  {h.evidence_criteria && (
                    <p style={{ fontSize: '13px', color: '#666', marginBottom: '4px' }}>
                      <strong>驗證標準：</strong>{h.evidence_criteria}
                    </p>
                  )}
                  {h.evidence_notes && (
                    <p style={{ fontSize: '13px', color: '#666' }}>
                      <strong>證據筆記：</strong>{h.evidence_notes}
                    </p>
                  )}
                  <p style={{ fontSize: '12px', color: '#999', marginTop: '8px' }}>
                    建於 {new Date(h.created_at).toLocaleDateString('zh-Hant')}
                  </p>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function FunnelCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="card" style={{ textAlign: 'center', padding: '20px' }}>
      <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>{label}</div>
      <div style={{ fontSize: '28px', fontWeight: 700 }}>{value}</div>
    </div>
  );
}
