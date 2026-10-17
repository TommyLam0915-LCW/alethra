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

export default function DashboardPage() {
  const router = useRouter();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }

    loadExperiments();
  }, [router]);

  const loadExperiments = async () => {
    try {
      const data = await api.experiments.list();
      setExperiments(data);
    } catch (err) {
      console.error('Failed to load experiments:', err);
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

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '30px' }}>
        <h1 style={{ marginBottom: '8px' }}>{brand.ui.dashboard.titleZh}</h1>
        <p style={{ color: '#666', fontSize: '14px', margin: 0 }}>
          你的投資想法，值得被認真檢驗。
        </p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          <Link href="/workspace" className="btn btn-primary" data-testid="nav-workspace">
            進入工作台
          </Link>
          <Link href="/experiments/new" className="btn btn-secondary">
            建立新實驗
          </Link>
          <Link href="/research-drafts" className="btn btn-secondary">
            研究草稿
          </Link>
        </div>
        <button onClick={handleLogout} className="btn btn-secondary">
          {brand.ui.auth.logoutZh}
        </button>
      </div>

      <div className="disclaimer" style={{ marginBottom: '24px' }}>
        <strong>提醒：</strong>本系統是研究工具，所有結果僅供研究參考，不代表未來績效，不構成投資建議。
      </div>

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中...
        </div>
      ) : experiments.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <h3 style={{ marginBottom: '16px', color: '#333' }}>還沒有任何實驗</h3>
          <p style={{ color: '#666', marginBottom: '12px', lineHeight: '1.6' }}>
            建立第一個實驗，記錄你的投資假設，<br />
            追蹤測試結果，保存研究證據。
          </p>
          <p style={{ color: '#888', fontSize: '14px', marginBottom: '24px' }}>
            你可以隨時暫停、修改或停止研究。<br />
            「證據不足」和「不需要修改」都是有效的結果。
          </p>
          <Link href="/experiments/new" className="btn btn-primary">
            建立第一個實驗
          </Link>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px', marginBottom: '32px' }}>
          {experiments.map((exp) => {
            const nextAction = getNextAction(exp);
            return (
              <Link
                key={exp.id}
                href={`/experiments/${exp.id}`}
                style={{ textDecoration: 'none', color: 'inherit' }}
              >
                <div className="card" style={{ cursor: 'pointer', transition: 'transform 0.2s' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '12px' }}>
                    <div style={{ flex: 1 }}>
                      <h3 style={{ marginBottom: '8px' }}>{exp.title}</h3>
                      <p style={{ color: '#666', fontSize: '14px', margin: 0 }}>
                        建立於 {new Date(exp.created_at).toLocaleDateString('zh-Hant')}
                        {exp.updated_at !== exp.created_at && (
                          <> · 最後活動 {new Date(exp.updated_at).toLocaleDateString('zh-Hant')}</>
                        )}
                      </p>
                    </div>
                    <span
                      style={{
                        padding: '4px 12px',
                        borderRadius: '12px',
                        fontSize: '12px',
                        backgroundColor: exp.status === 'locked' ? '#d1ecf1' : '#eaeaea',
                        color: exp.status === 'locked' ? '#0c5460' : '#666',
                      }}
                    >
                      {getStatusLabel(exp.status)}
                    </span>
                  </div>
                  <div style={{ borderTop: '1px solid #eaeaea', paddingTop: '12px' }}>
                    <span style={{ color: '#0070f3', fontSize: '14px' }}>
                      {nextAction.label} →
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      <div style={{ borderTop: '1px solid #eaeaea', paddingTop: '24px', marginBottom: '32px' }}>
        <h4 style={{ marginBottom: '16px', color: '#666', fontSize: '14px' }}>其他功能</h4>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          <Link href="/models" className="btn btn-secondary">
            模型研究
          </Link>
          <Link href="/training" className="btn btn-secondary">
            訓練任務
          </Link>
          <Link href="/inference" className="btn btn-secondary">
            推論執行
          </Link>
          <Link href="/execution" className="btn btn-secondary">
            執行意向
          </Link>
          <Link href="/market-data" className="btn btn-secondary">
            市場數據
          </Link>
          <Link href="/api-keys" className="btn btn-secondary">
            API 金鑰
          </Link>
          <Link href="/subscription" className="btn btn-secondary">
            訂閱方案
          </Link>
        </div>
      </div>
    </div>
  );
}
