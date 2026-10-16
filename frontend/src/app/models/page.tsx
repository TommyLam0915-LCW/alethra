'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface ModelRecord {
  id: string;
  title: string;
  description: string | null;
  purpose: string;
  status: string;
  created_at: string;
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  researching: '研究中',
  historical_test_complete: '歷史測試完成',
  locked_for_simulation: '已鎖定待模擬',
  forward_observation: '重播觀察中',
  evidence_insufficient: '證據不足',
  retired: '已退役',
  revoked: '已撤銷',
};

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft: { bg: '#eaeaea', color: '#666' },
  researching: { bg: '#fff3cd', color: '#856404' },
  historical_test_complete: { bg: '#d4edda', color: '#155724' },
  locked_for_simulation: { bg: '#d1ecf1', color: '#0c5460' },
  forward_observation: { bg: '#cce5ff', color: '#004085' },
  evidence_insufficient: { bg: '#f8d7da', color: '#721c24' },
  retired: { bg: '#e2e3e5', color: '#383d41' },
  revoked: { bg: '#f5c6cb', color: '#721c24' },
};

export default function ModelResearchListPage() {
  const router = useRouter();
  const [records, setRecords] = useState<ModelRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newPurpose, setNewPurpose] = useState('');
  const [newDescription, setNewDescription] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    loadRecords();
  }, [router]);

  const loadRecords = async () => {
    try {
      const data = await api.modelResearch.list();
      setRecords(data);
    } catch (err) {
      console.error('Failed to load model records:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!newTitle.trim() || !newPurpose.trim()) return;
    try {
      await api.modelResearch.create({
        title: newTitle.trim(),
        purpose: newPurpose.trim(),
        description: newDescription.trim() || undefined,
      });
      setNewTitle('');
      setNewPurpose('');
      setNewDescription('');
      setShowCreate(false);
      loadRecords();
    } catch (err) {
      console.error('Failed to create record:', err);
    }
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <h1>模型研究記錄</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <button onClick={() => setShowCreate(!showCreate)} className="btn btn-primary">
            {showCreate ? '取消' : '建立模型'}
          </button>
          <Link href="/dashboard" className="btn btn-secondary">返回我的實驗</Link>
        </div>
      </div>

      <p style={{ color: '#666', marginBottom: '24px' }}>
        追蹤研究模型的生命週期：從草稿到鎖定、模擬、證據評估。
      </p>

      <div className="disclaimer" style={{ marginBottom: '24px' }}>
        <strong>提醒：</strong>模型研究記錄僅供研究追蹤使用。Alethra 不會執行任何真實交易，不提供買賣信號，不連接任何券商。
      </div>

      {showCreate && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '16px' }}>建立新模型研究記錄</h3>
          <div className="form-group">
            <label>標題</label>
            <input className="input" value={newTitle} onChange={e => setNewTitle(e.target.value)} placeholder="例：SMA 交叉策略模型" />
          </div>
          <div className="form-group">
            <label>研究目的</label>
            <input className="input" value={newPurpose} onChange={e => setNewPurpose(e.target.value)} placeholder="此模型的研究目的" />
          </div>
          <div className="form-group">
            <label>描述（選填）</label>
            <textarea className="input" rows={3} value={newDescription} onChange={e => setNewDescription(e.target.value)} placeholder="模型描述..." />
          </div>
          <button onClick={handleCreate} className="btn btn-primary">建立</button>
        </div>
      )}

      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>載入中...</div>
      ) : records.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666' }}>尚無模型研究記錄。點擊「建立模型」開始。</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {records.map((record) => {
            const sc = STATUS_COLORS[record.status] || STATUS_COLORS.draft;
            return (
              <Link key={record.id} href={`/models/${record.id}`} style={{ textDecoration: 'none', color: 'inherit' }}>
                <div className="card" style={{ cursor: 'pointer' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                    <div>
                      <h3 style={{ marginBottom: '4px' }}>{record.title}</h3>
                      <p style={{ color: '#666', fontSize: '14px', marginBottom: '4px' }}>{record.purpose}</p>
                      <p style={{ color: '#999', fontSize: '12px' }}>
                        建立於 {new Date(record.created_at).toLocaleDateString('zh-Hant')}
                      </p>
                    </div>
                    <span style={{
                      padding: '4px 12px', borderRadius: '12px', fontSize: '12px',
                      backgroundColor: sc.bg, color: sc.color, whiteSpace: 'nowrap',
                    }}>
                      {STATUS_LABELS[record.status] || record.status}
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      <div style={{ marginTop: '32px' }}>
        <Link href="/personal-memory" style={{ color: '#0066cc', fontSize: '14px' }}>
          個人研究記憶設定 →
        </Link>
      </div>
    </div>
  );
}
