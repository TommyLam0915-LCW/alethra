'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface InferenceRun {
  id: string;
  model_version_id: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  input_spec: any;
  output_signals: any;
}

export default function InferencePage() {
  const router = useRouter();
  const [runs, setRuns] = useState<InferenceRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    model_version_id: '',
    input_spec: '',
  });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadRuns();
  }, [router]);

  const loadRuns = async () => {
    try {
      const data = await api.inference.listRuns();
      setRuns(data);
      setError(null);
    } catch (err) {
      console.error('Failed to load inference runs:', err);
      setError('載入推論紀錄失敗');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateRun = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      const inputSpec = formData.input_spec ? JSON.parse(formData.input_spec) : {};
      await api.inference.createRun({
        model_version_id: formData.model_version_id,
        input_spec: inputSpec,
      });
      setShowForm(false);
      setFormData({ model_version_id: '', input_spec: '' });
      await loadRuns();
    } catch (err) {
      console.error('Failed to create run:', err);
      alert('建立推論任務失敗');
    } finally {
      setCreating(false);
    }
  };

  const toggleExpand = (runId: string) => {
    setExpandedRunId(expandedRunId === runId ? null : runId);
  };

  const getStatusBadge = (status: string) => {
    const colors: Record<string, { bg: string; text: string }> = {
      pending: { bg: '#fff3cd', text: '#856404' },
      running: { bg: '#cce5ff', text: '#004085' },
      completed: { bg: '#d4edda', text: '#155724' },
      failed: { bg: '#f8d7da', text: '#721c24' },
    };
    const labels: Record<string, string> = {
      pending: '待處理',
      running: '執行中',
      completed: '已完成',
      failed: '失敗',
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
        <h1>推論紀錄</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/dashboard" className="btn btn-secondary">
            返回儀表板
          </Link>
          <button onClick={() => setShowForm(!showForm)} className="btn btn-primary">
            {showForm ? '隱藏表單' : '建立推論任務'}
          </button>
          <button onClick={handleLogout} className="btn btn-secondary">
            登出
          </button>
        </div>
      </div>

      <div className="disclaimer" style={{ backgroundColor: '#fff3cd', border: '1px solid #ffc107', padding: '16px', borderRadius: '8px', marginBottom: '24px' }}>
        <strong>提醒：</strong>模型推論為規則式運算，使用演示資料 — 非 AI/ML 預測。
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '16px' }}>建立新推論任務</h3>
          <form onSubmit={handleCreateRun}>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                模型版本 ID
              </label>
              <input
                type="text"
                value={formData.model_version_id}
                onChange={(e) => setFormData({ ...formData, model_version_id: e.target.value })}
                placeholder="輸入模型版本 ID"
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
                輸入規格 (JSON)
              </label>
              <textarea
                value={formData.input_spec}
                onChange={(e) => setFormData({ ...formData, input_spec: e.target.value })}
                placeholder='{"symbol": "BTC-USD", "indicators": ["sma", "rsi"]}'
                rows={6}
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
      ) : runs.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666' }}>尚無推論紀錄</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {runs.map((run) => (
            <div key={run.id} className="card" style={{ cursor: 'pointer' }}>
              <div onClick={() => toggleExpand(run.id)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '12px' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', gap: '24px', marginBottom: '8px', fontSize: '14px' }}>
                      <div>
                        <span style={{ color: '#666' }}>任務 ID：</span>
                        <span style={{ fontFamily: 'monospace' }}>{truncateId(run.id)}</span>
                      </div>
                      <div>
                        <span style={{ color: '#666' }}>模型版本：</span>
                        <span style={{ fontFamily: 'monospace' }}>{truncateId(run.model_version_id)}</span>
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '24px', fontSize: '13px', color: '#666' }}>
                      <div>建立：{new Date(run.created_at).toLocaleString('zh-Hant')}</div>
                      {run.completed_at && (
                        <div>完成：{new Date(run.completed_at).toLocaleString('zh-Hant')}</div>
                      )}
                    </div>
                  </div>
                  {getStatusBadge(run.status)}
                </div>
              </div>

              {expandedRunId === run.id && (
                <div style={{ marginTop: '16px', borderTop: '1px solid #eee', paddingTop: '16px' }}>
                  <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#666' }}>輸入規格</h4>
                  <pre style={{
                    backgroundColor: '#f8f9fa',
                    padding: '12px',
                    borderRadius: '4px',
                    fontSize: '13px',
                    overflow: 'auto',
                    marginBottom: '16px',
                  }}>
                    {JSON.stringify(run.input_spec, null, 2)}
                  </pre>

                  {run.output_signals && (
                    <>
                      <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#666' }}>輸出訊號</h4>
                      <pre style={{
                        backgroundColor: '#f8f9fa',
                        padding: '12px',
                        borderRadius: '4px',
                        fontSize: '13px',
                        overflow: 'auto',
                      }}>
                        {JSON.stringify(run.output_signals, null, 2)}
                      </pre>
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
