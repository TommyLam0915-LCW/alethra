// What the activation panel is allowed to show while a read, an approval or a
// withdrawal is still in flight.
//
// Run with:
//   node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/activationPanel.test.mjs
//
// Round 21 proved the panel could read, approve and withdraw an artifact. It did
// not prove three things this round asks for, and the first of them is a real
// defect: `loadActivation` wrote whatever arrived last, so an answer about
// artifact A that landed after the owner selected B overwrote B's status, its
// acknowledgement text and its notice. The handlers then showed "Approval
// recorded" as soon as the PUT resolved — including when the readback behind it
// had thrown, and including for whichever artifact happened to be on screen.
//
// So this file tests the decisions themselves (they live in src/lib/
// activationPanel.ts) and then reads the page's source to show the page goes
// through them. Nothing here is rendered-UI evidence: the browser journey stays
// NOT RUN, and the panel is not clicked by this suite.

import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  acceptActivationRead,
  activationIntentHeld,
  activationWriteNotice,
  beginActivationRead,
  isDisplayedActivationJob,
  NO_ACTIVATION_READ,
  verifyActivation,
  verificationOf,
  ACTIVATION_APPROVED,
  ACTIVATION_NOT_APPROVED,
} from '../src/lib/activationPanel.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = join(FRONTEND, '..');

const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';

const approved = (approvalId = 'a'.repeat(36)) => ({
  activation_status: ACTIVATION_APPROVED,
  reason_code: null,
  approval_id: approvalId,
});
const notApproved = (reason = 'no_recorded_approval') => ({
  activation_status: ACTIVATION_NOT_APPROVED,
  reason_code: reason,
  approval_id: null,
});

// The panel's own two moves: select an artifact (retiring the read before it)
// and read one. `select` is the switching effect, `open` the first lines of
// `loadActivation`, including its refusal to fetch an artifact no longer shown.
function panelReads() {
  let current = NO_ACTIVATION_READ;
  const select = (jobId) => {
    current = beginActivationRead(jobId, current);
    return current;
  };
  const open = async (jobId, answer) => {
    if (!isDisplayedActivationJob(current, jobId)) return { outcome: 'stale' };
    const read = select(jobId);
    const outcome = await answer;
    if (!acceptActivationRead(current, read, outcome.job_id)) return { outcome: 'stale' };
    return { outcome: 'loaded', state: outcome.state };
  };
  return { get current() { return current; }, select, open };
}

/** A call whose answer the test releases by hand, so the switch can land between. */
function defer() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

/** One macrotask: every promise already resolved has run its continuation. */
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('an answer only reaches the panel while its artifact is the one on screen', async () => {
  const panel = panelReads();
  panel.select(A);
  const pending = defer();
  const readA = panel.open(A, pending.promise);
  panel.select(B);
  assert.equal(isDisplayedActivationJob(panel.current, A), false, 'A must not write B');
  assert.equal(isDisplayedActivationJob(panel.current, B), true);
  pending.resolve({ job_id: A, state: approved() });
  assert.equal((await readA).outcome, 'stale', 'A answers after the switch and is dropped');
});

test('a payload that names a different artifact than it was asked for is discarded', () => {
  const read = beginActivationRead(A, NO_ACTIVATION_READ);
  // Right slot, wrong subject: the server's own requirements.job_id disagrees.
  assert.equal(acceptActivationRead(read, read, B), false);
  // A refusal body carries no requirements; that is not a mismatch.
  assert.equal(acceptActivationRead(read, read, null), true);
  assert.equal(acceptActivationRead(read, read, undefined), true);
});

test('the newest read of one artifact retires the one before it', () => {
  const first = beginActivationRead(A, NO_ACTIVATION_READ);
  const second = beginActivationRead(A, first);
  assert.notEqual(first.seq, second.seq, 'each read gets its own slot');
  assert.equal(acceptActivationRead(second, first, A), false, 'a late first answer is stale');
  assert.equal(acceptActivationRead(second, second, A), true);
});

test('selecting an artifact with nothing to approve voids every in-flight answer', () => {
  const panel = panelReads();
  const displayed = panel.select(A);
  assert.equal(acceptActivationRead(displayed, displayed, A), true, 'A was on screen');
  const nothing = panel.select('');
  assert.equal(acceptActivationRead(nothing, displayed, A), false);
  assert.equal(isDisplayedActivationJob(nothing, A), false);
  assert.equal(isDisplayedActivationJob(NO_ACTIVATION_READ, A), false, 'nothing has been read yet');
});

test('an approval is only held when the record itself is named', () => {
  assert.equal(activationIntentHeld('approve', approved()), true);
  assert.equal(activationIntentHeld('approve', notApproved()), false);
  // A status token without a row behind it is not something the gate can point at.
  assert.equal(activationIntentHeld('approve', approved(null)), false);
  // Withdrawal is the mirror: still APPROVED means the withdrawal did not land.
  assert.equal(activationIntentHeld('withdraw', notApproved()), true);
  assert.equal(activationIntentHeld('withdraw', approved()), false);
});

test('a read that never happened is never upgraded into the state it aimed at', () => {
  for (const action of ['approve', 'withdraw']) {
    for (const read of [{ outcome: 'failed' }, { outcome: 'stale' }]) {
      const notice = activationWriteNotice(action, { verified: verificationOf(action, read) });
      assert.equal(notice.tone, 'warn', `${action}: ${read.outcome} is not a success`);
      assert.doesNotMatch(
        notice.text,
        /^(Approval|The approval) (recorded|withdrawn)/,
        `${action}: ${read.outcome} must not claim the intent`,
      );
      assert.match(notice.text, /was sent/, `${action}: ${read.outcome} says the request went out`);
      assert.match(notice.text, /not shown as/, `${action}: ${read.outcome} names what it is not`);
    }
  }
});

test('a refusal, a failed readback and a refused status are three different sentences', () => {
  const refusalText = 'activation is switched off in this environment (agent_activation_kill_switch)';
  const refused = activationWriteNotice('approve', {
    refused: { message: refusalText, tone: 'warn' },
  });
  const readFailed = activationWriteNotice('approve', {
    verified: verificationOf('approve', { outcome: 'failed' }),
  });
  const stillRefused = activationWriteNotice('approve', {
    verified: verifyActivation('approve', notApproved('owner_consent_not_active')),
  });
  const texts = [refused.text, readFailed.text, stillRefused.text];
  assert.equal(new Set(texts).size, 3, 'the three answers must not collapse into one sentence');
  assert.ok(refused.text.startsWith('The approval was not recorded: '), 'refusal names the act it undid');
  assert.ok(refused.text.includes(refusalText), 'the server sentence is what the owner reads');
  assert.match(stillRefused.text, /still reads this artifact as NOT_APPROVED \(owner_consent_not_active\)/);
  assert.doesNotMatch(readFailed.text, /NOT_APPROVED/, 'no read means no status to quote');
  for (const notice of [refused, readFailed, stillRefused]) assert.notEqual(notice.tone, 'ok');
});

test('a 5xx stays an error where a 409 refusal is a warning', () => {
  const gate = activationWriteNotice('withdraw', {
    refused: { message: 'nope (agent_activation_approval_not_found)', tone: 'warn' },
  });
  const transport = activationWriteNotice('withdraw', {
    refused: { message: 'server error', tone: 'err' },
  });
  assert.equal(gate.tone, 'warn', 'a refusal is a decision, not a defect');
  assert.equal(transport.tone, 'err');
  assert.match(gate.text, /was not withdrawn/);
});

test('only a confirming readback of the same artifact is shown as a success', () => {
  const success = {
    approve: activationWriteNotice('approve', {
      verified: verificationOf('approve', { outcome: 'loaded', state: approved() }),
    }),
    withdraw: activationWriteNotice('withdraw', {
      verified: verificationOf('withdraw', { outcome: 'loaded', state: notApproved() }),
    }),
  };
  assert.equal(success.approve.tone, 'ok');
  assert.match(success.approve.text, /reads back as APPROVED on the server/);
  assert.equal(success.withdraw.tone, 'ok');
  assert.match(success.withdraw.text, /no longer reads back as APPROVED/);

  // The opposite read of each action is the unconfirmed case, not a success.
  assert.equal(
    activationWriteNotice('approve', {
      verified: verificationOf('approve', { outcome: 'loaded', state: notApproved() }),
    }).tone,
    'warn',
  );
  assert.equal(
    activationWriteNotice('withdraw', {
      verified: verificationOf('withdraw', { outcome: 'loaded', state: approved() }),
    }).tone,
    'warn',
  );
});

test('批准→讀回→撤回→讀回, with a switch to B inside the in-flight read', async () => {
  // The same order of calls the handlers make, over a panel that carries the
  // three fields the brief names — status, acknowledgement text, notice — so
  // "A cannot overwrite B" is shown by the values and not only by a boolean.
  const panel = panelReads();
  const screen = { jobId: '', activation: null, ack: '', notice: null };

  // Every render of the panel, in order. An accepted read writes; a dropped one
  // and a refused write do not, which is what the last assertion counts.
  const writes = [];
  const ACKText = (jobId) => `activation text for ${jobId}`;

  const render = (jobId, state) => {
    screen.jobId = jobId;
    screen.activation = state;
    screen.ack = state?.ack_text ?? '';
    writes.push([jobId, state?.activation_status ?? '—']);
  };

  const openActivation = async (jobId, answer) => {
    const result = await panel.open(jobId, answer);
    if (result.outcome === 'loaded') render(jobId, { ...result.state, ack_text: ACKText(jobId) });
    return result;
  };

  // Selecting an artifact is the switching effect — retire the previous read and
  // reset the panel — followed by the read that effect starts.
  const switchTo = async (jobId, answer) => {
    panel.select(jobId);
    screen.notice = null;
    screen.ack = '';
    screen.activation = null;
    return openActivation(jobId, answer);
  };

  // One click: write, then read back, with the panel guarded after each await.
  const run = async (action, jobId, write, readback) => {
    const refusal = await write;
    if (!isDisplayedActivationJob(panel.current, jobId)) return null;
    if (refusal) {
      screen.notice = activationWriteNotice(action, { refused: refusal });
      return screen.notice;
    }
    const read = await openActivation(jobId, readback);
    if (!isDisplayedActivationJob(panel.current, jobId)) return null;
    screen.notice = activationWriteNotice(action, { verified: verificationOf(action, read) });
    return screen.notice;
  };

  // 1. A opens as NOT_APPROVED, with A's own acknowledgement text on screen.
  const opened = await switchTo(A, Promise.resolve({ job_id: A, state: notApproved() }));
  assert.equal(verificationOf('approve', opened).state, 'not_confirmed');
  assert.equal(screen.jobId, A);
  assert.equal(screen.ack, ACKText(A));

  // 2. A is approved, and the owner selects B while the readback is in flight.
  const write = defer();
  const readback = defer();
  const inflight = run('approve', A, write.promise, readback.promise);
  write.resolve(null);
  await tick();
  assert.equal(panel.current.jobId, A, 'the readback is running while A is on screen');
  await switchTo(B, Promise.resolve({ job_id: B, state: notApproved() }));
  const bStatus = screen.activation.activation_status;
  const bAck = screen.ack;
  const bSeq = panel.current.seq;
  readback.resolve({ job_id: A, state: approved() });
  const notice = await inflight;
  assert.equal(notice, null, 'A has no panel to notify once the owner left it');
  assert.equal(screen.jobId, B, 'B is still what the owner is looking at');
  assert.equal(screen.activation.activation_status, bStatus, 'B status unchanged');
  assert.equal(screen.ack, bAck, 'B acknowledgement text unchanged');
  assert.equal(screen.notice, null, 'A success notice never appeared on B');
  assert.equal(isDisplayedActivationJob(panel.current, A), false);
  assert.equal(panel.current.seq, bSeq, "A's late answer retired nothing: it took no slot");
  // What it would have said had the guard not held:
  const wouldSay = activationWriteNotice('approve', {
    verified: verificationOf('approve', { outcome: 'stale' }),
  });
  assert.equal(wouldSay.tone, 'warn');
  assert.match(wouldSay.text, /moved to another artifact/);

  // 3. Back to A: the write resolves and this artifact reads back APPROVED, so
  //    the approval is the only state this panel has been allowed to name.
  await switchTo(A, Promise.resolve({ job_id: A, state: notApproved() }));
  const confirmed = await run(
    'approve',
    A,
    Promise.resolve(null),
    Promise.resolve({ job_id: A, state: approved() }),
  );
  assert.equal(confirmed.tone, 'ok');
  assert.match(confirmed.text, /reads back as APPROVED on the server/);
  assert.equal(screen.activation.activation_status, ACTIVATION_APPROVED);

  // 4. The write the gate refuses (409 with a named code) never reaches a
  //    readback, and says what the server said instead of a status.
  const refused = await run(
    'approve',
    A,
    Promise.resolve({
      message: 'the security switch is on (agent_execution_kill_switch_global)',
      tone: 'warn',
    }),
    Promise.resolve({ job_id: A, state: approved() }),
  );
  assert.equal(refused.tone, 'warn');
  assert.match(refused.text, /The approval was not recorded: the security switch is on/);
  assert.equal(screen.activation.activation_status, ACTIVATION_APPROVED, 'no read, no rewrite');

  // 5. A write that resolved but whose readback is still refused is shown as
  //    unconfirmed — the brief's 請求已提交但狀態未確認 — not as approved.
  const unverified = await run(
    'approve',
    A,
    Promise.resolve(null),
    Promise.resolve({ job_id: A, state: notApproved('owner_consent_not_active') }),
  );
  assert.equal(unverified.tone, 'warn');
  assert.match(
    unverified.text,
    /still reads this artifact as NOT_APPROVED \(owner_consent_not_active\)/,
  );
  assert.doesNotMatch(unverified.text, /Approval recorded/);

  // 6. The withdrawal, read back through the same door the approval used.
  const withdrawn = await run(
    'withdraw',
    A,
    Promise.resolve(null),
    Promise.resolve({ job_id: A, state: notApproved() }),
  );
  assert.equal(withdrawn.tone, 'ok');
  assert.match(withdrawn.text, /no longer reads back as APPROVED/);
  assert.equal(screen.activation.activation_status, ACTIVATION_NOT_APPROVED);

  // 7. The same race on the other door: a withdrawal whose readback lands after
  //    the owner has already moved to B must not read as withdrawn on B either.
  await switchTo(A, Promise.resolve({ job_id: A, state: approved() }));
  const revokeWrite = defer();
  const revokeRead = defer();
  const inflightRevoke = run('withdraw', A, revokeWrite.promise, revokeRead.promise);
  revokeWrite.resolve(null);
  await tick();
  assert.equal(panel.current.jobId, A, 'the withdrawal readback is running while A is on screen');
  await switchTo(B, Promise.resolve({ job_id: B, state: notApproved() }));
  revokeRead.resolve({ job_id: A, state: notApproved() });
  assert.equal(await inflightRevoke, null, 'the withdrawal has no panel to notify either');
  assert.equal(screen.jobId, B);
  assert.equal(screen.notice, null, 'the withdrawal success sentence never reached B');

  assert.deepEqual(
    writes,
    [
      [A, ACTIVATION_NOT_APPROVED],
      [B, ACTIVATION_NOT_APPROVED],
      [A, ACTIVATION_NOT_APPROVED],
      [A, ACTIVATION_APPROVED],
      [A, ACTIVATION_NOT_APPROVED],
      [A, ACTIVATION_NOT_APPROVED],
      [A, ACTIVATION_APPROVED],
      [B, ACTIVATION_NOT_APPROVED],
    ],
    'one render per accepted read: the two dropped answers and the refusal wrote nothing',
  );
  assert.deepEqual(
    writes.filter(([jobId]) => jobId === B),
    [
      [B, ACTIVATION_NOT_APPROVED],
      [B, ACTIVATION_NOT_APPROVED],
    ],
    'B was written only by the switches that moved to it, never by an answer about A',
  );
});

// --- positive control: the journey above can see the defect it fixes --------

test('without the guard, an answer about A writes itself over B', async () => {
  // The pre-fix shape of `loadActivation`, kept here so the assertions in the
  // journey test are shown to be sensitive rather than merely passing: a read
  // writes whatever arrives last, whoever it was asked about.
  const screen = { jobId: '', activation: null, ack: '' };
  const render = (jobId, state) => {
    screen.activation = state;
    screen.ack = `activation text for ${state.job_id}`;
  };
  const preFixRead = async (answer) => {
    const state = await answer;
    render(state.job_id, state);
    return state;
  };

  screen.jobId = A;
  const pending = defer();
  const inflight = preFixRead(pending.promise);
  screen.jobId = B;
  await preFixRead(Promise.resolve({ job_id: B, state: notApproved() }));
  const bStatus = screen.activation.state.activation_status;
  const bAck = screen.ack;
  pending.resolve({ job_id: A, state: approved() });
  await inflight;

  assert.equal(screen.jobId, B, 'the owner is still looking at B');
  assert.equal(
    screen.activation.job_id,
    A,
    'and the panel is showing A — which is exactly what the guard now refuses',
  );
  assert.notEqual(screen.activation.state.activation_status, bStatus, "B's status was overwritten");
  assert.equal(screen.ack, `activation text for ${A}`, "B's acknowledgement text was overwritten");
  assert.notEqual(screen.ack, bAck);
});

// --- wiring: does the page go through the functions tested above? -----------

function source(path) {
  assert.ok(existsSync(path), `${path} is not in the checkout to read`);
  return readFileSync(path, 'utf8');
}

const page = source(join(FRONTEND, 'src/app/agent-strategy/page.tsx'));
const api = source(join(FRONTEND, 'src/lib/api.ts'));

test('the page reads through the guard instead of writing what arrives', () => {
  assert.match(
    page,
    /import \{[\s\S]*?acceptActivationRead,[\s\S]*?beginActivationRead,[\s\S]*?isDisplayedActivationJob,[\s\S]*?\} from '@\/lib\/activationPanel';/,
  );
  // Every write that follows an awaited read is behind the guard.
  assert.match(page, /const read = beginActivationRead\(jobId, activationReadRef\.current\);/);
  // A read is not even started for an artifact that is no longer on screen: it
  // would retire the read that artifact is still waiting for.
  assert.match(
    page,
    /async \(jobId: string\): Promise<ActivationReadResult> => \{\s*if \(!isDisplayedActivationJob\(activationReadRef\.current, jobId\)\) \{/,
  );
  assert.match(page, /!acceptActivationRead\(\s*activationReadRef\.current,\s*read,\s*payload\?\.requirements\?\.job_id \?\? jobId,?\s*\)/);
  assert.match(page, /const stillOnActivationJob = \(jobId: string\) =>\s*isDisplayedActivationJob\(activationReadRef\.current, jobId\);/);
  // Selecting another artifact retires the in-flight read before anything else.
  const effect = /useEffect\(\(\) => \{\s*activationReadRef\.current = beginActivationRead\(\s*activationJobId,[\s\S]*?\}, \[activationJobId, loadActivation\]\);/.exec(page);
  assert.ok(effect, 'the switching effect no longer retires the previous read first');
  assert.match(effect[0], /setActivation\(null\);/);
  assert.match(effect[0], /setActivationAck\(''\);/);
  assert.match(effect[0], /setActivationNotice\(null\);/);
});

test('no notice claims an approval or a withdrawal before the readback says so', () => {
  for (const action of ['approve', 'withdraw']) {
    assert.equal(
      [...page.matchAll(new RegExp(`activationWriteNotice\\('${action}'`, 'g'))].length,
      2,
      `${action}: one refusal notice and one verified notice expected`,
    );
  }
  assert.match(page, /verified: verificationOf\('approve', read\)/);
  assert.match(page, /verified: verificationOf\('withdraw', read\)/);
  // The unconditional successes round 21 shipped are gone.
  assert.doesNotMatch(page, /Approval recorded\. The status below/);
  assert.doesNotMatch(page, /Approval withdrawn\. The approval record is kept/);
  // and neither handler reaches a notice without passing the guard twice.
  const approve = /const onApproveActivation = async \(\) => \{[\s\S]*?\n  \};/.exec(page);
  const revoke = /const onRevokeActivation = async \(\) => \{[\s\S]*?\n  \};/.exec(page);
  assert.ok(approve && revoke, 'the handlers are not in the shape this test reads');
  for (const handler of [approve[0], revoke[0]]) {
    assert.equal(
      [...handler.matchAll(/if \(!stillOnActivationJob\(jobId\)\) return;/g)].length,
      2,
      'both the write and the readback must be gated',
    );
    assert.doesNotMatch(handler, /tone: 'ok'/, 'the page writes no success tone itself');
  }
  // Positive control: that count is a measurement, not a phrase that always
  // matches. One guard removed from the same text counts as one.
  const oneGuardShort = approve[0].replace('if (!stillOnActivationJob(jobId)) return;', '');
  assert.equal(
    [...oneGuardShort.matchAll(/if \(!stillOnActivationJob\(jobId\)\) return;/g)].length,
    1,
    'a handler with one guard missing must not pass the count above',
  );
});

test('the client still calls the three formal doors and no local assertion', () => {
  assert.match(api, /jobActivation: \(jobId: string\) =>\s*apiFetch\(`\/api\/v1\/agent-strategy\/training\/jobs\/\$\{jobId\}\/activation`\)/);
  assert.match(api, /revokeJobActivation: \(jobId: string, reason\?: string \| null\) =>/);
  assert.match(page, /await api\.agentStrategy\.activateJob\(jobId, \{/);
  assert.match(page, /await api\.agentStrategy\.revokeJobActivation\(\s*jobId,\s*revokeActivationReason\.trim\(\) \|\| null,?\s*\)/);
  // The panel never invents a status: only the server payload is written.
  assert.doesNotMatch(page, /setActivation\(\{ *state: *\{ *activation_status: '/);
});

test('the status tokens the panel compares are the ones the gate emits', () => {
  const gate = source(join(REPO, 'backend/app/services/model_activation.py'));
  const emitted = [...gate.matchAll(/^(\w+) = "(APPROVED|NOT_APPROVED)"$/gm)].map((m) => m[2]);
  assert.deepEqual(
    [...new Set(emitted)].sort(),
    [ACTIVATION_APPROVED, ACTIVATION_NOT_APPROVED].sort(),
    'the panel pins exactly the vocabulary the gate writes',
  );
});
