/**
 * Shared vocabulary for the in-product feedback form: the category list, the
 * field limits and the shape of a stored row. The same form text can be copied
 * or exported by hand, or submitted to POST /api/v1/feedback and stored.
 */

export interface FeedbackDraft {
  category: string;
  page: string;
  steps: string;
  expected: string;
  actual: string;
  errorReference: string;
}

export const FEEDBACK_CATEGORIES = [
  { value: 'research_results', label: 'Research results or reports' },
  { value: 'strategy_setup', label: 'Strategy setup or parameters' },
  { value: 'data_instruments', label: 'Data or instruments' },
  { value: 'paper_trading', label: 'Paper trading or journal' },
  { value: 'account_access', label: 'Account or access' },
  { value: 'other', label: 'Something else' },
] as const;

/**
 * Field length ceilings, mirrored from backend/app/api/feedback.py. The server
 * enforces these; the form uses them only to warn before a submission fails.
 */
export const FEEDBACK_LIMITS = {
  page: 200,
  steps: 4000,
  expected: 2000,
  actual: 4000,
  errorReference: 200,
} as const;

/** The request body of POST /api/v1/feedback — exactly the fields on this form. */
export interface FeedbackSubmitRequest {
  category: string;
  page: string | null;
  steps: string;
  expected: string | null;
  actual: string;
  error_reference: string | null;
}

/** One stored feedback row, as returned by the API. */
export interface FeedbackEntry {
  id: string;
  category: string;
  page: string | null;
  steps: string;
  expected: string | null;
  actual: string;
  error_reference: string | null;
  created_at: string;
  submitted_by: string;
}

/**
 * Trim to what the server stores. Blank optional fields become null rather than
 * an empty string, so a read-back shows "not provided" instead of a blank cell.
 */
export function toFeedbackSubmitRequest(draft: FeedbackDraft): FeedbackSubmitRequest {
  const optional = (value: string): string | null => {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  };
  return {
    category: draft.category,
    page: optional(draft.page),
    steps: draft.steps.trim(),
    expected: optional(draft.expected),
    actual: draft.actual.trim(),
    error_reference: optional(draft.errorReference),
  };
}

export function emptyFeedbackDraft(): FeedbackDraft {
  return {
    category: FEEDBACK_CATEGORIES[0].value,
    page: '',
    steps: '',
    expected: '',
    actual: '',
    errorReference: '',
  };
}

function valueOrNotProvided(value: string): string {
  const trimmed = value.trim();
  return trimmed ? trimmed : 'Not provided';
}

export function formatFeedbackDraft(draft: FeedbackDraft): string {
  const category =
    FEEDBACK_CATEGORIES.find((entry) => entry.value === draft.category)?.label ?? draft.category;
  return [
    '# Feedback draft (not submitted)',
    '',
    `- Category: ${valueOrNotProvided(category)}`,
    `- Page or screen: ${valueOrNotProvided(draft.page)}`,
    `- Error reference (optional): ${valueOrNotProvided(draft.errorReference)}`,
    '',
    '## Steps taken',
    valueOrNotProvided(draft.steps),
    '',
    '## Expected result',
    valueOrNotProvided(draft.expected),
    '',
    '## Actual result',
    valueOrNotProvided(draft.actual),
    '',
    '---',
    'This draft was written locally in the product. It has not been sent, and no',
    'ticket number exists.',
    '',
  ].join('\n');
}

export function feedbackDraftFilename(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `feedback-draft-${year}-${month}-${day}.md`;
}
