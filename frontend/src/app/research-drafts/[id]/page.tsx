'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { statusPillClass } from '@/lib/status-styles';
import { formatDateTime } from '@/lib/format';
import { DRAFT_STATUS_LABELS, STRATEGY_TEMPLATE_LABELS, label } from '@/lib/copy';

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
  title: 'Title',
  hypothesis: 'Research hypothesis',
  falsification_criteria: 'Falsification criteria',
  strategy_template: 'Strategy template',
  asset_symbol: 'Asset',
  benchmark_symbol: 'Benchmark',
  initial_cash: 'Initial cash',
  commission_rate: 'Commission rate',
  slippage_rate: 'Slippage rate',
  research_rules: 'Research rules',
};

const EVENT_LABELS: Record<string, string> = {
  research_draft_created: 'Draft created',
  research_draft_updated: 'Draft updated',
  research_draft_questions_generated: 'Questions generated',
  research_draft_answers_submitted: 'Answers submitted',
  research_draft_accepted: 'Draft accepted',
  research_draft_rejected: 'Draft returned',
};

const EDITABLE_FIELDS = [
  'title', 'hypothesis', 'falsification_criteria', 'strategy_template',
  'asset_symbol', 'benchmark_symbol', 'initial_cash', 'commission_rate',
  'slippage_rate',
] as const;

const TEMPLATE_OPTIONS = Object.entries(STRATEGY_TEMPLATE_LABELS).map(([value, text]) => ({
  value,
  label: text,
}));

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
      setError(err.message || 'The draft could not be loaded');
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
      setError(err.message || 'The revision history could not be loaded');
    }
  };

  const loadAuditEvents = async () => {
    try {
      const data = await api.researchDrafts.auditEvents(draftId);
      setAuditEvents(data);
    } catch (err: any) {
      setError(err.message || 'The audit events could not be loaded');
    }
  };

  const handleGenerateQuestions = async () => {
    setError('');
    setSuccess('');
    try {
      await api.researchDrafts.generateQuestions(draftId);
      setSuccess('Questions generated');
      setShowQuestions(true);
      await loadDraft();
    } catch (err: any) {
      setError(err.message || 'The questions could not be generated');
    }
  };

  const handleSubmitAnswers = async () => {
    setError('');
    setSuccess('');
    const filtered = Object.fromEntries(
      Object.entries(answers).filter(([, v]) => v.trim() !== '')
    );
    if (Object.keys(filtered).length === 0) {
      setError('Answer at least one question');
      return;
    }
    try {
      const updated = await api.researchDrafts.submitAnswers(draftId, filtered);
      setDraft(updated);
      setAnswers({});
      setSuccess('Answers submitted and the draft updated');
      setShowQuestions(false);
    } catch (err: any) {
      setError(err.message || 'The answers could not be submitted');
    }
  };

  const handleSaveEdit = async () => {
    setError('');
    setSuccess('');
    const filtered = Object.fromEntries(
      Object.entries(editForm).filter(([, v]) => v.trim() !== '')
    );
    if (Object.keys(filtered).length === 0) {
      setError('Change at least one field');
      return;
    }
    try {
      const updated = await api.researchDrafts.update(draftId, filtered);
      setDraft(updated);
      setEditing(false);
      setSuccess('Draft updated');
    } catch (err: any) {
      setError(err.message || 'The draft could not be updated');
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
      setSuccess('Draft accepted — this is a research note, not a verified strategy');
    } catch (err: any) {
      setError(err.message || 'The draft could not be accepted');
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
      setSuccess('Draft returned');
    } catch (err: any) {
      setError(err.message || 'The draft could not be returned');
    }
  };

  const handleDelete = async () => {
    setError('');
    try {
      await api.researchDrafts.delete(draftId);
      router.push('/research-drafts');
    } catch (err: any) {
      setError(err.message || 'The draft could not be deleted');
    }
  };

  const handleApplySelectedFields = () => {
    if (!draft) return;
    if (draft.status !== 'accepted') {
      setError('Only accepted drafts can transfer fields');
      return;
    }
    const params = new URLSearchParams();
    params.set('draft_id', draft.id);
    params.set('fields', Array.from(selectedFields).join(','));
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
    return (
      <div className="container" role="status" aria-live="polite" style={{ paddingTop: '40px' }}>
        Loading…
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="container" style={{ paddingTop: '40px' }}>
        <p>This draft does not exist.</p>
        <Link href="/research-drafts" style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          ← Back to research drafts
        </Link>
      </div>
    );
  }

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href="/research-drafts" style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          &larr; Back to research drafts
        </Link>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '20px' }}>
        <div>
          <h1 style={{ marginBottom: '8px' }}>{draft.title || '(Untitled draft)'}</h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
            Created {formatDateTime(draft.created_at)}
            {' | '}
            Updated {formatDateTime(draft.updated_at)}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span className={statusPillClass(draft.status)} style={{ padding: '6px 16px', fontSize: '14px' }}>
            {label(DRAFT_STATUS_LABELS, draft.status)}
          </span>
        </div>
      </div>

      <div className="alert alert-info" style={{ marginBottom: '20px', fontSize: '13px' }}>
        <strong>Simulated research assistant — workflow demo only.</strong>{' '}
        This feature uses a deterministic simulated assistant (not real AI) and the questions are predefined.
        Accepting a draft only records research notes; it does not create an executable strategy, start a backtest, lock a strategy, create paper orders, or create a model version.
      </div>

      {error && <div className="alert alert-warning" role="alert">{error}</div>}
      {success && <div className="alert alert-info" role="status">{success}</div>}

      {/* Tab navigation */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
        {(['detail', 'revisions', 'audit'] as const).map((tab) => (
          <button
            key={tab}
            className={view === tab ? 'btn btn-primary' : 'btn btn-secondary'}
            aria-pressed={view === tab}
            style={{ fontSize: '13px' }}
            onClick={() => {
              setView(tab);
              setError('');
              setSuccess('');
              if (tab === 'revisions') loadRevisions();
              if (tab === 'audit') loadAuditEvents();
            }}
          >
            {tab === 'detail' ? 'Draft content' : tab === 'revisions' ? 'Revision history' : 'Audit events'}
          </button>
        ))}
      </div>

      {/* Detail view */}
      {view === 'detail' && (
        <>
          {/* Draft fields */}
          <div className="card" style={{ marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <h2>Draft fields</h2>
              {!isFinalized && !editing && (
                <button className="btn btn-secondary" style={{ fontSize: '13px' }} onClick={startEdit}>
                  Edit
                </button>
              )}
            </div>

            {editing ? (
              <div>
                {EDITABLE_FIELDS.map((field) => (
                  <div className="form-group" key={field}>
                    <label htmlFor={`draft-field-${field}`}>{FIELD_LABELS[field]}</label>
                    {field === 'strategy_template' ? (
                      <select
                        id={`draft-field-${field}`}
                        className="input"
                        value={editForm[field] || ''}
                        onChange={(e) => setEditForm({ ...editForm, [field]: e.target.value })}
                      >
                        <option value="">(Not selected)</option>
                        {TEMPLATE_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    ) : field === 'hypothesis' || field === 'falsification_criteria' ? (
                      <textarea
                        id={`draft-field-${field}`}
                        className="input"
                        rows={3}
                        value={editForm[field] || ''}
                        onChange={(e) => setEditForm({ ...editForm, [field]: e.target.value })}
                      />
                    ) : (
                      <input
                        id={`draft-field-${field}`}
                        className="input"
                        value={editForm[field] || ''}
                        onChange={(e) => setEditForm({ ...editForm, [field]: e.target.value })}
                        placeholder={field === 'asset_symbol' ? 'e.g. DEMO-ALPHA' : field === 'benchmark_symbol' ? 'e.g. DEMO-BENCHMARK' : ''}
                      />
                    )}
                  </div>
                ))}
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button className="btn btn-primary" onClick={handleSaveEdit}>Save</button>
                  <button className="btn btn-secondary" onClick={() => setEditing(false)}>Cancel</button>
                </div>
              </div>
            ) : (
              <div style={{ display: 'grid', gap: '12px' }}>
                {EDITABLE_FIELDS.map((field) => {
                  const value = (draft as any)[field];
                  return (
                    <div key={field} style={{ display: 'flex', gap: '12px', padding: '8px 0', borderBottom: '1px solid var(--surface-2)' }}>
                      <div style={{ width: '140px', fontWeight: '500', fontSize: '14px', color: 'var(--text-muted)', flexShrink: 0 }}>
                        {FIELD_LABELS[field]}
                      </div>
                      <div style={{ fontSize: '14px', color: value ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                        {field === 'strategy_template' && value
                          ? label(STRATEGY_TEMPLATE_LABELS, value)
                          : value || '(Not filled in)'}
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
                <h2>Assistant questions</h2>
                <button className="btn btn-primary" style={{ fontSize: '13px' }} onClick={handleGenerateQuestions}>
                  {draft.assistant_questions ? 'Regenerate questions' : 'Generate questions'}
                </button>
              </div>

              <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px', padding: '8px 12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                The simulated assistant asks about fields you have not filled in yet. Questions are deterministic and predefined, not generated dynamically by AI.
              </div>

              {showQuestions && draft.assistant_questions && draft.assistant_questions.length > 0 ? (
                <div>
                  {draft.assistant_questions.map((q, idx) => (
                    <div key={idx} style={{ marginBottom: '16px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                      <div style={{ fontWeight: '500', fontSize: '14px', marginBottom: '4px' }}>
                        {FIELD_LABELS[q.field] || q.field}
                      </div>
                      <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '8px' }}>
                        {q.question}
                      </div>
                      {q.context && (
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '8px' }}>
                          Hint: {q.context}
                        </div>
                      )}
                      {q.field === 'strategy_template' ? (
                        <select
                          className="input"
                          aria-label={FIELD_LABELS[q.field] || q.field}
                          value={answers[q.field] || ''}
                          onChange={(e) => setAnswers({ ...answers, [q.field]: e.target.value })}
                        >
                          <option value="">(Select a template)</option>
                          {TEMPLATE_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className="input"
                          aria-label={FIELD_LABELS[q.field] || q.field}
                          placeholder="Type your answer…"
                          value={answers[q.field] || ''}
                          onChange={(e) => setAnswers({ ...answers, [q.field]: e.target.value })}
                        />
                      )}
                    </div>
                  ))}
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn btn-primary" onClick={handleSubmitAnswers}>Submit answers</button>
                    <button className="btn btn-secondary" onClick={() => setShowQuestions(false)}>Cancel</button>
                  </div>
                </div>
              ) : draft.assistant_questions && draft.assistant_questions.length > 0 ? (
                <div>
                  <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                    {draft.assistant_questions.length} question{draft.assistant_questions.length === 1 ? '' : 's'} generated.
                  </p>
                  <button className="btn btn-secondary" style={{ fontSize: '13px' }} onClick={() => setShowQuestions(true)}>
                    Answer questions
                  </button>
                </div>
              ) : (
                <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>
                  No questions yet. Select &ldquo;Generate questions&rdquo; to have the simulated assistant check the fields you have not filled in.
                </p>
              )}
            </div>
          )}

          {/* Accepted draft: field application */}
          {draft.status === 'accepted' && (
            <div className="card" style={{ marginBottom: '20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <h2>Apply draft fields</h2>
                <button
                  className="btn btn-primary"
                  style={{ fontSize: '13px' }}
                  onClick={() => {
                    setShowApplyFields(!showApplyFields);
                    setSelectedFields(new Set());
                  }}
                >
                  {showApplyFields ? 'Cancel' : 'Choose fields to apply'}
                </button>
              </div>

              <div className="alert alert-warning" style={{ fontSize: '13px', marginBottom: '16px' }}>
                Accepting a draft only means the research notes have been reviewed. The field values below are not applied to any experiment automatically —
                you choose which fields to copy, and they are recorded only after you confirm in the next step.
              </div>

              {showApplyFields && (
                <div>
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                    Tick the fields you want to apply. You will see the draft values before anything is written.
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
                          backgroundColor: selectedFields.has(field) ? 'rgba(59,130,246,0.16)' : 'var(--surface-2)',
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
                          <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>
                            Draft value: <strong>
                              {field === 'strategy_template'
                                ? label(STRATEGY_TEMPLATE_LABELS, String(proposedValue))
                                : String(proposedValue)}
                            </strong>
                          </div>
                        </div>
                      </label>
                    );
                  })}
                  {selectedFields.size > 0 && (
                    <div style={{ marginTop: '12px' }}>
                      <button className="btn btn-primary" onClick={handleApplySelectedFields}>
                        Apply {selectedFields.size} field{selectedFields.size === 1 ? '' : 's'}
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
              <h2 style={{ marginBottom: '16px' }}>Review decision</h2>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="btn" style={{ backgroundColor: 'var(--positive)', color: 'var(--text-on-bright)', fontSize: '13px' }} onClick={() => { setShowApproveDialog(true); setError(''); }}>
                  Accept draft
                </button>
                <button className="btn" style={{ backgroundColor: 'var(--negative)', color: 'var(--text-on-bright)', fontSize: '13px' }} onClick={() => { setShowRejectDialog(true); setError(''); }}>
                  Return draft
                </button>
              </div>

              {showApproveDialog && (
                <div style={{ marginTop: '16px', padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                  <p style={{ fontSize: '14px', marginBottom: '12px' }}>
                    Accepting this draft records that you reviewed the research hypothesis and method.
                    <strong> It does not create an executable strategy.</strong>
                  </p>
                  <textarea
                    className="input"
                    aria-label="Acceptance note"
                    rows={2}
                    placeholder="Note (optional)…"
                    value={approveNotes}
                    onChange={(e) => setApproveNotes(e.target.value)}
                    style={{ marginBottom: '12px' }}
                  />
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn" style={{ backgroundColor: 'var(--positive)', color: 'var(--text-on-bright)' }} onClick={handleApprove}>Confirm accept</button>
                    <button className="btn btn-secondary" onClick={() => { setShowApproveDialog(false); setApproveNotes(''); }}>Cancel</button>
                  </div>
                </div>
              )}

              {showRejectDialog && (
                <div style={{ marginTop: '16px', padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}>
                  <p style={{ fontSize: '14px', marginBottom: '12px' }}>Return this draft and record why.</p>
                  <textarea
                    className="input"
                    aria-label="Return reason"
                    rows={2}
                    placeholder="Reason for returning (optional)…"
                    value={approveNotes}
                    onChange={(e) => setApproveNotes(e.target.value)}
                    style={{ marginBottom: '12px' }}
                  />
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="btn" style={{ backgroundColor: 'var(--negative)', color: 'var(--text-on-bright)' }} onClick={handleReject}>Confirm return</button>
                    <button className="btn btn-secondary" onClick={() => { setShowRejectDialog(false); setApproveNotes(''); }}>Cancel</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Delete */}
          <div className="card" style={{ marginBottom: '20px' }}>
            <h2 style={{ marginBottom: '12px' }}>Delete draft</h2>
            <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
              Deleting a draft also removes all of its revisions and audit events. This cannot be undone.
            </p>
            {showDeleteConfirm ? (
              <div style={{ padding: '12px', backgroundColor: 'rgba(239,68,68,0.12)', borderRadius: '6px' }}>
                <p style={{ fontSize: '14px', color: 'var(--negative)', marginBottom: '12px' }}>
                  Delete this draft? All revisions and audit events will be deleted with it.
                </p>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button className="btn" style={{ backgroundColor: 'var(--negative)', color: 'var(--text-on-bright)' }} onClick={handleDelete}>Confirm delete</button>
                  <button className="btn btn-secondary" onClick={() => setShowDeleteConfirm(false)}>Cancel</button>
                </div>
              </div>
            ) : (
              <button className="btn" style={{ backgroundColor: 'var(--surface-2)', color: 'var(--negative)', fontSize: '13px' }} onClick={() => setShowDeleteConfirm(true)}>
                Delete this draft
              </button>
            )}
          </div>
        </>
      )}

      {/* Revisions view */}
      {view === 'revisions' && (
        <div className="card">
          <h2 style={{ marginBottom: '16px' }}>Revision history ({revisions.length})</h2>
          {revisions.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No revisions yet</p>
          ) : (
            <div style={{ display: 'grid', gap: '8px' }}>
              {[...revisions].reverse().map((rev) => (
                <div key={rev.id} style={{ padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', borderLeft: '3px solid var(--primary)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <span style={{ fontWeight: '500', fontSize: '14px' }}>Revision #{rev.revision_number}</span>
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {formatDateTime(rev.created_at)}
                    </span>
                  </div>
                  <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                    <span style={{ marginRight: '12px' }}>Status: {label(DRAFT_STATUS_LABELS, rev.status)}</span>
                    {rev.title && <span>Title: {rev.title}</span>}
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
          <h2 style={{ marginBottom: '16px' }}>Audit events ({auditEvents.length})</h2>
          {auditEvents.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '14px' }}>No audit events yet</p>
          ) : (
            <div style={{ display: 'grid', gap: '4px' }}>
              {[...auditEvents].reverse().map((evt) => (
                <div key={evt.id} style={{
                  padding: '8px 12px',
                  backgroundColor: 'var(--surface-2)',
                  borderRadius: '4px',
                  borderLeft: '3px solid var(--primary)',
                  fontSize: '13px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontWeight: '500' }}>
                      {EVENT_LABELS[evt.event_type] || evt.event_type}
                    </span>
                    <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                      {formatDateTime(evt.created_at)}
                    </span>
                  </div>
                  {Object.keys(evt.event_data).length > 0 && (
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
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
