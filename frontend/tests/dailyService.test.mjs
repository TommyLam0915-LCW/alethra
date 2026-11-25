// Unit coverage for the daily replay service card's derivations.
//
// Run with: node22 --experimental-strip-types --import ./tests/tsResolveLoader.mjs --test tests/dailyService.test.mjs
//
// Two kinds of claim live here. The field lists are read out of the backend
// source (backend/app/api/schemas.py) rather than typed by hand, so a route
// field renamed on the server side fails this file instead of drifting. The
// behaviour tests assert what the card says for each state the tick can leave
// behind; the wiring section at the bottom reads the frontend's source text and
// proves the page goes through the entry tested above — it is not rendered-UI
// evidence.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  cadenceLabel,
  lastRunLabel,
  runnerNote,
  serviceState,
  serviceView,
} from '../src/lib/dailyService.ts';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const LIB = readFileSync(`${HERE}../src/lib/dailyService.ts`, 'utf8');
const SCHEMAS = readFileSync(`${HERE}../../backend/app/api/schemas.py`, 'utf8');
const PANEL = readFileSync(
  `${HERE}../src/components/experiments/DailyServicePanel.tsx`,
  'utf8'
);
const PAGE = readFileSync(
  `${HERE}../src/app/experiments/[id]/simulation/page.tsx`,
  'utf8'
);
const API = readFileSync(`${HERE}../src/lib/api.ts`, 'utf8');

// The annotated fields of a Pydantic model, read from its own class body: the
// segment runs to the next top-level class, and only ``name: type`` lines count,
// so docstrings, comments and ``model_config = ConfigDict(...)`` stay out.
function pydanticFields(name) {
  const start = SCHEMAS.indexOf(`class ${name}(BaseModel):`);
  assert.notEqual(start, -1, `${name} not found in schemas.py`);
  const rest = SCHEMAS.slice(start + `class ${name}(BaseModel):`.length);
  const next = rest.search(/\n(class|def|router|@) /);
  const body = next === -1 ? rest : rest.slice(0, next);
  return body
    .split('\n')
    .map((line) => line.match(/^ {4}([a-z_][a-z0-9_]*):\s/))
    .filter(Boolean)
    .map((match) => match[1]);
}

function interfaceKeys(name) {
  const match = LIB.match(new RegExp(`interface ${name} \\{[^}]*\\}`));
  assert.ok(match, `${name} not found in dailyService.ts`);
  return match[0]
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => /^[a-zA-Z_][a-zA-Z0-9_]*:/.test(line))
    .map((line) => line.split(':')[0]);
}

function service(overrides = {}) {
  return {
    session_id: 'a0000000-0000-4000-8000-000000000001',
    session_status: 'active',
    timeframe: '1d',
    cadence_hour_utc: 3,
    cadence_timezone: 'UTC',
    job_type: 'simulation_daily',
    enrolled: true,
    enrolment_reason: null,
    queue_position: 0,
    batch_limit: 20,
    in_next_batch: true,
    kill_switch: 'inactive',
    runs_in_this_process: false,
    next_run_at: null,
    in_flight: false,
    last_run: null,
    statuses_served: ['pending', 'active'],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// the two ends of one contract
// ---------------------------------------------------------------------------

test('the card models exactly the fields the route returns', () => {
  assert.deepEqual(
    interfaceKeys('DailyService').sort(),
    pydanticFields('DailyServiceStatus').sort()
  );
  assert.deepEqual(
    interfaceKeys('DailyServiceRun').sort(),
    pydanticFields('DailyServiceRun').sort()
  );
});

// ---------------------------------------------------------------------------
// which state the card shows, and in what order it decides
// ---------------------------------------------------------------------------

test('the switch outranks everything the replay itself looks like', () => {
  // The tick checks the kill switch before it asks who to serve, and a switch
  // it cannot read stops the tick rather than guessing. A card that showed
  // "included in the daily step" over either of those would promise a step the
  // worker explicitly refuses.
  assert.equal(serviceState(service({ kill_switch: 'active' })), 'switch_active');
  assert.equal(
    serviceState(service({ kill_switch: 'unreadable', session_status: 'paused', enrolled: false, enrolment_reason: 'paused' })),
    'switch_unreadable'
  );
  assert.equal(
    serviceView(service({ kill_switch: 'unreadable' })).tone,
    'bad'
  );
});

test('each reason the tick gives has its own copy, not a shared "not eligible"', () => {
  const cases = [
    ['paused', 'paused'],
    ['completed', 'finished'],
    ['retired', 'finished'],
    ['intraday_timeframe_needs_a_bar_clock', 'bar_clock'],
    [null, 'status_unreadable'],
  ];
  const titles = new Set();
  for (const [reason, expected] of cases) {
    const body = service({
      enrolled: false,
      enrolment_reason: reason,
      queue_position: null,
      in_next_batch: false,
    });
    assert.equal(serviceState(body), expected, String(reason));
    const view = serviceView(body);
    // An empty title, or the same sentence reused for five different refusals,
    // would both pass a "has copy" check and tell the owner nothing.
    assert.ok(view.title.length > 8, `${reason}: ${view.title}`);
    assert.ok(view.reason.length > 20, reason);
    titles.add(view.title);
  }
  // completed and retired share one state and still get separate headings.
  assert.equal(titles.size, 5, [...titles].join(' | '));
});

test('a queue beyond the daily limit is deferred and says so with numbers', () => {
  const body = service({ queue_position: 20, in_next_batch: false, batch_limit: 20 });
  assert.equal(serviceState(body), 'deferred');
  const view = serviceView(body);
  assert.match(view.title, /position 21 of 20/);
  assert.match(view.reason, /next day/);
  assert.doesNotMatch(view.title, /refused|not eligible/i);
});

test('a tick still running is visible before any outcome exists', () => {
  assert.equal(serviceState(service({ in_flight: true })), 'in_flight');
  // in_flight is only read once the replay is eligible: a paused session cannot
  // be stepped by the row that is running for someone else's book.
  assert.equal(
    serviceState(service({ in_flight: true, enrolled: false, enrolment_reason: 'paused', in_next_batch: false })),
    'paused'
  );
});

// ---------------------------------------------------------------------------
// the honesty of the running claim
// ---------------------------------------------------------------------------

test('an eligible replay is never described as being run by another process', () => {
  const here = service({ runs_in_this_process: true, next_run_at: '2026-11-18T03:00:00+00:00' });
  assert.match(runnerNote(here), /next step is 18/);
  assert.match(cadenceLabel(here), /^03:00 UTC step$/);

  const away = service({ runs_in_this_process: false });
  assert.match(
    runnerNote(away),
    /does not run the interval service/,
    'a card that promised a daily step from a process with no scheduler is the claim this forbids'
  );
  assert.doesNotMatch(serviceView(away).reason, /will run daily|runs every day/i);
  assert.match(serviceView(away).reason, /does not run the interval service/);
});

test('the hour the card prints is the hour in the response', () => {
  assert.match(cadenceLabel(service({ cadence_hour_utc: 3 })), /^03:00/);
  assert.match(cadenceLabel(service({ cadence_hour_utc: 9, cadence_timezone: 'Asia/Shanghai' })), /^09:00 Asia\/Shanghai/);
});

// ---------------------------------------------------------------------------
// the ledger sentence
// ---------------------------------------------------------------------------

test('each last-run shape reads differently', () => {
  const base = {
    job_id: 'b0000000-0000-4000-8000-000000000001',
    job_status: 'completed',
    scope: 'daily',
    scheduled_for: '2026-11-17',
    completed_at: '2026-11-17T03:00:04+00:00',
    outcome: null,
    step_number: null,
    snapshot_date: null,
    detail: null,
  };

  assert.equal(lastRunLabel(service({ last_run: null })), 'The daily service has not stepped this replay yet.');
  assert.match(
    lastRunLabel(service({ last_run: { ...base, outcome: 'advanced', step_number: 4, snapshot_date: '2025-06-05' } })),
    /Last step: 2026-11-17, day 4 at 2025-06-05\.$/
  );
  assert.match(
    lastRunLabel(service({ last_run: { ...base, outcome: 'completed' } })),
    /reached the end of the stored dataset/
  );
  assert.match(
    lastRunLabel(service({ last_run: { ...base, outcome: 'skipped', detail: 'intraday_timeframe_needs_a_bar_clock' } })),
    /skipped this replay \(intraday_timeframe_needs_a_bar_clock\)/
  );
  assert.match(
    lastRunLabel(service({ last_run: { ...base, job_status: 'failed', outcome: 'failed', detail: 'no stored bars for US:AAPL' } })),
    /failed on this replay: no stored bars/
  );
  // A row that ran without naming this replay must not be read as this replay's
  // history — the sentence has to say it never reached it.
  assert.match(
    lastRunLabel(service({ last_run: { ...base, outcome: null } })),
    /ran without reaching this replay/
  );
});

test('a date the server could not fill falls back to the timestamp, not to nothing', () => {
  const base = {
    job_id: 'b0000000-0000-4000-8000-000000000002',
    job_status: 'completed',
    scope: 'manual',
    scheduled_for: null,
    completed_at: '2026-11-17T03:00:04+00:00',
    outcome: 'advanced',
    step_number: 1,
    snapshot_date: '2025-06-02',
    detail: null,
  };
  assert.match(
    lastRunLabel(service({ last_run: base })),
    /Last step: 2026-11-17, day 1/
  );
  assert.match(
    lastRunLabel(service({ last_run: { ...base, completed_at: null } })),
    /Last step: this request, day 1/
  );
});

// ---------------------------------------------------------------------------
// wiring: the page renders through the functions tested above
// ---------------------------------------------------------------------------

test('the replay page mounts the card and the card calls the one route', () => {
  assert.match(PAGE, /<DailyServicePanel\n?\s*sessionId=\{session\.id\}\n?\s*changedAt=\{session\.state_version\}\n?\s*\/>/);
  assert.match(API, /dailyService: \(sessionId: string\) =>\s*\n?\s*apiFetch\(`\/api\/v1\/simulations\/\$\{sessionId\}\/daily-service`\)/);

  assert.match(PANEL, /api\.simulations\.dailyService\(sessionId\)/);
  for (const entry of ['serviceView', 'serviceState', 'lastRunLabel', 'formatTime']) {
    assert.match(PANEL, new RegExp(`import \\{[^}]*\\b${entry}\\b[^}]*\\} from '@/lib/dailyService'`), entry);
  }
  // Every state the card can be in is addressable in the DOM, so an acceptance
  // pass can assert the state rather than the sentence.
  for (const marker of ['loading', 'unreachable', 'data-service-state={serviceState(service)}']) {
    assert.ok(PANEL.includes(marker), marker);
  }
  // The hour is printed, never branched on: a card that decided the verdict
  // from a clock literal would drift the moment the tick's hour changed.
  assert.doesNotMatch(PANEL, /hour_utc\s*===?\s*\d/);
});
