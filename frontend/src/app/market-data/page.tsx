'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
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
  synthetic: 'Synthetic',
  live: 'Live feed',
  delayed: 'Delayed',
  unknown: 'Unknown',
};

const ADJUSTMENT_LABELS: Record<string, string> = {
  not_applicable: 'Not applicable (sample data)',
  unadjusted: 'Unadjusted',
  adjusted: 'Adjusted (dividends, splits)',
  unknown: 'Unknown, not confirmed',
};

const HEALTH_LABELS: Record<string, string> = {
  ok: 'Operational',
  degraded: 'Degraded (consecutive errors)',
  rate_limited: 'Rate limited',
  disconnected: 'Disconnected',
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
      ? 'rgba(79,199,155,0.14)'
      : 'rgba(140,226,193,0.14)'
    : 'var(--surface-2)',
  color: selected ? (tone === 'positive' ? 'var(--positive-text)' : 'var(--primary-text)') : 'var(--text-primary)',
  cursor: 'pointer',
  fontSize: '13px',
});

const sectionStyle = {
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--surface-1)',
  borderRadius: '6px',
  padding: '20px',
  marginBottom: '20px',
} as const;

const labelStyle = { color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' } as const;
const valueStyle = { fontSize: '14px', fontWeight: 500 } as const;

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
      setError("Couldn't load the instrument list. Try again.");
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
      setError("Couldn't load market data. Your selection is saved. Try again.");
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

  const formatNumber = (num: number) =>
    num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const formatVolume = (num: number | null) => (num !== null ? num.toLocaleString('en-US') : '—');

  const maxClose = bars.length > 0 ? Math.max(...bars.map((b) => b.close)) : 0;
  const minClose = bars.length > 0 ? Math.min(...bars.map((b) => b.close)) : 0;
  const closeRange = maxClose - minClose || 1;

  const asOf = dataQuality?.latest_bar_date ? formatDate(dataQuality.latest_bar_date) : null;

  return (
    <AppShell active="market">
      <div className="container" style={{ paddingTop: '32px', paddingBottom: '40px' }}>
        <div
          style={{
            marginBottom: '24px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <div>
            <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 600 }}>Markets</h1>
            <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
              Daily bars for listed instruments. Source, adjustment and freshness are shown with the data.
            </p>
          </div>
          <Link href="/market-data/intraday" style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            Intraday bars · internal test
          </Link>
        </div>

        {error && (
          <div
            role="alert"
            style={{
              backgroundColor: 'rgba(226,99,110,0.1)',
              border: '1px solid var(--negative)',
              borderRadius: '6px',
              padding: '12px 16px',
              marginBottom: '20px',
              fontSize: '14px',
              color: 'var(--text-primary)',
            }}
          >
            {error}
          </div>
        )}

        <section style={sectionStyle} aria-labelledby="data-source-heading">
          <h2 id="data-source-heading" style={{ margin: '0 0 16px', fontSize: '15px', fontWeight: 600 }}>
            Data source
          </h2>
          {providerStatus ? (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px' }}>
                <div>
                  <div style={labelStyle}>Provider</div>
                  <div style={valueStyle}>{providerStatus.provider_name}</div>
                </div>
                <div>
                  <div style={labelStyle}>Feed</div>
                  <div style={valueStyle}>
                    {providerStatus.feed_mode === 'offline' ? 'Offline (simulated)' : providerStatus.feed_mode}
                  </div>
                </div>
                <div>
                  <div style={labelStyle}>Instruments</div>
                  <div style={valueStyle}>{instruments.length} listed · 3 demo</div>
                </div>
                <div>
                  <div style={labelStyle}>As of</div>
                  <div style={valueStyle} className="num">
                    {asOf ?? '—'}
                  </div>
                </div>
              </div>
              {typeof providerStatus.status?.health === 'string' && providerStatus.status.health !== 'ok' && (
                <div
                  role="status"
                  style={{
                    marginTop: '16px',
                    padding: '12px',
                    backgroundColor: 'rgba(217,162,59,0.1)',
                    border: '1px solid var(--warning)',
                    borderRadius: '6px',
                    fontSize: '13px',
                    color: 'var(--text-primary)',
                  }}
                >
                  <strong>Provider status: </strong>
                  {HEALTH_LABELS[providerStatus.status.health] || providerStatus.status.health}
                  {typeof providerStatus.status?.rate_limited_retry_after_seconds === 'number' &&
                    providerStatus.status.rate_limited_retry_after_seconds > 0 &&
                    ` · retry in ${providerStatus.status.rate_limited_retry_after_seconds}s`}
                  {typeof providerStatus.status?.last_error === 'string' && (
                    <div style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>
                      Last error: {providerStatus.status.last_error}
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Source details unavailable.</div>
          )}
        </section>

        <section style={sectionStyle} aria-labelledby="market-data-heading">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: '16px',
              flexWrap: 'wrap',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '16px', flexWrap: 'wrap' }}>
              <h2 id="market-data-heading" style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>
                {sourceTab === 'real' ? 'Listed instruments' : 'Demo instruments'}
              </h2>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button onClick={() => setSourceTab('real')} style={controlStyle(sourceTab === 'real')}>
                  Listed
                </button>
                <button onClick={() => setSourceTab('demo')} style={controlStyle(sourceTab === 'demo')}>
                  Demo
                </button>
              </div>
            </div>

            {sourceTab === 'real' && (
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                {(['ALL', 'US', 'HK'] as MarketFilter[]).map((m) => (
                  <button
                    key={m}
                    onClick={() => setMarketFilter(m)}
                    style={{ ...controlStyle(marketFilter === m, 'positive'), padding: '4px 12px' }}
                  >
                    {m === 'ALL' ? 'All' : m === 'US' ? 'US' : 'HK'}
                  </button>
                ))}
              </div>
            )}
          </div>

          <select
            aria-label="Choose instrument"
            value={selectedSymbol}
            onChange={handleSymbolChange}
            style={{
              width: '100%',
              padding: '9px 12px',
              fontSize: '14px',
              border: '1px solid var(--border-strong)',
              borderRadius: '4px',
              backgroundColor: 'var(--surface-2)',
              color: 'var(--text-primary)',
              marginBottom: '12px',
            }}
          >
            {sourceTab === 'real'
              ? filteredInstruments.map((inst) => (
                  <option key={inst.instrument_id} value={inst.instrument_id}>
                    {inst.instrument_id} — {inst.name} ({inst.market} · {inst.currency})
                  </option>
                ))
              : DEMO_SYMBOLS.map((symbol) => (
                  <option key={symbol} value={symbol}>
                    {symbol} (synthetic)
                  </option>
                ))}
          </select>

          {sourceTab === 'real' && selectedInstrument && (
            <div
              style={{
                padding: '12px 16px',
                backgroundColor: 'var(--surface-2)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '4px',
                fontSize: '13px',
                marginBottom: '20px',
              }}
            >
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '8px' }}>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Exchange </span>
                  {selectedInstrument.exchange}
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Asset type </span>
                  {selectedInstrument.asset_type}
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Currency </span>
                  {selectedInstrument.currency}
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Timezone </span>
                  {selectedInstrument.timezone}
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Lot size </span>
                  {selectedInstrument.lot_size !== null
                    ? `${selectedInstrument.lot_size.toLocaleString('en-US')}${
                        selectedInstrument.lot_size_verified ? '' : ' (unverified)'
                      }`
                    : 'Unknown'}
                </div>
                {dataQuality && (
                  <>
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>Data </span>
                      {DATA_NATURE_LABELS[dataQuality.data_nature] || dataQuality.data_nature}
                    </div>
                    <div>
                      <span style={{ color: 'var(--text-secondary)' }}>Adjustment </span>
                      {ADJUSTMENT_LABELS[dataQuality.adjustment] || dataQuality.adjustment}
                    </div>
                    {asOf && (
                      <div>
                        <span style={{ color: 'var(--text-secondary)' }}>As of </span>
                        <span className="num">{asOf}</span>
                        {dataQuality.bar_age_days !== null && ` · ${dataQuality.bar_age_days}d old`}
                      </div>
                    )}
                  </>
                )}
              </div>
              {(dataQuality?.note || dataQuality?.note_zh) && (
                <div style={{ marginTop: '8px', color: 'var(--text-secondary)' }}>
                  {dataQuality?.note || dataQuality?.note_zh}
                </div>
              )}
            </div>
          )}

          {dataLoading ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)', fontSize: '14px' }}>
              Loading…
            </div>
          ) : !latestBar ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)', fontSize: '14px' }}>
              No bars available for this instrument.
            </div>
          ) : (
            <>
              <div style={{ marginBottom: '24px' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'baseline',
                    gap: '12px',
                    flexWrap: 'wrap',
                    marginBottom: '12px',
                  }}
                >
                  <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>
                    Latest close — {selectedSymbol}
                    {selectedInstrument && (
                      <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>
                        {' '}
                        · {selectedInstrument.name}
                      </span>
                    )}
                  </h3>
                  {dataQuality?.is_stale && (
                    <span className="status-pill status-warn" style={{ fontSize: '12px' }}>
                      Stale
                      {dataQuality.bar_age_days !== null && ` · no update for ${dataQuality.bar_age_days}d`}
                    </span>
                  )}
                </div>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
                    gap: '12px',
                    padding: '16px',
                    backgroundColor: 'var(--surface-2)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '4px',
                  }}
                >
                  {[
                    { label: 'Date', value: formatDate(latestBar.date) },
                    { label: 'Open', value: formatNumber(latestBar.open) },
                    { label: 'High', value: formatNumber(latestBar.high), tone: 'positive' },
                    { label: 'Low', value: formatNumber(latestBar.low), tone: 'negative' },
                    { label: 'Close', value: formatNumber(latestBar.close) },
                    { label: 'Volume', value: formatVolume(latestBar.volume) },
                  ].map((item) => (
                    <div key={item.label}>
                      <div style={labelStyle}>{item.label}</div>
                      <div
                        className="num"
                        style={{
                          ...valueStyle,
                          color:
                            item.tone === 'positive'
                              ? 'var(--positive-text)'
                              : item.tone === 'negative'
                                ? 'var(--negative-text)'
                                : undefined,
                        }}
                      >
                        {item.value}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {bars.length > 0 && (
                <>
                  <div style={{ marginBottom: '24px' }}>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'baseline',
                        gap: '12px',
                        marginBottom: '12px',
                      }}
                    >
                      <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>Close · last 60 sessions</h3>
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {providerStatus ? providerStatus.provider_name : 'Source unavailable'}
                        {asOf && ` · as of ${asOf}`}
                      </span>
                    </div>
                    <div
                      role="img"
                      aria-label={`Close price for the last ${Math.min(bars.length, 60)} sessions`}
                      style={{
                        display: 'flex',
                        alignItems: 'flex-end',
                        gap: '2px',
                        height: '180px',
                        padding: '12px',
                        backgroundColor: 'var(--surface-2)',
                        borderRadius: '4px',
                        border: '1px solid var(--border-subtle)',
                        overflow: 'hidden',
                      }}
                    >
                      {bars.slice(-60).map((bar, idx) => {
                        const heightPercent = ((bar.close - minClose) / closeRange) * 100;
                        const height = Math.max(heightPercent, 4);
                        return (
                          <div
                            key={idx}
                            title={`${formatDate(bar.date)}: ${formatNumber(bar.close)}`}
                            style={{
                              flex: 1,
                              height: `${height}%`,
                              backgroundColor: 'var(--primary)',
                              borderRadius: '2px 2px 0 0',
                              transition: 'background-color 0.2s',
                              minWidth: '3px',
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
                      <span className="num">Low {formatNumber(minClose)}</span>
                      <span>Last {Math.min(bars.length, 60)} sessions</span>
                      <span className="num">High {formatNumber(maxClose)}</span>
                    </div>
                  </div>

                  <div>
                    <h3 style={{ margin: '0 0 12px', fontSize: '14px', fontWeight: 600 }}>
                      Daily bars · last 30 sessions
                    </h3>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                        <thead>
                          <tr
                            style={{
                              borderBottom: '1px solid var(--border-strong)',
                              color: 'var(--text-secondary)',
                            }}
                          >
                            <th style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 500 }}>Date</th>
                            <th style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500 }}>Open</th>
                            <th style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500 }}>High</th>
                            <th style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500 }}>Low</th>
                            <th style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500 }}>Close</th>
                            <th style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500 }}>Volume</th>
                          </tr>
                        </thead>
                        <tbody>
                          {bars
                            .slice(-30)
                            .reverse()
                            .map((bar, idx) => (
                              <tr
                                key={idx}
                                style={{
                                  borderBottom: '1px solid var(--border-subtle)',
                                  backgroundColor: idx % 2 === 0 ? 'transparent' : 'var(--surface-2)',
                                }}
                              >
                                <td style={{ padding: '10px 12px' }} className="num">
                                  {formatDate(bar.date)}
                                </td>
                                <td style={{ padding: '10px 12px', textAlign: 'right' }} className="num">
                                  {formatNumber(bar.open)}
                                </td>
                                <td
                                  style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--positive-text)' }}
                                  className="num"
                                >
                                  {formatNumber(bar.high)}
                                </td>
                                <td
                                  style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--negative-text)' }}
                                  className="num"
                                >
                                  {formatNumber(bar.low)}
                                </td>
                                <td
                                  style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500 }}
                                  className="num"
                                >
                                  {formatNumber(bar.close)}
                                </td>
                                <td style={{ padding: '10px 12px', textAlign: 'right' }} className="num">
                                  {formatVolume(bar.volume)}
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
        </section>

        <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
          Covered by the environment indicator. <Link href="/legal" style={{ color: 'var(--text-secondary)' }}>Data, execution and legal details</Link>
        </p>
      </div>
    </AppShell>
  );
}
