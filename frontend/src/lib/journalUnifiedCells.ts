// G3-5: the unified table shows two ledgers that record different facts, so
// three of its cells cannot be a plain field read.
//
// A daily paper fill has no observed instant — `paper_fills.bar_timestamp` is
// nullable precisely because the daily path never samples one. Reading only
// `entry_time` rendered every such row as an em-dash in the Date column, which
// is how a filter appears to lose rows it did not lose. The day is known, so
// the cell says the day and marks that no clock time was ever observed.
//
// A fill also has no per-fill P&L. Realised P&L is a property of the position
// the fill closes, and nothing in the journal recomputes it per fill, so the
// row carries `pnl_status` instead of a number; inventing one would be the
// 「補造無對應經濟事件的成交」 mistake in a new place.

import { formatDate, formatDateTime } from './format';

export const FILL_PNL_STATUS = 'fill_level_pnl_not_recorded';

export const FILL_PNL_NOTE =
  'A fill is one leg of a round trip. Realised P&L is recorded on the position ' +
  'it closes, not on the fill, and nothing here recomputes it per fill.';

export interface UnifiedCellRow {
  time_precision?: string | null;
  entry_time?: string | null;
  execution_date?: string | null;
  pnl_status?: string | null;
  exit_reason?: string | null;
  fill_reason?: string | null;
}

/** The day label, with the grain stated so a reader cannot read it as midnight. */
function dayLabel(executionDate: string): string {
  return `${formatDate(executionDate)} · day`;
}

/**
 * When this record says something happened, at the precision it was recorded.
 *
 * The instant wins when there is one; the day is the fallback, not the default.
 * A grain word needs a date to describe — `time_precision` alone would render
 * `— · day`, which claims a day the record does not carry.
 */
export function unifiedDateLabel(row: UnifiedCellRow): string {
  if (row.time_precision === 'day')
    return row.execution_date ? dayLabel(row.execution_date) : '—';
  if (row.entry_time) return formatDateTime(row.entry_time);
  if (row.execution_date) return dayLabel(row.execution_date);
  return '—';
}

/**
 * Why the P&L cell is empty, or null when it is not empty.
 *
 * Keyed on the status the server wrote rather than on the absence of a number:
 * a row that simply has no P&L field is not a row that has an explanation for
 * not having one.
 */
export function unifiedPnlNote(row: UnifiedCellRow): string | null {
  return row.pnl_status === FILL_PNL_STATUS ? FILL_PNL_NOTE : null;
}

/**
 * Which of two different reasons this row is closing on.
 *
 * `exit_reason` is the research leg's signal that ended a round trip;
 * `fill_reason` is the paper leg's simulation rule that decided a fill. They
 * are not the same fact, so the cell names the one it is showing.
 */
export function unifiedReason(
  row: UnifiedCellRow,
): { kind: 'exit' | 'fill' | 'none'; text: string } {
  if (row.exit_reason) return { kind: 'exit', text: row.exit_reason };
  if (row.fill_reason) return { kind: 'fill', text: row.fill_reason };
  return { kind: 'none', text: '—' };
}
