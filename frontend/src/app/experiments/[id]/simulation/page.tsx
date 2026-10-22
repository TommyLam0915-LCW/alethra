'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

interface SimulationSession {
  id: string;
  experiment_version_id: string;
  status: string;
  initial_virtual_cash: number;
  data_cutoff_at: string;
  current_snapshot_date: string | null;
  paper_equity: number | null;
  current_position_json: any;
  warnings: string[] | null;
  total_decisions: number;
  observed_days: number;
  provider_id: string | null;
  created_at: string;
}

interface AvailableProvider {
  provider_id: string;
  bar_count: number;
  min_date: string;
  max_date: string;
  requested_start: string;
  requested_end: string;
}

interface Decision {
  id: string;
  step_number: number;
  snapshot_date: string;
  decision_type: string;
  decision_detail: any;
  paper_cash: number;
  paper_position: any;
  paper_equity: number;
  warnings: string[] | null;
  engine_version: string;
  data_cutoff_at: string;
  created_at: string;
}

interface Experiment {
  id: string;
  title: string;
  status: string;
  current_version_id: string | null;
}

const DECISION_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  no_action: { label: '無操作', color: '#666', bg: '#f5f5f5' },
  simulated_entry: { label: '模擬買入', color: '#155724', bg: '#d4edda' },
  simulated_exit: { label: '模擬賣出', color: '#721c24', bg: '#f8d7da' },
  forced_exit: { label: '強制平倉', color: '#856404', bg: '#fff3cd' },
  blocked_action: { label: '操作受阻', color: '#856404', bg: '#fff3cd' },
};

const STATUS_LABELS: Record<string, string> = {
  pending: '待開始',
  active: '進行中',
  paused: '已暫停',
  completed: '已完成',
  retired: '已退役',
};

export default function SimulationPage() {
  const router = useRouter();
  const params = useParams();
  const experimentId = params.id as string;

  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [session, setSession] = useState<SimulationSession | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [advancing, setAdvancing] = useState(false);
  const [error, setError] = useState('');
  const [cashInput, setCashInput] = useState('10000');
  const [creating, setCreating] = useState(false);
  const [showDetail, setShowDetail] = useState<number | null>(null);
  const [manualMode, setManualMode] = useState(false);
  const [manualAction, setManualAction] = useState<'buy' | 'sell' | 'hold'>('hold');
  const [manualQuantity, setManualQuantity] = useState('100');
  const [manualRationale, setManualRationale] = useState('');
  const [manualReflection, setManualReflection] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [availableProviders, setAvailableProviders] = useState<AvailableProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [editingReflection, setEditingReflection] = useState<string | null>(null);
  const [reflectionInput, setReflectionInput] = useState('');
  const [updatingReflection, setUpdatingReflection] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadExperiment();
  }, [experimentId, router]);

  const loadExperiment = async () => {
    try {
      const exp = await api.experiments.get(experimentId);
      setExperiment(exp);
      if (exp.status !== 'locked') {
        setError('實驗必須鎖定後才能進行前進模擬');
      }
      // Fetch available providers for this version
      if (exp.current_version_id) {
        loadAvailableProviders(exp.current_version_id);
      }
    } catch (err: any) {
      setError(err.message || '載入失敗');
    } finally {
      setLoading(false);
    }
  };

  const loadAvailableProviders = async (versionId: string) => {
    setLoadingProviders(true);
    try {
      const result = await api.simulations.getAvailableProviders(versionId);
      setAvailableProviders(result.available_providers || []);
      // Auto-select if only one provider available
      if (result.available_providers?.length === 1) {
        setSelectedProvider(result.available_providers[0].provider_id);
      }
    } catch (err: any) {
      console.error('Failed to load providers:', err);
    } finally {
      setLoadingProviders(false);
    }
  };

  const loadSession = useCallback(async () => {
    if (!session?.id) return;
    try {
      const [s, d] = await Promise.all([
        api.simulations.get(session.id),
        api.simulations.decisions(session.id),
      ]);
      setSession(s);
      setDecisions(d);
    } catch (err: any) {
      setError(err.message);
    }
  }, [session?.id]);

  const handleCreate = async () => {
    if (!experiment?.current_version_id) return;
    const cash = parseFloat(cashInput);
    if (isNaN(cash) || cash <= 0) {
      setError('請輸入有效的虛擬資金');
      return;
    }
    if (!selectedProvider) {
      setError('請選擇資料來源');
      return;
    }
    setCreating(true);
    setError('');
    try {
      const s = await api.simulations.create(experiment.current_version_id, {
        initial_virtual_cash: cash,
        provider_id: selectedProvider,
      });
      setSession(s);
      setDecisions([]);
    } catch (err: any) {
      setError(err.message || '建立模擬失敗');
    } finally {
      setCreating(false);
    }
  };

  const handleAdvance = async () => {
    if (!session?.id) return;
    setAdvancing(true);
    setError('');
    try {
      const result = await api.simulations.advance(session.id);
      setSession(result.session);
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
    } catch (err: any) {
      setError(err.message || '推進失敗');
    } finally {
      setAdvancing(false);
    }
  };

  const handlePause = async () => {
    if (!session?.id) return;
    try {
      const s = await api.simulations.pause(session.id);
      setSession(s);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleResume = async () => {
    if (!session?.id) return;
    try {
      const s = await api.simulations.resume(session.id);
      setSession(s);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleRetire = async () => {
    if (!session?.id) return;
    if (!confirm('確定要退役此模擬？退役後無法繼續推進。')) return;
    try {
      const s = await api.simulations.retire(session.id);
      setSession(s);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleManualDecision = async () => {
    if (!session?.id) return;
    setSubmitting(true);
    setError('');
    try {
      const idempotencyKey = `manual-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      const payload: any = {
        action: manualAction,
        idempotency_key: idempotencyKey,
        rationale: manualRationale || undefined,
        reflection: manualReflection || undefined,
      };
      if (manualAction === 'buy') {
        const qty = parseInt(manualQuantity);
        if (isNaN(qty) || qty <= 0) {
          setError('請輸入有效的數量');
          setSubmitting(false);
          return;
        }
        payload.quantity = qty;
      }
      const result = await api.simulations.manualDecision(session.id, payload);
      setSession(result.session);
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      setManualRationale('');
      setManualReflection('');
    } catch (err: any) {
      setError(err.message || '決策提交失敗');
    } finally {
      setSubmitting(false);
    }
  };

  const handleUpdateReflection = async (decisionId: string) => {
    if (!session || !reflectionInput.trim()) return;
    
    setUpdatingReflection(true);
    try {
      await api.simulations.updateReflection(session.id, decisionId, reflectionInput.trim());
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      setEditingReflection(null);
      setReflectionInput('');
    } catch (err: any) {
      setError(err.message || '反思更新失敗');
    } finally {
      setUpdatingReflection(false);
    }
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px' }}>載入中...</div>;
  }

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href={`/experiments/${experimentId}`} style={{ color: '#0070f3', textDecoration: 'none' }}>
          &larr; 返回實驗
        </Link>
      </div>

      <h1 style={{ marginBottom: '8px' }}>前進模擬</h1>
      <p style={{ color: '#666', marginBottom: '24px' }}>
        {experiment?.title}
      </p>

      <div className="alert alert-info" style={{ marginBottom: '24px' }}>
        <strong>方法論聲明：</strong>此模擬使用鎖定後的策略規格，逐步釋放歷史資料快照。
        每一步僅使用當時可取得的資料，不允許使用後續資訊（no hindsight）。
        所有交易均為虛擬模擬，不涉及真實資金。
      </div>

      {error && <div className="alert alert-warning">{error}</div>}

      {!session && experiment?.status === 'locked' && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '16px' }}>建立模擬工作階段</h3>
          <div className="form-group">
            <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
              虛擬初始資金 (USD)
            </label>
            <input
              type="number"
              className="input"
              value={cashInput}
              onChange={(e) => setCashInput(e.target.value)}
              min="1"
              step="100"
            />
          </div>
          
          {loadingProviders ? (
            <div style={{ padding: '12px', color: '#666' }}>載入資料來源中...</div>
          ) : availableProviders.length === 0 ? (
            <div className="alert alert-warning" style={{ marginTop: '12px' }}>
              此實驗標的無可用儲存資料
            </div>
          ) : (
            <div className="form-group" style={{ marginTop: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                資料來源
              </label>
              {availableProviders.length === 1 ? (
                <div style={{ padding: '10px', background: '#f8f9fa', borderRadius: '4px', border: '1px solid #dee2e6' }}>
                  <div style={{ fontWeight: 600 }}>{availableProviders[0].provider_id}</div>
                  <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                    {availableProviders[0].bar_count} 筆資料 | 
                    覆蓋區間: {availableProviders[0].min_date} 至 {availableProviders[0].max_date}
                  </div>
                  {availableProviders[0].min_date > availableProviders[0].requested_start || 
                   availableProviders[0].max_date < availableProviders[0].requested_end ? (
                    <div style={{ fontSize: '12px', color: '#856404', marginTop: '4px' }}>
                      ⚠️ 部分覆蓋：請求區間 {availableProviders[0].requested_start} 至 {availableProviders[0].requested_end}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {availableProviders.map((p) => (
                    <label key={p.provider_id} style={{ 
                      display: 'flex', 
                      alignItems: 'flex-start', 
                      padding: '10px', 
                      background: selectedProvider === p.provider_id ? '#e7f3ff' : '#f8f9fa',
                      borderRadius: '4px', 
                      border: selectedProvider === p.provider_id ? '2px solid #007bff' : '1px solid #dee2e6',
                      cursor: 'pointer',
                    }}>
                      <input
                        type="radio"
                        name="provider"
                        value={p.provider_id}
                        checked={selectedProvider === p.provider_id}
                        onChange={(e) => setSelectedProvider(e.target.value)}
                        style={{ marginTop: '2px', marginRight: '8px' }}
                      />
                      <div>
                        <div style={{ fontWeight: 600 }}>{p.provider_id}</div>
                        <div style={{ fontSize: '12px', color: '#666', marginTop: '2px' }}>
                          {p.bar_count} 筆資料 | 
                          覆蓋區間: {p.min_date} 至 {p.max_date}
                        </div>
                        {p.min_date > p.requested_start || p.max_date < p.requested_end ? (
                          <div style={{ fontSize: '12px', color: '#856404', marginTop: '2px' }}>
                            ⚠️ 部分覆蓋：請求區間 {p.requested_start} 至 {p.requested_end}
                          </div>
                        ) : null}
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
          
          <button
            className="btn btn-primary"
            onClick={handleCreate}
            disabled={creating || !selectedProvider || availableProviders.length === 0}
            style={{ marginTop: '12px' }}
          >
            {creating ? '建立中...' : '開始模擬'}
          </button>
        </div>
      )}

      {session && (
        <>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: '16px',
            marginBottom: '24px',
          }}>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>狀態</div>
              <div style={{ fontSize: '20px', fontWeight: 700 }}>
                {STATUS_LABELS[session.status] || session.status}
              </div>
            </div>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>虛擬淨值</div>
              <div style={{ fontSize: '20px', fontWeight: 700 }}>
                ${session.paper_equity?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
              <div style={{ fontSize: '12px', color: session.paper_equity! >= session.initial_virtual_cash ? '#155724' : '#721c24' }}>
                {session.paper_equity! >= session.initial_virtual_cash ? '+' : ''}
                {((session.paper_equity! - session.initial_virtual_cash) / session.initial_virtual_cash * 100).toFixed(2)}%
              </div>
            </div>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>觀察天數</div>
              <div style={{ fontSize: '20px', fontWeight: 700 }}>{session.observed_days}</div>
            </div>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>決策次數</div>
              <div style={{ fontSize: '20px', fontWeight: 700 }}>{session.total_decisions}</div>
            </div>
            {session.provider_id && (
              <div className="card" style={{ padding: '16px' }}>
                <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>資料來源</div>
                <div style={{ fontSize: '16px', fontWeight: 700 }}>{session.provider_id}</div>
                <div style={{ fontSize: '11px', color: '#888', marginTop: '2px' }}>儲存資料</div>
              </div>
            )}
          </div>

          {session.current_position_json && (
            <div className="card" style={{ marginBottom: '24px', borderLeft: '4px solid #0070f3' }}>
              <h3 style={{ marginBottom: '8px' }}>目前部位</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px' }}>
                <div>
                  <span style={{ fontSize: '12px', color: '#666' }}>標的</span>
                  <div style={{ fontWeight: 600 }}>{session.current_position_json.symbol}</div>
                </div>
                <div>
                  <span style={{ fontSize: '12px', color: '#666' }}>股數</span>
                  <div style={{ fontWeight: 600 }}>{session.current_position_json.shares}</div>
                </div>
                <div>
                  <span style={{ fontSize: '12px', color: '#666' }}>進場價</span>
                  <div style={{ fontWeight: 600 }}>${session.current_position_json.entry_price?.toFixed(2)}</div>
                </div>
                <div>
                  <span style={{ fontSize: '12px', color: '#666' }}>進場日</span>
                  <div style={{ fontWeight: 600 }}>{session.current_position_json.entry_date}</div>
                </div>
                {session.current_position_json.unrealized_pnl !== undefined && (
                  <div>
                    <span style={{ fontSize: '12px', color: '#666' }}>未實現損益</span>
                    <div style={{
                      fontWeight: 600,
                      color: session.current_position_json.unrealized_pnl >= 0 ? '#155724' : '#721c24',
                    }}>
                      ${session.current_position_json.unrealized_pnl?.toFixed(2)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: '8px', marginBottom: '24px', flexWrap: 'wrap' }}>
            {(session.status === 'pending' || session.status === 'active') && (
              <>
                <button
                  className="btn btn-primary"
                  onClick={handleAdvance}
                  disabled={advancing || manualMode}
                >
                  {advancing ? '推進中...' : '自動推進一日'}
                </button>
                <button
                  className="btn"
                  style={{ backgroundColor: manualMode ? '#0070f3' : '#eaeaea', color: manualMode ? '#fff' : '#000' }}
                  onClick={() => setManualMode(!manualMode)}
                >
                  {manualMode ? '切換至自動模式' : '切換至手動模式'}
                </button>
              </>
            )}
            {session.status === 'active' && (
              <button className="btn" style={{ backgroundColor: '#eaeaea' }} onClick={handlePause}>
                暫停
              </button>
            )}
            {session.status === 'paused' && (
              <button className="btn btn-primary" onClick={handleResume}>
                繼續
              </button>
            )}
            {!['completed', 'retired'].includes(session.status) && (
              <button
                className="btn"
                style={{ backgroundColor: '#f8d7da', color: '#721c24' }}
                onClick={handleRetire}
              >
                退役
              </button>
            )}
          </div>

          {manualMode && (session.status === 'pending' || session.status === 'active') && (
            <div className="card" style={{ marginBottom: '24px', borderLeft: '4px solid #0070f3' }}>
              <h3 style={{ marginBottom: '16px' }}>手動決策</h3>
              <div className="alert alert-info" style={{ marginBottom: '16px', fontSize: '13px' }}>
                <strong>執行時序規則：</strong>決策於觀察日收盤後做出，成交於下一可用交易日開盤。
                買入與賣出均採用相同規則，確保無前瞻偏差。
              </div>
              <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', flexWrap: 'wrap' }}>
                <button
                  className="btn"
                  style={{
                    backgroundColor: manualAction === 'buy' ? '#28a745' : '#eaeaea',
                    color: manualAction === 'buy' ? '#fff' : '#000',
                    minWidth: '100px',
                  }}
                  onClick={() => setManualAction('buy')}
                >
                  買入 (BUY)
                </button>
                <button
                  className="btn"
                  style={{
                    backgroundColor: manualAction === 'sell' ? '#dc3545' : '#eaeaea',
                    color: manualAction === 'sell' ? '#fff' : '#000',
                    minWidth: '100px',
                  }}
                  onClick={() => setManualAction('sell')}
                >
                  賣出 (SELL)
                </button>
                <button
                  className="btn"
                  style={{
                    backgroundColor: manualAction === 'hold' ? '#6c757d' : '#eaeaea',
                    color: manualAction === 'hold' ? '#fff' : '#000',
                    minWidth: '100px',
                  }}
                  onClick={() => setManualAction('hold')}
                >
                  持有 (HOLD)
                </button>
              </div>
              {manualAction === 'buy' && (
                <div className="form-group" style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                    數量 (股)
                  </label>
                  <input
                    type="number"
                    className="input"
                    value={manualQuantity}
                    onChange={(e) => setManualQuantity(e.target.value)}
                    min="1"
                    step="1"
                  />
                </div>
              )}
              <div className="form-group" style={{ marginBottom: '16px' }}>
                <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                  決策理由 (可選)
                </label>
                <textarea
                  className="input"
                  value={manualRationale}
                  onChange={(e) => setManualRationale(e.target.value)}
                  placeholder="說明您做出此決策的原因..."
                  rows={2}
                  style={{ resize: 'vertical' }}
                />
              </div>
              <div className="form-group" style={{ marginBottom: '16px' }}>
                <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                  事後反思 (可選，可稍後填寫)
                </label>
                <textarea
                  className="input"
                  value={manualReflection}
                  onChange={(e) => setManualReflection(e.target.value)}
                  placeholder="決策後的反思與學習..."
                  rows={2}
                  style={{ resize: 'vertical' }}
                />
              </div>
              <button
                className="btn btn-primary"
                onClick={handleManualDecision}
                disabled={submitting}
              >
                {submitting ? '提交中...' : '提交決策'}
              </button>
            </div>
          )}

          {session.warnings && session.warnings.length > 0 && (
            <div className="alert alert-warning" style={{ marginBottom: '24px' }}>
              <strong>警告 ({session.warnings.length}):</strong>
              <ul style={{ marginTop: '8px', paddingLeft: '20px' }}>
                {session.warnings.slice(-5).map((w, i) => (
                  <li key={i} style={{ marginBottom: '4px' }}>{w}</li>
                ))}
                {session.warnings.length > 5 && (
                  <li style={{ color: '#999' }}>...及其他 {session.warnings.length - 5} 則</li>
                )}
              </ul>
            </div>
          )}

          <div className="card">
            <h3 style={{ marginBottom: '16px' }}>決策時間線</h3>
            {decisions.length === 0 ? (
              <p style={{ color: '#999' }}>尚無決策紀錄。點擊「推進一日」開始模擬。</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {decisions.map((d) => {
                  const style = DECISION_LABELS[d.decision_type] || DECISION_LABELS.no_action;
                  const isExpanded = showDetail === d.step_number;
                  return (
                    <div
                      key={d.id}
                      style={{
                        border: '1px solid #eaeaea',
                        borderLeft: `4px solid ${style.color}`,
                        borderRadius: '6px',
                        padding: '12px 16px',
                        backgroundColor: isExpanded ? '#fafafa' : '#fff',
                        cursor: 'pointer',
                      }}
                      onClick={() => setShowDetail(isExpanded ? null : d.step_number)}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                          <span style={{ fontSize: '12px', color: '#999', minWidth: '24px' }}>
                            #{d.step_number}
                          </span>
                          <span style={{ fontSize: '13px', color: '#666' }}>
                            {d.snapshot_date}
                          </span>
                          <span style={{
                            padding: '2px 10px',
                            borderRadius: '12px',
                            fontSize: '12px',
                            fontWeight: 600,
                            color: style.color,
                            backgroundColor: style.bg,
                          }}>
                            {style.label}
                          </span>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <span style={{ fontSize: '14px', fontWeight: 600 }}>
                            ${d.paper_equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                        </div>
                      </div>

                      {isExpanded && d.decision_detail && (
                        <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #eaeaea' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '8px', fontSize: '13px' }}>
                            {d.decision_detail.symbol && (
                              <div><span style={{ color: '#999' }}>標的:</span> {d.decision_detail.symbol}</div>
                            )}
                            {d.decision_detail.action && (
                              <div><span style={{ color: '#999' }}>操作:</span> {d.decision_detail.action.toUpperCase()}</div>
                            )}
                            {d.decision_detail.fill?.quantity && (
                              <div><span style={{ color: '#999' }}>數量:</span> {d.decision_detail.fill.quantity}</div>
                            )}
                            {d.decision_detail.fill?.observed_date && (
                              <div><span style={{ color: '#999' }}>觀察日:</span> {d.decision_detail.fill.observed_date}</div>
                            )}
                            {d.decision_detail.fill?.observed_close && (
                              <div><span style={{ color: '#999' }}>觀察收盤價:</span> ${d.decision_detail.fill.observed_close?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.execution_date && (
                              <div><span style={{ color: '#999' }}>成交日:</span> {d.decision_detail.fill.execution_date}</div>
                            )}
                            {d.decision_detail.fill?.price && (
                              <div><span style={{ color: '#999' }}>成交價:</span> ${d.decision_detail.fill.price?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.execution_rule && (
                              <div><span style={{ color: '#999' }}>執行規則:</span> {d.decision_detail.fill.execution_rule}</div>
                            )}
                            {d.decision_detail.fill?.commission !== undefined && (
                              <div><span style={{ color: '#999' }}>佣金:</span> ${d.decision_detail.fill.commission?.toFixed(4)}</div>
                            )}
                            {d.decision_detail.fill?.slippage !== undefined && (
                              <div><span style={{ color: '#999' }}>滑價:</span> ${d.decision_detail.fill.slippage?.toFixed(4)}</div>
                            )}
                            {d.decision_detail.fill?.total_cost && (
                              <div><span style={{ color: '#999' }}>總成本:</span> ${d.decision_detail.fill.total_cost?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.net_proceeds && (
                              <div><span style={{ color: '#999' }}>淨收入:</span> ${d.decision_detail.fill.net_proceeds?.toFixed(2)}</div>
                            )}
                            {(d.decision_detail.fill?.gross_pnl !== undefined || d.decision_detail.fill?.realized_pnl !== undefined) && (
                              <div>
                                <span style={{ color: '#999' }}>價差損益:</span> 
                                <span style={{ 
                                  color: (d.decision_detail.fill.gross_pnl ?? d.decision_detail.fill.realized_pnl) >= 0 ? '#155724' : '#721c24', 
                                  fontWeight: 600 
                                }}>
                                  {' '}${(d.decision_detail.fill.gross_pnl ?? d.decision_detail.fill.realized_pnl)?.toFixed(2)}
                                </span>
                              </div>
                            )}
                            {d.decision_detail.rejection && (
                              <div style={{ gridColumn: '1 / -1', padding: '8px', backgroundColor: '#fff3cd', borderRadius: '4px' }}>
                                <span style={{ color: '#856404', fontWeight: 600 }}>拒絕:</span> {d.decision_detail.rejection.reason}
                                {d.decision_detail.rejection.required && (
                                  <span> (需要: ${d.decision_detail.rejection.required.toFixed(2)}, 可用: ${d.decision_detail.rejection.available.toFixed(2)})</span>
                                )}
                              </div>
                            )}
                            {d.decision_detail.rationale && (
                              <div style={{ gridColumn: '1 / -1' }}><span style={{ color: '#999' }}>決策理由:</span> {d.decision_detail.rationale}</div>
                            )}
                            {d.decision_detail.reflection && (
                              <div style={{ gridColumn: '1 / -1' }}>
                                <span style={{ color: '#999' }}>事後反思:</span> {d.decision_detail.reflection}
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setEditingReflection(d.id);
                                    setReflectionInput(d.decision_detail.reflection || '');
                                  }}
                                  style={{
                                    marginLeft: '8px',
                                    padding: '2px 8px',
                                    fontSize: '11px',
                                    border: '1px solid #ccc',
                                    borderRadius: '3px',
                                    backgroundColor: '#fff',
                                    cursor: 'pointer',
                                  }}
                                >
                                  編輯
                                </button>
                              </div>
                            )}
                            {!d.decision_detail.reflection && (
                              <div style={{ gridColumn: '1 / -1' }}>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setEditingReflection(d.id);
                                    setReflectionInput('');
                                  }}
                                  style={{
                                    padding: '4px 12px',
                                    fontSize: '12px',
                                    border: '1px solid #0070f3',
                                    borderRadius: '4px',
                                    backgroundColor: '#fff',
                                    color: '#0070f3',
                                    cursor: 'pointer',
                                  }}
                                >
                                  新增事後反思
                                </button>
                              </div>
                            )}
                            {editingReflection === d.id && (
                              <div style={{ gridColumn: '1 / -1', marginTop: '8px' }} onClick={(e) => e.stopPropagation()}>
                                <textarea
                                  value={reflectionInput}
                                  onChange={(e) => setReflectionInput(e.target.value)}
                                  placeholder="輸入事後反思..."
                                  style={{
                                    width: '100%',
                                    minHeight: '80px',
                                    padding: '8px',
                                    border: '1px solid #ccc',
                                    borderRadius: '4px',
                                    fontSize: '13px',
                                    fontFamily: 'inherit',
                                    resize: 'vertical',
                                  }}
                                />
                                <div style={{ marginTop: '8px', display: 'flex', gap: '8px' }}>
                                  <button
                                    onClick={() => handleUpdateReflection(d.id)}
                                    disabled={updatingReflection || !reflectionInput.trim()}
                                    style={{
                                      padding: '6px 16px',
                                      fontSize: '12px',
                                      border: 'none',
                                      borderRadius: '4px',
                                      backgroundColor: '#0070f3',
                                      color: '#fff',
                                      cursor: updatingReflection || !reflectionInput.trim() ? 'not-allowed' : 'pointer',
                                      opacity: updatingReflection || !reflectionInput.trim() ? 0.6 : 1,
                                    }}
                                  >
                                    {updatingReflection ? '保存中...' : '保存'}
                                  </button>
                                  <button
                                    onClick={() => {
                                      setEditingReflection(null);
                                      setReflectionInput('');
                                    }}
                                    style={{
                                      padding: '6px 16px',
                                      fontSize: '12px',
                                      border: '1px solid #ccc',
                                      borderRadius: '4px',
                                      backgroundColor: '#fff',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    取消
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                          <div style={{ marginTop: '8px', fontSize: '12px', color: '#999' }}>
                            現金: ${d.paper_cash.toFixed(2)} | 引擎: {d.engine_version} | 資料截止: {d.data_cutoff_at}
                          </div>
                        </div>
                      )}

                      {d.warnings && d.warnings.length > 0 && (
                        <div style={{ marginTop: '6px', fontSize: '12px', color: '#856404' }}>
                          {d.warnings.join('; ')}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div style={{ marginTop: '24px', padding: '16px', backgroundColor: '#f8f9fa', borderRadius: '8px', fontSize: '12px', color: '#666' }}>
            <strong>方法論狀態：</strong>
            {manualMode ? 'manual_replay' :
             session.status === 'completed' ? 'historical_test_complete' :
             session.status === 'retired' ? 'retired' :
             'historical_replay'}
            {' '}&mdash; 
            {manualMode ? '人工程回放模式，每步由使用者決定買入／賣出／持有。' :
             '自動策略回放模式，由引擎根據策略規則自動決策。'}
            {' '}資料來源：保存的歷史數據（零即時API請求）。
            資料截止日：{session.data_cutoff_at}。
            <br />
            <strong>執行時序：</strong>
            決策於觀察日收盤後做出，成交於下一可用交易日開盤（買入與賣出均採用相同規則）。
          </div>
        </>
      )}
    </div>
  );
}
