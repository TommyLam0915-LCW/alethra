'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface MemoryRecord {
  id: string;
  memory_type: string;
  content: string;
  source_entity_type: string | null;
  source_entity_id: string | null;
  created_at: string;
  user_confirmed_at: string | null;
  is_confirmed: boolean;
  active: boolean;
  privacy_scope: string;
  general_model_training_opt_in: boolean;
}

const MEMORY_TYPE_LABELS: Record<string, string> = {
  explicit_preference: '明確偏好',
  approved_constraint: '已核准約束',
  recurring_methodology_issue: '反覆出現的方法論問題',
  reflection: '反思',
  accepted_ai_draft: '已接受 AI 草稿',
  rejected_ai_draft: '已拒絕 AI 草稿',
};

const MEMORY_TYPES = Object.keys(MEMORY_TYPE_LABELS);

const PRIVACY_LABELS: Record<string, string> = {
  private_only: '僅私人',
  approved_for_personal_ai: '允許個人 AI 使用',
};

export default function PersonalMemoryPage() {
  const router = useRouter();
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState('');
  const [showCreate, setShowCreate] = useState(false);

  const [newType, setNewType] = useState('explicit_preference');
  const [newContent, setNewContent] = useState('');
  const [newPrivacy, setNewPrivacy] = useState('private_only');
  const [newConfirmed, setNewConfirmed] = useState(false);

  const [trainingOptInId, setTrainingOptInId] = useState<string | null>(null);
  const [trainingOptInValue, setTrainingOptInValue] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    loadMemories();
  }, [router]);

  const loadMemories = async () => {
    try {
      const data = await api.personalMemory.list(typeFilter || undefined);
      setMemories(data);
    } catch (err) {
      console.error('Failed to load memories:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!loading) loadMemories();
  }, [typeFilter]);

  const handleCreate = async () => {
    if (!newContent.trim()) return;
    try {
      await api.personalMemory.create({
        memory_type: newType,
        content: newContent.trim(),
        privacy_scope: newPrivacy,
        user_confirmed: newConfirmed,
      });
      setNewContent('');
      setShowCreate(false);
      loadMemories();
    } catch (err) {
      console.error('Failed to create memory:', err);
    }
  };

  const handleConfirm = async (id: string, confirmed: boolean) => {
    try {
      await api.personalMemory.confirm(id, confirmed);
      loadMemories();
    } catch (err) {
      console.error('Failed to confirm memory:', err);
    }
  };

  const handleDeactivate = async (id: string) => {
    try {
      await api.personalMemory.deactivate(id);
      loadMemories();
    } catch (err) {
      console.error('Failed to deactivate memory:', err);
    }
  };

  const handleSetTrainingOptIn = async (id: string) => {
    try {
      await api.personalMemory.setTrainingOptIn(id, trainingOptInValue);
      setTrainingOptInId(null);
      loadMemories();
    } catch (err) {
      console.error('Failed to set training opt-in:', err);
    }
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <h1>個人研究記憶</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <button onClick={() => setShowCreate(!showCreate)} className="btn btn-primary">
            {showCreate ? '取消' : '新增記憶'}
          </button>
          <Link href="/models" className="btn btn-secondary">返回模型列表</Link>
        </div>
      </div>

      <p style={{ color: '#666', marginBottom: '16px' }}>
        管理你的研究偏好、約束與反思。所有記憶僅在你明確確認後才會生效。
      </p>

      <div className="disclaimer" style={{ marginBottom: '24px' }}>
        <strong>隱私原則：</strong>
        系統不會在未經你明確確認的情況下，將任何偏好套用於研究流程。
        所有記憶預設為「僅私人」，你可選擇開放給個人 AI 工具使用。
      </div>

      {showCreate && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '16px' }}>新增記憶</h3>
          <div className="form-group">
            <label>記憶類型</label>
            <select className="input" value={newType} onChange={e => setNewType(e.target.value)}>
              {MEMORY_TYPES.map(t => (
                <option key={t} value={t}>{MEMORY_TYPE_LABELS[t]}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>內容</label>
            <textarea
              className="input"
              rows={3}
              value={newContent}
              onChange={e => setNewContent(e.target.value)}
              placeholder="描述你的偏好、約束或反思..."
            />
          </div>
          <div className="form-group">
            <label>隱私範圍</label>
            <select className="input" value={newPrivacy} onChange={e => setNewPrivacy(e.target.value)}>
              <option value="private_only">{PRIVACY_LABELS.private_only}</option>
              <option value="approved_for_personal_ai">{PRIVACY_LABELS.approved_for_personal_ai}</option>
            </select>
          </div>
          <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <input
              type="checkbox"
              id="create-confirmed"
              checked={newConfirmed}
              onChange={e => setNewConfirmed(e.target.checked)}
            />
            <label htmlFor="create-confirmed" style={{ margin: 0 }}>
              我確認此記憶內容正確，立即生效
            </label>
          </div>
          <button onClick={handleCreate} className="btn btn-primary">儲存</button>
        </div>
      )}

      <div style={{ marginBottom: '20px' }}>
        <label style={{ marginRight: '8px', fontSize: '14px', color: '#666' }}>篩選類型：</label>
        <select
          className="input"
          style={{ width: 'auto', display: 'inline-block' }}
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value)}
        >
          <option value="">全部</option>
          {MEMORY_TYPES.map(t => (
            <option key={t} value={t}>{MEMORY_TYPE_LABELS[t]}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>載入中...</div>
      ) : memories.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666' }}>尚無記憶記錄。點擊「新增記憶」開始。</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {memories.map((mem) => (
            <div key={mem.id} className="card" style={{ opacity: mem.active ? 1 : 0.6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '8px' }}>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{
                    padding: '2px 10px', borderRadius: '10px', fontSize: '12px',
                    backgroundColor: '#eaeaea', color: '#555',
                  }}>
                    {MEMORY_TYPE_LABELS[mem.memory_type] || mem.memory_type}
                  </span>
                  <span style={{
                    padding: '2px 10px', borderRadius: '10px', fontSize: '12px',
                    backgroundColor: mem.privacy_scope === 'approved_for_personal_ai' ? '#d1ecf1' : '#eaeaea',
                    color: mem.privacy_scope === 'approved_for_personal_ai' ? '#0c5460' : '#666',
                  }}>
                    {PRIVACY_LABELS[mem.privacy_scope] || mem.privacy_scope}
                  </span>
                  {mem.is_confirmed ? (
                    <span style={{ padding: '2px 10px', borderRadius: '10px', fontSize: '12px', backgroundColor: '#d4edda', color: '#155724' }}>
                      已確認
                    </span>
                  ) : (
                    <span style={{ padding: '2px 10px', borderRadius: '10px', fontSize: '12px', backgroundColor: '#fff3cd', color: '#856404' }}>
                      待確認
                    </span>
                  )}
                  {!mem.active && (
                    <span style={{ padding: '2px 10px', borderRadius: '10px', fontSize: '12px', backgroundColor: '#f5c6cb', color: '#721c24' }}>
                      已停用
                    </span>
                  )}
                </div>
                <span style={{ fontSize: '12px', color: '#999' }}>
                  {new Date(mem.created_at).toLocaleDateString('zh-Hant')}
                </span>
              </div>

              <p style={{ marginBottom: '12px', whiteSpace: 'pre-wrap' }}>{mem.content}</p>

              {mem.general_model_training_opt_in && (
                <p style={{ fontSize: '13px', color: '#0c5460', marginBottom: '12px' }}>
                  ✓ 已啟用一般模型訓練
                </p>
              )}

              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {!mem.is_confirmed && mem.active && (
                  <>
                    <button onClick={() => handleConfirm(mem.id, true)} className="btn btn-primary" style={{ fontSize: '13px', padding: '4px 12px' }}>
                      確認
                    </button>
                    <button onClick={() => handleConfirm(mem.id, false)} className="btn btn-secondary" style={{ fontSize: '13px', padding: '4px 12px' }}>
                      拒絕
                    </button>
                  </>
                )}
                {mem.is_confirmed && mem.active && (
                  <>
                    {trainingOptInId === mem.id ? (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <select
                          className="input"
                          style={{ width: 'auto', fontSize: '13px', padding: '4px 8px' }}
                          value={String(trainingOptInValue)}
                          onChange={e => setTrainingOptInValue(e.target.value === 'true')}
                        >
                          <option value="true">啟用</option>
                          <option value="false">停用</option>
                        </select>
                        <button onClick={() => handleSetTrainingOptIn(mem.id)} className="btn btn-primary" style={{ fontSize: '13px', padding: '4px 12px' }}>
                          儲存
                        </button>
                        <button onClick={() => setTrainingOptInId(null)} className="btn btn-secondary" style={{ fontSize: '13px', padding: '4px 12px' }}>
                          取消
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => { setTrainingOptInId(mem.id); setTrainingOptInValue(mem.general_model_training_opt_in); }}
                        className="btn btn-secondary"
                        style={{ fontSize: '13px', padding: '4px 12px' }}
                      >
                        模型訓練設定
                      </button>
                    )}
                  </>
                )}
                {mem.active && (
                  <button onClick={() => handleDeactivate(mem.id)} className="btn btn-secondary" style={{ fontSize: '13px', padding: '4px 12px', color: '#721c24' }}>
                    停用
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
