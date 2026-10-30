'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';

interface InferenceRun {
  id: string;
  model_version_id: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  input_spec: { symbol?: string; start_date?: string; end_date?: string; [key: string]: unknown };
  output_signals: unknown;
}

interface ModelRecord {
  id: string;
  title: string;
  status: string;
  active_version_id: string | null;
}

interface ModelVersion {
  id: string;
  version_number: number;
  model_type: string;
  created_at: string;
  locked_at: string | null;
  revoked_at: string | null;
}

interface VersionOption {
  id: string;
  version_number: number;
  model_type: string;
  created_at: string;
  revoked_at: string | null;
  record_title: string;
}

const MODEL_TYPE_LABELS: Record<string, string> = {
  rule_based: '規則型（逐日計算訊號）',
  prompt_based_research_assistant: '提示詞研究型（只回傳說明性 HOLD）',
  future_external_model_reference: '未來外部模型（目前不可執行）',
};

const RUN_STATUS_LABELS: Record<string, string> = {
  pending: '待處理',
  running: '執行中',
  completed: '已完成',
  failed: '失敗',
};

const RUN_STATUS_COLORS: Record<string, { bg: string; text: string }> = {
  pending: { bg: '#fff3cd', text: '#856404' },
  running: { bg: '#cce5ff', text: '#004085' },
  completed: { bg: '#d4edda', text: '#155724' },
  failed: { bg: '#f8d7da', text: '#721c24' },
};

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.message) return err.message;
  return err instanceof Error && err.message ? err.message : fallback;
}

function formatTime(value: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-Hant');
}

export default function InferencePage() {
  const router = useRouter();
  const [runs, setRuns] = useState<InferenceRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);

  const [versions, setVersions] = useState<VersionOption[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(true);
  const [versionsError, setVersionsError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [versionId, setVersionId] = useState('');
  const [symbol, setSymbol] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadRuns();
    loadVersions();
  }, [router]);

  const loadRuns = async () => {
    setRunsLoading(true);
    try {
      const data = await api.inference.listRuns();
      setRuns(Array.isArray(data) ? data : []);
      setRunsError(null);
    } catch (err) {
      console.error('Failed to load inference runs:', err);
      setRunsError(describeError(err, '無法載入推論紀錄。'));
    } finally {
      setRunsLoading(false);
    }
  };

  const loadVersions = async () => {
    setVersionsLoading(true);
    try {
      const records = await api.modelResearch.list();
      const list: ModelRecord[] = Array.isArray(records) ? records : [];
      const perRecord = await Promise.all(
        list.map(async (record) => {
          const recordVersions = await api.modelResearch.versions(record.id);
          const rows: ModelVersion[] = Array.isArray(recordVersions) ? recordVersions : [];
          return rows.map((version) => ({
            id: version.id,
            version_number: version.version_number,
            model_type: version.model_type,
            created_at: version.created_at,
            revoked_at: version.revoked_at,
            record_title: record.title,
          }));
        }),
      );
      const flattened = perRecord.flat();
      flattened.sort((a, b) =>
        a.record_title === b.record_title
          ? b.version_number - a.version_number
          : a.record_title.localeCompare(b.record_title, 'zh-Hant'),
      );
      setVersions(flattened);
      setVersionsError(null);
    } catch (err) {
      console.error('Failed to load model versions:', err);
      // The raw detail alone ("Internal Server Error") does not say which
      // panel failed, so the context is always prefixed.
      setVersionsError(`無法載入你的模型版本：${describeError(err, '請稍後再試。')}`);
    } finally {
      setVersionsLoading(false);
    }
  };

  const selectedVersion = versions.find((version) => version.id === versionId) ?? null;
  const versionLabelById = new Map(
    versions.map((version) => [version.id, `${version.record_title} · 版本 ${version.version_number}`]),
  );

  const validate = (): string | null => {
    if (!versionId) return '請先選擇一個模型版本。';
    if (!symbol.trim()) return '請輸入市場代號，例如 DEMO-ALPHA。';
    if (!startDate || !endDate) return '請選擇開始與結束日期。';
    if (endDate < startDate) return '結束日期不可早於開始日期。';
    return null;
  };

  const handleCreateRun = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setNotice(null);
    const problem = validate();
    if (problem) {
      setFormError(problem);
      return;
    }
    setCreating(true);
    try {
      const run = await api.inference.createRun({
        model_version_id: versionId,
        input_spec: {
          symbol: symbol.trim(),
          start_date: startDate,
          end_date: endDate,
        },
      });
      const label = selectedVersion
        ? `${selectedVersion.record_title} · 版本 ${selectedVersion.version_number}`
        : versionId;
      const finished = run && run.status === 'failed'
        ? '任務已建立，但執行失敗；失敗原因見下方紀錄（不會扣減 AI 額度）。'
        : '任務已建立，結果列於下方。';
      setNotice(`${label}：${finished}`);
      await loadRuns();
    } catch (err) {
      console.error('Failed to create run:', err);
      setFormError(`${describeError(err, '建立推論任務失敗')}（輸入內容已保留，可直接重試）`);
    } finally {
      setCreating(false);
    }
  };

  const toggleExpand = (runId: string) => {
    setExpandedRunId(expandedRunId === runId ? null : runId);
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    router.push('/');
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h1>推論紀錄</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/models" className="btn btn-secondary">管理模型版本</Link>
          <Link href="/dashboard" className="btn btn-secondary">返回我的實驗</Link>
          <button
            type="button"
            onClick={handleLogout}
            className="btn btn-secondary"
            data-testid="inference-logout"
          >
            登出
          </button>
          <button
            type="button"
            onClick={() => setShowForm(!showForm)}
            className="btn btn-primary"
            data-testid="inference-toggle-form"
          >
            {showForm ? '隱藏表單' : '建立推論任務'}
          </button>
        </div>
      </div>

      <p style={{ color: 'var(--text-secondary)', marginBottom: '20px', fontSize: '14px' }}>
        選一個你自己的模型版本，指定市場代號與歷史期間，系統會在下方的紀錄中列出逐日訊號。
        目前只有「規則型」版本會逐日計算；其他版本類型會如實回報不可執行或只提供說明。
      </p>

      <div className="disclaimer" style={{ backgroundColor: '#fff3cd', border: '1px solid #ffc107', padding: '16px', borderRadius: '8px', marginBottom: '24px' }}>
        <strong>提醒：</strong>模型推論為規則式運算，使用演示資料 — 非 AI/ML 預測。
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '16px' }}>建立新推論任務</h3>

          {versionsLoading ? (
            <p data-testid="inference-versions-loading" style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
              正在載入你的模型版本…
            </p>
          ) : versionsError ? (
            <div
              role="alert"
              data-testid="inference-versions-error"
              style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '16px' }}
            >
              <span style={{ color: '#721c24', fontSize: '14px' }}>{versionsError}</span>
              <button type="button" className="btn btn-secondary" onClick={loadVersions}>
                重試載入版本
              </button>
            </div>
          ) : versions.length === 0 ? (
            <div data-testid="inference-versions-empty" style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
              你的帳號尚無模型版本。先到{' '}
              <Link href="/models" style={{ color: 'var(--primary)' }}>模型研究記錄</Link>{' '}
              建立記錄與版本，之後就能在這裡選用。
            </div>
          ) : (
            <form onSubmit={handleCreateRun}>
              <div style={{ marginBottom: '16px' }}>
                <label htmlFor="inference-version" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                  模型版本
                </label>
                <select
                  id="inference-version"
                  data-testid="inference-version"
                  value={versionId}
                  onChange={(e) => setVersionId(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    fontSize: '14px',
                    background: 'var(--surface-1, #fff)',
                  }}
                >
                  <option value="">請選擇模型版本</option>
                  {versions.map((version) => (
                    <option key={version.id} value={version.id}>
                      {version.record_title} · 版本 {version.version_number}
                      {version.revoked_at ? '（已撤銷）' : ''}
                    </option>
                  ))}
                </select>
                {selectedVersion && (
                  <p data-testid="inference-version-detail" style={{ margin: '8px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                    類型：{MODEL_TYPE_LABELS[selectedVersion.model_type] ?? selectedVersion.model_type}
                    {' · '}建立於 {formatTime(selectedVersion.created_at)}
                    {selectedVersion.revoked_at ? ' · 此版本已撤銷，結果僅供歷史參考。' : ''}
                  </p>
                )}
              </div>

              <div style={{ marginBottom: '16px' }}>
                <label htmlFor="inference-symbol" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                  市場代號
                </label>
                <input
                  id="inference-symbol"
                  data-testid="inference-symbol"
                  type="text"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  placeholder="DEMO-ALPHA"
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    fontSize: '14px',
                  }}
                />
                <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                  使用資料來源支援的代號；演示環境可用 DEMO-ALPHA。
                </p>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginBottom: '16px' }}>
                <div>
                  <label htmlFor="inference-start" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                    開始日期
                  </label>
                  <input
                    id="inference-start"
                    data-testid="inference-start"
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      border: '1px solid var(--border)',
                      borderRadius: '4px',
                      fontSize: '14px',
                    }}
                  />
                </div>
                <div>
                  <label htmlFor="inference-end" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                    結束日期
                  </label>
                  <input
                    id="inference-end"
                    data-testid="inference-end"
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      border: '1px solid var(--border)',
                      borderRadius: '4px',
                      fontSize: '14px',
                    }}
                  />
                </div>
              </div>

              <button type="submit" className="btn btn-primary" disabled={creating} data-testid="inference-submit">
                {creating ? '建立中…' : '建立任務'}
              </button>

              {formError && (
                <p role="alert" data-testid="inference-form-error" style={{ marginTop: '12px', color: '#721c24', fontSize: '14px' }}>
                  {formError}
                </p>
              )}
              {notice && (
                <p data-testid="inference-notice" style={{ marginTop: '12px', color: '#155724', fontSize: '14px' }}>
                  {notice}
                </p>
              )}
            </form>
          )}
        </div>
      )}

      <h2 style={{ fontSize: '18px', marginBottom: '12px' }}>過往推論</h2>

      {runsLoading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中…
        </div>
      ) : runsError ? (
        <div
          role="alert"
          data-testid="inference-runs-error"
          className="card"
          style={{ display: 'flex', gap: '12px', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap', padding: '32px', color: '#721c24' }}
        >
          <span>{runsError}</span>
          <button type="button" className="btn btn-secondary" onClick={loadRuns}>
            重試載入紀錄
          </button>
        </div>
      ) : runs.length === 0 ? (
        <div className="card" data-testid="inference-runs-empty" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666', margin: 0 }}>尚無推論紀錄。建立任務後，結果會出現在這裡。</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {runs.map((run) => {
            const colors = RUN_STATUS_COLORS[run.status] || { bg: '#eaeaea', text: '#666' };
            return (
              <div key={run.id} className="card" data-testid="inference-run">
                <button
                  type="button"
                  onClick={() => toggleExpand(run.id)}
                  aria-expanded={expandedRunId === run.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'start',
                    gap: '12px',
                    width: '100%',
                    textAlign: 'left',
                    background: 'transparent',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '14px', marginBottom: '6px' }}>
                      <strong data-testid="inference-run-version">
                        {versionLabelById.get(run.model_version_id) ?? `版本 ${run.model_version_id.slice(0, 8)}…`}
                      </strong>
                    </div>
                    <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                      {run.input_spec?.symbol ?? '—'}
                      {run.input_spec?.start_date && run.input_spec?.end_date
                        ? ` · ${run.input_spec.start_date} 至 ${run.input_spec.end_date}`
                        : ''}
                    </div>
                    <div style={{ display: 'flex', gap: '24px', fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px' }}>
                      <span>建立：{formatTime(run.created_at)}</span>
                      {run.completed_at && <span>完成：{formatTime(run.completed_at)}</span>}
                    </div>
                  </div>
                  <span
                    style={{
                      padding: '4px 12px',
                      borderRadius: '12px',
                      fontSize: '12px',
                      backgroundColor: colors.bg,
                      color: colors.text,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {RUN_STATUS_LABELS[run.status] ?? run.status}
                  </span>
                </button>

                {expandedRunId === run.id && (
                  <div style={{ marginTop: '16px', borderTop: '1px solid var(--border)', paddingTop: '16px' }}>
                    <h4 style={{ marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>輸入規格</h4>
                    <pre style={{
                      backgroundColor: 'var(--surface-2, #f8f9fa)',
                      padding: '12px',
                      borderRadius: '4px',
                      fontSize: '13px',
                      overflow: 'auto',
                      marginBottom: '16px',
                    }}>
                      {JSON.stringify(run.input_spec, null, 2)}
                    </pre>

                    {run.output_signals != null && (
                      <>
                        <h4 style={{ marginBottom: '8px', fontSize: '14px', color: 'var(--text-secondary)' }}>輸出訊號</h4>
                        <p style={{ marginBottom: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                          置信度欄位是規則式運算的固定顯示值（HOLD 0.5、BUY/SELL 0.7），
                          並非機率、準確率或可信度；本頁未提供任何經校準的預測值。
                        </p>
                        <pre style={{
                          backgroundColor: 'var(--surface-2, #f8f9fa)',
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
            );
          })}
        </div>
      )}
    </div>
  );
}
