'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

interface Application {
  id: string;
  intended_user_type: string;
  status: string;
  created_at: string;
}

interface Entitlement {
  id: string;
  pilot_access: boolean;
  research_run_limit: number;
  ai_usage_limit: number;
  export_limit: number;
  forward_simulation_limit: number;
  expiry_date: string | null;
  support_tier: string;
}

const USER_TYPE_OPTIONS = [
  { value: 'individual_researcher', label: '個人研究者' },
  { value: 'educator', label: '教育者' },
  { value: 'creator', label: '內容創作者' },
  { value: 'ai_builder', label: 'AI 建構者' },
  { value: 'small_research_team', label: '小型研究團隊' },
];

export default function PilotApplicationPage() {
  const router = useRouter();
  const [application, setApplication] = useState<Application | null>(null);
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const [form, setForm] = useState({
    intended_user_type: 'individual_researcher',
    current_research_workflow: '',
    tools_currently_used: '',
    primary_pain_point: '',
    expected_usage_frequency: '',
    willingness_to_interview: false,
    consent_to_contact: false,
  });

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadExistingData();
  }, [router]);

  const loadExistingData = async () => {
    try {
      const app = await api.pilot.getMyApplication();
      setApplication(app);
    } catch {
      // No application yet
    }
    try {
      const ent = await api.pilot.getMyEntitlement();
      setEntitlement(ent);
    } catch {
      // No entitlement yet
    }
    setLoading(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    setSuccess('');

    try {
      const result = await api.pilot.submitApplication(form);
      setApplication(result);
      setSuccess('申請已提交！我們會盡快審核。');
    } catch (err: any) {
      setError(err.message || '提交失敗');
    } finally {
      setSubmitting(false);
    }
  };

  const getStatusLabel = (s: string) => {
    const labels: Record<string, string> = {
      submitted: '已提交',
      approved: '已批准',
      rejected: '已拒絕',
      waitlisted: '候補中',
    };
    return labels[s] || s;
  };

  const getStatusColor = (s: string) => {
    const colors: Record<string, { bg: string; fg: string }> = {
      submitted: { bg: '#fff3cd', fg: '#856404' },
      approved: { bg: '#d4edda', fg: '#155724' },
      rejected: { bg: '#f8d7da', fg: '#721c24' },
      waitlisted: { bg: '#e2e3e5', fg: '#383d41' },
    };
    return colors[s] || { bg: '#eaeaea', fg: '#666' };
  };

  if (loading) {
    return (
      <div className="container" style={{ paddingTop: '40px', textAlign: 'center' }}>
        載入中...
      </div>
    );
  }

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1>Pilot 試點計劃</h1>
        <Link href="/dashboard" className="btn btn-secondary">
          返回主控台
        </Link>
      </div>

      <div className="disclaimer" style={{ marginBottom: '24px' }}>
        <strong>提醒：</strong>本計劃為內部試點。所有功能僅供研究使用，不代表未來績效。
        <br />
        <em>Internal pilot pricing hypothesis — not public pricing and not market validated.</em>
      </div>

      {entitlement && (
        <div className="card" style={{ marginBottom: '24px', borderLeft: '4px solid #28a745' }}>
          <h3 style={{ marginBottom: '16px' }}>你的 Pilot 權限</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' }}>
            <div>
              <div style={{ fontSize: '12px', color: '#666' }}>回測執行上限</div>
              <div style={{ fontSize: '18px', fontWeight: 600 }}>{entitlement.research_run_limit}</div>
            </div>
            <div>
              <div style={{ fontSize: '12px', color: '#666' }}>AI 使用上限</div>
              <div style={{ fontSize: '18px', fontWeight: 600 }}>{entitlement.ai_usage_limit}</div>
            </div>
            <div>
              <div style={{ fontSize: '12px', color: '#666' }}>匯出上限</div>
              <div style={{ fontSize: '18px', fontWeight: 600 }}>{entitlement.export_limit}</div>
            </div>
            <div>
              <div style={{ fontSize: '12px', color: '#666' }}>前瞻模擬上限</div>
              <div style={{ fontSize: '18px', fontWeight: 600 }}>{entitlement.forward_simulation_limit}</div>
            </div>
            <div>
              <div style={{ fontSize: '12px', color: '#666' }}>支援等級</div>
              <div style={{ fontSize: '18px', fontWeight: 600 }}>{entitlement.support_tier}</div>
            </div>
            <div>
              <div style={{ fontSize: '12px', color: '#666' }}>到期日</div>
              <div style={{ fontSize: '18px', fontWeight: 600 }}>
                {entitlement.expiry_date ? new Date(entitlement.expiry_date).toLocaleDateString('zh-Hant') : '無限期'}
              </div>
            </div>
          </div>
        </div>
      )}

      {application ? (
        <div className="card">
          <h3 style={{ marginBottom: '16px' }}>申請狀態</h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
            <span
              style={{
                padding: '6px 16px',
                borderRadius: '16px',
                fontSize: '14px',
                fontWeight: 600,
                backgroundColor: getStatusColor(application.status).bg,
                color: getStatusColor(application.status).fg,
              }}
            >
              {getStatusLabel(application.status)}
            </span>
            <span style={{ color: '#666', fontSize: '14px' }}>
              提交於 {new Date(application.created_at).toLocaleDateString('zh-Hant')}
            </span>
          </div>
          <div style={{ fontSize: '14px', color: '#444' }}>
            <p>申請類型：{USER_TYPE_OPTIONS.find(o => o.value === application.intended_user_type)?.label || application.intended_user_type}</p>
          </div>
        </div>
      ) : (
        <div className="card">
          <h3 style={{ marginBottom: '20px' }}>提交 Pilot 申請</h3>
          <p style={{ color: '#666', marginBottom: '20px', fontSize: '14px' }}>
            填寫以下資訊以申請加入試點計劃。我們不會要求任何券商帳戶或敏感財務資料。
          </p>

          {error && (
            <div style={{ padding: '12px', backgroundColor: '#f8d7da', color: '#721c24', borderRadius: '6px', marginBottom: '16px' }}>
              {error}
            </div>
          )}
          {success && (
            <div style={{ padding: '12px', backgroundColor: '#d4edda', color: '#155724', borderRadius: '6px', marginBottom: '16px' }}>
              {success}
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 500, fontSize: '14px' }}>
                你的角色 *
              </label>
              <select
                className="input"
                value={form.intended_user_type}
                onChange={e => setForm({ ...form, intended_user_type: e.target.value })}
                required
              >
                {USER_TYPE_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 500, fontSize: '14px' }}>
                目前的研究流程
              </label>
              <textarea
                className="input"
                rows={3}
                placeholder="描述你目前如何進行投資研究..."
                value={form.current_research_workflow}
                onChange={e => setForm({ ...form, current_research_workflow: e.target.value })}
              />
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 500, fontSize: '14px' }}>
                目前使用的工具
              </label>
              <input
                className="input"
                type="text"
                placeholder="例如：Excel, TradingView, Python..."
                value={form.tools_currently_used}
                onChange={e => setForm({ ...form, tools_currently_used: e.target.value })}
              />
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 500, fontSize: '14px' }}>
                最大的痛點
              </label>
              <textarea
                className="input"
                rows={2}
                placeholder="你在投資研究中遇到最大的困難是什麼？"
                value={form.primary_pain_point}
                onChange={e => setForm({ ...form, primary_pain_point: e.target.value })}
              />
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 500, fontSize: '14px' }}>
                預期使用頻率
              </label>
              <select
                className="input"
                value={form.expected_usage_frequency}
                onChange={e => setForm({ ...form, expected_usage_frequency: e.target.value })}
              >
                <option value="">請選擇</option>
                <option value="daily">每天</option>
                <option value="few_times_week">每週數次</option>
                <option value="weekly">每週一次</option>
                <option value="few_times_month">每月數次</option>
                <option value="occasionally">偶爾使用</option>
              </select>
            </div>

            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'flex', alignItems: 'start', gap: '8px', fontSize: '14px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={form.willingness_to_interview}
                  onChange={e => setForm({ ...form, willingness_to_interview: e.target.checked })}
                  style={{ marginTop: '3px' }}
                />
                <span>我願意參與簡短的用戶訪談（約 15-20 分鐘）</span>
              </label>
            </div>

            <div style={{ marginBottom: '24px' }}>
              <label style={{ display: 'flex', alignItems: 'start', gap: '8px', fontSize: '14px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={form.consent_to_contact}
                  onChange={e => setForm({ ...form, consent_to_contact: e.target.checked })}
                  required
                  style={{ marginTop: '3px' }}
                />
                <span>我同意被聯繫關於我的申請 *</span>
              </label>
            </div>

            <button type="submit" className="btn btn-primary" disabled={submitting} style={{ width: '100%' }}>
              {submitting ? '提交中...' : '提交申請'}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
