/** Run status is the last state the server recorded, not proof the run is alive. */
export const RUN_STATUS_LABELS: Record<string, string> = {
  pending: 'Not started',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
};

/** A synchronous run that has not completed after this long is almost certainly gone. */
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
