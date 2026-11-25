/**
 * Pure decisions for the paper-execution panel on /agent-strategy.
 *
 * The door itself (`POST /training/jobs/{id}/paper-execution`) already refuses by
 * name and answers in the owner's own language, so nothing here re-states a
 * server verdict. What the door cannot see from one request is what this module
 * owns: which skill account the owner is standing on right now, whether the live
 * approval's *recorded* scope covers it, and whether an answer that arrives late
 * still belongs to the context on screen.
 *
 * Three facts stay three facts, because the backend keeps them apart:
 * the approval's scope (a row the owner signed), the execution's outcome (a ledger
 * row read back), and the press (a browser event that proves nothing about either).
 * A press is never rendered as an outcome, and a read that did not happen is never
 * rendered as the state it was asked to confirm.
 */

import { ACTIVATION_APPROVED } from './activationPanel';

/** The two scope values `activation_state` writes; mirrors model_activation.py:408. */
export const SCOPE_SPECIFIC = 'specific';
export const SCOPE_NONE = 'none';

/**
 * What the live approval says about skill accounts — four states, not three.
 *
 * `unreadable` is its own state because a panel that collapsed it into
 * `job_only` would be telling the owner their account is covered by an approval
 * whose scope it never read.
 */
export type PaperScope = 'no_approval' | 'job_only' | 'specific' | 'unreadable';

/** The part of `GET .../activation` that settles whether this account may run. */
export interface ActivationStateLike {
  activation_status?: string | null;
  approval_id?: string | null;
  skill_unit_id?: string | null;
  skill_unit_scope?: string | null;
  artifact_checksum?: string | null;
}

export function approvalScopeOf(
  state: ActivationStateLike | null | undefined
): PaperScope {
  if (!state) return 'unreadable';
  if (state.activation_status !== ACTIVATION_APPROVED) return 'no_approval';
  // APPROVED with no row named is not a scope: it is a status this panel cannot
  // point at, and the gate itself refuses on exactly that difference.
  if (!state.approval_id) return 'unreadable';
  if (state.skill_unit_scope === SCOPE_SPECIFIC) {
    return state.skill_unit_id ? 'specific' : 'unreadable';
  }
  if (state.skill_unit_scope === SCOPE_NONE) return 'job_only';
  return 'unreadable';
}

/** The three things one press is about. All three invalidate the panel's state. */
export interface PaperContext {
  jobId: string;
  skillUnitId: string;
  artifactChecksum: string;
}

export function paperContextKey(context: PaperContext): string {
  return `${context.jobId}|${context.skillUnitId}|${context.artifactChecksum}`;
}

/**
 * The same key taken from what the server wrote about a row or a reply.
 *
 * A payload missing any leg is not a match and not a mismatch — it is
 * unidentifiable, and `null` lets the caller drop it instead of guessing.
 */
export function identityKeyOf(row: {
  job_id?: string | null;
  skill_unit_id?: string | null;
  artifact_checksum?: string | null;
}): string | null {
  if (!row.job_id || !row.skill_unit_id || !row.artifact_checksum) return null;
  return paperContextKey({
    jobId: row.job_id,
    skillUnitId: row.skill_unit_id,
    artifactChecksum: row.artifact_checksum,
  });
}

/** Which read of which context the panel currently owns. */
export interface PaperReadId {
  context: string;
  seq: number;
}

export const NO_PAPER_READ: PaperReadId = { context: '', seq: 0 };

export function beginPaperRead(context: string, previous: PaperReadId): PaperReadId {
  return { context, seq: previous.seq + 1 };
}

/** Whether the context a click was made on is still the one on screen. */
export function isDisplayedPaperContext(current: PaperReadId, context: string): boolean {
  return current.context !== '' && current.context === context;
}

/**
 * Whether an answer may still touch the panel. `answeredContext` is the key taken
 * from the server's own rows, so a reply that identifies a different job, account
 * or artifact is dropped even when this client asked with the right one.
 */
export function acceptPaperRead(
  current: PaperReadId,
  read: PaperReadId,
  answeredContext?: string | null
): boolean {
  if (!isDisplayedPaperContext(current, read.context)) return false;
  if (current.seq !== read.seq) return false;
  if (answeredContext != null && answeredContext !== read.context) return false;
  return true;
}

export type PaperTone = 'ok' | 'warn' | 'err';

export interface PaperNotice {
  tone: PaperTone;
  text: string;
}

/** The scope sentence shown next to the execute control, one per state. */
export function approvalScopeNotice(
  state: ActivationStateLike | null | undefined,
  selectedSkillUnitId: string
): PaperNotice {
  const scope = approvalScopeOf(state);
  if (scope === 'no_approval') {
    return {
      tone: 'warn',
      text:
        '這個產物目前沒有生效中的本人啟用同意，紙上執行會被整筆拒絕、不會寫入任何紙上紀錄。' +
        '要執行，請先在上方讀過啟用全文，並由你本人提交一次同意。',
    };
  }
  if (scope === 'unreadable') {
    return {
      tone: 'err',
      text:
        '讀不到這份同意的綁定範圍（status 或 approval 欄位不完整）。讀不到不等於已綁定這個技能帳號，' +
        '所以這裡不開放提交執行；請重新讀一次啟用狀態，讀不到就保持不執行。',
    };
  }
  if (scope === 'job_only') {
    return {
      tone: 'warn',
      text:
        '生效中的同意是 job-only（skill_unit_id 為 NULL）：它綁的是這個產物本身，' +
        '不代表任何技能帳號可以據此紙上執行，也不會被這頁解讀成「這個帳號可以跑」。' +
        '同一個 job 只容一份生效同意（後端以 agent_activation_already_approved 拒絕第二次批准），' +
        '所以合法處置是由你本人先撤回這份 job-only 同意，再重新提交一次綁定所選技能帳號的同意。' +
        '這頁不會代你撤回、不會替換、也不會把範圍放大。',
    };
  }
  if (!selectedSkillUnitId) {
    return {
      tone: 'warn',
      text:
        '這份同意綁定了一個技能帳號，但你還沒有在這個面板上明選要執行哪一個。' +
        '這裡不會自動幫你選一個帳號來湊那個同意。',
    };
  }
  if (state?.skill_unit_id === selectedSkillUnitId) {
    return {
      tone: 'ok',
      text:
        `生效中的同意綁定的正是你選取的這個技能帳號（approval ${state?.approval_id ?? '—'}），` +
        '可以提交一次紙上決策。實際結果以送出後正式讀回的那一列為準。',
    };
  }
  return {
    tone: 'warn',
    text:
      `生效中的同意綁的是另一個技能帳號（id 後 8 碼 ${String(state?.skill_unit_id).slice(-8)}），不是你選取的這一個。` +
      '提交會被整筆拒絕，這頁不會把那份同意解讀成給這個帳號的許可。',
  };
}

/**
 * Why the execute control is disabled, in the owner's language, or null when a
 * press is admissible. The job-only case is here rather than only in the copy so
 * the gate and the sentence cannot drift apart.
 */
export function submitBlockedReason(
  context: PaperContext,
  state: ActivationStateLike | null | undefined
): string | null {
  if (!context.jobId) return '還沒有選定要執行的訓練產物。';
  if (!context.skillUnitId) return '還沒有明選要用哪一個你的技能帳號執行。';
  if (!context.artifactChecksum) return '讀不到這個 job 的產物雜湊，無法綁定執行。';
  const scope = approvalScopeOf(state);
  if (scope === 'no_approval') return '這個產物還沒有生效中的本人啟用同意。';
  if (scope === 'unreadable') return '讀不到這份同意的綁定範圍。';
  if (scope === 'job_only') return '生效中的同意沒有綁任何技能帳號（job-only），此帳號不可據此執行。';
  if (state?.skill_unit_id !== context.skillUnitId) return '生效中的同意綁的是另一個技能帳號。';
  return null;
}

/** The two shapes an approval click can record, spelled out before it is sent. */
export type ApprovalChoice = 'job_only' | 'skill_unit';

export function approvalScopeError(
  choice: ApprovalChoice,
  selectedSkillUnitId: string
): string | null {
  if (choice === 'job_only') return null;
  return selectedSkillUnitId
    ? null
    : '選了「綁定這個技能帳號」就必須先明選一個技能帳號；這頁不會替你補一個。';
}

export function approvalScopeSentence(
  choice: ApprovalChoice,
  selectedSkillUnitId: string
): string {
  if (choice === 'job_only') {
    return '這份同意只綁這個產物本身（skill_unit_id 為 NULL）。它不授權任何技能帳號做紙上執行。';
  }
  return (
    `這份同意綁定你選取的技能帳號 ${selectedSkillUnitId}，` +
    '只有這個帳號可以拿這個產物做紙上執行；其他帳號不會因為這份同意而多出任何授權。'
  );
}

/** Which codes the paper door answers with, mirrored for the test to pin. */
export const PAPER_SCOPE_REFUSAL_CODES: ReadonlySet<string> = new Set([
  'paper_execution_approval_scope_not_granted',
  'paper_execution_artifact_mismatch',
  'paper_execution_version_mismatch',
  'paper_execution_approval_changed_midflight',
  'paper_execution_approval_revoked_midflight',
  'agent_activation_already_approved',
]);

// The disposal the door cannot name, because the door sees one request and not
// the account the owner is standing on. Anything outside this map keeps the
// server's own sentence plus its code: a refusal is never collapsed into a
// generic failure here, which is the difference between a retry that can help
// and one that cannot.
const DISPOSAL_ZH: Record<string, string> = {
  paper_execution_approval_scope_not_granted:
    '處置：撤回那份不覆蓋這個帳號的同意，再由你本人重新提交一次綁定這個帳號的同意；這頁不會代你按那兩個按鈕。',
  paper_execution_artifact_mismatch:
    '處置：這份同意綁的產物與目前載入的已經不同，請重新讀取啟用狀態後再由本人批准實際要用的產物。',
  paper_execution_version_mismatch:
    '處置：同意與訓練工作所綁的策略版本已不一致，請對目前這個 job 的產物重新批准。',
  paper_execution_approval_changed_midflight:
    '處置：這一次沒有綁任何同意、也沒有寫入；重新讀取啟用狀態後再送一次。',
  paper_execution_approval_revoked_midflight:
    '處置：這一筆送出之後本人的生效同意已被撤回，所以它停在提交前——沒有決策、委託、成交，也沒有帳本列；要再執行，先由本人重新批准綁定這個帳號的同意。',
  agent_activation_already_approved:
    '處置：同一個 job 只能有一份生效同意。要改綁定範圍，先由本人撤回現存的，再提交新的。',
};

export interface RefusalDetail {
  code?: unknown;
  message_zh?: unknown;
  message?: unknown;
  details?: unknown;
}

/**
 * The sentence for a refused paper request, from the server's own body.
 * Null when the body carries no recognisable code — the caller then keeps
 * whatever it already shows rather than dressing an unknown up as a refusal.
 */
export function paperRefusalNotice(detail: unknown): PaperNotice | null {
  if (!detail || typeof detail !== 'object') return null;
  const body = detail as RefusalDetail;
  if (typeof body.code !== 'string' || !body.code) return null;
  const server =
    typeof body.message_zh === 'string' && body.message_zh
      ? body.message_zh
      : typeof body.message === 'string'
        ? body.message
        : '';
  const parts = [
    server ? `${server}` : '這一筆被擋下了。',
    `（code：${body.code}）`,
  ];
  const disposal = DISPOSAL_ZH[body.code];
  if (disposal) parts.push(` ${disposal}`);
  return {
    tone: body.code === 'paper_execution_owner_consent_not_active' ? 'err' : 'warn',
    text: parts.join(''),
  };
}

/** The part of a ledger row this panel renders. Field names follow `execution_payload`. */
export interface PaperLedgerRow {
  execution_id: string;
  job_id: string;
  skill_unit_id: string;
  artifact_checksum: string;
  execution_status: string;
  model_decision?: string | null;
  decision_record_id?: string | null;
  order_id?: string | null;
  fill_id?: string | null;
  blocked_reason?: string | null;
  candidate_id?: string | null;
  signal_date?: string | null;
  quantity?: number | null;
  execution_price?: number | null;
  created_at?: string | null;
  duplicate?: boolean;
}

export const PAPER_EXECUTED = 'executed';
export const PAPER_SKIPPED = 'skipped';
export const PAPER_BLOCKED = 'blocked';

/**
 * The ledger's own verdict in one sentence, with no inference added.
 *
 * An unrecognised status is named as unreadable rather than sorted into
 * `executed`: the difference is whether the owner is told a trade exists.
 */
export function executionOutcomeZH(row: PaperLedgerRow): string {
  if (row.execution_status === PAPER_EXECUTED) {
    return '已執行：這個帳號的紙上書本進了一筆決策；部位、現金與成交細節以該帳號紙上書本的正式讀回為準。';
  }
  if (row.execution_status === PAPER_SKIPPED) {
    return `模型決定 skip（decision：${row.model_decision ?? '—'}），因此沒有進場、沒有 order、沒有 fill。這是一筆有記錄的沒做，不是失敗。`;
  }
  if (row.execution_status === PAPER_BLOCKED) {
    return `這筆被擋下（blocked_reason：${row.blocked_reason ?? '—'}），紙上書本沒有因此多出部位。`;
  }
  return `執行狀態讀不懂（execution_status：${String(row.execution_status)}），這裡不把它解讀成已執行。`;
}

/** What the row points at — including the legs that are absent, said out loud. */
export function tradeLinksZH(row: PaperLedgerRow): string {
  const leg = (value: string | null | undefined, label: string) =>
    `${label}：${value ? String(value).slice(0, 8) + '…' : '無'}`;
  return [
    `decision ${row.model_decision ?? '—'}`,
    leg(row.decision_record_id, 'decision 列'),
    leg(row.order_id, 'order'),
    leg(row.fill_id, 'fill'),
  ].join(' · ');
}

/** The ledger rows of one context, from the formal read; foreign rows dropped. */
export function ledgerOfContext(rows: PaperLedgerRow[], context: PaperContext): PaperLedgerRow[] {
  const key = paperContextKey(context);
  return rows.filter((row) => identityKeyOf(row) === key);
}

export function readbackRowOf(
  rows: PaperLedgerRow[],
  context: PaperContext,
  executionId: string | null
): PaperLedgerRow | null {
  const own = ledgerOfContext(rows, context);
  if (!executionId) return null;
  return own.find((row) => row.execution_id === executionId) ?? null;
}

export type PaperReadback =
  | { state: 'read'; row: PaperLedgerRow | null; counted: number }
  | { state: 'read_failed' }
  | { state: 'stale' };

/**
 * The sentence the panel may show after a press, given the read that followed it.
 *
 * The press is only ever evidence of an intent: the wording comes from the ledger
 * row the formal GET returned, and a press whose row is not there says so instead
 * of reporting the state it was aiming for.
 */
export function paperReadbackNotice(pressed: PaperLedgerRow | null, read: PaperReadback): PaperNotice {
  if (read.state === 'stale') {
    return {
      tone: 'warn',
      text:
        '送出的時候你已經換掉了 job、技能帳號或產物，所以這一次的結果不屬於目前畫面上的內容，' +
        '也沒有寫進來。目前內容的狀態以下方的讀回為準。',
    };
  }
  if (read.state === 'read_failed') {
    return {
      tone: 'warn',
      text:
        '讀不回 ledger（正式讀取入口沒有回話）。讀不到不等於沒有執行，也不等於執行成功：' +
        '這一筆在這裡不顯示終態，請重新讀一次。',
    };
  }
  if (!pressed) {
    return {
      tone: 'warn',
      text:
        '送出後沒有拿到可辨識的回報，這次結果不明。這一頁不會自動再送一次，也不會另開一個新意圖：' +
        `以下向正式入口重新查得 ${read.counted} 列屬於這個上下文的紙上執行紀錄，請以該讀回為準。`,
    };
  }
  if (read.state !== 'read' || read.row === null) {
    return {
      tone: 'err',
      text:
        `送出的那筆（execution ${pressed.execution_id}）不在這個上下文的正式讀回裡，` +
        '所以這裡不顯示它為已執行。請重新讀一次 ledger；若仍沒有這列，代表書本沒有收到它。',
    };
  }
  const row = read.row;
  const tone: PaperTone =
    row.execution_status === PAPER_EXECUTED ? 'ok' : row.execution_status === PAPER_SKIPPED ? 'warn' : 'err';
  // The repeat flag is the door's own word about this press; a ledger row carries
  // no such column, so the echo is the only place it can be seen.
  const duplicate = row.duplicate || pressed.duplicate
    ? '（同一意圖已記過一次，這次沒有多記一筆）'
    : '';
  return { tone, text: `${executionOutcomeZH(row)} ${tradeLinksZH(row)}${duplicate}` };
}
