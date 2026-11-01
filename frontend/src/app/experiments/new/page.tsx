'use client';

import { useState, useEffect, useRef, Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';
import { STRATEGY_TEMPLATE_LABELS } from '@/lib/copy';
import type { DataCoverage } from '@/components/workspace/types';

const DEMO_ASSETS = ['DEMO-ALPHA', 'DEMO-BETA', 'DEMO-BENCHMARK'];

interface InstrumentOption {
  instrument_id: string;
  symbol: string;
  name: string;
  market: string;
  asset_type: string;
}

interface ResearchDraft {
  id: string;
  title: string;
  hypothesis: string | null;
  falsification_criteria: string | null;
  strategy_template: string | null;
  asset_symbol: string | null;
  benchmark_symbol: string | null;
  initial_cash: string | null;
  commission_rate: string | null;
  slippage_rate: string | null;
  research_rules: Record<string, any> | null;
  status: string;
  experiment_id: string | null;
}

const FIELD_LABELS: Record<string, string> = {
  title: 'Title',
  hypothesis: 'Research hypothesis',
  strategy_template: 'Strategy template',
  asset_symbol: 'Asset',
  benchmark_symbol: 'Benchmark',
  initial_cash: 'Initial cash',
  commission_rate: 'Commission rate',
  slippage_rate: 'Slippage rate',
};

const DRAFT_TO_FORM: Record<string, string> = {
  title: 'title',
  hypothesis: 'hypothesis',
  strategy_template: 'templateType',
  asset_symbol: 'assetId',
  benchmark_symbol: 'benchmarkId',
  initial_cash: 'initialCash',
  commission_rate: 'commissionRate',
  slippage_rate: 'slippageRate',
};

const HORIZON_DAYS = 366;

function shiftDate(isoDay: string, days: number): string {
  const d = new Date(`${isoDay}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * The authoring default has to be anchored on bars this server actually stores.
 * The provider's declared window answers "what could be served", which is a
 * different question — defaulting a strategy over a declared-but-empty window
 * produces a backtest with no rows and a replay that cannot take one step.
 */
function windowFromCoverage(coverage: DataCoverage | null): { start: string; end: string } {
  const today = new Date().toISOString().slice(0, 10);
  const upper = coverage?.stored_end ?? coverage?.available_end ?? today;
  const end = upper > today ? today : upper;
  const earliest = shiftDate(end, -HORIZON_DAYS);
  const floor = coverage?.stored_start ?? earliest;
  const start = floor > earliest ? floor : earliest;
  return { start: start < end ? start : shiftDate(end, -1), end };
}

function touchesStoredBars(startDate: string, endDate: string, coverage: DataCoverage | null): boolean {
  if (!coverage || !coverage.stored_start || !coverage.stored_end) return true;
  return startDate <= coverage.stored_end && endDate >= coverage.stored_start;
}

function NewExperimentForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const draftId = searchParams.get('draft_id');
  const requestedFields = searchParams.get('fields')?.split(',').filter(Boolean) || [];

  const [title, setTitle] = useState('');
  const [hypothesis, setHypothesis] = useState('');
  const [templateType, setTemplateType] = useState('buy_and_hold');
  const [assetId, setAssetId] = useState('US:AAPL');
  const [benchmarkId, setBenchmarkId] = useState('');
  const [startDate, setStartDate] = useState(() => {
    const yearAgo = new Date(Date.now() - 366 * 24 * 60 * 60 * 1000);
    return yearAgo.toISOString().slice(0, 10);
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [initialCash, setInitialCash] = useState('10000');
  const [commissionRate, setCommissionRate] = useState('0');
  const [slippageRate, setSlippageRate] = useState('0');

  // Empty means "no plan declared"; the engine must not read that as zero risk.
  const [stopPrice, setStopPrice] = useState('');
  const [stopPct, setStopPct] = useState('');
  const [targetPrice, setTargetPrice] = useState('');
  const [targetPct, setTargetPct] = useState('');

  const [shortWindow, setShortWindow] = useState('5');
  const [longWindow, setLongWindow] = useState('10');

  const [lookbackDays, setLookbackDays] = useState('3');
  const [thresholdPct, setThresholdPct] = useState('0.05');
  const [holdingDays, setHoldingDays] = useState('5');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [instruments, setInstruments] = useState<InstrumentOption[]>([]);
  const [assetTab, setAssetTab] = useState<'real' | 'demo'>('real');

  const [draft, setDraft] = useState<ResearchDraft | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftError, setDraftError] = useState('');
  const [showPreview, setShowPreview] = useState(false);
  const [appliedFields, setAppliedFields] = useState<string[]>([]);

  const [coverage, setCoverage] = useState<DataCoverage | null>(null);
  const [coverageLoaded, setCoverageLoaded] = useState(false);
  // A picked window is user intent: once the owner edits either date, fetched
  // coverage must stop moving the dates under them.
  const datesEditedRef = useRef(false);

  useEffect(() => {
    api.instruments.list().then((data: any) => {
      setInstruments(data.instruments || []);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!assetId.includes(':')) {
      setCoverage(null);
      setCoverageLoaded(false);
      return;
    }
    let cancelled = false;
    api.instruments.get(assetId).then((data: any) => {
      if (cancelled) return;
      const next = (data?.data_coverage ?? null) as DataCoverage | null;
      setCoverage(next);
      setCoverageLoaded(Boolean(next));
      if (next && !datesEditedRef.current) {
        const nextWindow = windowFromCoverage(next);
        setStartDate(nextWindow.start);
        setEndDate(nextWindow.end);
      }
    }).catch(() => {
      if (cancelled) return;
      setCoverage(null);
      setCoverageLoaded(false);
    });
    return () => { cancelled = true; };
  }, [assetId]);

  useEffect(() => {
    if (!draftId || requestedFields.length === 0) return;

    setDraftLoading(true);
    setDraftError('');

    api.researchDrafts.get(draftId)
      .then((data) => {
        if (data.status !== 'accepted') {
          setDraftError('Only accepted drafts can transfer fields');
          setDraftLoading(false);
          return;
        }
        setDraft(data);
        setShowPreview(true);
        setDraftLoading(false);
      })
      .catch((err: any) => {
        setDraftError(err.message || 'The draft could not be loaded');
        setDraftLoading(false);
      });
  }, [draftId, requestedFields.length]);

  const handleApplyFields = () => {
    if (!draft) return;

    const applied: string[] = [];
    for (const field of requestedFields) {
      const value = (draft as any)[field];
      if (!value) continue;

      const formField = DRAFT_TO_FORM[field];
      if (!formField) continue;

      switch (formField) {
        case 'title': setTitle(String(value)); break;
        case 'hypothesis': setHypothesis(String(value)); break;
        case 'templateType': setTemplateType(String(value)); break;
        case 'assetId': setAssetId(String(value)); break;
        case 'benchmarkId': setBenchmarkId(String(value)); break;
        case 'initialCash': setInitialCash(String(value)); break;
        case 'commissionRate': setCommissionRate(String(value)); break;
        case 'slippageRate': setSlippageRate(String(value)); break;
      }
      applied.push(FIELD_LABELS[field] || field);
    }

    setAppliedFields(applied);
    setShowPreview(false);
  };

  const handleCancelPreview = () => {
    setShowPreview(false);
    setDraft(null);
    router.push('/research-drafts');
  };

  const buildSignalParameters = () => {
    if (templateType === 'sma_crossover') {
      return { short_window: parseInt(shortWindow), long_window: parseInt(longWindow) };
    }
    if (templateType === 'price_change_fixed_hold') {
      return {
        lookback_days: parseInt(lookbackDays),
        threshold_pct: parseFloat(thresholdPct),
        holding_days: parseInt(holdingDays),
      };
    }
    return {};
  };

  const buildRiskParameters = () => {
    const params: Record<string, number> = {};
    const entries: Array<[string, string]> = [
      ['stop_price', stopPrice],
      ['stop_pct', stopPct],
      ['target_price', targetPrice],
      ['target_pct', targetPct],
    ];
    for (const [key, raw] of entries) {
      const value = parseFloat(raw);
      if (Number.isFinite(value)) params[key] = value;
    }
    return params;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const experiment = await api.experiments.create({
        title,
        hypothesis,
        research_type: 'quantitative',
        strategy_spec: {
          schema_version: '1.0',
          template_type: templateType,
          asset_ids: [assetId],
          frequency: '1d',
          start_date: startDate,
          end_date: endDate,
          initial_cash: parseFloat(initialCash),
          signal_parameters: buildSignalParameters(),
          entry_rule: {},
          exit_rule: {},
          sizing_rule: {},
          risk_parameters: buildRiskParameters(),
          execution_timing: { execute_at: 'next_open' },
          commission_rate: parseFloat(commissionRate) || 0,
          slippage_rate: parseFloat(slippageRate) || 0,
          benchmark_id: benchmarkId,
          currency: 'USD',
          user_approved_at: new Date().toISOString(),
        },
      });

      router.push(`/experiments/${experiment.id}`);
    } catch (err: any) {
      setError(err.message || 'The experiment could not be created');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container" style={{ maxWidth: '800px', paddingTop: '40px', paddingBottom: '40px' }}>
      <h1 style={{ marginBottom: '30px' }}>{brand.ui.experiment.createTitle}</h1>

      <div className="disclaimer">
        <strong>Heads-up:</strong> this workspace backtests on sample data. Results are for research only and do not
        represent real market performance.
      </div>

      {draftLoading && (
        <div className="alert alert-info" style={{ marginBottom: '20px', fontSize: '13px' }}>
          Loading research draft…
        </div>
      )}

      {draftError && (
        <div className="alert alert-warning" style={{ marginBottom: '20px', fontSize: '13px' }}>
          {draftError}
        </div>
      )}

      {showPreview && draft && (
        <div className="card" style={{ marginBottom: '20px', backgroundColor: 'var(--surface-2)', border: '2px solid var(--primary)' }}>
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>Preview draft fields</h2>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
            These fields will be transferred from the draft into the form. Review them, then choose
            &ldquo;Apply fields&rdquo;, or cancel to go back.
          </p>
          <div style={{ display: 'grid', gap: '12px', marginBottom: '16px' }}>
            {requestedFields.map((field) => {
              const value = (draft as any)[field];
              if (!value) return null;
              return (
                <div key={field} style={{ display: 'flex', gap: '12px', padding: '8px 0', borderBottom: '1px solid var(--border-strong)' }}>
                  <div style={{ width: '120px', fontWeight: '500', fontSize: '14px', color: 'var(--text-muted)', flexShrink: 0 }}>
                    {FIELD_LABELS[field] || field}
                  </div>
                  <div style={{ fontSize: '14px', color: 'var(--text-primary)', flex: 1 }}>
                    <strong>{String(value)}</strong>
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-primary" onClick={handleApplyFields}>
              Apply {requestedFields.length} field{requestedFields.length === 1 ? '' : 's'}
            </button>
            <button className="btn btn-secondary" onClick={handleCancelPreview}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {appliedFields.length > 0 && (
        <div className="alert alert-info" style={{ marginBottom: '20px', fontSize: '13px' }}>
          <strong>Applied from the research draft:</strong> {appliedFields.join(', ')}.
          Review these fields and fill in the execution parameters such as start and end date before creating the
          experiment.
        </div>
      )}

      {error && <div className="alert alert-warning">{error}</div>}

      <div className="card">
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="exp-title">Experiment title *</label>
            <input
              id="exp-title"
              type="text"
              className="input"
              data-testid="exp-title"
              aria-label="Experiment title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="For example: Test an SMA crossover on DEMO-ALPHA"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="exp-hypothesis">Research hypothesis</label>
            <textarea
              id="exp-hypothesis"
              className="input"
              aria-label="Research hypothesis"
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              placeholder="Describe your investment hypothesis"
              rows={3}
              style={{ resize: 'vertical' }}
            />
          </div>

          <div className="form-group">
            <label htmlFor="exp-template">Strategy template *</label>
            <select
              id="exp-template"
              className="input"
              aria-label="Strategy template"
              value={templateType}
              onChange={(e) => setTemplateType(e.target.value)}
            >
              {Object.entries(STRATEGY_TEMPLATE_LABELS).map(([value, text]) => (
                <option key={value} value={value}>{text}</option>
              ))}
            </select>
            <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
              Only these templates run on the current engine.{' '}
              <Link href="/strategy-templates" style={{ color: 'var(--primary-text)' }}>
                See supported parameters, limits and preflight a spec
              </Link>
              .
            </p>
          </div>

          <div className="form-group">
            <label htmlFor="exp-asset">Asset *</label>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
              <button type="button" className={assetTab === 'real' ? 'btn btn-primary' : 'btn btn-secondary'} aria-pressed={assetTab === 'real'} style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => setAssetTab('real')}>
                Real instruments (US/HK)
              </button>
              <button type="button" className={assetTab === 'demo' ? 'btn btn-primary' : 'btn btn-secondary'} aria-pressed={assetTab === 'demo'} style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => setAssetTab('demo')}>
                Sample instruments
              </button>
            </div>
            <select
              id="exp-asset"
              className="input"
              data-testid="exp-asset"
              aria-label="Asset"
              value={assetId}
              onChange={(e) => {
                datesEditedRef.current = false;
                setAssetId(e.target.value);
              }}
            >
              {assetTab === 'real' ? (
                <>
                  <optgroup label="US Stocks">
                    {instruments.filter(i => i.market === 'US' && i.asset_type === 'STOCK').map(i => (
                      <option key={i.instrument_id} value={i.instrument_id}>{i.instrument_id} — {i.name}</option>
                    ))}
                  </optgroup>
                  <optgroup label="US ETFs">
                    {instruments.filter(i => i.market === 'US' && i.asset_type === 'ETF').map(i => (
                      <option key={i.instrument_id} value={i.instrument_id}>{i.instrument_id} — {i.name}</option>
                    ))}
                  </optgroup>
                  <optgroup label="HK Stocks">
                    {instruments.filter(i => i.market === 'HK' && i.asset_type === 'STOCK').map(i => (
                      <option key={i.instrument_id} value={i.instrument_id}>{i.instrument_id} — {i.name}</option>
                    ))}
                  </optgroup>
                  <optgroup label="HK ETFs">
                    {instruments.filter(i => i.market === 'HK' && i.asset_type === 'ETF').map(i => (
                      <option key={i.instrument_id} value={i.instrument_id}>{i.instrument_id} — {i.name}</option>
                    ))}
                  </optgroup>
                </>
              ) : (
                DEMO_ASSETS.filter(a => a !== 'DEMO-BENCHMARK').map(a => (
                  <option key={a} value={a}>{a}</option>
                ))
              )}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="exp-benchmark">Benchmark</label>
            <select
              id="exp-benchmark"
              className="input"
              aria-label="Benchmark"
              value={benchmarkId}
              onChange={(e) => setBenchmarkId(e.target.value)}
            >
              <option value="">None</option>
              {assetTab === 'real' ? (
                instruments.filter(i => i.instrument_id !== assetId).map(i => (
                  <option key={i.instrument_id} value={i.instrument_id}>{i.instrument_id} — {i.name}</option>
                ))
              ) : (
                DEMO_ASSETS.map(a => (
                  <option key={a} value={a}>{a}</option>
                ))
              )}
            </select>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label htmlFor="exp-start-date">Start date *</label>
              <input
                id="exp-start-date"
                type="date"
                className="input"
                data-testid="exp-start-date"
                aria-label="Start date"
                value={startDate}
                onChange={(e) => {
                  datesEditedRef.current = true;
                  setStartDate(e.target.value);
                }}
                required
              />
            </div>
            <div className="form-group">
              <label htmlFor="exp-end-date">End date *</label>
              <input
                id="exp-end-date"
                type="date"
                className="input"
                data-testid="exp-end-date"
                aria-label="End date"
                value={endDate}
                onChange={(e) => {
                  datesEditedRef.current = true;
                  setEndDate(e.target.value);
                }}
                required
              />
            </div>
          </div>

          <p data-testid="exp-coverage-line" style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '-8px', marginBottom: '16px' }}>
            {coverageLoaded ? (
              <>
                Data coverage for {assetId}: the server stores bars from {coverage?.stored_start ?? 'UNKNOWN'} to{' '}
                {coverage?.stored_end ?? 'UNKNOWN'} ({coverage?.stored_bars ?? 0} bars); the provider declares it can
                supply {coverage?.available_start ?? 'UNKNOWN'} to {coverage?.available_end ?? 'UNKNOWN'}. Backtests,
                replays and fills only read stored bars, so the dates default to that stored range.
              </>
            ) : (
              <>
                Data coverage for {assetId}: UNKNOWN (no coverage result was returned; sample instruments do not offer
                a coverage query). The dates below are a default window — after creating the experiment, confirm the
                backtest actually found data.
              </>
            )}
          </p>

          {coverageLoaded && (coverage?.stored_bars ?? 0) === 0 && (
            <div className="alert alert-warning" data-testid="exp-coverage-empty" style={{ fontSize: '13px', marginBottom: '16px' }}>
              {assetId} has no stored bars on the server: the backtest will return an empty result and a replay cannot
              take its first step. This page can only query coverage, not load data; bars must be written to storage by
              the data pipeline first.
            </div>
          )}

          {coverageLoaded && (coverage?.stored_bars ?? 0) > 0 && !touchesStoredBars(startDate, endDate, coverage) && (
            <div className="alert alert-warning" data-testid="exp-coverage-mismatch" style={{ fontSize: '13px', marginBottom: '16px' }}>
              The selected period ({startDate || 'UNKNOWN'} to {endDate || 'UNKNOWN'}) does not overlap the stored bars
              ({coverage?.stored_start ?? 'UNKNOWN'} to {coverage?.stored_end ?? 'UNKNOWN'}), so the backtest and replay
              will find no data.
            </div>
          )}

          <div className="form-group">
            <label htmlFor="exp-initial-cash">Initial cash (USD) *</label>
            <input
              id="exp-initial-cash"
              type="number"
              className="input"
              aria-label="Initial cash (USD)"
              value={initialCash}
              onChange={(e) => setInitialCash(e.target.value)}
              min="100"
              step="100"
              required
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label htmlFor="exp-commission">Commission rate (fraction, 0–0.05)</label>
              <input
                id="exp-commission"
                type="number"
                className="input"
                aria-label="Commission rate"
                value={commissionRate}
                onChange={(e) => setCommissionRate(e.target.value)}
                min="0"
                max="0.05"
                step="0.0001"
              />
              <p data-testid="exp-commission-hint" style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                Share of each trade&apos;s value charged per fill, not a percentage. 0.001 = 0.1%; 0.0005 = 0.05%.
              </p>
            </div>
            <div className="form-group">
              <label htmlFor="exp-slippage">Slippage rate (fraction, 0–0.05)</label>
              <input
                id="exp-slippage"
                type="number"
                className="input"
                aria-label="Slippage rate"
                value={slippageRate}
                onChange={(e) => setSlippageRate(e.target.value)}
                min="0"
                max="0.05"
                step="0.0001"
              />
              <p data-testid="exp-slippage-hint" style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                Price move against you on each fill, not a percentage of return. 0.001 = 0.1%; 0.0005 = 0.05%.
              </p>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label htmlFor="risk-stop-price">Stop price (USD, optional)</label>
              <input
                id="risk-stop-price"
                type="number"
                className="input"
                data-testid="risk-stop-price"
                aria-label="Stop price (USD)"
                value={stopPrice}
                onChange={(e) => setStopPrice(e.target.value)}
                step="0.01"
                placeholder="e.g. 190.00"
              />
            </div>
            <div className="form-group">
              <label htmlFor="risk-stop-pct">Stop distance (0–1, optional)</label>
              <input
                id="risk-stop-pct"
                type="number"
                className="input"
                data-testid="risk-stop-pct"
                aria-label="Stop distance"
                value={stopPct}
                onChange={(e) => setStopPct(e.target.value)}
                min="0"
                max="1"
                step="0.01"
                placeholder="e.g. 0.05"
              />
            </div>
            <div className="form-group">
              <label htmlFor="risk-target-price">Target price (USD, optional)</label>
              <input
                id="risk-target-price"
                type="number"
                className="input"
                data-testid="risk-target-price"
                aria-label="Target price (USD)"
                value={targetPrice}
                onChange={(e) => setTargetPrice(e.target.value)}
                step="0.01"
                placeholder="e.g. 220.00"
              />
            </div>
            <div className="form-group">
              <label htmlFor="risk-target-pct">Target distance (0–1, optional)</label>
              <input
                id="risk-target-pct"
                type="number"
                className="input"
                data-testid="risk-target-pct"
                aria-label="Target distance"
                value={targetPct}
                onChange={(e) => setTargetPct(e.target.value)}
                min="0"
                max="1"
                step="0.01"
                placeholder="e.g. 0.10"
              />
            </div>
          </div>

          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '-8px', marginBottom: '16px' }}>
            The risk plan drives post-fill attribution: planned risk and R are computed from the entry price and the
            stop price, and a position is not closed automatically when price touches the stop or target. Leaving a
            field empty declares no stop, so that trade&apos;s planned risk and realized R show as &ldquo;Not
            computable&rdquo; rather than 0. When both a price and a distance are given, the price wins; a stop at or
            above the entry price counts as no valid stop.
          </p>

          {templateType === 'sma_crossover' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div className="form-group">
                <label htmlFor="sma-short-window">Short moving average (days) *</label>
                <input
                  id="sma-short-window"
                  type="number"
                  className="input"
                  data-testid="sma-short-window"
                  aria-label="Short moving average days"
                  value={shortWindow}
                  onChange={(e) => setShortWindow(e.target.value)}
                  min="2"
                  max="60"
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="sma-long-window">Long moving average (days) *</label>
                <input
                  id="sma-long-window"
                  type="number"
                  className="input"
                  data-testid="sma-long-window"
                  aria-label="Long moving average days"
                  value={longWindow}
                  onChange={(e) => setLongWindow(e.target.value)}
                  min="3"
                  max="200"
                  required
                />
              </div>
            </div>
          )}

          {templateType === 'price_change_fixed_hold' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
              <div className="form-group">
                <label htmlFor="price-lookback">Lookback (days) *</label>
                <input
                  id="price-lookback"
                  type="number"
                  className="input"
                  aria-label="Lookback days"
                  value={lookbackDays}
                  onChange={(e) => setLookbackDays(e.target.value)}
                  min="1"
                  max="60"
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="price-threshold">Drop threshold (0.01–0.50) *</label>
                <input
                  id="price-threshold"
                  type="number"
                  className="input"
                  aria-label="Drop threshold"
                  value={thresholdPct}
                  onChange={(e) => setThresholdPct(e.target.value)}
                  min="0.01"
                  max="0.50"
                  step="0.01"
                  required
                />
              </div>
              <div className="form-group">
                <label htmlFor="price-holding">Holding period (days) *</label>
                <input
                  id="price-holding"
                  type="number"
                  className="input"
                  aria-label="Holding period days"
                  value={holdingDays}
                  onChange={(e) => setHoldingDays(e.target.value)}
                  min="1"
                  max="120"
                  required
                />
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: '12px' }}>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? 'Creating…' : 'Create experiment'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => router.back()}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function NewExperimentPage() {
  return (
    <Suspense fallback={<div className="container" style={{ paddingTop: '40px' }}>Loading…</div>}>
      <NewExperimentForm />
    </Suspense>
  );
}
