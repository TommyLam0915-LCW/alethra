'use client';

import type { ReplaySession } from './ReplayController';
import { NOT_PROVIDED, type RulesState } from './strategyRules';
import { label, SESSION_STATUS_LABELS } from '@/lib/copy';
import { formatRatePercent } from '@/lib/format';

function formatScalar(value: unknown): string {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value;
  return formatValue(value);
}

/** Rates are stored as fractions; show the percent used elsewhere plus the raw stored value. */
function formatRate(value: unknown): string {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  const rate = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(rate)) return formatScalar(value);
  return `${formatRatePercent(rate)} (${String(value)})`;
}

/** Parameters arrive as nested objects/arrays; they are printed as-is, never expanded. */
function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  if (Array.isArray(value)) {
    return value.length === 0 ? '(empty)' : value.map((v) => formatScalar(v)).join(', ');
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return '(empty)';
    return entries.map(([k, v]) => `${k}=${formatScalar(v)}`).join(', ');
  }
  return formatScalar(value);
}

const EXPLICIT_KEYS = new Set([
  'template_type',
  'asset_ids',
  'start_date',
  'end_date',
  'initial_cash',
  'commission_rate',
  'slippage_rate',
  'timeframe',
  'dataset',
  'risk_parameters',
  'signal_parameters',
  'synthetic',
]);

function Row({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <tr>
      <td className="ws-muted">{label}</td>
      <td data-testid={testId}>{value}</td>
    </tr>
  );
}

/**
 * Read-only view of the rules a replay session actually runs on. Every line comes
 * from the session response or the matched experiment version; fields the API does
 * not carry are shown as not provided rather than filled with engine defaults.
 */
export default function StrategyRulesPanel({
  session,
  rules,
  hideHeader,
}: {
  session: ReplaySession | null;
  rules: RulesState;
  hideHeader?: boolean;
}) {
  const spec = rules.status === 'resolved' ? rules.version.strategy_spec ?? null : null;
  const remaining = spec
    ? Object.entries(spec).filter(([key]) => !EXPLICIT_KEYS.has(key))
    : [];

  return (
    <section className="ws-panel ws-panel-fill" data-testid="strategy-rules-panel" aria-label="Strategy rules">
      {!hideHeader && <div className="ws-panel-header">Strategy rules (read-only)</div>}
      <div className="ws-panel-body" style={{ padding: 10 }}>
        {!session ? (
          <p className="ws-muted" data-testid="rules-no-session">
            No historical replay session is bound, so there is no active strategy version to show.
            Strategy rules are created with a session in Research.
          </p>
        ) : rules.status === 'loading' ? (
          <p className="ws-muted" data-testid="rules-loading" role="status">
            Matching version {rules.versionId.slice(0, 8)}…
          </p>
        ) : rules.status === 'error' ? (
          <div className="ws-message-error" role="alert" data-testid="rules-error">
            Strategy rules could not be loaded: {rules.message}
          </div>
        ) : rules.status === 'unresolved' ? (
          <div data-testid="rules-unresolved">
            <p className="ws-message-error" role="alert">
              Strategy rules: {NOT_PROVIDED}
            </p>
            <p className="ws-muted">
              Active version {rules.versionId.slice(0, 8)}. {rules.reason} The rule contents are
              not inferred from this version.
            </p>
          </div>
        ) : rules.status === 'resolved' ? (
          <table className="ws-table ws-table-wrap" data-testid="rules-table">
            <tbody>
              <Row
                label="Experiment"
                value={`${rules.experiment.title} (${rules.experiment.status})`}
                testId="rules-experiment"
              />
              <Row
                label="Active version (session)"
                value={`${rules.versionId.slice(0, 8)} · v${rules.version.version_number}`}
                testId="rules-effective-version"
              />
              <Row
                label="Saved version (experiment)"
                value={
                  rules.experiment.current_version_id
                    ? `${rules.experiment.current_version_id.slice(0, 8)}`
                    : NOT_PROVIDED
                }
                testId="rules-saved-version"
              />
              <Row label="Strategy template" value={formatScalar(spec?.template_type)} testId="rules-template" />
              <Row label="Bound instruments" value={formatValue(spec?.asset_ids)} testId="rules-assets" />
              <Row
                label="Study period"
                value={
                  spec?.start_date || spec?.end_date
                    ? `${formatScalar(spec?.start_date)} – ${formatScalar(spec?.end_date)}`
                    : NOT_PROVIDED
                }
                testId="rules-period"
              />
              <Row label="Dataset" value={formatScalar(spec?.dataset)} testId="rules-dataset" />
              <Row
                label="Data nature"
                value={
                  spec?.synthetic === true
                    ? 'Synthetic (synthetic=true)'
                    : spec?.synthetic === false
                      ? 'Not synthetic (synthetic=false)'
                      : NOT_PROVIDED
                }
                testId="rules-data-nature"
              />
              <Row label="Timeframe (rules)" value={formatScalar(spec?.timeframe)} testId="rules-spec-timeframe" />
              <Row label="Starting virtual cash" value={formatScalar(spec?.initial_cash)} testId="rules-initial-cash" />
              <Row label="Commission rate" value={formatRate(spec?.commission_rate)} testId="rules-commission-rate" />
              <Row label="Slippage rate" value={formatRate(spec?.slippage_rate)} testId="rules-slippage-rate" />
              <Row label="Risk parameters" value={formatValue(spec?.risk_parameters)} testId="rules-risk-parameters" />
              <Row label="Signal parameters" value={formatValue(spec?.signal_parameters)} testId="rules-signal-parameters" />
              {remaining.map(([key, value]) => (
                <Row key={key} label={`Other: ${key}`} value={formatValue(value)} />
              ))}
              <Row label="Config hash" value={formatScalar(rules.version.config_hash)} testId="rules-config-hash" />
              <Row label="Locked at" value={formatScalar(rules.version.locked_at)} testId="rules-locked-at" />
              <Row
                label="Human approval time"
                value={formatScalar(rules.version.user_approved_at)}
                testId="rules-approved-at"
              />
              <Row label="Change reason" value={formatScalar(rules.version.change_reason)} testId="rules-change-reason" />
              <Row
                label="Research assumptions"
                value={formatValue(rules.version.assumptions)}
                testId="rules-assumptions"
              />
            </tbody>
          </table>
        ) : (
          <p className="ws-muted" data-testid="rules-idle">
            Rule lookup has not started (status: {rules.status}).
          </p>
        )}

        {session && rules.status === 'resolved' && (
          <p className="ws-muted ws-small" data-testid="rules-mapping-note">
            How this was matched: the session response only carries experiment_version_id, not an
            experiment id; this table matches it against each experiment&apos;s current_version_id
            (active version {rules.versionId.slice(0, 8)} = saved version{' '}
            {rules.experiment.current_version_id?.slice(0, 8) ?? NOT_PROVIDED}).
            Session status: {label(SESSION_STATUS_LABELS, session.status)}.
          </p>
        )}
      </div>
    </section>
  );
}
