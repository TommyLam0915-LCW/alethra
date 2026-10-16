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

interface MLInstrument {
  instrument_id: string;
  name: string;
  market: string;
  currency: string;
}

interface MLModel {
  model_id: string;
  instrument_id: string;
  model_type: string;
  trained_at: string;
  n_train: number;
  n_val: number;
  n_test: number;
  metrics?: {
    test?: { accuracy?: number };
    baseline_test?: { accuracy?: number };
  };
  data_provenance?: {
    is_synthetic?: boolean;
    provider_name?: string;
    feed_mode?: string;
    last_bar_date?: string;
  };
}

const ML_MODEL_TYPE_LABELS: Record<string, string> = {
  logistic_regression: '邏輯回歸',
  gradient_boosting: '梯度提升',
  baseline: '基線（最常見類別）',
};

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
  const [mlInstruments, setMlInstruments] = useState<MLInstrument[]>([]);
  const [mlModels, setMlModels] = useState<MLModel[]>([]);
  const [showMLForm, setShowMLForm] = useState(false);
  const [mlForm, setMlForm] = useState({
    instrument_id: 'US:AAPL',
    model_type: 'logistic_regression',
    start_date: '',
    end_date: '',
  });
  const [mlTraining, setMlTraining] = useState(false);
  const [mlResult, setMlResult] = useState<any>(null);
  const [mlError, setMlError] = useState<string | null>(null);
  const [expandedModelId, setExpandedModelId] = useState<string | null>(null);
  const [latestPredictions, setLatestPredictions] = useState<Record<string, any>>({});
  const [deletingModelId, setDeletingModelId] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadJobs();
    loadMLData();
  }, [router]);

  const loadMLData = async () => {
    try {
      const [instData, modelData] = await Promise.all([
        api.instruments.list(),
        api.ml.listModels(),
      ]);
      setMlInstruments(instData.instruments || []);
      setMlModels(modelData.models || []);
    } catch (err) {
      console.error('Failed to load ML data:', err);
    }
  };

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

  const handleTrainML = async (e: React.FormEvent) => {
    e.preventDefault();
    setMlTraining(true);
    setMlError(null);
    setMlResult(null);
    try {
      const result = await api.ml.train({
        instrument_id: mlForm.instrument_id,
        model_type: mlForm.model_type,
        ...(mlForm.start_date ? { start_date: mlForm.start_date } : {}),
        ...(mlForm.end_date ? { end_date: mlForm.end_date } : {}),
      });
      setMlResult(result);
      await loadMLData();
    } catch (err: any) {
      console.error('ML training failed:', err);
      setMlError(err?.message || 'ML 訓練失敗');
    } finally {
      setMlTraining(false);
    }
  };

  const handleLatestPrediction = async (modelId: string) => {
    try {
      const result = await api.ml.predictLatest(modelId);
      setLatestPredictions((prev) => ({ ...prev, [modelId]: result }));
    } catch (err: any) {
      console.error('Prediction failed:', err);
      alert(err?.message || '預測失敗');
    }
  };

  const handleDeleteModel = async (modelId: string) => {
    if (!confirm('確定刪除此模型？刪除後無法復原。')) return;
    setDeletingModelId(modelId);
    try {
      await api.ml.deleteModel(modelId);
      setMlModels((prev) => prev.filter((m) => m.model_id !== modelId));
    } catch (err: any) {
      console.error('Delete model failed:', err);
      alert(err?.message || '刪除模型失敗');
    } finally {
      setDeletingModelId(null);
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

      <div style={{ marginTop: '48px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <div>
            <h2 style={{ margin: 0 }}>ML 模型訓練</h2>
            <p style={{ color: '#666', margin: '8px 0 0' }}>
              以實際機器學習分類器預測次日漲跌方向。資料依時間順序分割（訓練 / 驗證 / 測試），無未來資料洩漏。
            </p>
          </div>
          <button onClick={() => setShowMLForm(!showMLForm)} className="btn btn-primary">
            {showMLForm ? '隱藏表單' : '訓練新模型'}
          </button>
        </div>

        <div className="disclaimer" style={{ backgroundColor: '#f8d7da', border: '1px solid #721c24', padding: '16px', borderRadius: '8px', marginBottom: '24px' }}>
          <strong>提醒：</strong>目前資料來源為模擬數據（演示模式），即使標的代碼是真實股票，價格仍是合成資料。模型結果僅供研究流程驗證，不代表真實市場績效。
        </div>

        {showMLForm && (
          <div className="card" style={{ marginBottom: '24px' }}>
            <h3 style={{ marginBottom: '16px' }}>訓練新 ML 模型</h3>
            <form onSubmit={handleTrainML}>
              <div style={{ marginBottom: '16px' }}>
                <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>標的</label>
                <select
                  value={mlForm.instrument_id}
                  onChange={(e) => setMlForm({ ...mlForm, instrument_id: e.target.value })}
                  required
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    border: '1px solid #ddd',
                    borderRadius: '4px',
                    fontSize: '14px',
                  }}
                >
                  {mlInstruments.map((inst) => (
                    <option key={inst.instrument_id} value={inst.instrument_id}>
                      {inst.instrument_id} — {inst.name}（{inst.currency}）
                    </option>
                  ))}
                </select>
              </div>
              <div style={{ marginBottom: '16px' }}>
                <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>模型類型</label>
                <select
                  value={mlForm.model_type}
                  onChange={(e) => setMlForm({ ...mlForm, model_type: e.target.value })}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    border: '1px solid #ddd',
                    borderRadius: '4px',
                    fontSize: '14px',
                  }}
                >
                  <option value="logistic_regression">邏輯回歸</option>
                  <option value="gradient_boosting">梯度提升</option>
                  <option value="baseline">基線（最常見類別）</option>
                </select>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '16px' }}>
                <div>
                  <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>開始日期（選填）</label>
                  <input
                    type="date"
                    value={mlForm.start_date}
                    onChange={(e) => setMlForm({ ...mlForm, start_date: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      border: '1px solid #ddd',
                      borderRadius: '4px',
                      fontSize: '14px',
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>結束日期（選填）</label>
                  <input
                    type="date"
                    value={mlForm.end_date}
                    onChange={(e) => setMlForm({ ...mlForm, end_date: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      border: '1px solid #ddd',
                      borderRadius: '4px',
                      fontSize: '14px',
                    }}
                  />
                </div>
              </div>
              <button type="submit" className="btn btn-primary" disabled={mlTraining}>
                {mlTraining ? '訓練中...' : '開始訓練'}
              </button>
            </form>

            {mlError && (
              <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#f8d7da', border: '1px solid #721c24', borderRadius: '4px', color: '#721c24' }}>
                {mlError}
              </div>
            )}

            {mlResult && mlResult.model_id && (
              <div style={{ marginTop: '24px', padding: '16px', backgroundColor: '#d4edda', border: '1px solid #c3e6cb', borderRadius: '4px' }}>
                <h4 style={{ margin: '0 0 12px', color: '#155724' }}>訓練完成</h4>
                <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                  <div>
                    <span style={{ color: '#666' }}>模型 ID：</span>
                    <span style={{ fontFamily: 'monospace' }}>{truncateId(mlResult.model_id)}</span>
                  </div>
                  <div>
                    <span style={{ color: '#666' }}>測試準確率：</span>
                    <span style={{ fontFamily: 'monospace' }}>
                      {mlResult.metrics?.test?.accuracy ?? '-'}
                      {mlResult.metrics?.baseline_test?.accuracy != null && (
                        <>（基線：{mlResult.metrics.baseline_test.accuracy}）</>
                      )}
                    </span>
                  </div>
                  <div>
                    <span style={{ color: '#666' }}>資料分割：</span>
                    <span style={{ fontFamily: 'monospace' }}>
                      訓練 {mlResult.split?.train_size} / 驗證 {mlResult.split?.val_size} / 測試 {mlResult.split?.test_size}
                    </span>
                  </div>
                  {mlResult.data_provenance?.is_synthetic && (
                    <div style={{ color: '#721c24' }}>
                      ⚠ 訓練資料為模擬數據（{mlResult.data_provenance.provider_name}，{mlResult.data_provenance.feed_mode}）
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        <h3 style={{ marginBottom: '16px' }}>我的模型（{mlModels.length}）</h3>
        {mlModels.length === 0 ? (
          <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
            <p style={{ color: '#666', margin: 0 }}>尚無 ML 模型 — 點擊「訓練新模型」開始</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: '16px' }}>
            {mlModels.map((model) => (
              <div key={model.model_id} className="card">
                <div
                  onClick={() => setExpandedModelId(expandedModelId === model.model_id ? null : model.model_id)}
                  style={{ cursor: 'pointer' }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                    <div>
                      <div style={{ display: 'flex', gap: '24px', marginBottom: '8px', fontSize: '14px' }}>
                        <div>
                          <span style={{ color: '#666' }}>模型 ID：</span>
                          <span style={{ fontFamily: 'monospace' }}>{truncateId(model.model_id)}</span>
                        </div>
                        <div>
                          <span style={{ color: '#666' }}>標的：</span>
                          <span style={{ fontFamily: 'monospace' }}>{model.instrument_id}</span>
                        </div>
                        <div>
                          <span style={{ color: '#666' }}>類型：</span>
                          {ML_MODEL_TYPE_LABELS[model.model_type] || model.model_type}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: '24px', fontSize: '13px', color: '#666' }}>
                        <div>
                          測試準確率：
                          <span style={{ fontFamily: 'monospace' }}>
                            {model.metrics?.test?.accuracy != null ? model.metrics.test.accuracy : '-'}
                          </span>
                        </div>
                        <div>訓練時間：{new Date(model.trained_at).toLocaleString('zh-Hant')}</div>
                      </div>
                      {model.data_provenance?.is_synthetic && (
                        <span style={{
                          display: 'inline-block',
                          marginTop: '8px',
                          padding: '2px 10px',
                          borderRadius: '10px',
                          fontSize: '12px',
                          backgroundColor: '#f8d7da',
                          color: '#721c24',
                        }}>
                          模擬數據訓練
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleLatestPrediction(model.model_id);
                        }}
                        className="btn btn-secondary"
                        style={{ padding: '6px 14px' }}
                      >
                        最新預測
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteModel(model.model_id);
                        }}
                        disabled={deletingModelId === model.model_id}
                        className="btn btn-secondary"
                        style={{ padding: '6px 14px', color: '#a94442' }}
                      >
                        {deletingModelId === model.model_id ? '刪除中…' : '刪除'}
                      </button>
                    </div>
                  </div>
                </div>

                {latestPredictions[model.model_id] && (
                  <div style={{
                    marginTop: '12px',
                    padding: '12px',
                    backgroundColor: '#cce5ff',
                    border: '1px solid #b8daff',
                    borderRadius: '4px',
                    fontSize: '13px',
                  }}>
                    <strong>最新預測（{latestPredictions[model.model_id].latest_prediction?.date}）：</strong>
                    <span style={{ fontFamily: 'monospace' }}>
                      {latestPredictions[model.model_id].latest_prediction?.signal}
                      （信心 {latestPredictions[model.model_id].latest_prediction?.confidence}）
                    </span>
                    <span style={{ color: '#666' }}> — 僅供研究用途，非交易建議</span>
                  </div>
                )}

                {expandedModelId === model.model_id && (
                  <div style={{ marginTop: '16px', borderTop: '1px solid #eee', paddingTop: '16px' }}>
                    <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#666' }}>評估指標</h4>
                    <pre style={{
                      backgroundColor: '#f8f9fa',
                      padding: '12px',
                      borderRadius: '4px',
                      fontSize: '13px',
                      overflow: 'auto',
                      marginBottom: '16px',
                    }}>
                      {JSON.stringify(model.metrics, null, 2)}
                    </pre>
                    <h4 style={{ marginBottom: '8px', fontSize: '14px', color: '#666' }}>資料來源（Provenance）</h4>
                    <pre style={{
                      backgroundColor: '#f8f9fa',
                      padding: '12px',
                      borderRadius: '4px',
                      fontSize: '13px',
                      overflow: 'auto',
                      margin: 0,
                    }}>
                      {JSON.stringify(model.data_provenance, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
