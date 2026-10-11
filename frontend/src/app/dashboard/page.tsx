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

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1>{brand.ui.dashboard.titleZh}</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/models" className="btn btn-secondary">
            模型研究
          </Link>
          <Link href="/experiments/new" className="btn btn-primary">
            建立實驗
          </Link>
          <button onClick={handleLogout} className="btn btn-secondary">
            {brand.ui.auth.logoutZh}
          </button>
        </div>
      </div>

      <div className="disclaimer">
        <strong>提醒：</strong>本系統是研究工具，所有結果僅供研究參考，不代表未來績效，不構成投資建議。
      </div>

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中...
        </div>
      ) : experiments.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666', marginBottom: '20px' }}>{brand.ui.dashboard.emptyStateZh}</p>
          <Link href="/experiments/new" className="btn btn-primary">
            {brand.ui.dashboard.createFirstZh}
          </Link>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {experiments.map((exp) => (
            <Link
              key={exp.id}
              href={`/experiments/${exp.id}`}
              style={{ textDecoration: 'none', color: 'inherit' }}
            >
              <div className="card" style={{ cursor: 'pointer', transition: 'transform 0.2s' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                  <div>
                    <h3 style={{ marginBottom: '8px' }}>{exp.title}</h3>
                    <p style={{ color: '#666', fontSize: '14px' }}>
                      建立於 {new Date(exp.created_at).toLocaleDateString('zh-Hant')}
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
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
