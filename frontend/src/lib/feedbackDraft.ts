/**
 * Pure formatting for the in-product feedback draft. The draft is never sent,
 * stored or given a ticket number; these helpers only prepare text the user can
 * copy or export themselves.
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
