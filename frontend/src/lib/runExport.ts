// The export surface of one formal research run.
//
// Every literal here is the server's own, copied from
// backend/app/services/run_trades.py (BASIS_WITH_TRADES, BASIS_WITHOUT_TRADES)
// and from the Content-Disposition header backend/app/api/runs.py builds for
// GET /api/v1/runs/{run_id}/trades.csv. The two ends are pinned by
// backend/tests/test_run_trades_export.py and tests/runTradeExport.test.mjs:
// a basis the UI renames, or a filename the UI invents, stops being the record
// the server signed.

export const EXPORT_BASIS_WITH_TRADES = 'completed_with_trades';
export const EXPORT_BASIS_WITHOUT_TRADES = 'completed_without_trades';

export interface RunTradesExport {
  run_id: string;
  experiment_id: string;
  experiment_version_id: string;
  version_number: number;
  owner_id: string;
  status: string;
  export_basis: string;
  basis_note: string;
  trade_count: number;
  columns: string[];
  provenance: Record<string, unknown>;
  trades: Record<string, unknown>[];
}

/** The download name the server asked for, so the file on disk matches the header. */
export function exportFileName(runId: string): string {
  return `run-${runId}-trades.csv`;
}

/**
 * One line saying how many fills the record holds.
 *
 * The zero case is spelled out rather than left at "0", because a blank
 * spreadsheet and a run that filled nothing are different claims, and the
 * server's own `basis_note` (rendered beside this) is what settles which one
 * the caller is looking at.
 */
export function describeExportHeadline(summary: RunTradesExport): string {
  if (summary.export_basis === EXPORT_BASIS_WITHOUT_TRADES) {
    return 'This run completed and recorded zero fills.';
  }
  if (summary.export_basis === EXPORT_BASIS_WITH_TRADES) {
    return `${summary.trade_count} filled leg(s) recorded.`;
  }
  // A basis this build has never seen is not guessed at: the count is stated,
  // and the server's note is shown next to it unchanged.
  return `The server reported ${summary.trade_count} row(s) under an unrecognised basis (${summary.export_basis}).`;
}
