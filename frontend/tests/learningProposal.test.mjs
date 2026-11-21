// §十 P-5: `/learning` is the first frontend consumer of the learning API, so
// the affordances the page shows are only honest if they are the affordances the
// server will accept. Every predicate below is the backend's own, read out of
// the source at the cited line — the test pins the contract in both directions
// so a change to a gate has to be made in the API and in the page together.
//
// Two absences matter as much as the presences:
//   - there is no validate endpoint (the backtest runs only inside the
//     scheduler), so the page must not offer one;
//   - deploy writes status + timestamp and nothing else, and no trading path
//     reads the proposal, so the page must not promise a behaviour change.
//
// Run with: node22 --test tests/learningProposal.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const lib = await import('../src/lib/learningProposal.ts');
const {
  DEPLOYABLE_EVIDENCE_STATUS,
  decisionFor,
  evidenceStatusLabel,
  evidenceStatusOf,
  jobResultSummaryOf,
  learningStatusLabel,
  refusalText,
  validationErrorOf,
  validationMetricsOf,
  validationPassedOf,
} = lib;

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const backend = (rel) => src(`../../backend/${rel}`);

const learningApi = backend('app/api/learning.py');
const scheduler = backend('app/workers/learning_scheduler.py');
const patternAnalysis = backend('app/services/pattern_analysis.py');
const apiClient = src('../src/lib/api.ts');
const page = src('../src/app/learning/page.tsx');
const nav = src('../src/components/shell/AppShell.tsx');

const PROPOSAL_FIELDS = [
  'id',
  'user_id',
  'learning_job_id',
  'rule_id',
  'exit_id',
  'instrument_id',
  'interval_minutes',
  'baseline_exit_config',
  'proposed_exit_config',
  'rationale_json',
  'validation_result',
  'status',
  'created_at',
  'validated_at',
  'deployed_at',
];

const proposal = (over = {}) => ({
  id: 'p-1',
  user_id: 'u-1',
  learning_job_id: 'j-1',
  rule_id: 'rule_mr_rsi',
  exit_id: 'exit_mean_reversion_2',
  instrument_id: 'CRYPTO:KRAKEN:SOL-USD',
  interval_minutes: 1440,
  baseline_exit_config: { target_atr: '1.2', stop_atr: '1.8', time_stop_bars: 6 },
  proposed_exit_config: { target_atr: '1.4', stop_atr: '1.8', time_stop_bars: 6 },
  rationale_json: { pattern: 'low_win_rate', evidence_status: 'hypothesis' },
  validation_result: null,
  status: 'pending',
  created_at: '2026-11-20T04:00:00+00:00',
  validated_at: null,
  deployed_at: null,
  ...over,
});

// --- the ladder the backend actually runs ------------------------------------

test('the scheduler hands over pending + hypothesis, and approve is the only evidence producer', () => {
  // Scheduler: every proposal it creates starts pending…
  assert.ok(scheduler.includes('status="pending",'), 'scheduler no longer creates pending proposals');
  // …and a hypothesis is explicitly held at pending even when validation passed.
  assert.equal(scheduler.match(/if evidence_status == "hypothesis":/g).length, 1);
  assert.equal(
    scheduler.match(/evidence_status = adjustment\.get\("evidence_status", "hypothesis"\)/g).length,
    1,
    'the default evidence status is no longer hypothesis',
  );
  // API: approve is the only statement that writes validated_candidate, and
  // deploy is the only place it is required (`:312` write, `:434` gate).
  assert.equal(
    learningApi.match(/new_rationale\["evidence_status"\] = "validated_candidate"/g).length,
    1,
    'evidence_status="validated_candidate" is no longer written in exactly one place',
  );
  assert.equal(learningApi.match(/!= "validated_candidate"/g).length, 1);
  assert.equal(patternAnalysis.match(/validated_candidate/g)?.length || 0, 0);
});

test('a pending proposal is approvable only when validation passed', () => {
  assert.equal(learningApi.match(/if not validation_result\.get\("passed", False\):/g).length, 2);
  assert.equal(decisionFor(proposal({ status: 'pending', validation_result: { passed: true } })).kind, 'approve');
  assert.equal(decisionFor(proposal({ status: 'pending', validation_result: null })).kind, 'none');
  assert.equal(decisionFor(proposal({ status: 'pending', validation_result: { passed: false } })).kind, 'none');
});

test('the blocked reason for a pending proposal names the missing validation, not a button', () => {
  const decision = decisionFor(proposal({ validation_result: { passed: false, error: 'no_baseline_run' } }));
  assert.equal(decision.kind, 'none');
  assert.ok(decision.reason.includes('no_baseline_run'), decision.reason);
  assert.ok(decision.reason.includes('validation_result 只能由排程研究自己寫入'), decision.reason);
  // The page must not offer an action the API does not have.
  assert.ok(!/\/validate|validation\/run|run-validation/.test(page), 'the page invented a validate endpoint');
  assert.ok(!apiClient.includes('/learning/analysis'), 'the client wired the on-demand analysis call');
});

test('a validated proposal deploys only with validated_candidate evidence and a passing validation', () => {
  const ok = proposal({
    status: 'validated',
    rationale_json: { evidence_status: DEPLOYABLE_EVIDENCE_STATUS },
    validation_result: { passed: true },
  });
  assert.equal(decisionFor(ok).kind, 'deploy');

  const badEvidence = decisionFor(
    proposal({ status: 'validated', validation_result: { passed: true } }),
  );
  assert.equal(badEvidence.kind, 'none');
  assert.ok(badEvidence.reason.includes('hypothesis'), badEvidence.reason);

  const badValidation = decisionFor(
    proposal({
      status: 'validated',
      rationale_json: { evidence_status: DEPLOYABLE_EVIDENCE_STATUS },
      validation_result: { passed: false },
    }),
  );
  assert.equal(badValidation.kind, 'none');
  assert.ok(badValidation.reason.includes('再檢查一次'), badValidation.reason);
});

test('deployed, rejected and unrecognised statuses are terminal on the page', () => {
  for (const status of ['deployed', 'rejected']) {
    assert.equal(decisionFor(proposal({ status, validation_result: { passed: true } })).kind, 'none');
  }
  const odd = decisionFor(proposal({ status: 'queued_for_review', validation_result: { passed: true } }));
  assert.equal(odd.kind, 'none');
  assert.ok(odd.reason.includes('queued_for_review'), odd.reason);
});

// --- reading the two JSON columns the way the server reads them --------------

test('evidence status defaults to unknown exactly like rationale_json.get(…, "unknown")', () => {
  // Two reads of the same key: the approve event's before-value (`:311`) and
  // the deploy gate (`:433`). Both use the same default, so both agree.
  assert.equal(learningApi.match(/\.get\("evidence_status", "unknown"\)/g).length, 2);
  assert.equal(evidenceStatusOf(proposal({ rationale_json: null })), 'unknown');
  assert.equal(evidenceStatusOf(proposal({ rationale_json: {} })), 'unknown');
  assert.equal(evidenceStatusOf(proposal({ rationale_json: { evidence_status: null } })), 'unknown');
  assert.equal(evidenceStatusOf(proposal({ rationale_json: { evidence_status: '  ' } })), 'unknown');
  assert.equal(evidenceStatusOf(proposal()), 'hypothesis');
});

test('passed is truthiness, not === true, so a truthy non-boolean still allows the rung', () => {
  for (const truthy of [true, 1, 'yes']) {
    assert.equal(
      validationPassedOf(proposal({ validation_result: { passed: truthy } })),
      true,
      `${JSON.stringify(truthy)} should count as passed`,
    );
  }
  for (const falsy of [false, 0, '', null, undefined]) {
    assert.equal(validationPassedOf(proposal({ validation_result: { passed: falsy } })), false);
  }
  assert.equal(validationErrorOf(proposal({ validation_result: { error: 'unknown_rule' } })), 'unknown_rule');
  assert.equal(validationErrorOf(proposal({ validation_result: null })), '');
});

test('metrics the refusing branches never wrote stay absent instead of becoming zero', () => {
  const refused = validationMetricsOf(proposal({ validation_result: { passed: false, error: 'no_baseline_run' } }));
  assert.equal(refused.length, 3);
  for (const metric of refused) {
    assert.equal(metric.before, null, `${metric.key} before invented a value`);
    assert.equal(metric.after, null, `${metric.key} after invented a value`);
  }
  // No validation record at all is a different fact from a recorded refusal:
  // there is no comparison to lay out, so the page renders 「尚未驗證」 and this
  // returns no rows rather than three dashes.
  assert.equal(validationMetricsOf(proposal({ validation_result: null })).length, 0);
  assert.equal(validationMetricsOf(proposal({ validation_result: undefined })).length, 0);

  const compared = validationMetricsOf(
    proposal({
      validation_result: {
        passed: true,
        sharpe_before: -0.31,
        sharpe_after: 0.02,
        max_dd_before: 12.5,
        max_dd_after: 9.75,
        win_rate_before: 0.62,
        win_rate_after: 0.44,
      },
    }),
  );
  const byKey = Object.fromEntries(compared.map((m) => [m.key, m]));
  assert.equal(byKey.sharpe.before, -0.31);
  assert.equal(byKey.max_dd.after, 9.75);
  assert.equal(byKey.win_rate.before, 0.62);
  // A stored non-finite number must not be printed as a measurement.
  const broken = validationMetricsOf(proposal({ validation_result: { sharpe_before: 'Infinity' } }));
  assert.equal(broken.find((m) => m.key === 'sharpe').before, null);
});

test('the result_json line uses only the counts each producer writes', () => {
  const research = { result_json: { results: [{}], errors: [], total_combos: 12, successful: 11, failed: 1 } };
  assert.match(jobResultSummaryOf(research), /成功 11／失敗 1，本批共 12 組/);
  const analysis = { result_json: { analysis: { recommendations: [] }, proposals_created: 2 } };
  assert.match(jobResultSummaryOf(analysis), /提出 2 個提案/);
  assert.equal(jobResultSummaryOf({ result_json: null }), '');
  assert.equal(jobResultSummaryOf({ result_json: { note: 'nothing counted' } }), '');
  assert.equal(jobResultSummaryOf({ result_json: { successful: 3 } }), '');
});

// --- the four server refusals stay four separate sentences -------------------

test('every refusal reason key the API can raise renders distinctly', () => {
  for (const key of ['proposal_not_found', 'validation_not_passed', 'proposal_not_approvable', 'proposal_not_validated']) {
    assert.ok(learningApi.includes(`"${key}"`), `${key} no longer in the API`);
  }
  const texts = [
    refusalText(404, { reason: 'proposal_not_found', proposal_id: 'abc' }),
    refusalText(409, { reason: 'validation_not_passed', current_status: 'pending' }),
    refusalText(409, { reason: 'proposal_not_approvable', current_status: 'validated' }),
    refusalText(409, { reason: 'proposal_not_validated', current_status: 'pending' }),
    refusalText(409, { reason: 'proposal_not_validated', evidence_status: 'hypothesis', allowed_status: 'validated_candidate' }),
  ];
  for (const text of texts) assert.ok(text.startsWith('拒絕：') || text.includes('找不到這個提案'), text);
  assert.equal(new Set(texts).size, texts.length, 'two refusals collapsed into one message');
  assert.ok(texts[0].includes('abc'));
  assert.ok(texts[2].includes('validated'));
  assert.ok(texts[3].includes('pending'));
  assert.ok(texts[4].includes('hypothesis'));
  // The two proposal_not_validated shapes must not be read as one case.
  assert.notEqual(texts[3], texts[4]);
  assert.ok(texts[3].includes('status'), texts[3]);
  assert.ok(texts[4].includes('evidence_status'), texts[4]);
});

test('an unrecognised refusal yields nothing so the caller keeps the server message', () => {
  assert.equal(refusalText(500, { reason: 'database_unavailable' }), '');
  assert.equal(refusalText(409, 'a plain string'), '');
  assert.equal(refusalText(409, [{ msg: 'field required' }]), '');
  assert.equal(refusalText(409, null), '');
});

test('the evidence and status vocabularies cover what the backend writes', () => {
  // The deploy message text defines all five non-deployable values; each needs a label.
  for (const value of ['unknown', 'descriptive', 'hypothesis', 'insufficient', 'unsupported', 'validated_candidate']) {
    assert.notEqual(evidenceStatusLabel(value), `未分類（${value}）`, `${value} has no label`);
  }
  assert.equal(evidenceStatusLabel('brand_new_value'), '未分類（brand_new_value）');
  for (const value of ['pending', 'running', 'completed', 'failed', 'validated', 'deployed', 'rejected']) {
    assert.notEqual(learningStatusLabel(value), `未分類（${value}）`, `${value} has no label`);
  }
});

// --- the serialized row is what the page reads ------------------------------

test('the proposal fields the page declares are the fields the API serializes', () => {
  // `_serialize_job` is defined first (`app/api/learning.py:81`), so the
  // proposal body runs from `:96` to the first route decorator after it.
  const jobStart = learningApi.indexOf('def _serialize_job');
  const proposalStart = learningApi.indexOf('def _serialize_proposal');
  assert.ok(jobStart >= 0 && proposalStart > jobStart, 'the two serializers changed order or name');
  const jobSerializer = learningApi.slice(jobStart, proposalStart);
  const serializer = learningApi.slice(proposalStart, learningApi.indexOf('@router.get("/jobs")'));
  assert.ok(serializer.length > 0, 'the proposal serializer slice is empty');
  for (const field of PROPOSAL_FIELDS) {
    assert.ok(serializer.includes(`"${field}":`), `_serialize_proposal no longer emits ${field}`);
  }
  for (const field of ['status', 'job_type', 'config_json', 'result_json', 'error_details', 'created_at', 'completed_at']) {
    assert.ok(jobSerializer.includes(`"${field}":`), `_serialize_job no longer emits ${field}`);
  }
  // The write endpoints answer with the row they re-read, which is what the
  // page replaces its own copy with.
  assert.equal(learningApi.match(/"proposal": _serialize_proposal\(proposal\)/g).length, 2);
});

// Five of the six declared routes are actionable by a page; `/analysis` is a
// read endpoint the page does not use, and the sixth is counted but not called.
test('the client calls the five real routes and the page is reachable from the nav', () => {
  for (const route of [
    '/api/v1/learning/jobs?',
    '/api/v1/learning/proposals?',
    "'/api/v1/learning/jobs/trigger'",
    '/approve`',
    '/deploy`',
  ]) {
    assert.ok(apiClient.includes(route), `api.ts no longer builds ${route}`);
  }
  const declared = learningApi.match(/@router\.(get|post)\("/g) || [];
  assert.equal(declared.length, 6, 'the learning API route count changed');
  assert.ok(nav.includes("'/learning'"), 'the tools menu lost the /learning entry');
  assert.ok(page.includes("data-testid=\"learning-proposal-row\""));
  assert.ok(page.includes('decision.kind'));
});
