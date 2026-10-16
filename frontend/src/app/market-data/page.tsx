'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

interface MarketDataStatus {
  provider: string;
  is_demo: boolean;
  last_updated: string;
}

interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const SYMBOLS = ['DEMO-ALPHA', 'DEMO-BETA', 'DEMO-BENCHMARK'];

export default function MarketDataPage() {
  const router = useRouter();
  const [status, setStatus] = useState<MarketDataStatus | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState(SYMBOLS[0]);
  const [latestBar, setLatestBar] = useState<Bar | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }

    loadStatus();
    loadSymbolData(SYMBOLS[0]);
  }, [router]);

  const loadStatus = async () => {
    try {
      const data = await api.marketData.status();
      setStatus(data);
    } catch (err) {
      console.error('Failed to load status:', err);
      setError('無法載入資料來源狀態');
    }
  };

  const loadSymbolData = async (symbol: string) => {
    setLoading(true);
    setError(null);
    try {
      const [latest, barsData] = await Promise.all([
        api.marketData.latest(symbol),
        api.marketData.bars(symbol),
      ]);
      setLatestBar(latest);
      setBars(barsData);
    } catch (err) {
      console.error('Failed to load market data:', err);
      setError('無法載入市場資料');
    } finally {
      setLoading(false);
    }
  };

  const handleSymbolChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const symbol = e.target.value;
    setSelectedSymbol(symbol);
    loadSymbolData(symbol);
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

  const maxClose = Math.max(...bars.map((b) => b.close));
  const minClose = Math.min(...bars.map((b) => b.close));
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
        <strong>⚠ 警告：</strong>僅供示範資料 — 非即時市場資料
      </div>

      <div className="disclaimer">
        <strong>提醒：</strong>本系統是研究工具，所有結果僅供研究參考，不代表未來績效，不構成投資建議。
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

      {status && (
        <div className="card" style={{ marginBottom: '24px' }}>
          <h3 style={{ marginBottom: '16px' }}>資料來源狀態</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
            <div>
              <div style={{ color: '#666', fontSize: '14px', marginBottom: '4px' }}>資料提供者</div>
              <div style={{ fontSize: '16px', fontWeight: 500 }}>{status.provider}</div>
            </div>
            <div>
              <div style={{ color: '#666', fontSize: '14px', marginBottom: '4px' }}>資料類型</div>
              <div
                style={{
                  display: 'inline-block',
                  padding: '4px 12px',
                  borderRadius: '12px',
                  fontSize: '14px',
                  backgroundColor: status.is_demo ? '#fff3cd' : '#d4edda',
                  color: status.is_demo ? '#856404' : '#155724',
                }}
              >
                {status.is_demo ? '示範資料' : '即時資料'}
              </div>
            </div>
            <div>
              <div style={{ color: '#666', fontSize: '14px', marginBottom: '4px' }}>最後更新</div>
              <div style={{ fontSize: '16px', fontWeight: 500 }}>{formatDate(status.last_updated)}</div>
            </div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h3 style={{ margin: 0 }}>選擇標的</h3>
          <select
            value={selectedSymbol}
            onChange={handleSymbolChange}
            style={{
              padding: '8px 12px',
              fontSize: '14px',
              border: '1px solid #ddd',
              borderRadius: '4px',
              backgroundColor: 'white',
              cursor: 'pointer',
            }}
          >
            {SYMBOLS.map((symbol) => (
              <option key={symbol} value={symbol}>
                {symbol}
              </option>
            ))}
          </select>
        </div>

        {loading ? (
          <div style={{ textAlign: 'center', padding: '40px' }}>載入中...</div>
        ) : !latestBar ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
            尚無資料
          </div>
        ) : (
          <>
            <div style={{ marginBottom: '32px' }}>
              <h4 style={{ marginBottom: '16px', color: '#333' }}>最新資料</h4>
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
                    {latestBar.volume.toLocaleString('zh-Hant')}
                  </div>
                </div>
              </div>
            </div>

            {bars.length > 0 && (
              <>
                <div style={{ marginBottom: '32px' }}>
                  <h4 style={{ marginBottom: '16px', color: '#333' }}>收盤價走勢圖（示範資料）</h4>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-end',
                      gap: '4px',
                      height: '200px',
                      padding: '16px',
                      backgroundColor: '#f8f9fa',
                      borderRadius: '6px',
                      border: '1px solid #e9ecef',
                    }}
                  >
                    {bars.map((bar, idx) => {
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
                    <span>最高: {formatNumber(maxClose)}</span>
                  </div>
                </div>

                <div>
                  <h4 style={{ marginBottom: '16px', color: '#333' }}>歷史資料表</h4>
                  <div style={{ overflowX: 'auto' }}>
                    <table
                      style={{
                        width: '100%',
                        borderCollapse: 'collapse',
                        fontSize: '14px',
                      }}
                    >
                      <thead>
                        <tr
                          style={{
                            backgroundColor: '#f8f9fa',
                            borderBottom: '2px solid #dee2e6',
                          }}
                        >
                          <th style={{ padding: '12px', textAlign: 'left', fontWeight: 600 }}>日期</th>
                          <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>開盤價</th>
                          <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>最高價</th>
                          <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>最低價</th>
                          <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>收盤價</th>
                          <th style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>成交量</th>
                        </tr>
                      </thead>
                      <tbody>
                        {bars.map((bar, idx) => (
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
                              {bar.volume.toLocaleString('zh-Hant')}
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
