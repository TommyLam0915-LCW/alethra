// G3-6: which tab the journal shows was React state only, so `/journal` had one
// address no matter what was on screen. A user who bookmarked the unified view,
// sent it to a colleague, or reloaded after a filter shared — and came back to
// Trades. The tab now has a spelling in the URL, and that spelling is the whole
// contract this file owns.
//
// Two rules here are load-bearing rather than cosmetic:
//
// A name that is not one of the panel's tabs is ignored, not written through.
// `?tab=../../admin` would be a route the page does not have, and `?tab=` with
// a typo would silently render nothing while the URL claimed otherwise.
//
// The page path and the API path are different strings on purpose. The unified
// data comes from `/api/v1/journal/unified`; the view a person reopens is
// `/journal?tab=unified`. A page URL that could be pointed at an API path would
// make the same word mean two things depending on who typed it.

/** The only tab whose view has a second scope control of its own. */
export const UNIFIED_TAB = 'unified';

/** Where the journal lives. Nothing below may turn this into an API path. */
export const JOURNAL_PAGE_PATH = '/journal';

/**
 * The address for one view of the journal.
 *
 * `unified` is a page of its own (`/journal/unified`) rather than only a query
 * string, because a page is what a person can name to someone else: it appears
 * in the browser history, it reloads without the client bundle deciding which
 * panel to open, and it is the address the unified view's own tab bar links to.
 * The remaining panels are sections of `/journal` and keep `?tab=`.
 */
export function journalPagePath(tab: string): string {
  return tab === UNIFIED_TAB ? `${JOURNAL_PAGE_PATH}/unified` : JOURNAL_PAGE_PATH;
}

export type JournalSource = 'all' | 'research' | 'paper';

export const JOURNAL_SOURCES: readonly JournalSource[] = ['all', 'research', 'paper'];

export const DEFAULT_JOURNAL_SOURCE: JournalSource = 'all';

/**
 * The tab named in the URL, or `fallback` when it names nothing the panel has.
 *
 * `keys` is passed in rather than duplicated here: the panel already owns the
 * tab table, and a second list of tab names is a second truth that can disagree
 * with the first.
 */
export function readJournalTab<T extends string>(
  raw: string | null | undefined,
  keys: readonly T[],
  fallback: T,
): T {
  return raw != null && (keys as readonly string[]).includes(raw) ? (raw as T) : fallback;
}

/** The ledger scope named in the URL, or the whole population. */
export function readJournalSource(raw: string | null | undefined): JournalSource {
  return raw != null && (JOURNAL_SOURCES as readonly string[]).includes(raw)
    ? (raw as JournalSource)
    : DEFAULT_JOURNAL_SOURCE;
}

/**
 * The query string that reopens this view, appended to `journalPagePath`.
 *
 * Defaults are left out, so `/journal` and `/journal?tab=trades` are one view
 * with one spelling instead of two links that could drift apart. `tab=` is not
 * written on the unified page either — the page address already says which view
 * is open, and a parameter repeating it is a second source that can disagree.
 * `source` is only written on the unified view: on every other tab it is a
 * control with no effect, which is what this round set out to stop.
 */
export function journalQueryString(
  tab: string,
  defaultTab: string,
  source: JournalSource,
): string {
  const parts: string[] = [];
  if (tab !== defaultTab && tab !== UNIFIED_TAB) parts.push(`tab=${encodeURIComponent(tab)}`);
  if (tab === UNIFIED_TAB && source !== DEFAULT_JOURNAL_SOURCE) {
    parts.push(`source=${encodeURIComponent(source)}`);
  }
  return parts.length > 0 ? `?${parts.join('&')}` : '';
}

/** The whole address — path and query decided together, so they cannot disagree. */
export function journalViewUrl(tab: string, defaultTab: string, source: JournalSource): string {
  return `${journalPagePath(tab)}${journalQueryString(tab, defaultTab, source)}`;
}
