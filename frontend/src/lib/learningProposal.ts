// What the /learning page is allowed to offer for one proposal, in the server's
// own gate order.
//
// The ladder has three rungs and every predicate lives in the backend, not here:
//
//   pending → validated   `approve` checks a truthy `validation_result.passed`
//                         first (`api/learning.py:295-308`) and only then lets an
//                         atomic `UPDATE … WHERE status = 'pending'` land
//                         (`:317-329`); a second approve loses there and comes
//                         back as `proposal_not_approvable`.
//   validated → deployed  `deploy` re-checks three things in order — status
//                         (`:416-429`), then `rationale_json.evidence_status`
//                         against the single allowed value (`:431-453`), then
//                         `passed` again (`:455-468`).
//
// `approve` is the only producer of `evidence_status="validated_candidate"`
// (`:310-312`), and the scheduler writes every proposal it creates as
// `status="pending"` with `evidence_status="hypothesis"`
// (`workers/learning_scheduler.py:398-416`, `:440-441`). So a pending proposal
// whose `validation_result.passed` is false has no legal next rung at all, and
// the page says so instead of offering a button the server would refuse.
//
// Two things this file refuses to imply:
//
//   - There is no "validate" endpoint. `validate_proposal` runs only inside the
//     scheduler (`workers/learning_scheduler.py:421-436`), so a proposal with no
//     passing validation cannot be pushed forward from this page.
//   - Deploy is bookkeeping. `deploy_proposal` writes `status` and `deployed_at`
//     and nothing else (`:470-471`), and no research, paper, or live path reads
//     `LearningProposal` or `proposed_exit_config` outside the validation call
//     above. The server's own response sentence promises the config "will be
//     used in future research runs"; nothing in the code does that, so the page
//     quotes the recorded config instead of repeating the promise.

export interface LearningProposalRow {
  id: string;
  user_id: string;
  learning_job_id: string;
  rule_id: string;
  exit_id: string;
  instrument_id: string;
  interval_minutes: number;
  baseline_exit_config: Record<string, unknown> | null;
  proposed_exit_config: Record<string, unknown> | null;
  rationale_json: Record<string, unknown> | null;
  validation_result: Record<string, unknown> | null;
  status: string;
  created_at: string | null;
  validated_at: string | null;
  deployed_at: string | null;
}

export interface LearningJobRow {
  id: string;
  user_id: string;
  status: string;
  job_type: string;
  config_json: Record<string, unknown> | null;
  result_json: Record<string, unknown> | null;
  error_details: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

// The value `deploy_proposal` compares against (`api/learning.py:434`).
export const DEPLOYABLE_EVIDENCE_STATUS = 'validated_candidate';

/**
 * Reads the evidence status the way `api/learning.py:433` does —
 * `rationale_json.get("evidence_status", "unknown")`.
 *
 * A missing key and a non-string value are both reported as `unknown`, which is
 * what the server's own message text calls the not-assessed case (`:445`).
 * Python would keep a stored `null` as `None`; either way the deploy comparison
 * fails, so the verdict shown here is the server's verdict.
 */
export function evidenceStatusOf(proposal: LearningProposalRow): string {
  const rationale = proposal.rationale_json;
  if (!rationale || typeof rationale !== 'object') return 'unknown';
  const value = (rationale as Record<string, unknown>).evidence_status;
  return typeof value === 'string' && value.trim() ? value : 'unknown';
}

/**
 * `validation_result` is `null` until the scheduler's validation call returns,
 * and the server's test is truthiness of `.get("passed", False)` (`:296`,
 * `:457`) — not `=== true`. A non-boolean truthy value therefore has to count
 * here or the page would offer an action the server accepts.
 */
export function validationPassedOf(proposal: LearningProposalRow): boolean {
  const result = proposal.validation_result;
  if (!result || typeof result !== 'object') return false;
  return Boolean((result as Record<string, unknown>).passed);
}

/**
 * The `error` key the validation functions return when they refuse to compare
 * anything (`services/pattern_analysis.py:469-606`, eight keys), so "not passed" can be
 * told apart from "could not be checked".
 */
export function validationErrorOf(proposal: LearningProposalRow): string {
  const result = proposal.validation_result;
  if (!result || typeof result !== 'object') return '';
  const value = (result as Record<string, unknown>).error;
  return typeof value === 'string' ? value : '';
}

/**
 * The comparison numbers a passing validation recorded, field by field.
 *
 * `services/pattern_analysis.py:625-640` writes these only on the branch that
 * actually replayed both configs; every refusal branch returns early with
 * `passed: false` and no numbers at all. So an absent field is shown as `null`
 * and the page renders `—` — printing `0` here would claim a measured zero
 * where the product recorded nothing.
 *
 * The scales are not the same and the producer names them: Sharpe is a ratio,
 * max drawdown is percent points (`_compute_max_dd_from_research_trades`
 * multiplies by 100, `:666`), win rate is a 0-1 fraction (`:581`, `:587-592`).
 */
export interface ValidationMetric {
  key: 'sharpe' | 'max_dd' | 'win_rate';
  label: string;
  before: number | null;
  after: number | null;
}

function finiteOrNull(value: unknown): number | null {
  const num = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(num) ? num : null;
}

export function validationMetricsOf(proposal: LearningProposalRow): ValidationMetric[] {
  const result = proposal.validation_result;
  if (!result || typeof result !== 'object') return [];
  const read = (name: string) =>
    Object.prototype.hasOwnProperty.call(result, name) ? finiteOrNull(result[name]) : null;
  return [
    {
      key: 'sharpe',
      label: 'Sharpe',
      before: read('sharpe_before'),
      after: read('sharpe_after'),
    },
    {
      key: 'max_dd',
      label: '最大回撤（百分點）',
      before: read('max_dd_before'),
      after: read('max_dd_after'),
    },
    {
      key: 'win_rate',
      label: '勝率（0–1）',
      before: read('win_rate_before'),
      after: read('win_rate_after'),
    },
  ];
}

/**
 * One line for a job's `result_json`, using only the counts the two producers
 * actually write:
 *
 *   `run_scheduled_research` → `{results, errors, total_combos, successful,
 *   failed}` (`workers/learning_scheduler.py:222-228`)
 *   `run_pattern_analysis_job` → `{analysis, proposals_created}` (`:472-475`)
 *
 * The `results` and `analysis` values are nested lists and objects, so a
 * generic key=value dump would print `[object Object]` where a number belongs.
 * Returning `''` when neither count set is present means "this record does not
 * say", which the caller renders as a dash rather than as zero.
 */
export function jobResultSummaryOf(job: LearningJobRow): string {
  const result = job.result_json;
  if (!result || typeof result !== 'object' || Array.isArray(result)) return '';
  const read = (name: string): number | null => {
    const value = (result as Record<string, unknown>)[name];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const successful = read('successful');
  const failed = read('failed');
  const total = read('total_combos');
  if (successful !== null && failed !== null && total !== null) {
    return `研究組合成功 ${successful}／失敗 ${failed}，本批共 ${total} 組（逐組失敗原因在「失敗原因」欄）。`;
  }
  const created = read('proposals_created');
  if (created !== null) {
    return `提出 ${created} 個提案（樣板分析的明細存放在 result_json.analysis，此頁不展開）。`;
  }
  return '';
}

export type ProposalDecision =
  | { kind: 'approve' }
  | { kind: 'deploy' }
  | { kind: 'none'; reason: string };

/**
 * Decides the one action the page may offer, checking the gates in the same
 * order the endpoints do, because the refusal a click would produce depends on
 * which gate the server hits first.
 */
export function decisionFor(proposal: LearningProposalRow): ProposalDecision {
  const status = proposal.status;
  const passed = validationPassedOf(proposal);
  const evidence = evidenceStatusOf(proposal);

  if (status === 'pending') {
    if (passed) return { kind: 'approve' };
    const why = validationErrorOf(proposal)
      ? `驗證未通過（伺服器記錄的拒絕理由：${validationErrorOf(proposal)}）。`
      : '驗證未通過。';
    return {
      kind: 'none',
      reason: `${why} 頁面沒有「送驗證」的端點可點——validation_result 只能由排程研究自己寫入。`,
    };
  }

  if (status === 'validated') {
    if (evidence !== DEPLOYABLE_EVIDENCE_STATUS) {
      return {
        kind: 'none',
        reason:
          `已批准，但 evidence_status 是「${evidence}」而不是「${DEPLOYABLE_EVIDENCE_STATUS}」，` +
          '所以 deploy 會被伺服器拒絕。這個組合不該出現在真實記錄上，請保留提案 ID 回報。',
      };
    }
    if (!passed) {
      return {
        kind: 'none',
        reason: 'deploy 會再檢查一次 validation_result.passed，而這份記錄目前不是通過的。',
      };
    }
    return { kind: 'deploy' };
  }

  if (status === 'deployed') {
    return { kind: 'none', reason: '已部署：決定已寫入記錄，沒有下一步動作。' };
  }

  if (status === 'rejected') {
    return { kind: 'none', reason: '已被驗證拒絕：決定已寫入記錄，重複批准會被拒絕。' };
  }

  return {
    kind: 'none',
    reason: `status「${status}」不是這個 API 會動作的狀態，批准與部署都會被伺服器拒絕。`,
  };
}

// The four `reason` keys the two endpoints can raise: 404
// `proposal_not_found` (`:289-293`, `:410-414`), 409 `validation_not_passed`
// (`:297-308` approve with `current_status`; `:459-468` deploy without it),
// 409 `proposal_not_approvable` (`:336-347`), and 409 `proposal_not_validated`
// in two different shapes — the status variant carries `current_status`
// (`:418-429`) and the evidence variant carries `evidence_status` plus
// `allowed_status` (`:435-453`). They must not be collapsed into one message:
// the first says the ladder position is wrong, the second says the evidence
// classification is, and they have different fixes.
export function refusalText(status: number, detail: unknown): string {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return '';
  const ent = detail as Record<string, unknown>;
  const reason = typeof ent.reason === 'string' ? ent.reason : '';
  if (!reason) return '';

  switch (reason) {
    case 'proposal_not_found': {
      const id = typeof ent.proposal_id === 'string' ? ent.proposal_id : '';
      return `找不到這個提案（${id || 'ID 未回報'}）。它不屬於目前登入的帳號，或已不存在。HTTP ${status}。`;
    }
    case 'validation_not_passed': {
      const current = typeof ent.current_status === 'string' ? ent.current_status : '';
      return current
        ? `拒絕：validation_result.passed 不是真，無法批准（目前 status「${current}」）。`
        : '拒絕：validation_result.passed 不是真，這個提案不能部署。';
    }
    case 'proposal_not_approvable': {
      const current = typeof ent.current_status === 'string' ? ent.current_status : null;
      return `拒絕：只有 status 是「pending」的提案能批准，這份記錄現在是「${
        current === null ? '未知' : String(current)
      }」。已寫下的決定不會被重複批准（可能是重複點擊）。`;
    }
    case 'proposal_not_validated': {
      if (typeof ent.evidence_status === 'string') {
        const allowed =
          typeof ent.allowed_status === 'string' ? ent.allowed_status : DEPLOYABLE_EVIDENCE_STATUS;
        return `拒絕：evidence_status 是「${ent.evidence_status}」，只有「${allowed}」能部署。`;
      }
      const current = typeof ent.current_status === 'string' ? ent.current_status : '未知';
      return `拒絕：只有 status 是「validated」的提案能部署，這份記錄現在是「${current}」。請先批准。`;
    }
    default:
      return '';
  }
}

const EVIDENCE_LABELS: Record<string, string> = {
  unknown: '未評估',
  descriptive: '僅歷史描述',
  hypothesis: '參數探索假說',
  insufficient: '資料不足',
  unsupported: '證據相反',
  validated_candidate: '已批准之候選',
};

/**
 * The label list is the server's own (`:441-451` spells out all five
 * non-deployable values), so the Chinese short forms stay tied to those
 * definitions rather than to a guess made here.
 */
export function evidenceStatusLabel(evidence: string): string {
  return EVIDENCE_LABELS[evidence] || `未分類（${evidence}）`;
}

const STATUS_LABELS: Record<string, string> = {
  pending: '待處理',
  running: '執行中',
  completed: '已完成',
  failed: '失敗',
  validated: '已批准',
  deployed: '已部署',
  rejected: '已拒絕',
};

export function learningStatusLabel(value: string): string {
  return STATUS_LABELS[value] || `未分類（${value}）`;
}
