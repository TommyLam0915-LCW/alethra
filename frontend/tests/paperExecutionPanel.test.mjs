// What the paper-execution panel is allowed to show, and what it is forbidden to
// claim, while an approval, an execution press or a ledger read is in flight.
//
// Run with:
//   node22 --experimental-strip-types --test tests/paperExecutionPanel.test.mjs
//
// The module under test imports its sibling extensionless, the way every product
// module does, so the resolve hook is registered here before either module is
// loaded rather than passed on the command line.
//
// Round 21 shipped the approval door with `skill_unit_id: null` and stopped there;
// round 22 shipped the paper door in the backend. Nothing connected the two to the
// owner's hands, so the product gap this round names is a specific one: a reader of
// that screen could not tell that a job-only approval authorises *no* skill account,
// could not select the account they meant, and could not see what the ledger said
// after a press. The three groups below correspond to those three gaps, and the last
// one re-reads `src/app/agent-strategy/page.tsx` to show the screen goes through the
// functions being tested rather than through a copy of them.
//
// Nothing here is rendered-UI evidence. The browser journey is written in
// `e2e/agent-approval-paper-journey.spec.ts` and NOT RUN, exactly as the round asks.

import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const { ACTIVATION_APPROVED, ACTIVATION_NOT_APPROVED, verificationOf } = await import(
  '../src/lib/activationPanel.ts'
);
const {
  acceptPaperRead,
  approvalScopeError,
  approvalScopeNotice,
  approvalScopeOf,
  approvalScopeSentence,
  beginPaperRead,
  executionOutcomeZH,
  identityKeyOf,
  isDisplayedPaperContext,
  ledgerOfContext,
  NO_PAPER_READ,
  paperContextKey,
  paperReadbackNotice,
  paperRefusalNotice,
  PAPER_BLOCKED,
  PAPER_EXECUTED,
  PAPER_SCOPE_REFUSAL_CODES,
  PAPER_SKIPPED,
  readbackRowOf,
  SCOPE_NONE,
  SCOPE_SPECIFIC,
  submitBlockedReason,
  tradeLinksZH,
} = await import('../src/lib/paperExecutionPanel.ts');

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = join(FRONTEND, '..');

const JOB_A = '11111111-1111-4111-8111-111111111111';
const JOB_B = '22222222-2222-4222-8222-222222222222';
const SKILL_1 = '33333333-3333-4333-8333-333333333333';
const SKILL_2 = '44444444-4444-4444-8444-444444444444';
const SUM_A = 'a'.repeat(64);
const SUM_B = 'b'.repeat(64);
const EXEC_1 = '55555555-5555-4555-8555-555555555555';
const EXEC_2 = '66666666-6666-4666-8666-666666666666';

const ctx = (over = {}) => ({
  jobId: JOB_A,
  skillUnitId: SKILL_1,
  artifactChecksum: SUM_A,
  ...over,
});

/** `activation_state` as the gate writes it, one leg at a time. */
const state = (over = {}) => ({
  activation_status: ACTIVATION_APPROVED,
  reason_code: null,
  approval_id: 'a'.repeat(36),
  skill_unit_id: null,
  skill_unit_scope: SCOPE_NONE,
  artifact_checksum: SUM_A,
  ...over,
});

const scoped = (skillUnitId = SKILL_1) =>
  state({ skill_unit_id: skillUnitId, skill_unit_scope: SCOPE_SPECIFIC });

const row = (over = {}) => ({
  execution_id: EXEC_1,
  job_id: JOB_A,
  skill_unit_id: SKILL_1,
  artifact_checksum: SUM_A,
  execution_status: PAPER_EXECUTED,
  model_decision: 'take',
  decision_record_id: '7'.repeat(36),
  order_id: '8'.repeat(36),
  fill_id: '9'.repeat(36),
  blocked_reason: null,
  candidate_id: '1'.repeat(36),
  signal_date: '2026-11-02',
  quantity: 3,
  execution_price: 101.5,
  ...over,
});

const defer = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// --- group 1: the scope of the live approval, read from the gate's own fields --

test('approvalScopeOf separates the four things an approval record can be', () => {
  assert.equal(approvalScopeOf(state()), 'job_only');
  assert.equal(approvalScopeOf(scoped()), 'specific');
  assert.equal(
    approvalScopeOf(state({ activation_status: ACTIVATION_NOT_APPROVED, approval_id: null, reason_code: 'no_recorded_approval' })),
    'no_approval',
  );
  // APPROVED with no row named is not a scope anyone can act on.
  assert.equal(approvalScopeOf(state({ approval_id: null })), 'unreadable');
  // A scope token this build does not know is not "job-only" either.
  assert.equal(approvalScopeOf(state({ skill_unit_scope: 'everything' })), 'unreadable');
  assert.equal(approvalScopeOf(state({ skill_unit_scope: null })), 'unreadable');
  assert.equal(approvalScopeOf(scoped(null)), 'unreadable');
  // Nothing read at all: the panel has no approval to describe.
  assert.equal(approvalScopeOf(null), 'unreadable');
  assert.equal(approvalScopeOf(undefined), 'unreadable');
});

test('a job-only approval is never shown as cover for the selected account', () => {
  const notice = approvalScopeNotice(state(), SKILL_1);
  assert.equal(notice.tone, 'warn');
  assert.match(notice.text, /job-only/);
  assert.match(notice.text, /不代表任何技能帳號可以據此紙上執行/);
  // The one legal disposal, named, and stated as the owner's own two acts.
  assert.match(notice.text, /agent_activation_already_approved/);
  assert.match(notice.text, /由你本人先撤回這份 job-only 同意，再重新提交一次綁定所選技能帳號的同意/);
  assert.match(notice.text, /不會代你撤回、不會替換、也不會把範圍放大/);
  // Positive control on the wording: no sentence here may read as permission.
  assert.doesNotMatch(notice.text, /可以提交/);
  assert.doesNotMatch(notice.text, /已綁定/);
});

test('the scope sentence differs per state, and only the covered one is ok', () => {
  const seen = {
    no_approval: approvalScopeNotice(state({ activation_status: ACTIVATION_NOT_APPROVED, approval_id: null }), SKILL_1),
    job_only: approvalScopeNotice(state(), SKILL_1),
    unreadable: approvalScopeNotice(state({ approval_id: null }), SKILL_1),
    other_account: approvalScopeNotice(scoped(SKILL_2), SKILL_1),
    covered: approvalScopeNotice(scoped(SKILL_1), SKILL_1),
    none_selected: approvalScopeNotice(scoped(SKILL_1), ''),
  };
  assert.deepEqual(
    Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, v.tone])),
    {
      no_approval: 'warn',
      job_only: 'warn',
      unreadable: 'err',
      other_account: 'warn',
      covered: 'ok',
      none_selected: 'warn',
    },
    'one tone per state — a missing read is err, a refusal to guess is warn',
  );
  const texts = Object.values(seen).map((n) => n.text);
  assert.equal(new Set(texts).size, texts.length, 'six states, six sentences, none shared');
  assert.match(seen.other_account.text, /另一個技能帳號/);
  assert.match(seen.unreadable.text, /讀不到不等於已綁定/);
  assert.match(seen.none_selected.text, /還沒有在這個面板上明選/);
  assert.match(seen.covered.text, /生效中的同意綁定的正是你選取的這個技能帳號/);
});

test('submitBlockedReason names every leg that keeps a press from leaving', () => {
  const lanes = [
    [ctx({ jobId: '' }), state(), '還沒有選定要執行的訓練產物。'],
    [ctx({ skillUnitId: '' }), state(), '還沒有明選要用哪一個你的技能帳號執行。'],
    [ctx({ artifactChecksum: '' }), state(), '讀不到這個 job 的產物雜湊，無法綁定執行。'],
    [ctx(), state({ activation_status: ACTIVATION_NOT_APPROVED, approval_id: null }), '這個產物還沒有生效中的本人啟用同意。'],
    [ctx(), state({ approval_id: null }), '讀不到這份同意的綁定範圍。'],
    [ctx(), state(), '生效中的同意沒有綁任何技能帳號（job-only），此帳號不可據此執行。'],
    [ctx(), scoped(SKILL_2), '生效中的同意綁的是另一個技能帳號。'],
  ];
  for (const [context, activation, expected] of lanes) {
    assert.equal(submitBlockedReason(context, activation), expected);
  }
  // The one admissible press: a scoped approval for exactly this triple.
  assert.equal(submitBlockedReason(ctx(), scoped(SKILL_1)), null);
  assert.equal(submitBlockedReason(ctx(), null), '讀不到這份同意的綁定範圍。');
  // The job-only lane is the round-23 gap: it must be blocked, not silently run.
  assert.notEqual(submitBlockedReason(ctx(), state()), null);
});

test('an approval form cannot send a scope it has not been told', () => {
  assert.equal(approvalScopeError('job_only', ''), null);
  assert.equal(approvalScopeError('job_only', SKILL_1), null);
  assert.equal(approvalScopeError('skill_unit', ''), '選了「綁定這個技能帳號」就必須先明選一個技能帳號；這頁不會替你補一個。');
  assert.equal(approvalScopeError('skill_unit', SKILL_1), null);

  assert.match(approvalScopeSentence('job_only', SKILL_1), /skill_unit_id 為 NULL/);
  assert.match(approvalScopeSentence('job_only', SKILL_1), /不授權任何技能帳號做紙上執行/);
  // Even when an account is selected below, job-only says what it does NOT do.
  assert.doesNotMatch(approvalScopeSentence('job_only', SKILL_1), /綁定你選取的技能帳號/);
  assert.match(approvalScopeSentence('skill_unit', SKILL_1), new RegExp(SKILL_1));
  assert.match(approvalScopeSentence('skill_unit', SKILL_1), /其他帳號不會因為這份同意而多出任何授權/);
});

// --- group 2: what the door said, kept distinguishable ------------------------

test('every code the panel names a disposal for is a code the backend raises', () => {
  const gate = source(join(REPO, 'backend/app/services/agent_paper_execution.py'));
  const approval = source(join(REPO, 'backend/app/services/model_activation.py'));
  for (const code of PAPER_SCOPE_REFUSAL_CODES) {
    const emitted = gate.includes(`"${code}"`) || approval.includes(`"${code}"`);
    assert.ok(emitted, `${code} is compared by this panel but raised nowhere in the lane`);
  }
  // The scope-leg refusals this round is about are all present, by name.
  for (const code of [
    'paper_execution_approval_scope_not_granted',
    'paper_execution_artifact_mismatch',
    'paper_execution_version_mismatch',
    'paper_execution_approval_changed_midflight',
    'agent_activation_already_approved',
  ]) {
    assert.ok(PAPER_SCOPE_REFUSAL_CODES.has(code), `${code} dropped out of the named set`);
  }
});

test('each refusal gets its own disposal, and the server keeps the floor', () => {
  const notices = [...PAPER_SCOPE_REFUSAL_CODES].map((code) =>
    paperRefusalNotice({ code, message_zh: `server sentence for ${code}`, details: { code } }),
  );
  assert.equal(new Set(notices.map((n) => n.text)).size, notices.length, 'five codes, five sentences');
  for (const [index, notice] of notices.entries()) {
    const code = [...PAPER_SCOPE_REFUSAL_CODES][index];
    assert.match(notice.text, new RegExp(`（code：${code}）`), 'the code stays visible');
    assert.match(notice.text, new RegExp(`server sentence for ${code}`), 'the server sentence is not replaced');
    assert.match(notice.text, /處置：/);
    assert.notEqual(notice.tone, 'ok', 'a refusal is never rendered as a success');
  }
  // The scope refusal says who has to act, because the page may not.
  const scope = paperRefusalNotice({ code: 'paper_execution_approval_scope_not_granted' });
  assert.match(scope.text, /這頁不會代你按那兩個按鈕/);
  assert.match(scope.text, /由你本人重新提交一次綁定這個帳號的同意/);
  // No server sentence, no invention: the door's own fallback line is used.
  assert.match(paperRefusalNotice({ code: 'paper_execution_artifact_mismatch' }).text, /被擋下了/);
});

test('an unrecognised failure is not dressed up as a paper refusal', () => {
  // The fallback the round-23 brief protects: these all return null so the caller
  // keeps whatever it already shows instead of a generic green or a fake code.
  assert.equal(paperRefusalNotice(null), null);
  assert.equal(paperRefusalNotice(undefined), null);
  assert.equal(paperRefusalNotice('boom'), null);
  assert.equal(paperRefusalNotice({}), null);
  assert.equal(paperRefusalNotice({ code: '' }), null);
  assert.equal(paperRefusalNotice({ code: 42 }), null);
  assert.equal(paperRefusalNotice({ reason: 'paper_execution_artifact_mismatch' }), null);
  // An unknown code is still rendered — with its own code and no invented disposal.
  const unknown = paperRefusalNotice({ code: 'paper_execution_brand_new', message_zh: '新拒因。' });
  assert.equal(unknown.tone, 'warn');
  assert.match(unknown.text, /（code：paper_execution_brand_new）/);
  assert.doesNotMatch(unknown.text, /處置：/);
  // A missing consent is a different severity from a scope mismatch.
  assert.equal(paperRefusalNotice({ code: 'paper_execution_owner_consent_not_active' }).tone, 'err');
});

test('the ledger vocabulary the panel compares is the vocabulary the lane writes', () => {
  const model = source(join(REPO, 'backend/app/models/agent_paper_execution.py'));
  const statuses = [...model.matchAll(/^(EXECUTED|SKIPPED|BLOCKED) = "(\w+)"$/gm)].map((m) => m[2]);
  assert.deepEqual(statuses, [PAPER_EXECUTED, PAPER_SKIPPED, PAPER_BLOCKED]);

  const gate = source(join(REPO, 'backend/app/services/model_activation.py'));
  const scope = /"skill_unit_scope": "(\w+)" if approval\.skill_unit_id else "(\w+)"/.exec(gate);
  assert.ok(scope, 'activation_state no longer publishes its scope the way this panel reads it');
  assert.deepEqual([scope[1], scope[2]], [SCOPE_SPECIFIC, SCOPE_NONE]);
});

test('the outcome sentence is the ledger status, said without additions', () => {
  const executed = executionOutcomeZH(row());
  const skipped = executionOutcomeZH(
    row({ execution_status: PAPER_SKIPPED, model_decision: 'skip', decision_record_id: null, order_id: null, fill_id: null }),
  );
  const blocked = executionOutcomeZH(
    row({ execution_status: PAPER_BLOCKED, blocked_reason: 'insufficient_virtual_cash', order_id: null, fill_id: null }),
  );
  const unknown = executionOutcomeZH(row({ execution_status: 'pending_review' }));

  assert.match(executed, /已執行/);
  assert.match(skipped, /沒有進場、沒有 order、沒有 fill/);
  assert.match(skipped, /有記錄的沒做，不是失敗/);
  assert.match(blocked, /blocked_reason：insufficient_virtual_cash/);
  assert.match(blocked, /沒有因此多出部位/);
  // An unreadable status must not be sorted into "executed".
  assert.match(unknown, /執行狀態讀不懂/);
  assert.doesNotMatch(unknown, /已執行：/);
  assert.equal(new Set([executed, skipped, blocked, unknown]).size, 4);
});

test('the trade links say which legs are absent instead of hiding them', () => {
  const full = tradeLinksZH(row());
  assert.match(full, /decision take/);
  assert.equal((full.match(/：無/g) ?? []).length, 0, 'a row with all legs says 無 zero times');
  const bare = tradeLinksZH(
    row({ execution_status: PAPER_SKIPPED, model_decision: 'skip', decision_record_id: null, order_id: null, fill_id: null }),
  );
  assert.equal((bare.match(/：無/g) ?? []).length, 3, 'the three absent legs are each named 無');
  assert.match(bare, /decision 列：無/);
  assert.match(bare, /order：無/);
  assert.match(bare, /fill：無/);
});

// --- group 3: which rows belong to this context -------------------------------

test('the ledger read is narrowed to the exact triple, and foreign rows are dropped', () => {
  const context = ctx();
  const rows = [
    row(),
    row({ execution_id: EXEC_2 }),
    row({ execution_id: '2'.repeat(36), skill_unit_id: SKILL_2 }),
    row({ execution_id: '3'.repeat(36), job_id: JOB_B }),
    row({ execution_id: '4'.repeat(36), artifact_checksum: SUM_B }),
    row({ execution_id: '5'.repeat(36), artifact_checksum: null }),
  ];
  const own = ledgerOfContext(rows, context);
  assert.deepEqual(own.map((r) => r.execution_id), [EXEC_1, EXEC_2]);
  assert.equal(own.length, 2, 'two accounts, two jobs, two artifacts and one unreadable row are all excluded');
  assert.equal(readbackRowOf(rows, context, EXEC_2).execution_id, EXEC_2);
  assert.equal(readbackRowOf(rows, context, '3'.repeat(36)), null, 'another account row is not this press');
  assert.equal(readbackRowOf(rows, context, null), null, 'a press with no id names no row');
  assert.equal(readbackRowOf(rows, context, 'nope'), null);
  assert.equal(identityKeyOf(row()), paperContextKey(context));
  for (const leg of ['job_id', 'skill_unit_id', 'artifact_checksum']) {
    assert.equal(identityKeyOf(row({ [leg]: null })), null, `${leg} missing must be unidentifiable, not a match`);
  }
});

test('an in-flight ledger answer about the old triple cannot reach the new panel', async () => {
  const screen = { context: '', rows: [], notice: null };
  let current = NO_PAPER_READ;
  const switchTo = (context) => {
    const key = context.jobId ? paperContextKey(context) : '';
    current = beginPaperRead(key, current);
    screen.context = key;
    screen.rows = [];
    screen.notice = null;
    return key;
  };
  // `loadPaperLedger` in miniature: same three checks, in the same order.
  const readLedger = async (context, answer) => {
    const key = paperContextKey(context);
    if (!isDisplayedPaperContext(current, key)) return { state: 'stale' };
    const read = beginPaperRead(key, current);
    current = read;
    const outcome = await answer;
    if (outcome.kind === 'failed') {
      return acceptPaperRead(current, read) ? { state: 'read_failed' } : { state: 'stale' };
    }
    const jobLegs = new Set(outcome.rows.map((r) => r.job_id).filter(Boolean));
    const answered =
      jobLegs.size === 0 ? null : jobLegs.size === 1 && jobLegs.has(context.jobId) ? key : 'foreign-job';
    if (!acceptPaperRead(current, read, answered)) return { state: 'stale' };
    screen.rows = outcome.rows;
    return {
      state: 'read',
      row: readbackRowOf(outcome.rows, context, outcome.executionId ?? null),
      counted: ledgerOfContext(outcome.rows, context).length,
    };
  };

  const keyA = switchTo(ctx());
  const pending = defer();
  const inflight = readLedger(ctx({ jobId: JOB_A }), pending.promise);
  const keyB = switchTo(ctx({ jobId: JOB_B, artifactChecksum: SUM_B }));
  assert.notEqual(keyA, keyB, 'the owner is standing on a different job now');

  const bRows = [row({ job_id: JOB_B, artifact_checksum: SUM_B, execution_id: 'c'.repeat(36) })];
  const bRead = await readLedger(ctx({ jobId: JOB_B, artifactChecksum: SUM_B }), Promise.resolve({ kind: 'rows', rows: bRows }));
  assert.equal(bRead.state, 'read');
  const rowsAfterB = screen.rows;
  assert.equal(rowsAfterB[0].job_id, JOB_B);

  // A's answer arrives now — including a row that names A, so the mismatch is real.
  pending.resolve({ kind: 'rows', rows: [row()], executionId: EXEC_1 });
  const aRead = await inflight;
  assert.deepEqual(aRead, { state: 'stale' });
  assert.deepEqual(screen.rows, rowsAfterB, 'B rows survived the late answer about A');

  // The same race on the failure path.
  const failing = defer();
  const inflightFail = readLedger(ctx({ jobId: JOB_B, artifactChecksum: SUM_B }), failing.promise);
  switchTo(ctx({ jobId: JOB_A }));
  failing.resolve({ kind: 'failed' });
  assert.deepEqual(await inflightFail, { state: 'stale' }, 'a failed read about A is not B\'s problem');

  // A payload that names a different job than the one asked about is dropped even
  // while its own context is on screen.
  switchTo(ctx({ jobId: JOB_A }));
  const crossed = await readLedger(
    ctx({ jobId: JOB_A }),
    Promise.resolve({ kind: 'rows', rows: [row({ job_id: JOB_B })] }),
  );
  assert.deepEqual(crossed, { state: 'stale' });
  assert.equal(screen.rows.length, 0, 'the foreign rows were never rendered as this job\'s ledger');

  // An empty read is not a mismatch, and counts as zero.
  switchTo(ctx({ jobId: JOB_A }));
  const empty = await readLedger(ctx({ jobId: JOB_A }), Promise.resolve({ kind: 'rows', rows: [] }));
  assert.deepEqual(empty, { state: 'read', row: null, counted: 0 });

  assert.deepEqual(
    [acceptPaperRead({ context: keyA, seq: 4 }, { context: keyA, seq: 4 }), acceptPaperRead({ context: keyA, seq: 4 }, { context: keyA, seq: 3 }), acceptPaperRead({ context: '', seq: 1 }, { context: '', seq: 1 })],
    [true, false, false],
    'newest-and-displayed is the accept rule; a retired seq and an unclaimed panel are not',
  );
});

test('without the guard, a late answer about A lands on the B panel', async () => {
  // The pre-fix shape, kept as the positive control for the test above: whatever
  // arrives last is what gets rendered, whoever it was asked about.
  const screen = { jobId: JOB_A, rows: [], notice: null };
  const preFixRead = async (answer) => {
    const outcome = await answer;
    screen.rows = outcome.rows;
    screen.notice = `ledger for ${outcome.rows[0].job_id}`;
    return screen.notice;
  };

  const pending = defer();
  const inflight = preFixRead(pending.promise);
  screen.jobId = JOB_B;
  await preFixRead({ rows: [row({ job_id: JOB_B })] });
  const bNotice = screen.notice;
  pending.resolve({ rows: [row({ job_id: JOB_A })] });
  await inflight;

  assert.equal(screen.jobId, JOB_B, 'the owner is looking at B');
  assert.equal(screen.notice, `ledger for ${JOB_A}`, 'and the panel now narrates A');
  assert.notEqual(screen.notice, bNotice);
  assert.equal(screen.rows[0].job_id, JOB_A);
});

// --- group 4: the notice a press is allowed to leave behind -------------------

test('a press is only ever evidence of an intent, never of an outcome', () => {
  const pressed = row();
  const readRows = [pressed];
  const confirmed = paperReadbackNotice(pressed, {
    state: 'read',
    row: readbackRowOf(readRows, ctx(), EXEC_1),
    counted: 1,
  });
  assert.equal(confirmed.tone, 'ok');
  assert.match(confirmed.text, /已執行/);
  assert.match(confirmed.text, /order：88888888…/);

  const notThere = paperReadbackNotice(pressed, { state: 'read', row: null, counted: 0 });
  assert.equal(notThere.tone, 'err');
  assert.match(notThere.text, /不在這個上下文的正式讀回裡/);
  assert.doesNotMatch(notThere.text, /已執行：/);

  const stale = paperReadbackNotice(pressed, { state: 'stale' });
  assert.equal(stale.tone, 'warn');
  assert.match(stale.text, /送出的時候你已經換掉了 job、技能帳號或產物/);
  assert.doesNotMatch(stale.text, /已執行：/);

  const failed = paperReadbackNotice(pressed, { state: 'read_failed' });
  assert.equal(failed.tone, 'warn');
  assert.match(failed.text, /讀不到不等於沒有執行，也不等於執行成功/);
  assert.doesNotMatch(failed.text, /已執行：/);

  // The uncertain press: no identifiable reply, so the panel re-reads and says it
  // will not mint a second intent.
  const uncertain = paperReadbackNotice(null, { state: 'read', row: null, counted: 2 });
  assert.equal(uncertain.tone, 'warn');
  assert.match(uncertain.text, /這次結果不明/);
  assert.match(uncertain.text, /不會自動再送一次，也不會另開一個新意圖/);
  assert.match(uncertain.text, /2 列/);

  const skippedTone = paperReadbackNotice(row({ execution_status: PAPER_SKIPPED }), {
    state: 'read',
    row: row({ execution_status: PAPER_SKIPPED }),
    counted: 1,
  });
  assert.equal(skippedTone.tone, 'warn', 'a recorded skip is not a green light');
  const blockedTone = paperReadbackNotice(row({ execution_status: PAPER_BLOCKED }), {
    state: 'read',
    row: row({ execution_status: PAPER_BLOCKED }),
    counted: 1,
  });
  assert.equal(blockedTone.tone, 'err');
});

test('the same intent sent twice is shown as one row, not two', () => {
  const ledgerRow = row();
  const echo = { ...ledgerRow, duplicate: true };
  const first = paperReadbackNotice(ledgerRow, { state: 'read', row: ledgerRow, counted: 1 });
  const second = paperReadbackNotice(echo, { state: 'read', row: ledgerRow, counted: 1 });
  assert.doesNotMatch(first.text, /多記一筆/);
  assert.match(second.text, /同一意圖已記過一次，這次沒有多記一筆/);
  assert.equal(second.tone, 'ok', 'the repeat is the same ledger row, told in the same voice');
  // One row in the ledger for two presses: the read is what says so.
  assert.equal(ledgerOfContext([ledgerRow], ctx()).length, 1);
  assert.equal(readbackRowOf([ledgerRow], ctx(), EXEC_1).execution_id, EXEC_1);
});

// --- group 5: the press, end to end, through the panel's own gates ------------

test('the eight lanes of one press, from blocked to read back', async () => {
  const renders = [];
  const screen = {
    context: '',
    rows: [],
    notice: null,
    busy: false,
  };
  let current = NO_PAPER_READ;
  const setNotice = (context, notice) => {
    if (!isDisplayedPaperContext(current, context)) return false;
    screen.notice = notice;
    renders.push([context, notice.text]);
    return true;
  };
  const switchTo = (context) => {
    const key = context.jobId ? paperContextKey(context) : '';
    current = beginPaperRead(key, current);
    screen.context = key;
    screen.rows = [];
    screen.notice = null;
    screen.busy = false;
    return key;
  };
  const readLedger = async (context, answer, executionId = null) => {
    const key = paperContextKey(context);
    if (!isDisplayedPaperContext(current, key)) return { state: 'stale' };
    const read = beginPaperRead(key, current);
    current = read;
    const outcome = await answer;
    if (outcome.kind === 'failed') {
      return acceptPaperRead(current, read) ? { state: 'read_failed' } : { state: 'stale' };
    }
    if (!acceptPaperRead(current, read, key)) return { state: 'stale' };
    screen.rows = outcome.rows;
    return {
      state: 'read',
      row: readbackRowOf(outcome.rows, context, executionId),
      counted: ledgerOfContext(outcome.rows, context).length,
    };
  };
  // `onRunPaperExecution`: gate, press, guard, read, guard, notice.
  const press = async (context, stateValue, writeAnswer, readAnswer) => {
    const key = paperContextKey(context);
    const blocked = submitBlockedReason(context, stateValue);
    if (blocked) {
      setNotice(key, { tone: 'warn', text: `${blocked}（這一筆不會送出）` });
      return { lane: 'blocked', writeCalls: 0 };
    }
    if (!isDisplayedPaperContext(current, key)) return { lane: 'stale-before-write', writeCalls: 0 };
    screen.busy = true;
    screen.notice = null;
    const write = await writeAnswer;
    if (!isDisplayedPaperContext(current, key)) return { lane: 'stale-after-write', writeCalls: 1 };
    if (write.kind === 'refusal') {
      const named = paperRefusalNotice(write.detail);
      setNotice(key, named);
      screen.busy = false;
      await readLedger(context, readAnswer);
      return { lane: 'refused', writeCalls: 1 };
    }
    if (write.kind === 'thrown') {
      const read = await readLedger(context, readAnswer);
      if (!isDisplayedPaperContext(current, key)) return { lane: 'stale-after-read', writeCalls: 1 };
      setNotice(key, { tone: read.tone ?? 'warn', text: `${write.message} ${paperReadbackNotice(null, read).text}` });
      screen.busy = false;
      return { lane: 'uncertain', writeCalls: 1 };
    }
    const read = await readLedger(context, readAnswer, write.row.execution_id);
    if (!isDisplayedPaperContext(current, key)) return { lane: 'stale-after-read', writeCalls: 1 };
    setNotice(key, paperReadbackNotice(write.row, read));
    screen.busy = false;
    return { lane: 'read-back', writeCalls: 1 };
  };

  // 1. job-only approval: the press never leaves the browser.
  switchTo(ctx());
  assert.deepEqual(
    await press(ctx(), state(), Promise.resolve({ kind: 'rows' }), Promise.resolve({ kind: 'rows', rows: [] })),
    { lane: 'blocked', writeCalls: 0 },
  );
  assert.match(screen.notice.text, /job-only/);

  // 2. no approval at all (the state after a withdrawal): also never leaves.
  switchTo(ctx());
  await press(
    ctx(),
    state({ activation_status: ACTIVATION_NOT_APPROVED, approval_id: null }),
    Promise.resolve({ kind: 'rows' }),
    Promise.resolve({ kind: 'rows', rows: [] }),
  );
  assert.match(screen.notice.text, /還沒有生效中的本人啟用同意/);

  // 3. the scoped positive control: one press, one read, the ledger's own word.
  switchTo(ctx());
  const scopedState = scoped(SKILL_1);
  const executed = await press(
    ctx(),
    scopedState,
    Promise.resolve({ kind: 'row', row: row() }),
    Promise.resolve({ kind: 'rows', rows: [row()] }),
  );
  assert.deepEqual(executed, { lane: 'read-back', writeCalls: 1 });
  assert.equal(screen.notice.tone, 'ok');
  assert.match(screen.notice.text, /已執行/);
  assert.equal(screen.rows.length, 1);

  // 4. the same intent again: the ledger still holds one row, and the door said so.
  await press(
    ctx(),
    scopedState,
    Promise.resolve({ kind: 'row', row: { ...row(), duplicate: true } }),
    Promise.resolve({ kind: 'rows', rows: [row()] }),
  );
  assert.match(screen.notice.text, /同一意圖已記過一次，這次沒有多記一筆/);
  assert.equal(screen.rows.length, 1, 'two presses, one ledger row');

  // 5. the owner stands on an account the displayed approval does not cover: the
  //    client gate keeps the press home, so no intent reaches the door at all.
  switchTo(ctx({ skillUnitId: SKILL_2 }));
  const gateKept = await press(
    ctx({ skillUnitId: SKILL_2 }),
    scoped(SKILL_1),
    Promise.resolve({ kind: 'refusal', detail: { code: 'paper_execution_approval_scope_not_granted' } }),
    Promise.resolve({ kind: 'rows', rows: [row({ skill_unit_id: SKILL_2 })] }),
  );
  assert.deepEqual(gateKept, { lane: 'blocked', writeCalls: 0 });
  assert.match(screen.notice.text, /生效中的同意綁的是另一個技能帳號/);

  // 6. a named refusal the client gate could not foresee: the approval moved between
  //    the read and the write, so the door answered and the page shows its code.
  switchTo(ctx());
  const refused = await press(
    ctx(),
    scoped(SKILL_1),
    Promise.resolve({
      kind: 'refusal',
      detail: {
        code: 'paper_execution_approval_changed_midflight',
        message_zh: '啟用同意在這一筆送出的期間已經改變，這一筆沒有綁任何同意。',
        details: { reason_code: 'approval_changed_midflight' },
      },
    }),
    Promise.resolve({ kind: 'rows', rows: [] }),
  );
  assert.deepEqual(refused, { lane: 'refused', writeCalls: 1 });
  assert.match(screen.notice.text, /paper_execution_approval_changed_midflight/);
  assert.match(screen.notice.text, /這一次沒有綁任何同意、也沒有寫入/);
  assert.equal(screen.rows.length, 0, 'the door wrote nothing, and the read says so');

  // 7. the press threw with no code: re-read, report the read, open no new intent.
  switchTo(ctx());
  const thrown = await press(
    ctx(),
    scopedState,
    Promise.resolve({ kind: 'thrown', message: '網路錯誤。' }),
    Promise.resolve({ kind: 'rows', rows: [row()] }),
  );
  assert.deepEqual(thrown, { lane: 'uncertain', writeCalls: 1 });
  assert.match(screen.notice.text, /網路錯誤。/);
  assert.match(screen.notice.text, /這次結果不明/);
  assert.match(screen.notice.text, /1 列/);
  assert.equal(screen.busy, false);

  // 8. the race: a read-back that lands after the owner switched accounts.
  switchTo(ctx());
  const write = defer();
  const read = defer();
  const inflight = press(ctx(), scopedState, write.promise, read.promise);
  write.resolve({ kind: 'row', row: row() });
  await tick();
  assert.equal(current.context, paperContextKey(ctx()), 'the read is running for the account A triple');
  switchTo(ctx({ skillUnitId: SKILL_2 }));
  read.resolve({ kind: 'rows', rows: [row()] });
  assert.deepEqual(await inflight, { lane: 'stale-after-read', writeCalls: 1 });
  assert.equal(screen.notice, null, 'A\'s outcome never narrates itself on B\'s panel');
  assert.equal(screen.rows.length, 0, 'and A\'s ledger rows are not shown as B\'s');

  const keyA = paperContextKey(ctx());
  const keyB = paperContextKey(ctx({ skillUnitId: SKILL_2 }));
  assert.deepEqual(
    renders.map(([context]) => context),
    [keyA, keyA, keyA, keyA, keyB, keyA, keyA],
    'one sentence per lane that rendered, in lane order (1, 2, 3, 4, the gate on B, 6, 7); lane 8 was stale and wrote nothing',
  );
  const onB = renders.filter(([context]) => context === keyB).map(([, text]) => text);
  assert.equal(onB.length, 1, 'the B panel was written once, by the gate sentence made on it');
  assert.match(onB[0], /生效中的同意綁的是另一個技能帳號/);
  assert.doesNotMatch(onB[0], /已執行|skipped|blocked_reason/, 'no outcome vocabulary reaches the other context');
});

test('verificationOf still answers from the approval record, not from the press', () => {
  // The activation lane's read-back helper is re-pinned here because the paper
  // panel reads the same field to decide whether a press is admissible at all.
  assert.deepEqual(verificationOf('approve', { outcome: 'loaded', state: scoped(SKILL_1) }), { state: 'confirmed' });
  // The status says APPROVED and still the intent is not held, because no approval
  // row is named; the sentence quotes what was read, not what was wanted.
  assert.deepEqual(verificationOf('approve', { outcome: 'loaded', state: state({ approval_id: null }) }), {
    state: 'not_confirmed',
    observed_status: ACTIVATION_APPROVED,
    reason_code: null,
  });
  assert.deepEqual(verificationOf('approve', { outcome: 'failed' }), { state: 'read_failed' });
});

// --- group 6: wiring — does the page go through the functions tested above? ----

function source(path) {
  assert.ok(existsSync(path), `${path} is not in the checkout to read`);
  return readFileSync(path, 'utf8');
}

const page = source(join(FRONTEND, 'src/app/agent-strategy/page.tsx'));
const api = source(join(FRONTEND, 'src/lib/api.ts'));
const lib = source(join(FRONTEND, 'src/lib/paperExecutionPanel.ts'));

test('the page reads and writes the paper lane through this module', () => {
  assert.match(
    page,
    /import \{[\s\S]*?acceptPaperRead,[\s\S]*?beginPaperRead,[\s\S]*?isDisplayedPaperContext,[\s\S]*?paperReadbackNotice,[\s\S]*?submitBlockedReason,[\s\S]*?\} from '@\/lib\/paperExecutionPanel';/,
  );
  // The read itself is guarded before it starts and after it lands.
  assert.match(page, /const key = paperContext\.jobId \? paperContextKey\(paperContext\) : '';\s*paperReadRef\.current = beginPaperRead\(key, paperReadRef\.current\);/);
  assert.match(page, /if \(!isDisplayedPaperContext\(paperReadRef\.current, key\)\) \{/);
  assert.match(page, /if \(!acceptPaperRead\(paperReadRef\.current, read, answered\)\) \{/);
  // Switching job, account or artifact clears the review before anything else.
  const effect = /useEffect\(\(\) => \{\s*const key = paperContext\.jobId[\s\S]*?\}, \[paperContext, loadPaperLedger\]\);/.exec(page);
  assert.ok(effect, 'the invalidating effect is no longer in the shape this test reads');
  for (const setter of ['setPaperRows\\(\\[\\]\\)', 'setPaperNotice\\(null\\)', 'setPaperReadError\\(\'\'\\)', 'setPaperBusy\\(false\\)']) {
    assert.match(effect[0], new RegExp(setter));
  }
  // The list of accounts comes from the formal portfolio door, twice at most.
  assert.match(page, /const rows = \(await api\.paperPortfolio\.list\(\)\) as SkillUnitRow\[\];/);
  assert.equal(
    [...page.matchAll(/<SkillUnitSelect\b/g)].length,
    2,
    'one selector for the approval scope, one for the execution target',
  );
});

test('the press is gated by the same function that writes the sentence', () => {
  const handler = /const onRunPaperExecution = async \(\) => \{[\s\S]*?\n  \};/.exec(page);
  assert.ok(handler, 'the paper handler is not in the shape this test reads');
  assert.match(handler[0], /const blocked = submitBlockedReason\(paperContext, activation\.state\);/);
  assert.match(handler[0], /if \(blocked\) \{\s*setPaperNotice\(\{ tone: 'warn', text: `\$\{blocked\} 這一筆沒有送出。`\ \}\);/);
  assert.match(handler[0], /await api\.agentStrategy\.paperExecution\(jobId, \{\s*skill_unit_id: paperContext\.skillUnitId,\s*\}\)/);
  // Both the write and the read-back are guarded, and neither invents a success.
  assert.equal(
    [...handler[0].matchAll(/if \(!stillOnPaperContext\(context\)\) return;/g)].length,
    3,
    'before the write, after the write, after the read',
  );
  assert.match(handler[0], /const read = await loadPaperLedger\(\s*paperContext,\s*pressed \? pressed\.execution_id : null,\s*\);/);
  assert.match(handler[0], /const notice = paperReadbackNotice\(pressed, read\);/);
  assert.doesNotMatch(handler[0], /tone: 'ok'/, 'the page writes no success tone itself');
  assert.doesNotMatch(handler[0], /execution_status: /, 'the page never fabricates a ledger status');
  // Positive control: the count above measures something.
  const oneGuardShort = handler[0].replace('if (!stillOnPaperContext(context)) return;', '');
  assert.equal(
    [...oneGuardShort.matchAll(/if \(!stillOnPaperContext\(context\)\) return;/g)].length,
    2,
    'a handler with one guard missing must not pass the count above',
  );
});

test('the approval press sends the scope the owner chose and the page reads back both', () => {
  assert.match(
    page,
    /skill_unit_id: approvalChoice === 'skill_unit' \? skillUnitId : null,/,
  );
  assert.doesNotMatch(page, /skill_unit_id: null,\n/, 'the round-21 hardcoded job-only body is gone');
  // The radio group is the owner's statement, and its sentence comes from the lib.
  assert.match(page, /data-testid="approval-scope-job-only"/);
  assert.match(page, /data-testid="approval-scope-skill-unit"/);
  assert.match(page, /\{approvalScopeSentence\(approvalChoice, skillUnitId\)\}/);
  assert.match(page, /disabled=\{!approveReady \|\| scopeChoiceError !== null\}/);
  // The displayed scope is read from the server's own fields, not from the form.
  assert.match(page, /const approvalScope = approvalScopeOf\(activation\?\.state\);/);
  assert.match(page, /data-testid="activation-scope"/);
  assert.match(page, /\{approvalScopeNotice\(activation\.state, skillUnitId\)\}/);
  // No local assertion of an approved status anywhere on the page.
  assert.doesNotMatch(page, /setActivation\(\{ *state: *\{ *activation_status: '/);
  assert.doesNotMatch(page, /activation_status: ACTIVATION_APPROVED/);
});

test('the two formal paper doors are the ones the client calls', () => {
  assert.match(
    api,
    /paperExecution: \(jobId: string, data: \{ skill_unit_id: string \}\) =>\s*apiFetch\(`\/api\/v1\/agent-strategy\/training\/jobs\/\$\{jobId\}\/paper-execution`, \{\s*method: 'POST',/,
  );
  assert.match(
    api,
    /paperExecutions: \(jobId: string, skillUnitId\?: string \| null\) =>\s*apiFetch\(\s*`\/api\/v1\/agent-strategy\/training\/jobs\/\$\{jobId\}\/paper-executions/,
  );
  assert.match(lib, /import \{ ACTIVATION_APPROVED \} from '\.\/activationPanel';/);
});

test('the panel shows the read, including the legs that are absent', () => {
  const panel = /data-testid="paper-execution-panel"[\s\S]*?\n                  \)\}/.exec(page);
  assert.ok(panel, 'the paper panel is not in the shape this test reads');
  // Two spellings of the same handle: `NoticeBox` takes the id as a prop and puts it
  // on the node as `data-testid`, so the source is read where each one actually is.
  for (const id of ['paper-scope-notice', 'paper-notice', 'paper-read-error']) {
    assert.match(panel[0], new RegExp(`testId="${id}"`), `${id} is gone from the panel`);
  }
  for (const id of [
    'paper-block-reason',
    'paper-execute',
    'paper-reload',
    'paper-ledger-summary',
    'paper-ledger-empty',
  ]) {
    assert.match(panel[0], new RegExp(`data-testid="${id}"`), `${id} is gone from the panel`);
  }
  assert.match(page, /function NoticeBox\(\{ notice, testId \}/);
  assert.match(page, /data-testid=\{testId\}/);
  assert.match(panel[0], /\{executionOutcomeZH\(row\)\}/);
  assert.match(panel[0], /\{tradeLinksZH\(row\)\}/);
  assert.match(panel[0], /disabled=\{paperBlockReason !== null \|\| paperBusy \|\| busy !== null\}/);
  assert.match(panel[0], /onClick=\{\(\) => void loadPaperLedger\(paperContext\)\}/);
  // The count the isolation claim rests on is the filtered one, said next to the raw one.
  assert.match(panel[0], /共 \{paperRows\.length\} 列[\s\S]*?屬於目前上下文[\s\S]*?\{paperRowsOfContext\.length\} 列/);
  assert.doesNotMatch(panel[0], /已執行 \$\{/, 'no locally computed success count');
});
