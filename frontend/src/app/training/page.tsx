'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError, API_BASE_URL } from '@/lib/api';
import AppShell from '@/components/shell/AppShell';
import PersonalizationPanel from '@/components/training/PersonalizationPanel';

interface TrainingJob {
  id: string;
  experiment_id: string | null;
  status: string;
  created_at: string;
  completed_at: string | null;
  parameter_grid: Record<string, any[]>;
  results: any[] | null;
  best_params: Record<string, any> | null;
  error_details: string | null;
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
  training_job_id?: string | null;
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

interface QuotaStatus {
  quota_type: string;
  limit: number | null;
  used: number;
  reserved: number;
  remaining: number | null;
  unlimited: boolean;
  entitlement_active: boolean;
  source: 'entitlement' | 'free_plan';
  expiry_date: string | null;
  support_tier: string | null;
}

const ACTIVE_JOB_STATUSES = new Set(['pending', 'running']);
// A queued job that has not moved in this long is almost always waiting for a
// worker that is not running in this environment — say so instead of spinning.
const QUEUE_STALE_MS = 120_000;

const ML_MODEL_TYPE_LABELS: Record<string, string> = {
  logistic_regression: '邏輯回歸',
  gradient_boosting: '梯度提升',
  baseline: '基線（最常見類別）',
};

const SUPPORT_TIER_LABELS: Record<string, string> = {
  standard: '標準支援',
  priority: '優先支援',
  dedicated: '專屬支援',
};

// The API returns structured 429/403 refusals for quota and entitlement
// problems. Surface the actual reason instead of the raw error code.
function describeMlRefusal(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return 'AI 使用額度已用完：每次訓練或預測需要 1 個 ai_usage 單位。請查看上方額度，或於「訂閱與帳單」調整方案。';
    }
    if (err.status === 403) {
      return 'Pilot 授權已過期或停用，無法執行 AI 訓練與預測。請於「Pilot 計劃」查看授權狀態。';
    }
  }
  return (err as any)?.message || fallback;
}

// The card header shows a truncated id; a caller needs the whole one.
function modelApiSnippet(modelId: string): string {
  return [
    `curl -X POST "${API_BASE_URL}/api/v1/ml/models/${modelId}/predict-latest" \\`,
    '  -H "Authorization: Bearer <你的 API 金鑰>"',
  ].join('\n');
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
  const [mlInstruments, setMlInstruments] = useState<MLInstrument[]>([]);
  const [mlModels, setMlModels] = useState<MLModel[]>([]);
  const [quota, setQuota] = useState<QuotaStatus | null>(null);
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
  const [copiedModelApi, setCopiedModelApi] = useState<string | null>(null);

  const hasActiveJobs = jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status));
  const quotaExhausted = quota !== null && quota.remaining !== null && quota.remaining <= 0;
  const mlBlocked = quota !== null && (!quota.entitlement_active || quotaExhausted);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadJobs();
    loadMLData();
  }, [router]);

  // Queued/running jobs move without any click: poll while work is in flight so
  // the status, and the model it produces, appear on their own.
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = setInterval(() => {
      loadJobs();
      loadMLData();
    }, 4000);
    return () => clearInterval(timer);
  }, [hasActiveJobs]);

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
      // Cancelling a queued job returns its reserved quota unit; refresh the
      // quota card too, otherwise it keeps showing the stale reservation.
      await Promise.all([loadJobs(), loadMLData()]);
    } catch (err) {
      console.error('Failed to cancel job:', err);
      alert('取消任務失敗');
    }
  };

  const loadMLData = async () => {
    try {
      const [instData, modelData, quotaData] = await Promise.all([
        api.instruments.list(),
        api.ml.listModels(),
        api.ml.entitlement(),
      ]);
      setMlInstruments(instData.instruments || []);
      setMlModels(modelData.models || []);
      setQuota(quotaData);
    } catch (err) {
      console.error('Failed to load ML data:', err);
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
      await Promise.all([loadJobs(), loadMLData()]);
    } catch (err: any) {
      console.error('ML training failed:', err);
      setMlError(describeMlRefusal(err, 'ML 訓練失敗'));
      // A refusal usually updates the numbers that caused it; refresh them.
      loadMLData();
    } finally {
      setMlTraining(false);
    }
  };

  const handleLatestPrediction = async (modelId: string) => {
    try {
      const result = await api.ml.predictLatest(modelId);
      setLatestPredictions((prev) => ({ ...prev, [modelId]: result }));
      // Inference settles one ai_usage unit server-side; refresh the quota card
      // so it does not keep showing the pre-inference balance.
      loadMLData();
    } catch (err: any) {
      console.error('Prediction failed:', err);
      alert(describeMlRefusal(err, '預測失敗'));
      loadMLData();
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

  const handleCopyModelApi = async (modelId: string) => {
    try {
      await navigator.clipboard.writeText(modelApiSnippet(modelId));
      setCopiedModelApi(modelId);
      setTimeout(() => {
        setCopiedModelApi((current) => (current === modelId ? null : current));
      }, 2000);
    } catch (err) {
      // Clipboard access can be denied (insecure origin, permission); the
      // snippet is still on screen to select by hand.
      console.error('Copy model API snippet failed:', err);
      setCopiedModelApi(null);
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

  const isQueueStale = (job: TrainingJob) =>
    ACTIVE_JOB_STATUSES.has(job.status) &&
    Date.now() - new Date(job.created_at).getTime() > QUEUE_STALE_MS;

  const bestModelId = (job: TrainingJob): string | null =>
    job.best_params && typeof job.best_params.model_id === 'string'
      ? job.best_params.model_id
      : null;

  return (
    <AppShell active="training">
      <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px', gap: '16px', flexWrap: 'wrap' }}>
          <h1 style={{ margin: 0 }}>訓練 / 最佳化任務</h1>
          <div style={{ display: 'flex', gap: '12px' }}>
            <button onClick={() => setShowForm(!showForm)} className="btn btn-primary">
              {showForm ? '隱藏表單' : '建立訓練任務'}
            </button>
          </div>
        </div>

        {quota && (
          <div
            className="card"
            data-testid="ml-quota"
            style={{
              display: 'flex',
              gap: '20px',
              flexWrap: 'wrap',
              alignItems: 'center',
              padding: '14px 18px',
              marginBottom: '24px',
              fontSize: '13px',
            }}
          >
            <span style={{ color: 'var(--text-secondary)' }}>AI 使用額度</span>
            <span
              style={{
                fontSize: '15px',
                fontWeight: 600,
                color: quotaExhausted ? 'var(--negative)' : 'var(--text-primary)',
              }}
            >
              {quota.unlimited ? '無限' : `剩餘 ${quota.remaining} / ${quota.limit}`}
            </span>
            <span style={{ color: 'var(--text-muted)' }}>
              已用 {quota.used}
              {quota.reserved > 0 ? ` · 進行中 ${quota.reserved}` : ''}
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              {quota.source === 'entitlement'
                ? `Pilot 授權${quota.support_tier ? ` · ${SUPPORT_TIER_LABELS[quota.support_tier] || quota.support_tier}` : ''}`
                : '免費方案'}
            </span>
            {quota.expiry_date && (
              <span style={{ color: 'var(--text-muted)' }}>
                有效期至 {new Date(quota.expiry_date).toLocaleDateString('zh-Hant')}
              </span>
            )}
            {!quota.entitlement_active && (
              <span style={{ color: 'var(--negative)' }}>
                授權已過期或停用 — 訓練與預測會被拒絕
              </span>
            )}
            {quota.entitlement_active && quotaExhausted && (
              <span style={{ color: 'var(--warning)' }}>
                額度已用完 — 訓練與預測會被拒絕（429）
              </span>
            )}
          </div>
        )}

        <div className="disclaimer" style={{ marginBottom: '24px' }}>
          <strong>提醒：</strong>參數搜尋採用網格搜尋法 — 非機器學習訓練。結果為演示資料的回測指標。
        </div>

        <PersonalizationPanel />

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
                    border: '1px solid var(--border-strong)',
                    borderRadius: '4px',
                    fontSize: '14px',
                    background: 'var(--surface-2)',
                    color: 'var(--text-primary)',
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
                    border: '1px solid var(--border-strong)',
                    borderRadius: '4px',
                    fontSize: '14px',
                    fontFamily: 'monospace',
                    resize: 'vertical',
                    background: 'var(--surface-2)',
                    color: 'var(--text-primary)',
                  }}
                />
                <small style={{ color: 'var(--text-muted)', marginTop: '4px', display: 'block' }}>
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
          <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--negative)' }}>
            {error}
          </div>
        ) : jobs.length === 0 ? (
          <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
            <p style={{ color: 'var(--text-secondary)', margin: 0 }}>尚無訓練任務</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: '16px' }}>
            {jobs.map((job) => (
              <div key={job.id} className="card">
                <div onClick={() => toggleExpand(job.id)} style={{ cursor: 'pointer' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '12px' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', gap: '24px', marginBottom: '8px', fontSize: '14px', flexWrap: 'wrap' }}>
                        <div>
                          <span style={{ color: 'var(--text-secondary)' }}>任務 ID：</span>
                          <span style={{ fontFamily: 'monospace' }}>{truncateId(job.id)}</span>
                        </div>
                        {job.experiment_id && (
                          <div>
                            <span style={{ color: 'var(--text-secondary)' }}>實驗：</span>
                            <Link
                              href={`/experiments/${job.experiment_id}`}
                              style={{ fontFamily: 'monospace', color: 'var(--primary)', textDecoration: 'none' }}
                              onClick={(e) => e.stopPropagation()}
                            >
                              {truncateId(job.experiment_id)}
                            </Link>
                          </div>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: '24px', fontSize: '13px', color: 'var(--text-muted)', flexWrap: 'wrap' }}>
                        <div>建立：{new Date(job.created_at).toLocaleString('zh-Hant')}</div>
                        {job.completed_at && (
                          <div>完成：{new Date(job.completed_at).toLocaleString('zh-Hant')}</div>
                        )}
                      </div>
                      {job.best_params && Object.keys(job.best_params).length > 0 && (
                        <div style={{ marginTop: '8px', fontSize: '13px' }}>
                          <span style={{ color: 'var(--text-secondary)' }}>最佳參數：</span>
                          <span style={{ fontFamily: 'monospace', color: 'var(--positive)' }}>
                            {JSON.stringify(job.best_params)}
                          </span>
                        </div>
                      )}
                      {bestModelId(job) && (
                        <div style={{ marginTop: '8px', fontSize: '13px' }}>
                          <span style={{ color: 'var(--text-secondary)' }}>產出模型：</span>
                          <a
                            href={`#model-${bestModelId(job)}`}
                            style={{ fontFamily: 'monospace', color: 'var(--primary)', textDecoration: 'none' }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {truncateId(bestModelId(job) as string)}
                          </a>
                        </div>
                      )}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-end' }}>
                      {getStatusBadge(job.status)}
                      {isQueueStale(job) && (
                        <span
                          style={{
                            fontSize: '12px',
                            color: 'var(--warning)',
                            maxWidth: '240px',
                            textAlign: 'right',
                          }}
                        >
                          已等待超過 2 分鐘：背景訓練工作可能未啟動（此環境需運行
                          training worker）
                        </span>
                      )}
                      {ACTIVE_JOB_STATUSES.has(job.status) && (
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
                  <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-strong)', paddingTop: '16px' }}>
                    {job.error_details && (
                      <div style={{
                        backgroundColor: 'rgba(239,68,68,0.12)',
                        border: '1px solid var(--negative)',
                        color: 'var(--text-primary)',
                        padding: '12px',
                        borderRadius: '4px',
                        marginBottom: '16px',
                      }}>
                        <h4 style={{ margin: '0 0 8px', fontSize: '14px', color: 'var(--negative)' }}>失敗原因</h4>
                        <pre style={{ margin: 0, fontSize: '13px', whiteSpace: 'pre-wrap' }}>
                          {job.error_details}
                        </pre>
                      </div>
                    )}
                    <h4 style={{ marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>參數網格</h4>
                    <pre style={{
                      backgroundColor: 'var(--surface-2)',
                      border: '1px solid var(--border-strong)',
                      color: 'var(--text-primary)',
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
                        backgroundColor: 'rgba(34,197,94,0.12)',
                        padding: '12px',
                        borderRadius: '4px',
                        marginBottom: '16px',
                        border: '1px solid var(--positive)',
                      }}>
                        <h4 style={{ marginBottom: '8px', fontSize: '14px', color: 'var(--positive)' }}>
                          最佳參數組合
                        </h4>
                        <pre style={{
                          backgroundColor: 'var(--surface-2)',
                          color: 'var(--text-primary)',
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
                        <h4 style={{ marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>結果排名</h4>
                        <div style={{ overflowX: 'auto' }}>
                          <table style={{
                            width: '100%',
                            borderCollapse: 'collapse',
                            fontSize: '13px',
                          }}>
                            <thead>
                              <tr style={{ backgroundColor: 'var(--surface-2)', borderBottom: '2px solid var(--border-strong)' }}>
                                <th style={{ padding: '8px', textAlign: 'left', color: 'var(--text-secondary)' }}>排名</th>
                                <th style={{ padding: '8px', textAlign: 'left', color: 'var(--text-secondary)' }}>參數</th>
                                <th style={{ padding: '8px', textAlign: 'right', color: 'var(--text-secondary)' }}>指標</th>
                              </tr>
                            </thead>
                            <tbody>
                              {job.results.map((result, idx) => (
                                <tr
                                  key={idx}
                                  style={{
                                    borderBottom: '1px solid var(--border-strong)',
                                    backgroundColor: job.best_params && JSON.stringify(result.params) === JSON.stringify(job.best_params)
                                      ? 'rgba(34,197,94,0.12)'
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
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', gap: '16px', flexWrap: 'wrap' }}>
            <div>
              <h2 style={{ margin: 0 }}>ML 模型訓練</h2>
              <p style={{ color: 'var(--text-secondary)', margin: '8px 0 0' }}>
                以實際機器學習分類器預測次日漲跌方向。資料依時間順序分割（訓練 / 驗證 / 測試），無未來資料洩漏。
              </p>
            </div>
            <button onClick={() => setShowMLForm(!showMLForm)} className="btn btn-primary">
              {showMLForm ? '隱藏表單' : '訓練新模型'}
            </button>
          </div>

          <div className="disclaimer" style={{ marginBottom: '24px' }}>
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
                      border: '1px solid var(--border-strong)',
                      borderRadius: '4px',
                      fontSize: '14px',
                      background: 'var(--surface-2)',
                      color: 'var(--text-primary)',
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
                      border: '1px solid var(--border-strong)',
                      borderRadius: '4px',
                      fontSize: '14px',
                      background: 'var(--surface-2)',
                      color: 'var(--text-primary)',
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
                        border: '1px solid var(--border-strong)',
                        borderRadius: '4px',
                        fontSize: '14px',
                        background: 'var(--surface-2)',
                        color: 'var(--text-primary)',
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
                        border: '1px solid var(--border-strong)',
                        borderRadius: '4px',
                        fontSize: '14px',
                        background: 'var(--surface-2)',
                        color: 'var(--text-primary)',
                      }}
                    />
                  </div>
                </div>
                {mlBlocked && (
                  <div style={{ marginBottom: '16px', padding: '12px', borderRadius: '4px', background: 'rgba(245,158,11,0.12)', border: '1px solid var(--warning)', color: 'var(--text-primary)', fontSize: '13px' }}>
                    {!quota?.entitlement_active
                      ? 'Pilot 授權已過期或停用，目前無法建立訓練任務。請於「Pilot 計劃」查看授權狀態。'
                      : 'AI 使用額度已用完，目前無法建立訓練任務。請於「訂閱與帳單」調整方案。'}
                  </div>
                )}
                <button type="submit" className="btn btn-primary" disabled={mlTraining || mlBlocked}>
                  {mlTraining ? '訓練中...' : '開始訓練'}
                </button>
              </form>

              {mlError && (
                <div style={{ marginTop: '16px', padding: '12px', backgroundColor: 'rgba(239,68,68,0.12)', border: '1px solid var(--negative)', borderRadius: '4px', color: 'var(--text-primary)' }}>
                  {mlError}
                </div>
              )}

              {mlResult && mlResult.job_id && (
                <div style={{ marginTop: '24px', padding: '16px', backgroundColor: 'rgba(34,197,94,0.12)', border: '1px solid var(--positive)', borderRadius: '4px' }}>
                  <h4 style={{ margin: '0 0 12px', color: 'var(--positive)' }}>訓練任務已建立</h4>
                  <div style={{ display: 'grid', gap: '8px', fontSize: '13px' }}>
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>任務 ID：</span>
                      <span style={{ fontFamily: 'monospace' }}>{truncateId(mlResult.job_id)}</span>
                    </div>
                    <div style={{ color: 'var(--text-primary)' }}>
                      模型訓練由背景工作執行，完成後會出現在下方「我的模型」，亦可於上方任務列表查看進度。
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          <h3 style={{ marginBottom: '16px' }}>我的模型（{mlModels.length}）</h3>
          {mlModels.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
              <p style={{ color: 'var(--text-secondary)', margin: 0 }}>尚無 ML 模型 — 點擊「訓練新模型」開始</p>
            </div>
          ) : (
            <div style={{ display: 'grid', gap: '16px' }}>
              {mlModels.map((model) => (
                <div key={model.model_id} id={`model-${model.model_id}`} className="card">
                  <div
                    onClick={() => setExpandedModelId(expandedModelId === model.model_id ? null : model.model_id)}
                    style={{ cursor: 'pointer' }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                      <div>
                        <div style={{ display: 'flex', gap: '24px', marginBottom: '8px', fontSize: '14px', flexWrap: 'wrap' }}>
                          <div>
                            <span style={{ color: 'var(--text-secondary)' }}>模型 ID：</span>
                            <span style={{ fontFamily: 'monospace' }}>{truncateId(model.model_id)}</span>
                          </div>
                          <div>
                            <span style={{ color: 'var(--text-secondary)' }}>標的：</span>
                            <span style={{ fontFamily: 'monospace' }}>{model.instrument_id}</span>
                          </div>
                          <div>
                            <span style={{ color: 'var(--text-secondary)' }}>類型：</span>
                            {ML_MODEL_TYPE_LABELS[model.model_type] || model.model_type}
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: '24px', fontSize: '13px', color: 'var(--text-muted)', flexWrap: 'wrap' }}>
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
                          style={{ padding: '6px 14px', color: 'var(--negative)' }}
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
                      backgroundColor: 'rgba(59,130,246,0.12)',
                      border: '1px solid var(--primary)',
                      borderRadius: '4px',
                      fontSize: '13px',
                    }}>
                      <strong>最新預測（{latestPredictions[model.model_id].latest_prediction?.date}）：</strong>
                      <span style={{ fontFamily: 'monospace' }}>
                        {latestPredictions[model.model_id].latest_prediction?.signal}
                        {' '}（訊號邊際 {latestPredictions[model.model_id].latest_prediction?.confidence}，未校準）
                      </span>
                      <div style={{ marginTop: '6px', color: 'var(--text-muted)', fontSize: '12px' }}>
                        定義：2 × |P(up) − 0.5|，即模型輸出偏離五五分的幅度。數值越接近 1
                        只代表輸出越偏向單一方向，並非準確率或可信度；本模型未做機率校準。
                      </div>
                      <span style={{ color: 'var(--text-muted)' }}> — 僅供研究用途，非交易建議</span>
                    </div>
                  )}

                  {expandedModelId === model.model_id && (
                    <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-strong)', paddingTop: '16px' }}>
                      <h4 style={{ marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>評估指標</h4>
                      <pre style={{
                        backgroundColor: 'var(--surface-2)',
                        border: '1px solid var(--border-strong)',
                        color: 'var(--text-primary)',
                        padding: '12px',
                        borderRadius: '4px',
                        fontSize: '13px',
                        overflow: 'auto',
                        marginBottom: '16px',
                      }}>
                        {JSON.stringify(model.metrics, null, 2)}
                      </pre>
                      <h4 style={{ marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>資料來源（Provenance）</h4>
                      <pre style={{
                        backgroundColor: 'var(--surface-2)',
                        border: '1px solid var(--border-strong)',
                        color: 'var(--text-primary)',
                        padding: '12px',
                        borderRadius: '4px',
                        fontSize: '13px',
                        overflow: 'auto',
                        margin: 0,
                      }}>
                        {JSON.stringify(model.data_provenance, null, 2)}
                      </pre>
                      <h4 style={{ margin: '16px 0 8px', fontSize: '14px', color: 'var(--text-secondary)' }}>血緣</h4>
                      <div style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                        <div>
                          <span style={{ color: 'var(--text-secondary)' }}>來源訓練任務：</span>
                          <span style={{ fontFamily: 'monospace' }}>
                            {model.training_job_id || '未記錄（舊模型 artifact 無任務欄位）'}
                          </span>
                        </div>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '16px 0 8px', gap: '12px' }}>
                        <h4 style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)' }}>以 API 呼叫此模型</h4>
                        <button
                          onClick={() => handleCopyModelApi(model.model_id)}
                          className="btn btn-secondary"
                          style={{ padding: '4px 12px', fontSize: '12px' }}
                          data-testid={`model-api-copy-${model.model_id}`}
                        >
                          {copiedModelApi === model.model_id ? '已複製' : '複製指令'}
                        </button>
                      </div>
                      <pre
                        data-testid={`model-api-${model.model_id}`}
                        style={{
                          backgroundColor: 'var(--surface-2)',
                          border: '1px solid var(--border-strong)',
                          color: 'var(--text-primary)',
                          padding: '12px',
                          borderRadius: '4px',
                          fontSize: '13px',
                          overflow: 'auto',
                          margin: 0,
                        }}
                      >
                        {modelApiSnippet(model.model_id)}
                      </pre>
                      <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-muted)' }}>
                        需要具備「模型推論」或「寫入」權限的 API 金鑰（於{' '}
                        <Link href="/api-keys" style={{ color: 'var(--primary)' }}>API 金鑰</Link>
                        {' '}頁建立）。每次呼叫扣 1 個 AI 額度，額度不足時回傳 429；
                        僅供研究用途，非交易建議。
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
