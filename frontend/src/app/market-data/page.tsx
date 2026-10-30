'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import AppShell from '@/components/shell/AppShell';
import { formatDate } from '@/lib/format';

interface Instrument {
  instrument_id: string;
  symbol: string;
  name: string;
  market: string;
  exchange: string;
  asset_type: string;
  currency: string;
  timezone: string;
  lot_size: number | null;
  lot_size_verified: boolean;
}

interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

interface DataQuality {
  data_nature: string;
  adjustment: string;
  timezone: string | null;
  note: string | null;
  note_zh: string | null;
  latest_bar_date: string | null;
  bar_age_days: number | null;
  is_stale: boolean;
  stale_threshold_days: number;
}

interface ProviderStatus {
  active_provider: string;
  provider_name: string;
  feed_mode: string;
  available: boolean;
  status: Record<string, unknown>;
}

const DATA_NATURE_LABELS: Record<string, string> = {
  synthetic: '模擬資料',
  live: '即時資料',
  delayed: '延遲資料',
  unknown: '未知',
};

const ADJUSTMENT_LABELS: Record<string, string> = {
  not_applicable: '不適用（模擬數據）',
  unadjusted: '未調整',
  adjusted: '已調整（股息/拆股）',
  unknown: '未知（未確認）',
};

const HEALTH_LABELS: Record<string, string> = {
  ok: '正常',
  degraded: '異常（連續錯誤）',
  rate_limited: '已達速率限制',
  disconnected: '未連線',
};

type MarketFilter = 'ALL' | 'US' | 'HK';
type SourceTab = 'real' | 'demo';

const DEMO_SYMBOLS = ['DEMO-ALPHA', 'DEMO-BETA', 'DEMO-BENCHMARK'];

const controlStyle = (selected: boolean, tone: 'primary' | 'positive' = 'primary') => ({
  padding: '6px 16px',
  borderRadius: '4px',
  border: '1px solid',
  borderColor: selected
    ? tone === 'positive'
      ? 'var(--positive)'
      : 'var(--primary)'
    : 'var(--border-strong)',
  backgroundColor: selected
    ? tone === 'positive'
      ? 'rgba(34,197,94,0.16)'
      : 'rgba(59,130,246,0.16)'
    : 'var(--surface-2)',
  color: selected ? 'var(--text-white)' : 'var(--text-primary)',
  cursor: 'pointer',
  fontSize: '14px',
});

export default function MarketDataPage() {
  const router = useRouter();
  const [sourceTab, setSourceTab] = useState<SourceTab>('real');
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('ALL');
  const [selectedSymbol, setSelectedSymbol] = useState<string>('');
  const [latestBar, setLatestBar] = useState<Bar | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [dataQuality, setDataQuality] = useState<DataQuality | null>(null);
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [dataLoading, setDataLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadInstruments();
    loadProviderStatus();
  }, [router]);

  const loadProviderStatus = async () => {
    try {
      const data = await api.marketData.status();
      setProviderStatus(data);
    } catch {
      // non-critical
    }
  };

  const loadInstruments = async () => {
    setLoading(true);
    try {
      const data = await api.instruments.list();
      const items: Instrument[] = data.instruments || [];
      setInstruments(items);
      if (items.length > 0 && !selectedSymbol) {
        const first = items[0].instrument_id;
        setSelectedSymbol(first);
        loadBarData(first);
      }
    } catch (err) {
      console.error('Failed to load instruments:', err);
      setError('無法載入產品目錄');
    } finally {
      setLoading(false);
    }
  };

  const loadBarData = useCallback(async (symbol: string) => {
    setDataLoading(true);
    setError(null);
    setDataQuality(null);
    const today = new Date();
    const yearAgo = new Date(today.getTime() - 366 * 24 * 60 * 60 * 1000);
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    try {
      if (symbol.includes(':')) {
        const data = await api.instruments.bars(symbol, iso(yearAgo), iso(today));
        setBars(data.bars || []);
        const allBars = data.bars || [];
        setLatestBar(allBars.length > 0 ? allBars[allBars.length - 1] : null);
        setDataQuality(data.data_quality || null);
      } else {
        const [latest, barsData] = await Promise.all([
          api.marketData.latest(symbol),
          api.marketData.bars(symbol, iso(yearAgo), iso(today)),
        ]);
        setLatestBar(latest?.bar || null);
        setBars(barsData?.bars || []);
        setDataQuality(latest?.data_quality || barsData?.data_quality || null);
      }
    } catch (err) {
      console.error('Failed to load bar data:', err);
      setError('無法載入市場資料');
    } finally {
      setDataLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedSymbol) {
      loadBarData(selectedSymbol);
    }
  }, [selectedSymbol, loadBarData]);

  const filteredInstruments = instruments.filter((inst) => {
    if (marketFilter === 'ALL') return true;
    return inst.market === marketFilter;
  });

  const selectedInstrument = instruments.find((i) => i.instrument_id === selectedSymbol);

  const handleSymbolChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedSymbol(e.target.value);
  };

  const formatNumber = (num: number) => {
    return num.toLocaleString('zh-Hant', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  const maxClose = bars.length > 0 ? Math.max(...bars.map((b) => b.close)) : 0;
  const minClose = bars.length > 0 ? Math.min(...bars.map((b) => b.close)) : 0;
  const closeRange = maxClose - minClose || 1;

  return (
    <AppShell active="market">
      <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
        <div style={{ marginBottom: '30px' }}>
          <h1 style={{ margin: 0 }}>市場資料</h1>
        </div>

        <div className="disclaimer" style={{ marginBottom: '24px' }}>
          <strong>警告：</strong>所有數據均為模擬或演示數據 — 非即時市場資料。僅供研究用途。
        </div>

        {error && (
          <div
            style={{
              backgroundColor: 'rgba(239,68,68,0.12)',
              border: '1px solid var(--negative)',
              borderRadius: '6px',
              padding: '16px',
              marginBottom: '24px',
              color: 'var(--text-primary)',
            }}
          >
            {error}
          </div>
        )}

        {providerStatus && (
          <div className="card" style={{ marginBottom: '24px' }}>
            <h3 style={{ marginBottom: '16px' }}>資料來源狀態</h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
              <div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '14px', marginBottom: '4px' }}>資料提供者</div>
                <div style={{ fontSize: '16px', fontWeight: 500 }}>{providerStatus.provider_name}</div>
              </div>
              <div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '14px', marginBottom: '4px' }}>資料模式</div>
                <div
                  className={`status-pill status-${providerStatus.feed_mode === 'offline' ? 'warn' : 'ok'}`}
                  style={{ padding: '4px 12px', fontSize: '14px' }}
                >
                  {providerStatus.feed_mode === 'offline' ? '離線（模擬）' : providerStatus.feed_mode}
                </div>
              </div>
              <div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '14px', marginBottom: '4px' }}>可用產品</div>
                <div style={{ fontSize: '16px', fontWeight: 500 }}>
                  {instruments.length} 個真實產品 + 3 個演示產品
                </div>
              </div>
            </div>
            {typeof providerStatus.status?.health === 'string' && providerStatus.status.health !== 'ok' && (
              <div
                style={{
                  marginTop: '16px',
                  padding: '12px',
                  backgroundColor: 'rgba(239,68,68,0.12)',
                  border: '1px solid var(--negative)',
                  borderRadius: '6px',
                  fontSize: '14px',
                  color: 'var(--text-primary)',
                }}
              >
                <strong>提供者健康狀態：</strong>
                {HEALTH_LABELS[providerStatus.status.health] || providerStatus.status.health}
                {typeof providerStatus.status?.rate_limited_retry_after_seconds === 'number' &&
                  providerStatus.status.rate_limited_retry_after_seconds > 0 &&
                  ` — 建議 ${providerStatus.status.rate_limited_retry_after_seconds} 秒後重試`}
                {typeof providerStatus.status?.last_error === 'string' && (
                  <div style={{ marginTop: '4px', fontSize: '13px' }}>最近錯誤：{providerStatus.status.last_error}</div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="card" style={{ marginBottom: '24px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button onClick={() => setSourceTab('real')} style={controlStyle(sourceTab === 'real')}>
                真實產品 (US/HK)
              </button>
              <button onClick={() => setSourceTab('demo')} style={controlStyle(sourceTab === 'demo')}>
                演示產品
              </button>
            </div>

            {sourceTab === 'real' && (
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                {(['ALL', 'US', 'HK'] as MarketFilter[]).map((m) => (
                  <button
                    key={m}
                    onClick={() => setMarketFilter(m)}
                    style={{ ...controlStyle(marketFilter === m, 'positive'), fontSize: '13px', padding: '4px 12px' }}
                  >
                    {m === 'ALL' ? '全部' : m === 'US' ? '美股' : '港股'}
                  </button>
                ))}
              </div>
            )}
          </div>

          {sourceTab === 'real' ? (
            <div style={{ marginBottom: '20px' }}>
              <select
                aria-label="選擇標的"
                value={selectedSymbol}
                onChange={handleSymbolChange}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  fontSize: '14px',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '4px',
                  backgroundColor: 'var(--surface-2)',
                  color: 'var(--text-primary)',
                }}
              >
                {filteredInstruments.map((inst) => (
                  <option key={inst.instrument_id} value={inst.instrument_id}>
                    {inst.instrument_id} — {inst.name} ({inst.market} · {inst.currency})
                  </option>
                ))}
              </select>

              {selectedInstrument && (
                <div style={{ marginTop: '12px', padding: '12px', backgroundColor: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '14px' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '8px' }}>
                    <div><span style={{ color: 'var(--text-secondary)' }}>交易所：</span>{selectedInstrument.exchange}</div>
                    <div><span style={{ color: 'var(--text-secondary)' }}>資產類型：</span>{selectedInstrument.asset_type}</div>
                    <div><span style={{ color: 'var(--text-secondary)' }}>貨幣：</span>{selectedInstrument.currency}</div>
                    <div><span style={{ color: 'var(--text-secondary)' }}>時區：</span>{selectedInstrument.timezone}</div>
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>每手股數：</span>
                      {selectedInstrument.lot_size !== null
                        ? `${selectedInstrument.lot_size}${selectedInstrument.lot_size_verified ? '' : '（未驗證）'}`
                        : '未知'}
                    </div>
                    {dataQuality && (
                      <>
                        <div>
                          <span style={{ color: 'var(--text-secondary)' }}>資料性質：</span>
                          {DATA_NATURE_LABELS[dataQuality.data_nature] || dataQuality.data_nature}
                        </div>
                        <div>
                          <span style={{ color: 'var(--text-secondary)' }}>調整狀態：</span>
                          {ADJUSTMENT_LABELS[dataQuality.adjustment] || dataQuality.adjustment}
                        </div>
                        {dataQuality.latest_bar_date && (
                          <div>
                            <span style={{ color: 'var(--text-secondary)' }}>資料截至：</span>
                            {formatDate(dataQuality.latest_bar_date)}
                            {dataQuality.bar_age_days !== null && `（${dataQuality.bar_age_days} 天前）`}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                  {(dataQuality?.note_zh || dataQuality?.note) && (
                    <div style={{ marginTop: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                      {dataQuality?.note_zh || dataQuality?.note}
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div style={{ marginBottom: '20px' }}>
              <select
                aria-label="選擇標的"
                value={selectedSymbol}
                onChange={handleSymbolChange}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  fontSize: '14px',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '4px',
                  backgroundColor: 'var(--surface-2)',
                  color: 'var(--text-primary)',
                }}
              >
                {DEMO_SYMBOLS.map((symbol) => (
                  <option key={symbol} value={symbol}>
                    {symbol}（合成數據）
                  </option>
                ))}
              </select>
            </div>
          )}

          {dataLoading ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>載入中...</div>
          ) : !latestBar ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
              尚無資料
            </div>
          ) : (
            <>
              <div style={{ marginBottom: '32px' }}>
                <h4 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>
                  最新資料 — {selectedSymbol}
                  {selectedInstrument && ` (${selectedInstrument.name})`}
                  {dataQuality?.is_stale && (
                    <span
                      className="status-pill status-warn"
                      style={{ marginLeft: '12px', verticalAlign: 'middle' }}
                    >
                      資料已過期
                      {dataQuality.bar_age_days !== null && `（${dataQuality.bar_age_days} 天未更新）`}
                    </span>
                  )}
                </h4>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
                    gap: '16px',
                    padding: '20px',
                    backgroundColor: 'var(--surface-2)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: '6px',
                  }}
                >
                  <div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>日期</div>
                    <div style={{ fontSize: '14px', fontWeight: 500 }}>{formatDate(latestBar.date)}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>開盤價</div>
                    <div style={{ fontSize: '14px', fontWeight: 500 }}>{formatNumber(latestBar.open)}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>最高價</div>
                    <div style={{ fontSize: '14px', fontWeight: 500, color: 'var(--positive)' }}>
                      {formatNumber(latestBar.high)}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>最低價</div>
                    <div style={{ fontSize: '14px', fontWeight: 500, color: 'var(--negative)' }}>
                      {formatNumber(latestBar.low)}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>收盤價</div>
                    <div style={{ fontSize: '14px', fontWeight: 500 }}>{formatNumber(latestBar.close)}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>成交量</div>
                    <div style={{ fontSize: '14px', fontWeight: 500 }}>
                      {latestBar.volume !== null ? latestBar.volume.toLocaleString('zh-Hant') : '—'}
                    </div>
                  </div>
                </div>
              </div>

              {bars.length > 0 && (
                <>
                  <div style={{ marginBottom: '32px' }}>
                    <h4 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>收盤價走勢圖（模擬數據）</h4>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'flex-end',
                        gap: '2px',
                        height: '200px',
                        padding: '16px',
                        backgroundColor: 'var(--surface-2)',
                        borderRadius: '6px',
                        border: '1px solid var(--border-strong)',
                        overflow: 'hidden',
                      }}
                    >
                      {bars.slice(-60).map((bar, idx) => {
                        const heightPercent = ((bar.close - minClose) / closeRange) * 100;
                        const height = Math.max(heightPercent, 5);
                        return (
                          <div
                            key={idx}
                            title={`${formatDate(bar.date)}: ${formatNumber(bar.close)}`}
                            style={{
                              flex: 1,
                              height: `${height}%`,
                              backgroundColor: 'var(--primary)',
                              borderRadius: '2px 2px 0 0',
                              cursor: 'pointer',
                              transition: 'background-color 0.2s',
                              minWidth: '3px',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.backgroundColor = 'var(--primary-solid)';
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.backgroundColor = 'var(--primary)';
                            }}
                          />
                        );
                      })}
                    </div>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        marginTop: '8px',
                        fontSize: '12px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      <span>最低: {formatNumber(minClose)}</span>
                      <span>顯示最近 {Math.min(bars.length, 60)} 天</span>
                      <span>最高: {formatNumber(maxClose)}</span>
                    </div>
                  </div>

                  <div>
                    <h4 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>歷史資料表（最近 30 天）</h4>
                    <div style={{ overflowX: 'auto' }}>
                      <table
                        style={{
                          width: '100%',
                          borderCollapse: 'collapse',
                          fontSize: '14px',
                        }}
                      >
                        <thead>
                          <tr style={{ backgroundColor: 'var(--surface-2)', borderBottom: '2px solid var(--border-strong)', color: 'var(--text-secondary)' }}>
                            <th style={{ padding: '12px', textAlign: 'left', fontWeight: 600 }}>日期</th>
                            <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>開盤價</th>
                            <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>最高價</th>
                            <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>最低價</th>
                            <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>收盤價</th>
                            <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>成交量</th>
                          </tr>
                        </thead>
                        <tbody>
                          {bars.slice(-30).reverse().map((bar, idx) => (
                            <tr
                              key={idx}
                              style={{
                                borderBottom: '1px solid var(--border-strong)',
                                backgroundColor: idx % 2 === 0 ? 'transparent' : 'var(--surface-2)',
                              }}
                            >
                              <td style={{ padding: '12px' }}>{formatDate(bar.date)}</td>
                              <td style={{ padding: '12px', textAlign: 'right' }}>{formatNumber(bar.open)}</td>
                              <td style={{ padding: '12px', textAlign: 'right', color: 'var(--positive)' }}>
                                {formatNumber(bar.high)}
                              </td>
                              <td style={{ padding: '12px', textAlign: 'right', color: 'var(--negative)' }}>
                                {formatNumber(bar.low)}
                              </td>
                              <td style={{ padding: '12px', textAlign: 'right', fontWeight: 500 }}>
                                {formatNumber(bar.close)}
                              </td>
                              <td style={{ padding: '12px', textAlign: 'right' }}>
                                {bar.volume !== null ? bar.volume.toLocaleString('zh-Hant') : '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
