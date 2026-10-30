'use client';

import { useState } from 'react';
import { label, NOT_PROVIDED, SESSION_STATUS_LABELS } from '@/lib/copy';

export const INTRADAY_TIMEFRAMES = new Set(['1m', '5m', '15m', '30m', '1h']);

export interface ReplaySession {
  id: string;
  status: string;
  timeframe: string | null;
  provider_id: string | null;
  current_snapshot_date: string | null;
  current_bar_timestamp: string | null;
  data_cutoff_at: string | null;
  observed_days: number;
  experiment_version_id: string;
  /** Server-side optimistic lock; manual decisions must send it as expected_version. */
  state_version?: number;
}

/**
 * The replay controller is a view over the server-owned session cursor: it never
 * keeps its own clock, and it only exposes the steps the API actually supports
 * (advance one day / one bar, pause, resume). Backward, seek and speed controls
 * are absent because no endpoint implements them.
 */
export default function ReplayController({
  session,
  barsEnd,
  instrumentNote,
  busy,
  error,
  onAdvance,
  onPause,
  onResume,
}: {
  session: ReplaySession;
  /** end_date the workspace actually requested bars with — the chart's real cut. */
  barsEnd: string | null;
  /** Set when the session cursor belongs to another instrument, so no cut was applied. */
  instrumentNote?: string | null;
  busy: boolean;
  error: string;
  onAdvance: () => void;
  onPause: () => void;
  onResume: () => void;
}) {
  const [summaryOpen, setSummaryOpen] = useState(false);
  const intraday = !!session.timeframe && INTRADAY_TIMEFRAMES.has(session.timeframe);
  const clock = intraday
    ? session.current_bar_timestamp?.slice(0, 16).replace('T', ' ') ?? null
    : session.current_snapshot_date;
  const canAdvance = session.status === 'pending' || session.status === 'active';
  const advanceLabel = intraday ? `Next ${session.timeframe} bar` : 'Next day';

  return (
    <section
      className="ws-replay"
      aria-label="Historical replay controls"
      data-testid="replay-controller"
      data-summary-open={summaryOpen}
    >
      {/* One row holds what a user needs every frame: which session this is, where the
          clock stands, and the only step the backend implements. ⑥ was 129px of this
          stacked three rows, and the centre column has to spend those pixels on ⑤'s
          plotted area and ⑧'s band instead. */}
      <div className="ws-replay-clock" data-testid="replay-clock">
        <span className="ws-replay-badge">Historical replay · not live</span>
        <span className="ctx-label">Status</span>
        <span className="ctx-value" data-testid="replay-status">
          {label(SESSION_STATUS_LABELS, session.status)}
        </span>
        <span className="ctx-label">Replay clock</span>
        <span className="ctx-value numeric" data-testid="replay-clock-value">
          {clock ?? 'Not advanced yet'}
          {clock && intraday ? ' (exchange local time)' : ''}
        </span>
        <span className="ctx-label">Rhythm</span>
        <span className="ctx-value">{intraday ? 'Per bar' : 'Per day'}</span>
      </div>

      {/* Whose series the cursor cuts is not a secondary fact: when it is another
          instrument's, the “Not cut” below is the honest reading, not a bug. */}
      {instrumentNote && (
        <p className="ws-replay-mismatch" data-testid="replay-instrument-note">
          {instrumentNote}
        </p>
      )}

      <div className="ws-replay-actions">
        <button
          type="button"
          className="ctx-meta-toggle"
          aria-expanded={summaryOpen}
          aria-controls="replay-summary"
          onClick={() => setSummaryOpen((open) => !open)}
          data-testid="replay-meta-toggle"
        >
          Data summary
        </button>
        <button
          type="button"
          className="ws-btn ws-btn-accent"
          onClick={onAdvance}
          disabled={busy || !canAdvance}
          data-testid="replay-advance"
        >
          {advanceLabel}
        </button>
        {session.status === 'active' && (
          <button
            type="button"
            className="ws-btn"
            onClick={onPause}
            disabled={busy}
            data-testid="replay-pause"
          >
            Pause
          </button>
        )}
        {session.status === 'paused' && (
          <button
            type="button"
            className="ws-btn"
            onClick={onResume}
            disabled={busy}
            data-testid="replay-resume"
          >
            Resume
          </button>
        )}
        {!canAdvance && (
          <span className="ws-muted" data-testid="replay-advance-disabled-reason">
            This session is already {label(SESSION_STATUS_LABELS, session.status)} and cannot
            advance further.
          </span>
        )}
      </div>

      {summaryOpen && (
        <div
          className="ws-replay-summary"
          id="replay-summary"
          data-testid="replay-summary"
        >
          <span className="ctx-label">Observed</span>
          <span className="ctx-value numeric">{session.observed_days} observations</span>
          <span className="ctx-label">Dataset</span>
          <span className="ctx-value">{session.provider_id ?? NOT_PROVIDED}</span>
          <span className="ctx-label">Timeframe</span>
          <span className="ctx-value">{session.timeframe ?? '1d'}</span>
          <span className="ctx-label">Strategy version</span>
          <span className="ctx-value numeric">{session.experiment_version_id.slice(0, 8)}</span>
          <span className="ctx-label">Chart cut-off</span>
          <span className="ctx-value numeric" data-testid="replay-bars-end">
            {barsEnd ?? 'Not cut'}
          </span>
        </div>
      )}

      {error ? (
        <p className="ws-replay-error" role="alert" data-testid="replay-error">
          {error}
        </p>
      ) : (
        summaryOpen && (
          <p className="ws-replay-hint" data-testid="replay-hint">
            {clock
              ? instrumentNote
                ? 'The chart is not cut to this cursor: the cursor belongs to a different instrument, and cutting the current chart would fabricate a replay view this session never produced. The backend only supports stepping forward — there is no rewind, seek or speed control.'
                : intraday
                  ? `The chart, indicators and summary are all cut to the replay clock and never include future data. With an intraday cursor the chart only shows up to ${barsEnd} (the backend /bars endpoint currently serves daily bars only). The backend only supports stepping forward — there is no rewind, seek or speed control.`
                  : 'The chart, indicators and summary are all cut to the replay clock and never include future bars. The backend only supports stepping forward — there is no rewind, seek or speed control.'
              : 'Not advanced yet: the replay clock has no cursor, so the chart shows the whole stored dataset. Future data is cut to the replay clock only after you choose “Next day” or “Next bar”.'}
          </p>
        )
      )}
    </section>
  );
}
