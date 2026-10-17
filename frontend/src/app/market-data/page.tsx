'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

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
    try {
      if (symbol.includes(':')) {
        const data = await api.instruments.bars(symbol, '2024-01-02', '2024-12-31');
        setBars(data.bars || []);
        const allBars = data.bars || [];
        setLatestBar(allBars.length > 0 ? allBars[allBars.length - 1] : null);
        setDataQuality(data.data_quality || null);
      } else {
        const [latest, barsData] = await Promise.all([
          api.marketData.latest(symbol),
          api.marketData.bars(symbol, '2024-01-02', '2024-02-13'),
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

  const handleLogout = () => {
    localStorage.removeItem('token');
    router.push('/');
  };

  const formatNumber = (num: number) => {
    return num.toLocaleString('zh-Hant', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('zh-Hant');
  };

  const maxClose = bars.length > 0 ? Math.max(...bars.map((b) => b.close)) : 0;
  const minClose = bars.length > 0 ? Math.min(...bars.map((b) => b.close)) : 0;
  const closeRange = maxClose - minClose || 1;

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1>市場資料</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/dashboard" className="btn btn-secondary">
            返回我的實驗
          </Link>
          <button onClick={handleLogout} className="btn btn-secondary">
            {brand.ui.auth.logoutZh}
          </button>
        </div>
      </div>

      <div
        style={{
          backgroundColor: '#fff3cd',
          border: '1px solid #ffc107',
          borderRadius: '6px',
          padding: '16px',
          marginBottom: '24px',
        }}
      >
        <strong>⚠ 警告：</strong>所有數據均為模擬或演示數據 — 非即時市場資料。僅供研究用途。
      </div>

      {error && (
        <div
          style={{
            backgroundColor: '#f8d7da',
            border: '1px solid #f5c6cb',
            borderRadius: '6px',
            padding: '16px',
            marginBottom: '24px',
            color: '#721c24',
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
              <div style={{ color: '#666', fontSize: '14px', marginBottom: '4px' }}>資料提供者</div>
              <div style={{ fontSize: '16px', fontWeight: 500 }}>{providerStatus.provider_name}</div>
            </div>
            <div>
              <div style={{ color: '#666', fontSize: '14px', marginBottom: '4px' }}>資料模式</div>
              <div
                style={{
                  display: 'inline-block',
                  padding: '4px 12px',
                  borderRadius: '12px',
                  fontSize: '14px',
                  backgroundColor: providerStatus.feed_mode === 'offline' ? '#fff3cd' : '#d4edda',
                  color: providerStatus.feed_mode === 'offline' ? '#856404' : '#155724',
                }}
              >
                {providerStatus.feed_mode === 'offline' ? '離線（模擬）' : providerStatus.feed_mode}
              </div>
            </div>
            <div>
              <div style={{ color: '#666', fontSize: '14px', marginBottom: '4px' }}>可用產品</div>
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
                backgroundColor: '#f8d7da',
                border: '1px solid #f5c6cb',
                borderRadius: '6px',
                fontSize: '14px',
                color: '#721c24',
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
            <button
              onClick={() => setSourceTab('real')}
              style={{
                padding: '6px 16px',
                borderRadius: '4px',
                border: '1px solid',
                borderColor: sourceTab === 'real' ? '#007bff' : '#ddd',
                backgroundColor: sourceTab === 'real' ? '#007bff' : 'white',
                color: sourceTab === 'real' ? 'white' : '#333',
                cursor: 'pointer',
                fontSize: '14px',
              }}
            >
              真實產品 (US/HK)
            </button>
            <button
              onClick={() => setSourceTab('demo')}
              style={{
                padding: '6px 16px',
                borderRadius: '4px',
                border: '1px solid',
                borderColor: sourceTab === 'demo' ? '#007bff' : '#ddd',
                backgroundColor: sourceTab === 'demo' ? '#007bff' : 'white',
                color: sourceTab === 'demo' ? 'white' : '#333',
                cursor: 'pointer',
                fontSize: '14px',
              }}
            >
              演示產品
            </button>
          </div>

          {sourceTab === 'real' && (
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              {(['ALL', 'US', 'HK'] as MarketFilter[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMarketFilter(m)}
                  style={{
                    padding: '4px 12px',
                    borderRadius: '4px',
                    border: '1px solid',
                    borderColor: marketFilter === m ? '#28a745' : '#ddd',
                    backgroundColor: marketFilter === m ? '#28a745' : 'white',
                    color: marketFilter === m ? 'white' : '#333',
                    cursor: 'pointer',
                    fontSize: '13px',
                  }}
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
              value={selectedSymbol}
              onChange={handleSymbolChange}
              style={{
                width: '100%',
                padding: '10px 12px',
                fontSize: '14px',
                border: '1px solid #ddd',
                borderRadius: '4px',
                backgroundColor: 'white',
              }}
            >
              {filteredInstruments.map((inst) => (
                <option key={inst.instrument_id} value={inst.instrument_id}>
                  {inst.instrument_id} — {inst.name} ({inst.market} · {inst.currency})
                </option>
              ))}
            </select>

            {selectedInstrument && (
              <div style={{ marginTop: '12px', padding: '12px', backgroundColor: '#f8f9fa', borderRadius: '6px', fontSize: '14px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '8px' }}>
                  <div><span style={{ color: '#666' }}>交易所：</span>{selectedInstrument.exchange}</div>
                  <div><span style={{ color: '#666' }}>資產類型：</span>{selectedInstrument.asset_type}</div>
                  <div><span style={{ color: '#666' }}>貨幣：</span>{selectedInstrument.currency}</div>
                  <div><span style={{ color: '#666' }}>時區：</span>{selectedInstrument.timezone}</div>
                  <div>
                    <span style={{ color: '#666' }}>每手股數：</span>
                    {selectedInstrument.lot_size !== null
                      ? `${selectedInstrument.lot_size}${selectedInstrument.lot_size_verified ? '' : '（未驗證）'}`
                      : '未知'}
                  </div>
                  {dataQuality && (
                    <>
                      <div>
                        <span style={{ color: '#666' }}>資料性質：</span>
                        {DATA_NATURE_LABELS[dataQuality.data_nature] || dataQuality.data_nature}
                      </div>
                      <div>
                        <span style={{ color: '#666' }}>調整狀態：</span>
                        {ADJUSTMENT_LABELS[dataQuality.adjustment] || dataQuality.adjustment}
                      </div>
                      {dataQuality.latest_bar_date && (
                        <div>
                          <span style={{ color: '#666' }}>資料截至：</span>
                          {formatDate(dataQuality.latest_bar_date)}
                          {dataQuality.bar_age_days !== null && `（${dataQuality.bar_age_days} 天前）`}
                        </div>
                      )}
                    </>
                  )}
                </div>
                {(dataQuality?.note_zh || dataQuality?.note) && (
                  <div style={{ marginTop: '8px', fontSize: '13px', color: '#666' }}>
                    {dataQuality?.note_zh || dataQuality?.note}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div style={{ marginBottom: '20px' }}>
            <select
              value={selectedSymbol}
              onChange={handleSymbolChange}
              style={{
                width: '100%',
                padding: '10px 12px',
                fontSize: '14px',
                border: '1px solid #ddd',
                borderRadius: '4px',
                backgroundColor: 'white',
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
          <div style={{ textAlign: 'center', padding: '40px' }}>載入中...</div>
        ) : !latestBar ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
            尚無資料
          </div>
        ) : (
          <>
            <div style={{ marginBottom: '32px' }}>
              <h4 style={{ marginBottom: '16px', color: '#333' }}>
                最新資料 — {selectedSymbol}
                {selectedInstrument && ` (${selectedInstrument.name})`}
                {dataQuality?.is_stale && (
                  <span
                    style={{
                      marginLeft: '12px',
                      padding: '2px 10px',
                      borderRadius: '12px',
                      fontSize: '12px',
                      backgroundColor: '#fff3cd',
                      color: '#856404',
                      verticalAlign: 'middle',
                    }}
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
                  backgroundColor: '#f8f9fa',
                  borderRadius: '6px',
                }}
              >
                <div>
                  <div style={{ color: '#666', fontSize: '12px', marginBottom: '4px' }}>日期</div>
                  <div style={{ fontSize: '14px', fontWeight: 500 }}>{formatDate(latestBar.date)}</div>
                </div>
                <div>
                  <div style={{ color: '#666', fontSize: '12px', marginBottom: '4px' }}>開盤價</div>
                  <div style={{ fontSize: '14px', fontWeight: 500 }}>{formatNumber(latestBar.open)}</div>
                </div>
                <div>
                  <div style={{ color: '#666', fontSize: '12px', marginBottom: '4px' }}>最高價</div>
                  <div style={{ fontSize: '14px', fontWeight: 500, color: '#28a745' }}>
                    {formatNumber(latestBar.high)}
                  </div>
                </div>
                <div>
                  <div style={{ color: '#666', fontSize: '12px', marginBottom: '4px' }}>最低價</div>
                  <div style={{ fontSize: '14px', fontWeight: 500, color: '#dc3545' }}>
                    {formatNumber(latestBar.low)}
                  </div>
                </div>
                <div>
                  <div style={{ color: '#666', fontSize: '12px', marginBottom: '4px' }}>收盤價</div>
                  <div style={{ fontSize: '14px', fontWeight: 500 }}>{formatNumber(latestBar.close)}</div>
                </div>
                <div>
                  <div style={{ color: '#666', fontSize: '12px', marginBottom: '4px' }}>成交量</div>
                  <div style={{ fontSize: '14px', fontWeight: 500 }}>
                    {latestBar.volume !== null ? latestBar.volume.toLocaleString('zh-Hant') : '—'}
                  </div>
                </div>
              </div>
            </div>

            {bars.length > 0 && (
              <>
                <div style={{ marginBottom: '32px' }}>
                  <h4 style={{ marginBottom: '16px', color: '#333' }}>收盤價走勢圖（模擬數據）</h4>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-end',
                      gap: '2px',
                      height: '200px',
                      padding: '16px',
                      backgroundColor: '#f8f9fa',
                      borderRadius: '6px',
                      border: '1px solid #e9ecef',
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
                            backgroundColor: '#007bff',
                            borderRadius: '2px 2px 0 0',
                            cursor: 'pointer',
                            transition: 'background-color 0.2s',
                            minWidth: '3px',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = '#0056b3';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = '#007bff';
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
                      color: '#666',
                    }}
                  >
                    <span>最低: {formatNumber(minClose)}</span>
                    <span>顯示最近 {Math.min(bars.length, 60)} 天</span>
                    <span>最高: {formatNumber(maxClose)}</span>
                  </div>
                </div>

                <div>
                  <h4 style={{ marginBottom: '16px', color: '#333' }}>歷史資料表（最近 30 天）</h4>
                  <div style={{ overflowX: 'auto' }}>
                    <table
                      style={{
                        width: '100%',
                        borderCollapse: 'collapse',
                        fontSize: '14px',
                      }}
                    >
                      <thead>
                        <tr style={{ backgroundColor: '#f8f9fa', borderBottom: '2px solid #dee2e6' }}>
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
                              borderBottom: '1px solid #dee2e6',
                              backgroundColor: idx % 2 === 0 ? 'white' : '#f8f9fa',
                            }}
                          >
                            <td style={{ padding: '12px' }}>{formatDate(bar.date)}</td>
                            <td style={{ padding: '12px', textAlign: 'right' }}>{formatNumber(bar.open)}</td>
                            <td style={{ padding: '12px', textAlign: 'right', color: '#28a745' }}>
                              {formatNumber(bar.high)}
                            </td>
                            <td style={{ padding: '12px', textAlign: 'right', color: '#dc3545' }}>
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
  );
}
