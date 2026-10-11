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
      const [exp, runsData, versionsData, timelineData, notesData] = await Promise.all([
        api.experiments.get(experimentId),
        api.runs.list(experimentId),
        api.experiments.versions(experimentId),
        api.timeline.get(experimentId),
        api.notes.list(experimentId),
      ]);
      setExperiment(exp);
      setRuns(runsData);
      setVersions(versionsData);
      setTimeline(timelineData);
      setNotes(notesData);
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

      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2>回測結果</h2>
          <button
            onClick={handleRunBacktest}
            className="btn btn-primary"
            disabled={running || !experiment.current_version_id}
          >
            {running ? brand.ui.experiment.runningZh : brand.ui.experiment.runBacktestZh}
          </button>
        </div>

        {!latestRun ? (
          <p style={{ color: '#666', textAlign: 'center', padding: '40px' }}>
            尚未執行回測，點擊上方按鈕開始
          </p>
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
