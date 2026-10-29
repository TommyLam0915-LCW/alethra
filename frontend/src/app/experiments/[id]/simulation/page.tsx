'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';

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
  state_version: number;
  provider_id: string | null;
  timeframe: string | null;
  current_bar_timestamp: string | null;
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
  bar_timestamp: string | null;
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

interface LinkedPortfolio {
  id: string;
  status: string;
  simulation_session_id: string | null;
  initial_virtual_cash: number;
  current_virtual_cash: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number | null;
  currency: string;
}

const DECISION_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  no_action: { label: '無操作', color: '#666', bg: '#f5f5f5' },
  simulated_entry: { label: '模擬買入', color: '#155724', bg: '#d4edda' },
  simulated_exit: { label: '模擬賣出', color: '#721c24', bg: '#f8d7da' },
  forced_exit: { label: '強制平倉', color: '#856404', bg: '#fff3cd' },
  blocked_action: { label: '操作受阻', color: '#856404', bg: '#fff3cd' },
};

// A submitted manual order that is queued for the next executable bar is stored
// as blocked_action (native enum reuse) with reason awaiting_execution_window.
// Rendering it as 操作受阻 would report an accepted order as a refusal.
const PENDING_ORDER_STYLE = { label: '掛單待成交', color: '#0056b3', bg: '#e8f1fb' };

function isPendingOrder(d: Decision): boolean {
  return d.decision_detail?.rejection?.reason === 'awaiting_execution_window';
}

function decisionStyle(d: Decision) {
  if (isPendingOrder(d)) return PENDING_ORDER_STYLE;
  return DECISION_LABELS[d.decision_type] || DECISION_LABELS.no_action;
}

const STATUS_LABELS: Record<string, string> = {
  pending: '待開始',
  active: '進行中',
  paused: '已暫停',
  completed: '已完成',
  retired: '已退役',
};

// A session whose timeframe is one of these replays bar-by-bar (intraday); the
// daily path ('1d' or null) keeps the original "advance one day" semantics.
const INTRADAY_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h'];

const TIMEFRAME_LABELS: Record<string, string> = {
  '1m': '1 分鐘',
  '5m': '5 分鐘',
  '15m': '15 分鐘',
  '30m': '30 分鐘',
  '1h': '1 小時',
  '1d': '日線',
};

// bar_timestamp is a naive exchange-local ISO instant (e.g. 2024-03-04T09:50:00).
// Render only the HH:MM time portion; the local date is shown separately.
function formatBarTime(barTimestamp: string | null | undefined): string {
  if (!barTimestamp) return '';
  const m = /T(\d{2}:\d{2})/.exec(barTimestamp);
  return m ? m[1] : barTimestamp;
}

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
  const [pendingDecision, setPendingDecision] = useState<{
    idempotency_key: string;
    action: 'buy' | 'sell' | 'hold';
    quantity?: number;
    rationale?: string;
    reflection?: string;
    expected_version: number;
  } | null>(null);
  const [linkedPortfolio, setLinkedPortfolio] = useState<LinkedPortfolio | null>(null);
  const [linking, setLinking] = useState(false);
  const [priorSessions, setPriorSessions] = useState<SimulationSession[]>([]);
  const [showCreateForm, setShowCreateForm] = useState(false);

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
        loadVersionSessions(exp.current_version_id);
      }
    } catch (err: any) {
      setError(err.message || '載入失敗');
    } finally {
      setLoading(false);
    }
  };

  const loadLinkedPortfolio = async (sessionId: string) => {
    try {
      const portfolios: LinkedPortfolio[] = await api.paperPortfolio.list();
      setLinkedPortfolio(
        (portfolios || []).find((p) => p.simulation_session_id === sessionId) || null
      );
    } catch {
      setLinkedPortfolio(null);
    }
  };

  const loadVersionSessions = async (versionId: string) => {
    try {
      const list: SimulationSession[] = (await api.simulations.listForVersion(versionId)) || [];
      setPriorSessions(list);
      const resumable = list.find((s) =>
        ['pending', 'active', 'paused'].includes(s.status)
      );
      if (!resumable) return;
      setSession(resumable);
      setDecisions((await api.simulations.decisions(resumable.id)) || []);
      loadLinkedPortfolio(resumable.id);
    } catch (err: any) {
      setError(err.message || '載入模擬紀錄失敗');
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
      setLinkedPortfolio(null);
      setPriorSessions((prev) => [s, ...prev.filter((p) => p.id !== s.id)]);
      setShowCreateForm(false);
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
      loadLinkedPortfolio(session.id);
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

  const handleLinkPortfolio = async () => {
    if (!session?.id) return;
    setLinking(true);
    setError('');
    try {
      const created: LinkedPortfolio = await api.paperPortfolio.create({
        initial_virtual_cash: session.initial_virtual_cash,
        simulation_session_id: session.id,
      });
      setLinkedPortfolio(created);
    } catch (err: any) {
      setError(err.message || '建立紙上帳戶失敗');
    } finally {
      setLinking(false);
    }
  };

  const handlePortfolioStatus = async (action: 'pause' | 'resume') => {
    if (!linkedPortfolio || !session?.id) return;
    setLinking(true);
    setError('');
    try {
      await api.paperPortfolio[action](linkedPortfolio.id);
      loadLinkedPortfolio(session.id);
    } catch (err: any) {
      setError(err.message || '更新紙上帳戶狀態失敗');
    } finally {
      setLinking(false);
    }
  };

  const handleReopenSession = async (target: SimulationSession) => {
    setError('');
    setShowCreateForm(false);
    try {
      const [s, d] = await Promise.all([
        api.simulations.get(target.id),
        api.simulations.decisions(target.id),
      ]);
      setSession(s);
      setDecisions(d || []);
      loadLinkedPortfolio(s.id);
    } catch (err: any) {
      setError(err.message || '開啟模擬失敗');
    }
  };

  const handleManualDecision = async () => {
    if (!session?.id) return;
    setSubmitting(true);
    setError('');

    let payload: {
      idempotency_key: string;
      action: 'buy' | 'sell' | 'hold';
      quantity?: number;
      rationale?: string;
      reflection?: string;
      expected_version: number;
    };

    if (pendingDecision) {
      payload = pendingDecision;
    } else {
      const key = `manual-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      const p: typeof payload = {
        action: manualAction,
        idempotency_key: key,
        expected_version: session.state_version,
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
        p.quantity = qty;
      }
      payload = p;
      setPendingDecision(payload);
    }

    try {
      const result = await api.simulations.manualDecision(session.id, payload);
      setSession(result.session);
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      setPendingDecision(null);
      setManualRationale('');
      setManualReflection('');
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.message || '';
        if (detail.includes('State version conflict')) {
          // Stale-version conflict: reload state, preserve form, require explicit new submit
          setPendingDecision(null);
          try {
            const [s, d] = await Promise.all([
              api.simulations.get(session.id),
              api.simulations.decisions(session.id),
            ]);
            setSession(s);
            setDecisions(d);
          } catch { /* refresh best-effort */ }
          setError('狀態版本衝突：其他操作已變更狀態，請檢查後重新提交。');
        } else if (detail.includes('Idempotency key conflict') && detail.includes('different payload')) {
          // Payload conflict: same key but different payload - show specific error
          setPendingDecision(null);
          setError('冪等鍵衝突：相同 key 但 payload 不同。請使用新嘅決策或取消待重試。');
        } else {
          // Other 409: clear pending, show generic
          setPendingDecision(null);
          setError(`衝突（409）：${detail}`);
        }
      } else {
        // Network failure or other error: preserve pendingDecision for retry
        setError(err.message || '決策提交失敗（可點擊「重試」使用相同 payload 再送）');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetryDecision = async () => {
    if (!pendingDecision) return;
    await handleManualDecision();
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

  const isIntraday = !!session?.timeframe && INTRADAY_TIMEFRAMES.includes(session.timeframe);
  const tfLabel = session?.timeframe ? (TIMEFRAME_LABELS[session.timeframe] || session.timeframe) : '';

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

      {(!session || showCreateForm) && experiment?.status === 'locked' && (
        <div className="card" style={{ marginBottom: '24px' }} data-testid="sim-create-card">
          <h3 style={{ marginBottom: '16px' }}>
            {session ? '建立新的模擬工作階段' : '建立模擬工作階段'}
          </h3>
          {session && (
            <p style={{ fontSize: '13px', color: '#666', marginBottom: '12px' }}>
              新工作階段會由鎖定規格重新開始；現有重播紀錄不受影響。
            </p>
          )}
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
            data-testid="sim-create-btn"
            onClick={handleCreate}
            disabled={creating || !selectedProvider || availableProviders.length === 0}
            style={{ marginTop: '12px' }}
          >
            {creating ? '建立中...' : '開始模擬'}
          </button>
          {session && (
            <button
              className="btn"
              data-testid="sim-create-cancel"
              onClick={() => setShowCreateForm(false)}
              style={{ marginTop: '12px', marginLeft: '12px' }}
            >
              取消
            </button>
          )}
        </div>
      )}

      {session && (
        <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '12px' }}>
            <button
              className="btn btn-secondary"
              data-testid="sim-new-session"
              onClick={() => {
                setError('');
                setShowCreateForm(true);
              }}
            >
              建立新模擬工作階段
            </button>
          </div>
          {isIntraday && (
            <div
              data-testid="sim-intraday-badge"
              className="alert"
              style={{
                marginBottom: '16px',
                background: '#fde8ef',
                border: '1px solid #f5a3c0',
                color: '#7a1f45',
                fontSize: '13px',
                lineHeight: 1.6,
              }}
            >
              <strong>合成資料 · 歷史回放 · 非即時行情</strong>
              <span style={{ marginLeft: '8px' }}>
                時間框架：<span data-testid="sim-intraday-timeframe">{tfLabel} K 線</span>
                {session.provider_id && (
                  <> · 資料來源：<span data-testid="sim-intraday-provider">{session.provider_id}</span></>
                )}
                {session.current_bar_timestamp && (
                  <>
                    {' '}· 目前回放至：
                    <span data-testid="sim-current-bar">
                      {session.current_bar_timestamp.slice(0, 10)} {formatBarTime(session.current_bar_timestamp)}（交易所當地時間）
                    </span>
                  </>
                )}
              </span>
              <div style={{ marginTop: '4px', color: '#8a4a63' }}>
                每次「推進」只釋放下一根已完成的 K 線；訊號於完成 bar 收盤形成，於下一根 bar 開盤成交。固定合成資料的獲利不代表策略真實有效。
              </div>
            </div>
          )}
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
            <div className="card" style={{ padding: '16px' }} data-testid="sim-paper-equity">
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>虛擬淨值</div>
              {session.paper_equity === null ? (
                <>
                  <div style={{ fontSize: '20px', fontWeight: 700 }}>—</div>
                  <div style={{ fontSize: '12px', color: '#666' }}>未連結紙上帳戶</div>
                </>
              ) : (
                <>
                  <div style={{ fontSize: '20px', fontWeight: 700 }}>
                    ${session.paper_equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                  <div style={{ fontSize: '12px', color: session.paper_equity >= session.initial_virtual_cash ? '#155724' : '#721c24' }}>
                    {session.paper_equity >= session.initial_virtual_cash ? '+' : ''}
                    {((session.paper_equity - session.initial_virtual_cash) / session.initial_virtual_cash * 100).toFixed(2)}%
                  </div>
                </>
              )}
            </div>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>
                {isIntraday ? '觀察根數（K 線）' : '觀察天數'}
              </div>
              <div data-testid="sim-observed-count" style={{ fontSize: '20px', fontWeight: 700 }}>{session.observed_days}</div>
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
                  data-testid="sim-advance-btn"
                  onClick={handleAdvance}
                  disabled={advancing || manualMode}
                >
                  {advancing ? '推進中...' : isIntraday ? `推進下一根 ${tfLabel} K 線` : '自動推進一日'}
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

          <div
            data-testid="sim-linked-portfolio"
            className="card"
            style={{ marginBottom: '24px', borderLeft: '4px solid #6f42c1' }}
          >
            <h3 style={{ marginBottom: '8px' }}>連結的紙上帳戶</h3>
            {!linkedPortfolio ? (
              <>
                <p style={{ fontSize: '13px', color: '#666', marginBottom: '12px' }}>
                  尚未連結紙上帳戶。連結後，鎖定策略觸發的進出場會自動寫入帳戶的委託、成交、部位、現金與決策日誌；
                  未連結時，此重播只記錄研究日誌，不會產生任何帳戶資料。
                </p>
                <button
                  data-testid="sim-link-portfolio-btn"
                  className="btn btn-primary"
                  onClick={handleLinkPortfolio}
                  disabled={linking}
                >
                  {linking ? '建立中...' : `建立並連結紙上帳戶（初始資金 $${session.initial_virtual_cash.toLocaleString()}）`}
                </button>
              </>
            ) : (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px', marginBottom: '12px' }}>
                  <div>
                    <span style={{ fontSize: '12px', color: '#666' }}>帳戶狀態</span>
                    <div data-testid="sim-portfolio-status" style={{ fontWeight: 600 }}>
                      {STATUS_LABELS[linkedPortfolio.status] || linkedPortfolio.status}
                    </div>
                  </div>
                  <div>
                    <span style={{ fontSize: '12px', color: '#666' }}>可用虛擬現金</span>
                    <div data-testid="sim-portfolio-cash" style={{ fontWeight: 600 }}>
                      ${linkedPortfolio.current_virtual_cash.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                  <div>
                    <span style={{ fontSize: '12px', color: '#666' }}>已實現損益</span>
                    <div data-testid="sim-portfolio-realized" style={{
                      fontWeight: 600,
                      color: linkedPortfolio.total_realized_pnl >= 0 ? '#155724' : '#721c24',
                    }}>
                      ${linkedPortfolio.total_realized_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                  <div>
                    <span style={{ fontSize: '12px', color: '#666' }}>未實現損益</span>
                    <div data-testid="sim-portfolio-unrealized" style={{ fontWeight: 600, color: '#666' }}>
                      {linkedPortfolio.total_unrealized_pnl === null
                        ? '無法估值'
                        : `$${linkedPortfolio.total_unrealized_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                  <Link
                    data-testid="sim-portfolio-link"
                    href={`/paper-portfolio/${linkedPortfolio.id}`}
                    className="btn"
                    style={{ backgroundColor: '#eaeaea', textDecoration: 'none' }}
                  >
                    開啟帳戶（委託／成交／日誌）
                  </Link>
                  {linkedPortfolio.status === 'active' && (
                    <button
                      data-testid="sim-portfolio-pause"
                      className="btn"
                      style={{ backgroundColor: '#fff3cd', color: '#856404' }}
                      onClick={() => handlePortfolioStatus('pause')}
                      disabled={linking}
                    >
                      暫停帳戶
                    </button>
                  )}
                  {linkedPortfolio.status === 'paused' && (
                    <button
                      data-testid="sim-portfolio-resume"
                      className="btn btn-primary"
                      onClick={() => handlePortfolioStatus('resume')}
                      disabled={linking}
                    >
                      繼續帳戶
                    </button>
                  )}
                </div>
                <p style={{ fontSize: '12px', color: '#666', marginTop: '12px' }}>
                  帳戶暫停時，規則觸發的<strong>新進場</strong>會被記錄為受阻且不會成交；停損／停利等風險出場仍會執行。
                  帳戶退役後不再接受任何規則下單。手動模式提交的買賣會在這個帳戶建立掛單，於綁定數據集下一根可執行的 K 線成交；
                  HOLD 只寫入重播日誌，不動帳戶。
                </p>
              </>
            )}
          </div>

          {priorSessions.length > 1 && (
            <div className="card" style={{ marginBottom: '24px' }}>
              <h3 style={{ marginBottom: '8px' }}>此版本的重播紀錄</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {priorSessions.map((s) => (
                  <button
                    key={s.id}
                    data-testid={`sim-session-${s.status}`}
                    className="btn"
                    style={{
                      justifyContent: 'flex-start',
                      backgroundColor: s.id === session?.id ? '#e7f0fe' : 'transparent',
                      border: '1px solid #e0e0e0',
                      fontSize: '13px',
                    }}
                    onClick={() => handleReopenSession(s)}
                    disabled={s.id === session?.id}
                  >
                    {new Date(s.created_at).toLocaleString('zh-Hant')} — {STATUS_LABELS[s.status] || s.status}
                    {s.current_snapshot_date ? `（觀察日 ${s.current_snapshot_date}）` : '（尚未推進）'}
                  </button>
                ))}
              </div>
            </div>
          )}

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
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button
                  className="btn btn-primary"
                  onClick={handleManualDecision}
                  disabled={submitting || !!pendingDecision}
                >
                  {submitting ? '提交中...' : '提交決策'}
                </button>
                {pendingDecision && (
                  <button
                    className="btn"
                    style={{ backgroundColor: '#ffc107', color: '#000' }}
                    onClick={handleRetryDecision}
                    disabled={submitting}
                  >
                    {submitting ? '重試中...' : '重試（相同 payload）'}
                  </button>
                )}
                {pendingDecision && (
                  <button
                    className="btn"
                    style={{ backgroundColor: '#eaeaea', color: '#000' }}
                    onClick={() => {
                      if (confirm('放棄重試唔等於取消後端交易。如果決策已提交成功，佢仍然有效。確定要放棄重試並重新整理狀態？')) {
                        setPendingDecision(null);
                        setError('');
                        // Refresh state to reconcile
                        loadSession();
                      }
                    }}
                    disabled={submitting}
                  >
                    放棄重試
                  </button>
                )}
              </div>
              {pendingDecision && (
                <div className="alert" style={{ marginTop: '12px', fontSize: '12px', background: '#fff3cd', border: '1px solid #ffc107' }}>
                  <strong>待重試決策：</strong>
                  idempotency_key=<code>{pendingDecision.idempotency_key}</code>,
                  action={pendingDecision.action},
                  expected_version={pendingDecision.expected_version}
                  {pendingDecision.quantity != null && `, quantity=${pendingDecision.quantity}`}
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
              <p style={{ color: '#999' }}>
                尚無決策紀錄。點擊「{isIntraday ? `推進下一根 ${tfLabel} K 線` : '自動推進一日'}」開始模擬。
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {decisions.map((d) => {
                  const style = decisionStyle(d);
                  const isExpanded = showDetail === d.step_number;
                  return (
                    <div
                      key={d.id}
                      style={{
                        border: '1px solid var(--border-strong)',
                        borderLeft: `4px solid ${style.color}`,
                        borderRadius: '6px',
                        padding: '12px 16px',
                        backgroundColor: isExpanded ? 'var(--surface-2)' : 'var(--surface-1)',
                        cursor: 'pointer',
                      }}
                      onClick={() => setShowDetail(isExpanded ? null : d.step_number)}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                          <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '24px' }}>
                            #{d.step_number}
                          </span>
                          <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                            {d.snapshot_date}
                          </span>
                          {d.bar_timestamp && (
                            <span
                              data-testid="sim-decision-bar-ts"
                              title="交易所當地時間的 K 線時點"
                              style={{
                                fontSize: '12px',
                                fontWeight: 600,
                                color: '#7a1f45',
                                background: '#fde8ef',
                                border: '1px solid #f5a3c0',
                                borderRadius: '10px',
                                padding: '1px 8px',
                              }}
                            >
                              {formatBarTime(d.bar_timestamp)}
                            </span>
                          )}
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
                        <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-strong)' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '8px', fontSize: '13px' }}>
                            {d.decision_detail.symbol && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>標的:</span> {d.decision_detail.symbol}</div>
                            )}
                            {d.decision_detail.action && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>操作:</span> {d.decision_detail.action.toUpperCase()}</div>
                            )}
                            {d.decision_detail.fill?.quantity && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>數量:</span> {d.decision_detail.fill.quantity}</div>
                            )}
                            {d.decision_detail.fill?.observed_date && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>觀察日:</span> {d.decision_detail.fill.observed_date}</div>
                            )}
                            {d.decision_detail.fill?.observed_close && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>觀察收盤價:</span> ${d.decision_detail.fill.observed_close?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.execution_date && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>成交日:</span> {d.decision_detail.fill.execution_date}</div>
                            )}
                            {d.decision_detail.fill?.price && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>成交價:</span> ${d.decision_detail.fill.price?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.execution_rule && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>執行規則:</span> {d.decision_detail.fill.execution_rule}</div>
                            )}
                            {d.decision_detail.fill?.commission !== undefined && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>佣金:</span> ${d.decision_detail.fill.commission?.toFixed(4)}</div>
                            )}
                            {d.decision_detail.fill?.slippage !== undefined && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>滑價:</span> ${d.decision_detail.fill.slippage?.toFixed(4)}</div>
                            )}
                            {d.decision_detail.fill?.total_cost && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>總成本:</span> ${d.decision_detail.fill.total_cost?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.net_proceeds && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>淨收入:</span> ${d.decision_detail.fill.net_proceeds?.toFixed(2)}</div>
                            )}
                            {(d.decision_detail.fill?.gross_pnl !== undefined || d.decision_detail.fill?.realized_pnl !== undefined) && (
                              <div>
                                <span style={{ color: 'var(--text-secondary)' }}>價差損益:</span> 
                                <span style={{ 
                                  color: (d.decision_detail.fill.gross_pnl ?? d.decision_detail.fill.realized_pnl) >= 0 ? 'var(--positive)' : 'var(--negative)', 
                                  fontWeight: 600 
                                }}>
                                  {' '}${(d.decision_detail.fill.gross_pnl ?? d.decision_detail.fill.realized_pnl)?.toFixed(2)}
                                </span>
                              </div>
                            )}
                            {d.decision_detail.rejection && (
                              <div
                                data-testid="sim-decision-rejection"
                                style={{
                                  gridColumn: '1 / -1',
                                  padding: '8px',
                                  borderRadius: '4px',
                                  backgroundColor: isPendingOrder(d) ? PENDING_ORDER_STYLE.bg : '#fff3cd',
                                  color: isPendingOrder(d) ? '#0b3d75' : '#5c4500',
                                }}
                              >
                                <span style={{ color: isPendingOrder(d) ? PENDING_ORDER_STYLE.color : '#856404', fontWeight: 600 }}>
                                  {isPendingOrder(d) ? '待成交:' : '拒絕:'}
                                </span>{' '}
                                {d.decision_detail.rejection.message || d.decision_detail.rejection.reason}
                                {d.decision_detail.rejection.message && (
                                  <span style={{ color: '#5a6b7a', fontSize: '11px', fontFamily: 'monospace' }}>
                                    {' '}({d.decision_detail.rejection.reason})
                                  </span>
                                )}
                                {d.decision_detail.rejection.required && (
                                  <span> (需要: ${d.decision_detail.rejection.required.toFixed(2)}, 可用: ${d.decision_detail.rejection.available.toFixed(2)})</span>
                                )}
                              </div>
                            )}
                            {d.decision_detail.rationale && (
                              <div style={{ gridColumn: '1 / -1' }}><span style={{ color: 'var(--text-secondary)' }}>決策理由:</span> {d.decision_detail.rationale}</div>
                            )}
                            {d.decision_detail.reflection && (
                              <div style={{ gridColumn: '1 / -1' }}>
                                <span style={{ color: 'var(--text-secondary)' }}>事後反思:</span> {d.decision_detail.reflection}
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
                          <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                            現金: ${d.paper_cash.toFixed(2)} | 引擎: {d.engine_version} | 資料截止: {d.data_cutoff_at}
                          </div>
                        </div>
                      )}

                      {d.warnings && d.warnings.length > 0 && (
                        <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--warning)' }}>
                          {d.warnings.join('; ')}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div style={{ marginTop: '24px', padding: '16px', backgroundColor: 'var(--surface-1)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            <strong>方法論狀態：</strong>
            {manualMode ? 'manual_replay' :
             session.status === 'completed' ? 'historical_test_complete' :
             session.status === 'retired' ? 'retired' :
             'historical_replay'}
            {' '}&mdash; 
            {manualMode ? '人工程回放模式，每步由使用者決定買入／賣出／持有。' :
             '自動策略回放模式，由引擎根據策略規則自動決策。'}
            {' '}資料來源：{isIntraday
              ? `綁定的合成／歷史 ${tfLabel} K 線資料集（歷史回放，非即時行情；零即時 API 請求）`
              : '保存的歷史數據（零即時API請求）'}。
            資料截止日：{session.data_cutoff_at}。
            <br />
            <strong>執行時序：</strong>
            {isIntraday
              ? `訊號於已完成 ${tfLabel} K 線收盤時形成，成交於下一根 K 線開盤（交易所當地時間；買入與賣出均採用相同規則）。`
              : '決策於觀察日收盤後做出，成交於下一可用交易日開盤（買入與賣出均採用相同規則）。'}
          </div>
        </>
      )}
    </div>
  );
}
