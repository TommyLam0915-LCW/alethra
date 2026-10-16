'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface ResearchDraft {
  id: string;
  title: string;
  status: string;
  strategy_template: string | null;
  asset_symbol: string | null;
  created_at: string;
  updated_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  pending_review: '待審閱',
  accepted: '已接受',
  rejected: '已退回',
};

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft: { bg: '#eaeaea', color: '#666' },
  pending_review: { bg: '#fff3cd', color: '#856404' },
  accepted: { bg: '#d4edda', color: '#155724' },
  rejected: { bg: '#f8d7da', color: '#721c24' },
};

const TEMPLATE_OPTIONS = [
  { value: 'buy_and_hold', label: 'Buy & Hold（買入持有）' },
  { value: 'sma_crossover', label: 'SMA Crossover（均線交叉）' },
  { value: 'price_change_fixed_hold', label: 'Price Change Fixed Hold（價變固定持有）' },
];

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
      setError(err.message || '載入失敗');
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
      setError(err.message || '建立草稿失敗');
      setCreating(false);
    }
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href="/dashboard" style={{ color: '#0070f3', textDecoration: 'none' }}>
          返回我的實驗
        </Link>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h1>研究草稿</h1>
        <button
          className="btn btn-primary"
          onClick={() => setShowCreate(!showCreate)}
        >
          {showCreate ? '取消' : '建立草稿'}
        </button>
      </div>

      <div className="alert alert-warning" style={{ marginBottom: '24px' }}>
        <strong>模擬研究助手——僅供流程示範。</strong>{' '}
        本功能使用確定性模擬助手，不會呼叫外部 AI 服務。問題為預先定義，非真實 AI 分析。
        接受草稿僅記錄研究筆記，不會建立可執行策略、啟動回測、或建立模型版本。
      </div>

      {showCreate && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '8px' }}>建立新研究草稿</h3>
          <p style={{ color: '#888', fontSize: '13px', marginBottom: '16px' }}>
            先記錄標題與假設即可。其他欄位可在草稿詳情頁補充；接受草稿後，可將欄位套用到新實驗。
          </p>
          <form onSubmit={handleCreateDraft}>
            <div className="form-group">
              <label>草稿標題 *</label>
              <input
                type="text"
                className="input"
                value={createTitle}
                onChange={(e) => setCreateTitle(e.target.value)}
                placeholder="例如：測試 DEMO-ALPHA 的均線交叉策略"
                required
              />
            </div>
            <div className="form-group">
              <label>研究假設</label>
              <textarea
                className="input"
                value={createHypothesis}
                onChange={(e) => setCreateHypothesis(e.target.value)}
                placeholder="描述你想檢驗的投資假設"
                rows={3}
                style={{ resize: 'vertical' }}
              />
            </div>
            <div className="form-group">
              <label>策略模板</label>
              <select
                className="input"
                value={createTemplate}
                onChange={(e) => setCreateTemplate(e.target.value)}
              >
                <option value="">未選擇</option>
                {TEMPLATE_OPTIONS.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>研究標的</label>
              <input
                type="text"
                className="input"
                value={createAsset}
                onChange={(e) => setCreateAsset(e.target.value)}
                placeholder="例如 DEMO-ALPHA"
              />
              <p style={{ color: '#999', fontSize: '12px', marginBottom: 0 }}>
                僅限大寫字母、數字、連字號與點號。
              </p>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={!createTitle.trim() || creating}
              >
                {creating ? '建立中...' : '建立草稿'}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowCreate(false)}
              >
                取消
              </button>
            </div>
          </form>
        </div>
      )}

      {error && <div className="alert alert-warning">{error}</div>}

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中...
        </div>
      ) : drafts.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666', marginBottom: '20px' }}>尚無研究草稿</p>
          <p style={{ color: '#999', fontSize: '14px', marginBottom: '24px' }}>
            研究草稿用於在正式建立實驗前，結構化地整理你的研究假設與方法論。
          </p>
          <button
            className="btn btn-primary"
            onClick={() => setShowCreate(true)}
          >
            建立第一個草稿
          </button>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {drafts.map((draft) => {
            const statusStyle = STATUS_COLORS[draft.status] || STATUS_COLORS.draft;
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
                      <p style={{ color: '#666', fontSize: '14px' }}>
                        {draft.strategy_template && <span style={{ marginRight: '16px' }}>模板: {draft.strategy_template}</span>}
                        {draft.asset_symbol && <span style={{ marginRight: '16px' }}>標的: {draft.asset_symbol}</span>}
                        <span>建立於 {new Date(draft.created_at).toLocaleDateString('zh-Hant')}</span>
                      </p>
                    </div>
                    <span
                      style={{
                        padding: '4px 12px',
                        borderRadius: '12px',
                        fontSize: '12px',
                        backgroundColor: statusStyle.bg,
                        color: statusStyle.color,
                      }}
                    >
                      {STATUS_LABELS[draft.status] || draft.status}
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
