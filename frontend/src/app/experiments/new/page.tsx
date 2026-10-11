'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

const DEMO_ASSETS = ['DEMO-ALPHA', 'DEMO-BETA', 'DEMO-BENCHMARK'];
const TEMPLATES = [
  { value: 'buy_and_hold', label: 'Buy & Hold', labelZh: '買入並持有' },
  { value: 'sma_crossover', label: 'SMA Crossover', labelZh: '均線交叉' },
  { value: 'price_change_fixed_hold', label: 'Price Change', labelZh: '跌幅定持' },
];

export default function NewExperimentPage() {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [hypothesis, setHypothesis] = useState('');
  const [templateType, setTemplateType] = useState('buy_and_hold');
  const [assetId, setAssetId] = useState('DEMO-ALPHA');
  const [benchmarkId, setBenchmarkId] = useState('DEMO-BENCHMARK');
  const [startDate, setStartDate] = useState('2024-01-02');
  const [endDate, setEndDate] = useState('2024-02-14');
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
            <select
              className="input"
              value={assetId}
              onChange={(e) => setAssetId(e.target.value)}
            >
              {DEMO_ASSETS.filter(a => a !== 'DEMO-BENCHMARK').map(a => (
                <option key={a} value={a}>{a}</option>
              ))}
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
              {DEMO_ASSETS.map(a => (
                <option key={a} value={a}>{a}</option>
              ))}
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
