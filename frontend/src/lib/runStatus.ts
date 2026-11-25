/** Run status is the last state the server recorded, not proof the run is alive. */
export const RUN_STATUS_LABELS: Record<string, string> = {
  pending: 'Not started',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  // Closed by a decision recorded at POST /api/v1/runs/{id}/cancel, never by a
  // timeout. Without this label the row renders "Unknown status (cancelled)",
  // which reads as a defect in the record rather than the record itself.
  cancelled: 'Cancelled',
};

/** The server's own predicate: only these rows still have an outcome to write. */
export const CANCELLABLE_RUN_STATUSES = ['pending', 'running'] as const;

export function isCancellable(status: string): boolean {
  return (CANCELLABLE_RUN_STATUSES as readonly string[]).includes(status);
}

/** How long a run may say `running` before the UI calls the record stale. */
export const STALE_RUNNING_MS = 30 * 60 * 1000;

export interface RunStatusView {
  label: string;
  stale: boolean;
  detail?: string;
}

export function describeRunStatus(
  status: string,
  startedAt?: string | null,
  now: number = Date.now(),
): RunStatusView {
  const base = RUN_STATUS_LABELS[status] ?? `Unknown status (${status})`;
  if (status !== 'running') return { label: base, stale: false };

  const started = startedAt ? Date.parse(startedAt) : NaN;
  if (!Number.isFinite(started) || now - started <= STALE_RUNNING_MS) {
    return { label: base, stale: false };
  }
  return {
    label: 'Running (possibly interrupted)',
    stale: true,
    detail:
      'The server still reports running, but more than 30 minutes have passed with no completion ' +
      'record and no completed_at; this is neither a completed run nor proof that it is still running.',
  };
}

const UUID_IN_TEXT =
  /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/;

/**
 * The blocking run id inside the 409 that refuses a second run of this version.
 *
 * `active_run_conflict_detail` (backend/app/services/research_runs.py) names the
 * row it is refusing for, so the refusal can be turned into a link to that run
 * and its cancel entry instead of a paragraph the user has to act on by hand.
 * Any other 409 — or a detail the server wrote without an id — returns null:
 * the caller must still show the refusal, just not a button it cannot justify.
 */
export function activeRunIdFromRefusal(detail: unknown): string | null {
  if (typeof detail !== 'string') return null;
  const match = UUID_IN_TEXT.exec(detail);
  return match ? match[0] : null;
}

