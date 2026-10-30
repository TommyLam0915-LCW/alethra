// One visual tone per lifecycle state, shared by every page.
// State names carried over from the backend status vocabularies.
export type StatusTone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral';

const TONES: Record<string, StatusTone> = {
  // terminal success
  active: 'ok',
  completed: 'ok',
  filled: 'ok',
  approved: 'ok',
  accepted: 'ok',
  succeeded: 'ok',
  success: 'ok',
  met_expectation: 'ok',
  // awaiting action
  pending: 'warn',
  pending_review: 'warn',
  submitted: 'warn',
  paused: 'warn',
  trialing: 'warn',
  evidence_emerging: 'warn',
  researching: 'warn',
  // in flight
  running: 'info',
  authorized: 'info',
  in_progress: 'info',
  locked_for_simulation: 'info',
  locked: 'info',
  forward_observation: 'info',
  historical_test_complete: 'ok',
  // failures / adverse
  failed: 'danger',
  rejected: 'danger',
  past_due: 'danger',
  contradicted: 'danger',
  evidence_insufficient: 'danger',
  revoked: 'danger',
  // inert / unknown
  cancelled: 'neutral',
  canceled: 'neutral',
  expired: 'neutral',
  waitlisted: 'neutral',
  draft: 'neutral',
  untested: 'neutral',
  insufficient_evidence: 'neutral',
  uncertain: 'neutral',
  retired: 'neutral',
  unknown: 'neutral',
};

export function statusTone(status: string | null | undefined): StatusTone {
  if (!status) return 'neutral';
  return TONES[status.toLowerCase()] ?? 'neutral';
}

export function statusPillClass(status: string | null | undefined): string {
  return `status-pill status-${statusTone(status)}`;
}

export function statusNoticeClass(status: string | null | undefined): string {
  return `notice notice-${statusTone(status)}`;
}
