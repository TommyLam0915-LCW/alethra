// What the /learning page is allowed to offer for one proposal, in the server's
// own gate order.
//
// The ladder has three rungs and every predicate lives in the backend, not here:
//
//   pending → validated   `approve` checks a truthy `validation_result.passed`
//                         first (`api/learning.py:405-418`) and only then lets an
//                         atomic `UPDATE … WHERE status = 'pending'` land
//                         (`:427-439`); a second approve loses there and comes
//                         back as `proposal_not_approvable` (`:452-463`).
//   validated → deployed  `deploy` re-checks three things in order — status
//                         (`:534-546`), then `rationale_json.evidence_status`
//                         against the single allowed value (`:551-571`), then
//                         `passed` again (`:573-586`).
//
// `approve` is the only producer of `evidence_status="validated_candidate"`
// (`:420-422`), and the scheduler writes every proposal it creates as
// `status="pending"` with `evidence_status="hypothesis"`
// (`workers/learning_scheduler.py:1034-1041`, `:1023`). So a pending proposal
// whose `validation_result.passed` is false has no legal next rung at all, and
// the page says so instead of offering a button the server would refuse.
//
// Two things this file refuses to imply:
//
//   - There is no "validate" endpoint. `validate_proposal` runs only inside the
//     scheduler (`workers/learning_scheduler.py:1046-1061`, and the hypothesis
//     gate at `:1065` keeps those proposals pending), so a proposal with no
//     passing validation cannot be pushed forward from this page.
//   - Deploy is bookkeeping. `deploy_proposal` writes `status` and `deployed_at`
//     and nothing else (`api/learning.py:592-600`), and no research, paper, or
//     live path reads `LearningProposal` or `proposed_exit_config` outside the
//     validation call above. The server's own response sentence promises the
//     config "will be used in future research runs"; nothing in the code does
//     that, so the page quotes the recorded config instead of repeating the
//     promise.
//
// The second half of this file is the other decision the page makes: what one
// "submit a learning request" press costs the owner when it is pressed twice.
// That is a server-side gate too, so it lives here for the same reason.

import { formatDateTime } from './format';

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

// The value `deploy_proposal` compares against (`api/learning.py:552`).
export const DEPLOYABLE_EVIDENCE_STATUS = 'validated_candidate';

/**
 * Reads the evidence status the way `api/learning.py:551` does —
 * `rationale_json.get("evidence_status", "unknown")`.
 *
 * A missing key and a non-string value are both reported as `unknown`, which is
 * what the server's own message text calls the not-assessed case (`:561-568`).
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
 * and the server's test is truthiness of `.get("passed", False)` (`:406`,
 * `:575`) — not `=== true`. A non-boolean truthy value therefore has to count
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
 *   `_handle_scheduled_study` → `{results, errors, total_combos, successful,
 *   failed}` (`workers/learning_scheduler.py:800-810`)
 *   `_handle_pattern_analysis` → `{analysis, proposals_created}` (`:1095-1097`)
 *   (`run_scheduled_research` `:702` and `run_pattern_analysis_job` `:935` are
 *   the service entry points those handlers call, not the writers of this row.)
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

// -- Resubmission safety (本輪決策 三) ---------------------------------------
//
// POST /jobs/trigger is a queue write, so a press that reaches the server
// twice is a second research batch paid for by this owner. `disabled` on the
// button only covers the one tab that is already waiting; a second tab, a
// slow-network retry, and a refresh-then-press are all outside it. The header
// is what closes those: the server stores the key as `user:<key>`
// (`api/learning.py:232-240`) and compares it together with a fingerprint of
// the request body (`:263-295`), so the same key plus the same content replays
// the row the first press filed, while the same key with different content is
// refused rather than rewritten.
//
// That only works if the key names one *logical submit* — not one page and not
// one browser session. A deliberate second run must not inherit the first
// run's key, or the owner's second request silently becomes the first one's
// receipt.

export type LearningJobType = 'scheduled_study' | 'pattern_analysis';

// USER_KEY_MIN / USER_KEY_MAX at `api/learning.py:44-45`, enforced at `:235`.
// A key outside the range is refused 400 before anything is filed, so the page
// must not mint one.
export const OPERATION_KEY_MIN = 8;
export const OPERATION_KEY_MAX = 120;

/**
 * One opaque key for one logical submit of one job type.
 *
 * `crypto.randomUUID` exists only in a secure context, and this app is served
 * over plain http on a LAN address as often as on localhost, so the fallback
 * stays — the same two-branch idiom the experiment panels use
 * (`components/experiments/RevisionPanel.tsx:70-75`). Both branches keep the
 * prefixed key inside the server's bounds; the prefix is what makes a key
 * generated for `scheduled_study` never be mistaken for one generated for
 * `pattern_analysis` when a person reads the row afterwards.
 */
export function newOperationKey(jobType: LearningJobType): string {
  const random =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  return `${jobType}:${random}`.slice(0, OPERATION_KEY_MAX);
}

/**
 * Whether the page may forget the key it just sent.
 *
 * `status` is the HTTP status the browser actually read, or `null` when it read
 * none (offline, connection reset, the renewal path throwing a plain Error).
 * A 2xx ends the submit: either this call filed the row or the server said the
 * key had already filed it. A 4xx also ends it, because the endpoint refused
 * before enqueueing, so nothing is waiting under this key. A 5xx and a missing
 * answer do not: the row may have been committed before the response died, and
 * keeping the key is what turns the next press into a replay of that row
 * instead of a second batch.
 */
export function isSubmitSettled(status: number | null): boolean {
  return status !== null && status < 500;
}

export interface TriggerResponseShape {
  job?: { id?: unknown } | null;
  replayed?: unknown;
  next_claim_at?: unknown;
}

function claimTimeText(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return '未回報（以工作記錄的狀態為準）';
  return formatDateTime(value);
}

/**
 * The one line a successful trigger earns, separated by what the server
 * actually did — `replayed` is a boolean at `api/learning.py:322-332` and is
 * the only difference between "I queued this" and "this was already queued".
 * Collapsing them would hide the double-click from the person who needs to
 * know their second press cost nothing.
 */
export function triggerNoticeOf(response: TriggerResponseShape | null | undefined): string {
  const jobId =
    response?.job && typeof response.job.id === 'string' && response.job.id.trim()
      ? response.job.id
      : 'ID 未回報';
  if (response?.replayed === true) {
    return (
      `重複點擊已被伺服器去重：這把金鑰原本已排入同一個要求，回應的是那一筆工作 ${jobId}，` +
      '沒有建立第二份工作，也不會多跑一次研究。'
    );
  }
  return (
    `已排入學習要求（工作 ${jobId}），下次認領時間：${claimTimeText(response?.next_claim_at)}。` +
    '執行器會認領這一列並把結果寫回同一筆記錄；頁面不會另開第二份工作記錄。'
  );
}

/**
 * What to say when the press produced no readable answer. The request may or
 * may not have landed, so this must not read as either success or failure, and
 * it has to tell the owner what the next press will do: the same key is kept,
 * so a press now re-sends this operation and the server returns the row it
 * already has.
 */
export function triggerUnsettledText(jobIdOrKey: string): string {
  return (
    '送出後沒有讀到伺服器的回應，所以這個要求可能已被記錄，也可能沒有。' +
    `頁面保留這次提交的金鑰（${jobIdOrKey}）；再按一次會重送同一個要求，` +
    '伺服器會回給你原本那一筆工作，而不是排入第二次。'
  );
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

// The `reason` keys these three endpoints can raise. Two of them are proposals
// refusing a rung and two are a submit being refused before anything is filed;
// none of them may be collapsed into one sentence, because the fix differs:
//
//   404 `proposal_not_found` (`api/learning.py:402`, `:531`)
//   409 `validation_not_passed` — approve carries `current_status` (`:405-418`),
//       deploy does not (`:573-586`)
//   409 `proposal_not_approvable` (`:441-463`)
//   409 `proposal_not_validated` in two shapes: the status variant carries
//       `current_status` (`:533-546`) and the evidence variant carries
//       `evidence_status` plus `allowed_status` (`:549-571`). The first says the
//       ladder position is wrong, the second says the evidence classification
//       is.
//   409 `learning_executor_unavailable` (`:244-261`) — no queue executor would
//       claim this request, so nothing was filed.
//   409 `idempotency_conflict` (`:281-295`) — the key already belongs to a
//       different operation; the first row is left exactly as it was.
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
    // The refusal is the point: an unclaimed request row would sit at pending
    // with no deadline and no owner who can see it running. Say what did not
    // happen (nothing was filed) before repeating the server's remedy.
    case 'learning_executor_unavailable': {
      const why = typeof ent.executor_status === 'string' ? ent.executor_status : '未知';
      const remedy = typeof ent.remedy === 'string' ? ent.remedy.trim() : '';
      return (
        `拒絕：這個伺服器程序裡沒有可用的學習執行器（${why}），` +
        '所以這個要求沒有被排入佇列，也沒有建立工作記錄。' +
        (remedy ? `伺服器提供的處理方式：${remedy}` : '請先啟動學習執行器再送出。')
      );
    }
    case 'idempotency_conflict': {
      return (
        '拒絕：這把 Idempotency-Key 已經用在另一個內容不同的學習要求上，' +
        '伺服器不會改寫原本那一筆，這一次也沒有建立新工作。' +
        '頁面已放棄這把金鑰，所以下一次按下會用一把新的；' +
        '要重送原本那個要求，請送完全相同的內容與原本的金鑰。'
      );
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
 * The label list is the server's own (`api/learning.py:561-568` spells out all
 * five non-deployable values), so the Chinese short forms stay tied to those
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
