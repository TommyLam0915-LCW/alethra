'use client';

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

interface Experiment {
  id: string;
  title: string;
  hypothesis: string | null;
  falsification_conditions: string | null;
  status: string;
  created_at: string;
  current_version_id: string | null;
}

interface Run {
  id: string;
  status: string;
  metrics: any;
  warnings: string[] | null;
  config_hash: string;
  dataset_checksum: string | null;
  methodology_json: any;
  started_at: string | null;
  completed_at: string | null;
}

interface TimelineEvent {
  id: string;
  entity_type: string;
  entity_id: string;
  event_type: string;
  actor_type: string;
  payload_json: any;
  recorded_before_outcome: boolean;
  rules_locked: boolean;
  data_cutoff_at: string | null;
  created_at: string;
}

interface Note {
  id: string;
  content: string;
  author_id: string;
  created_at: string;
}

interface Version {
  id: string;
  version_number: number;
  config_hash: string | null;
  change_reason: string | null;
  created_at: string;
  strategy_spec: any;
}

interface DiffEntry {
  field: string;
  old_value: any;
  new_value: any;
}

interface CheckResult {
  rule_id: string;
  rule_version: string;
  status: string;
  evidence: Record<string, any>;
  explanation: string;
  limitations: string[];
}

interface MethodReview {
  rule_set_version: string;
  experiment_id: string;
  version_id: string | null;
  run_id: string | null;
  run_selection: string;
  config_hash_match: boolean | null;
  completed_at_available: boolean | null;
  generated_at: string;
  checks: CheckResult[];
}

const EVENT_LABELS: Record<string, string> = {
  experiment_created: '實驗建立',
  hypothesis_updated: '假設更新',
  falsification_condition_added: '推翻條件更新',
  dataset_selected: '資料集選取',
  strategy_template_selected: '策略模板選取',
  parameter_changed: '參數變更',
  strategy_version_created: '新版本建立',
  research_run_started: '回測開始',
  research_run_completed: '回測完成',
  research_run_failed: '回測失敗',
  warning_generated: '警告產生',
  strategy_locked: '策略鎖定',
  research_note_added: '研究筆記新增',
  export_generated: '匯出產生',
};

export default function ExperimentDetailPage() {
  const router = useRouter();
  const params = useParams();
  const experimentId = params.id as string;

  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [noteText, setNoteText] = useState('');
  const [showTimeline, setShowTimeline] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [diffResult, setDiffResult] = useState<{ diffs: DiffEntry[]; version_a_number: number; version_b_number: number } | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [methodReview, setMethodReview] = useState<MethodReview | null>(null);
  const [methodReviewLoading, setMethodReviewLoading] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadData();
  }, [experimentId, router]);

  const loadData = async () => {
    try {
      const [exp, runsData, versionsData, timelineData, notesData, reviewData] = await Promise.all([
        api.experiments.get(experimentId),
        api.runs.list(experimentId),
        api.experiments.versions(experimentId),
        api.timeline.get(experimentId),
        api.notes.list(experimentId),
        api.experiments.methodReview(experimentId).catch(() => null),
      ]);
      setExperiment(exp);
      setRuns(runsData);
      setVersions(versionsData);
      setTimeline(timelineData);
      setNotes(notesData);
      setMethodReview(reviewData);
    } catch (err) {
      setError('載入失敗');
    } finally {
      setLoading(false);
    }
  };

  const handleRunBacktest = async () => {
    if (!experiment?.current_version_id) {
      setError('沒有可執行的版本');
      return;
    }
    setRunning(true);
    setError('');

    try {
      const run = await api.runs.create(experiment.current_version_id);
      setRuns([run, ...runs]);
      const [timelineData, notesData] = await Promise.all([
        api.timeline.get(experimentId),
        api.notes.list(experimentId),
      ]);
      setTimeline(timelineData);
    } catch (err: any) {
      setError(err.message || '執行失敗');
    } finally {
      setRunning(false);
    }
  };

  const handleAddNote = async () => {
    if (!noteText.trim()) return;
    try {
      const note = await api.notes.create(experimentId, { content: noteText.trim() });
      setNotes([note, ...notes]);
      setNoteText('');
      const timelineData = await api.timeline.get(experimentId);
      setTimeline(timelineData);
    } catch (err: any) {
      setError(err.message || '筆記新增失敗');
    }
  };

  const handleCompare = async (v1Id: string, v2Id: string) => {
    setDiffLoading(true);
    setDiffResult(null);
    try {
      const result = await api.versions.compare(v1Id, v2Id);
      setDiffResult(result);
    } catch (err: any) {
      setError(err.message || '比較失敗');
    } finally {
      setDiffLoading(false);
    }
  };

  const handleExport = async () => {
    try {
      const data = await api.experiments.export(experimentId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `alethra-export-${experimentId.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err.message || '匯出失敗');
    }
  };

  const handleRefreshReview = async () => {
    setMethodReviewLoading(true);
    try {
      const reviewData = await api.experiments.methodReview(experimentId);
      setMethodReview(reviewData);
    } catch (err: any) {
      setError(err.message || '方法審查載入失敗');
    } finally {
      setMethodReviewLoading(false);
    }
  };

  const STATUS_STYLE: Record<string, { bg: string; color: string; label: string }> = {
    PASS: { bg: '#d4edda', color: '#155724', label: '通過' },
    WARNING: { bg: '#fff3cd', color: '#856404', label: '警告' },
    INSUFFICIENT_INFORMATION: { bg: '#e2e3e5', color: '#383d49', label: '資訊不足' },
    NOT_APPLICABLE: { bg: '#f8f9fa', color: '#6c757d', label: '不適用' },
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px' }}>載入中...</div>;
  }

  if (!experiment) {
    return <div className="container" style={{ paddingTop: '40px' }}>實驗不存在</div>;
  }

  const latestRun = runs.find(r => r.status === 'completed');

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href="/dashboard" style={{ color: '#0070f3', textDecoration: 'none' }}>
          ← 返回控制台
        </Link>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '30px' }}>
        <div>
          <h1 style={{ marginBottom: '8px' }}>{experiment.title}</h1>
          <p style={{ color: '#666' }}>
            建立於 {new Date(experiment.created_at).toLocaleDateString('zh-Hant')}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {experiment.status === 'locked' && (
            <Link href={`/experiments/${experimentId}/simulation`}>
              <button className="btn btn-primary" style={{ fontSize: '13px' }}>
                前進模擬
              </button>
            </Link>
          )}
          <button onClick={handleExport} className="btn" style={{ backgroundColor: '#eaeaea', fontSize: '13px' }}>
            匯出
          </button>
          <span
            style={{
              padding: '6px 16px',
              borderRadius: '16px',
              fontSize: '14px',
              backgroundColor: experiment.status === 'locked' ? '#d1ecf1' : '#eaeaea',
              color: experiment.status === 'locked' ? '#0c5460' : '#666',
            }}
          >
            {experiment.status === 'draft' ? '草稿' : experiment.status === 'locked' ? '已鎖定' : experiment.status}
          </span>
        </div>
      </div>

      {error && <div className="alert alert-warning">{error}</div>}

      {experiment.hypothesis && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <h3 style={{ marginBottom: '12px' }}>研究假設</h3>
          <p style={{ color: '#666' }}>{experiment.hypothesis}</p>
          {experiment.falsification_conditions && (
            <>
              <h4 style={{ marginTop: '16px', marginBottom: '8px', fontSize: '14px' }}>推翻條件</h4>
              <p style={{ color: '#666' }}>{experiment.falsification_conditions}</p>
            </>
          )}
        </div>
      )}

      {experiment.current_version_id && versions.length > 0 && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <h3 style={{ margin: 0 }}>目前策略版本</h3>
            <button
              onClick={() => setShowVersions(!showVersions)}
              className="btn"
              style={{ backgroundColor: '#eaeaea', fontSize: '13px' }}
            >
              {showVersions ? '隱藏版本歷史' : `查看所有版本 (${versions.length})`}
            </button>
          </div>
          <p style={{ color: '#666', fontSize: '14px', margin: 0 }}>
            版本 {versions.find(v => v.id === experiment.current_version_id)?.version_number || versions.length}
            {versions.find(v => v.id === experiment.current_version_id)?.change_reason && (
              <> · 變更原因：{versions.find(v => v.id === experiment.current_version_id)?.change_reason}</>
            )}
          </p>
          {showVersions && (
            <div style={{ marginTop: '16px', borderTop: '1px solid #eaeaea', paddingTop: '16px' }}>
              {versions.map((v, i) => (
                <div key={v.id} style={{ padding: '12px', backgroundColor: '#fafafa', borderRadius: '6px', marginBottom: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <strong>版本 {v.version_number}</strong>
                      {v.change_reason && <span style={{ marginLeft: '8px', color: '#666' }}>· {v.change_reason}</span>}
                    </div>
                    <span style={{ fontSize: '12px', color: '#999' }}>
                      {new Date(v.created_at).toLocaleDateString('zh-Hant')}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2 style={{ margin: 0 }}>回測結果</h2>
          <button
            onClick={handleRunBacktest}
            className="btn btn-primary"
            disabled={running || !experiment.current_version_id}
          >
            {running ? brand.ui.experiment.runningZh : brand.ui.experiment.runBacktestZh}
          </button>
        </div>

        {!latestRun ? (
          <div style={{ textAlign: 'center', padding: '40px' }}>
            <p style={{ color: '#666', marginBottom: '12px' }}>
              尚未執行回測
            </p>
            <p style={{ color: '#888', fontSize: '14px' }}>
              點擊上方按鈕開始測試你的策略假設。<br />
              回測使用歷史示範資料，結果僅供研究參考。
            </p>
          </div>
        ) : (
          <div>
            {latestRun.warnings && latestRun.warnings.length > 0 && (
              <div className="alert alert-warning" style={{ marginBottom: '16px' }}>
                <strong>警告：</strong>
                <ul style={{ marginTop: '8px', marginLeft: '20px' }}>
                  {latestRun.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
              {latestRun.metrics?.total_return_pct != null && (
                <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>總報酬</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{latestRun.metrics.total_return_pct.toFixed(2)}%</div>
                </div>
              )}
              {latestRun.metrics?.final_equity != null && (
                <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>最終淨值</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>${latestRun.metrics.final_equity.toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
                </div>
              )}
              {latestRun.metrics?.max_drawdown_pct != null && (
                <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>最大回撤</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{latestRun.metrics.max_drawdown_pct.toFixed(2)}%</div>
                </div>
              )}
              {latestRun.metrics?.total_trades != null && (
                <div style={{ padding: '16px', backgroundColor: '#f5f5f5', borderRadius: '6px' }}>
                  <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>交易次數</div>
                  <div style={{ fontSize: '24px', fontWeight: 'bold' }}>{latestRun.metrics.total_trades}</div>
                </div>
              )}
            </div>

            {latestRun.methodology_json && (
              <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#f0f4f8', borderRadius: '6px', fontSize: '13px' }}>
                <strong>方法摘要：</strong>
                <span style={{ marginLeft: '8px', color: '#555' }}>
                  引擎 v{latestRun.methodology_json.engine_version} |
                  模板: {latestRun.methodology_json.template_type}
                </span>
              </div>
            )}

            {latestRun.config_hash && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: '#999' }}>
                config_hash: {latestRun.config_hash.slice(0, 16)}...
                {latestRun.dataset_checksum && <> | dataset: {latestRun.dataset_checksum.slice(0, 16)}...</>}
              </div>
            )}

            <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#fff3cd', borderRadius: '6px', fontSize: '14px' }}>
              <strong>提醒：</strong>以上結果基於歷史示範資料，不代表未來績效。本系統是研究工具，不構成投資建議。
            </div>
          </div>
        )}
      </div>

      {/* Workflow Continuity */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <h3 style={{ marginBottom: '16px' }}>研究紀錄</h3>
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
          <button
            onClick={() => setShowTimeline(!showTimeline)}
            className="btn"
            style={{ backgroundColor: '#eaeaea', fontSize: '13px' }}
          >
            {showTimeline ? '隱藏時間線' : `查看時間線 (${timeline.length} 事件)`}
          </button>
          <Link href={`/experiments/${experimentId}/simulation`} className="btn" style={{ backgroundColor: '#eaeaea', fontSize: '13px', textDecoration: 'none' }}>
            前進模擬 →
          </Link>
        </div>

        {showTimeline && timeline.length > 0 && (
          <div style={{ marginTop: '16px', borderTop: '1px solid #eaeaea', paddingTop: '16px' }}>
            {timeline.slice(0, 10).map((event) => (
              <div key={event.id} style={{ padding: '10px', backgroundColor: '#fafafa', borderRadius: '6px', marginBottom: '8px', fontSize: '13px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <strong>{EVENT_LABELS[event.event_type] || event.event_type}</strong>
                    {event.payload_json?.summary && (
                      <span style={{ marginLeft: '8px', color: '#666' }}>· {event.payload_json.summary}</span>
                    )}
                  </div>
                  <span style={{ color: '#999', fontSize: '12px' }}>
                    {new Date(event.created_at).toLocaleString('zh-Hant')}
                  </span>
                </div>
              </div>
            ))}
            {timeline.length > 10 && (
              <p style={{ color: '#888', fontSize: '12px', textAlign: 'center', marginTop: '12px' }}>
                顯示最近 10 筆事件，共 {timeline.length} 筆
              </p>
            )}
          </div>
        )}

        {showTimeline && timeline.length === 0 && (
          <div style={{ marginTop: '16px', textAlign: 'center', padding: '20px' }}>
            <p style={{ color: '#888', fontSize: '14px' }}>尚無時間線事件</p>
          </div>
        )}
      </div>

      {/* Method Review */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2>方法審查</h2>
          <button
            onClick={handleRefreshReview}
            className="btn"
            style={{ backgroundColor: '#eaeaea', fontSize: '13px' }}
            disabled={methodReviewLoading}
          >
            {methodReviewLoading ? '重新整理中...' : '重新整理'}
          </button>
        </div>

        {!methodReview ? (
          <p style={{ color: '#666', textAlign: 'center', padding: '20px' }}>
            方法審查暫時無法載入
          </p>
        ) : (
          <div>
            <div style={{ display: 'flex', gap: '16px', fontSize: '12px', color: '#666', marginBottom: '16px', flexWrap: 'wrap' }}>
              <span>規則集 v{methodReview.rule_set_version}</span>
              {methodReview.run_id && (
                <span>已審查執行: {methodReview.run_id.slice(0, 8)}...</span>
              )}
              {methodReview.version_id && (
                <span>版本: {methodReview.version_id.slice(0, 8)}...</span>
              )}
              {!methodReview.run_id && (
                <span style={{ color: '#856404' }}>尚無已完成的回測執行</span>
              )}
              <span>產生於 {new Date(methodReview.generated_at).toLocaleString('zh-Hant')}</span>
            </div>

            <div style={{ padding: '10px 12px', backgroundColor: '#fffbeb', borderRadius: '6px', fontSize: '12px', color: '#856404', marginBottom: '12px', lineHeight: '1.6' }}>
              <strong>資料來源說明：</strong>RC-001 及 RC-002 檢查的是實驗目前的文字內容（假說、原始想法、否決條件）。這些欄位可隨時編輯，因此審查的文字可能與回測執行時不同。這並非歷史文字快照。
            </div>

            {methodReview.completed_at_available === false && (
              <div style={{ padding: '10px 12px', backgroundColor: '#fff3cd', borderRadius: '6px', fontSize: '12px', color: '#856404', marginBottom: '12px', lineHeight: '1.6' }}>
                <strong>時間戳提醒：</strong>所選的回測執行缺少完成時間戳記。執行排序依據 ID 而非時間先後，因此「最新」不代表時間上最近。
              </div>
            )}

            <div style={{ display: 'grid', gap: '12px' }}>
              {methodReview.checks.map((check) => {
                const style = STATUS_STYLE[check.status] || STATUS_STYLE.INSUFFICIENT_INFORMATION;
                return (
                  <div key={check.rule_id} style={{
                    padding: '16px',
                    backgroundColor: '#fafafa',
                    borderRadius: '6px',
                    borderLeft: `4px solid ${style.color}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                      <span style={{ fontWeight: '600', fontSize: '14px' }}>{check.rule_id}</span>
                      <span style={{
                        padding: '2px 10px',
                        borderRadius: '12px',
                        fontSize: '12px',
                        fontWeight: '500',
                        backgroundColor: style.bg,
                        color: style.color,
                      }}>
                        {style.label}
                      </span>
                    </div>
                    <div style={{ fontSize: '13px', color: '#333', whiteSpace: 'pre-line', lineHeight: '1.6' }}>
                      {check.explanation}
                    </div>
                    {check.limitations.length > 0 && (
                      <div style={{ marginTop: '8px', fontSize: '12px', color: '#888' }}>
                        {check.limitations.map((lim, i) => (
                          <div key={i}>• {lim}</div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div style={{ marginTop: '16px', padding: '12px', backgroundColor: '#f0f4f8', borderRadius: '6px', fontSize: '13px', color: '#555' }}>
              <strong>聲明：</strong>方法審查是確定性的規則檢查，不構成投資建議。PASS 不代表策略可獲利或適合實盤交易。本系統使用示範資料，非即時市場資料。
            </div>
          </div>
        )}
      </div>

      {/* Research Notes */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <h3 style={{ marginBottom: '16px' }}>研究筆記</h3>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          <input
            type="text"
            className="input"
            placeholder="記錄你的研究想法..."
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddNote()}
            style={{ flex: 1 }}
          />
          <button onClick={handleAddNote} className="btn btn-primary" disabled={!noteText.trim()}>
            新增
          </button>
        </div>
        {notes.length === 0 ? (
          <p style={{ color: '#999', fontSize: '14px' }}>尚無筆記</p>
        ) : (
          <div style={{ display: 'grid', gap: '8px' }}>
            {notes.map((note) => (
              <div key={note.id} style={{ padding: '12px', backgroundColor: '#f9f9f9', borderRadius: '6px', borderLeft: '3px solid #0070f3' }}>
                <p style={{ fontSize: '14px', marginBottom: '4px' }}>{note.content}</p>
                <p style={{ fontSize: '12px', color: '#999' }}>
                  {new Date(note.created_at).toLocaleString('zh-Hant')}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Version History */}
      {versions.length > 0 && (
        <div className="card" style={{ marginBottom: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <h3>版本歷史 ({versions.length})</h3>
            <button
              onClick={() => setShowVersions(!showVersions)}
              className="btn"
              style={{ backgroundColor: '#eaeaea', fontSize: '13px' }}
            >
              {showVersions ? '收合' : '展開'}
            </button>
          </div>
          {showVersions && (
            <div>
              <div style={{ display: 'grid', gap: '8px', marginBottom: '16px' }}>
                {versions.map((v) => (
                  <div key={v.id} style={{
                    padding: '12px',
                    backgroundColor: v.id === experiment.current_version_id ? '#e8f4fd' : '#f5f5f5',
                    borderRadius: '6px',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}>
                    <div>
                      <div style={{ fontSize: '14px', fontWeight: '500' }}>
                        v{v.version_number}
                        {v.id === experiment.current_version_id && (
                          <span style={{ marginLeft: '8px', fontSize: '12px', color: '#0070f3' }}>(目前)</span>
                        )}
                      </div>
                      <div style={{ fontSize: '12px', color: '#666' }}>
                        {new Date(v.created_at).toLocaleString('zh-Hant')}
                        {v.change_reason && <> — {v.change_reason}</>}
                      </div>
                    </div>
                    <div style={{ fontSize: '11px', color: '#999' }}>
                      {v.config_hash ? v.config_hash.slice(0, 12) + '...' : ''}
                    </div>
                  </div>
                ))}
              </div>
              {versions.length >= 2 && (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <select id="compare-a" className="input" style={{ flex: 1, fontSize: '13px' }} defaultValue={versions[versions.length - 1]?.id}>
                    {versions.map(v => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
                  </select>
                  <span style={{ color: '#999' }}>vs</span>
                  <select id="compare-b" className="input" style={{ flex: 1, fontSize: '13px' }} defaultValue={versions[0]?.id}>
                    {versions.map(v => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
                  </select>
                  <button
                    className="btn btn-primary"
                    style={{ fontSize: '13px' }}
                    onClick={() => {
                      const a = (document.getElementById('compare-a') as HTMLSelectElement)?.value;
                      const b = (document.getElementById('compare-b') as HTMLSelectElement)?.value;
                      if (a && b && a !== b) handleCompare(a, b);
                    }}
                  >
                    比較
                  </button>
                </div>
              )}
              {diffLoading && <p style={{ marginTop: '12px', color: '#666' }}>比較中...</p>}
              {diffResult && (
                <div style={{ marginTop: '12px', padding: '12px', backgroundColor: '#f0f4f8', borderRadius: '6px' }}>
                  <h4 style={{ marginBottom: '8px', fontSize: '14px' }}>
                    v{diffResult.version_a_number} → v{diffResult.version_b_number} 差異
                  </h4>
                  {diffResult.diffs.length === 0 ? (
                    <p style={{ color: '#666', fontSize: '13px' }}>無差異</p>
                  ) : (
                    <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid #ddd' }}>
                          <th style={{ textAlign: 'left', padding: '4px 8px' }}>欄位</th>
                          <th style={{ textAlign: 'left', padding: '4px 8px' }}>舊值</th>
                          <th style={{ textAlign: 'left', padding: '4px 8px' }}>新值</th>
                        </tr>
                      </thead>
                      <tbody>
                        {diffResult.diffs.map((d, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid #eee' }}>
                            <td style={{ padding: '4px 8px', fontWeight: '500' }}>{d.field}</td>
                            <td style={{ padding: '4px 8px', color: '#c00' }}>{JSON.stringify(d.old_value)}</td>
                            <td style={{ padding: '4px 8px', color: '#080' }}>{JSON.stringify(d.new_value)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Research Timeline */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3>研究時間線 ({timeline.length} 事件)</h3>
          <button
            onClick={() => setShowTimeline(!showTimeline)}
            className="btn"
            style={{ backgroundColor: '#eaeaea', fontSize: '13px' }}
          >
            {showTimeline ? '收合' : '展開'}
          </button>
        </div>
        {showTimeline && (
          <div>
            {timeline.length === 0 ? (
              <p style={{ color: '#999', fontSize: '14px' }}>尚無事件</p>
            ) : (
              <div style={{ display: 'grid', gap: '4px' }}>
                {timeline.map((evt) => (
                  <div key={evt.id} style={{
                    padding: '8px 12px',
                    backgroundColor: evt.recorded_before_outcome ? '#f8f9fa' : '#fff8e1',
                    borderRadius: '4px',
                    borderLeft: `3px solid ${evt.actor_type === 'system' ? '#ffc107' : evt.rules_locked ? '#17a2b8' : '#0070f3'}`,
                    fontSize: '13px',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontWeight: '500' }}>
                        {EVENT_LABELS[evt.event_type] || evt.event_type}
                      </span>
                      <span style={{ color: '#999', fontSize: '11px' }}>
                        {new Date(evt.created_at).toLocaleString('zh-Hant')}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: '8px', marginTop: '2px', fontSize: '11px', color: '#888' }}>
                      <span>{evt.entity_type}</span>
                      {evt.actor_type === 'system' && <span style={{ color: '#ffc107' }}>system</span>}
                      {evt.rules_locked && <span style={{ color: '#17a2b8' }}>locked</span>}
                      {!evt.recorded_before_outcome && <span style={{ color: '#e67e22' }}>post-outcome</span>}
                      {evt.data_cutoff_at && <span>cutoff: {evt.data_cutoff_at}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Run History */}
      {runs.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: '16px' }}>執行歷史</h3>
          <div style={{ display: 'grid', gap: '8px' }}>
            {runs.map((run) => (
              <div
                key={run.id}
                style={{
                  padding: '12px',
                  backgroundColor: '#f5f5f5',
                  borderRadius: '6px',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <div>
                  <div style={{ fontSize: '14px', fontWeight: '500' }}>
                    {run.status === 'completed' ? '已完成' : run.status === 'failed' ? '失敗' : '執行中'}
                  </div>
                  <div style={{ fontSize: '12px', color: '#666' }}>
                    {run.started_at && new Date(run.started_at).toLocaleString('zh-Hant')}
                  </div>
                </div>
                {run.metrics?.total_return_pct != null && (
                  <div style={{ fontSize: '14px', fontWeight: 'bold' }}>
                    {run.metrics.total_return_pct.toFixed(2)}%
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
