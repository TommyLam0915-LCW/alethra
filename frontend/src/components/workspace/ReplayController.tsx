'use client';

import { useState } from 'react';

export const INTRADAY_TIMEFRAMES = new Set(['1m', '5m', '15m', '30m', '1h']);

const STATUS_LABELS: Record<string, string> = {
  pending: '待開始',
  active: '進行中',
  paused: '已暫停',
  completed: '已完成',
  retired: '已停用',
};

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
  const advanceLabel = intraday ? `下一根 ${session.timeframe} K 線` : '下一日';

  return (
    <section
      className="ws-replay"
      aria-label="歷史回放控制器"
      data-testid="replay-controller"
      data-summary-open={summaryOpen}
    >
      {/* One row holds what a user needs every frame: which session this is, where the
          clock stands, and the only step the backend implements. ⑥ was 129px of this
          stacked three rows, and the centre column has to spend those pixels on ⑤'s
          plotted area and ⑧'s band instead. */}
      <div className="ws-replay-clock" data-testid="replay-clock">
        <span className="ws-replay-badge">歷史回放・非即時</span>
        <span className="ctx-label">狀態</span>
        <span className="ctx-value" data-testid="replay-status">
          {STATUS_LABELS[session.status] ?? session.status}
        </span>
        <span className="ctx-label">回放時鐘</span>
        <span className="ctx-value numeric" data-testid="replay-clock-value">
          {clock ?? '尚未推進'}
          {clock && intraday ? '（交易所當地時間）' : ''}
        </span>
        <span className="ctx-label">節奏</span>
        <span className="ctx-value">{intraday ? '逐根 K 線' : '逐日'}</span>
      </div>

      {/* Whose series the cursor cuts is not a secondary fact: when it is another
          instrument's, the 「未裁切」 below is the honest reading, not a bug. */}
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
          資料摘要
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
            暫停
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
            繼續
          </button>
        )}
        {!canAdvance && (
          <span className="ws-muted" data-testid="replay-advance-disabled-reason">
            此工作階段已{STATUS_LABELS[session.status] ?? session.status}，無法再推進
          </span>
        )}
      </div>

      {summaryOpen && (
        <div
          className="ws-replay-summary"
          id="replay-summary"
          data-testid="replay-summary"
        >
          <span className="ctx-label">已觀察</span>
          <span className="ctx-value numeric">{session.observed_days} 個觀測點</span>
          <span className="ctx-label">資料集</span>
          <span className="ctx-value">{session.provider_id ?? '未提供'}</span>
          <span className="ctx-label">週期</span>
          <span className="ctx-value">{session.timeframe ?? '1d'}</span>
          <span className="ctx-label">策略版本</span>
          <span className="ctx-value numeric">{session.experiment_version_id.slice(0, 8)}</span>
          <span className="ctx-label">圖表截止</span>
          <span className="ctx-value numeric" data-testid="replay-bars-end">
            {barsEnd ?? '未裁切'}
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
                ? '圖表未依此游標裁切：這個游標屬於另一個標的，裁切目前圖表會製造出該工作階段從未產生的回放視圖。後端僅支援向前逐步，沒有回溯、跳轉或變速。'
                : intraday
                  ? `圖表、指標與摘要一律依回放時鐘裁切，不含未來資料；日內游標之下，圖表只顯示至 ${barsEnd}（後端 /bars 目前僅提供日線）。後端僅支援向前逐步，沒有回溯、跳轉或變速。`
                  : '圖表、指標與摘要一律依回放時鐘裁切，不含未來 K 線。後端僅支援向前逐步，沒有回溯、跳轉或變速。'
              : '尚未推進：回放時鐘沒有游標，圖表顯示整個已儲存資料集。按下「下一日」或「下一根 K 線」後才會依回放時鐘裁切未來資料。'}
          </p>
        )
      )}
    </section>
  );
}
