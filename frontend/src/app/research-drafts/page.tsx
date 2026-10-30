'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { statusPillClass } from '@/lib/status-styles';
import { formatDate } from '@/lib/format';
import { DRAFT_STATUS_LABELS, STRATEGY_TEMPLATE_LABELS, label } from '@/lib/copy';

interface ResearchDraft {
  id: string;
  title: string;
  status: string;
  strategy_template: string | null;
  asset_symbol: string | null;
  created_at: string;
  updated_at: string;
}

const TEMPLATE_OPTIONS = Object.entries(STRATEGY_TEMPLATE_LABELS).map(([value, text]) => ({
  value,
  label: text,
}));

export default function ResearchDraftsPage() {
  const router = useRouter();
  const [drafts, setDrafts] = useState<ResearchDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [showCreate, setShowCreate] = useState(false);
  const [createTitle, setCreateTitle] = useState('');
  const [createHypothesis, setCreateHypothesis] = useState('');
  const [createTemplate, setCreateTemplate] = useState('');
  const [createAsset, setCreateAsset] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadDrafts();
  }, [router]);

  const loadDrafts = async () => {
    try {
      const data = await api.researchDrafts.list();
      setDrafts(data);
    } catch (err: any) {
      setError(err.message || 'The research drafts could not be loaded');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateDraft = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createTitle.trim() || creating) return;
    setCreating(true);
    setError('');
    try {
      const payload: Record<string, string> = { title: createTitle.trim() };
      if (createHypothesis.trim()) payload.hypothesis = createHypothesis.trim();
      if (createTemplate) payload.strategy_template = createTemplate;
      if (createAsset.trim()) payload.asset_symbol = createAsset.trim().toUpperCase();
      const draft = await api.researchDrafts.create(payload);
      router.push(`/research-drafts/${draft.id}`);
    } catch (err: any) {
      setError(err.message || 'The draft could not be created');
      setCreating(false);
    }
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href="/dashboard" style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          Back to my experiments
        </Link>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h1>Research drafts</h1>
        <button
          className="btn btn-primary"
          onClick={() => setShowCreate(!showCreate)}
        >
          {showCreate ? 'Cancel' : 'New draft'}
        </button>
      </div>

      <div className="alert alert-warning" style={{ marginBottom: '24px' }}>
        <strong>Simulated research assistant — workflow demo only.</strong>{' '}
        This feature uses a deterministic simulated assistant and does not call an external AI service. The questions are predefined, not real AI analysis.
        Accepting a draft only records research notes; it does not create an executable strategy, start a backtest, or create a model version.
      </div>

      {showCreate && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '8px' }}>Create a new research draft</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: '13px', marginBottom: '16px' }}>
            Start with a title and hypothesis. You can fill in the other fields on the draft page; once a draft is accepted, its fields can be applied to a new experiment.
          </p>
          <form onSubmit={handleCreateDraft}>
            <div className="form-group">
              <label htmlFor="draft-title">Draft title *</label>
              <input
                id="draft-title"
                type="text"
                className="input"
                value={createTitle}
                onChange={(e) => setCreateTitle(e.target.value)}
                placeholder="e.g. Test a moving-average crossover on DEMO-ALPHA"
                required
              />
            </div>
            <div className="form-group">
              <label htmlFor="draft-hypothesis">Research hypothesis</label>
              <textarea
                id="draft-hypothesis"
                className="input"
                value={createHypothesis}
                onChange={(e) => setCreateHypothesis(e.target.value)}
                placeholder="Describe the investment hypothesis you want to test"
                rows={3}
                style={{ resize: 'vertical' }}
              />
            </div>
            <div className="form-group">
              <label htmlFor="draft-template">Strategy template</label>
              <select
                id="draft-template"
                className="input"
                value={createTemplate}
                onChange={(e) => setCreateTemplate(e.target.value)}
              >
                <option value="">Not selected</option>
                {TEMPLATE_OPTIONS.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label htmlFor="draft-asset">Asset</label>
              <input
                id="draft-asset"
                type="text"
                className="input"
                value={createAsset}
                onChange={(e) => setCreateAsset(e.target.value)}
                placeholder="e.g. DEMO-ALPHA"
              />
              <p style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: 0 }}>
                Uppercase letters, digits, hyphens and dots only.
              </p>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={!createTitle.trim() || creating}
              >
                {creating ? 'Creating…' : 'Create draft'}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowCreate(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {error && <div className="alert alert-warning" role="alert">{error}</div>}

      {loading ? (
        <div className="card" role="status" aria-live="polite" style={{ textAlign: 'center', padding: '40px' }}>
          Loading…
        </div>
      ) : drafts.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>No research drafts yet</p>
          <p style={{ color: 'var(--text-muted)', fontSize: '14px', marginBottom: '24px' }}>
            Research drafts structure your hypothesis and method before you create an experiment.
          </p>
          <button
            className="btn btn-primary"
            onClick={() => setShowCreate(true)}
          >
            Create your first draft
          </button>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {drafts.map((draft) => {
            return (
              <Link
                key={draft.id}
                href={`/research-drafts/${draft.id}`}
                style={{ textDecoration: 'none', color: 'inherit' }}
              >
                <div className="card" style={{ cursor: 'pointer', transition: 'transform 0.2s' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                    <div>
                      <h3 style={{ marginBottom: '8px' }}>{draft.title}</h3>
                      <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
                        {draft.strategy_template && <span style={{ marginRight: '16px' }}>Template: {label(STRATEGY_TEMPLATE_LABELS, draft.strategy_template)}</span>}
                        {draft.asset_symbol && <span style={{ marginRight: '16px' }}>Asset: {draft.asset_symbol}</span>}
                        <span>Created {formatDate(draft.created_at)}</span>
                      </p>
                    </div>
                    <span className={statusPillClass(draft.status)}>
                      {label(DRAFT_STATUS_LABELS, draft.status)}
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
