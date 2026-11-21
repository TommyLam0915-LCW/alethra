// What a paper-trading run's status cell is allowed to say about why it stopped.
//
// A non-empty `paper_trading_runs.failure_reason` has one producer: `backend/app/
// services/paper_trading.py:1086` sets it to `safe_error_message(exc)` in the same
// statement that decides `failed` vs `cancelled`, and it writes NULL for the
// cancelled arm. Two other statements clear the column — the acquisition UPDATE
// when a run executes again, and the immediate-cancel UPDATE — so NULL always
// belongs to a run that is not currently failing. So an empty field means one of
// two different things, and the page cannot tell them apart without the status:
//
//   - status 'cancelled' or 'completed': no reason was expected, so there is
//     nothing to explain and nothing to withhold. Show nothing.
//   - status 'failed' with an empty field: the run died before the column
//     shipped (`backend/migrations/versions/f2a3b4c5d6e7_add_paper_trading_
//     run_failure_reason.py:40` adds it as nullable, and no backfill writes
//     into existing rows). Saying "the platform recorded no reason" is a
//     different claim from saying "the reason is withheld", and neither of them
//     is the raw text, which the page does not have.
//
// The text itself goes through `renderStoredFailure`, the same provenance gate
// the ML job and inference surfaces use: backend-authored literals stay as they
// are, anything else keeps its words but is marked as unverified.

import { renderStoredFailure } from './mlFailureMessages';

export const NO_REASON_RECORDED =
  '這筆失敗沒有留下原因（原因欄是之後才加入的，舊記錄不會補寫）。';

// Only the failure branch can carry a reason, so the status decides whether an
// empty field is "nothing to say" or "something is missing".
export function failureReasonText(
  status: string,
  raw: string | null | undefined,
): string {
  if (status !== 'failed') return '';
  return renderStoredFailure(raw) || NO_REASON_RECORDED;
}
