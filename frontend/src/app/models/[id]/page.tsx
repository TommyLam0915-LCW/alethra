'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface ModelRecord {
  id: string;
  title: string;
  description: string | null;
  purpose: string;
  status: string;
  created_at: string;
  updated_at: string;
}

interface ModelVersion {
  id: string;
  version_number: number;
  model_type: string;
  model_specification_json: any;
  created_at: string;
  locked_at: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  model_card_id: string | null;
}

interface ModelEvaluation {
  id: string;
  model_version_id: string;
  evaluation_type: string;
  result_summary: string | null;
  evaluation_date: string;
  data_cutoff_at: string;
  engine_version: string;
  evaluator: string;
  created_at: string;
}

interface ModelCard {
  id: string;
  purpose: string;
  intended_research_use: string;
  excluded_use: string;
  data_sources: string[];
  data_cutoff: string;
  known_limitations: string[];
  risk_flags: string[] | null;
  disclaimer: string;
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿', researching: '研究中', historical_test_complete: '歷史測試完成',
  locked_for_simulation: '已鎖定待模擬', forward_observation: '重播觀察中',
  evidence_insufficient: '證據不足', retired: '已退役', revoked: '已撤銷',
};

const MODEL_TYPE_LABELS: Record<string, string> = {
  rule_based: '規則型', prompt_based_research_assistant: '提示詞研究型', future_external_model_reference: '未來外部模型',
};

export default function ModelDetailPage() {
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;

  const [record, setRecord] = useState<ModelRecord | null>(null);
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [evaluations, setEvaluations] = useState<ModelEvaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'versions' | 'evaluations' | 'card' | 'export'>('versions');
  const [showNewVersion, setShowNewVersion] = useState(false);
  const [newVersionType, setNewVersionType] = useState('rule_based');
  const [newVersionSpec, setNewVersionSpec] = useState('{}');
  const [showStatusChange, setShowStatusChange] = useState(false);
  const [newStatus, setNewStatus] = useState('');
  const [revokeReason, setRevokeReason] = useState('');
  const [revokingVersionId, setRevokingVersionId] = useState<string | null>(null);
  const [exportVersionId, setExportVersionId] = useState('');
  const [includeNotes, setIncludeNotes] = useState(false);
  const [exportResult, setExportResult] = useState<any>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { router.push('/login'); return; }
    loadData();
  }, [id, router]);

  const loadData = async () => {
    try {
      const [rec, vers, evals] = await Promise.all([
        api.modelResearch.get(id),
        api.modelResearch.versions(id),
        api.modelResearch.evaluations(id),
      ]);
      setRecord(rec);
      setVersions(vers);
      setEvaluations(evals);
    } catch (err) {
      console.error('Failed to load model:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateVersion = async () => {
    try {
      const spec = JSON.parse(newVersionSpec);
      await api.modelResearch.createVersion(id, {
        model_type: newVersionType,
        model_specification_json: spec,
      });
      setNewVersionSpec('{}');
      setShowNewVersion(false);
      loadData();
    } catch (err) {
      console.error('Failed to create version:', err);
    }
  };

  const handleStatusChange = async () => {
    if (!newStatus) return;
    try {
      const updated = await api.modelResearch.update(id, { status: newStatus });
      setRecord(updated);
      setShowStatusChange(false);
      setNewStatus('');
    } catch (err: any) {
      alert(err.message);
    }
  };

  const handleLock = async (versionId: string) => {
    try {
      await api.modelResearch.lockVersion(id, versionId);
      loadData();
    } catch (err: any) {
      alert(err.message);
    }
  };

  const handleRevoke = async (versionId: string) => {
    if (!revokeReason.trim()) return;
    try {
      await api.modelResearch.revokeVersion(id, versionId, revokeReason);
      setRevokingVersionId(null);
      setRevokeReason('');
      loadData();
    } catch (err: any) {
      alert(err.message);
    }
  };

  const handleExport = async () => {
    if (!exportVersionId) return;
    setExporting(true);
    setExportError('');
    setExportResult(null);
    try {
      const result = await api.modelResearch.createExport(id, {
        model_version_id: exportVersionId,
        include_private_notes: includeNotes,
      });
      setExportResult(result);
    } catch (err: any) {
      setExportError(err.message);
    } finally {
      setExporting(false);
    }
  };

  const handleDownload = async () => {
    if (!exportResult) return;
    try {
      const pkg = await api.modelResearch.downloadExport(exportResult.id);
      const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `alethra-model-package-${exportResult.id.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      alert(err.message);
    }
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px', textAlign: 'center' }}>載入中...</div>;
  }
  if (!record) {
    return <div className="container" style={{ paddingTop: '40px' }}>模型記錄不存在。</div>;
  }

  const canAddVersions = record.status === 'draft' || record.status === 'researching';

  const tabStyle = (tab: string) => ({
    padding: '8px 20px', cursor: 'pointer', border: 'none', borderBottom: activeTab === tab ? '2px solid #0066cc' : '2px solid transparent',
    backgroundColor: 'transparent', color: activeTab === tab ? '#0066cc' : '#666', fontWeight: activeTab === tab ? 600 : 400,
  });

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <Link href="/models" style={{ color: '#0066cc', fontSize: '14px', marginBottom: '16px', display: 'inline-block' }}>
        ← 返回模型列表
      </Link>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ marginBottom: '4px' }}>{record.title}</h1>
          <p style={{ color: '#666' }}>{record.purpose}</p>
          {record.description && <p style={{ color: '#888', fontSize: '14px', marginTop: '8px' }}>{record.description}</p>}
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{
            padding: '4px 12px', borderRadius: '12px', fontSize: '12px',
            backgroundColor: '#d1ecf1', color: '#0c5460',
          }}>
            {STATUS_LABELS[record.status] || record.status}
          </span>
          {canAddVersions && (
            <button onClick={() => setShowStatusChange(!showStatusChange)} className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }}>
              變更狀態
            </button>
          )}
        </div>
      </div>

      {showStatusChange && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <div className="form-group">
            <label>新狀態</label>
            <select className="input" value={newStatus} onChange={e => setNewStatus(e.target.value)}>
              <option value="">選擇狀態...</option>
              {record.status === 'draft' && <option value="researching">研究中</option>}
              {record.status === 'researching' && <><option value="historical_test_complete">歷史測試完成</option><option value="evidence_insufficient">證據不足</option><option value="retired">退役</option></>}
              {record.status === 'historical_test_complete' && <><option value="locked_for_simulation">鎖定待模擬</option><option value="evidence_insufficient">證據不足</option><option value="retired">退役</option></>}
              {record.status === 'locked_for_simulation' && <><option value="forward_observation">重播觀察</option><option value="evidence_insufficient">證據不足</option><option value="retired">退役</option></>}
              {record.status === 'forward_observation' && <><option value="evidence_insufficient">證據不足</option><option value="retired">退役</option></>}
              {record.status === 'evidence_insufficient' && <option value="retired">退役</option>}
            </select>
          </div>
          <button onClick={handleStatusChange} className="btn btn-primary" disabled={!newStatus}>確認變更</button>
        </div>
      )}

      <div style={{ display: 'flex', borderBottom: '1px solid #e0e0e0', marginBottom: '24px' }}>
        <button style={tabStyle('versions')} onClick={() => setActiveTab('versions')}>
          版本 ({versions.length})
        </button>
        <button style={tabStyle('evaluations')} onClick={() => setActiveTab('evaluations')}>
          評估 ({evaluations.length})
        </button>
        <button style={tabStyle('card')} onClick={() => setActiveTab('card')}>
          模型卡
        </button>
        <button style={tabStyle('export')} onClick={() => setActiveTab('export')}>
          匯出
        </button>
      </div>

      {activeTab === 'versions' && (
        <div>
          {canAddVersions && (
            <div style={{ marginBottom: '16px' }}>
              <button onClick={() => setShowNewVersion(!showNewVersion)} className="btn btn-primary">
                {showNewVersion ? '取消' : '建立新版本'}
              </button>
            </div>
          )}

          {showNewVersion && (
            <div className="card" style={{ marginBottom: '20px' }}>
              <div className="form-group">
                <label>模型類型</label>
                <select className="input" value={newVersionType} onChange={e => setNewVersionType(e.target.value)}>
                  <option value="rule_based">規則型</option>
                  <option value="prompt_based_research_assistant">提示詞研究型</option>
                  <option value="future_external_model_reference">未來外部模型</option>
                </select>
              </div>
              <div className="form-group">
                <label>模型規格 (JSON)</label>
                <textarea className="input" rows={6} value={newVersionSpec} onChange={e => setNewVersionSpec(e.target.value)} />
              </div>
              <button onClick={handleCreateVersion} className="btn btn-primary">建立</button>
            </div>
          )}

          {versions.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '40px', color: '#666' }}>尚無版本。</div>
          ) : (
            <div style={{ display: 'grid', gap: '12px' }}>
              {versions.map((v) => (
                <div key={v.id} className="card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                    <div>
                      <h4 style={{ marginBottom: '4px' }}>版本 {v.version_number}</h4>
                      <p style={{ fontSize: '13px', color: '#666' }}>
                        {MODEL_TYPE_LABELS[v.model_type] || v.model_type} · 建立於 {new Date(v.created_at).toLocaleDateString('zh-Hant')}
                      </p>
                      {v.locked_at && <p style={{ fontSize: '12px', color: '#0c5460' }}>已鎖定: {new Date(v.locked_at).toLocaleString('zh-Hant')}</p>}
                      {v.revoked_at && <p style={{ fontSize: '12px', color: '#721c24' }}>已撤銷: {v.revocation_reason}</p>}
                    </div>
                    <div style={{ display: 'flex', gap: '6px' }}>
                      {!v.locked_at && !v.revoked_at && canAddVersions && (
                        <button onClick={() => handleLock(v.id)} className="btn btn-secondary" style={{ fontSize: '11px', padding: '2px 8px' }}>鎖定</button>
                      )}
                      {!v.revoked_at && (
                        <button onClick={() => setRevokingVersionId(revokingVersionId === v.id ? null : v.id)} className="btn btn-secondary" style={{ fontSize: '11px', padding: '2px 8px' }}>撤銷</button>
                      )}
                    </div>
                  </div>
                  {revokingVersionId === v.id && (
                    <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #eee' }}>
                      <input className="input" placeholder="撤銷原因..." value={revokeReason} onChange={e => setRevokeReason(e.target.value)} style={{ marginBottom: '8px' }} />
                      <button onClick={() => handleRevoke(v.id)} className="btn btn-primary" style={{ fontSize: '12px' }}>確認撤銷</button>
                    </div>
                  )}
                  <details style={{ marginTop: '8px' }}>
                    <summary style={{ cursor: 'pointer', fontSize: '12px', color: '#888' }}>查看規格</summary>
                    <pre style={{ fontSize: '11px', backgroundColor: '#f5f5f5', padding: '8px', borderRadius: '4px', overflow: 'auto', marginTop: '4px' }}>
                      {JSON.stringify(v.model_specification_json, null, 2)}
                    </pre>
                  </details>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'evaluations' && (
        <div>
          {evaluations.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '40px', color: '#666' }}>尚無評估記錄。</div>
          ) : (
            <div style={{ display: 'grid', gap: '12px' }}>
              {evaluations.map((ev) => (
                <div key={ev.id} className="card">
                  <h4 style={{ marginBottom: '4px' }}>{ev.evaluation_type}</h4>
                  <p style={{ fontSize: '13px', color: '#666' }}>
                    評估者: {ev.evaluator} · 引擎: {ev.engine_version} · 數據截止: {ev.data_cutoff_at}
                  </p>
                  {ev.result_summary && <p style={{ fontSize: '13px', marginTop: '8px' }}>{ev.result_summary}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'card' && (
        <div>
          {versions.find(v => v.model_card_id) ? (
            <div className="card">
              <p style={{ color: '#666', fontSize: '14px' }}>
                模型卡已建立。模型卡記錄了模型的目的、限制、已知風險及撤銷流程。
              </p>
            </div>
          ) : (
            <div className="card" style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
              尚未建立模型卡。模型卡在版本鎖定後建立，記錄模型的用途、限制與風險。
            </div>
          )}
        </div>
      )}

      {activeTab === 'export' && (
        <div>
          <div className="disclaimer" style={{ marginBottom: '20px' }}>
            <strong>研究匯出聲明</strong><br />
            匯出套件為研究產物，並非執行套件、交易信號、投資建議或部署批准。所有結果僅供研究參考，不代表未來表現。
          </div>

          <div className="card" style={{ marginBottom: '20px' }}>
            <h3 style={{ marginBottom: '16px' }}>建立 Alethra 模型研究套件</h3>

            <div className="form-group">
              <label>選擇版本</label>
              <select className="input" value={exportVersionId} onChange={e => setExportVersionId(e.target.value)}>
                <option value="">選擇版本...</option>
                {versions.map(v => (
                  <option key={v.id} value={v.id}>
                    版本 {v.version_number} — {MODEL_TYPE_LABELS[v.model_type] || v.model_type}
                    {v.revoked_at ? ' (已撤銷)' : ''}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <input
                type="checkbox"
                id="includeNotes"
                checked={includeNotes}
                onChange={e => setIncludeNotes(e.target.checked)}
              />
              <label htmlFor="includeNotes" style={{ marginBottom: 0 }}>包含私人研究筆記</label>
            </div>

            <button
              onClick={handleExport}
              className="btn btn-primary"
              disabled={!exportVersionId || exporting}
            >
              {exporting ? '匯出中...' : '匯出研究套件'}
            </button>

            {exportError && (
              <div className="alert alert-warning" style={{ marginTop: '12px' }}>{exportError}</div>
            )}
          </div>

          {exportResult && exportResult.status === 'completed' && (
            <div className="card">
              <h4 style={{ marginBottom: '12px' }}>匯出完成</h4>
              <p style={{ fontSize: '13px', color: '#666', marginBottom: '8px' }}>
                匯出 ID: {exportResult.id}<br />
                校驗碼: <code style={{ fontSize: '11px' }}>{exportResult.package_checksum?.slice(0, 16)}...</code><br />
                完成時間: {exportResult.completed_at ? new Date(exportResult.completed_at).toLocaleString('zh-Hant') : '-'}
              </p>
              <button onClick={handleDownload} className="btn btn-primary" style={{ marginTop: '8px' }}>
                下載研究套件
              </button>
            </div>
          )}

          {exportResult && exportResult.status === 'failed' && (
            <div className="alert alert-warning">
              匯出失敗: {exportResult.error_message || '未知錯誤'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
