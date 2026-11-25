// Unit coverage for what the UI does with a run that has no outcome.
//
// Run with: node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/runCancellation.test.mjs
// The refusal string below is copied from active_run_conflict_detail in
// backend/app/services/research_runs.py; backend/tests/test_research_run_recovery.py
// pins that every id in the real 409 is the blocking run's, so this fixture and
// that test are the two ends of the same contract.
//
// The wiring section at the bottom reads the components' source text. It proves
// that a page goes through the functions tested above — nothing in it is
// rendered-UI evidence.

import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

import {
  RUN_STATUS_LABELS,
  activeRunIdFromRefusal,
  describeRunStatus,
  isCancellable,
} from '../src/lib/runStatus.ts';
import {
  ATTEMPT_STATUS_LABELS,
  RUN_STATUS_LABELS as COPY_RUN_STATUS_LABELS,
} from '../src/lib/copy.ts';

const BLOCKING_RUN = '7d1a2b3c-4455-6677-8899-001122334455';
const ADMISSION_REFUSAL =
  `A committed run of this version is still active as run ${BLOCKING_RUN}. ` +
  'Admission is serialised on the version row, so a second run would ' +
  'double-bill the same work; it is not proof the first process is alive — the ' +
  'platform cannot see processes, only rows.';

test('cancelled is a label, not an unknown status', () => {
  assert.equal(RUN_STATUS_LABELS.cancelled, 'Cancelled');
  // Two maps render run status in this app; a row that reads differently
  // depending on which page shows it is a record the user cannot trust.
  assert.deepEqual(
    { ...RUN_STATUS_LABELS },
    { ...COPY_RUN_STATUS_LABELS },
    'runStatus.ts and copy.ts must label the same statuses the same way',
  );
});

test('a cancelled row is described as cancelled, and never as possibly running', () => {
  const weekOld = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();
  assert.deepEqual(describeRunStatus('cancelled', weekOld), {
    label: 'Cancelled',
    stale: false,
  });
});

test('only the states with no outcome yet can be cancelled', () => {
  assert.equal(isCancellable('pending'), true);
  assert.equal(isCancellable('running'), true);
  for (const closed of ['completed', 'failed', 'cancelled']) {
    assert.equal(isCancellable(closed), false, `${closed} already has an outcome`);
  }
  assert.equal(isCancellable(''), false);
  assert.equal(isCancellable('running_'), false);
});

test('age makes a running row stale without claiming its process died', () => {
  const started = Date.parse('2026-11-16T00:00:00Z');
  const later = started + 31 * 60 * 1000;
  const view = describeRunStatus('running', '2026-11-16T00:00:00Z', later);
  assert.equal(view.stale, true);
  assert.match(view.detail, /nor proof that it is still running/);
});

test('the refusal that names its run becomes an actionable id', () => {
  assert.equal(activeRunIdFromRefusal(ADMISSION_REFUSAL), BLOCKING_RUN);
});

test('a refusal with no id, or not text, yields no id to link to', () => {
  assert.equal(activeRunIdFromRefusal('Version is locked'), null);
  assert.equal(activeRunIdFromRefusal('run not-a-uuid-here'), null);
  assert.equal(activeRunIdFromRefusal(undefined), null);
  assert.equal(activeRunIdFromRefusal({ error: 'run_finished' }), null);
});

// --- wiring: the pages must go through the functions tested above -----------
//
// These read source text, so they say that a page is wired to the entry, not
// that the entry renders. Rendered evidence needs a browser, and this machine
// has no Playwright chromium build for mac13-arm64.

import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = join(fileURLToPath(new URL('..', import.meta.url)));
const REPO = join(FRONTEND, '..');

function source(path) {
  assert.ok(existsSync(path), `${path} is not in the checkout to read`);
  return readFileSync(path, 'utf8');
}

const runDetail = source(
  join(FRONTEND, 'src/app/experiments/[id]/runs/[runId]/page.tsx'),
);
const revisionPanel = source(
  join(FRONTEND, 'src/components/experiments/RevisionPanel.tsx'),
);
const studyPanel = source(
  join(FRONTEND, 'src/components/experiments/RobustnessPanel.tsx'),
);

test('the run detail page offers the cancel entry only while the run has no outcome', () => {
  assert.match(runDetail, /import \{ describeRunStatus, isCancellable \} from '@\/lib\/runStatus';/);
  assert.match(runDetail, /\{isCancellable\(run\.status\) \? \(/);
  assert.match(runDetail, /data-testid="run-cancel-submit"/);
  assert.match(runDetail, /onClick=\{cancelRun\}/);
  assert.match(runDetail, /await api\.runs\.cancel\(runId, cancelReason/);
});

test('what the cancellation proved is the server sentence, not one written here', () => {
  // The note is the part that says the process is not confirmed stopped.
  // Rendering a local sentence instead would delete that claim from the UI.
  assert.match(runDetail, /setCancelNotice\(result\?\.note/);
  assert.doesNotMatch(runDetail, /the process (has been|was) stopped/i);
});

test('a 409 that names its run is answered with a link to that run', () => {
  assert.match(revisionPanel, /import \{ activeRunIdFromRefusal \} from '@\/lib\/runStatus';/);
  assert.match(revisionPanel, /activeRunIdFromRefusal\(err\.detail\)/);
  assert.match(revisionPanel, /data-testid="revision-blocking-run-link"/);
  assert.match(
    revisionPanel,
    /href=\{`\/experiments\/\$\{experimentId\}\/runs\/\$\{blockingRunId\}`\}/,
  );
});

// The vocabulary is read from the lane that emits it, because the defect this
// pins is a new state landing in the database while the page keeps a list
// written by hand: the numbers stop adding up and nothing fails.
const modelSource = source(join(REPO, 'backend/app/models/research_run.py'));
const statusClass =
  /class RunStatus\(str, enum\.Enum\):([\s\S]*?)\n\n(?=class )/.exec(modelSource);
assert.ok(statusClass, 'RunStatus is not in the model file in the shape this test reads');
const runStatuses = [...statusClass[1].matchAll(/^ {4}(\w+) = "(\w+)"$/gm)].map((m) => m[1]);

test('every run status the model can store has a label the UI renders', () => {
  assert.ok(runStatuses.length >= 5, `read ${runStatuses} from the model`);
  for (const status of runStatuses) {
    assert.ok(status in RUN_STATUS_LABELS, `runStatus.ts has no label for ${status}`);
    assert.ok(status in COPY_RUN_STATUS_LABELS, `copy.ts has no label for ${status}`);
  }
});

const countsSource = source(join(REPO, 'backend/app/services/robustness.py'));
const counted = [...countsSource.matchAll(/for status in \(([^)]*)\)/g)]
  .flatMap((m) => [...m[1].matchAll(/"([^"]+)"/g)].map((q) => q[1]));

test('the study panel renders every status the aggregate counts', () => {
  assert.ok(counted.length > 0, 'no count vocabulary found in the study aggregate');
  const ordered = /const ATTEMPT_COUNT_ORDER = \[([^\]]*)\]/.exec(studyPanel);
  assert.ok(ordered, 'the study panel no longer declares its count order');
  const order = [...ordered[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
  for (const status of counted) {
    assert.ok(order.includes(status), `${status} is counted but never rendered`);
    assert.ok(status in ATTEMPT_STATUS_LABELS, `${status} has no attempt label`);
  }
});
