const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

const NAIVE_DATE_TIME = /^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/;

/**
 * Reads a timestamp the API sent as the instant it names, or null if it cannot.
 *
 * The platform's clock convention is naive UTC: `app/lib/timeutil.utcnow()`
 * returns a datetime with no tzinfo and 188 of the ORM's 210 `DateTime` column
 * declarations carry no timezone, so Postgres stores and returns them without an
 * offset and Pydantic serialises them as `2026-11-15T13:59:47.794757` — measured
 * on the acceptance stack, where `experiments.updated_at` is
 * `timestamp without time zone`. JavaScript reads that shape as *local* wall
 * clock, so every derived value (rendered time, elapsed-time gate, chart axis)
 * is wrong by the viewer's whole UTC offset. Appending `Z` to exactly the
 * offset-less date-time shape recovers the written instant; strings that already
 * carry `Z` or `±hh:mm`, and `Date` objects, are left as they are.
 *
 * A bare `YYYY-MM-DD` keeps its ES-spec reading as UTC midnight: it is a
 * calendar day, not a clock time, and shifting it would move the day label.
 */
export function parseApiTimestamp(value: string | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const instant = NAIVE_DATE_TIME.test(value) ? new Date(`${value}Z`) : new Date(value);
  return Number.isNaN(instant.getTime()) ? null : instant;
}

/**
 * Renders a date as `YYYY/MM/DD` in local time.
 *
 * Bare `toLocaleDateString('zh-Hant')` produced `2025/12/9` (unpadded) and did so
 * inconsistently against the two-digit dates in filter inputs. Date-only strings are
 * formatted from their literal parts: `new Date('2025-12-09')` is UTC midnight, which
 * renders as the previous day for any client west of UTC.
 */
export function formatDate(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'string') {
    const dateOnly = DATE_ONLY.exec(value);
    if (dateOnly) return `${dateOnly[1]}/${dateOnly[2]}/${dateOnly[3]}`;
  }
  const date = parseApiTimestamp(value);
  if (!date) return '—';
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}/${month}/${day}`;
}

/**
 * Renders a timestamp as `YYYY/MM/DD HH:mm` in local time.
 *
 * `toLocaleString('zh-Hant')` produced `2026/10/30 上午8:57:59` — 12-hour 上午/下午 markers
 * next to a 24-hour date, with seconds no research view reads. 24-hour keeps ordering
 * legible in journal and event lists.
 */
export function formatDateTime(value: string | Date | null | undefined): string {
  const date = parseApiTimestamp(value);
  if (!date) return '—';
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${formatDate(date)} ${hours}:${minutes}`;
}

/**
 * Renders a cost rate stored as a fraction of trade value (0.001 → `0.10%`).
 *
 * The engine stores commission and slippage as fractions, so a raw `0.001` is
 * easy to read as "0.001%" — a hundred-fold mistake. Sub-0.01% rates keep three
 * decimals so a real difference is never rounded away to `0.00%`.
 */
export function formatRatePercent(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const rate = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(rate)) return String(value);
  const percent = rate * 100;
  const decimals = percent !== 0 && Math.abs(percent) < 0.1 ? 3 : 2;
  return `${percent.toFixed(decimals)}%`;
}

/**
 * Renders a stored amount the API may not have measured (`$12.34` / `—`).
 *
 * The journal responses serialise Decimal columns as strings and send `null` for
 * a value no writer recorded. `Number(null)` is `0` and `null * 100` is `0`, so
 * arithmetic on an absent money value silently becomes the claim "this was zero",
 * and calling `.toFixed(2)` on it throws instead of rendering. Absence renders
 * as `—`; a measured zero keeps its digits, because "settled at $0.00" and "never
 * settled" are different claims.
 */
export function formatMoney(value: number | string | null | undefined): string {
  return formatNumber(value, 2, '$');
}

/**
 * Renders a plain numeric measure with fixed decimals, or `—` when absent.
 *
 * `scale` converts a stored unit into the displayed one (0.625 → 62.5 with
 * `scale: 100` for a proportion the API sends as a fraction). The multiplication
 * happens here, in one place, so a percent-scaled field and a fraction-scaled
 * field cannot be read with the same wrong assumption.
 */
export function formatNumber(
  value: number | string | null | undefined,
  decimals: number = 2,
  prefix: string = '',
  scale: number = 1,
): string {
  if (value === null || value === undefined || value === '') return '—';
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) return String(value);
  const scaled = num * scale;
  // A negative P&L reads as `-$42.10`: the sign belongs to the amount, and
  // `$-42.10` puts it inside the unit.
  const sign = scaled < 0 ? '-' : '';
  return `${sign}${prefix}${Math.abs(scaled).toFixed(decimals)}`;
}

/**
 * Renders a percentage the API already scaled to points (`60.00` → `60.0%`).
 *
 * `/journal/summary` and `/journal/skill-attribution` return win rate and drawdown
 * as percent points; `/journal/compare` and `/journal/unified` return win rate as a
 * fraction. Multiplying the points form by 100 again would print a 60% skill as
 * 6000%, so the two scales get two names.
 */
export function formatPercentPoints(
  value: number | string | null | undefined,
  decimals: number = 1,
): string {
  if (value === null || value === undefined || value === '') return '—';
  return `${formatNumber(value, decimals)}%`;
}

/**
 * Renders a proportion the API sends as a 0-1 fraction (`0.625` → `62.5%`).
 *
 * `/journal/compare` and `/journal/unified` report win rate in this scale while
 * `/journal/summary` reports the same quantity in points, so the two need
 * different names: multiplying the points form here would print a 62.5% skill as
 * 6250%, and skipping the scale would print it as 0.6%.
 */
export function formatPercentFraction(
  value: number | string | null | undefined,
  decimals: number = 1,
): string {
  if (value === null || value === undefined || value === '') return '—';
  return `${formatNumber(value, decimals, '', 100)}%`;
}

/**
 * Renders a small flat parameter map as `key=value` pairs.
 *
 * Run methodology stores signal and risk parameters as nested objects; two runs
 * can only be compared if the stored values are readable without parsing JSON.
 * An empty declared block is distinguished from a missing one.
 */
export function formatParamMap(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value !== 'object') return String(value);
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return '— (none declared)';
  return entries.map(([key, val]) => `${key}=${String(val)}`).join(', ');
}
