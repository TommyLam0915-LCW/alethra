'use client';

import { useState, useEffect, useRef, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';
import type { DataCoverage } from '@/components/workspace/types';

const DEMO_ASSETS = ['DEMO-ALPHA', 'DEMO-BETA', 'DEMO-BENCHMARK'];
const TEMPLATES = [
  { value: 'buy_and_hold', label: 'Buy & Hold', labelZh: '買入並持有' },
  { value: 'sma_crossover', label: 'SMA Crossover', labelZh: '均線交叉' },
  { value: 'price_change_fixed_hold', label: 'Price Change', labelZh: '跌幅定持' },
];

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
  title: '標題',
  hypothesis: '研究假設',
  strategy_template: '策略模板',
  asset_symbol: '研究標的',
  benchmark_symbol: '基準標的',
  initial_cash: '初始資金',
  commission_rate: '手續費率',
  slippage_rate: '滑價率',
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
          setDraftError('只有已接受的草稿才能套用欄位');
          setDraftLoading(false);
          return;
        }
        setDraft(data);
        setShowPreview(true);
        setDraftLoading(false);
      })
      .catch((err: any) => {
        setDraftError(err.message || '載入草稿失敗');
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
      setError(err.message || '建立失敗');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container" style={{ maxWidth: '800px', paddingTop: '40px', paddingBottom: '40px' }}>
      <h1 style={{ marginBottom: '30px' }}>{brand.ui.experiment.createTitleZh}</h1>

      <div className="disclaimer">
        <strong>提醒：</strong>本系統使用示範資料進行回測，結果僅供研究參考，不代表真實市場表現。
      </div>

      {draftLoading && (
        <div className="alert alert-info" style={{ marginBottom: '20px', fontSize: '13px' }}>
          載入研究草稿...
        </div>
      )}

      {draftError && (
        <div className="alert alert-warning" style={{ marginBottom: '20px', fontSize: '13px' }}>
          {draftError}
        </div>
      )}

      {showPreview && draft && (
        <div className="card" style={{ marginBottom: '20px', backgroundColor: 'var(--surface-2)', border: '2px solid var(--primary)' }}>
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>預覽草稿欄位</h2>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
            以下欄位將從草稿套用到表單。請確認後點擊「確認套用」，或點擊「取消」返回。
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
              確認套用 {requestedFields.length} 個欄位
            </button>
            <button className="btn btn-secondary" onClick={handleCancelPreview}>
              取消
            </button>
          </div>
        </div>
      )}

      {appliedFields.length > 0 && (
        <div className="alert alert-info" style={{ marginBottom: '20px', fontSize: '13px' }}>
          <strong>已從研究草稿套用：</strong>{appliedFields.join('、')}。
          請確認以下欄位，並補完開始日期、結束日期等執行參數後再建立實驗。
        </div>
      )}

      {error && <div className="alert alert-warning">{error}</div>}

      <div className="card">
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>實驗標題 *</label>
            <input
              type="text"
              className="input"
              data-testid="exp-title"
              aria-label="實驗標題"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：測試 DEMO-ALPHA 的均線交叉策略"
              required
            />
          </div>

          <div className="form-group">
            <label>研究假設</label>
            <textarea
              className="input"
              aria-label="研究假設"
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              placeholder="描述你的投資假設"
              rows={3}
              style={{ resize: 'vertical' }}
            />
          </div>

          <div className="form-group">
            <label>策略模板 *</label>
            <select
              className="input"
              aria-label="策略模板"
              value={templateType}
              onChange={(e) => setTemplateType(e.target.value)}
            >
              {TEMPLATES.map(t => (
                <option key={t.value} value={t.value}>{t.labelZh} ({t.label})</option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label>資產 *</label>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
              <button type="button" className={assetTab === 'real' ? 'btn btn-primary' : 'btn btn-secondary'} aria-pressed={assetTab === 'real'} style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => setAssetTab('real')}>
                真實產品 (US/HK)
              </button>
              <button type="button" className={assetTab === 'demo' ? 'btn btn-primary' : 'btn btn-secondary'} aria-pressed={assetTab === 'demo'} style={{ fontSize: '12px', padding: '4px 12px' }} onClick={() => setAssetTab('demo')}>
                示範產品
              </button>
            </div>
            <select
              className="input"
              data-testid="exp-asset"
              aria-label="資產"
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
            <label>基準 (Benchmark)</label>
            <select
              className="input"
              aria-label="基準 (Benchmark)"
              value={benchmarkId}
              onChange={(e) => setBenchmarkId(e.target.value)}
            >
              <option value="">無</option>
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
              <label>開始日期 *</label>
              <input
                type="date"
                className="input"
                data-testid="exp-start-date"
                aria-label="開始日期"
                value={startDate}
                onChange={(e) => {
                  datesEditedRef.current = true;
                  setStartDate(e.target.value);
                }}
                required
              />
            </div>
            <div className="form-group">
              <label>結束日期 *</label>
              <input
                type="date"
                className="input"
                data-testid="exp-end-date"
                aria-label="結束日期"
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
                {assetId} 的資料覆蓋：伺服器已儲存 K 線 {coverage?.stored_start ?? 'UNKNOWN'} 至 {coverage?.stored_end ?? 'UNKNOWN'}（{coverage?.stored_bars ?? 0} 根）；
                提供者聲明可供應 {coverage?.available_start ?? 'UNKNOWN'} 至 {coverage?.available_end ?? 'UNKNOWN'}。回測、回放與成交只會讀取已儲存的 K 線，日期已依該範圍預設。
              </>
            ) : (
              <>
                {assetId} 的資料覆蓋：UNKNOWN（未取得覆蓋查詢結果，示範標的不提供覆蓋查詢）。日期為預設期間，建立後請先確認回測確實有數據。
              </>
            )}
          </p>

          {coverageLoaded && (coverage?.stored_bars ?? 0) === 0 && (
            <div className="alert alert-warning" data-testid="exp-coverage-empty" style={{ fontSize: '13px', marginBottom: '16px' }}>
              {assetId} 在伺服器上沒有任何已儲存的 K 線：回測會得到空結果，回放也無法推進第一步。此頁面只能查詢覆蓋，無法載入數據，需先由數據管道把 K 線寫入儲存。
            </div>
          )}

          {coverageLoaded && (coverage?.stored_bars ?? 0) > 0 && !touchesStoredBars(startDate, endDate, coverage) && (
            <div className="alert alert-warning" data-testid="exp-coverage-mismatch" style={{ fontSize: '13px', marginBottom: '16px' }}>
              所選期間（{startDate || 'UNKNOWN'} 至 {endDate || 'UNKNOWN'}）與已儲存 K 線（{coverage?.stored_start ?? 'UNKNOWN'} 至 {coverage?.stored_end ?? 'UNKNOWN'}）沒有重疊，回測與回放會找不到數據。
            </div>
          )}

          <div className="form-group">
            <label>初始資金 (USD) *</label>
            <input
              type="number"
              className="input"
              aria-label="初始資金 (USD)"
              value={initialCash}
              onChange={(e) => setInitialCash(e.target.value)}
              min="100"
              step="100"
              required
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label>佣金率 (0-0.05)</label>
              <input
                type="number"
                className="input"
                aria-label="佣金率"
                value={commissionRate}
                onChange={(e) => setCommissionRate(e.target.value)}
                min="0"
                max="0.05"
                step="0.001"
              />
            </div>
            <div className="form-group">
              <label>滑價率 (0-0.05)</label>
              <input
                type="number"
                className="input"
                aria-label="滑價率"
                value={slippageRate}
                onChange={(e) => setSlippageRate(e.target.value)}
                min="0"
                max="0.05"
                step="0.001"
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label>停損價 (USD，可留空)</label>
              <input
                type="number"
                className="input"
                data-testid="risk-stop-price"
                aria-label="停損價 (USD)"
                value={stopPrice}
                onChange={(e) => setStopPrice(e.target.value)}
                step="0.01"
                placeholder="例如 190.00"
              />
            </div>
            <div className="form-group">
              <label>停損幅度 (0-1，可留空)</label>
              <input
                type="number"
                className="input"
                data-testid="risk-stop-pct"
                aria-label="停損幅度"
                value={stopPct}
                onChange={(e) => setStopPct(e.target.value)}
                min="0"
                max="1"
                step="0.01"
                placeholder="例如 0.05"
              />
            </div>
            <div className="form-group">
              <label>目標價 (USD，可留空)</label>
              <input
                type="number"
                className="input"
                data-testid="risk-target-price"
                aria-label="目標價 (USD)"
                value={targetPrice}
                onChange={(e) => setTargetPrice(e.target.value)}
                step="0.01"
                placeholder="例如 220.00"
              />
            </div>
            <div className="form-group">
              <label>目標幅度 (0-1，可留空)</label>
              <input
                type="number"
                className="input"
                data-testid="risk-target-pct"
                aria-label="目標幅度"
                value={targetPct}
                onChange={(e) => setTargetPct(e.target.value)}
                min="0"
                max="1"
                step="0.01"
                placeholder="例如 0.10"
              />
            </div>
          </div>

          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '-8px', marginBottom: '16px' }}>
            風險計劃用於成交後的歸因：系統以入場價與停損價計算每筆交易的計劃風險與 R 值，並不會在價格觸及停損或目標時自動平倉。留空即冇宣稱停損，該筆交易嘅計劃風險與實現 R 會標示為「不可計算」，唔會被當成 0。同時填寫價格與幅度時以價格為準；停損價等於或高於入場價時視為冇有效停損。
          </p>

          {templateType === 'sma_crossover' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div className="form-group">
                <label>短均線天數 *</label>
                <input
                  type="number"
                  className="input"
                  data-testid="sma-short-window"
                  aria-label="短均線天數"
                  value={shortWindow}
                  onChange={(e) => setShortWindow(e.target.value)}
                  min="2"
                  max="60"
                  required
                />
              </div>
              <div className="form-group">
                <label>長均線天數 *</label>
                <input
                  type="number"
                  className="input"
                  data-testid="sma-long-window"
                  aria-label="長均線天數"
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
                <label>回望天數 *</label>
                <input
                  type="number"
                  className="input"
                  aria-label="回望天數"
                  value={lookbackDays}
                  onChange={(e) => setLookbackDays(e.target.value)}
                  min="1"
                  max="60"
                  required
                />
              </div>
              <div className="form-group">
                <label>跌幅門檻 (0.01-0.50) *</label>
                <input
                  type="number"
                  className="input"
                  aria-label="跌幅門檻"
                  value={thresholdPct}
                  onChange={(e) => setThresholdPct(e.target.value)}
                  min="0.01"
                  max="0.50"
                  step="0.01"
                  required
                />
              </div>
              <div className="form-group">
                <label>持有天數 *</label>
                <input
                  type="number"
                  className="input"
                  aria-label="持有天數"
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
              {loading ? '建立中...' : '建立實驗'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => router.back()}>
              取消
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function NewExperimentPage() {
  return (
    <Suspense fallback={<div className="container" style={{ paddingTop: '40px' }}>載入中...</div>}>
      <NewExperimentForm />
    </Suspense>
  );
}
