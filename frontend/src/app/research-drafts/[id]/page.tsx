'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface ResearchDraft {
  id: string;
  title: string;
  hypothesis: string | null;
  falsification_criteria: string | null;
  strategy_template: string | null;
  asset_symbol: string | null;
  benchmark_symbol: string | null;
  initial_cash: string | null;
  commission_rate: string | null;
  slippage_rate: string | null;
  research_rules: Record<string, any> | null;
  status: string;
  experiment_id: string | null;
  assistant_questions: Array<{ field: string; question: string; context?: string }> | null;
  assistant_responses: Record<string, string> | null;
  created_at: string;
  updated_at: string;
}

interface Revision {
  id: string;
  revision_number: string;
  title: string | null;
  hypothesis: string | null;
  falsification_criteria: string | null;
  strategy_template: string | null;
  asset_symbol: string | null;
  benchmark_symbol: string | null;
  initial_cash: string | null;
  commission_rate: string | null;
  slippage_rate: string | null;
  research_rules: Record<string, any> | null;
  status: string;
  created_at: string;
}

interface AuditEvent {
  id: string;
  event_type: string;
  event_data: Record<string, any>;
  created_at: string;
}

const FIELD_LABELS: Record<string, string> = {
  title: '標題',
  hypothesis: '研究假設',
  falsification_criteria: '推翻條件',
  strategy_template: '策略模板',
  asset_symbol: '研究標的',
  benchmark_symbol: '基準標的',
  initial_cash: '初始資金',
  commission_rate: '手續費率',
  slippage_rate: '滑價率',
  research_rules: '研究規則',
};

const EVENT_LABELS: Record<string, string> = {
  research_draft_created: '草稿建立',
  research_draft_updated: '草稿更新',
  research_draft_questions_generated: '問題產生',
  research_draft_answers_submitted: '答案提交',
  research_draft_accepted: '草稿接受',
  research_draft_rejected: '草稿退回',
};

const EDITABLE_FIELDS = [
  'title', 'hypothesis', 'falsification_criteria', 'strategy_template',
  'asset_symbol', 'benchmark_symbol', 'initial_cash', 'commission_rate',
  'slippage_rate',
] as const;

const TEMPLATE_OPTIONS = [
  { value: 'buy_and_hold', label: 'Buy & Hold（買入持有）' },
  { value: 'sma_crossover', label: 'SMA Crossover（均線交叉）' },
  { value: 'price_change_fixed_hold', label: 'Price Change Fixed Hold（價變固定持有）' },
];

export default function ResearchDraftDetailPage() {
  const router = useRouter();
  const params = useParams();
  const draftId = params.id as string;

  const [draft, setDraft] = useState<ResearchDraft | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const [view, setView] = useState<'detail' | 'revisions' | 'audit'>('detail');
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<Record<string, string>>({});
  const [showQuestions, setShowQuestions] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [approveNotes, setApproveNotes] = useState('');
  const [showApproveDialog, setShowApproveDialog] = useState(false);
  const [showRejectDialog, setShowRejectDialog] = useState(false);
  const [showApplyFields, setShowApplyFields] = useState(false);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(new Set());
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const loadDraft = useCallback(async () => {
    try {
      const data = await api.researchDrafts.get(draftId);
      setDraft(data);
    } catch (err: any) {
      setError(err.message || '載入失敗');
    } finally {
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadDraft();
  }, [loadDraft, router]);

  const loadRevisions = async () => {
    try {
      const data = await api.researchDrafts.revisions(draftId);
      setRevisions(data);
    } catch (err: any) {
      setError(err.message || '載入修訂記錄失敗');
    }
  };

  const loadAuditEvents = async () => {
    try {
      const data = await api.researchDrafts.auditEvents(draftId);
      setAuditEvents(data);
    } catch (err: any) {
      setError(err.message || '載入稽核事件失敗');
    }
  };

  const handleGenerateQuestions = async () => {
    setError('');
    setSuccess('');
    try {
      await api.researchDrafts.generateQuestions(draftId);
      setSuccess('問題已產生');
      setShowQuestions(true);
      await loadDraft();
    } catch (err: any) {
      setError(err.message || '產生問題失敗');
    }
  };

  const handleSubmitAnswers = async () => {
    setError('');
    setSuccess('');
    const filtered = Object.fromEntries(
      Object.entries(answers).filter(([, v]) => v.trim() !== '')
    );
    if (Object.keys(filtered).length === 0) {
      setError('請至少回答一個問題');
      return;
    }
    try {
      const updated = await api.researchDrafts.submitAnswers(draftId, filtered);
      setDraft(updated);
      setAnswers({});
      setSuccess('答案已提交，草稿已更新');
      setShowQuestions(false);
    } catch (err: any) {
      setError(err.message || '提交答案失敗');
    }
  };

  const handleSaveEdit = async () => {
    setError('');
    setSuccess('');
    const filtered = Object.fromEntries(
      Object.entries(editForm).filter(([, v]) => v.trim() !== '')
    );
    if (Object.keys(filtered).length === 0) {
      setError('請至少修改一個欄位');
      return;
    }
    try {
      const updated = await api.researchDrafts.update(draftId, filtered);
      setDraft(updated);
      setEditing(false);
      setSuccess('草稿已更新');
    } catch (err: any) {
      setError(err.message || '更新失敗');
    }
  };

  const handleApprove = async () => {
    setError('');
    setSuccess('');
    try {
      const updated = await api.researchDrafts.approve(draftId, 'accept', approveNotes || undefined);
      setDraft(updated);
      setShowApproveDialog(false);
      setApproveNotes('');
      setSuccess('草稿已接受——此為研究筆記記錄，非已驗證策略');
    } catch (err: any) {
      setError(err.message || '接受失敗');
    }
  };

  const handleReject = async () => {
    setError('');
    setSuccess('');
    try {
      const updated = await api.researchDrafts.approve(draftId, 'reject', approveNotes || undefined);
      setDraft(updated);
      setShowRejectDialog(false);
      setShowApproveDialog(false);
      setApproveNotes('');
      setSuccess('草稿已退回');
    } catch (err: any) {
      setError(err.message || '退回失敗');
    }
  };

  const handleDelete = async () => {
    setError('');
    try {
      await api.researchDrafts.delete(draftId);
      router.push('/research-drafts');
    } catch (err: any) {
      setError(err.message || '刪除失敗');
    }
  };

  const handleApplySelectedFields = () => {
    if (!draft) return;
    const params = new URLSearchParams();
    const FIELD_TO_FORM: Record<string, string> = {
      title: 'title',
      hypothesis: 'hypothesis',
      strategy_template: 'templateType',
      asset_symbol: 'assetId',
      benchmark_symbol: 'benchmarkId',
      initial_cash: 'initialCash',
      commission_rate: 'commissionRate',
      slippage_rate: 'slippageRate',
    };
    for (const field of Array.from(selectedFields)) {
      const formField = FIELD_TO_FORM[field];
      const value = (draft as any)[field];
      if (formField && value) {
        params.set(formField, String(value));
      }
    }
    router.push(`/experiments/new?${params.toString()}`);
  };

  const startEdit = () => {
    if (!draft) return;
    const form: Record<string, string> = {};
    for (const field of EDITABLE_FIELDS) {
      form[field] = (draft as any)[field] || '';
    }
    setEditForm(form);
    setEditing(true);
    setError('');
    setSuccess('');
  };

  const isFinalized = draft?.status === 'accepted' || draft?.status === 'rejected';

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px' }}>載入中...</div>;
  }

  if (!draft) {
    return <div className="container" style={{ paddingTop: '40px' }}>草稿不存在</div>;
  }

  const statusLabel: Record<string, string> = {
    draft: '草稿',
    pending_review: '待審閱',
    accepted: '已接受',
    rejected: '已退回',
  };

  const statusColor: Record<string, { bg: string; color: string }> = {
    draft: { bg: '#eaeaea', color: '#666' },
    pending_review: { bg: '#fff3cd', color: '#856404' },
    accepted: { bg: '#d4edda', color: '#155724' },
    rejected: { bg: '#f8d7da', color: '#721c24' },
  };

  const sStyle = statusColor[draft.status] || statusColor.draft;

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href="/research-drafts" style={{ color: '#0070f3', textDecoration: 'none' }}>
          &larr; 返回研究草稿
        </Link>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '20px' }}>
        <div>
          <h1 style={{ marginBottom: '8px' }}>{draft.title || '（未命名草稿）'}</h1>
          <p style={{ color: '#666', fontSize: '14px' }}>
            建立於 {new Date(draft.created_at).toLocaleString('zh-Hant')}
            {' | '}
            更新於 {new Date(draft.updated_at).toLocaleString('zh-Hant')}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ padding: '6px 16px', borderRadius: '16px', fontSize: '14px', backgroundColor: sStyle.bg, color: sStyle.color }}>
            {statusLabel[draft.status] || draft.status}
          </span>
        </div>
      </div>

      <div className="alert alert-info" style={{ marginBottom: '20px', fontSize: '13px' }}>
        <strong>模擬研究助手——僅供流程示範。</strong>{' '}
        本功能使用確定性模擬助手（非真實 AI），問題為預先定義。
        接受草稿僅記錄研究筆記，不會建立可執行策略、啟動回測、鎖定策略、建立紙上訂單、或建立模型版本。
      </div>

      {error && <div className="alert alert-warning">{error}</div>}
      {success && <div className="alert alert-info">{success}</div>}

      {/* Tab navigation */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
        {(['detail', 'revisions', 'audit'] as const).map((tab) => (
          <button
            key={tab}
            className="btn"
            style={{
              backgroundColor: view === tab ? '#0070f3' : '#eaeaea',
              color: view === tab ? 'white' : '#333',
              fontSize: '13px',
            }}
            onClick={() => {
              setView(tab);
              setError('');
              setSuccess('');
              if (tab === 'revisions') loadRevisions();
              if (tab === 'audit') loadAuditEvents();
            }}
          >
            {tab === 'detail' ? '草稿內容' : tab === 'revisions' ? '修訂記錄' : '稽核事件'}
          </button>
        ))}
      </div>

      {/* Detail view */}
      {view === 'detail' && (
        <>
          {/* Draft fields */}
          <div className="card" style={{ marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <h2>草稿欄位</h2>
              {!isFinalized && !editing && (
                <button className="btn btn-secondary" style={{ fontSize: '13px' }} onClick={startEdit}>
                  編輯
                </button>
              )}
            </div>

            {editing ? (
              <div>
                {EDITABLE_FIELDS.map((field) => (
                  <div className="form-group" key={field}>
                    <label>{FIELD_LABELS[field]}</label>
                    {field === 'strategy_template' ? (
                      <select
                        className="input"
                        value={editForm[field] || ''}
                        onChange={(e) => setEditForm({ ...editForm, [field]: e.target.value })}
                      >
                        <option value="">（未選擇）</option>
                        {TEMPLATE_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    ) : field === 'hypothesis' || field === 'falsification_criteria' ? (
                      <textarea
                        className="input"
                        rows={3}
                        value={editForm[field] || ''}
                        onChange={(e) => setEditForm({ ...editForm, [field]: e.target.value })}
                      />
                    ) : (
                      <input
                        className="input"
                        value={editForm[field] || ''}
                        onChange={(e) => setEditForm({ ...editForm, [field]: e.target.value })}
                        placeholder={field === 'asset_symbol' ? '例如 DEMO-ALPHA' : field === 'benchmark_symbol' ? '例如 DEMO-BENCHMARK' : ''}
                      />
                    )}
                  </div>
                ))}
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button className="btn btn-primary" onClick={handleSaveEdit}>儲存</button>
                  <button className="btn btn-secondary" onClick={() => setEditing(false)}>取消</button>
                </div>
              </div>
            ) : (
              <div style={{ display: 'grid', gap: '12px' }}>
                {EDITABLE_FIELDS.map((field) => {
                  const value = (draft as any)[field];
                  return (
                    <div key={field} style={{ display: 'flex', gap: '12px', padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                      <div style={{ width: '140px', fontWeight: '500', fontSize: '14px', color: '#555', flexShrink: 0 }}>
                        {FIELD_LABELS[field]}
                      </div>
                      <div style={{ fontSize: '14px', color: value ? '#333' : '#ccc' }}>
                        {field === 'strategy_template' && value
                          ? TEMPLATE_OPTIONS.find((o) => o.value === value)?.label || value
                          : value || '（未填寫）'}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Questions and answers */}
          {!isFinalized && (
            <div className="card" style={{ marginBottom: '20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <h2>研究助手問題</h2>
                <button className="btn btn-primary" style={{ fontSize: '13px' }} onClick={handleGenerateQuestions}>
                  {draft.assistant_questions ? '重新產生問題' : '產生問題'}
                </button>
              </div>

              <div style={{ fontSize: '13px', color: '#666', marginBottom: '12px', padding: '8px 12px', backgroundColor: '#f8f9fa', borderRadius: '6px' }}>
                模擬助手會針對尚未填寫的欄位產生問題。問題為確定性預先定義，非 AI 動態產生。
              </div>

              {showQuestions && draft.assistant_questions && draft.assistant_questions.length > 0 ? (
                <div>
                  {draft.assistant_questions.map((q, idx) => (
                    <div key={idx} style={{ marginBottom: '16px', padding: '12px', backgroundColor: '#f9f9f9', borderRadius: '6px' }}>
                      <div style={{ fontWeight: '500', fontSize: '14px', marginBottom: '4px' }}>
                        {FIELD_LABELS[q.field] || q.field}
                      </div>
                      <div style={{ fontSize: '13px', color: '#555', marginBottom: '8px' }}>
                        {q.question}
                      </div>
                      {q.context && (
                        <div style={{ fontSize: '12px', color: '#888', marginBottom: '8px' }}>
                          提示: {q.context}
                        </div>
                      )}
                      {q.field === 'strategy_template' ? (
                        <select
                          className="input"
                          value={answers[q.field] || ''}
                          onChange={(e) => setAnswers({ ...answers, [q.field]: e.target.value })}
                        >
                          <option value="">（選擇模板）</option>
                          {TEMPLATE_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className="input"
                          placeholder="輸入你的回答..."
                          value={answers[q.field] || ''}
                          onChange={(e) => setAnswers({ ...answers, [q.field]: e.target.value })}
                        />
                      )}
                    </div>
                  ))}
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn btn-primary" onClick={handleSubmitAnswers}>提交答案</button>
                    <button className="btn btn-secondary" onClick={() => setShowQuestions(false)}>取消</button>
                  </div>
                </div>
              ) : draft.assistant_questions && draft.assistant_questions.length > 0 ? (
                <div>
                  <p style={{ fontSize: '14px', color: '#666', marginBottom: '12px' }}>
                    已產生 {draft.assistant_questions.length} 個問題。
                  </p>
                  <button className="btn btn-secondary" style={{ fontSize: '13px' }} onClick={() => setShowQuestions(true)}>
                    回答問題
                  </button>
                </div>
              ) : (
                <p style={{ color: '#999', fontSize: '14px' }}>
                  尚未產生問題。點擊「產生問題」讓模擬助手檢查尚未填寫的欄位。
                </p>
              )}
            </div>
          )}

          {/* Accepted draft: field application */}
          {draft.status === 'accepted' && (
            <div className="card" style={{ marginBottom: '20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <h2>套用草稿欄位</h2>
                <button
                  className="btn btn-primary"
                  style={{ fontSize: '13px' }}
                  onClick={() => {
                    setShowApplyFields(!showApplyFields);
                    setSelectedFields(new Set());
                  }}
                >
                  {showApplyFields ? '取消' : '選擇要套用的欄位'}
                </button>
              </div>

              <div className="alert alert-warning" style={{ fontSize: '13px', marginBottom: '16px' }}>
                接受草稿僅表示研究筆記已審閱。以下欄位值不會自動套用到任何實驗——
                你需要手動選擇要複製的欄位，並在下一步確認後才會記錄。
              </div>

              {showApplyFields && (
                <div>
                  <p style={{ fontSize: '13px', color: '#666', marginBottom: '12px' }}>
                    勾選你要套用的欄位。你會看到目前值與草稿建議值的比較。
                  </p>
                  {EDITABLE_FIELDS.map((field) => {
                    const proposedValue = (draft as any)[field];
                    if (!proposedValue) return null;
                    return (
                      <label
                        key={field}
                        style={{
                          display: 'flex',
                          gap: '12px',
                          alignItems: 'start',
                          padding: '10px 12px',
                          marginBottom: '8px',
                          backgroundColor: selectedFields.has(field) ? '#e8f4fd' : '#f9f9f9',
                          borderRadius: '6px',
                          cursor: 'pointer',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={selectedFields.has(field)}
                          onChange={(e) => {
                            const next = new Set(selectedFields);
                            if (e.target.checked) next.add(field);
                            else next.delete(field);
                            setSelectedFields(next);
                          }}
                          style={{ marginTop: '2px' }}
                        />
                        <div>
                          <div style={{ fontWeight: '500', fontSize: '14px' }}>{FIELD_LABELS[field]}</div>
                          <div style={{ fontSize: '13px', color: '#555', marginTop: '4px' }}>
                            草稿值: <strong>{proposedValue}</strong>
                          </div>
                        </div>
                      </label>
                    );
                  })}
                  {selectedFields.size > 0 && (
                    <div style={{ marginTop: '12px' }}>
                      <button className="btn btn-primary" onClick={handleApplySelectedFields}>
                        確認套用 {selectedFields.size} 個欄位
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Approve / Reject */}
          {!isFinalized && (
            <div className="card" style={{ marginBottom: '20px' }}>
              <h2 style={{ marginBottom: '16px' }}>審閱決定</h2>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="btn" style={{ backgroundColor: '#28a745', color: 'white', fontSize: '13px' }} onClick={() => { setShowApproveDialog(true); setError(''); }}>
                  接受草稿
                </button>
                <button className="btn" style={{ backgroundColor: '#dc3545', color: 'white', fontSize: '13px' }} onClick={() => { setShowRejectDialog(true); setError(''); }}>
                  退回草稿
                </button>
              </div>

              {showApproveDialog && (
                <div style={{ marginTop: '16px', padding: '16px', backgroundColor: '#f8f9fa', borderRadius: '6px' }}>
                  <p style={{ fontSize: '14px', marginBottom: '12px' }}>
                    接受此草稿表示你已審閱研究假設與方法論。
                    <strong>這不會建立可執行的策略。</strong>
                  </p>
                  <textarea
                    className="input"
                    rows={2}
                    placeholder="備註（選填）..."
                    value={approveNotes}
                    onChange={(e) => setApproveNotes(e.target.value)}
                    style={{ marginBottom: '12px' }}
                  />
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn" style={{ backgroundColor: '#28a745', color: 'white' }} onClick={handleApprove}>確認接受</button>
                    <button className="btn btn-secondary" onClick={() => { setShowApproveDialog(false); setApproveNotes(''); }}>取消</button>
                  </div>
                </div>
              )}

              {showRejectDialog && (
                <div style={{ marginTop: '16px', padding: '16px', backgroundColor: '#f8f9fa', borderRadius: '6px' }}>
                  <p style={{ fontSize: '14px', marginBottom: '12px' }}>退回此草稿，記錄退回原因。</p>
                  <textarea
                    className="input"
                    rows={2}
                    placeholder="退回原因（選填）..."
                    value={approveNotes}
                    onChange={(e) => setApproveNotes(e.target.value)}
                    style={{ marginBottom: '12px' }}
                  />
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn" style={{ backgroundColor: '#dc3545', color: 'white' }} onClick={handleReject}>確認退回</button>
                    <button className="btn btn-secondary" onClick={() => { setShowRejectDialog(false); setApproveNotes(''); }}>取消</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Delete */}
          <div className="card" style={{ marginBottom: '20px' }}>
            <h2 style={{ marginBottom: '12px' }}>刪除草稿</h2>
            <p style={{ fontSize: '14px', color: '#666', marginBottom: '12px' }}>
              刪除草稿會一併移除所有修訂記錄與稽核事件。此操作無法復原。
            </p>
            {showDeleteConfirm ? (
              <div style={{ padding: '12px', backgroundColor: '#f8d7da', borderRadius: '6px' }}>
                <p style={{ fontSize: '14px', color: '#721c24', marginBottom: '12px' }}>
                  確定要刪除此草稿嗎？所有修訂與稽核事件將一併刪除。
                </p>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button className="btn" style={{ backgroundColor: '#dc3545', color: 'white' }} onClick={handleDelete}>確認刪除</button>
                  <button className="btn btn-secondary" onClick={() => setShowDeleteConfirm(false)}>取消</button>
                </div>
              </div>
            ) : (
              <button className="btn" style={{ backgroundColor: '#eaeaea', color: '#dc3545', fontSize: '13px' }} onClick={() => setShowDeleteConfirm(true)}>
                刪除此草稿
              </button>
            )}
          </div>
        </>
      )}

      {/* Revisions view */}
      {view === 'revisions' && (
        <div className="card">
          <h2 style={{ marginBottom: '16px' }}>修訂記錄 ({revisions.length})</h2>
          {revisions.length === 0 ? (
            <p style={{ color: '#999', fontSize: '14px' }}>尚無修訂記錄</p>
          ) : (
            <div style={{ display: 'grid', gap: '8px' }}>
              {[...revisions].reverse().map((rev) => (
                <div key={rev.id} style={{ padding: '12px', backgroundColor: '#f9f9f9', borderRadius: '6px', borderLeft: '3px solid #0070f3' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <span style={{ fontWeight: '500', fontSize: '14px' }}>修訂 #{rev.revision_number}</span>
                    <span style={{ fontSize: '12px', color: '#999' }}>
                      {new Date(rev.created_at).toLocaleString('zh-Hant')}
                    </span>
                  </div>
                  <div style={{ fontSize: '13px', color: '#666' }}>
                    <span style={{ marginRight: '12px' }}>狀態: {statusLabel[rev.status] || rev.status}</span>
                    {rev.title && <span>標題: {rev.title}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Audit events view */}
      {view === 'audit' && (
        <div className="card">
          <h2 style={{ marginBottom: '16px' }}>稽核事件 ({auditEvents.length})</h2>
          {auditEvents.length === 0 ? (
            <p style={{ color: '#999', fontSize: '14px' }}>尚無稽核事件</p>
          ) : (
            <div style={{ display: 'grid', gap: '4px' }}>
              {[...auditEvents].reverse().map((evt) => (
                <div key={evt.id} style={{
                  padding: '8px 12px',
                  backgroundColor: '#f8f9fa',
                  borderRadius: '4px',
                  borderLeft: '3px solid #0070f3',
                  fontSize: '13px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontWeight: '500' }}>
                      {EVENT_LABELS[evt.event_type] || evt.event_type}
                    </span>
                    <span style={{ color: '#999', fontSize: '11px' }}>
                      {new Date(evt.created_at).toLocaleString('zh-Hant')}
                    </span>
                  </div>
                  {Object.keys(evt.event_data).length > 0 && (
                    <div style={{ fontSize: '12px', color: '#888', marginTop: '4px' }}>
                      {JSON.stringify(evt.event_data)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
