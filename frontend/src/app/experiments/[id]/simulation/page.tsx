'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api, generateIdempotencyKey, isStateVersionConflict, isIdempotencyConflict } from '@/lib/api';
import type { ManualDecisionRequest, ManualDecisionResponse } from '@/lib/api';

interface SimulationSession {
  id: string;
  experiment_version_id: string;
  status: string;
  initial_virtual_cash: number;
  data_cutoff_at: string;
  current_snapshot_date?: string;
  paper_equity?: number;
  current_position_json?: any;
  warnings?: string[];
  total_decisions: number;
  observed_days: number;
  state_version: number;
  provider_id?: string;
  provider_confirmed: boolean;
  created_at: string;
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
  
  // Manual mode state
  const [mode, setMode] = useState<'automatic' | 'manual'>('automatic');
  const [availableProviders, setAvailableProviders] = useState<Array<{
    provider_id: string;
    bar_count: number;
    min_date: string;
    max_date: string;
    requested_start: string;
    requested_end: string;
  }>>([]);
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [providerError, setProviderError] = useState('');
  const [selectedProviderId, setSelectedProviderId] = useState<string>('');
  const [manualAction, setManualAction] = useState<'buy' | 'sell' | 'hold'>('hold');
  const [manualQuantity, setManualQuantity] = useState<string>('1');
  const [manualRationale, setManualRationale] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const [pendingIdempotencyKey, setPendingIdempotencyKey] = useState<string | null>(null);
  const [editingReflection, setEditingReflection] = useState<string | null>(null);
  const [reflectionText, setReflectionText] = useState<string>('');
  const [savingReflection, setSavingReflection] = useState(false);

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
    } catch (err: any) {
      setError(err.message || '載入失敗');
    } finally {
      setLoading(false);
    }
  };

  const loadAvailableProviders = async () => {
    if (!experiment?.current_version_id) return;
    
    setLoadingProviders(true);
    setProviderError('');
    setAvailableProviders([]);
    setSelectedProviderId('');
    
    try {
      const result = await api.simulations.availableProviders(experiment.current_version_id);
      setAvailableProviders(result.available_providers || []);
      
      // Auto-select if only one provider available
      if (result.available_providers?.length === 1) {
        setSelectedProviderId(result.available_providers[0].provider_id);
      }
    } catch (err: any) {
      setProviderError(err.message || '無法載入可用資料來源');
    } finally {
      setLoadingProviders(false);
    }
  };

  useEffect(() => {
    if (mode === 'manual' && experiment?.current_version_id && !session) {
      loadAvailableProviders();
    }
  }, [mode, experiment?.current_version_id, session]);

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
    
    // Validate provider selection for manual mode
    if (mode === 'manual') {
      if (availableProviders.length === 0) {
        setError('沒有可用的歷史資料來源。請先載入資料。');
        return;
      }
      if (availableProviders.length > 1 && !selectedProviderId) {
        setError('有多個資料來源可用，請選擇一個。');
        return;
      }
    }
    
    setCreating(true);
    setError('');
    try {
      const createData: { initial_virtual_cash: number; provider_id?: string } = {
        initial_virtual_cash: cash,
      };
      
      // Include provider_id if selected (manual mode with explicit selection)
      if (mode === 'manual' && selectedProviderId) {
        createData.provider_id = selectedProviderId;
      }
      
      const s = await api.simulations.create(experiment.current_version_id, createData);
      setSession(s);
      setDecisions([]);
    } catch (err: any) {
      const errorMessage = err.message || '建立模擬失敗';
      
      // Surface backend provider validation errors clearly
      if (errorMessage.includes('Multiple providers')) {
        setError(`${errorMessage} 請重新選擇資料來源。`);
        await loadAvailableProviders();
      } else if (errorMessage.includes('No stored data')) {
        setError(`${errorMessage} 請先載入歷史資料。`);
      } else if (errorMessage.includes('has no stored data')) {
        setError(`${errorMessage} 請選擇其他資料來源。`);
        await loadAvailableProviders();
      } else {
        setError(errorMessage);
      }
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
    
    const quantity = manualAction !== 'hold' ? parseInt(manualQuantity) : undefined;
    if (manualAction !== 'hold' && (!quantity || quantity <= 0)) {
      setError('請輸入有效的數量');
      return;
    }

    setSubmitting(true);
    setError('');
    
    const idempotencyKey = pendingIdempotencyKey || generateIdempotencyKey();
    setPendingIdempotencyKey(idempotencyKey);

    try {
      const payload: ManualDecisionRequest = {
        action: manualAction,
        quantity,
        rationale: manualRationale || undefined,
        idempotency_key: idempotencyKey,
        expected_version: session.state_version,
      };

      const result: ManualDecisionResponse = await api.simulations.manualDecision(session.id, payload);
      
      setSession(result.session);
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      
      setPendingIdempotencyKey(null);
      setManualRationale('');
      
      if (result.rejection) {
        setError(`決策被拒絕：${result.rejection.reason}`);
      }
    } catch (err: any) {
      if (isStateVersionConflict(err)) {
        setError('狀態版本衝突：工作階段已被修改。請重新載入後再試。');
        await loadSession();
      } else if (isIdempotencyConflict(err)) {
        setError('冪等性金鑰衝突：相同金鑰但不同內容。請使用新的金鑰重試。');
        setPendingIdempotencyKey(null);
      } else {
        setError(err.message || '提交決策失敗');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleUpdateReflection = async (decisionId: string) => {
    if (!session?.id || !reflectionText.trim()) return;
    
    setSavingReflection(true);
    setError('');
    
    try {
      await api.simulations.updateReflection(session.id, decisionId, {
        reflection: reflectionText.trim(),
      });
      
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      setEditingReflection(null);
      setReflectionText('');
    } catch (err: any) {
      setError(err.message || '更新反思失敗');
    } finally {
      setSavingReflection(false);
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
          
          <div className="form-group" style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
              模擬模式
            </label>
            <div style={{ display: 'flex', gap: '12px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                <input
                  type="radio"
                  name="mode"
                  value="automatic"
                  checked={mode === 'automatic'}
                  onChange={(e) => setMode(e.target.value as 'automatic' | 'manual')}
                />
                <span>自動模式（策略驅動）</span>
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                <input
                  type="radio"
                  name="mode"
                  value="manual"
                  checked={mode === 'manual'}
                  onChange={(e) => setMode(e.target.value as 'automatic' | 'manual')}
                />
                <span>手動模式（人工決策）</span>
              </label>
            </div>
          </div>
          
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
          
          {mode === 'manual' && (
            <div style={{ marginTop: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                歷史資料來源
              </label>
              
              {loadingProviders && (
                <div style={{ padding: '12px', backgroundColor: '#f8f9fa', borderRadius: '6px', fontSize: '13px', color: '#666' }}>
                  載入可用資料來源...
                </div>
              )}
              
              {providerError && (
                <div style={{ padding: '12px', backgroundColor: '#f8d7da', borderRadius: '6px', fontSize: '13px', color: '#721c24' }}>
                  <strong>錯誤：</strong>{providerError}
                </div>
              )}
              
              {!loadingProviders && !providerError && availableProviders.length === 0 && (
                <div style={{ padding: '12px', backgroundColor: '#fff3cd', borderRadius: '6px', fontSize: '13px', color: '#856404' }}>
                  <strong>無法建立：</strong>沒有可用的歷史資料來源。請先載入資料或選擇其他實驗。
                </div>
              )}
              
              {!loadingProviders && !providerError && availableProviders.length === 1 && (
                <div style={{ padding: '12px', backgroundColor: '#d4edda', borderRadius: '6px', fontSize: '13px', color: '#155724' }}>
                  <strong>自動選擇：</strong>{availableProviders[0].provider_id}
                  <div style={{ marginTop: '4px', fontSize: '12px' }}>
                    資料範圍：{availableProviders[0].min_date} 至 {availableProviders[0].max_date}（{availableProviders[0].bar_count} 筆）
                  </div>
                </div>
              )}
              
              {!loadingProviders && !providerError && availableProviders.length > 1 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {availableProviders.map((provider) => (
                    <label
                      key={provider.provider_id}
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        gap: '8px',
                        padding: '12px',
                        border: `2px solid ${selectedProviderId === provider.provider_id ? '#0070f3' : '#eaeaea'}`,
                        borderRadius: '6px',
                        cursor: 'pointer',
                        backgroundColor: selectedProviderId === provider.provider_id ? '#f0f7ff' : '#fff',
                      }}
                    >
                      <input
                        type="radio"
                        name="provider"
                        value={provider.provider_id}
                        checked={selectedProviderId === provider.provider_id}
                        onChange={(e) => setSelectedProviderId(e.target.value)}
                        style={{ marginTop: '2px' }}
                      />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 600, marginBottom: '4px' }}>{provider.provider_id}</div>
                        <div style={{ fontSize: '12px', color: '#666' }}>
                          資料範圍：{provider.min_date} 至 {provider.max_date}
                        </div>
                        <div style={{ fontSize: '12px', color: '#666' }}>
                          資料筆數：{provider.bar_count} 筆
                        </div>
                        {(provider.min_date !== provider.requested_start || provider.max_date !== provider.requested_end) && (
                          <div style={{ fontSize: '11px', color: '#856404', marginTop: '4px' }}>
                            注意：實際覆蓋範圍與請求範圍不完全一致
                          </div>
                        )}
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
            disabled={creating}
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
            {mode === 'automatic' && (session.status === 'pending' || session.status === 'active') && (
              <button
                className="btn btn-primary"
                onClick={handleAdvance}
                disabled={advancing}
              >
                {advancing ? '推進中...' : '推進一日'}
              </button>
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

          {mode === 'manual' && session.status !== 'completed' && session.status !== 'retired' && (
            <div className="card" style={{ marginBottom: '24px', borderLeft: '4px solid #0070f3' }}>
              <h3 style={{ marginBottom: '16px' }}>人工決策</h3>
              
              <div style={{ marginBottom: '16px', padding: '12px', backgroundColor: '#f8f9fa', borderRadius: '6px', fontSize: '13px' }}>
                <div style={{ marginBottom: '8px' }}>
                  <strong>當前觀察日期：</strong>{session.current_snapshot_date || '尚未開始'}
                </div>
                <div style={{ marginBottom: '8px' }}>
                  <strong>當前現金：</strong>${session.paper_equity?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '0.00'}
                </div>
                {session.current_position_json && (
                  <div>
                    <strong>當前持倉：</strong>{session.current_position_json.shares} 股 @ ${session.current_position_json.entry_price?.toFixed(2)}
                  </div>
                )}
              </div>

              <div className="alert alert-info" style={{ marginBottom: '16px', fontSize: '13px' }}>
                <strong>執行規則：</strong>決策將在下一個交易日以開盤價執行。當前觀察日期的資料已鎖定，不使用後續資訊（no hindsight）。
              </div>

              <div className="form-group" style={{ marginBottom: '16px' }}>
                <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                  決策類型
                </label>
                <div style={{ display: 'flex', gap: '12px' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="action"
                      value="buy"
                      checked={manualAction === 'buy'}
                      onChange={(e) => setManualAction(e.target.value as 'buy' | 'sell' | 'hold')}
                      disabled={!!session.current_position_json}
                    />
                    <span>買入 (BUY)</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="action"
                      value="sell"
                      checked={manualAction === 'sell'}
                      onChange={(e) => setManualAction(e.target.value as 'buy' | 'sell' | 'hold')}
                      disabled={!session.current_position_json}
                    />
                    <span>賣出 (SELL)</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                    <input
                      type="radio"
                      name="action"
                      value="hold"
                      checked={manualAction === 'hold'}
                      onChange={(e) => setManualAction(e.target.value as 'buy' | 'sell' | 'hold')}
                    />
                    <span>持有 (HOLD)</span>
                  </label>
                </div>
              </div>

              {manualAction !== 'hold' && (
                <div className="form-group" style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                    數量（股）
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
                  決策理由
                </label>
                <textarea
                  className="input"
                  value={manualRationale}
                  onChange={(e) => setManualRationale(e.target.value)}
                  placeholder="說明您的決策依據（可選）"
                  rows={3}
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

              {pendingIdempotencyKey && (
                <div style={{ marginTop: '12px', fontSize: '12px', color: '#666' }}>
                  冪等性金鑰：{pendingIdempotencyKey.substring(0, 20)}...
                </div>
              )}
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
                            {d.decision_detail.quantity && (
                              <div><span style={{ color: '#999' }}>數量:</span> {d.decision_detail.quantity}</div>
                            )}
                            {d.decision_detail.fill && (
                              <>
                                <div><span style={{ color: '#999' }}>成交價:</span> ${d.decision_detail.fill.execution_price?.toFixed(2)}</div>
                                <div><span style={{ color: '#999' }}>成交日期:</span> {d.decision_detail.fill.execution_date}</div>
                                {d.decision_detail.fill.commission !== undefined && (
                                  <div><span style={{ color: '#999' }}>佣金:</span> ${d.decision_detail.fill.commission?.toFixed(4)}</div>
                                )}
                                {d.decision_detail.fill.slippage !== undefined && (
                                  <div><span style={{ color: '#999' }}>滑價:</span> ${d.decision_detail.fill.slippage?.toFixed(4)}</div>
                                )}
                              </>
                            )}
                            {d.decision_detail.rejection && (
                              <div style={{ gridColumn: '1 / -1', padding: '8px', backgroundColor: '#fff3cd', borderRadius: '4px', color: '#856404' }}>
                                <strong>拒絕原因:</strong> {d.decision_detail.rejection.reason}
                                {d.decision_detail.rejection.required && (
                                  <span> | 需要: ${d.decision_detail.rejection.required?.toFixed(2)}, 可用: ${d.decision_detail.rejection.available?.toFixed(2)}</span>
                                )}
                              </div>
                            )}
                            {d.decision_detail.rationale && (
                              <div style={{ gridColumn: '1 / -1' }}><span style={{ color: '#999' }}>決策理由:</span> {d.decision_detail.rationale}</div>
                            )}
                          </div>
                          
                          {d.decision_detail.reflection && (
                            <div style={{ marginTop: '12px', padding: '8px', backgroundColor: '#e7f3ff', borderRadius: '4px', fontSize: '13px' }}>
                              <span style={{ color: '#0070f3', fontWeight: 600 }}>事後反思:</span> {d.decision_detail.reflection}
                            </div>
                          )}
                          
                          {mode === 'manual' && editingReflection !== d.id && (
                            <button
                              className="btn"
                              style={{ marginTop: '8px', fontSize: '12px', padding: '4px 12px' }}
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditingReflection(d.id);
                                setReflectionText(d.decision_detail.reflection || '');
                              }}
                            >
                              {d.decision_detail.reflection ? '編輯反思' : '添加反思'}
                            </button>
                          )}
                          
                          {mode === 'manual' && editingReflection === d.id && (
                            <div style={{ marginTop: '12px' }} onClick={(e) => e.stopPropagation()}>
                              <textarea
                                className="input"
                                value={reflectionText}
                                onChange={(e) => setReflectionText(e.target.value)}
                                placeholder="添加事後反思：當時的判斷是否正確？學到了什麼？"
                                rows={3}
                                style={{ resize: 'vertical', marginBottom: '8px' }}
                              />
                              <div style={{ display: 'flex', gap: '8px' }}>
                                <button
                                  className="btn btn-primary"
                                  style={{ fontSize: '12px', padding: '4px 12px' }}
                                  onClick={() => handleUpdateReflection(d.id)}
                                  disabled={savingReflection}
                                >
                                  {savingReflection ? '儲存中...' : '儲存'}
                                </button>
                                <button
                                  className="btn"
                                  style={{ fontSize: '12px', padding: '4px 12px' }}
                                  onClick={() => {
                                    setEditingReflection(null);
                                    setReflectionText('');
                                  }}
                                >
                                  取消
                                </button>
                              </div>
                            </div>
                          )}
                          
                          <div style={{ marginTop: '8px', fontSize: '12px', color: '#999' }}>
                            現金: ${d.paper_cash.toFixed(2)} | 來源: {d.engine_version} | 資料截止: {d.data_cutoff_at}
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
            {session.status === 'completed' ? 'historical_test_complete' :
             session.status === 'retired' ? 'retired' :
             mode === 'manual' ? 'manual_replay' :
             'historical_replay'}
            {' '}&mdash; 
            {mode === 'manual' 
              ? '此模擬進行人工決策回放，所有操作均由用戶手動執行。'
              : '此模擬使用預先存在的示範數據進行歷史重播，僅供研究用途，不構成投資建議。'}
            {session.provider_id && (
              <span> 資料來源：{session.provider_id}。</span>
            )}
            {' '}資料截止日：{session.data_cutoff_at}。
          </div>
        </>
      )}
    </div>
  );
}
