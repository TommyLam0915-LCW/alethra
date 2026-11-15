// Read-only strategy-spec logic for the revision form.
//
// It lives here rather than inside RevisionPanel because the repo's unit lane is
// `node --test` with type stripping, which cannot import a JSX module: keeping
// the decision pure means the function the component calls is the function the
// tests exercise. Interaction coverage stays with the Playwright spec.

export const SPEC_DEFAULTS: Record<string, any> = {
  schema_version: '1.0',
  frequency: '1d',
  signal_parameters: {},
  entry_rule: {},
  exit_rule: {},
  sizing_rule: {},
  risk_parameters: {},
  execution_timing: { execute_at: 'next_open' },
  commission_rate: 0,
  slippage_rate: 0,
  benchmark_id: null,
  currency: 'USD',
};

// Older versions predate some fields, so comparing raw objects would report
// schema_version/frequency as edits.
//
// `timeframe` belongs in this set: the backend persists the declaration and
// counts it as a strategy change, so leaving it out would preview an
// undeclared-to-declared revision as "Nothing changed yet" while the API
// accepts it as strategy_only.
const SPEC_FIELDS = new Set([
  'schema_version', 'template_type', 'asset_ids', 'frequency', 'timeframe', 'start_date',
  'end_date', 'initial_cash', 'signal_parameters', 'entry_rule', 'exit_rule',
  'sizing_rule', 'risk_parameters', 'execution_timing', 'commission_rate',
  'slippage_rate', 'benchmark_id', 'currency',
]);

export const IGNORED_FIELDS = new Set(['user_approved_at']);

const DAILY_GRAIN = '1d';

// The grain-sensitive keys the backend keeps out of the revision surface,
// each with the value this surface is willing to carry. Judged by value rather
// than by key presence, because the official authoring API accepts and persists
// an explicit daily declaration: a version carrying timeframe="1d" is an
// ordinary daily strategy and must stay revisable, while any other declaration
// stays refused instead of being downgraded silently. The normalisation mirrors
// the backend validator (trim + lowercase), and a key that is present but
// null/empty is an illegal declaration, not "undeclared".
const REVISION_KEY_CAPABILITY: Record<string, (value: unknown) => boolean> = {
  timeframe: (value) =>
    typeof value === 'string' && value.trim().toLowerCase() === DAILY_GRAIN,
  // No session shape is authorisable by this form, whatever its value.
  session: () => false,
};

export function unsupportedRevisionKeys(spec: any): string[] {
  if (!spec || typeof spec !== 'object') return [];
  return Object.keys(REVISION_KEY_CAPABILITY)
    .filter((key) => Object.prototype.hasOwnProperty.call(spec, key))
    .filter((key) => !REVISION_KEY_CAPABILITY[key](spec[key]))
    .sort();
}

export function canonicalSpec(spec: any): Record<string, any> {
  const merged = { ...SPEC_DEFAULTS, ...(spec ?? {}) };
  return Object.fromEntries(Object.entries(merged).filter(([key]) => SPEC_FIELDS.has(key)));
}

export function flatten(spec: any, prefix = ''): Record<string, any> {
  const flat: Record<string, any> = {};
  for (const [key, value] of Object.entries(spec ?? {})) {
    const path = `${prefix}${key}`;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      Object.assign(flat, flatten(value, `${path}.`));
    } else {
      flat[path] = value;
    }
  }
  return flat;
}

export function diffSpecs(oldSpec: any, newSpec: any): { fields: string[]; kind: string } {
  const a = flatten(canonicalSpec(oldSpec));
  const b = flatten(canonicalSpec(newSpec));
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  // Array leaves (asset_ids) must compare by value: two identical arrays built
  // separately are different references, which reported a phantom edit.
  const serialize = (value: any) => (value === undefined ? '\u0000' : JSON.stringify(value));
  const fields = Array.from(keys)
    .filter((key) => !IGNORED_FIELDS.has(key) && serialize(a[key]) !== serialize(b[key]))
    .sort();
  const period = fields.some((f) => f === 'start_date' || f === 'end_date');
  const strategy = fields.some((f) => f !== 'start_date' && f !== 'end_date');
  const kind = period && strategy ? 'strategy_and_period' : period ? 'period_only' : 'strategy_only';
  return { fields, kind };
}

export function isUnsupportedSpec(spec: any): { blocked: boolean; reason: string } {
  if (!spec) return { blocked: true, reason: 'The source version has no strategy settings, so a revision cannot be created from it.' };
  const frequency = String(spec.frequency ?? '1d');
  if (frequency !== '1d') {
    return {
      blocked: true,
      reason: `This version runs at ${frequency}. The revision form supports daily bars (1d) only, and the backend rejects other frequencies, so nothing is downgraded automatically.`,
    };
  }
  const offenders = unsupportedRevisionKeys(spec);
  if (offenders.length) {
    return {
      blocked: true,
      reason: `This version carries ${offenders.join(' / ')} settings that are outside what this revision form can authorise. The backend will not downgrade them to daily bars, so no revision is offered here — create a supported daily experiment instead.`,
    };
  }
  return { blocked: false, reason: '' };
}
