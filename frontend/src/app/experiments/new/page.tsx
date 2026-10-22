'use client';

import { useState, useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

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

  useEffect(() => {
    api.instruments.list().then((data: any) => {
      setInstruments(data.instruments || []);
    }).catch(() => {});
  }, []);

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
        <div className="card" style={{ marginBottom: '20px', backgroundColor: '#f8f9fa', border: '2px solid #0070f3' }}>
          <h2 style={{ marginBottom: '16px', fontSize: '18px' }}>預覽草稿欄位</h2>
          <p style={{ fontSize: '13px', color: '#666', marginBottom: '16px' }}>
            以下欄位將從草稿套用到表單。請確認後點擊「確認套用」，或點擊「取消」返回。
          </p>
          <div style={{ display: 'grid', gap: '12px', marginBottom: '16px' }}>
            {requestedFields.map((field) => {
              const value = (draft as any)[field];
              if (!value) return null;
              return (
                <div key={field} style={{ display: 'flex', gap: '12px', padding: '8px 0', borderBottom: '1px solid #e0e0e0' }}>
                  <div style={{ width: '120px', fontWeight: '500', fontSize: '14px', color: '#555', flexShrink: 0 }}>
                    {FIELD_LABELS[field] || field}
                  </div>
                  <div style={{ fontSize: '14px', color: '#333', flex: 1 }}>
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
              <button type="button" className="btn" style={{ fontSize: '12px', padding: '4px 12px', background: assetTab === 'real' ? '#0070f3' : '#e0e0e0', color: assetTab === 'real' ? '#fff' : '#333' }} onClick={() => setAssetTab('real')}>
                真實產品 (US/HK)
              </button>
              <button type="button" className="btn" style={{ fontSize: '12px', padding: '4px 12px', background: assetTab === 'demo' ? '#0070f3' : '#e0e0e0', color: assetTab === 'demo' ? '#fff' : '#333' }} onClick={() => setAssetTab('demo')}>
                示範產品
              </button>
            </div>
            <select
              className="input"
              value={assetId}
              onChange={(e) => setAssetId(e.target.value)}
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
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                required
              />
            </div>
            <div className="form-group">
              <label>結束日期 *</label>
              <input
                type="date"
                className="input"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="form-group">
            <label>初始資金 (USD) *</label>
            <input
              type="number"
              className="input"
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
                value={slippageRate}
                onChange={(e) => setSlippageRate(e.target.value)}
                min="0"
                max="0.05"
                step="0.001"
              />
            </div>
          </div>

          {templateType === 'sma_crossover' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div className="form-group">
                <label>短均線天數 *</label>
                <input
                  type="number"
                  className="input"
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
