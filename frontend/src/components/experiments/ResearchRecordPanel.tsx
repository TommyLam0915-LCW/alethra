'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';

interface RecordCoverage {
  versions: number;
  revisions: number;
  runs: number;
  completed_runs: number;
  robustness_studies: number;
  robustness_attempts: number;
  saved_comparisons: number;
  simulation_sessions: number;
  simulation_decisions: number;
  human_decisions: number;
  notes: number;
  events: number;
  excluded: string[];
}

interface RevisionEntry {
  version_id: string;
  version_number: number;
  base_version_id: string | null;
  base_version_number: number | null;
  change_reason: string | null;
  change_kind: string | null;
  changed_fields: string[];
  expectation: string | null;
  outcome: string | null;
  met_expectation: boolean | null;
  outcome_recorded_at: string | null;
}

interface AttemptEntry {
  id: string;
  cell_key: string;
  label: string;
  role: string;
  status: string;
  run_id: string | null;
  error_details: string | null;
}

interface StudyEntry {
  id: string;
  kind: string;
  status: string;
  aggregate: any;
  error_details: string | null;
  attempts: AttemptEntry[];
}

interface SavedComparisonEntry {
  id: string;
  run_a_id: string;
  run_b_id: string;
  run_a_version_number: number | null;
  run_b_version_number: number | null;
  purpose: string;
  notes: string | null;
  updated_at: string | null;
}

interface DecisionEntry {
  id: string;
  step_number: number;
  snapshot_date: string;
  decision_type: string;
  action: string | null;
  rationale: string | null;
  reflection: string | null;
}

interface HumanDecisionEntry {
  id: string;
  action: string;
  quantity: number | null;
  rationale: string | null;
  execution_status: string;
  server_snapshot_date: string;
}

interface SessionEntry {
  id: string;
  version_number: number | null;
  status: string;
  timeframe: string;
  data_cutoff_at: string;
  current_snapshot_date: string | null;
  observed_days: number | null;
  decisions: DecisionEntry[];
  human_decisions: HumanDecisionEntry[];
}

interface ExperimentRecord {
  schema_version: string;
  generated_at: string;
  coverage: RecordCoverage;
  decision_review: {
    revisions: RevisionEntry[];
    robustness_studies: StudyEntry[];
    saved_comparisons: SavedComparisonEntry[];
    simulation_sessions: SessionEntry[];
  };
}

interface Props {
  experimentId: string;
  refreshToken?: number;
}

const KIND_LABEL: Record<string, string> = {
  cost_scenarios: '成本情境',
  period_slices: '期間分段',
  parameter_sensitivity: '參數敏感度',
  walk_forward: '前進式驗證',
};

const STUDY_STATUS_LABEL: Record<string, string> = {
  planned: '已計劃，未執行',
  running: '執行中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失敗',
  cancelled: '已取消',
};

const CHANGE_KIND_LABEL: Record<string, string> = {
  strategy_only: '策略設定變更',
  period_only: '只改研究期間',
  strategy_and_period: '策略設定與期間同時變更',
};

const EXCLUDED_LABEL: Record<string, string> = {
  'runs.result_json.transactions': '逐筆交易明細',
  'runs.result_json.round_trips': '回合交易明細',
  'runs.result_json.daily_equity_curve': '逐日權益曲線',
  'runs.result_json.daily_accounting': '逐日帳務紀錄',
};

function outcomeLabel(entry: RevisionEntry): { text: string; color: string } {
  if (entry.met_expectation === true) return { text: '符合預期', color: 'var(--positive)' };
  if (entry.met_expectation === false) return { text: '未符預期', color: 'var(--warning)' };
  return { text: '未記錄結論', color: 'var(--text-secondary)' };
}

function CountChip({ label, value, testId }: { label: string; value: number; testId: string }) {
  return (
    <div
      data-testid={testId}
      style={{
        padding: '8px 12px',
        backgroundColor: 'var(--surface-2)',
        border: '1px solid var(--border-strong)',
        borderRadius: '6px',
        minWidth: '96px',
      }}
    >
      <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{label}</div>
      <div style={{ fontSize: '18px', fontWeight: 600 }}>{value}</div>
    </div>
  );
}

export default function ResearchRecordPanel({ experimentId, refreshToken = 0 }: Props) {
  const [record, setRecord] = useState<ExperimentRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.experiments.record(experimentId);
        if (!cancelled) {
          setRecord(data);
          setError('');
        }
      } catch (err: any) {
        if (!cancelled) setError(err?.message || '研究紀錄載入失敗');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [experimentId, refreshToken]);

  const handleDownload = async () => {
    setDownloading(true);
    setNotice('');
    setError('');
    try {
      const data = await api.experiments.export(experimentId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `alethra-record-${experimentId.slice(0, 8)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      const sections = data?.decision_review ? Object.keys(data.decision_review).length : 0;
      setNotice(
        `已下載完整研究紀錄（${sections} 個決策復盤區段、${data?.coverage?.events ?? 0} 個事件）。`
      );
    } catch (err: any) {
      setError(err?.message || '匯出失敗');
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <div className="card" style={{ marginBottom: '20px' }} data-testid="record-panel">
        <h3 style={{ marginTop: 0 }}>研究紀錄總覽</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '13px' }}>載入研究紀錄…</p>
      </div>
    );
  }

  if (!record) {
    return (
      <div className="card" style={{ marginBottom: '20px' }} data-testid="record-panel">
        <h3 style={{ marginTop: 0 }}>研究紀錄總覽</h3>
        <p role="alert" data-testid="record-error" style={{ color: 'var(--negative)', fontSize: '13px' }}>
          {error || '研究紀錄載入失敗'}
        </p>
      </div>
    );
  }

  const { coverage, decision_review: review } = record;
  const rationaleDecisions = review.simulation_sessions.flatMap((session) =>
    session.decisions
      .filter((decision) => decision.rationale || decision.reflection)
      .map((decision) => ({ session, decision }))
  );

  return (
    <div className="card" style={{ marginBottom: '20px' }} data-testid="record-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '12px',
          flexWrap: 'wrap',
          marginBottom: '8px',
        }}
      >
        <h3 style={{ margin: 0 }}>研究紀錄總覽</h3>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '13px' }}
          onClick={handleDownload}
          disabled={downloading}
          data-testid="record-download"
        >
          {downloading ? '匯出中…' : '下載完整研究紀錄'}
        </button>
      </div>

      <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: 0 }}>
        這裡整理決策復盤需要的證據：修訂及其結論、每次穩健性嘗試、已儲存比較的理由、模擬決策的理由與反思。
        缺失的區段會以 0 顯示，不會自動補值。
      </p>

      {notice && (
        <p
          data-testid="record-notice"
          style={{ color: 'var(--positive)', fontSize: '13px', margin: '0 0 12px' }}
        >
          {notice}
        </p>
      )}
      {error && (
        <p
          role="alert"
          data-testid="record-error"
          style={{ color: 'var(--negative)', fontSize: '13px', margin: '0 0 12px' }}
        >
          {error}
        </p>
      )}

      <div
        data-testid="record-counts"
        style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}
      >
        <CountChip label="版本" value={coverage.versions} testId="record-count-versions" />
        <CountChip label="修訂" value={coverage.revisions} testId="record-count-revisions" />
        <CountChip label="執行" value={coverage.runs} testId="record-count-runs" />
        <CountChip
          label="穩健性研究"
          value={coverage.robustness_studies}
          testId="record-count-studies"
        />
        <CountChip
          label="穩健性嘗試"
          value={coverage.robustness_attempts}
          testId="record-count-attempts"
        />
        <CountChip
          label="已儲存比較"
          value={coverage.saved_comparisons}
          testId="record-count-comparisons"
        />
        <CountChip
          label="模擬決策"
          value={coverage.simulation_decisions}
          testId="record-count-decisions"
        />
        <CountChip
          label="人工意向"
          value={coverage.human_decisions}
          testId="record-count-human"
        />
        <CountChip label="筆記" value={coverage.notes} testId="record-count-notes" />
        <CountChip label="事件" value={coverage.events} testId="record-count-events" />
      </div>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>修訂結論</h4>
        {review.revisions.length === 0 ? (
          <p data-testid="record-revisions-empty" style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
            尚未建立修訂版本，因此沒有可復盤的變更結論。
          </p>
        ) : (
          <div data-testid="record-revisions" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px' }}>版本</th>
                  <th style={{ padding: '6px 8px' }}>來源</th>
                  <th style={{ padding: '6px 8px' }}>變更</th>
                  <th style={{ padding: '6px 8px' }}>原因</th>
                  <th style={{ padding: '6px 8px' }}>預期</th>
                  <th style={{ padding: '6px 8px' }}>結論</th>
                </tr>
              </thead>
              <tbody>
                {review.revisions.map((entry) => {
                  const label = outcomeLabel(entry);
                  return (
                    <tr
                      key={entry.version_id}
                      data-testid={`record-revision-${entry.version_id}`}
                      style={{ borderBottom: '1px solid var(--surface-2)', verticalAlign: 'top' }}
                    >
                      <td style={{ padding: '6px 8px', fontWeight: 500 }}>
                        v{entry.version_number}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-secondary)' }}>
                        {entry.base_version_number != null ? `v${entry.base_version_number}` : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-secondary)' }}>
                        {entry.change_kind ? CHANGE_KIND_LABEL[entry.change_kind] ?? entry.change_kind : '—'}
                        {entry.changed_fields.length > 0 && (
                          <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                            {entry.changed_fields.join('、')}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-muted)' }}>
                        {entry.change_reason || '—'}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text-muted)' }}>
                        {entry.expectation || '—'}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        <div style={{ color: label.color, fontWeight: 500 }}>{label.text}</div>
                        {entry.outcome && (
                          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{entry.outcome}</div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>穩健性研究</h4>
        {review.robustness_studies.length === 0 ? (
          <p data-testid="record-studies-empty" style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
            尚未執行穩健性研究；單一次回測不能當作穩健性證據。
          </p>
        ) : (
          <div data-testid="record-studies" style={{ display: 'grid', gap: '8px' }}>
            {review.robustness_studies.map((study) => {
              const counts = study.aggregate?.counts ?? {};
              return (
                <div
                  key={study.id}
                  data-testid={`record-study-${study.id}`}
                  style={{
                    padding: '10px 12px',
                    backgroundColor: 'var(--surface-2)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: '6px',
                    fontSize: '13px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
                    <strong>{KIND_LABEL[study.kind] ?? study.kind}</strong>
                    <span style={{ color: 'var(--text-muted)' }}>
                      {STUDY_STATUS_LABEL[study.status] ?? study.status}
                    </span>
                  </div>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '4px' }}>
                    嘗試 {study.attempts.length}（完成 {counts.completed ?? 0}、失敗{' '}
                    {counts.failed ?? 0}、跳過 {counts.skipped ?? 0}）
                    {study.aggregate?.return_stats
                      ? ` · 中位數報酬 ${study.aggregate.return_stats.median}%`
                      : ''}
                  </div>
                  {study.attempts.length > 0 && (
                    <div style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '4px' }}>
                      {study.attempts
                        .map((attempt) => `${attempt.label}（${attempt.status}）`)
                        .join('、')}
                    </div>
                  )}
                  {study.error_details && (
                    <div style={{ color: 'var(--warning)', fontSize: '12px', marginTop: '4px' }}>
                      {study.error_details}
                    </div>
                  )}
                </div>
              );
            })}
            <p style={{ color: 'var(--text-muted)', fontSize: '12px', margin: 0 }}>
              最佳單一嘗試不是穩健性證據：請同時看中位數、最差結果、分散程度與失敗次數。
            </p>
          </div>
        )}
      </section>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>已儲存比較</h4>
        {review.saved_comparisons.length === 0 ? (
          <p
            data-testid="record-comparisons-empty"
            style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}
          >
            尚未儲存任何比較；沒有比較理由，日後難以復盤當時為何作出判斷。
          </p>
        ) : (
          <div data-testid="record-comparisons" style={{ display: 'grid', gap: '8px' }}>
            {review.saved_comparisons.map((entry) => (
              <Link
                key={entry.id}
                href={`/experiments/${experimentId}/runs/compare?saved_comparison_id=${entry.id}`}
                data-testid={`record-comparison-${entry.id}`}
                style={{
                  padding: '10px 12px',
                  backgroundColor: 'var(--surface-2)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  textDecoration: 'none',
                  color: 'inherit',
                  fontSize: '13px',
                }}
              >
                <div style={{ fontWeight: 500 }}>{entry.purpose}</div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '2px' }}>
                  {entry.run_a_version_number != null ? `v${entry.run_a_version_number}` : '—'} →{' '}
                  {entry.run_b_version_number != null ? `v${entry.run_b_version_number}` : '—'}
                  {entry.notes ? ` · ${entry.notes}` : ''}
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section style={{ marginBottom: '16px' }}>
        <h4 style={{ fontSize: '14px', margin: '0 0 8px' }}>模擬決策與反思</h4>
        {review.simulation_sessions.length === 0 ? (
          <p data-testid="record-sessions-empty" style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
            尚未進行前進模擬，因此沒有可復盤的決策紀錄。
          </p>
        ) : (
          <div data-testid="record-sessions" style={{ display: 'grid', gap: '8px' }}>
            {review.simulation_sessions.map((session) => (
              <div
                key={session.id}
                data-testid={`record-session-${session.id}`}
                style={{
                  padding: '10px 12px',
                  backgroundColor: 'var(--surface-2)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  fontSize: '13px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
                  <strong>
                    {session.version_number != null ? `v${session.version_number}` : '—'} ·{' '}
                    {session.timeframe === '1d' ? '日線' : session.timeframe}
                  </strong>
                  <span style={{ color: 'var(--text-muted)' }}>
                    {session.status} · 資料截止 {session.data_cutoff_at}
                  </span>
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '2px' }}>
                  決策 {session.decisions.length}、人工意向 {session.human_decisions.length}
                  {session.current_snapshot_date ? ` · 目前觀察至 ${session.current_snapshot_date}` : ''}
                </div>
                {session.human_decisions.map((human) => (
                  <div
                    key={human.id}
                    data-testid={`record-human-${human.id}`}
                    style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-muted)' }}
                  >
                    人工意向：{human.action}
                    {human.quantity != null ? ` ${human.quantity}` : ''}（{human.execution_status}）
                    {human.rationale ? ` · 理由：${human.rationale}` : ''}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {rationaleDecisions.length > 0 && (
          <div data-testid="record-decision-notes" style={{ marginTop: '8px', display: 'grid', gap: '6px' }}>
            {rationaleDecisions.map(({ decision }) => (
              <div
                key={decision.id}
                data-testid={`record-decision-${decision.id}`}
                style={{
                  padding: '8px 12px',
                  backgroundColor: 'var(--surface-1)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '6px',
                  fontSize: '12px',
                  color: 'var(--text-muted)',
                }}
              >
                <span style={{ fontWeight: 500 }}>
                  {decision.snapshot_date} · {decision.action || decision.decision_type}
                </span>
                {decision.rationale && <span> · 理由：{decision.rationale}</span>}
                {decision.reflection && (
                  <div style={{ color: 'var(--text-secondary)', marginTop: '2px' }}>反思：{decision.reflection}</div>
                )}
              </div>
            ))}
            <p style={{ color: 'var(--text-muted)', fontSize: '11px', margin: 0 }}>
              只列出有記錄理由或反思的決策；完整決策明細在匯出檔內。
            </p>
          </div>
        )}
      </section>

      <div
        data-testid="record-scope"
        style={{
          padding: '10px 12px',
          backgroundColor: 'var(--surface-2)',
          borderRadius: '6px',
          fontSize: '12px',
          color: 'var(--text-muted)',
        }}
      >
        匯出檔（{record.schema_version}）包含研究方法、版本、修訂結論、穩健性研究與嘗試、已儲存比較、模擬決策與事件。
        {coverage.excluded.length > 0 && (
          <>
            {' '}以下內容刻意不含在紀錄內：
            {coverage.excluded
              .map((key) => EXCLUDED_LABEL[key] ?? key)
              .join('、')}
            ；需要時請到單次執行的詳情頁查看。
          </>
        )}
        <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
          這是研究過程的整理，不是投資建議；紀錄不會改寫已發生的資料。
        </div>
      </div>
    </div>
  );
}
