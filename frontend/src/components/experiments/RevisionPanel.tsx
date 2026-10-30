'use client';

import { useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import { RUN_STATUS_LABELS, STRATEGY_TEMPLATE_LABELS, label } from '@/lib/copy';

interface StrategyVersion {
  id: string;
  version_number: number;
  strategy_spec: any;
  assumptions?: any;
  change_reason: string | null;
  config_hash: string | null;
  locked_at: string | null;
  created_at: string;
}

interface RunRow {
  id: string;
  experiment_version_id: string;
  status: string;
  metrics?: any;
  completed_at?: string | null;
}

interface RevisionMeta {
  of_version_id: string;
  of_version_number: number;
  expectation: string | null;
  changed_fields: string[];
  change_kind: string;
  outcome: string | null;
  met_expectation: boolean | null;
  outcome_recorded_at: string | null;
}

interface Props {
  experimentId: string;
  experimentStatus: string;
  currentVersionId: string | null;
  versions: StrategyVersion[];
  runs: RunRow[];
  onChanged: () => Promise<void> | void;
}

const TEMPLATES = Object.entries(STRATEGY_TEMPLATE_LABELS).map(([value, text]) => ({
  value,
  label: text,
}));

const CHANGE_KIND_LABEL: Record<string, string> = {
  strategy_only: 'Strategy settings changed',
  period_only: 'Research period only',
  strategy_and_period: 'Strategy settings and research period changed',
};

const IGNORED_FIELDS = new Set(['user_approved_at']);

// Defaults applied by the backend schema. Older versions predate them, so
// comparing raw objects would report schema_version/frequency as edits.
const SPEC_DEFAULTS: Record<string, any> = {
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

const SPEC_FIELDS = new Set([
  'schema_version', 'template_type', 'asset_ids', 'frequency', 'start_date',
  'end_date', 'initial_cash', 'signal_parameters', 'entry_rule', 'exit_rule',
  'sizing_rule', 'risk_parameters', 'execution_timing', 'commission_rate',
  'slippage_rate', 'benchmark_id', 'currency',
]);

function canonicalSpec(spec: any): Record<string, any> {
  const merged = { ...SPEC_DEFAULTS, ...(spec ?? {}) };
  return Object.fromEntries(Object.entries(merged).filter(([key]) => SPEC_FIELDS.has(key)));
}

function isUnsupportedSpec(spec: any): { blocked: boolean; reason: string } {
  if (!spec) return { blocked: true, reason: 'The source version has no strategy settings, so a revision cannot be created from it.' };
  const frequency = String(spec.frequency ?? '1d');
  if (frequency !== '1d') {
    return {
      blocked: true,
      reason: `This version runs at ${frequency}. The revision form supports daily bars (1d) only, and the backend rejects other frequencies, so nothing is downgraded automatically.`,
    };
  }
  if (spec.session || spec.timeframe) {
    return {
      blocked: true,
      reason: 'This version contains intraday (session/timeframe) settings that are outside what this revision form can authorise. Do not change them here — that would diverge from how the strategy executes.',
    };
  }
  return { blocked: false, reason: '' };
}

function flatten(spec: any, prefix = ''): Record<string, any> {
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

function diffSpecs(oldSpec: any, newSpec: any): { fields: string[]; kind: string } {
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

function num(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function latestCompletedRun(runs: RunRow[], versionId: string): RunRow | null {
  return (
    runs.find((r) => r.experiment_version_id === versionId && r.status === 'completed') ??
    null
  );
}

// The quota guard answers with a machine code, which is useless inline: say what
// did not happen, and where the owner can see why.
function describeError(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback;
  if (err.status === 429 && err.message === 'quota_exceeded') {
    return 'No backtest was started and no results were produced: the research run quota for this plan is used up. Check used versus limit on the Subscription plans page, or try again later.';
  }
  return err.message;
}

export default function RevisionPanel({
  experimentId,
  experimentStatus,
  currentVersionId,
  versions,
  runs,
  onChanged,
}: Props) {
  const revisionVersions = useMemo(
    () =>
      versions
        .filter((v) => v.assumptions?.revision)
        .sort((a, b) => b.version_number - a.version_number),
    [versions]
  );

  const defaultBaseId = currentVersionId ?? versions[0]?.id ?? '';
  const [baseId, setBaseId] = useState(defaultBaseId);
  const base = versions.find((v) => v.id === baseId) ?? versions[0] ?? null;

  const [formOpen, setFormOpen] = useState(false);
  const [templateType, setTemplateType] = useState('');
  const [assetIds, setAssetIds] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [initialCash, setInitialCash] = useState('');
  const [commissionRate, setCommissionRate] = useState('');
  const [slippageRate, setSlippageRate] = useState('');
  const [shortWindow, setShortWindow] = useState('');
  const [longWindow, setLongWindow] = useState('');
  const [lookbackDays, setLookbackDays] = useState('');
  const [thresholdPct, setThresholdPct] = useState('');
  const [holdingDays, setHoldingDays] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [stopPct, setStopPct] = useState('');
  const [targetPrice, setTargetPrice] = useState('');
  const [targetPct, setTargetPct] = useState('');
  const [changeReason, setChangeReason] = useState('');
  const [expectation, setExpectation] = useState('');
  const [requestId, setRequestId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');

  const [runningVersionId, setRunningVersionId] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState('');
  const [actionError, setActionError] = useState('');

  const [outcomeDraft, setOutcomeDraft] = useState<Record<string, { met: string; text: string }>>({});
  const [savingOutcomeId, setSavingOutcomeId] = useState<string | null>(null);

  if (!base) return null;

  const unsupported = isUnsupportedSpec(base.strategy_spec);
  const spec = base.strategy_spec ?? {};

  const seedForm = (version: StrategyVersion) => {
    const s = version.strategy_spec ?? {};
    setTemplateType(s.template_type ?? 'buy_and_hold');
    setAssetIds((s.asset_ids ?? []).join(', '));
    setStartDate(s.start_date ?? '');
    setEndDate(s.end_date ?? '');
    setInitialCash(String(s.initial_cash ?? ''));
    setCommissionRate(String(s.commission_rate ?? 0));
    setSlippageRate(String(s.slippage_rate ?? 0));
    setShortWindow(String(s.signal_parameters?.short_window ?? ''));
    setLongWindow(String(s.signal_parameters?.long_window ?? ''));
    setLookbackDays(String(s.signal_parameters?.lookback_days ?? ''));
    setThresholdPct(String(s.signal_parameters?.threshold_pct ?? ''));
    setHoldingDays(String(s.signal_parameters?.holding_days ?? ''));
    setStopPrice(String(s.risk_parameters?.stop_price ?? ''));
    setStopPct(String(s.risk_parameters?.stop_pct ?? ''));
    setTargetPrice(String(s.risk_parameters?.target_price ?? ''));
    setTargetPct(String(s.risk_parameters?.target_pct ?? ''));
    setChangeReason('');
    setExpectation('');
    setRequestId(newRequestId());
    setFormError('');
  };

  const openForm = () => {
    if (unsupported.blocked) return;
    seedForm(base);
    setFormOpen(true);
  };

  const selectBase = (id: string) => {
    setBaseId(id);
    const next = versions.find((v) => v.id === id);
    if (next && formOpen) seedForm(next);
  };

  const buildSignalParameters = (): Record<string, number> => {
    if (templateType === 'sma_crossover') {
      const params: Record<string, number> = {};
      const short = num(shortWindow);
      const long = num(longWindow);
      if (short !== null) params.short_window = short;
      if (long !== null) params.long_window = long;
      return params;
    }
    if (templateType === 'price_change_fixed_hold') {
      const params: Record<string, number> = {};
      const lookback = num(lookbackDays);
      const threshold = num(thresholdPct);
      const holding = num(holdingDays);
      if (lookback !== null) params.lookback_days = lookback;
      if (threshold !== null) params.threshold_pct = threshold;
      if (holding !== null) params.holding_days = holding;
      return params;
    }
    return {};
  };

  const buildRiskParameters = (): Record<string, number> => {
    const params: Record<string, number> = { ...(spec.risk_parameters ?? {}) };
    const entries: Array<[string, string]> = [
      ['stop_price', stopPrice],
      ['stop_pct', stopPct],
      ['target_price', targetPrice],
      ['target_pct', targetPct],
    ];
    for (const [key, raw] of entries) {
      if (raw.trim() === '') {
        delete params[key];
        continue;
      }
      const value = num(raw);
      if (value !== null) params[key] = value;
    }
    return params;
  };

  const draftSpec = {
    ...spec,
    schema_version: spec.schema_version ?? '1.0',
    template_type: templateType || spec.template_type,
    asset_ids: assetIds
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    frequency: '1d',
    start_date: startDate,
    end_date: endDate,
    initial_cash: num(initialCash) ?? spec.initial_cash,
    signal_parameters: buildSignalParameters(),
    risk_parameters: buildRiskParameters(),
    commission_rate: num(commissionRate) ?? 0,
    slippage_rate: num(slippageRate) ?? 0,
  };

  const preview = diffSpecs(spec, draftSpec);
  const baseFlat = flatten(canonicalSpec(spec));
  const draftFlat = flatten(canonicalSpec(draftSpec));

  const submitRevision = async () => {
    setFormError('');
    if (!changeReason.trim()) {
      setFormError('Add a reason for the change: a version number alone will not explain what you were trying to find out.');
      return;
    }
    if (preview.fields.length === 0) {
      setFormError('Nothing has changed yet. Change at least one field.');
      return;
    }
    if (!draftSpec.asset_ids.length) {
      setFormError('Enter at least one research asset.');
      return;
    }
    if (!draftSpec.start_date || !draftSpec.end_date) {
      setFormError('Enter both a start and an end date for the research period.');
      return;
    }

    setSubmitting(true);
    try {
      await api.revisions.create(experimentId, {
        base_version_id: base.id,
        strategy_spec: draftSpec,
        change_reason: changeReason.trim(),
        expectation: expectation.trim() || null,
        request_id: requestId || undefined,
      });
      setFormOpen(false);
      setActionError('');
      setActionNotice(
        `Revision created from v${base.version_number}. The source version was not overwritten.`
      );
      await onChanged();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : 'The revision could not be created';
      setFormError(`${detail} (your input is kept, so you can retry directly)`);
    } finally {
      setSubmitting(false);
    }
  };

  const runVersion = async (version: VersionRowForRun) => {
    setRunningVersionId(version.id);
    setActionError('');
    setActionNotice('');
    try {
      const run = await api.runs.create(version.id);
      const ret = run?.metrics?.total_return_pct;
      const status = run?.status ? label(RUN_STATUS_LABELS, run.status) : 'Unknown';
      setActionNotice(
        run?.status === 'completed'
          ? `v${version.version_number} backtest completed${ret != null ? `: total return ${Number(ret).toFixed(2)}%` : ''}.`
          : `v${version.version_number} backtest status: ${status}.`
      );
      await onChanged();
    } catch (err) {
      setActionError(describeError(err, 'The backtest could not be started'));
    } finally {
      setRunningVersionId(null);
    }
  };

  const saveOutcome = async (version: StrategyVersion, meta: RevisionMeta) => {
    const draft = outcomeDraft[version.id] ?? {
      met: meta.met_expectation === null ? '' : String(meta.met_expectation),
      text: meta.outcome ?? '',
    };
    if (draft.met === '' || !draft.text.trim()) {
      setActionError('Choose whether the outcome matched your expectation, and write a conclusion.');
      return;
    }
    setSavingOutcomeId(version.id);
    setActionError('');
    try {
      await api.revisions.recordOutcome(experimentId, version.id, {
        met_expectation: draft.met === 'true',
        outcome: draft.text.trim(),
      });
      setActionNotice(`Recorded the result for v${version.version_number}.`);
      await onChanged();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : 'The result could not be recorded';
      setActionError(detail);
    } finally {
      setSavingOutcomeId(null);
    }
  };

  return (
    <div className="card" style={{ marginBottom: '20px' }} data-testid="revision-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ margin: 0 }}>Strategy revisions</h3>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '13px' }}
          data-testid="revision-open"
          onClick={openForm}
          disabled={unsupported.blocked || submitting}
        >
          {formOpen ? 'Reset form' : 'Create revision'}
        </button>
      </div>

      <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: 0 }}>
        A revision creates a new version; the source version is not overwritten. The source
        version, the reason for the change and the expected outcome stay in the version history.
      </p>

      {unsupported.blocked && (
        <div
          style={{ padding: '12px', backgroundColor: 'rgba(245,158,11,0.14)', borderRadius: '6px', fontSize: '13px', color: 'var(--warning)', marginBottom: '12px' }}
          data-testid="revision-unsupported"
        >
          <strong>Revisions cannot be created in the UI for this version:</strong> {unsupported.reason}
        </div>
      )}

      {!formOpen && (
        <div style={{ marginBottom: '12px' }}>
          <label htmlFor="revision-base-select" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
            Revision source version
          </label>
          <select
            id="revision-base-select"
            className="input"
            style={{ maxWidth: '260px', fontSize: '13px' }}
            value={base.id}
            onChange={(e) => selectBase(e.target.value)}
            data-testid="revision-base-select"
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_number}
                {v.id === currentVersionId ? ' (current)' : ''}
                {v.locked_at ? ' · locked' : ''}
              </option>
            ))}
          </select>
        </div>
      )}

      {formOpen && (
        <div
          style={{ padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', marginBottom: '16px' }}
          data-testid="revision-form"
        >
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Source: v{base.version_number}
            {base.locked_at ? ' (locked — the original version is kept after the revision)' : ''}
            {experimentStatus === 'locked' ? ' · This experiment is locked, so the revision will not replace the current version&apos;s metrics' : ''}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
            <Field label="Strategy template" htmlFor="revision-template">
              <select
                id="revision-template"
                className="input"
                value={templateType}
                onChange={(e) => setTemplateType(e.target.value)}
                data-testid="revision-template"
                style={{ width: '100%' }}
              >
                {TEMPLATES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Research assets (comma-separated)" htmlFor="revision-assets">
              <input
                id="revision-assets"
                className="input"
                value={assetIds}
                onChange={(e) => setAssetIds(e.target.value)}
                data-testid="revision-assets"
                style={{ width: '100%' }}
              />
            </Field>
            <Field label="Start date" htmlFor="revision-start">
              <input id="revision-start" type="date" className="input" value={startDate}
                onChange={(e) => setStartDate(e.target.value)} data-testid="revision-start" style={{ width: '100%' }} />
            </Field>
            <Field label="End date" htmlFor="revision-end">
              <input id="revision-end" type="date" className="input" value={endDate}
                onChange={(e) => setEndDate(e.target.value)} data-testid="revision-end" style={{ width: '100%' }} />
            </Field>
            <Field label="Initial cash" htmlFor="revision-cash">
              <input id="revision-cash" type="number" className="input" value={initialCash}
                onChange={(e) => setInitialCash(e.target.value)} data-testid="revision-cash" style={{ width: '100%' }} />
            </Field>
            <Field label="Commission rate" htmlFor="revision-commission">
              <input id="revision-commission" type="number" step="0.0001" className="input" value={commissionRate}
                onChange={(e) => setCommissionRate(e.target.value)} data-testid="revision-commission" style={{ width: '100%' }} />
            </Field>
            <Field label="Slippage rate" htmlFor="revision-slippage">
              <input id="revision-slippage" type="number" step="0.0001" className="input" value={slippageRate}
                onChange={(e) => setSlippageRate(e.target.value)} data-testid="revision-slippage" style={{ width: '100%' }} />
            </Field>

            {templateType === 'sma_crossover' && (
              <>
                <Field label="Short moving-average window" htmlFor="revision-short-window">
                  <input id="revision-short-window" type="number" className="input" value={shortWindow}
                    onChange={(e) => setShortWindow(e.target.value)} data-testid="revision-short-window" style={{ width: '100%' }} />
                </Field>
                <Field label="Long moving-average window" htmlFor="revision-long-window">
                  <input id="revision-long-window" type="number" className="input" value={longWindow}
                    onChange={(e) => setLongWindow(e.target.value)} data-testid="revision-long-window" style={{ width: '100%' }} />
                </Field>
              </>
            )}

            {templateType === 'price_change_fixed_hold' && (
              <>
                <Field label="Lookback (days)" htmlFor="revision-lookback">
                  <input id="revision-lookback" type="number" className="input" value={lookbackDays}
                    onChange={(e) => setLookbackDays(e.target.value)} data-testid="revision-lookback" style={{ width: '100%' }} />
                </Field>
                <Field label="Drop threshold (fraction)" htmlFor="revision-threshold">
                  <input id="revision-threshold" type="number" step="0.001" className="input" value={thresholdPct}
                    onChange={(e) => setThresholdPct(e.target.value)} data-testid="revision-threshold" style={{ width: '100%' }} />
                </Field>
                <Field label="Holding period (days)" htmlFor="revision-holding">
                  <input id="revision-holding" type="number" className="input" value={holdingDays}
                    onChange={(e) => setHoldingDays(e.target.value)} data-testid="revision-holding" style={{ width: '100%' }} />
                </Field>
              </>
            )}

            <Field label="Stop price (optional)" htmlFor="revision-stop-price">
              <input id="revision-stop-price" type="number" step="0.01" className="input" value={stopPrice}
                onChange={(e) => setStopPrice(e.target.value)} data-testid="revision-stop-price" style={{ width: '100%' }} />
            </Field>
            <Field label="Stop fraction (optional)" htmlFor="revision-stop-pct">
              <input id="revision-stop-pct" type="number" step="0.001" className="input" value={stopPct}
                onChange={(e) => setStopPct(e.target.value)} data-testid="revision-stop-pct" style={{ width: '100%' }} />
            </Field>
            <Field label="Target price (optional)" htmlFor="revision-target-price">
              <input id="revision-target-price" type="number" step="0.01" className="input" value={targetPrice}
                onChange={(e) => setTargetPrice(e.target.value)} data-testid="revision-target-price" style={{ width: '100%' }} />
            </Field>
            <Field label="Target fraction (optional)" htmlFor="revision-target-pct">
              <input id="revision-target-pct" type="number" step="0.001" className="input" value={targetPct}
                onChange={(e) => setTargetPct(e.target.value)} data-testid="revision-target-pct" style={{ width: '100%' }} />
            </Field>
          </div>

          <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Frequency is fixed at daily bars (1d). Intraday strategies cannot be created from this
            form, and the backend rejects non-1d settings rather than downgrading them.
          </div>

          <div style={{ marginTop: '16px' }}>
            <label htmlFor="revision-change-reason" style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
              Reason for change (required)
            </label>
            <textarea
              id="revision-change-reason"
              className="input"
              rows={2}
              value={changeReason}
              onChange={(e) => setChangeReason(e.target.value)}
              data-testid="revision-change-reason"
              placeholder="Why change this setting?"
              style={{ width: '100%', resize: 'vertical' }}
            />
          </div>

          <div style={{ marginTop: '12px' }}>
            <label htmlFor="revision-expectation" style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
              Expected outcome (optional)
            </label>
            <textarea
              id="revision-expectation"
              className="input"
              rows={2}
              value={expectation}
              onChange={(e) => setExpectation(e.target.value)}
              data-testid="revision-expectation"
              placeholder="What do you expect to see after the change?"
              style={{ width: '100%', resize: 'vertical' }}
            />
          </div>

          <div
            style={{ marginTop: '16px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', fontSize: '13px' }}
            data-testid="revision-preview"
          >
            <strong>
              {preview.fields.length === 0
                ? 'Nothing changed yet'
                : `${CHANGE_KIND_LABEL[preview.kind]} · ${preview.fields.length} field${preview.fields.length === 1 ? '' : 's'}`}
            </strong>
            {preview.fields.length > 0 && (
              <div style={{ marginTop: '6px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
                {preview.fields.map((field) => (
                  <div key={field}>
                    {field}: {JSON.stringify(baseFlat[field])} → {JSON.stringify(draftFlat[field])}
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: '6px', color: 'var(--text-muted)', fontSize: '12px' }}>
              Differences in data period, provider and costs appear on the run and comparison pages.
              A period-only change is labelled &ldquo;Research period only&rdquo;.
            </div>
          </div>

          {formError && (
            <p role="alert" data-testid="revision-form-error" style={{ color: 'var(--negative)', fontSize: '13px', marginTop: '12px' }}>
              {formError}
            </p>
          )}

          <div style={{ display: 'flex', gap: '8px', marginTop: '16px' }}>
            <button
              type="button"
              className="btn btn-primary"
              data-testid="revision-submit"
              onClick={submitRevision}
              disabled={submitting}
            >
              {submitting ? 'Creating…' : 'Create revision'}
            </button>
            <button
              type="button"
              className="btn"
              style={{ backgroundColor: 'var(--surface-2)' }}
              onClick={() => { setFormOpen(false); setFormError(''); }}
              disabled={submitting}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {actionNotice && (
        <p role="status" data-testid="revision-notice" style={{ color: 'var(--positive)', fontSize: '13px', margin: '0 0 12px' }}>
          {actionNotice}
        </p>
      )}
      {actionError && (
        <p role="alert" data-testid="revision-action-error" style={{ color: 'var(--negative)', fontSize: '13px', margin: '0 0 12px' }}>
          {actionError}
        </p>
      )}

      {revisionVersions.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
          No revisions yet. Once you create one, you can run a backtest, compare it with the
          source version, and record whether the outcome matched your expectation.
        </p>
      ) : (
        <div style={{ display: 'grid', gap: '12px' }}>
          {revisionVersions.map((version) => {
            const meta = version.assumptions?.revision as RevisionMeta | undefined;
            if (!meta) return null;
            const source = versions.find((v) => v.id === meta.of_version_id) ?? null;
            const revisionRun = latestCompletedRun(runs, version.id);
            const sourceRun = source ? latestCompletedRun(runs, source.id) : null;
            const compareHref =
              revisionRun && sourceRun
                ? `/experiments/${experimentId}/runs/compare?run_a=${sourceRun.id}&run_b=${revisionRun.id}`
                : null;
            const draft = outcomeDraft[version.id] ?? {
              met: meta.met_expectation === null || meta.met_expectation === undefined ? '' : String(meta.met_expectation),
              text: meta.outcome ?? '',
            };

            return (
              <div
                key={version.id}
                data-testid={`revision-row-${version.id}`}
                style={{ padding: '14px', border: '1px solid var(--border-strong)', borderRadius: '6px', backgroundColor: 'var(--surface-1)' }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', gap: '12px' }}>
                  <div>
                    <strong>v{version.version_number}</strong>
                    <span style={{ color: 'var(--text-secondary)', marginLeft: '8px', fontSize: '13px' }}>
                      Revised from v{meta.of_version_number}
                      {version.id === currentVersionId ? ' · current version' : ''}
                    </span>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                      {CHANGE_KIND_LABEL[meta.change_kind] ?? meta.change_kind}:
                      {meta.changed_fields?.length ? meta.changed_fields.join(', ') : 'Not recorded'}
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                      Reason for change: {version.change_reason ?? 'Not provided'}
                    </div>
                    {meta.expectation && (
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                        Expected: {meta.expectation}
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    <button
                      type="button"
                      className="btn"
                      style={{ backgroundColor: 'var(--surface-2)', fontSize: '12px' }}
                      data-testid={`revision-run-${version.id}`}
                      onClick={() => runVersion({ id: version.id, version_number: version.version_number })}
                      disabled={runningVersionId !== null}
                    >
                      {runningVersionId === version.id ? 'Running…' : 'Run backtest'}
                    </button>
                    {compareHref ? (
                      <Link
                        href={compareHref}
                        className="btn btn-primary"
                        style={{ fontSize: '12px', textDecoration: 'none' }}
                        data-testid={`revision-compare-${version.id}`}
                      >
                        Compare with source version
                      </Link>
                    ) : (
                      <span style={{ fontSize: '12px', color: 'var(--text-muted)', alignSelf: 'center' }}>
                        Both sides need a completed backtest before you can compare
                      </span>
                    )}
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '12px', color: 'var(--text-muted)', marginTop: '10px' }}>
                  <span>
                    Revision backtest: {revisionRun ? 'Completed' : 'Not completed'}
                    {revisionRun?.metrics?.total_return_pct != null &&
                      ` · Total return ${Number(revisionRun.metrics.total_return_pct).toFixed(2)}%`}
                  </span>
                  <span>
                    Source backtest: {sourceRun ? 'Completed' : 'Not completed'}
                    {sourceRun?.metrics?.total_return_pct != null &&
                      ` · Total return ${Number(sourceRun.metrics.total_return_pct).toFixed(2)}%`}
                  </span>
                </div>

                <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-strong)' }}>
                  {meta.outcome ? (
                    <div data-testid={`revision-outcome-value-${version.id}`} style={{ fontSize: '13px' }}>
                      <strong style={{ color: meta.met_expectation ? 'var(--positive)' : 'var(--warning)' }}>
                        {meta.met_expectation ? 'Matched expectation' : 'Did not match expectation'}
                      </strong>
                      <span style={{ marginLeft: '8px', color: 'var(--text-muted)' }}>{meta.outcome}</span>
                      {meta.outcome_recorded_at && (
                        <span style={{ marginLeft: '8px', color: 'var(--text-muted)', fontSize: '11px' }}>
                          · {formatDateTime(meta.outcome_recorded_at)}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>No outcome recorded yet</div>
                  )}

                  <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                    <select
                      className="input"
                      style={{ width: '170px', fontSize: '12px' }}
                      aria-label={`Does the outcome of v${version.version_number} match your expectation?`}
                      data-testid={`revision-outcome-met-${version.id}`}
                      value={draft.met}
                      onChange={(e) =>
                        setOutcomeDraft((prev) => ({
                          ...prev,
                          [version.id]: { ...draft, met: e.target.value },
                        }))
                      }
                    >
                      <option value="">Does it match your expectation?</option>
                      <option value="true">Matched expectation</option>
                      <option value="false">Did not match expectation</option>
                    </select>
                    <input
                      className="input"
                      style={{ flex: 1, minWidth: '220px', fontSize: '12px' }}
                      aria-label={`Conclusion for v${version.version_number}`}
                      placeholder="Record the conclusion (e.g. lower costs did improve net return)"
                      data-testid={`revision-outcome-text-${version.id}`}
                      value={draft.text}
                      onChange={(e) =>
                        setOutcomeDraft((prev) => ({
                          ...prev,
                          [version.id]: { ...draft, text: e.target.value },
                        }))
                      }
                    />
                    <button
                      type="button"
                      className="btn"
                      style={{ backgroundColor: 'var(--surface-2)', fontSize: '12px' }}
                      data-testid={`revision-outcome-save-${version.id}`}
                      onClick={() => saveOutcome(version, meta)}
                      disabled={savingOutcomeId !== null}
                    >
                      {savingOutcomeId === version.id ? 'Saving…' : meta.outcome ? 'Update result' : 'Record result'}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>{label}</label>
      {children}
    </div>
  );
}

interface VersionRowForRun {
  id: string;
  version_number: number;
}
