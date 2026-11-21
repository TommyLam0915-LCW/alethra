// P-3b: five surfaces render a stored `error_details`. P-3 closed the `/inference`
// card by gating it through `describeStoredJobFailure()`, whose contract is
// "recognised text verbatim, anything else null so the caller shows a safe
// prompt". Applied unchanged to the other sites that prompt would have erased a
// reason the backend already recorded — `error_safety.py` scrubs paths, DSNs and
// credentials precisely so the surviving summary *can* be shown.
//
// So the fix here is provenance, not suppression: platform-authored text stays
// as-is, `PersonalTrainingRefused` payloads are read as the structured refusal
// they are, and anything else keeps its words and gains a marker saying the
// platform did not author them. No recorded reason can leave the page.
//
// Run with: node22 --test tests/storedFailureProvenance.test.mjs
// No new dependency: Node's built-in runner + type stripping.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  CONTROLLED_JOB_MESSAGES,
  UNVERIFIED_REASON_SUFFIX,
  describeStoredJobFailure,
  parseStructuredRefusal,
  renderStoredFailure,
} from '../src/lib/mlFailureMessages.ts';

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const gateSource = src('../src/lib/mlFailureMessages.ts');
const backend = (rel) => src(`../../backend/${rel}`);

// --- nothing that was recorded can be hidden ----------------------------------

test('an unrecognised stored reason is still shown, with its provenance marked', () => {
  // What `safe_error_message()` actually produces: scrubbed, bounded, arbitrary.
  const stored = "connection to server at <path> failed: couldn't connect to host";
  assert.equal(describeStoredJobFailure(stored), null, 'the P-3 gate returns null for this');
  const rendered = renderStoredFailure(stored);
  assert.ok(rendered.includes(stored), 'the recorded reason was dropped from the page');
  assert.ok(
    rendered.includes(UNVERIFIED_REASON_SUFFIX),
    'a sentence the platform never wrote is presented as if it had',
  );
});

test('a blank stored reason renders blank rather than a fabricated explanation', () => {
  assert.equal(renderStoredFailure(null), '');
  assert.equal(renderStoredFailure(''), '');
  assert.equal(renderStoredFailure('   '), '');
});

test('the platform authored text renders unchanged, so no marker is added', () => {
  const authored = 'Some attempts did not run; completed results are kept.';
  assert.equal(renderStoredFailure(authored), authored);
  assert.ok(!renderStoredFailure(authored).includes(UNVERIFIED_REASON_SUFFIX));
});

// --- the whitelist is the backend\'s own text, not a guess --------------------

test('every whitelisted literal is a string a backend service writes', () => {
  // Producers of `error_details`, all found by searching the writers under
  // backend/app. Each entry must appear in one of them as a plain literal.
  const producers = [
    'app/services/inference.py',
    'app/services/robustness.py',
    'app/services/research_runs.py',
    'app/services/job_control.py',
    'app/services/training.py',
    'app/ml/trainer.py',
  ].map((rel) => ({ rel, body: backend(rel) }));

  // Implicit multi-line concatenation is the one form a source scan must undo.
  const collapsed = new Map(
    producers.map(({ rel, body }) => [
      rel,
      body.replace(/"\s*\n\s*"/g, '').replace(/'\s*\n\s*'/g, ''),
    ]),
  );

  for (const literal of CONTROLLED_JOB_MESSAGES) {
    const writers = producers
      .filter(({ rel }) => collapsed.get(rel).includes(JSON.stringify(literal).slice(1, -1)))
      .map(({ rel }) => rel);
    assert.ok(
      writers.length > 0,
      `"${literal}" is quoted to customers but no backend service writes it`,
    );
  }
});

// --- structured refusals are read as data, not as text ------------------------

const REFUSAL = JSON.stringify({
  code: 'PERSONAL_TRAINING_INSUFFICIENT_CONFIRMED_DECISIONS',
  message_zh: '可用決策還不足，請先累積至少 20 筆已確認決策。',
  details: { have: 7, need: 20, feature_names: ['ret_1d', 'rsi_14'], user_id: 'u-1' },
});

test('a refusal payload shows its own Chinese text and code, never details', () => {
  const rendered = renderStoredFailure(REFUSAL);
  assert.ok(rendered.includes('可用決策還不足'), 'the message the backend authored is missing');
  assert.ok(rendered.includes('PERSONAL_TRAINING_INSUFFICIENT_CONFIRMED_DECISIONS'));
  // `details` carries internal names, ids and counts: it stays in the DB.
  assert.ok(!rendered.includes('ret_1d'), 'internal feature names reached the page');
  assert.ok(!rendered.includes('u-1'), 'an internal id reached the page');
  assert.ok(!rendered.startsWith('{'), 'the raw JSON blob is quoted to the customer');
  assert.ok(!rendered.includes(UNVERIFIED_REASON_SUFFIX), 'backend text marked as unverified');
});

test('parseStructuredRefusal accepts only the shape the backend writes', () => {
  const accepted = parseStructuredRefusal(REFUSAL);
  assert.equal(accepted.code, 'PERSONAL_TRAINING_INSUFFICIENT_CONFIRMED_DECISIONS');
  assert.equal(accepted.messageZh, '可用決策還不足，請先累積至少 20 筆已確認決策。');

  const notRefusals = [
    null,
    undefined,
    '',
    'a plain scrubbed sentence',
    '{"code":"X"}', // no message_zh
    '{"message_zh":"y"}', // no code
    '{"code":"","message_zh":"y"}', // empty code
    '{"code":"X","message_zh":""}', // empty message
    '{"code":42,"message_zh":"y"}', // code not a string
    '{"code":"X","message_zh":{"nested":"y"}}', // message not a string
    '[{"code":"X","message_zh":"y"}]', // array, not object
    '{"code":"X","message_zh":"y"', // truncated JSON
    'null',
    '123',
    '"just a json string"',
  ];
  for (const raw of notRefusals) {
    assert.equal(parseStructuredRefusal(raw), null, `accepted ${JSON.stringify(raw)} as a refusal`);
  }
});

test('a malformed refusal blob degrades to the marked raw text, not to silence', () => {
  const broken = '{"code":"X","message_zh":';
  const rendered = renderStoredFailure(broken);
  assert.ok(rendered.includes(broken));
  assert.ok(rendered.includes(UNVERIFIED_REASON_SUFFIX));
});

// --- wiring: each site reads the field through the shared helpers -------------

const SITES = [
  {
    name: 'robustness study header',
    file: '../src/components/experiments/RobustnessPanel.tsx',
    mustMatch: /· \{renderStoredFailure\(selected\.error_details\)\}/,
  },
  {
    name: 'robustness attempt cell',
    file: '../src/components/experiments/RobustnessPanel.tsx',
    mustMatch: /renderStoredFailure\(a\.error_details\)/,
  },
  {
    name: 'research study record',
    file: '../src/components/experiments/ResearchRecordPanel.tsx',
    mustMatch: /\{renderStoredFailure\(study\.error_details\)\}/,
  },
  {
    name: 'research run detail',
    file: '../src/app/experiments/[id]/runs/[runId]/page.tsx',
    mustMatch: /\{renderStoredFailure\(run\.error_details\)\}/,
  },
  {
    name: 'personal training job card',
    file: '../src/components/training/PersonalStrategyLearningPanel.tsx',
    mustMatch: /parseStructuredRefusal\(job\.error_details\)/,
  },
];

for (const site of SITES) {
  test(`${site.name} goes through the shared helpers`, () => {
    const body = src(site.file);
    assert.match(body, /from '@\/lib\/mlFailureMessages'/, 'the site does not import the gate');
    assert.match(body, site.mustMatch, 'the site does not read the field through the gate');
  });
}

test('no site interpolates a stored error_details as if it were display text', () => {
  // `{x.error_details}` is the raw echo. Wrapping it in a call ends with `)`,
  // which the trailing `\s*}` cannot match, so the gate is not satisfied by
  // `renderStoredFailure(x.error_details)`.
  for (const { file } of SITES) {
    const body = src(file);
    const raw = /\{\s*[\w.]+\.error_details\s*\}/.exec(body);
    assert.equal(raw && raw[0], null, `${file} renders ${raw && raw[0]} directly`);
  }
});

test('the research run detail keeps the reason readable without expanding it', () => {
  const body = src('../src/app/experiments/[id]/runs/[runId]/page.tsx');
  assert.match(body, /data-testid="run-error-details"/);
});

test('the personal training card distinguishes refusal from crash', () => {
  const body = src('../src/components/training/PersonalStrategyLearningPanel.tsx');
  assert.match(body, /data-testid="pl-job-failure"/);
  // A refusal is the platform declining, not a job dying: the labels differ.
  assert.match(body, /拒絕原因：/);
  assert.match(body, /失敗原因：/);
});

// --- the gate itself stays a whitelist, not a pattern matcher -----------------

test('the gate matches stored text exactly rather than by keyword', () => {
  // A paraphrase must not be treated as platform-authored.
  assert.equal(
    describeStoredJobFailure('Some attempts did not run; completed results are kept'),
    null,
    'a near-miss is labelled as platform text',
  );
  assert.ok(
    renderStoredFailure('Some attempts did not run; completed results are kept').includes(
      UNVERIFIED_REASON_SUFFIX,
    ),
  );
});

test('the whitelist lives in the gate module only', () => {
  const declarations = gateSource.match(/CONTROLLED_JOB_MESSAGES\s*=|CONTROLLED_JOB_MESSAGES/g) || [];
  assert.ok(
    declarations.length >= 2,
    'CONTROLLED_JOB_MESSAGES is no longer exported for the parity test to read',
  );
  // No page keeps a private list of acceptable reasons.
  for (const { file } of SITES) {
    assert.ok(!src(file).includes('CONTROLLED_JOB_MESSAGES'), `${file} re-implements the list`);
  }
});
