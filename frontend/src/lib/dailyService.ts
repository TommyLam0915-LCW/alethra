// The daily replay service card: pure derivation from
// GET /api/v1/simulations/{id}/daily-service.
//
// Kept out of the page so the wording can be tested without a browser, and so
// the card cannot invent a fact the route did not return. Every field on
// DailyService comes from the server's own tick constants and ledger; nothing
// here hardcodes an hour, a limit or a status list.

export type RunOutcome = 'advanced' | 'completed' | 'skipped' | 'failed' | null;

export interface DailyServiceRun {
  job_id: string;
  job_status: string;
  scope: string | null;
  scheduled_for: string | null;
  completed_at: string | null;
  outcome: RunOutcome;
  step_number: number | null;
  snapshot_date: string | null;
  detail: string | null;
}

export interface DailyService {
  session_id: string;
  session_status: string | null;
  timeframe: string | null;
  cadence_hour_utc: number;
  cadence_timezone: string;
  job_type: string;
  enrolled: boolean;
  enrolment_reason: string | null;
  queue_position: number | null;
  batch_limit: number;
  in_next_batch: boolean;
  kill_switch: 'active' | 'inactive' | 'unreadable';
  runs_in_this_process: boolean;
  next_run_at: string | null;
  in_flight: boolean;
  last_run: DailyServiceRun | null;
  statuses_served: string[];
}

export type Tone = 'ok' | 'muted' | 'warn' | 'bad';

export interface ServiceView {
  tone: Tone;
  title: string;
  reason: string;
  // The control the owner already has for this state, named rather than
  // duplicated: the service stops for Pause because Pause writes the status the
  // tick filters on.
  control: string;
}

export type ServiceState =
  | 'switch_unreadable'
  | 'switch_active'
  | 'paused'
  | 'finished'
  | 'bar_clock'
  | 'status_unreadable'
  | 'deferred'
  | 'in_flight'
  | 'enrolled';

export function serviceState(s: DailyService): ServiceState {
  // In the same order the tick decides: the switch first, because a tick that
  // cannot read it writes nothing.
  if (s.kill_switch === 'unreadable') return 'switch_unreadable';
  if (s.kill_switch === 'active') return 'switch_active';
  if (!s.enrolled) {
    switch (s.enrolment_reason) {
      case 'paused':
        return 'paused';
      case 'completed':
      case 'retired':
        return 'finished';
      case 'intraday_timeframe_needs_a_bar_clock':
        return 'bar_clock';
      default:
        return 'status_unreadable';
    }
  }
  if (!s.in_next_batch) return 'deferred';
  if (s.in_flight) return 'in_flight';
  return 'enrolled';
}

export function serviceView(s: DailyService): ServiceView {
  switch (serviceState(s)) {
    case 'switch_unreadable':
      return {
        tone: 'bad',
        title: 'The stop control could not be read',
        reason:
          'The daily step refuses to run while the kill switch is unreadable, so this replay is not moving until the database answers.',
        control: 'Fix the connection, then reload this page.',
      };
    case 'switch_active':
      return {
        tone: 'warn',
        title: 'Held by the execution kill switch',
        reason:
          'The switch that stops paper execution is on, so the daily service skips every account — including this replay.',
        control: 'Clear the execution kill switch to let the daily step resume.',
      };
    case 'paused':
      return {
        tone: 'muted',
        title: 'Paused by you',
        reason:
          'A paused replay is not a claim on the service: it stays exactly where the last step left it until you resume it.',
        control: 'Resume starts the daily steps again.',
      };
    case 'finished':
      return {
        tone: 'muted',
        title:
          s.enrolment_reason === 'completed'
            ? 'Replay complete'
            : 'Retired by you',
        reason:
          s.enrolment_reason === 'completed'
            ? 'The dataset ran out, so the service reached the end of this replay. Its decisions stay readable.'
            : 'A retired replay is closed, and the service does not reopen it.',
        control: 'Nothing to stop. Start a new replay to be served again.',
      };
    case 'bar_clock':
      return {
        tone: 'muted',
        title: `Not served by a daily clock (${s.timeframe} bars)`,
        reason:
          'The daily step releases one completed day. An intraday replay needs one step per bar, so it stays on the advance button rather than being walked at a pace its own timeframe does not describe.',
        control: 'Use Advance to release the next completed bar.',
      };
    case 'status_unreadable':
      return {
        tone: 'bad',
        title: 'This replay has no readable status',
        reason:
          'The service serves only the statuses it can read (' +
          s.statuses_served.join(', ') +
          '). A missing status is not treated as one of them.',
        control: 'Reload this page; if it persists, the row needs repair.',
      };
    case 'deferred':
      return {
        tone: 'warn',
        title: `Queued behind today's limit (position ${(s.queue_position ?? 0) + 1} of ${s.batch_limit} per day)`,
        reason:
          'One day steps at most this many open replays per account, oldest first. This one is not refused — it waits for the next day.',
        control: 'Close replays you no longer run, or let the queue work down.',
      };
    case 'in_flight':
      return {
        tone: 'ok',
        title: 'A daily step is running right now',
        reason:
          'The service has a record open that has not reported its outcome yet.',
        control: 'Reload after it finishes, or Pause to stop it from the next day.',
      };
    case 'enrolled':
      return {
        tone: 'ok',
        title: `Included in the daily ${cadenceLabel(s)}`,
        reason: runnerNote(s),
        control: 'Pause stops the daily steps for this replay.',
      };
  }
}

export function cadenceLabel(s: DailyService): string {
  const hour = String(s.cadence_hour_utc).padStart(2, '0');
  return `${hour}:00 ${s.cadence_timezone} step`;
}

// Honest by construction: one API process answers the request, and only the
// process that started the interval service can say it is running. A deployment
// with several workers shows this line rather than implying a runner.
export function runnerNote(s: DailyService): string {
  if (s.runs_in_this_process) {
    return s.next_run_at
      ? `The process answering this request runs the interval service; its next step is ${formatTime(s.next_run_at)}.`
      : 'The process answering this request runs the interval service.';
  }
  return 'The process that answered this request does not run the interval service, so nothing here promises a step until a worker that does is deployed.';
}

export function lastRunLabel(s: DailyService): string {
  const run = s.last_run;
  if (!run) {
    return 'The daily service has not stepped this replay yet.';
  }
  const when = run.scheduled_for || run.completed_at?.slice(0, 10) || 'this request';
  switch (run.outcome) {
    case 'advanced':
      return `Last step: ${when}, day ${run.step_number} at ${run.snapshot_date}.`;
    case 'completed':
      return `Last step: ${when} — it reached the end of the stored dataset.`;
    case 'skipped':
      return `Last tick skipped this replay (${run.detail ?? 'no reason recorded'}).`;
    case 'failed':
      return `Last tick failed on this replay: ${run.detail ?? 'no detail recorded'}.`;
    default:
      // A row that ran and reported its counts without naming this session is
      // not evidence about this replay, so the card says so instead of
      // borrowing the number.
      return 'The service ran without reaching this replay.';
  }
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('en-GB', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  }) + ' UTC';
}
