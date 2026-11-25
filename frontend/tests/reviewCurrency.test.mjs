// The two currency rules a frozen paper-order review lives by, exercised offline
// against the product module the panel imports. The component itself is JSX and
// this lane strips types only, so what is proven here is the rule; that the
// panel *calls* it is program evidence in the diff, and the end-to-end shape is
// the browser spec in e2e/manual-decision-lineage.spec.ts (NOT RUN).
//
// Run with: node --test tests/reviewCurrency.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';

register('./tsResolveLoader.mjs', import.meta.url);

const { reviewStamp, frozenVersion, isStillViewed } = await import(
  '../src/lib/reviewCurrency.ts'
);

const SESSION = '11111111-1111-4111-8111-111111111111';
const OTHER = '22222222-2222-4222-8222-222222222222';

function facts(over = {}) {
  return {
    portfolioId: 'pf-1',
    instrumentId: 'DEMO-ALPHA',
    action: 'buy',
    sessionId: SESSION,
    snapshotDate: '2024-01-05',
    barTimestamp: '2024-01-05T00:00:00Z',
    status: 'active',
    stateVersion: 4,
    buyQuantity: 1,
    rationale: 'the owner read this bar and chose to buy',
    attachedInferenceId: null,
    ...over,
  };
}

test('a write elsewhere moves the server counter and voids the review', () => {
  // The counter is the only thing a competing manual decision moves: the replay
  // cursor, the equity and the position stay where they were
  // (backend/app/api/simulations.py:2551-2567). If the stamp ignored it, the
  // panel would freeze a dead number and spend a 409 learning what it could
  // have seen without leaving the screen.
  const stamped = reviewStamp(facts());
  assert.notEqual(reviewStamp(facts({ stateVersion: 5 })), stamped);
  assert.notEqual(reviewStamp(facts({ stateVersion: null })), stamped);
});

test('the same facts re-derived void nothing', () => {
  // Key order is part of a JS object, not of the stamp: a fresh read that
  // returns the same session must not void a review the owner is about to press.
  const { stateVersion, ...rest } = facts();
  const reshuffled = { stateVersion, ...rest };
  assert.equal(reviewStamp(reshuffled), reviewStamp(facts()));
  assert.equal(reviewStamp(facts()), reviewStamp(facts()));
});

test('every field the submission carries belongs to the stamp', () => {
  const base = reviewStamp(facts());
  const changed = {
    portfolioId: 'pf-2',
    instrumentId: 'DEMO-BETA',
    action: 'sell',
    sessionId: OTHER,
    snapshotDate: '2024-01-08',
    barTimestamp: '2024-01-08T00:00:00Z',
    status: 'completed',
    buyQuantity: 2,
    rationale: 'a different reason',
    attachedInferenceId: 'inf-1',
  };
  for (const [field, value] of Object.entries(changed)) {
    assert.notEqual(reviewStamp(facts({ [field]: value })), base, field);
  }
});

test('the frozen version is the server row’s number, or nothing at all', () => {
  assert.equal(frozenVersion({ state_version: 7 }), 7);
  assert.equal(frozenVersion({ state_version: 0 }), 0, 'zero is a version, not an absence');
  // A session row written before the column exists has no lock to trip. That is
  // a separate case from "the lock held": the panel sends no field, and the
  // route's optimistic check is skipped (simulations.py:2420).
  assert.equal(frozenVersion({}), null);
  assert.equal(frozenVersion(null), null);
});

test('a late answer about a session the owner left cannot be installed', () => {
  const capturedSessionId = SESSION; // what the callback closed over
  const viewNow = OTHER; // what is on screen when the answer arrives
  // The closure proof still "passes" — which is exactly why it proves nothing.
  assert.equal(capturedSessionId === SESSION, true);
  assert.equal(isStillViewed(viewNow, SESSION), false);
  assert.equal(isStillViewed(SESSION, SESSION), true);
});

test('two unknowns are not each other’s match', () => {
  // `fresh?.id === session?.id` was true when both sides were undefined, so a
  // payload naming no session could be pushed into a view that had not loaded
  // one. An install needs two ids that name the same session.
  assert.equal(isStillViewed(null, undefined), false);
  assert.equal(isStillViewed(undefined, undefined), false);
  assert.equal(isStillViewed(SESSION, null), false);
  assert.equal(isStillViewed(SESSION, ''), false);
});
