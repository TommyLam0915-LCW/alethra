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
    setCreating(true);
    setError('');
    try {
      const s = await api.simulations.create(experiment.current_version_id, {
        initial_virtual_cash: cash,
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
            {(session.status === 'pending' || session.status === 'active') && (
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
                            {d.decision_detail.quantity && (
                              <div><span style={{ color: '#999' }}>數量:</span> {d.decision_detail.quantity}</div>
                            )}
                            {d.decision_detail.execution_price && (
                              <div><span style={{ color: '#999' }}>成交價:</span> ${d.decision_detail.execution_price?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.commission !== undefined && (
                              <div><span style={{ color: '#999' }}>佣金:</span> ${d.decision_detail.commission?.toFixed(4)}</div>
                            )}
                            {d.decision_detail.rationale && (
                              <div style={{ gridColumn: '1 / -1' }}><span style={{ color: '#999' }}>原因:</span> {d.decision_detail.rationale}</div>
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
            {session.status === 'completed' ? ' historical_test_complete' :
             session.status === 'retired' ? ' retired' :
             ' historical_replay'}
            {' '}&mdash; 此模擬使用預先存在的示範數據進行歷史重播，僅供研究用途，不構成投資建議。
            資料截止日：{session.data_cutoff_at}。
          </div>
        </>
      )}
    </div>
  );
}
