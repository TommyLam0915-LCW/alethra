'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, ApiError, formatErrorDetail } from '@/lib/api';
import { createRequestGuard } from '@/lib/latestRequest';
import { formatDateTime, formatParamMap, formatNumber, formatPercentFraction, formatPercentPoints } from '@/lib/format';
import { statusPillClass } from '@/lib/status-styles';
import { renderStoredFailure } from '@/lib/mlFailureMessages';
import {
  decisionFor,
  evidenceStatusLabel,
  evidenceStatusOf,
  jobResultSummaryOf,
  learningStatusLabel,
  refusalText,
  validationErrorOf,
  validationMetricsOf,
  validationPassedOf,
  type LearningJobRow,
  type LearningProposalRow,
} from '@/lib/learningProposal';

const PROPOSAL_STATUS_FILTERS = ['', 'pending', 'validated', 'deployed', 'rejected'];

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const keyed = refusalText(err.status, err.detail);
    if (keyed) return keyed;
    if (err.message) return err.message;
  }
  return err instanceof Error && err.message ? err.message : fallback;
}

function metricValue(key: string, value: number | null): string {
  if (value === null) return '—';
  if (key === 'sharpe') return formatNumber(value, 2);
  if (key === 'max_dd') return formatPercentPoints(value, 1);
  return formatPercentFraction(value, 1);
}

export default function LearningPage() {
  const router = useRouter();
  const [proposals, setProposals] = useState<LearningProposalRow[]>([]);
  const [jobs, setJobs] = useState<LearningJobRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);
  const proposalsGuard = useRef(createRequestGuard()).current;
  const jobsGuard = useRef(createRequestGuard()).current;

  const reload = useCallback(async () => {
    const proposalsToken = proposalsGuard.begin();
    const jobsToken = jobsGuard.begin();
    setLoading(true);
    const [proposalsResult, jobsResult] = await Promise.allSettled([
      api.learning.proposals(50, statusFilter || undefined),
      api.learning.jobs(20),
    ]);
    let failure: string | null = null;
    if (proposalsResult.status === 'fulfilled' && proposalsGuard.isCurrent(proposalsToken)) {
      const payload = proposalsResult.value as { proposals?: LearningProposalRow[] };
      setProposals(Array.isArray(payload?.proposals) ? payload.proposals : []);
    } else if (proposalsResult.status === 'rejected' && proposalsGuard.isCurrent(proposalsToken)) {
      failure = describeError(proposalsResult.reason, '無法載入提案。');
    }
    if (jobsResult.status === 'fulfilled' && jobsGuard.isCurrent(jobsToken)) {
      const payload = jobsResult.value as { jobs?: LearningJobRow[] };
      setJobs(Array.isArray(payload?.jobs) ? payload.jobs : []);
    } else if (jobsResult.status === 'rejected' && jobsGuard.isCurrent(jobsToken)) {
      failure = failure ?? describeError(jobsResult.reason, '無法載入工作記錄。');
    }
    if (proposalsGuard.isCurrent(proposalsToken)) {
      setLoadError(failure);
      setLoading(false);
    }
  }, [proposalsGuard, jobsGuard, statusFilter]);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    void reload();
  }, [router, reload]);

  const applyReturnedProposal = (returned: LearningProposalRow | undefined) => {
    // The endpoints answer with the row they re-read after commit, so the row
    // this replaces is the server's own copy — not a client-side guess about
    // what approve or deploy did.
    if (!returned || typeof returned.id !== 'string') return;
    setProposals((prev) => prev.map((row) => (row.id === returned.id ? returned : row)));
  };

  const handleDecision = async (proposal: LearningProposalRow, kind: 'approve' | 'deploy') => {
    setBusyId(proposal.id);
    setActionError(null);
    setNotice(null);
    try {
      const payload =
        kind === 'approve'
          ? await api.learning.approveProposal(proposal.id)
          : await api.learning.deployProposal(proposal.id);
      const result = payload as { proposal?: LearningProposalRow; message?: string };
      applyReturnedProposal(result?.proposal);
      const label = kind === 'approve' ? '已批准' : '已部署';
      setNotice(
        `${label}（提案 ${proposal.id}）。伺服器回應：${
          typeof result?.message === 'string' ? result.message : '（沒有訊息欄位）'
        }`,
      );
      await reload();
    } catch (err) {
      setActionError(describeError(err, '動作被拒絕。'));
      // A refusal can mean another tab already moved this row, so re-read
      // instead of leaving a stale affordance on screen.
      await reload();
    } finally {
      setBusyId(null);
    }
  };

  const handleTrigger = async (jobType: 'scheduled_study' | 'pattern_analysis') => {
    setTriggering(jobType);
    setActionError(null);
    setNotice(null);
    try {
      const payload = await api.learning.triggerJob({ job_type: jobType });
      const result = payload as { job?: LearningJobRow; message?: string };
      const jobId = result?.job?.id;
      setNotice(
        `已建立 ${jobType} 工作記錄${jobId ? ` ${jobId}` : '（未回報 ID）'}。伺服器訊息：${
          typeof result?.message === 'string' ? result.message : '（沒有訊息欄位）'
        }`,
      );
      await reload();
    } catch (err) {
      setActionError(
        err instanceof ApiError ? formatErrorDetail(err.detail) : '無法建立工作記錄。',
      );
    } finally {
      setTriggering(null);
    }
  };

  return (
    <div style={{ padding: '20px', maxWidth: '1180px', margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          gap: '12px',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h1 style={{ fontSize: '20px', margin: '0 0 4px' }}>學習提案</h1>
          <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
            排程研究與樣板分析的結果在這裡等待決定。批准與部署是兩次獨立的動作，
            頁面不會自動執行其中之一。
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <label style={{ fontSize: '12px', color: 'var(--text-muted)' }} htmlFor="proposal-status">
            狀態篩選
          </label>
          <select
            id="proposal-status"
            data-testid="learning-status-filter"
            className="input"
            style={{ minWidth: '120px', fontSize: '12px' }}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {PROPOSAL_STATUS_FILTERS.map((value) => (
              <option key={value || 'all'} value={value}>
                {value === '' ? '全部' : learningStatusLabel(value)}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-secondary"
            data-testid="learning-refresh"
            onClick={() => void reload()}
          >
            重新整理
          </button>
        </div>
      </div>

      {loadError && (
        <p role="alert" data-testid="learning-load-error" style={{ color: 'var(--danger)', fontSize: '12px' }}>
          {loadError}
        </p>
      )}
      {notice && (
        <p data-testid="learning-notice" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
          {notice}
        </p>
      )}
      {actionError && (
        <p role="alert" data-testid="learning-action-error" style={{ color: 'var(--danger)', fontSize: '12px' }}>
          {actionError}
        </p>
      )}

      <section className="card" aria-label="學習提案清單" style={{ marginTop: '16px' }}>
        <h2 style={{ fontSize: '15px', margin: '0 0 8px' }}>提案（{proposals.length}）</h2>
        {loading ? (
          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>載入中…</p>
        ) : proposals.length === 0 ? (
          <p data-testid="learning-proposals-empty" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            目前沒有符合篩選條件的提案。提案只能由排程工作建立：
            每個出場設定的成交筆數未達 30 筆時，分析不會提出任何參數調整
            （services/pattern_analysis.py:333-334 直接回傳 None）。
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border-subtle)' }}>
                  <th style={{ padding: '6px 8px' }}>建立時間</th>
                  <th style={{ padding: '6px 8px' }}>標的／週期</th>
                  <th style={{ padding: '6px 8px' }}>規則／出場</th>
                  <th style={{ padding: '6px 8px' }}>參數變動</th>
                  <th style={{ padding: '6px 8px' }}>證據狀態</th>
                  <th style={{ padding: '6px 8px' }}>驗證結果</th>
                  <th style={{ padding: '6px 8px' }}>狀態</th>
                  <th style={{ padding: '6px 8px' }}>動作</th>
                </tr>
              </thead>
              <tbody>
                {proposals.map((proposal) => {
                  const decision = decisionFor(proposal);
                  const metrics = validationMetricsOf(proposal);
                  const error = validationErrorOf(proposal);
                  const rationale =
                    proposal.rationale_json && typeof proposal.rationale_json.rationale === 'string'
                      ? proposal.rationale_json.rationale
                      : '';
                  const isBusy = busyId === proposal.id;
                  return (
                    <tr
                      key={proposal.id}
                      data-testid="learning-proposal-row"
                      data-proposal-id={proposal.id}
                      data-status={proposal.status}
                      style={{ borderBottom: '1px solid var(--border-subtle)', verticalAlign: 'top' }}
                    >
                      <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                        {formatDateTime(proposal.created_at)}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        {proposal.instrument_id}
                        <br />
                        <span style={{ color: 'var(--text-muted)' }}>
                          {proposal.interval_minutes} 分鐘
                        </span>
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        {proposal.rule_id}
                        <br />
                        <span style={{ color: 'var(--text-muted)' }}>{proposal.exit_id}</span>
                      </td>
                      <td style={{ padding: '6px 8px', maxWidth: '260px' }}>
                        <div>基準：{formatParamMap(proposal.baseline_exit_config)}</div>
                        <div data-testid="learning-proposal-proposed">
                          提案：{formatParamMap(proposal.proposed_exit_config)}
                        </div>
                        {rationale && (
                          <div data-testid="learning-proposal-rationale" style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
                            {rationale}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        {(() => {
                          const evidence = evidenceStatusOf(proposal);
                          return (
                            <>
                              <span data-testid="learning-proposal-evidence">{evidence}</span>
                              <br />
                              <span style={{ color: 'var(--text-muted)' }}>
                                {evidenceStatusLabel(evidence)}
                              </span>
                            </>
                          );
                        })()}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        {proposal.validation_result === null || proposal.validation_result === undefined ? (
                          <span data-testid="learning-validation-none">尚未驗證</span>
                        ) : (
                          <div data-testid="learning-validation-cell">
                            <span>{validationPassedOf(proposal) ? '通過' : '未通過'}</span>
                            {error && (
                              <div style={{ color: 'var(--text-muted)' }}>拒絕理由：{error}</div>
                            )}
                            {metrics.some((metric) => metric.before !== null || metric.after !== null) && (
                              <table style={{ borderCollapse: 'collapse', marginTop: '4px' }}>
                                <tbody>
                                  {metrics.map((metric) => (
                                    <tr key={metric.key}>
                                      <td style={{ paddingRight: '6px', color: 'var(--text-muted)' }}>
                                        {metric.label}
                                      </td>
                                      <td style={{ paddingRight: '4px' }}>
                                        {metricValue(metric.key, metric.before)}
                                      </td>
                                      <td>
                                        <span aria-hidden>→</span> {metricValue(metric.key, metric.after)}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        <span className={statusPillClass(proposal.status)} data-testid="learning-proposal-status">
                          {learningStatusLabel(proposal.status)}
                        </span>
                      </td>
                      <td style={{ padding: '6px 8px', minWidth: '150px' }}>
                        {decision.kind === 'none' ? (
                          <span data-testid="learning-proposal-blocked" style={{ color: 'var(--text-muted)' }}>
                            {decision.reason}
                          </span>
                        ) : (
                          <button
                            type="button"
                            className={decision.kind === 'deploy' ? 'btn btn-primary' : 'btn btn-secondary'}
                            data-testid={`learning-${decision.kind}`}
                            onClick={() => void handleDecision(proposal, decision.kind)}
                            disabled={isBusy}
                          >
                            {isBusy
                              ? '送請伺服器中…'
                              : decision.kind === 'approve'
                                ? '批准（approve）'
                                : '部署（deploy）'}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '10px' }}>
          「部署」這個按鈕做的事：後端只寫 status 與 deployed_at
          兩個欄位（api/learning.py:470-471），並沒有任何研究、紙上交易或實盤流程讀取
          LearningProposal 或 proposed_exit_config。所以部署之後的實際交易行為不會改變；
          它記錄的是決定，不是生效的設定。
        </p>
      </section>

      <section className="card" aria-label="排程工作記錄" style={{ marginTop: '20px' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            gap: '12px',
            flexWrap: 'wrap',
            marginBottom: '8px',
          }}
        >
          <h2 style={{ fontSize: '15px', margin: 0 }}>工作記錄（{jobs.length}）</h2>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="learning-trigger-scheduled-study"
              onClick={() => void handleTrigger('scheduled_study')}
              disabled={triggering !== null}
            >
              {triggering === 'scheduled_study' ? '建立中…' : '執行排程研究'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="learning-trigger-pattern-analysis"
              onClick={() => void handleTrigger('pattern_analysis')}
              disabled={triggering !== null}
            >
              {triggering === 'pattern_analysis' ? '建立中…' : '執行樣板分析'}
            </button>
          </div>
        </div>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '0 0 10px' }}>
          這兩個按鈕會真的送出 POST /api/v1/learning/jobs/trigger，建立的是「請求記錄」那一列：
          排程器確實在跑時才會立刻另起執行緒以預設的標的與週期清單開始工作
          （api/learning.py:203-232）；排程器沒啟動時，這一列會停在「待處理」，
          而且定時觸發不會來認領它（同一端點的文件說明，:148-151）。
          真的執行會取用外部市場資料並使用運算，不是預覽。
          自動排程的節奏是每日 02:00 研究、每週日 04:00 樣板分析
          （workers/learning_scheduler.py:79-93），而且那兩個排程把工作記在資料庫中
          「第一位使用者」名下（:99-104、:164、:355），不是目前登入者——
          所以第二個帳號的這張頁面不會看見那些自動記錄。
        </p>
        {loading ? (
          <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>載入中…</p>
        ) : jobs.length === 0 ? (
          <p data-testid="learning-jobs-empty" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            這個帳號還沒有學習工作記錄。每個出場設定的成交累積到 30 筆以上，
            樣板分析才會提出參數調整提案；也可以按上面的按鈕立刻建立一筆工作記錄。
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border-subtle)' }}>
                  <th style={{ padding: '6px 8px' }}>建立時間</th>
                  <th style={{ padding: '6px 8px' }}>類型</th>
                  <th style={{ padding: '6px 8px' }}>狀態</th>
                  <th style={{ padding: '6px 8px' }}>設定</th>
                  <th style={{ padding: '6px 8px' }}>結果</th>
                  <th style={{ padding: '6px 8px' }}>失敗原因</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const summary = jobResultSummaryOf(job);
                  return (
                  <tr
                    key={job.id}
                    data-testid="learning-job-row"
                    data-status={job.status}
                    style={{ borderBottom: '1px solid var(--border-subtle)', verticalAlign: 'top' }}
                  >
                    <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                      {formatDateTime(job.created_at)}
                      {job.completed_at && (
                        <div style={{ color: 'var(--text-muted)' }}>
                          完成：{formatDateTime(job.completed_at)}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '6px 8px' }}>{job.job_type}</td>
                    <td style={{ padding: '6px 8px' }}>
                      <span className={statusPillClass(job.status)}>{learningStatusLabel(job.status)}</span>
                    </td>
                    <td style={{ padding: '6px 8px', maxWidth: '240px' }}>
                      {formatParamMap(job.config_json)}
                    </td>
                    <td style={{ padding: '6px 8px', maxWidth: '240px' }}>
                      {summary ? (
                        <span data-testid="learning-job-result">{summary}</span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>
                          {job.result_json ? '結果欄沒有記錄計數' : '尚無結果'}
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '6px 8px', maxWidth: '240px' }}>
                      {job.error_details ? (
                        <span data-testid="learning-job-error">{renderStoredFailure(job.error_details)}</span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>—</span>
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
    </div>
  );
}
