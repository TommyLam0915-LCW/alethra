'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter, useParams, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { formatRatePercent } from '@/lib/format';
import { RUN_STATUS_LABELS, label } from '@/lib/copy';

interface EvidenceDetail {
  source_category?: string;
  record_id?: string;
  field_path?: string;
  derivation?: string;
}

interface ComparisonField {
  field: string;
  value_a: any;
  value_b: any;
  comparison_status: 'match' | 'differ' | 'insufficient';
  provenance_a: 'captured' | 'current' | 'absent' | 'unavailable' | 'derived';
  provenance_b: 'captured' | 'current' | 'absent' | 'unavailable' | 'derived';
  evidence_source?: string;
  evidence_detail?: { run_a?: EvidenceDetail; run_b?: EvidenceDetail };
}

interface PeriodOverlap {
  run_a_period: { start: string; end: string };
  run_b_period: { start: string; end: string };
  overlap: { start: string; end: string };
}

interface ComparisonData {
  run_a_id: string;
  run_b_id: string;
  experiment_id: string;
  experiment_version_a_id: string;
  experiment_version_b_id: string;
  status_a: string;
  status_b: string;
  metrics_comparison: ComparisonField[];
  conditions_comparison: ComparisonField[];
  limitations: string[];
  period_overlap?: PeriodOverlap;
}

const PROVENANCE_LABELS: Record<string, string> = {
  captured: 'Captured',
  current: 'Current metadata',
  absent: 'Absent',
  unavailable: 'Unavailable',
  derived: 'Derived',
};

const PROVENANCE_COLORS: Record<string, string> = {
  captured: 'rgba(34,197,94,0.14)',
  current: 'rgba(245,158,11,0.14)',
  absent: 'var(--surface-2)',
  unavailable: 'rgba(239,68,68,0.12)',
  derived: 'rgba(59,130,246,0.16)',
};

type FieldKind = 'rate' | 'percent' | 'currency' | 'count' | 'ratio' | 'text' | 'list';

// Human labels carry the unit, because the backend compares raw field names.
// `commission_rate` is a fraction of trade value: 0.001 is 0.1%, not 0.001%.
const FIELD_META: Record<string, { label: string; kind: FieldKind }> = {
  total_return_pct: { label: 'Total return', kind: 'percent' },
  final_equity: { label: 'Final equity', kind: 'currency' },
  max_drawdown_pct: { label: 'Max drawdown', kind: 'percent' },
  total_trades: { label: 'Number of trades', kind: 'count' },
  sharpe_ratio: { label: 'Sharpe ratio', kind: 'ratio' },
  template_type: { label: 'Strategy template', kind: 'text' },
  asset_ids: { label: 'Instruments', kind: 'list' },
  start_date: { label: 'Start date', kind: 'text' },
  end_date: { label: 'End date', kind: 'text' },
  initial_cash: { label: 'Starting cash', kind: 'currency' },
  commission_rate: { label: 'Commission rate (share of trade value, per fill)', kind: 'rate' },
  slippage_rate: { label: 'Slippage rate (share of price, per fill)', kind: 'rate' },
  benchmark_id: { label: 'Benchmark', kind: 'text' },
  engine_version: { label: 'Engine version', kind: 'text' },
};

const COST_FIELDS = ['commission_rate', 'slippage_rate', 'initial_cash'] as const;

function describeValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return 'not recorded';
  const kind = FIELD_META[field]?.kind;
  if (typeof value !== 'number') {
    return Array.isArray(value) ? value.join(', ') : String(value);
  }
  switch (kind) {
    case 'rate':
      return `${formatRatePercent(value)} (${value})`;
    case 'percent':
      return `${value.toFixed(2)}%`;
    case 'currency':
      return `$${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
    case 'count':
      return value.toLocaleString('en-US');
    case 'ratio':
      return value.toFixed(3);
    default:
      return String(value);
  }
}

export default function RunComparisonPage() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const experimentId = params.id as string;
  const runA = searchParams.get('run_a');
  const runB = searchParams.get('run_b');
  const savedComparisonIdParam = searchParams.get('saved_comparison_id');

  const [comparison, setComparison] = useState<ComparisonData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set());
  // Short IDs alone do not tell a reader which version produced a run, so the
  // page resolves the experiment title and version numbers with the same
  // endpoints the experiment page already uses. Failure is silent: the page
  // then falls back to the short IDs it already had.
  const [experimentTitle, setExperimentTitle] = useState<string | null>(null);
  const [versionNumbers, setVersionNumbers] = useState<Record<string, number>>({});
  
  // Save comparison state
  const [purpose, setPurpose] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [savedComparisonId, setSavedComparisonId] = useState<string | null>(null);
  // Guards against a slow response for a previously-opened record overwriting
  // the currently-selected record (stale write / data leak across switches).
  const activeSavedIdRef = useRef<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }

    // If loading a saved comparison, fetch it first
    if (savedComparisonIdParam) {
      activeSavedIdRef.current = savedComparisonIdParam;
      loadSavedComparison(savedComparisonIdParam);
      return;
    }

    // Fresh comparison (not a saved record): ensure we never PATCH a stale id.
    activeSavedIdRef.current = null;
    setSavedComparisonId(null);
    setPurpose('');
    setNotes('');
    setSaveSuccess(false);

    if (!runA || !runB) {
      setError('The run_a or run_b parameter is missing');
      setErrorStatus(400);
      setLoading(false);
      return;
    }
    if (runA === runB) {
      setError('A run cannot be compared with itself');
      setErrorStatus(400);
      setLoading(false);
      return;
    }
    loadComparison();
  }, [experimentId, runA, runB, savedComparisonIdParam, router]);

  useEffect(() => {
    if (typeof window === 'undefined' || !localStorage.getItem('token')) return;
    let cancelled = false;
    Promise.all([
      api.experiments.get(experimentId).catch(() => null),
      api.experiments.versions(experimentId).catch(() => [] as Array<{ id: string; version_number: number }>),
    ]).then(([experiment, versions]) => {
      if (cancelled) return;
      if (experiment?.title) setExperimentTitle(experiment.title);
      const map: Record<string, number> = {};
      for (const version of versions ?? []) map[version.id] = version.version_number;
      setVersionNumbers(map);
    });
    return () => {
      cancelled = true;
    };
  }, [experimentId]);

  const loadSavedComparison = async (comparisonId: string) => {
    setLoading(true);
    setError('');
    setErrorStatus(null);
    try {
      const saved = await api.savedComparisons.get(comparisonId);
      // Discard if the user switched records while this request was in flight.
      if (activeSavedIdRef.current !== comparisonId) return;
      setSavedComparisonId(saved.id);
      setPurpose(saved.purpose);
      setNotes(saved.notes || '');
      // Now load the actual comparison data using the saved run IDs
      const data = await api.runs.compare(saved.run_a_id, saved.run_b_id);
      if (activeSavedIdRef.current !== comparisonId) return;
      setComparison(data);
    } catch (err: any) {
      if (activeSavedIdRef.current !== comparisonId) return;
      if (err instanceof ApiError) {
        setErrorStatus(err.status);
        setError(err.status === 404 ? 'Saved comparison not found, or you do not have access' : err.message);
      } else {
        setError('The saved comparison could not be loaded');
      }
    } finally {
      if (activeSavedIdRef.current === comparisonId) setLoading(false);
    }
  };

  const loadComparison = async () => {
    setLoading(true);
    setError('');
    setErrorStatus(null);
    try {
      const data = await api.runs.compare(runA!, runB!);
      setComparison(data);
    } catch (err: any) {
      if (err instanceof ApiError) {
        setErrorStatus(err.status);
        if (err.status === 404) {
          setError('Run not found, or you do not have access');
        } else if (err.status === 400) {
          setError(err.message);
        } else {
          setError(err.message);
        }
      } else {
        setError('The comparison could not be loaded');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!purpose.trim()) {
      setSaveError('Enter a purpose for the comparison');
      return;
    }
    // Creating requires the two runs from the URL; updating a reopened record
    // does not (its run references are already fixed server-side).
    if (!savedComparisonId && (!runA || !runB)) {
      setSaveError('run_a or run_b is missing — a new comparison cannot be created');
      return;
    }

    setSaving(true);
    setSaveError('');
    setSaveSuccess(false);

    try {
      // Empty notes field → send null so the stored note is cleared (omitting
      // it would keep the previous value server-side).
      const notesValue = notes.trim() ? notes.trim() : null;
      if (savedComparisonId) {
        // Update the SAME record (PATCH), never a duplicate create.
        await api.savedComparisons.update(savedComparisonId, {
          purpose: purpose.trim(),
          notes: notesValue,
        });
        setSaveSuccess(true);
      } else {
        // Create new saved comparison and retain the returned identity so a
        // second save PATCHes this record instead of POSTing a duplicate.
        const saved = await api.savedComparisons.create({
          experiment_id: experimentId,
          run_a_id: runA!,
          run_b_id: runB!,
          purpose: purpose.trim(),
          notes: notesValue,
        });
        setSavedComparisonId(saved.id);
        setSaveSuccess(true);
      }
    } catch (err: any) {
      // Leave purpose/notes state untouched so the user's text survives a failed save.
      setSaveError(err.message || 'The comparison could not be saved');
    } finally {
      setSaving(false);
    }
  };

  const toggleFieldExpand = (fieldKey: string) => {
    setExpandedFields(prev => {
      const next = new Set(prev);
      if (next.has(fieldKey)) {
        next.delete(fieldKey);
      } else {
        next.add(fieldKey);
      }
      return next;
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'match': return 'rgba(34,197,94,0.14)';
      case 'differ': return 'rgba(245,158,11,0.14)';
      case 'insufficient': return 'var(--surface-2)';
      default: return 'var(--surface-2)';
    }
  };

  const getProvenanceBadge = (provenance: string) => {
    const color = PROVENANCE_COLORS[provenance] || 'var(--surface-2)';
    return (
      <span style={{
        display: 'inline-block',
        padding: '2px 6px',
        backgroundColor: color,
        borderRadius: '3px',
        fontSize: '11px',
        marginLeft: '4px',
      }}>
        {PROVENANCE_LABELS[provenance] || provenance}
      </span>
    );
  };

  const formatValue = (field: string, value: any) => describeValue(field, value);

  const formatDelta = (field: ComparisonField) => {
    if (field.comparison_status !== 'differ') return null;
    if (typeof field.value_a !== 'number' || typeof field.value_b !== 'number') {
      return <span style={{ color: 'var(--negative)' }}>Values differ</span>;
    }
    const delta = field.value_b - field.value_a;
    const kind = FIELD_META[field.field]?.kind;
    const trendColor = delta < 0 ? 'var(--negative)' : 'var(--positive)';
    if (kind === 'rate') {
      // Fractions are compared in percentage points, and a real sub-0.01%
      // difference keeps three decimals instead of collapsing to "0.00".
      const percentDelta = delta * 100;
      const decimals = percentDelta !== 0 && Math.abs(percentDelta) < 0.1 ? 3 : 2;
      return (
        <span style={{ color: trendColor, fontWeight: 600 }}>
          {delta > 0 ? '+' : ''}{percentDelta.toFixed(decimals)} pp
        </span>
      );
    }
    if (kind === 'percent') {
      return (
        <span style={{ color: trendColor, fontWeight: 600 }}>
          {delta > 0 ? '+' : ''}{delta.toFixed(2)} pp
        </span>
      );
    }
    if (kind === 'currency') {
      return (
        <span style={{ color: trendColor, fontWeight: 600 }}>
          {delta > 0 ? '+' : delta < 0 ? '-' : ''}${Math.abs(delta).toLocaleString('en-US', { maximumFractionDigits: 2 })}
        </span>
      );
    }
    if (kind === 'count') {
      return (
        <span style={{ color: trendColor, fontWeight: 600 }}>
          {delta > 0 ? '+' : ''}{delta.toLocaleString('en-US')}
        </span>
      );
    }
    return (
      <span style={{ color: trendColor, fontWeight: 600 }}>
        {delta > 0 ? '+' : ''}{delta.toFixed(3)}
      </span>
    );
  };

  // The backend only reports period and dataset limitations. Settings that were
  // captured on one side but read from current metadata on the other quietly
  // weaken the comparison, so the page states that limit itself. Nothing here
  // writes, rewrites or "approves" either run.
  const derivedLimitations = (() => {
    if (!comparison) return [] as string[];
    const notes: string[] = [];
    const conditions = new Map(comparison.conditions_comparison.map((field) => [field.field, field]));

    for (const name of COST_FIELDS) {
      const field = conditions.get(name);
      if (!field) continue;
      const labelText = FIELD_META[name]?.label ?? name;
      if (field.comparison_status === 'differ') {
        notes.push(
          `${labelText} differs — A ${describeValue(name, field.value_a)}, B ${describeValue(name, field.value_b)}. The two outcomes were produced under different settings, so the metric gap mixes this setting with every other change; it does not isolate the effect of any one setting.`,
        );
      }
      const sourcedFromMetadata =
        field.provenance_a === 'current' ||
        field.provenance_b === 'current' ||
        field.provenance_a === 'absent' ||
        field.provenance_b === 'absent';
      if (sourcedFromMetadata) {
        notes.push(
          `${labelText}: at least one side is not run-captured (source status "${PROVENANCE_LABELS[field.provenance_a] ?? field.provenance_a}" / "${PROVENANCE_LABELS[field.provenance_b] ?? field.provenance_b}"). The setting shown may not be the setting the run actually used, so treat any difference or match as unverified.`,
        );
      }
    }

    const provenanceMismatch = comparison.conditions_comparison.some(
      (field) => field.provenance_a !== field.provenance_b,
    );
    if (provenanceMismatch) {
      notes.push(
        'Some fields come from different sources on each side (captured at run time versus current version metadata). A "Values match" only means the displayed values agree, not that both were recorded when the runs executed.',
      );
    }

    return notes;
  })();

  const renderEvidenceDetail = (evidence: EvidenceDetail | undefined, label: string) => {
    if (!evidence) return <span style={{ color: 'var(--text-muted)' }}>—</span>;
    return (
      <div style={{ marginBottom: '4px' }}>
        <strong>{label}:</strong>
        {evidence.source_category && <span> {evidence.source_category}</span>}
        {evidence.field_path && <span> · <code style={{ fontSize: '11px' }}>{evidence.field_path}</code></span>}
        {evidence.derivation && <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginTop: '2px' }}>{evidence.derivation}</div>}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="container" role="status" aria-live="polite" style={{ paddingTop: '40px' }}>
        Loading comparison…
      </div>
    );
  }

  if (error) {
    const isRetryable = errorStatus !== 400;
    return (
      <div className="container" style={{ paddingTop: '40px' }}>
        <div role="alert" style={{
          background: errorStatus === 404 ? 'rgba(245,158,11,0.14)' : 'rgba(239,68,68,0.12)',
          borderLeft: `4px solid ${errorStatus === 404 ? 'var(--warning)' : 'var(--negative)'}`,
          padding: '16px',
          marginBottom: '16px',
          borderRadius: '4px',
        }}>
          <strong>{errorStatus === 404 ? 'Not found' : errorStatus === 400 ? 'Invalid parameters' : 'Could not load'}</strong>
          <p style={{ margin: '8px 0 0 0' }}>{error}</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          {isRetryable && (
            <button onClick={loadComparison} className="btn" style={{ backgroundColor: 'var(--primary-solid)', color: 'var(--text-white)' }}>
              Retry
            </button>
          )}
          <Link href={`/experiments/${experimentId}`} className="btn" style={{ backgroundColor: 'var(--surface-2)', textDecoration: 'none' }}>
            Back to experiment
          </Link>
        </div>
      </div>
    );
  }

  if (!comparison) {
    return (
      <div className="container" style={{ paddingTop: '40px' }}>
        <p>No comparison data</p>
        <Link href={`/experiments/${experimentId}`} style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          ← Back to experiment
        </Link>
      </div>
    );
  }

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href={`/experiments/${experimentId}`} style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          ← Back to experiment
        </Link>
      </div>

      <h1 style={{ marginBottom: '8px' }}>Backtest result comparison</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '24px' }} data-testid="comparison-experiment">
        {experimentTitle ?? `Experiment ${experimentId.slice(0, 8)}…`}
      </p>

      {/* Task Prompt */}
      <div style={{
        background: 'rgba(59,130,246,0.16)',
        border: '2px solid var(--primary-text)',
        padding: '15px',
        marginBottom: '24px',
        borderRadius: '4px',
      }}>
        <strong>Your task:</strong> decide which setting to investigate next. Use this page to inspect both results, state what you can conclude so far, and say what you will do next.
      </div>

      {/* Save Comparison Form */}
      <div style={{
        background: 'var(--surface-2)',
        border: '1px solid var(--border-strong)',
        padding: '16px',
        marginBottom: '24px',
        borderRadius: '4px',
      }}>
        <h3 style={{ marginTop: 0, marginBottom: '16px' }}>Save comparison</h3>
        
        <div style={{ marginBottom: '12px' }}>
          <label htmlFor="comparison-purpose" style={{ display: 'block', marginBottom: '4px', fontWeight: 500 }}>
            Comparison purpose <span style={{ color: 'var(--negative)' }}>*</span>
          </label>
          <input
            id="comparison-purpose"
            type="text"
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            placeholder="e.g. Check how commission rate affects returns"
            maxLength={500}
            style={{
              width: '100%',
              padding: '8px',
              border: '1px solid var(--border-strong)',
              borderRadius: '4px',
              fontSize: '14px',
            }}
            disabled={saving}
          />
        </div>

        <div style={{ marginBottom: '12px' }}>
          <label htmlFor="comparison-notes" style={{ display: 'block', marginBottom: '4px', fontWeight: 500 }}>
            Notes
          </label>
          <textarea
            id="comparison-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Record your observations and conclusions…"
            maxLength={5000}
            rows={4}
            style={{
              width: '100%',
              padding: '8px',
              border: '1px solid var(--border-strong)',
              borderRadius: '4px',
              fontSize: '14px',
              resize: 'vertical',
            }}
            disabled={saving}
          />
        </div>

        {saveError && (
          <div role="alert" style={{
            background: 'rgba(239,68,68,0.12)',
            border: '1px solid rgba(239,68,68,0.34)',
            color: 'var(--negative)',
            padding: '8px',
            marginBottom: '12px',
            borderRadius: '4px',
          }}>
            {saveError}
          </div>
        )}

        {saveSuccess && (
          <div role="status" style={{
            background: 'rgba(34,197,94,0.14)',
            border: '1px solid rgba(34,197,94,0.34)',
            color: 'var(--positive)',
            padding: '8px',
            marginBottom: '12px',
            borderRadius: '4px',
          }}>
            Comparison saved
          </div>
        )}

        <button
          onClick={handleSave}
          disabled={saving || !purpose.trim()}
          style={{
            backgroundColor: saving ? 'var(--text-secondary)' : 'var(--primary-text)',
            color: 'var(--text-white)',
            padding: '8px 16px',
            border: 'none',
            borderRadius: '4px',
            cursor: saving ? 'not-allowed' : 'pointer',
            fontSize: '14px',
            fontWeight: 500,
          }}
        >
          {saving ? 'Saving…' : savedComparisonId ? 'Update' : 'Save'}
        </button>
      </div>

      {/* Run Identities */}
      <div style={{
        background: 'rgba(59,130,246,0.16)',
        borderLeft: '4px solid var(--primary)',
        padding: '12px',
        marginBottom: '24px',
      }}>
        <strong>Selected results:</strong>
        <ul style={{ margin: '8px 0 0 20px', listStyle: 'none', padding: 0 }}>
          <li style={{ marginBottom: '4px' }} data-testid="comparison-run-a">
            Run A
            {versionNumbers[comparison.experiment_version_a_id] != null && (
              <> · version v{versionNumbers[comparison.experiment_version_a_id]}</>
            )}
            {' — '}status: {label(RUN_STATUS_LABELS, comparison.status_a)} ·{' '}
            <Link href={`/experiments/${experimentId}/runs/${comparison.run_a_id}`} style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
              open result
            </Link>
            <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}> ({comparison.run_a_id.slice(0, 8)}…)</span>
          </li>
          <li data-testid="comparison-run-b">
            Run B
            {versionNumbers[comparison.experiment_version_b_id] != null && (
              <> · version v{versionNumbers[comparison.experiment_version_b_id]}</>
            )}
            {' — '}status: {label(RUN_STATUS_LABELS, comparison.status_b)} ·{' '}
            <Link href={`/experiments/${experimentId}/runs/${comparison.run_b_id}`} style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
              open result
            </Link>
            <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}> ({comparison.run_b_id.slice(0, 8)}…)</span>
          </li>
        </ul>
        <details style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-secondary)' }}>
          <summary style={{ cursor: 'pointer' }}>How to read the source status</summary>
          <ul style={{ margin: '8px 0 0 20px', lineHeight: 1.7 }}>
            <li><strong>Captured</strong> — recorded in the run itself when it executed.</li>
            <li><strong>Current metadata</strong> — read from the strategy version&apos;s settings today; it may have changed since the run.</li>
            <li><strong>Derived</strong> — computed from the run&apos;s recorded trades and equity.</li>
            <li><strong>Absent / Unavailable</strong> — nothing was recorded for that side.</li>
          </ul>
        </details>
      </div>

      {/* Limitations */}
      {comparison.limitations.length > 0 && (
        <div style={{
          background: 'rgba(245,158,11,0.14)',
          borderLeft: '4px solid var(--warning)',
          padding: '12px',
          marginBottom: '24px',
        }}>
          <strong>Comparison limitations:</strong>
          <ul style={{ margin: '8px 0 0 20px' }}>
            {comparison.limitations.map((lim, i) => (
              <li key={i}>{lim}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Setting differences this page can see but the server does not report */}
      {derivedLimitations.length > 0 && (
        <div
          data-testid="comparison-setting-limitations"
          style={{
            background: 'var(--surface-2)',
            borderLeft: '4px solid var(--primary)',
            padding: '12px',
            marginBottom: '24px',
          }}
        >
          <strong>What this comparison cannot tell you:</strong>
          <ul style={{ margin: '8px 0 0 20px' }}>
            {derivedLimitations.map((lim, i) => (
              <li key={i} style={{ marginBottom: '6px' }}>{lim}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Period Overlap */}
      {comparison.period_overlap && (
        <div style={{
          background: 'var(--surface-2)',
          border: '1px solid var(--border-strong)',
          padding: '12px',
          marginBottom: '24px',
          borderRadius: '4px',
        }}>
          <strong>Period overlap:</strong>
          <div style={{ marginTop: '8px', fontSize: '13px' }}>
            <div>Run A: {comparison.period_overlap.run_a_period.start} to {comparison.period_overlap.run_a_period.end}</div>
            <div>Run B: {comparison.period_overlap.run_b_period.start} to {comparison.period_overlap.run_b_period.end}</div>
            <div style={{ marginTop: '4px', color: 'var(--primary-text)' }}>
              Overlapping period: {comparison.period_overlap.overlap.start} to {comparison.period_overlap.overlap.end}
            </div>
          </div>
        </div>
      )}

      {/* Metrics Comparison */}
      <h2 style={{ marginBottom: '16px' }}>Metric differences</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '24px' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid var(--border-strong)' }}>
            <th style={{ padding: '10px', textAlign: 'left', width: '30px' }}></th>
            <th style={{ padding: '10px', textAlign: 'left' }}>Metric</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run A</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run B</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Difference (B − A)</th>
            <th style={{ padding: '10px', textAlign: 'left' }}>Source status</th>
          </tr>
        </thead>
        <tbody>
          {comparison.metrics_comparison.map((field, i) => {
            const fieldKey = `metrics-${field.field}`;
            const isExpanded = expandedFields.has(fieldKey);
            const hasEvidence = field.evidence_detail?.run_a || field.evidence_detail?.run_b;
            return (
              <tr key={i}>
                <td colSpan={6} style={{ padding: 0, borderBottom: isExpanded ? 'none' : '1px solid var(--border-strong)' }}>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '30px 1fr 1fr 1fr 1fr 1fr',
                      backgroundColor: getStatusColor(field.comparison_status),
                      cursor: hasEvidence ? 'pointer' : 'default',
                    }}
                    onClick={() => hasEvidence && toggleFieldExpand(fieldKey)}
                  >
                    <div style={{ padding: '10px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {hasEvidence ? (isExpanded ? '▼' : '▶') : ''}
                    </div>
                    <div style={{ padding: '10px', fontWeight: 500 }} data-testid={`field-label-${fieldKey}`}>
                      {FIELD_META[field.field]?.label ?? field.field}
                    </div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.field, field.value_a)}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.field, field.value_b)}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatDelta(field)}</div>
                    <div style={{ padding: '10px' }}>
                      {getProvenanceBadge(field.provenance_a)}
                      {field.provenance_a !== field.provenance_b && getProvenanceBadge(field.provenance_b)}
                    </div>
                  </div>
                  {isExpanded && hasEvidence && (
                    <div style={{ padding: '8px 10px 12px 40px', background: 'var(--surface-2)', fontSize: '13px', borderBottom: '1px solid var(--border-strong)' }}>
                      {renderEvidenceDetail(field.evidence_detail?.run_a, 'Run A')}
                      {renderEvidenceDetail(field.evidence_detail?.run_b, 'Run B')}
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Conditions Comparison */}
      <h2 style={{ marginBottom: '16px' }}>Execution condition differences</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '24px' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid var(--border-strong)' }}>
            <th style={{ padding: '10px', textAlign: 'left', width: '30px' }}></th>
            <th style={{ padding: '10px', textAlign: 'left' }}>Field</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run A</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run B</th>
            <th style={{ padding: '10px', textAlign: 'left' }}>Comparison status</th>
            <th style={{ padding: '10px', textAlign: 'left' }}>Source status</th>
          </tr>
        </thead>
        <tbody>
          {comparison.conditions_comparison.map((field, i) => {
            const fieldKey = `conditions-${field.field}`;
            const isExpanded = expandedFields.has(fieldKey);
            const hasEvidence = field.evidence_detail?.run_a || field.evidence_detail?.run_b;
            return (
              <tr key={i}>
                <td colSpan={6} style={{ padding: 0, borderBottom: isExpanded ? 'none' : '1px solid var(--border-strong)' }}>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '30px 1fr 1fr 1fr 1fr 1fr',
                      backgroundColor: getStatusColor(field.comparison_status),
                      cursor: hasEvidence ? 'pointer' : 'default',
                    }}
                    onClick={() => hasEvidence && toggleFieldExpand(fieldKey)}
                  >
                    <div style={{ padding: '10px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {hasEvidence ? (isExpanded ? '▼' : '▶') : ''}
                    </div>
                    <div style={{ padding: '10px', fontWeight: 500 }} data-testid={`field-label-${fieldKey}`}>
                      {FIELD_META[field.field]?.label ?? field.field}
                    </div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.field, field.value_a)}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.field, field.value_b)}</div>
                    <div style={{ padding: '10px' }}>
                      {field.comparison_status === 'match' && 'Values match'}
                      {field.comparison_status === 'differ' && 'Values differ'}
                      {field.comparison_status === 'insufficient' && 'Insufficient information'}
                    </div>
                    <div style={{ padding: '10px' }}>
                      {getProvenanceBadge(field.provenance_a)}
                      {field.provenance_a !== field.provenance_b && getProvenanceBadge(field.provenance_b)}
                    </div>
                  </div>
                  {isExpanded && hasEvidence && (
                    <div style={{ padding: '8px 10px 12px 40px', background: 'var(--surface-2)', fontSize: '13px', borderBottom: '1px solid var(--border-strong)' }}>
                      {renderEvidenceDetail(field.evidence_detail?.run_a, 'Run A')}
                      {renderEvidenceDetail(field.evidence_detail?.run_b, 'Run B')}
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Disclaimer */}
      <div style={{
        background: 'rgba(245,158,11,0.14)',
        padding: '12px',
        borderRadius: '4px',
        fontSize: '14px',
      }}>
        <strong>Note:</strong> this comparison is based on recorded data. Some fields may be missing or reflect current metadata only. Alethra is a research tool; comparison results are not investment advice.
      </div>
    </div>
  );
}
