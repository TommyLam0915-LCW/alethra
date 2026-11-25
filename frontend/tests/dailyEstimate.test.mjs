// Unit coverage for the daily model-estimate card's three-way answer.
//
// Run with: node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyEstimate.test.mjs
//
// Two kinds of claim live here, and they are not the same weight. The behaviour
// tests assert what the card says for each shape the status route can hand it —
// an estimate this run produced, an estimate the window had already been billed
// for, and a row whose verdict is not in the billing record. The field lists are
// read out of the backend service source rather than typed by hand, so a key
// renamed on the server side fails this file instead of drifting. The wiring
// section at the bottom reads the panel's source text and proves the card goes
// through the function tested above — it is not rendered-UI evidence.
//
// A third kind arrived with round 18: the browser journey's badge oracle is sliced
// out of `e2e/daily-estimate-journey.spec.ts` and executed here (see
// 「the browser spec answers its own badge table」). It is neither a UI result nor a
// re-statement of the product rule — it is the file that will have to answer for a
// screen being run against a table, without a screen.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
  CONSENT_PROVENANCE_ZH,
  ESTIMATE_STATE_LABELS_ZH,
  LINEAGE_ATTACH_LABEL_ZH,
  LINEAGE_ATTACH_NOTE_ZH,
  LINEAGE_HOLD_NOTE_ZH,
  LINEAGE_NO_SOURCE_NOTE_ZH,
  LINEAGE_REFUSAL_LABELS_ZH,
  LINEAGE_REFUSAL_REASONS,
  LINEAGE_STATE_LABELS_ZH,
  NEVER_ESTIMATED_NOTE_ZH,
  PRESS_NOTICES_ZH,
  PRESS_WINDOW_NOTE_ZH,
  UNCONFIRMED_NOTE_ZH,
  WITHDRAWAL_TIMING_ZH,
  estimateDisplay,
  estimateSourceFromStatus,
  estimateState,
  failureNotice,
  lineageRefusalOf,
  lineageState,
  pressNotice,
  pressOutcome,
} from '../src/lib/dailyEstimate.ts';

import {
  SOURCE_STATE_LABELS,
  SOURCE_STATE_MISSING_NOTE,
  SOURCE_STATE_UNREADABLE_NOTE,
  inferenceSourceState,
} from '../src/lib/decisionLineage.ts';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const LIB = readFileSync(`${HERE}../src/lib/dailyEstimate.ts`, 'utf8');
const PANEL = readFileSync(
  `${HERE}../src/components/training/DailyEstimatePanel.tsx`,
  'utf8'
);
const SERVICE = readFileSync(
  `${HERE}../../backend/app/services/daily_estimates.py`,
  'utf8'
);
const JOURNEY = readFileSync(
  `${HERE}../e2e/daily-estimate-journey.spec.ts`,
  'utf8'
);
const PERSONALIZATION = readFileSync(
  `${HERE}../../backend/app/api/personalization.py`,
  'utf8'
);
const LINEAGE = readFileSync(
  `${HERE}../../backend/app/services/decision_lineage.py`,
  'utf8'
);
const SIMULATIONS_PY = readFileSync(
  `${HERE}../../backend/app/api/simulations.py`,
  'utf8'
);
const SIMULATION_PAGE = readFileSync(
  `${HERE}../src/app/experiments/[id]/simulation/page.tsx`,
  'utf8'
);
const WORKSPACE_PANEL = readFileSync(
  `${HERE}../src/components/workspace/OrderPreviewPanel.tsx`,
  'utf8'
);
// The two doors that can carry a claimed inference lineage. Both submit the same
// field to the same validator, so every assertion below runs against both — a
// fix that lands on one page is half a fix here.
const DOORS = [
  ['the replay page', SIMULATION_PAGE, 'sim'],
  ['the workspace panel', WORKSPACE_PANEL, 'ws'],
];
const PAPER_PAGE = readFileSync(
  `${HERE}../src/app/paper-portfolio/[id]/page.tsx`,
  'utf8'
);
const API_CLIENT = readFileSync(`${HERE}../src/lib/api.ts`, 'utf8');
const PAPER_API = readFileSync(
  `${HERE}../../backend/app/api/paper_portfolio.py`,
  'utf8'
);

function segment(text, startMarker, endMarker) {
  const start = text.indexOf(startMarker);
  assert.notEqual(start, -1, `${startMarker} not found`);
  const rest = text.slice(start);
  const end = rest.indexOf(endMarker);
  assert.notEqual(end, -1, `${endMarker} not found after ${startMarker}`);
  return rest.slice(0, end);
}

// The keys of a Python dict literal, taken from the source that writes it. Both
// producers of an estimate entry are read this way: ``_estimate_one`` returns
// what the run itself learned, and the billing-ledger rebuild is the second one
// — which is why the card has a reused state at all. After #227 the ledger read
// groups every bill by skill once (``billed_estimates_by_skill``) and the window
// filter is a pass over that grouping, so the entry's keys are taken from where
// the dict is actually written.
function quotedKeys(text) {
  return [...new Set([...text.matchAll(/"([a-z_][a-z0-9_]*)"\s*:/g)].map((m) => m[1]))];
}

const FRESH_KEYS = quotedKeys(
  segment(
    segment(SERVICE, 'def _estimate_one(', 'def _estimates_for_owner'),
    'return {',
    '\n    }\n'
  )
);
const LEDGER_KEYS = quotedKeys(
  segment(
    segment(SERVICE, 'def billed_estimates_by_skill(', '\n\ndef settled_estimates'),
    'grouped.setdefault(_canonical_skill_id(skill), []).append(',
    '\n        )\n'
  )
);
const ESTIMATE_FIELDS = [...new Set([...FRESH_KEYS, ...LEDGER_KEYS])];

// ``LastEstimate`` as the panel declares it, with the optional marker kept so a
// field only one producer emits cannot be treated as always present.
function interfaceFields(name) {
  const body = segment(PANEL, `interface ${name} {`, '\n}');
  return body
    .split('\n')
    .map((line) => line.match(/^ {2}([a-z_][a-z0-9_]*)(\??):/))
    .filter(Boolean)
    .map((m) => ({ key: m[1], optional: Boolean(m[2]) }));
}
const LAST_ESTIMATE = interfaceFields('LastEstimate');
const LAST_ESTIMATE_KEYS = LAST_ESTIMATE.map((f) => f.key);

// One row, spelled the way the producer spells it: every key it emits is
// present (null where this test does not care), and nothing else is. An
// override naming a key neither producer emits is a bug in this file, so it
// throws rather than testing a shape the server cannot send.
function row(keys, values = {}) {
  const out = {};
  for (const key of keys) out[key] = key in values ? values[key] : null;
  for (const [key, value] of Object.entries(values)) {
    if (!(key in out)) {
      if (!ESTIMATE_FIELDS.includes(key)) throw new Error(`${key} is emitted by neither producer`);
      out[key] = value;
    }
  }
  return out;
}

const freshRun = (overrides = {}) =>
  row(FRESH_KEYS, {
    instrument_id: 'US:AAPL',
    inference_id: '11111111-1111-4111-8111-111111111111',
    as_of_date: '2026-11-19',
    feature_bar_date: '2026-07-18',
    data_age_days: 124,
    max_data_age_days: 5,
    provider_id: 'twelve_data',
    data_nature: 'live',
    predicted_action: 'buy',
    ...overrides,
  });

const ledgerRow = (overrides = {}) =>
  row(LEDGER_KEYS, {
    instrument_id: 'US:AAPL',
    inference_id: '11111111-1111-4111-8111-111111111111',
    as_of_date: '2026-11-19',
    feature_bar_date: '2026-07-18',
    billed_at: '2026-11-19T05:00:03+00:00',
    verdict_durable: false,
    ...overrides,
  });

// ---------------------------------------------------------------------------
// the two ends of one contract
// ---------------------------------------------------------------------------

test('the card reads only fields the backend emits, and marks the one-sided ones optional', () => {
  // What the card prints, taken from both ends of the frontend: the panel's own
  // ``state.last_estimate.x`` reads and the lib's ``estimate.x`` reads. A key
  // renamed on the server side fails here instead of drifting into a blank cell.
  const read = [
    ...[...PANEL.matchAll(/state\.last_estimate\.([a-z_][a-z0-9_]*)/g)].map((m) => m[1]),
    ...[...LIB.matchAll(/estimate\.([a-z_][a-z0-9_]*)/g)].map((m) => m[1]),
  ];
  assert.ok(read.length >= 11, `read the card's fields, not: ${read.join(',')}`);
  for (const key of read) {
    assert.ok(LAST_ESTIMATE_KEYS.includes(key), `the card reads ${key}, which it does not model`);
    assert.ok(ESTIMATE_FIELDS.includes(key), `the card reads ${key}, which neither producer emits`);
  }
  // The three keys that carry the state itself have to be modelled even where
  // the card would render fine without them: dropping ``verdict_durable`` from
  // the type is what let a billed-and-reused estimate read as a blank one.
  for (const key of ['predicted_action', 'verdict_durable', 'billed_at']) {
    assert.ok(LAST_ESTIMATE_KEYS.includes(key), `${key} is the state, and the card does not model it`);
    assert.ok(LEDGER_KEYS.includes(key), `${key} is not on the billing row this card is about`);
  }
  // A field the ledger rebuild does not emit exists on the card only because a
  // run's own entry carries it; declaring it required is the claim that broke.
  const oneSided = FRESH_KEYS.filter((k) => !LEDGER_KEYS.includes(k));
  assert.ok(oneSided.length > 0, 'the ledger row is not a subset of the fresh row');
  for (const field of LAST_ESTIMATE) {
    if (oneSided.includes(field.key)) {
      assert.ok(field.optional, `${field.key} is emitted only by the run and is required in the card`);
    }
  }
  // Dropping an emitted field is allowed only when nothing prints it.
  for (const key of LEDGER_KEYS) {
    if (!LAST_ESTIMATE_KEYS.includes(key)) {
      assert.ok(
        !read.includes(key),
        `the ledger emits ${key} and the card neither models nor reads it, while printing it`
      );
    }
  }
});

// ---------------------------------------------------------------------------
// which of the three states the card is describing
// ---------------------------------------------------------------------------

test('an estimate this run produced reads as a verdict with a dated market', () => {
  const view = estimateDisplay(freshRun());
  assert.equal(view.verdict, ' · buy');
  assert.equal(view.reused, false);
  assert.equal(view.reusedNote, null, 'a fresh estimate cannot be described as reused');
  assert.equal(view.dataAge, '124 天前');
});

test('a row rebuilt off the bill reads as the window\'s existing estimate, not a failure', () => {
  const view = estimateDisplay(ledgerRow());
  assert.equal(view.reused, true);
  // The verdict is genuinely not in the billing row; the card says that instead
  // of showing nothing where a verdict should be.
  assert.equal(view.verdict, '（此列未持有結論）');
  assert.equal(view.dataAge, '資料 age 不在帳本列上');
  const note = view.reusedNote;
  assert.ok(note, 'the reused state has to say something');
  // The three facts the brief asked for: the estimate is the window's existing
  // one, the inference_id shown is the original one, and nothing was charged
  // again.
  assert.match(note, /這個視窗已經存在的那一次估計/);
  assert.match(note, /原本的 inference_id/);
  assert.match(note, /沒有再扣一次額度/);
  // And where the verdict actually lives, so the blank has an explanation.
  assert.match(note, /不在帳本列上/);
  assert.match(note, /計費時點 2026-11-19T05:00:03\+00:00/);
  assert.doesNotMatch(note, /未執行|推論失敗|額度已用完/);
});

test('the reused note is not one of the sentences the refusal door hands out', () => {
  const values = [
    ...PERSONALIZATION.matchAll(/^\s{4}"[a-z_][a-z0-9_]*":\s*"([^"]*)",/gm),
  ].map((m) => m[1]);
  assert.ok(values.length > 10, 'the refusal message map was not readable');
  const note = estimateDisplay(ledgerRow()).reusedNote;
  for (const message of values) {
    assert.ok(!note.includes(message), `the reused card reuses refusal copy: ${message}`);
  }
});

test('durability, not the missing verdict, decides whether the card says "reused"', () => {
  // A null verdict with no durability claim is still "this row does not hold one"
  // — the card must not upgrade it into the billed-and-reused story, because that
  // story claims a charge happened.
  const unknown = estimateDisplay({ ...freshRun({ predicted_action: null, data_age_days: null }) });
  assert.equal(unknown.verdict, '（此列未持有結論）');
  assert.equal(unknown.reused, false);
  assert.equal(unknown.reusedNote, null);
  assert.equal(unknown.dataAge, '無法計算資料age');

  // Same shape with durability spelled true: today a run's own entry leaves the
  // field off entirely (covered above), and either way the card must not put the
  // billed-and-reused wording on it, because that wording claims a charge.
  const durable = estimateDisplay(freshRun({ predicted_action: null, data_age_days: null, verdict_durable: true }));
  assert.equal(durable.reused, false);
  assert.equal(durable.dataAge, '無法計算資料age');
});

test('the three states cannot be collapsed into one sentence', () => {
  const fresh = estimateDisplay(freshRun());
  const reused = estimateDisplay(ledgerRow());
  const unreadable = estimateDisplay(freshRun({ predicted_action: null, data_age_days: null }));
  const ages = new Set([fresh.dataAge, reused.dataAge, unreadable.dataAge]);
  assert.equal(ages.size, 3, [...ages].join(' | '));
  const verdicts = new Set([fresh.verdict, reused.verdict, unreadable.verdict]);
  // reused and unreadable share the honest "no verdict here" heading on purpose;
  // what separates them is the note, which only the billed row gets.
  assert.equal(verdicts.size, 2);
  assert.equal(fresh.reusedNote, null);
  assert.equal(unreadable.reusedNote, null);
  assert.ok(reused.reusedNote);
});

test('zero days is a measurement, not a missing value', () => {
  // A falsy-collapsed age would print the "cannot compute" fallback for a bar
  // that is exactly today old — the freshest case the service can report.
  assert.equal(estimateDisplay(freshRun({ data_age_days: 0 })).dataAge, '0 天前');
  assert.equal(estimateDisplay(freshRun({ data_age_days: 1 })).dataAge, '1 天前');
});

// ---------------------------------------------------------------------------
// wiring: the card renders through the function tested above
// ---------------------------------------------------------------------------

test('the panel routes every estimate through the derivation and renders all three states', () => {
  assert.match(PANEL, /import \{[^}]*\bestimateDisplay\b[^}]*\} from '@\/lib\/dailyEstimate'/);
  // One derivation per skill card, taken before the markup so the card cannot
  // print a second, hand-worded version of one of the states.
  assert.match(
    PANEL,
    /const display = state\.last_estimate\s*\n?\s*\?\s*estimateDisplay\(state\.last_estimate\)\s*\n?\s*:\s*null;/
  );
  for (const field of ['display.verdict', 'display.reusedNote', 'display.dataAge']) {
    assert.ok(PANEL.includes(`{${field}}`), field);
  }
  // Each state is addressable in the DOM under its own test id, so an acceptance
  // pass can assert which state it is looking at rather than which sentence.
  for (const marker of [
    'daily-estimate-last-${state.skill_unit_id}',
    'daily-estimate-reused-${state.skill_unit_id}',
    'daily-estimate-refusal-${state.skill_unit_id}',
  ]) {
    assert.ok(PANEL.includes(marker), marker);
  }
  // The reused sentence and the age fallbacks live in one place only. Duplicating
  // them in the component is how the two halves of the card start disagreeing.
  assert.doesNotMatch(PANEL, /沒有再扣一次額度|資料 age 不在帳本列上|無法計算資料age/);
  assert.doesNotMatch(PANEL, /verdict_durable === false/);
  assert.doesNotMatch(PANEL, /predicted_action == null/);
  // The original inference id is printed on the reused card as well as the fresh
  // one: naming it is the only way the owner can look the charge up.
  assert.match(PANEL, /inference_id \{state\.last_estimate\.inference_id\}/);
});

// ---------------------------------------------------------------------------
// one screen, five answers: 已沿用 / 本次估計 / 本次拒絕 / 執行狀態未確認 / 沒有估計
// ---------------------------------------------------------------------------

test('estimateState keeps the five answers apart', () => {
  const states = [
    { has_estimate: true, reused: true, refused: false, window_claimed: true },
    { has_estimate: true, reused: false, refused: false, window_claimed: true },
    { has_estimate: false, reused: false, refused: true, window_claimed: true },
    { has_estimate: false, reused: false, refused: false, window_claimed: true },
    { has_estimate: false, reused: false, refused: false, window_claimed: false },
  ].map(estimateState);
  assert.deepEqual(states, [
    'carried_over',
    'estimated',
    'refused',
    'unconfirmed',
    'never_estimated',
  ]);
  const labels = states.map((state) => ESTIMATE_STATE_LABELS_ZH[state]);
  assert.equal(new Set(labels).size, 5, labels.join(' | '));
});

test('a claim row separates nobody-ran from ran-and-left-nothing-behind', () => {
  // The two blanks the brief asked to keep apart. ``window_claimed`` says a job
  // row opened this window; nothing else about it is known, so it can repaint a
  // blank as 「執行狀態未確認」 — and nothing more.
  assert.equal(
    estimateState({ has_estimate: false, reused: false, refused: false, window_claimed: true }),
    'unconfirmed',
  );
  assert.equal(
    estimateState({ has_estimate: false, reused: false, refused: false, window_claimed: false }),
    'never_estimated',
  );
  // It never outranks a fact: a bill, a reused row or a refusal is each a result
  // the platform can name, and the claim adds nothing to them.
  assert.equal(
    estimateState({ has_estimate: true, reused: false, refused: false, window_claimed: true }),
    'estimated',
  );
  assert.equal(
    estimateState({ has_estimate: true, reused: true, refused: false, window_claimed: true }),
    'carried_over',
  );
  assert.equal(
    estimateState({ has_estimate: false, reused: false, refused: true, window_claimed: true }),
    'refused',
  );
});

test('an estimate row outranks a refusal instead of the two cancelling out', () => {
  // One window can serve one instrument of a skill account and refuse another.
  // The card has an estimate to name, so it says 已沿用／本次估計; letting the
  // refusal repaint it as 「沒有估計」 would erase a charge that happened.
  assert.equal(estimateState({ has_estimate: true, reused: false, refused: true }), 'estimated');
  assert.equal(estimateState({ has_estimate: true, reused: true, refused: true }), 'carried_over');
});

test('the browser spec answers its own badge table, and its oracle is not the panel', async () => {
  // The round-18 clause has a second half. 「現有 spec 不得只用產品相同 helper 算
  // 答案、再拿答案驗證產品」 is fixed in e2e/daily-estimate-journey.spec.ts by a
  // reader written inside that file — but a browser expectation nobody can run is
  // an expectation nobody can falsify either, and this lane may not start a
  // server. So the spec's own pure region (its raw-field reader, its five literals,
  // its hand-written answer table) is sliced out of the file, compiled by the same
  // type-stripping this run already uses, and executed here. A wrong precedence or
  // a mistyped expectation fails without a browser, and the extraction anchors are
  // markers rather than line numbers: move the code and this node says so instead
  // of quietly testing a copy that no longer matches.
  const region = segment(JOURNEY, 'interface EstimateState {', 'const HAND_WRITTEN_BADGES');
  const table = segment(JOURNEY, 'const HAND_WRITTEN_BADGES', '\n};');
  const file = `${tmpdir()}/alethra-journey-badge-oracle-${process.pid}.ts`;
  writeFileSync(
    file,
    `${region + table}\n};\n\nexport { badgeOfLedger, allLedgerShapes, HAND_WRITTEN_BADGES, WINDOW_BADGE_ZH };\n`,
    'utf8'
  );
  let oracle;
  try {
    oracle = await import(pathToFileURL(file).href);
  } finally {
    unlinkSync(file);
  }
  const shapes = oracle.allLedgerShapes();
  const hand = oracle.HAND_WRITTEN_BADGES;

  // The table and the generator name the same twelve field combinations, in both
  // directions: a shape nobody hand-wrote an answer for, and an answer nobody can
  // reach, are each a hole in the oracle.
  assert.deepEqual(
    shapes.map((shape) => shape.key).sort(),
    Object.keys(hand).sort(),
    '規格的欄位組合與手寫答案表不同調'
  );
  assert.equal(shapes.length, 12, '四欄位應產生 12 種有意義的組合');
  for (const { key, row } of shapes) {
    assert.equal(
      oracle.badgeOfLedger(row),
      hand[key],
      `${key}（估計=${key[0]} durable=${key[1]} 拒絕=${key[2]} 開窗=${key[3]}）`
    );
  }
  // Five answers, five words, each reached by at least one shape.
  assert.deepEqual(
    [...new Set(Object.values(hand))].sort(),
    ['carried_over', 'estimated', 'never_estimated', 'refused', 'unconfirmed']
  );
  assert.equal(new Set(Object.values(oracle.WINDOW_BADGE_ZH)).size, 5);
  // The copy and the product still mean the same five words. This is the drift gate
  // the spec asserts at run time; here it is a lane result, so a rephrasing is a
  // red node rather than a surprise inside a browser run nobody has authorised.
  for (const badge of Object.keys(oracle.WINDOW_BADGE_ZH)) {
    assert.equal(
      ESTIMATE_STATE_LABELS_ZH[badge],
      oracle.WINDOW_BADGE_ZH[badge],
      `「${badge}」的產品措辭與瀏覽器規格的抄件不同：先決定哪一方要改`
    );
  }
  // A charge never reads as a blank, in either spelling of the charge.
  for (const { key, row } of shapes.filter((shape) => shape.key[0] === '1')) {
    assert.ok(
      ['estimated', 'carried_over'].includes(oracle.badgeOfLedger(row)),
      `${key}: 已計費的列被推成另一種終態`
    );
  }

  // And the gate on the file itself: what the DOM is compared against must be this
  // file's own literal, not the module the panel rendered it from. Anything else
  // would be the tautology the clause names, however well the paragraphs around it
  // are written.
  const badgeNode = segment(
    JOURNEY,
    "test('每一列的狀態徽章由帳本原始欄位獨立推導",
    '// The oracle itself, held to a table written before it ran.'
  );
  assert.ok(
    badgeNode.includes('.toHaveText(WINDOW_BADGE_ZH[expected])'),
    '徽章節點不再拿本檔自己的抄件比對畫面'
  );
  assert.doesNotMatch(
    badgeNode,
    /toHaveText\(ESTIMATE_STATE_LABELS_ZH|toContain\(.*ESTIMATE_STATE_LABELS_ZH/,
    '徽章節點的預期又變回了產品模組的輸出'
  );
  assert.match(badgeNode, /badgeOfLedger\(state\)/, '徽章節點不再自己讀原始欄位');
  // The product helper may appear in this file, but only where it is being
  // cross-examined — and the file has to say which of the two it is.
  assert.equal(
    JOURNEY.split('estimateState({').length - 1,
    1,
    '產品 estimateState() 在瀏覽器規格裡被叫了不止一次：只有對照那一段可以叫它'
  );
});

test('the never-estimated note borrows neither the refusal nor the reused wording', () => {
  assert.ok(NEVER_ESTIMATED_NOTE_ZH.includes('沒有任何一次估計'));
  assert.ok(NEVER_ESTIMATED_NOTE_ZH.includes('還沒有產生過任何估計'));
  assert.doesNotMatch(NEVER_ESTIMATED_NOTE_ZH, /本次拒絕|已沿用|沒有再扣一次額度/);
});

test('the unconfirmed note claims a start, not a live process', () => {
  // The sentence has to hold both halves of what the row proves and what it does
  // not: a job opened the window, and no signal in the platform says whether the
  // worker is still running. Lease/heartbeat is unimplemented, so saying so is
  // the only honest version of the second half.
  assert.ok(UNCONFIRMED_NOTE_ZH.includes('有開工紀錄'));
  assert.ok(UNCONFIRMED_NOTE_ZH.includes('不證明它還在執行'));
  assert.ok(UNCONFIRMED_NOTE_ZH.includes('尚未實作的 lease'));
  // No liveness claim in any form, and no age-or-string death judgement either.
  assert.doesNotMatch(UNCONFIRMED_NOTE_ZH, /仍在執行|正在執行|還在執行中/);
  assert.ok(UNCONFIRMED_NOTE_ZH.includes('也不會用這列存在多久'));
  // The three actions the display must not take on a historical running row.
  for (const phrase of ['不會因此退款', '不會釋放預留', '不會把終態改寫成失敗']) {
    assert.ok(UNCONFIRMED_NOTE_ZH.includes(phrase), phrase);
  }
  // And the re-press contract, stated on the same card as the blank.
  assert.ok(UNCONFIRMED_NOTE_ZH.includes('同一視窗可以再要求一次'));
  assert.ok(UNCONFIRMED_NOTE_ZH.includes('不會再扣一次額度'));
  assert.doesNotMatch(UNCONFIRMED_NOTE_ZH, /本次拒絕|已沿用/);
});

test('the withdrawal notice says what a withdrawal cannot reach', () => {
  assert.ok(WITHDRAWAL_TIMING_ZH.includes('已經在執行中'));
  assert.ok(WITHDRAWAL_TIMING_ZH.includes('不會因撤回而退額'));
  // The absolute promise this replaced. The route cannot recall an estimate that
  // already ran, so no sentence the withdraw button writes may say nothing else
  // will ever be billed.
  assert.doesNotMatch(WITHDRAWAL_TIMING_ZH, /不會替你執行任何推論/);
});

test('consent provenance matches what the two consent routes actually enforce', () => {
  // Read from the doors rather than typed: granting has a provenance gate,
  // withdrawal deliberately does not.
  const grant = segment(
    PERSONALIZATION,
    '@router.put("/personalization/daily-estimate/consent")',
    '@router.delete("/personalization/daily-estimate/consent")'
  );
  const withdraw = segment(
    PERSONALIZATION,
    '@router.delete("/personalization/daily-estimate/consent")',
    '@router.get("/personalization/daily-estimate/consent")'
  );
  assert.match(grant, /if actor_type != HUMAN:/);
  assert.doesNotMatch(withdraw, /actor_type != HUMAN/);
  assert.ok(CONSENT_PROVENANCE_ZH.includes('API 金鑰（代理）提出每日估計同意會被拒絕'));
  assert.ok(CONSENT_PROVENANCE_ZH.includes('代理不得替本人同意'));
});

test('the panel renders the states, the timing and the provenance through the lib', () => {
  assert.match(PANEL, /import \{[^}]*\bestimateState\b[^}]*\} from '@\/lib\/dailyEstimate'/);
  assert.match(PANEL, /const windowState = estimateState\(\{/);
  assert.ok(PANEL.includes('{ESTIMATE_STATE_LABELS_ZH[windowState]}'));
  for (const marker of [
    'daily-estimate-state-${state.skill_unit_id}',
    'daily-estimate-none-${state.skill_unit_id}',
    'daily-estimate-unconfirmed-${state.skill_unit_id}',
    'daily-estimate-consent-provenance',
  ]) {
    assert.ok(PANEL.includes(marker), marker);
  }
  // The three sentences live in the lib only; a hand-worded second copy in the
  // component is how the card and the tested strings start disagreeing.
  assert.doesNotMatch(PANEL, /撤回只對「尚未開始估計」|同意只能由本人以密碼登入的工作階段作出/);
  assert.ok(PANEL.includes('WITHDRAWAL_TIMING_ZH +'), 'the withdrawal notice is not the tested one');
  assert.ok(PANEL.includes('{CONSENT_PROVENANCE_ZH}'));
  assert.ok(PANEL.includes('{UNCONFIRMED_NOTE_ZH}'), 'the unconfirmed block is not the tested sentence');
  assert.doesNotMatch(PANEL, /每日服務現在不會替你執行任何推論/);
  // Nothing on this page may claim the run is still going, because the platform
  // has no signal that would let it. The old chip 「每日執行中」 was exactly such
  // a claim about a schedule, so it names the switch instead of a process.
  assert.doesNotMatch(PANEL, /每日執行中|仍在執行|正在執行|執行中的估計|還在做/);
  assert.ok(PANEL.includes('每日排程已開啟'), 'the enabled chip no longer says what it means');
  // A refusal is this run's refusal, and the card says which book decides
  // payment rather than implying the refusal did.
  assert.match(PANEL, /本次拒絕原因/);
  assert.doesNotMatch(PANEL, /未執行原因/);
});

test('the claim fact the unconfirmed state rests on is read from the server', () => {
  // ``window_claimed`` cannot be inferred client-side from a job's age or from
  // the word ``running`` — the brief forbids both. It is a key the status read
  // returns, so the panel models it and the service emits it, or the state is
  // unreachable rather than guessed.
  const from = SERVICE.indexOf('def daily_estimate_status(');
  assert.notEqual(from, -1, 'the status reader is not where this test expects');
  const rest = SERVICE.slice(from);
  const nextDef = rest.indexOf('\ndef ');
  const body = nextDef === -1 ? rest : rest.slice(0, nextDef);
  // Derived from the existence of a job row for this owner and this job type —
  // not from an age, a status string, or the summary.
  assert.match(body, /window_claimed = bool\(jobs\)/);
  for (const key of ['window', 'window_claimed']) {
    assert.match(
      body,
      new RegExp(`"${key}": ${key},`),
      `the status read does not emit ${key} from its own window, so the card would be inventing it`,
    );
  }
  const skillState = interfaceFields('SkillState').map((field) => field.key);
  for (const key of ['window', 'window_claimed']) {
    assert.ok(skillState.includes(key), `SkillState does not model ${key}`);
  }
  assert.match(PANEL, /window_claimed: state\.window_claimed === true,/);
});

// ---------------------------------------------------------------------------
// the lineage picker at both manual decision doors: one estimate row as one
// decision's origin (the replay page and the workspace order panel)
// ---------------------------------------------------------------------------

test('lineageState never offers to attach a source the door would refuse', () => {
  assert.equal(lineageState({ has_source: true, selected: true, action: 'hold' }), 'hold_no_lineage');
  assert.equal(lineageState({ has_source: true, selected: true, action: 'buy' }), 'attached');
  assert.equal(lineageState({ has_source: true, selected: false, action: 'sell' }), 'unselected');
  assert.equal(lineageState({ has_source: false, selected: true, action: 'buy' }), 'no_source');
  const labels = Object.values(LINEAGE_STATE_LABELS_ZH);
  assert.equal(new Set(labels).size, labels.length);
});

test('the hold note names the other door, and that door exists and says this reason', () => {
  assert.match(SIMULATIONS_PY, /"reason": "inference_needs_decision_row"/);
  assert.ok(LINEAGE_HOLD_NOTE_ZH.includes('inference_needs_decision_row'));
  assert.ok(LINEAGE_HOLD_NOTE_ZH.includes('POST /paper-portfolio/{portfolio_id}/decisions'));
  assert.match(PAPER_API, /@router\.post\("\/\{portfolio_id\}\/decisions"/);
  // The route's own pointer and the card's must name the same door; a card that
  // sends the owner to a route that does not exist is worse than no pointer.
  const pointer = SIMULATIONS_PY.match(/POST (\/api\/v1)?\/paper-portfolio\/\{portfolio_id\}\/decisions/);
  assert.ok(pointer, 'the backend hold refusal no longer points at a decisions route');
});

test('the attach note claims verification, not authorization', () => {
  assert.match(LINEAGE, /def validate_linked_inference/);
  assert.doesNotMatch(LINEAGE_ATTACH_NOTE_ZH, /表示已授權|因此可以執行|所以合法/);
  assert.ok(LINEAGE_ATTACH_NOTE_ZH.includes('不代表'));
});

test('the replay page sends the field only through the derivation, to a client that knows it', () => {
  assert.match(SIMULATION_PAGE, /import \{[^}]*\blineageState\b[^}]*\} from '@\/lib\/dailyEstimate'/);
  assert.match(
    SIMULATION_PAGE,
    /if \(\s*lineageState\(\{[\s\S]{0,200}?\}[^)]*\) === 'attached' &&\s*estimateSource\s*\)\s*\{\s*p\.linked_inference_id = estimateSource\.inference_id;/
  );
  // The retry payload type carries the field too: a retry that silently dropped
  // the claimed lineage would be a different request than the one the backend
  // may already have accepted.
  assert.match(SIMULATION_PAGE, /const \[pendingDecision, setPendingDecision\] = useState<ManualDecisionPayload \| null>/);
  assert.match(SIMULATION_PAGE, /interface ManualDecisionPayload \{[\s\S]*linked_inference_id\?: string \| null;/);
  const manual = segment(API_CLIENT, 'manualDecision: (sessionId: string, data: {', '}) =>');
  assert.match(manual, /linked_inference_id\?: string \| null;/);
  // Read back from what the server wrote, not from what this browser pressed.
  assert.ok(SIMULATION_PAGE.includes('{d.decision_detail.linked_inference_id}'));
  assert.match(SIMULATIONS_PY, /'linked_inference_id': \(/);
});

test('the shared vocabulary names exactly the lineage refusals the backend can answer', () => {
  const raised = [
    ...new Set(
      [...`${LINEAGE}\n${SIMULATIONS_PY}`.matchAll(/"reason":\s*"(inference_[a-z_]+)"/g)].map((m) => m[1])
    ),
  ];
  assert.ok(raised.length >= 5, `only ${raised.length} lineage reasons readable`);
  // The module value, not a re-typed list: what the doors consult is this set.
  assert.deepEqual([...LINEAGE_REFUSAL_REASONS].sort(), raised.sort());
  // Neither door keeps a private copy — a local list is the drift this move removed.
  for (const [name, source] of [
    ['the replay page', SIMULATION_PAGE],
    ['the workspace panel', WORKSPACE_PANEL],
  ]) {
    assert.doesNotMatch(source, /const LINEAGE_REFUSAL_REASONS/, `${name} re-declared the refusal set`);
    assert.doesNotMatch(source, /inference_needs_decision_row/, `${name} re-typed a refusal reason`);
  }
});

test('lineageRefusalOf recognises a refusal by its reason, not by one status', () => {
  // The validator answers inference_not_found with 404 and the rest with 400; a
  // door that matched on 400 alone would offer a retry of a request the server
  // has already refused for a reason the payload cannot fix.
  assert.deepEqual(lineageRefusalOf(404, { reason: 'inference_not_found', message: '找不到該推論' }), {
    reason: 'inference_not_found',
    message: '找不到該推論',
  });
  assert.deepEqual(lineageRefusalOf(400, { reason: 'inference_skill_mismatch', message: '不是這個技能帳號' }), {
    reason: 'inference_skill_mismatch',
    message: '不是這個技能帳號',
  });
  assert.deepEqual(lineageRefusalOf(409, { reason: 'inference_needs_decision_row' }), {
    reason: 'inference_needs_decision_row',
    message: 'inference_needs_decision_row',
  });
  // Not a lineage refusal: other statuses, other reasons, and shapes with no reason.
  assert.equal(lineageRefusalOf(500, { reason: 'inference_not_found' }), null);
  assert.equal(lineageRefusalOf(200, { reason: 'inference_not_found' }), null);
  assert.equal(lineageRefusalOf(undefined, { reason: 'inference_not_found' }), null);
  assert.equal(lineageRefusalOf(400, { reason: 'artifact_missing', message: 'x' }), null);
  assert.equal(lineageRefusalOf(400, { detail: 'not a structured body' }), null);
  assert.equal(lineageRefusalOf(400, null), null);
  assert.equal(lineageRefusalOf(400, undefined), null);
});

// The two refusals `check_inference_version` raises are the version leg of the
// same claim the other five check. They arrived in the backend before this file
// could name them, which is how a 400 that can never pass kept its
// 「Retry (same payload)」 affordance: an unrecognised reason falls through to
// the generic branch. These nodes hold the two halves of the fix — the reasons
// are recognised, and each reads as itself.
test('the version leg of the lineage is recognised at the shared mapping', () => {
  const versionLeg = segment(LINEAGE, 'def check_inference_version', 'def validate_linked_inference');
  const raised = [
    ...new Set([...versionLeg.matchAll(/"reason":\s*"(inference_[a-z_]+)"/g)].map((m) => m[1])),
  ];
  assert.deepEqual(
    raised.sort(),
    ['inference_version_mismatch', 'inference_version_unnamed'],
    'the backend version leg no longer raises exactly the two refusals this node guards'
  );
  for (const reason of raised) {
    assert.ok(LINEAGE_REFUSAL_REASONS.has(reason), `${reason} is not in the shared refusal set`);
  }
});

test('each version-leg refusal carries its own sentence, not a shared one', () => {
  const mismatch = lineageRefusalOf(400, {
    reason: 'inference_version_mismatch',
    message:
      'Linked inference was produced by a model trained from a different strategy version ' +
      'than the one this decision names.',
  });
  const unnamed = lineageRefusalOf(400, {
    reason: 'inference_version_unnamed',
    message: 'The linked inference does not name the strategy version its model was trained from.',
  });
  // Both are recognised (so neither door offers a retry of a request the server
  // has already refused for a reason the payload cannot fix) ...
  assert.ok(mismatch && unnamed);
  // ... the server sentence survives extraction untouched for whoever reports it ...
  assert.match(mismatch.message, /^Linked inference was produced/);
  // ... and each reason renders the sentence that describes *that* reason.
  assert.equal(mismatch.message_zh, LINEAGE_REFUSAL_LABELS_ZH.inference_version_mismatch);
  assert.equal(unnamed.message_zh, LINEAGE_REFUSAL_LABELS_ZH.inference_version_unnamed);
  assert.notEqual(mismatch.message_zh, unnamed.message_zh);
  assert.match(mismatch.message_zh, /另一個模型版本/);
  assert.match(unnamed.message_zh, /沒有記錄/);
  // Both say what the door did with the request, because both doors write
  // nothing: a refusal that reads like a partial success is the failure the
  // attach note above promises will not happen.
  for (const refusal of [mismatch, unnamed]) {
    assert.match(refusal.message_zh, /沒有寫入任何紀錄/);
    assert.match(refusal.message_zh, /取消勾選來源/);
  }
});

test('a recognised reason with no sentence, and an unknown code, keep the old fallback', () => {
  // An ordinary 400 for another lineage reason must not gain a locally written
  // sentence: the door still shows the server's own words plus the reason code.
  const plain = lineageRefusalOf(400, {
    reason: 'inference_skill_mismatch',
    message: 'Linked inference belongs to a different skill account.',
  });
  assert.deepEqual(plain, {
    reason: 'inference_skill_mismatch',
    message: 'Linked inference belongs to a different skill account.',
  });
  assert.equal('message_zh' in plain, false);
  // A code this module does not name is not a lineage refusal at all, even when
  // its prefix is one letter off from a reason that is.
  for (const reason of [
    'inference_version_drift',
    'inference_version',
    'inference_version_mismatched',
    'version_mismatch',
  ]) {
    assert.equal(lineageRefusalOf(400, { reason, message: 'x' }), null, `${reason} was recognised`);
  }
  // Every sentence belongs to a recognised reason — copy for a reason no door
  // classifies is dead text that still reads as a contract.
  for (const reason of Object.keys(LINEAGE_REFUSAL_LABELS_ZH)) {
    assert.ok(LINEAGE_REFUSAL_REASONS.has(reason), `${reason} has copy but is not a refusal`);
  }
});

test('estimateSourceFromStatus only says "no source" from a payload that says so', () => {
  assert.equal(estimateSourceFromStatus(undefined), null);
  assert.equal(estimateSourceFromStatus({}), null);
  assert.equal(estimateSourceFromStatus({ last_estimate: {} }), null);
  assert.equal(estimateSourceFromStatus({ last_estimate: { inference_id: null } }), null);
  const full = estimateSourceFromStatus({
    last_estimate: {
      inference_id: '  7c1d  ',
      instrument_id: null,
      as_of_date: '2026-11-20',
      predicted_action: 'hold',
      verdict_durable: true,
    },
  });
  assert.deepEqual(full, {
    inference_id: '  7c1d  ',
    instrument_id: null,
    as_of_date: '2026-11-20',
    predicted_action: 'hold',
    verdict_durable: true,
  });
  // A row the status route can name but says nothing about is still a source;
  // the missing keys normalise to null/undefined rather than a guessed value.
  assert.deepEqual(estimateSourceFromStatus({ last_estimate: { inference_id: 'a' } }), {
    inference_id: 'a',
    instrument_id: null,
    as_of_date: null,
    predicted_action: null,
    verdict_durable: undefined,
  });
});

test('both manual doors ask the same question in the same words', () => {
  // One definition, consulted by both: the day one door rewords the offer, the
  // other would still be asking about a field whose meaning it redefined. The
  // sentence is written as concatenation in the source, so the count is taken on
  // its opening clause rather than on the assembled value.
  for (const [name, sentence] of [
    ['LINEAGE_ATTACH_LABEL_ZH', LINEAGE_ATTACH_LABEL_ZH],
    ['LINEAGE_NO_SOURCE_NOTE_ZH', LINEAGE_NO_SOURCE_NOTE_ZH],
  ]) {
    const clause = sentence.slice(0, 12);
    assert.equal(LIB.split(clause).length - 1, 1, `${name} is not defined exactly once`);
    for (const [door, source] of DOORS) {
      assert.ok(source.includes(`{${name}}`), `${door} does not render ${name}`);
      assert.ok(!source.includes(clause), `${door} keeps a copy of ${name}`);
    }
  }
  for (const [name, source, prefix] of DOORS) {
    // The same affordances, addressable by the same test-id suffix at each door.
    for (const suffix of ['lineage-picker', 'lineage-attach', 'lineage-state', 'lineage-source', 'lineage-none', 'lineage-note', 'lineage-hold']) {
      assert.ok(source.includes(`${prefix}-${suffix}`), `${name} has no ${prefix}-${suffix}`);
    }
  }
});

test('the workspace panel sends claimed lineage only when the owner ticked it', () => {
  assert.match(
    WORKSPACE_PANEL,
    /import \{[^}]*\blineageState\b[^}]*\} from '@\/lib\/dailyEstimate'/
  );
  assert.match(
    WORKSPACE_PANEL,
    /import \{[^}]*\bestimateSourceFromStatus\b[^}]*\} from '@\/lib\/dailyEstimate'/
  );
  // One derivation, consulted for the payload and for the checkbox alike.
  assert.match(WORKSPACE_PANEL, /const lineage = lineageState\(\{/);
  assert.match(
    WORKSPACE_PANEL,
    /linked_inference_id:\s*\n?\s*lineage === 'attached' && estimateSource \? estimateSource\.inference_id : null,/
  );
  assert.match(
    WORKSPACE_PANEL,
    /if \(review\.linked_inference_id\) payload\.linked_inference_id = review\.linked_inference_id;/
  );
  // The field is a named part of the review, frozen at the press: ticking after
  // reviewing must not ride on the stamp, and neither must a new estimate row.
  assert.match(WORKSPACE_PANEL, /interface ManualReview \{[\s\S]*linked_inference_id: string \| null;/);
  // The stamp moved to src/lib/reviewCurrency.ts this round, so what is pinned here
  // is the call site handing it the same one derivation the checkbox reads. Dropping
  // the term — or feeding it something else — fails this, and so does dropping the
  // server counter, which is the term the review's `expected_version` comes from.
  assert.match(
    WORKSPACE_PANEL,
    /const signature = reviewStamp\(\{[\s\S]*?attachedInferenceId:\s*\n?\s*lineage === 'attached' \? estimateSource\?\.inference_id \?\? null : null,/
  );
  assert.match(WORKSPACE_PANEL, /stateVersion: frozenVersion\(session\),/);
  assert.match(WORKSPACE_PANEL, /expected_version: frozenVersion\(session\)/);
  // Nothing is auto-attached: no code path can tick the box, and the source is
  // re-read (and so re-asked) whenever the account or instrument changes.
  assert.doesNotMatch(WORKSPACE_PANEL, /setLineageSelected\(true\)/);
  assert.match(WORKSPACE_PANEL, /const \[lineageSelected, setLineageSelected\] = useState\(false\)/);
  assert.doesNotMatch(SIMULATION_PAGE, /setLineageSelected\(true\)/);
  // The receipt shows what the ledger holds, taken from the response body.
  assert.ok(
    WORKSPACE_PANEL.includes('manualResult.decision?.decision_detail?.linked_inference_id'),
    'the panel does not read the stored lineage back from the response'
  );
});

test('a claimed-lineage refusal is worded once, at both doors, and drops the retry', () => {
  for (const [name, source] of [
    ['the replay page', SIMULATION_PAGE],
    ['the workspace panel', WORKSPACE_PANEL],
  ]) {
    assert.ok(
      source.includes('err instanceof ApiError ? lineageRefusalOf(err.status, err.detail) : null'),
      `${name} does not classify failures through the shared helper`
    );
    // The server sentence with its reason, unless this round's shared module
    // words that reason — and the reason code stays either way, so the sentence
    // a customer reads is never the only trace of what the route answered.
    assert.ok(
      source.includes(
        'setError(`${refusal.message_zh ?? formatErrorDetail(err.detail)}（reason：${refusal.reason}）`);'
      ),
      `${name} does not show the refusal's own sentence with its reason`
    );
    assert.doesNotMatch(source, /LINEAGE_REFUSAL_LABELS_ZH\s*[:=]/, `${name} re-declared the refusal copy`);
    assert.ok(source.includes('setLineageSelected(false);'), `${name} keeps the refused tick`);
    // An unfixed retry is not offered: the page drops pendingDecision, the panel
    // drops the review both of which gate the submit controls.
    assert.match(source, /setPendingDecision\(null\)|setReview\(null\)/, `${name} still offers a retry`);
  }
});

// ---------------------------------------------------------------------------
// decision-history source state on the paper desk
// ---------------------------------------------------------------------------

test('the export vocabulary and the page vocabulary are the same three states', () => {
  const written = [
    ...new Set(
      [...segment(PAPER_API, 'if decision.linked_inference_id is None:', 'metadata = usage').matchAll(
        /source_state = "([a-z]+)"/g
      )].map((m) => m[1])
    ),
  ];
  assert.equal(written.length, 3, written.join(' | '));
  assert.deepEqual(Object.keys(SOURCE_STATE_LABELS).sort(), written.sort());
  for (const value of written) assert.equal(inferenceSourceState(value), value);
});

test('an unrecognised source state narrows to undefined, not to "none"', () => {
  for (const junk of [undefined, null, '', 'None', 'PRESENT', 'deleted', 0, {}]) {
    assert.equal(inferenceSourceState(junk), undefined, String(junk));
  }
});

test('the missing note neither promises a delete nor blames the write door', () => {
  assert.doesNotMatch(SOURCE_STATE_MISSING_NOTE, /was deleted|已被刪除/);
  assert.ok(SOURCE_STATE_MISSING_NOTE.includes('cannot be found under this owner now'));
  assert.ok(SOURCE_STATE_LABELS.missing.includes("this owner's ledger has no such row"));
});

test('the paper page reads the states from the export and keeps the CSV lanes', () => {
  assert.match(PAPER_PAGE, /import \{[^}]*\binferenceSourceState\b[^}]*\} from '@\/lib\/decisionLineage'/);
  assert.ok(PAPER_PAGE.includes('api.paperPortfolio.decisionHistoryJson(portfolioId)'));
  assert.ok(PAPER_PAGE.includes('data-testid="pp-download-decision-history-json"'));
  assert.ok(PAPER_PAGE.includes('SOURCE_STATE_LABELS[sourceState]'));
  assert.ok(PAPER_PAGE.includes('SOURCE_STATE_UNREADABLE_NOTE'));
  // The CSV filenames and handlers are unchanged: this adds a file, it does not
  // redefine what the trade journal rows mean.
  for (const marker of ['trade-journal-${portfolioId}.csv', 'decision-history-${portfolioId}.csv']) {
    assert.ok(PAPER_PAGE.includes(marker), marker);
  }
  // A failed read must not print the route's own `none` sentence.
  assert.doesNotMatch(PAPER_PAGE, /SOURCE_STATE_LABELS\.none/);
  assert.ok(PAPER_PAGE.includes('source state not read yet'));
});

test('one card can be withdrawn without touching the others', () => {
  // The route takes the skill in the query string (personalization.py:365), so
  // the button has to send it that way, not in a body the door never reads.
  const revoke = segment(
    API_CLIENT,
    'revokeDailyEstimateConsent: (skillUnitId?: string) => {',
    'dailyEstimateStatus:'
  );
  assert.match(revoke, /\?skill_unit_id=\$\{encodeURIComponent\(skillUnitId\)\}/);
  assert.match(PERSONALIZATION, /def delete_daily_estimate_consent\(\n    request: Request,\n    skill_unit_id: Optional\[uuid\.UUID\] = None,/);
  // The per-account control exists and names the account it stops; the
  // panel-wide button stays a separate call with no argument.
  assert.match(
    PANEL,
    /data-testid=\{`daily-estimate-withdraw-\$\{state\.skill_unit_id\}`\}\n\s*onClick=\{\(\) => withdraw\(state\.skill_unit_id\)\}/
  );
  assert.match(
    PANEL,
    /data-testid="daily-estimate-revoke-all"\n\s*onClick=\{\(\) => withdraw\(\)\}/
  );
  // An undeclared account has nothing to withdraw, and the button would be a
  // second way of saying "off" about a skill that was never on. The scope
  // editor below the cards is where the declared list lives, so everything from
  // the map to that editor is one card's worth of controls.
  const cards = segment(PANEL, '{skills.map((state) => {', 'data-testid="daily-estimate-scope-editor"');
  assert.equal(
    cards.split('daily-estimate-withdraw-${state.skill_unit_id}').length - 1,
    1,
    'the per-account withdrawal control is not exactly one'
  );
  assert.ok(cards.includes('撤回此帳號'));
  assert.equal(cards.includes('daily-estimate-revoke-all'), false);
  assert.ok(
    cards.slice(0, cards.indexOf('daily-estimate-withdraw-')).includes('{state.declared && ('),
    'the per-account withdrawal is not gated on the account being declared'
  );
  // The sentence written for the one-account branch says the others were left
  // alone, which is what the route does; the all-accounts branch says something
  // else entirely. Two acts, two notices.
  const notice = segment(PANEL, 'const withdraw = async (skillUnitId?: string) => {', 'const runNow = async');
  assert.ok(notice.includes('其他帳號的聲明範圍未改變'));
  assert.ok(notice.includes('從這次撤回之後開始的排程與手動要求都不再為任何帳號定價'));
});

test('the press notice says the button spends the day, not that it skips it', () => {
  // Until 2026-11-19 this sentence promised 「手動要求不佔用今日排程窗口，定時
  // 服務在今天仍會各執行一次」 — the defect, described cheerfully. A press files
  // the same window as the clock, so the honest sentence is the one below.
  const press = segment(PANEL, 'const runNow = async (skillUnitId: string) => {', 'const instrumentError =');
  assert.doesNotMatch(press, /不佔用今日排程窗口|仍會各執行一次/);
  // The press now composes nothing by hand: one call into the tested lib, so
  // the window claim cannot be worded one way here and another way there.
  assert.match(press, /setNotice\(pressNotice\(facts\)\);/, 'the press does not use the tested notice');
  assert.ok(
    PRESS_WINDOW_NOTE_ZH.includes('同一個 UTC 視窗、同一筆帳本'),
    'the press notice is not the shared-window one'
  );
  assert.ok(PRESS_WINDOW_NOTE_ZH.includes('不會多扣一次額度'));
  assert.doesNotMatch(PRESS_WINDOW_NOTE_ZH, /不佔用今日排程窗口|仍會各執行一次|不會替任何帳號定價/);
  // The claim is about the server, so it is read off the server: the manual
  // entry derives the window itself and passes it into the same tick.
  const trigger = segment(SERVICE, 'def trigger_daily_estimate(', '# ── what an owner can read back');
  assert.match(trigger, /window=estimate_window\(\)/);
  assert.match(trigger, /scope=MANUAL_SCOPE/);
});

// ---------------------------------------------------------------------------
// what one press bought: 新增估計 / 沿用 / 部分成功 / 全部失敗
//
// ``POST …/daily-estimate/run`` declares 201 before it knows whether an
// estimate happened, because the 201 names the attempt row it just opened. The
// four states below are what the brief asked the entry to distinguish, and they
// are built from the counts the same response carries.
// ---------------------------------------------------------------------------

// Refusal reasons the service can actually write into a ``refused`` entry —
// taken from the source that writes them, so 「沿用」 cannot be reachable only
// for a reason the platform never emits.
const REFUSAL_REASONS = [
  ...new Set([...SERVICE.matchAll(/"reason": "([a-z_]+)"/g)].map((m) => m[1])),
];
const ALREADY = 'already_estimated_this_window';
const otherReason = REFUSAL_REASONS.find((reason) => reason !== ALREADY);

test('the carried-over reason the press answer rests on is one the service emits', () => {
  assert.ok(REFUSAL_REASONS.length >= 5, `only ${REFUSAL_REASONS.length} refusal reasons readable`);
  assert.ok(REFUSAL_REASONS.includes(ALREADY), REFUSAL_REASONS.join(' | '));
  assert.ok(otherReason, 'every refusal reason is the carried-over one, so nothing distinguishes it');
});

test('pressOutcome keeps the four answers apart', () => {
  const answers = [
    { estimated: 1, refused: 0, failed: 0 },
    { estimated: 1, refused: 1, failed: 0 },
    { estimated: 0, refused: 1, failed: 0, refusal_reason: ALREADY },
    { estimated: 0, refused: 1, failed: 0, refusal_reason: otherReason },
  ].map(pressOutcome);
  assert.deepEqual(answers, ['estimated', 'partial', 'carried_over', 'nothing']);
  assert.equal(new Set(answers).size, 4, answers.join(' | '));
  // A press that billed nothing is not a partial success: 「部分成功」 claims a
  // settlement, and the ledger would have no row to point at.
  assert.equal(pressOutcome({ estimated: 0, refused: 2, failed: 0 }), 'nothing');
  assert.equal(pressOutcome({ estimated: 0, refused: 0, failed: 0 }), 'nothing');
  assert.equal(
    pressOutcome({ estimated: 0, refused: 0, failed: 0, refusal_reason: null }),
    'nothing'
  );
  // An estimate outranks the carried-over wording, the way it outranks a refusal
  // on the window card: this press did produce something.
  assert.equal(
    pressOutcome({ estimated: 2, refused: 0, failed: 0, refusal_reason: ALREADY }),
    'estimated'
  );
});

test('an item that died after the gate is a partial success, not a clean one', () => {
  // The tick that answered one account and had the other's worker raise writes
  // ``status="partial"`` with ``failed_count=1``, ``refused_count=0``. Counts read
  // without the third number say 「新增 1 筆、拒絕 0 項」 and word it 本次新增估計,
  // which claims the whole press landed.
  assert.equal(pressOutcome({ estimated: 1, refused: 0, failed: 1 }), 'partial');
  assert.ok(
    pressNotice({ estimated: 1, refused: 0, failed: 1 }).startsWith(PRESS_NOTICES_ZH.partial)
  );
  assert.match(pressNotice({ estimated: 1, refused: 0, failed: 1 }), /新增 1 筆、拒絕 0 項、失敗 1 筆/);
  // Nothing landed and both died: still 全部失敗, and the count is shown rather
  // than folded into 「拒絕」, because the ledger keeps them in two lists.
  assert.equal(pressOutcome({ estimated: 0, refused: 0, failed: 2 }), 'nothing');
  assert.match(pressNotice({ estimated: 0, refused: 0, failed: 2 }), /失敗 2 筆/);
});

test('the four press sentences each say what the counts do not prove', () => {
  assert.equal(new Set(Object.values(PRESS_NOTICES_ZH)).size, 4);
  assert.match(PRESS_NOTICES_ZH.estimated, /scope=manual/);
  assert.match(PRESS_NOTICES_ZH.estimated, /同一個 UTC 視窗/);
  assert.match(PRESS_NOTICES_ZH.carried_over, /沿用/);
  assert.match(PRESS_NOTICES_ZH.carried_over, /沒有再扣額度/);
  assert.match(PRESS_NOTICES_ZH.carried_over, /inference_id/);
  assert.match(PRESS_NOTICES_ZH.partial, /只有產生估計的那幾筆/);
  assert.match(PRESS_NOTICES_ZH.partial, /沒有結算/);
  assert.match(PRESS_NOTICES_ZH.nothing, /沒有新增結算/);
  assert.match(PRESS_NOTICES_ZH.nothing, /不是「已完成」/);
  for (const [name, text] of Object.entries(PRESS_NOTICES_ZH)) {
    // No sentence may claim a live process (no lease exists to prove one), a
    // refund, or an order — the three things a 201 has been read to mean.
    assert.doesNotMatch(text, /正在執行|仍在執行|還在執行/, name);
    assert.doesNotMatch(text, /已退款|已退額|會退額/, name);
    assert.doesNotMatch(text, /已完成手動估計|已下單|已建立訂單/, name);
  }
});

test('pressNotice reports the counts it was handed and disclaims the status code', () => {
  const text = pressNotice({
    estimated: 0, refused: 0, failed: 0, refusal_reason: otherReason,
  });
  assert.ok(text.startsWith(PRESS_NOTICES_ZH.nothing), text);
  assert.match(text, /新增 0 筆、拒絕 0 項、失敗 0 筆/);
  assert.match(text, /這個回應碼只代表這一次嘗試已建立/);
  assert.match(text, /不代表產生了估計/);
  assert.match(text, /以下方重新讀取的帳本為準/);
  assert.ok(text.endsWith(PRESS_WINDOW_NOTE_ZH), 'the standing window claim is not on the notice');
  // The numbers are the caller's, not a constant on the page.
  assert.match(pressNotice({ estimated: 2, refused: 1, failed: 0 }), /新增 2 筆、拒絕 1 項、失敗 0 筆/);
  assert.ok(
    pressNotice({ estimated: 1, refused: 0, failed: 0 }).startsWith(PRESS_NOTICES_ZH.estimated)
  );
});

test('the panel words the press through the lib and reads the counts off the body', () => {
  assert.match(PANEL, /import \{[^}]*\bpressNotice\b[^}]*\} from '@\/lib\/dailyEstimate'/);
  const press = segment(PANEL, 'const runNow = async (skillUnitId: string) => {', 'const instrumentError =');
  assert.match(press, /setNotice\(pressNotice\(facts\)\);/);
  assert.match(press, /estimated: result\.job\?\.estimated \?\? 0,/);
  assert.match(press, /refused: result\.job\?\.refused \?\? 0,/);
  assert.match(press, /failed: result\.job\?\.failed \?\? 0,/);
  assert.match(press, /refusal_reason: result\.status\?\.last_refusal\?\.reason \?\? null,/);
  // Nothing here may be derived from the response code: the client resolves with
  // the parsed body for any 2xx, and 201 is not a count.
  assert.doesNotMatch(press, /\.status(?!Code)\s*===|response\.ok|status_code/);
  // The worded sentences live in the lib only; a second hand-worded copy in the
  // component is how the tested strings and the screen start disagreeing.
  assert.doesNotMatch(PANEL, /已完成手動估計/);
  assert.doesNotMatch(PANEL, /本次嘗試：新增|這個回應碼只代表|全部失敗：本次|部分成功：只有|同一個 UTC 視窗、同一筆帳本|不會多扣一次額度/);
  // The keys are the ones the run response carries, taken from where the server
  // writes them. ``"job": outcome`` is the tick's own job dict.
  const job = segment(SERVICE, '"errors": errors,', '\ndef _refusal_code');
  for (const key of ['estimated', 'refused', 'failed']) {
    assert.match(job, new RegExp(`"${key}":`), `the job dict no longer carries ${key}`);
    assert.ok(
      press.includes(`result.job?.${key}`),
      `the press does not read the ${key} count the job dict emits`,
    );
  }
  const skillState = interfaceFields('SkillState').map((field) => field.key);
  assert.ok(skillState.includes('last_refusal'), 'SkillState does not model the refusal read back');
});

// ---------------------------------------------------------------------------
// 「目前未啟用」 is two different facts
// ---------------------------------------------------------------------------

test('an account the owner took back is not labelled as one nobody declared', () => {
  // The service's own ladder, read from the branch that decides between the two:
  // both literals here have to have a label on the card, or the fallback prints
  // the reason code itself to a person who cannot read it.
  const ladder = segment(SERVICE, 'disabled_reason = None', '"skill_unit_id": skill_id');
  assert.match(ladder, /"consent_withdrawn"/);
  assert.match(ladder, /"not_declared"/);
  assert.match(ladder, /_consent_withdrawn_for\(db, owner\.id, skill_id\)/);

  // The status-derived reasons are not hand-typed here either: the statuses the
  // service refuses on, plus the one it cannot read.
  const statuses = [
    ...new Set(
      [...segment(SERVICE, 'REFUSED_PORTFOLIO_STATUSES = (', ')').matchAll(/PortfolioStatus\.(\w+)/g)].map(
        (m) => m[1]
      )
    ),
  ];
  assert.ok(statuses.length >= 3, statuses.join(' | '));
  const fromServer = [
    'consent_withdrawn',
    'not_declared',
    ...statuses.map((status) => `skill_${status}`),
    'skill_status_unreadable',
  ];

  const labels = Object.fromEntries(
    [...segment(PANEL, 'const DISABLED_LABELS: Record<string, string> = {', '\n};').matchAll(
      /^ {2}([a-z_]+): '([^']*)',/gm
    )].map((m) => [m[1], m[2]])
  );
  assert.ok(Object.keys(labels).length >= fromServer.length, Object.keys(labels).join(' | '));
  for (const reason of fromServer) {
    assert.ok(labels[reason], `the card has no label for ${reason}`);
  }

  // The two texts must say the opposite thing about whether the owner ever
  // asked, and neither may claim more than the display knows.
  assert.match(labels.not_declared, /從未聲明/);
  assert.doesNotMatch(labels.not_declared, /撤回/);
  assert.match(labels.consent_withdrawn, /已撤回/);
  assert.match(labels.consent_withdrawn, /先前曾聲明/);
  assert.notEqual(labels.not_declared, labels.consent_withdrawn);
  for (const reason of ['not_declared', 'consent_withdrawn']) {
    assert.doesNotMatch(labels[reason], /已停止|退款|沒有估計|訂單/);
  }

  // Both render bare, in their own words, while every other reason keeps the
  // 「未執行：」 prefix it had. Saying 「未執行」 about an account the owner
  // withdrew would let the card read as if the platform had simply not run.
  assert.match(
    PANEL,
    /state\.disabled_reason === 'not_declared' \|\|\s*\n\s*state\.disabled_reason === 'consent_withdrawn'\s*\n\s*\n?\s*\?\s*DISABLED_LABELS\[state\.disabled_reason\]/
  );
});

// ---------------------------------------------------------------------------
// 本次失敗（未落帳）：the saved failure, read back without its exception text
// ---------------------------------------------------------------------------

test('failureNotice names the items it was given and none of the text around them', () => {
  const notice = failureNotice({
    skill_unit_id: 'a-skill',
    instrument_id: 'TEST-ENGINEERING-ASSET-B',
    reason: 'engine_failure',
    message_zh: '這個標的的每日估計在推論時失敗，這次沒有留下估計結果。',
    count: 2,
  });
  assert.match(notice, /本次失敗（未落帳）：2 筆/);
  assert.match(notice, /此處點名 TEST-ENGINEERING-ASSET-B/);
  assert.match(notice, /這個標的的每日估計在推論時失敗/);
  // The payment question stays where the payment record answers it.
  assert.match(notice, /inference_id/);
  assert.match(notice, /不看這條失敗/);
  // An absent count is the one item this row named — not zero, not a guess.
  assert.match(
    failureNotice({ skill_unit_id: 'a', instrument_id: 'X', reason: 'engine_failure' }),
    /本次失敗（未落帳）：1 筆/,
  );
  // No instrument named is said as 「未點名」; the card does not pick one.
  assert.match(
    failureNotice({ skill_unit_id: 'a', reason: 'engine_failure' }),
    /此行未點名標的/,
  );
  // Only the declared fields can reach the screen. A row that still carries the
  // internal text under any other key prints none of it.
  const leaky = failureNotice({
    skill_unit_id: 'a-skill',
    instrument_id: 'X',
    reason: 'engine_failure',
    message_zh: '安全句子',
    detail: 'ValueError: the internal text',
    error: 'another internal text',
    traceback: 'File "app/services/daily_estimates.py", line 1054',
  });
  assert.doesNotMatch(leaky, /ValueError|internal text|another|File "/, leaky);
  // The weaker attribution is labelled rather than hidden: a prefix read is not
  // a field that named the account.
  assert.match(
    failureNotice({
      skill_unit_id: 'a-skill',
      instrument_id: 'X',
      reason: 'engine_failure',
      attributed_from: 'error_string_prefix',
    }),
    /錯誤記錄字首讀回/,
  );
  assert.doesNotMatch(
    failureNotice({ skill_unit_id: 'a-skill', instrument_id: 'X', reason: 'engine_failure' }),
    /錯誤記錄字首讀回/,
  );
  // A row whose reason the door did not word is shown as its code, never as a
  // sentence invented here: 「原因代碼 x」 is quotable to support, while a
  // hand-worded guess would claim a cause the card was not told.
  assert.match(
    failureNotice({ skill_unit_id: 'a', instrument_id: 'X', reason: 'unworded_reason' }),
    /原因代碼 unworded_reason/,
  );
  // The two branches are the same row with the sentence present or absent, so a
  // card that always printed the code would pass the assertion above and fail
  // this one.
  assert.doesNotMatch(notice, /原因代碼/);
});

test('the panel renders the saved failure through the lib and keeps it addressable', () => {
  assert.match(PANEL, /import \{[^}]*\bfailureNotice\b[^}]*\} from '@\/lib\/dailyEstimate'/);
  assert.ok(
    PANEL.includes('daily-estimate-failure-${state.skill_unit_id}'),
    'the failure block has no test id of its own',
  );
  assert.match(PANEL, /\{failureNotice\(state\.last_failure\)\}/);
  // Wording lives in the lib; a second hand-worded copy in the component is how
  // the tested sentence and the screen start disagreeing.
  assert.doesNotMatch(PANEL, /本次失敗（未落帳）：|此行未點名標的|錯誤記錄字首讀回/);
  // The card reads it off the status payload, which is what survives a reload.
  const skillState = interfaceFields('SkillState').map((field) => field.key);
  assert.ok(skillState.includes('last_failure'), 'SkillState does not model last_failure');
  assert.match(PANEL, /last_failure: FailureRow \| null;/);
});

test('the failure field is written by the service and translated at the door', () => {
  // Both halves of the server contract, taken from the sources that write them
  // rather than typed by hand: the row carries the structured list, the status
  // read carries the entry, and the HTTP door is the only place that words it.
  assert.match(SERVICE, /"failures": failures,/);
  assert.match(SERVICE, /"reason": "engine_failure"/);
  const payload = segment(SERVICE, 'return {\n        "skill_unit_id": skill_id,', '"disclaimer_zh"');
  assert.match(payload, /"last_failure": last_failure,/);
  assert.match(PERSONALIZATION, /state\.get\("last_failure"\)/);
  const mapped = PERSONALIZATION.match(/^\s{4}"engine_failure": "([^"]*)",/m);
  assert.ok(mapped, 'the door no longer maps engine_failure to a sentence');
  assert.match(mapped[1], /沒有留下估計結果/);
  // What the card is handed is the structured entry and nothing else: the
  // lane's raw ``errors`` strings stay in the job row, so there is no exception
  // text on the wire for a screen to forget to redact.
  assert.doesNotMatch(payload, /"errors"/);
});
