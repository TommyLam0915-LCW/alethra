// Pure coverage for the feedback draft text. The draft is never submitted; the
// tracked Playwright spec covers the page and the absence of network delivery.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  emptyFeedbackDraft,
  feedbackDraftFilename,
  formatFeedbackDraft,
} from '../src/lib/feedbackDraft.ts';

test('empty draft still labels itself unsubmitted and explains missing fields', () => {
  const text = formatFeedbackDraft(emptyFeedbackDraft());
  assert.match(text, /not submitted/i);
  assert.match(text, /Not provided/);
  assert.match(text, /no\s+ticket number exists/i);
  assert.doesNotMatch(text, /ticket #/i);
});

test('draft text carries every entered field and its category label', () => {
  const text = formatFeedbackDraft({
    category: 'strategy_setup',
    page: 'Experiment settings',
    steps: 'Opened the form and entered 0.0005 commission.',
    expected: 'The value is kept.',
    actual: 'The value was rejected.',
    errorReference: 'ref-123',
  });
  assert.match(text, /Strategy setup or parameters/);
  assert.match(text, /Experiment settings/);
  assert.match(text, /entered 0\.0005 commission/);
  assert.match(text, /The value is kept\./);
  assert.match(text, /The value was rejected\./);
  assert.match(text, /ref-123/);
  assert.match(text, /not submitted/i);
});

test('draft text is plain text and cannot be mistaken for a receipt', () => {
  const text = formatFeedbackDraft({ ...emptyFeedbackDraft(), actual: 'Something happened.' });
  assert.doesNotMatch(text, /received|submitted successfully|we will get back|status:\s*open/i);
});

test('filename is date-stamped and deterministic', () => {
  assert.equal(
    feedbackDraftFilename(new Date(2026, 10, 1)),
    'feedback-draft-2026-11-01.md',
  );
});
