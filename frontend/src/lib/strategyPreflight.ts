/**
 * Pure helpers for the read-only strategy catalogue and spec preflight page.
 * Nothing here calls the API or creates work; the page owns those actions.
 */

export type PreflightStatus = 'valid' | 'invalid' | 'unsupported';

export interface PreflightError {
  field: string;
  message: string;
}

export interface PreflightResult {
  status: PreflightStatus;
  template_type: string | null;
  errors: PreflightError[];
  reason?: string;
  scope_note?: string;
}

export interface SignalParameter {
  name: string;
  type: string;
  min: number;
  max: number;
  required: boolean;
}

export interface StrategyTemplateDescriptor {
  name: string;
  label: string;
  purpose: string;
  required_fields: string[];
  signal_parameters: SignalParameter[];
  known_limits: string[];
  supported: boolean;
}

export interface UnsupportedTemplate {
  name: string;
  label: string;
  reason: string;
}

export interface SpecField {
  name: string;
  type: string;
  required: boolean;
  constraints?: Record<string, string | number>;
}

export interface StrategyTemplateCatalog {
  templates: StrategyTemplateDescriptor[];
  unsupported: UnsupportedTemplate[];
  spec_fields: SpecField[];
  scope_note: string;
}

export const PREFLIGHT_STATUS_COPY: Record<
  PreflightStatus,
  { label: string; detail: string; tone: 'ok' | 'warn' | 'muted' }
> = {
  valid: {
    label: 'Matches the current contract',
    detail:
      'Field types and template parameters match what the create and run paths accept. This does not show that the strategy works, that data is available, or that returns are likely.',
    tone: 'ok',
  },
  invalid: {
    label: 'Field errors',
    detail:
      'Correct the fields below and preflight again. Nothing has been created, queued or run.',
    tone: 'warn',
  },
  unsupported: {
    label: 'Capability not supported',
    detail:
      'The current engine cannot run this spec. Nothing has been created, queued or run.',
    tone: 'muted',
  },
};

const BASE_SPEC: Record<string, unknown> = {
  schema_version: '1.0',
  template_type: 'buy_and_hold',
  asset_ids: ['DEMO-ALPHA'],
  frequency: '1d',
  start_date: '2024-01-01',
  end_date: '2024-12-31',
  initial_cash: 10000,
  commission_rate: 0.001,
  slippage_rate: 0.0005,
  signal_parameters: {},
  risk_parameters: {},
};

const SIGNAL_PARAMETER_EXAMPLES: Record<string, Record<string, number>> = {
  buy_and_hold: {},
  sma_crossover: { short_window: 10, long_window: 30 },
  price_change_fixed_hold: { lookback_days: 5, threshold_pct: 0.05, holding_days: 10 },
};

export function buildDefaultSpec(templateName: string): Record<string, unknown> {
  return {
    ...BASE_SPEC,
    template_type: templateName,
    signal_parameters: { ...(SIGNAL_PARAMETER_EXAMPLES[templateName] ?? {}) },
    risk_parameters: {},
  };
}

export type ParseSpecResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string };

export function parseSpecText(text: string): ParseSpecResult {
  if (!text.trim()) return { ok: false, error: 'Enter a strategy spec before preflighting.' };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    const detail = err instanceof Error ? err.message : 'Invalid JSON';
    return { ok: false, error: `The spec is not valid JSON: ${detail}` };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, error: 'The spec must be a JSON object.' };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

export function formatConstraint(field: SpecField): string {
  if (!field.constraints) return field.type;
  const parts = Object.entries(field.constraints).map(([key, value]) => `${key} ${value}`);
  return `${field.type} (${parts.join(', ')})`;
}
