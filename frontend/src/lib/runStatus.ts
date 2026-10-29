/** Run status is the last state the server recorded, not proof the run is alive. */
export const RUN_STATUS_LABELS: Record<string, string> = {
  pending: '等待開始',
  running: '執行中',
  completed: '已完成',
  failed: '失敗',
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
  const base = RUN_STATUS_LABELS[status] ?? `狀態未知（${status}）`;
  if (status !== 'running') return { label: base, stale: false };

  const started = startedAt ? Date.parse(startedAt) : NaN;
  if (!Number.isFinite(started) || now - started <= STALE_RUNNING_MS) {
    return { label: base, stale: false };
  }
  return {
    label: '執行中（可能已中斷）',
    stale: true,
    detail:
      '伺服器仍保留 running，但已超過 30 分鐘沒有完成記錄，也沒有 completed_at；' +
      '這不是完成，也不是目前仍在執行。',
  };
}
