const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

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
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
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
  if (value === null || value === undefined || value === '') return '—';
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
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
