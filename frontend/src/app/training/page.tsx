'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface TrainingJob {
  id: string;
  experiment_id: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  parameter_grid: Record<string, any[]>;
  results: any[];
  best_params: Record<string, any>;
}

export default function TrainingPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<TrainingJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    experiment_id: '',
    parameter_grid: '{"fast_period": [3, 5, 10], "slow_period": [20, 30]}',
  });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadJobs();
  }, [router]);

  const loadJobs = async () => {
    try {
      const data = await api.training.listJobs();
      setJobs(data);
      setError(null);
    } catch (err) {
      console.error('Failed to load training jobs:', err);
      setError('載入訓練任務失敗');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateJob = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      const paramGrid = JSON.parse(formData.parameter_grid);
      await api.training.createJob({
        experiment_id: formData.experiment_id,
        parameter_grid: paramGrid,
      });
      setShowForm(false);
      setFormData({
        experiment_id: '',
        parameter_grid: '{"fast_period": [3, 5, 10], "slow_period": [20, 30]}',
      });
      await loadJobs();
    } catch (err) {
      console.error('Failed to create job:', err);
      alert('建立訓練任務失敗');
    } finally {
      setCreating(false);
    }
  };

  const handleCancelJob = async (jobId: string) => {
    if (!confirm('確定要取消此任務嗎？')) return;
    try {
      await api.training.cancel(jobId);
      await loadJobs();
    } catch (err) {
      console.error('Failed to cancel job:', err);
      alert('取消任務失敗');
    }
  };

  const toggleExpand = (jobId: string) => {
    setExpandedJobId(expandedJobId === jobId ? null : jobId);
  };

  const getStatusBadge = (status: string) => {
    const colors: Record<string, { bg: string; text: string }> = {
      pending: { bg: '#fff3cd', text: '#856404' },
      running: { bg: '#cce5ff', text: '#004085' },
      completed: { bg: '#d4edda', text: '#155724' },
      failed: { bg: '#f8d7da', text: '#721c24' },
      cancelled: { bg: '#e2e3e5', text: '#383d42' },
    };
    const labels: Record<string, string> = {
      pending: '待處理',
      running: '執行中',
      completed: '已完成',
      failed: '失敗',
      cancelled: '已取消',
    };
    const color = colors[status] || { bg: '#eaeaea', text: '#666' };
    return (
      <span
        style={{
          padding: '4px 12px',
          borderRadius: '12px',
          fontSize: '12px',
          backgroundColor: color.bg,
          color: color.text,
        }}
      >
        {labels[status] || status}
      </span>
    );
  };

  const truncateId = (id: string) => {
    if (!id) return '';
    return id.length > 8 ? `${id.substring(0, 8)}...` : id;
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    router.push('/');
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1>訓練 / 最佳化任務</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/dashboard" className="btn btn-secondary">
            返回我的實驗
          </Link>
          <button onClick={() => setShowForm(!showForm)} className="btn btn-primary">
            {showForm ? '隱藏表單' : '建立訓練任務'}
          </button>
          <button onClick={handleLogout} className="btn btn-secondary">
            登出
          </button>
        </div>
      </div>

      <div className="disclaimer" style={{ backgroundColor: '#fff3cd', border: '1px solid #ffc107', padding: '16px', borderRadius: '8px', marginBottom: '24px' }}>
        <strong>提醒：</strong>參數搜尋採用網格搜尋法 — 非機器學習訓練。結果為演示資料的回測指標。
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '16px' }}>建立新訓練任務</h3>
          <form onSubmit={handleCreateJob}>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                實驗 ID
              </label>
              <input
                type="text"
                value={formData.experiment_id}
                onChange={(e) => setFormData({ ...formData, experiment_id: e.target.value })}
                placeholder="輸入實驗 ID"
                required
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  border: '1px solid #ddd',
                  borderRadius: '4px',
                  fontSize: '14px',
                }}
              />
            </div>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                參數網格 (JSON)
              </label>
              <textarea
                value={formData.parameter_grid}
                onChange={(e) => setFormData({ ...formData, parameter_grid: e.target.value })}
                rows={8}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  border: '1px solid #ddd',
                  borderRadius: '4px',
                  fontSize: '14px',
                  fontFamily: 'monospace',
                  resize: 'vertical',
                }}
              />
              <small style={{ color: '#666', marginTop: '4px', display: 'block' }}>
                範例：{'{"fast_period": [3, 5, 10], "slow_period": [20, 30]}'}
              </small>
            </div>
            <button type="submit" className="btn btn-primary" disabled={creating}>
              {creating ? '建立中...' : '建立任務'}
            </button>
          </form>
        </div>
      )}

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中...
        </div>
      ) : error ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px', color: '#721c24' }}>
          {error}
        </div>
      ) : jobs.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666' }}>尚無訓練任務</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {jobs.map((job) => (
            <div key={job.id} className="card">
              <div onClick={() => toggleExpand(job.id)} style={{ cursor: 'pointer' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '12px' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', gap: '24px', marginBottom: '8px', fontSize: '14px' }}>
                      <div>
                        <span style={{ color: '#666' }}>任務 ID：</span>
                        <span style={{ fontFamily: 'monospace' }}>{truncateId(job.id)}</span>
                      </div>
                      <div>
                        <span style={{ color: '#666' }}>實驗：</span>
                        <Link
                          href={`/experiments/${job.experiment_id}`}
                          style={{ fontFamily: 'monospace', color: '#0070f3', textDecoration: 'none' }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {truncateId(job.experiment_id)}
                        </Link>
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '24px', fontSize: '13px', color: '#666' }}>
                      <div>建立：{new Date(job.created_at).toLocaleString('zh-Hant')}</div>
                      {job.completed_at && (
                        <div>完成：{new Date(job.completed_at).toLocaleString('zh-Hant')}</div>
                      )}
                    </div>
                    {job.best_params && Object.keys(job.best_params).length > 0 && (
                      <div style={{ marginTop: '8px', fontSize: '13px' }}>
                        <span style={{ color: '#666' }}>最佳參數：</span>
                        <span style={{ fontFamily: 'monospace', color: '#155724' }}>
                          {JSON.stringify(job.best_params)}
                        </span>
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-end' }}>
                    {getStatusBadge(job.status)}
                    {job.status === 'pending' && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCancelJob(job.id);
                        }}
                        className="btn btn-secondary"
                        style={{ fontSize: '12px', padding: '4px 12px' }}
                      >
                        取消
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {expandedJobId === job.id && (
                <div style={{ marginTop: '16px', borderTop: '1px solid #eee', paddingTop: '16px' }}>
                  <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#666' }}>參數網格</h4>
                  <pre style={{
                    backgroundColor: '#f8f9fa',
                    padding: '12px',
                    borderRadius: '4px',
                    fontSize: '13px',
                    overflow: 'auto',
                    marginBottom: '16px',
                  }}>
                    {JSON.stringify(job.parameter_grid, null, 2)}
                  </pre>

                  {job.best_params && Object.keys(job.best_params).length > 0 && (
                    <div style={{
                      backgroundColor: '#d4edda',
                      padding: '12px',
                      borderRadius: '4px',
                      marginBottom: '16px',
                      border: '1px solid #c3e6cb',
                    }}>
                      <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#155724' }}>
                        最佳參數組合
                      </h4>
                      <pre style={{
                        backgroundColor: 'rgba(255,255,255,0.6)',
                        padding: '8px',
                        borderRadius: '4px',
                        fontSize: '13px',
                        margin: 0,
                      }}>
                        {JSON.stringify(job.best_params, null, 2)}
                      </pre>
                    </div>
                  )}

                  {job.results && job.results.length > 0 && (
                    <>
                      <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#666' }}>結果排名</h4>
                      <div style={{ overflowX: 'auto' }}>
                        <table style={{
                          width: '100%',
                          borderCollapse: 'collapse',
                          fontSize: '13px',
                        }}>
                          <thead>
                            <tr style={{ backgroundColor: '#f8f9fa', borderBottom: '2px solid #dee2e6' }}>
                              <th style={{ padding: '8px', textAlign: 'left', color: '#666' }}>排名</th>
                              <th style={{ padding: '8px', textAlign: 'left', color: '#666' }}>參數</th>
                              <th style={{ padding: '8px', textAlign: 'right', color: '#666' }}>指標</th>
                            </tr>
                          </thead>
                          <tbody>
                            {job.results.map((result, idx) => (
                              <tr
                                key={idx}
                                style={{
                                  borderBottom: '1px solid #dee2e6',
                                  backgroundColor: job.best_params && JSON.stringify(result.params) === JSON.stringify(job.best_params)
                                    ? '#d4edda'
                                    : 'transparent',
                                }}
                              >
                                <td style={{ padding: '8px' }}>{idx + 1}</td>
                                <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: '12px' }}>
                                  {JSON.stringify(result.params)}
                                </td>
                                <td style={{ padding: '8px', textAlign: 'right', fontFamily: 'monospace' }}>
                                  {result.metrics ? JSON.stringify(result.metrics) : '-'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
