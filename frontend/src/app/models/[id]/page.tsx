'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { classifyWriteError } from '@/lib/userErrors';
import { formatDate, formatDateTime } from '@/lib/format';

interface ModelRecord {
  id: string;
  title: string;
  description: string | null;
  purpose: string;
  status: string;
  active_version_id: string | null;
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

interface ExperimentSummary {
  id: string;
  title: string;
}

interface StrategyVersion {
  id: string;
  version_number: number;
  change_reason: string | null;
  created_at: string;
}

interface CompareSide {
  id: string;
  version_number: number;
  model_type: string;
  created_at: string;
  locked_at: string | null;
  revoked_at: string | null;
  is_active: boolean;
}

interface SpecChange {
  path: string;
  kind: 'added' | 'removed' | 'changed';
  a: any;
  b: any;
}

interface CompareResult {
  record_id: string;
  version_a: CompareSide;
  version_b: CompareSide;
  spec_changes: SpecChange[];
  spec_changes_truncated: boolean;
  evaluations: {
    a: { id: string; evaluation_type: string; result_summary: string | null }[];
    b: { id: string; evaluation_type: string; result_summary: string | null }[];
  };
  note: string;
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
  const [activeTab, setActiveTab] = useState<'versions' | 'compare' | 'evaluations' | 'card' | 'export'>('versions');
  const [showNewVersion, setShowNewVersion] = useState(false);
  const [newVersionType, setNewVersionType] = useState('rule_based');
  const [newVersionSpec, setNewVersionSpec] = useState('{}');
  const [showFromStrategy, setShowFromStrategy] = useState(false);
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [strategyExperimentId, setStrategyExperimentId] = useState('');
  const [strategyVersions, setStrategyVersions] = useState<StrategyVersion[]>([]);
  const [strategyVersionId, setStrategyVersionId] = useState('');
  const [strategyNote, setStrategyNote] = useState('');
  const [fromStrategyError, setFromStrategyError] = useState('');
  const [creatingFromStrategy, setCreatingFromStrategy] = useState(false);
  const [versionActionError, setVersionActionError] = useState('');
  const [rollbackVersionId, setRollbackVersionId] = useState<string | null>(null);
  const [rollbackReason, setRollbackReason] = useState('');
  const [compareA, setCompareA] = useState('');
  const [compareB, setCompareB] = useState('');
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null);
  const [compareError, setCompareError] = useState('');
  const [compareLoading, setCompareLoading] = useState(false);
  const [showStatusChange, setShowStatusChange] = useState(false);
  const [newStatus, setNewStatus] = useState('');
  const [statusError, setStatusError] = useState('');
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
      setCompareA(prev => prev || vers[0]?.id || '');
      setCompareB(prev => prev || (vers.length > 1 ? vers[vers.length - 1].id : ''));
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

  const toggleFromStrategy = async () => {
    const next = !showFromStrategy;
    setShowFromStrategy(next);
    setFromStrategyError('');
    if (!next || experiments.length > 0) return;
    try {
      setExperiments(await api.experiments.list());
    } catch (err: any) {
      setFromStrategyError(err.message);
    }
  };

  const handleStrategyExperimentPick = async (experimentId: string) => {
    setStrategyExperimentId(experimentId);
    setStrategyVersionId('');
    setStrategyVersions([]);
    if (!experimentId) return;
    try {
      setStrategyVersions(await api.experiments.versions(experimentId));
    } catch (err: any) {
      setFromStrategyError(err.message);
    }
  };

  const handleCreateFromStrategy = async () => {
    if (!strategyVersionId) return;
    setCreatingFromStrategy(true);
    setFromStrategyError('');
    try {
      await api.modelResearch.createVersionFromStrategy(id, {
        experiment_version_id: strategyVersionId,
        note: strategyNote.trim() || undefined,
      });
      setShowFromStrategy(false);
      setStrategyVersionId('');
      setStrategyNote('');
      await loadData();
    } catch (err: any) {
      setFromStrategyError(err.message);
    } finally {
      setCreatingFromStrategy(false);
    }
  };

  const handleSetActive = async (versionId: string) => {
    setVersionActionError('');
    try {
      setRecord(await api.modelResearch.setActiveVersion(id, { version_id: versionId }));
    } catch (err: any) {
      setVersionActionError(err.message);
    }
  };

  const handleRollback = async (versionId: string) => {
    if (!rollbackReason.trim()) return;
    setVersionActionError('');
    try {
      const updated = await api.modelResearch.rollback(id, {
        to_version_id: versionId,
        reason: rollbackReason.trim(),
      });
      setRecord(updated);
      setRollbackVersionId(null);
      setRollbackReason('');
    } catch (err: any) {
      setVersionActionError(err.message);
    }
  };

  const handleCompare = async () => {
    if (!compareA || !compareB) return;
    setCompareLoading(true);
    setCompareError('');
    try {
      setCompareResult(await api.modelResearch.compareVersions(id, compareA, compareB));
    } catch (err: any) {
      setCompareResult(null);
      setCompareError(err.message);
    } finally {
      setCompareLoading(false);
    }
  };

  const handleStatusChange = async () => {
    if (!newStatus) return;
    setStatusError('');
    try {
      const updated = await api.modelResearch.update(id, { status: newStatus });
      setRecord(updated);
      setShowStatusChange(false);
      setNewStatus('');
    } catch (err) {
      const failure = classifyWriteError(err, '變更模型狀態');
      setStatusError(
        failure.unconfirmed ? failure.message : `變更模型狀態失敗：${failure.message}`,
      );
    }
  };

  const handleLock = async (versionId: string) => {
    setVersionActionError('');
    try {
      await api.modelResearch.lockVersion(id, versionId);
      loadData();
    } catch (err) {
      const failure = classifyWriteError(err, '鎖定版本');
      setVersionActionError(
        failure.unconfirmed ? failure.message : `鎖定版本失敗：${failure.message}`,
      );
    }
  };

  const handleRevoke = async (versionId: string) => {
    if (!revokeReason.trim()) return;
    setVersionActionError('');
    try {
      await api.modelResearch.revokeVersion(id, versionId, revokeReason);
      setRevokingVersionId(null);
      setRevokeReason('');
      loadData();
    } catch (err) {
      const failure = classifyWriteError(err, '撤銷版本');
      setVersionActionError(
        failure.unconfirmed ? failure.message : `撤銷版本失敗：${failure.message}`,
      );
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
    } catch (err) {
      const failure = classifyWriteError(err, '下載研究套件');
      setExportError(
        failure.kind === 'network' || failure.kind === 'service_unavailable'
          ? '下載研究套件失敗（連線或伺服器問題）。下載失敗不代表匯出包不存在；請重新整理頁面後再下載。'
          : `下載研究套件失敗：${failure.message}`,
      );
    }
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px', textAlign: 'center' }}>載入中...</div>;
  }
  if (!record) {
    return <div className="container" style={{ paddingTop: '40px' }}>模型記錄不存在。</div>;
  }

  const canAddVersions = record.status === 'draft' || record.status === 'researching';
  const activeVersion = versions.find(v => v.id === record.active_version_id) || null;
  const activeNumber = activeVersion ? activeVersion.version_number : null;

  const tabStyle = (tab: string) => ({
    padding: '8px 20px', cursor: 'pointer', border: 'none', borderBottom: activeTab === tab ? '2px solid var(--primary-text)' : '2px solid transparent',
    backgroundColor: 'transparent', color: activeTab === tab ? 'var(--primary-text)' : 'var(--text-secondary)', fontWeight: activeTab === tab ? 600 : 400,
  });

  const formatSpecValue = (value: any) => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <Link href="/models" style={{ color: 'var(--primary-text)', fontSize: '14px', marginBottom: '16px', display: 'inline-block' }}>
        ← 返回模型列表
      </Link>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ marginBottom: '4px' }}>{record.title}</h1>
          <p style={{ color: 'var(--text-secondary)' }}>{record.purpose}</p>
          {record.description && <p style={{ color: 'var(--text-muted)', fontSize: '14px', marginTop: '8px' }}>{record.description}</p>}
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{
            padding: '4px 12px', borderRadius: '12px', fontSize: '12px',
            backgroundColor: 'rgba(59,130,246,0.16)', color: 'var(--text-secondary)',
          }}>
            {STATUS_LABELS[record.status] || record.status}
          </span>
          {canAddVersions && (
            <button onClick={() => { setShowStatusChange(!showStatusChange); setStatusError(''); }} className="btn btn-secondary" style={{ fontSize: '12px', padding: '4px 12px' }}>
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
          {statusError && (
            <div
              role="alert"
              data-testid="status-change-error"
              className="alert alert-warning"
              style={{ marginTop: '12px', display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}
            >
              <span>{statusError}</span>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  setStatusError('');
                  loadData();
                }}
              >
                重新載入模型
              </button>
            </div>
          )}
        </div>
      )}

      <div
        data-testid="active-version-banner"
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px',
          padding: '12px 16px', marginBottom: '20px', borderRadius: '8px',
          backgroundColor: 'var(--surface-2)', border: '1px solid var(--border-strong)', flexWrap: 'wrap',
        }}
      >
        <div>
          <strong data-testid="active-version-label">
            {activeNumber ? `目前使用版本：v${activeNumber}` : '尚未指定目前使用版本'}
          </strong>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            設定目前使用版本與回滾都不會改寫任何版本內容；每次切換都會留下原因與時間。
          </p>
        </div>
      </div>

      <div style={{ display: 'flex', borderBottom: '1px solid var(--border-strong)', marginBottom: '24px' }}>
        <button style={tabStyle('versions')} onClick={() => setActiveTab('versions')}>
          版本 ({versions.length})
        </button>
        <button style={tabStyle('compare')} onClick={() => setActiveTab('compare')}>
          版本比較
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
            <div style={{ marginBottom: '16px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <button onClick={() => setShowNewVersion(!showNewVersion)} className="btn btn-primary">
                {showNewVersion ? '取消' : '建立新版本'}
              </button>
              <button data-testid="open-from-strategy" onClick={toggleFromStrategy} className="btn btn-secondary">
                {showFromStrategy ? '取消' : '從策略版本建立'}
              </button>
            </div>
          )}

          {showFromStrategy && (
            <div className="card" data-testid="from-strategy-panel" style={{ marginBottom: '20px' }}>
              <h4 style={{ marginBottom: '4px' }}>從已儲存的策略版本建立</h4>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                系統會複製該策略版本的規格與設定雜湊，不會持續連動；之後的策略修訂不會改變這個模型版本。
              </p>
              <div className="form-group">
                <label>實驗</label>
                <select
                  className="input"
                  data-testid="strategy-experiment-select"
                  value={strategyExperimentId}
                  onChange={e => handleStrategyExperimentPick(e.target.value)}
                >
                  <option value="">選擇實驗...</option>
                  {experiments.map(exp => (
                    <option key={exp.id} value={exp.id}>{exp.title}</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>策略版本</label>
                <select
                  className="input"
                  data-testid="strategy-version-select"
                  value={strategyVersionId}
                  onChange={e => setStrategyVersionId(e.target.value)}
                  disabled={!strategyExperimentId}
                >
                  <option value="">選擇版本...</option>
                  {strategyVersions.map(v => (
                    <option key={v.id} value={v.id}>
                      v{v.version_number}{v.change_reason ? ` — ${v.change_reason}` : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>備註（選填）</label>
                <input
                  className="input"
                  data-testid="strategy-note-input"
                  value={strategyNote}
                  maxLength={500}
                  onChange={e => setStrategyNote(e.target.value)}
                  placeholder="為何把這個策略版本存成個人模型？"
                />
              </div>
              <button
                onClick={handleCreateFromStrategy}
                className="btn btn-primary"
                data-testid="strategy-version-submit"
                disabled={!strategyVersionId || creatingFromStrategy}
              >
                {creatingFromStrategy ? '建立中...' : '建立模型版本'}
              </button>
              {fromStrategyError && (
                <div
                  className="alert alert-warning"
                  role="alert"
                  data-testid="from-strategy-error"
                  style={{ marginTop: '12px' }}
                >
                  {fromStrategyError}
                </div>
              )}
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
            <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>尚無版本。</div>
          ) : (
            <div style={{ display: 'grid', gap: '12px' }}>
              {versionActionError && (
                <div
                  className="alert alert-warning"
                  role="alert"
                  data-testid="version-action-error"
                  style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}
                >
                  <span>{versionActionError}</span>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => {
                      setVersionActionError('');
                      loadData();
                    }}
                  >
                    重新載入版本
                  </button>
                </div>
              )}
              {versions.map((v) => {
                const isActive = v.id === record.active_version_id;
                const canRollback = !v.revoked_at && activeNumber !== null && v.version_number < activeNumber;
                const source = v.model_specification_json?.source === 'strategy_version'
                  ? v.model_specification_json
                  : null;
                return (
                  <div key={v.id} className="card" data-testid={`model-version-${v.version_number}`}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                      <div>
                        <h4 style={{ marginBottom: '4px' }}>
                          版本 {v.version_number}
                          {isActive && (
                            <span
                              data-testid={`active-badge-${v.version_number}`}
                              style={{
                                marginLeft: '8px', padding: '2px 8px', borderRadius: '10px',
                                fontSize: '11px', backgroundColor: 'rgba(34,197,94,0.14)', color: 'var(--positive)',
                              }}
                            >
                              目前使用
                            </span>
                          )}
                        </h4>
                        <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                          {MODEL_TYPE_LABELS[v.model_type] || v.model_type} · 建立於 {formatDate(v.created_at)}
                        </p>
                        {source && (
                          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                            來自策略版本 v{source.experiment_version_number}
                            {source.experiment_title ? `（${source.experiment_title}）` : ''}
                            {source.note ? ` · ${source.note}` : ''}
                          </p>
                        )}
                        {v.locked_at && <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>已鎖定: {formatDateTime(v.locked_at)}</p>}
                        {v.revoked_at && <p style={{ fontSize: '12px', color: 'var(--negative)' }}>已撤銷: {v.revocation_reason}</p>}
                      </div>
                      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        {!isActive && !v.revoked_at && (
                          <button
                            onClick={() => handleSetActive(v.id)}
                            className="btn btn-secondary"
                            data-testid={`set-active-${v.version_number}`}
                            style={{ fontSize: '11px', padding: '2px 8px' }}
                          >
                            設為使用中
                          </button>
                        )}
                        {canRollback && (
                          <button
                            onClick={() => {
                              setRollbackVersionId(rollbackVersionId === v.id ? null : v.id);
                              setRollbackReason('');
                            }}
                            className="btn btn-secondary"
                            data-testid={`rollback-${v.version_number}`}
                            style={{ fontSize: '11px', padding: '2px 8px' }}
                          >
                            回滾到此版本
                          </button>
                        )}
                        {!v.locked_at && !v.revoked_at && canAddVersions && (
                          <button onClick={() => handleLock(v.id)} className="btn btn-secondary" style={{ fontSize: '11px', padding: '2px 8px' }}>鎖定</button>
                        )}
                        {!v.revoked_at && (
                          <button onClick={() => setRevokingVersionId(revokingVersionId === v.id ? null : v.id)} className="btn btn-secondary" style={{ fontSize: '11px', padding: '2px 8px' }}>撤銷</button>
                        )}
                      </div>
                    </div>
                    {rollbackVersionId === v.id && (
                      <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-strong)' }}>
                        <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                          回滾會把目前使用版本改回 v{v.version_number}，並要求在操作記錄留下原因；版本內容不會被刪除或改寫。
                        </p>
                        <input
                          className="input"
                          data-testid={`rollback-reason-${v.version_number}`}
                          placeholder="回滾原因（必填）..."
                          value={rollbackReason}
                          maxLength={500}
                          onChange={e => setRollbackReason(e.target.value)}
                          style={{ marginBottom: '8px' }}
                        />
                        <button
                          onClick={() => handleRollback(v.id)}
                          className="btn btn-primary"
                          data-testid={`rollback-confirm-${v.version_number}`}
                          disabled={!rollbackReason.trim()}
                          style={{ fontSize: '12px' }}
                        >
                          確認回滾
                        </button>
                      </div>
                    )}
                    {revokingVersionId === v.id && (
                      <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-strong)' }}>
                        <input className="input" placeholder="撤銷原因..." value={revokeReason} onChange={e => setRevokeReason(e.target.value)} style={{ marginBottom: '8px' }} />
                        <button onClick={() => handleRevoke(v.id)} className="btn btn-primary" style={{ fontSize: '12px' }}>確認撤銷</button>
                      </div>
                    )}
                    <details style={{ marginTop: '8px' }}>
                      <summary style={{ cursor: 'pointer', fontSize: '12px', color: 'var(--text-muted)' }}>查看規格</summary>
                      <pre style={{ fontSize: '11px', backgroundColor: 'var(--surface-2)', padding: '8px', borderRadius: '4px', overflow: 'auto', marginTop: '4px' }}>
                        {JSON.stringify(v.model_specification_json, null, 2)}
                      </pre>
                    </details>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {activeTab === 'compare' && (
        <div>
          {versions.length < 2 ? (
            <div className="card" data-testid="compare-empty" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
              至少需要兩個版本才能比較。先建立或從策略版本匯入另一個版本。
            </div>
          ) : (
            <div className="card">
              <h3 style={{ marginBottom: '4px' }}>比較兩個版本</h3>
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                比較只陳列兩個版本的規格差異與評估記錄；系統不會自動判定哪個版本更好。
              </p>
              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'end' }}>
                <div className="form-group" style={{ marginBottom: 0, minWidth: '200px' }}>
                  <label>版本 A</label>
                  <select
                    className="input"
                    data-testid="compare-select-a"
                    value={compareA}
                    onChange={e => setCompareA(e.target.value)}
                  >
                    <option value="">選擇版本...</option>
                    {versions.map(v => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number}{v.revoked_at ? '（已撤銷）' : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-group" style={{ marginBottom: 0, minWidth: '200px' }}>
                  <label>版本 B</label>
                  <select
                    className="input"
                    data-testid="compare-select-b"
                    value={compareB}
                    onChange={e => setCompareB(e.target.value)}
                  >
                    <option value="">選擇版本...</option>
                    {versions.map(v => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number}{v.revoked_at ? '（已撤銷）' : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  onClick={handleCompare}
                  className="btn btn-primary"
                  data-testid="compare-run"
                  disabled={!compareA || !compareB || compareLoading}
                >
                  {compareLoading ? '比較中...' : '比較'}
                </button>
              </div>

              {compareError && (
                <div
                  className="alert alert-warning"
                  role="alert"
                  data-testid="compare-error"
                  style={{ marginTop: '12px' }}
                >
                  {compareError}
                </div>
              )}

              {compareResult && (
                <div data-testid="compare-result" style={{ marginTop: '20px' }}>
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                    版本 A：v{compareResult.version_a.version_number}
                    {compareResult.version_a.is_active ? '（目前使用）' : ''}
                    {' · '}
                    版本 B：v{compareResult.version_b.version_number}
                    {compareResult.version_b.is_active ? '（目前使用）' : ''}
                  </p>

                  {compareResult.spec_changes.length === 0 ? (
                    <p data-testid="compare-no-changes" style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                      兩個版本的規格完全相同。
                    </p>
                  ) : (
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                        <thead>
                          <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
                            <th style={{ padding: '8px' }}>欄位</th>
                            <th style={{ padding: '8px' }}>版本 A</th>
                            <th style={{ padding: '8px' }}>版本 B</th>
                          </tr>
                        </thead>
                        <tbody>
                          {compareResult.spec_changes.map((change, index) => (
                            <tr
                              key={change.path}
                              data-testid={`compare-change-${index}`}
                              style={{ borderBottom: '1px solid var(--surface-2)' }}
                            >
                              <td style={{ padding: '8px', fontFamily: 'monospace', fontSize: '12px' }}>{change.path}</td>
                              <td style={{ padding: '8px' }}>
                                {change.kind === 'added' ? '（新增）' : formatSpecValue(change.a)}
                              </td>
                              <td style={{ padding: '8px' }}>
                                {change.kind === 'removed' ? '（移除）' : formatSpecValue(change.b)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {compareResult.spec_changes_truncated && (
                        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
                          差異過多，只顯示前 200 項。
                        </p>
                      )}
                    </div>
                  )}

                  <div style={{ display: 'grid', gap: '12px', marginTop: '20px' }}>
                    <div>
                      <h4 style={{ fontSize: '13px', marginBottom: '6px' }}>版本 A 的評估</h4>
                      {compareResult.evaluations.a.length === 0 ? (
                        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>尚無評估記錄。</p>
                      ) : (
                        <ul style={{ fontSize: '12px', color: 'var(--text-secondary)', paddingLeft: '18px' }}>
                          {compareResult.evaluations.a.map(ev => (
                            <li key={ev.id}>{ev.evaluation_type}{ev.result_summary ? ` — ${ev.result_summary}` : ''}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                    <div>
                      <h4 style={{ fontSize: '13px', marginBottom: '6px' }}>版本 B 的評估</h4>
                      {compareResult.evaluations.b.length === 0 ? (
                        <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>尚無評估記錄。</p>
                      ) : (
                        <ul style={{ fontSize: '12px', color: 'var(--text-secondary)', paddingLeft: '18px' }}>
                          {compareResult.evaluations.b.map(ev => (
                            <li key={ev.id}>{ev.evaluation_type}{ev.result_summary ? ` — ${ev.result_summary}` : ''}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>

                  <p data-testid="compare-note" style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '16px' }}>
                    {compareResult.note}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === 'evaluations' && (
        <div>
          {evaluations.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>尚無評估記錄。</div>
          ) : (
            <div style={{ display: 'grid', gap: '12px' }}>
              {evaluations.map((ev) => (
                <div key={ev.id} className="card">
                  <h4 style={{ marginBottom: '4px' }}>{ev.evaluation_type}</h4>
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
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
              <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
                模型卡已建立。模型卡記錄了模型的目的、限制、已知風險及撤銷流程。
              </p>
            </div>
          ) : (
            <div className="card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
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
              <div
                className="alert alert-warning"
                role="alert"
                data-testid="export-error"
                style={{ marginTop: '12px' }}
              >
                {exportError}
              </div>
            )}
          </div>

          {exportResult && exportResult.status === 'completed' && (
            <div className="card">
              <h4 style={{ marginBottom: '12px' }}>匯出完成</h4>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                匯出 ID: {exportResult.id}<br />
                校驗碼: <code style={{ fontSize: '11px' }}>{exportResult.package_checksum?.slice(0, 16)}...</code><br />
                完成時間: {exportResult.completed_at ? formatDateTime(exportResult.completed_at) : '-'}
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
