// What the journal's summary banner is allowed to claim about the numbers it is shown.
//
// `/journal/trades` answers an empty filter with `total_pnl: "0"` (a real additive
// zero) and `win_rate: null` (a ratio with no observations) — see the early return in
// `backend/app/services/journal_service.py`. A verdict built with `Number()` on those
// fields cannot tell the two apart: `Number(null) === 0`, so "nothing was measured"
// fell through to the branch that says "slightly negative" in red.
//
// Every field goes through `asNumber`, which also rejects undefined and NaN. The
// response body is cast, not validated, so an absent key has to fail closed into
// "not available" rather than into a sign.

export type SummaryTone = 'positive' | 'negative' | 'neutral';

export interface SummaryStatus {
  message: string;
  tone: SummaryTone;
}

export interface SummaryInputs {
  total_trades: number;
  total_pnl: string;
  win_rate: string | null;
}

function asNumber(value: string | number | null | undefined): number | null {
  // null and '' are the two values Number() would turn into a real 0; undefined and
  // any non-numeric string become NaN, which the line below refuses to return.
  const num = value === null || value === '' ? NaN : Number(value);
  return Number.isFinite(num) ? num : null;
}

export function classifySummaryStatus(summary: SummaryInputs): SummaryStatus {
  const trades = asNumber(summary.total_trades);
  if (trades === null || trades === 0) {
    return { message: 'No trades match these filters yet', tone: 'neutral' };
  }

  const totalPnl = asNumber(summary.total_pnl);
  if (totalPnl === null) {
    return { message: 'Total P&L is not available for these filters', tone: 'neutral' };
  }
  if (totalPnl > 0) {
    const winRate = asNumber(summary.win_rate);
    if (winRate === null || winRate > 50) {
      return { message: 'Your strategies are profitable', tone: 'positive' };
    }
    return {
      message: 'Your strategies are profitable, but win rate could improve',
      tone: 'positive',
    };
  }
  if (totalPnl === 0) {
    return { message: 'Your strategies are break-even so far', tone: 'neutral' };
  }
  return totalPnl < -1000
    ? { message: 'Your strategies are losing money', tone: 'negative' }
    : { message: 'Your strategies are slightly negative', tone: 'negative' };
}
