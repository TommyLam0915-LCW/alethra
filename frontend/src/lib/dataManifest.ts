/**
 * Pure helpers for the read-only data manifest
 * (`POST /api/v1/strategy-specs/data-manifest`, backend/app/api/data_manifest.py).
 *
 * The manifest's sentences — scope note, blockers, remedies, run behaviour,
 * `cannot_prove` — are produced by the backend from the same code a run
 * executes, so this module never paraphrases them. It does the two things a
 * page cannot leave to chance: it maps the status token to a label and a colour
 * (with a branch that refuses to colour an unknown token as success), and it
 * decides how a value the server did not return is displayed — as `UNKNOWN`,
 * never as `0` or an empty cell that reads like a known zero.
 */

export type ManifestStatus = 'ready' | 'partial' | 'blocked' | 'invalid_spec';

export interface GrainFact {
  interval_minutes: number | null;
  label: string;
  bar_count: number;
}

export interface CalendarFact {
  market: string | null;
  reason: string | null;
  coverage_start: string | null;
  coverage_end: string | null;
  expected_known_open_sessions: number;
  sessions_the_calendar_cannot_judge: number;
  missing_known_open_sessions: string[];
  missing_known_open_count: number;
  bars_on_days_the_calendar_closes: string[];
  note: string;
}

export interface ManifestInstrument {
  instrument_id: string;
  role: string;
  status: string;
  source: string | null;
  provider_id: string | null;
  provider_ids: string[];
  data_nature: string | null;
  bar_count: number;
  first_bar_date: string | null;
  last_bar_date: string | null;
  grains: GrainFact[];
  duplicate_dates: string[];
  calendar: CalendarFact | null;
}

export interface ManifestIssue {
  code: string;
  instrument_id: string | null;
  message: string;
  remedy: string | null;
}

export interface DataManifest {
  status: ManifestStatus | string;
  template_type: string | null;
  window: { start_date?: string | null; end_date?: string | null };
  provider_id_bound: string | null;
  instruments: ManifestInstrument[];
  dataset_checksum: string | null;
  checksum_basis: string;
  bars_loaded: number;
  blockers: ManifestIssue[];
  warnings: ManifestIssue[];
  run_behavior: Record<string, string>;
  engine_basis: string[];
  scope_note: string;
  cannot_prove: string[];
}

export interface StatusCopy {
  label: string;
  detail: string;
  tone: 'ok' | 'warn' | 'muted';
}

/**
 * One tone→colour map for the two read-only verdict panels in this app (spec
 * preflight and the data manifest). `--positive-text` and `--warning-text` are
 * the tints that meet the 4.5:1 floor on this canvas; a verdict must not read
 * as a different claim depending on which page rendered it.
 */
export const STATUS_TONE_COLOUR: Record<StatusCopy['tone'], string> = {
  ok: 'var(--positive-text)',
  warn: 'var(--warning-text)',
  muted: 'var(--text-muted)',
};

export function statusToneColour(tone: StatusCopy['tone']): string {
  return STATUS_TONE_COLOUR[tone];
}

export const MANIFEST_STATUS_COPY: Record<ManifestStatus, StatusCopy> = {
  ready: {
    label: 'Runnable off stored bars',
    detail:
      'Every instrument a stored-mode run reads is present for this window. It is a '
      + 'statement about rows on this server, not permission to run and not a claim '
      + 'that the prices are real, correct or profitable.',
    tone: 'ok',
  },
  partial: {
    label: 'Incomplete for stored mode',
    detail:
      'At least one traded instrument has no stored bars, so the verdict depends on '
      + 'the data_source the run is given. Read the per-mode lines below.',
    tone: 'warn',
  },
  blocked: {
    label: 'Will not run',
    detail:
      'A run given this window refuses before it trades. The blockers below carry the '
      + 'server’s own refusal sentence and the change that removes it.',
    tone: 'warn',
  },
  invalid_spec: {
    label: 'Spec is not executable',
    detail:
      'The spec did not pass the contract gate, so no dataset was resolved and no '
      + 'checksum exists. Nothing has been created, queued or run.',
    tone: 'muted',
  },
};

export function manifestStatusCopy(status: string): StatusCopy {
  const known = MANIFEST_STATUS_COPY[status as ManifestStatus];
  if (known) return known;
  // A token this build does not know must not inherit the 'ready' colour: an
  // unrecognised verdict displayed in green is a claim the server never made.
  return {
    label: `Unrecognised verdict: ${status}`,
    detail: 'This frontend build does not know this status, so it makes no claim about it.',
    tone: 'muted',
  };
}

/**
 * The three roles a row can carry, and why each one matters for a run.
 * Basis: app/engine/backtest.py — the union at the head of run_backtest loads
 * benchmark and every asset id, the stored-mode refusal loop walks asset_ids,
 * `symbol = asset_ids[0]` picks the traded series, and a missing benchmark
 * downgrades to a warning instead of a refusal.
 */
export const ROLE_LEGEND: Record<string, string> = {
  traded: 'the series the template trades',
  loaded_not_traded: 'loaded and counted in the checksum, but not traded by this template',
  benchmark: 'comparison only — a run continues with a warning if it is missing',
};

export function roleLegend(role: string): string {
  return ROLE_LEGEND[role] ?? `role "${role}" is not described by this frontend build`;
}

export const UNKNOWN = 'UNKNOWN';

/** A value the server omitted. 0 and '' are values; only null/undefined are not. */
export function display(value: unknown): string {
  if (value === null || value === undefined) return UNKNOWN;
  if (typeof value === 'string') return value.trim() === '' ? UNKNOWN : value;
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : UNKNOWN;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
}

/**
 * A checksum is only comparable to another checksum while it is whole, so the
 * display never truncates and never re-types it: the string is rendered as
 * returned and is selectable. A dataset that was not read has no checksum, and
 * that is shown as `none` rather than as an empty-looking hash.
 */
export function displayChecksum(checksum: string | null | undefined): string {
  if (checksum === null || checksum === undefined) return 'none — no dataset was read';
  return checksum.trim() === '' ? 'none — the server returned an empty checksum' : checksum;
}

/**
 * A short preview of a date list that keeps the total visible. A window with 54
 * colliding dates must not read as a window with 5: the hidden count and the
 * total are both returned so the caller can state them instead of implying the
 * list ended.
 */
export function previewDates(
  dates: string[] | null | undefined,
  limit = 5,
): { shown: string[]; hidden: number; total: number } {
  const values = dates ?? [];
  return {
    shown: values.slice(0, limit),
    hidden: Math.max(0, values.length - limit),
    total: values.length,
  };
}

/**
 * Fingerprint of the parameters a manifest verdict describes.
 *
 * `user_approved_at` is the one excluded field: the create/revision paths
 * already define it as not part of the spec’s substance
 * (`IGNORED_CHANGE_FIELDS` in app/api/experiments.py:575, and it is popped
 * before comparison at :461), while the stamp itself changes on every render —
 * including it would make every verdict stale on sight.
 *
 * Keys are sorted so an unchanged window with a reordered risk-parameter object
 * cannot look like a new request, and `undefined` is dropped so a field that was
 * never filled in is not distinguished from one explicitly cleared.
 */
export function specFingerprint(
  spec: Record<string, unknown>,
  providerId?: string | null,
): string {
  const { user_approved_at: _ignored, ...rest } = spec;
  return stableStringify({ spec: rest, provider_id: providerId ?? null });
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${JSON.stringify(k)}:${stableStringify(v)}`);
  return `{${entries.join(',')}}`;
}

/** True when the displayed verdict describes parameters the form no longer holds. */
export function isStale(checked: string | null, current: string): boolean {
  return checked !== null && checked !== current;
}

/**
 * The code the server puts on the two-providers-in-one-window blocker
 * (`MULTIPLE_PROVIDERS`, backend/app/api/data_manifest.py:53). Its remedy names
 * the `provider_id` query parameter, and the only values worth offering are the
 * ones the server listed — so the control is built from the response, never
 * from a guessed provider list.
 *
 * The blocker itself is not instrument-scoped: it emits with
 * `instrument_id=None` carrying the engine's refusal sentence
 * (data_manifest.py:422-434), while the rows the engine could not merge are
 * marked `status: "ambiguous"` with their `provider_ids` (:438-446). Reading
 * candidates off the blocker's instrument id would therefore always find
 * nothing, so the instrument set comes from the rows.
 */
export const MULTIPLE_PROVIDERS = 'multiple_providers';

export function providerCandidates(manifest: DataManifest | null): string[] {
  if (!manifest) return [];
  if (!manifest.blockers.some((issue) => issue.code === MULTIPLE_PROVIDERS)) return [];
  const candidates = new Set<string>();
  for (const row of manifest.instruments) {
    if (row.status !== 'ambiguous') continue;
    for (const providerId of row.provider_ids) candidates.add(providerId);
  }
  return Array.from(candidates).sort();
}
