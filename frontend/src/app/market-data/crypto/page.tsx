'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import AppShell from '@/components/shell/AppShell';
import CandleChart, { type CandleChartRow } from '@/components/market/CandleChart';
import CryptoPaperPanel from '@/components/market/CryptoPaperPanel';
import { ApiError, api } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import {
  candlesForCryptoChart,
  cryptoDataAsOfLabel,
  cryptoIntegritySummary,
  cryptoSeriesKey,
  describeConnection,
  describeMode,
  describeVenueRefusal,
  detectNewCompletedBar,
  isCurrentCryptoSeries,
  latestPrice,
  nextPollDelayMs,
  retentionSummary,
  type CryptoConnectionState,
  type CryptoOverview,
  type CryptoSeriesPayload,
} from '@/lib/cryptoBars';

const REDISTRIBUTION_LABELS: Record<string, string> = {
  NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION:
    'Internal research only — not cleared for external redistribution',
};

interface BarEvent {
  baseline: boolean;
  isNew: boolean;
  added: number;
  openTime: string | null;
  heartbeatOnly: boolean;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function refusalReason(err: unknown): string | null {
  if (err instanceof ApiError && err.detail && typeof err.detail === 'object') {
    const reason = (err.detail as { reason?: unknown }).reason;
    if (typeof reason === 'string') return reason;
  }
  return null;
}

/** Exchange timestamps are UTC; rendering them in the browser's zone would lie. */
function utcLabel(value: string | null | undefined): string {
  if (!value) return '—';
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/.exec(value);
  if (!match) return value;
  return `${match[1]} ${match[2]} UTC`;
}

function formatPrice(value: string | null, precision: number | undefined): string {
  if (value === null) return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  const digits = precision ?? 2;
  return numeric.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13 }}>{children}</div>
    </div>
  );
}

function toneStyle(tone: 'ok' | 'warn' | 'bad') {
  if (tone === 'ok')
    return { color: 'var(--positive-text)', borderColor: 'rgba(79,199,155,0.5)' };
  if (tone === 'bad')
    return { color: 'var(--negative-text)', borderColor: 'rgba(226,99,110,0.5)' };
  return { color: 'var(--warning-text)', borderColor: 'rgba(217,162,59,0.5)' };
}

const sectionStyle = {
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--surface-1)',
  borderRadius: '6px',
  padding: '20px',
  marginBottom: '20px',
} as const;

const selectStyle = { minWidth: 200, padding: '7px 10px' } as const;

export default function CryptoMarketPage() {
  const router = useRouter();
  const [overview, setOverview] = useState<CryptoOverview | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [instrumentId, setInstrumentId] = useState('');
  const [interval, setInterval] = useState('');
  const [payload, setPayload] = useState<CryptoSeriesPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [paused, setPaused] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [barEvent, setBarEvent] = useState<BarEvent | null>(null);
  const [budgetFrozen, setBudgetFrozen] = useState(false);

  const requestRef = useRef(0);
  const activeKeyRef = useRef('');
  const timerRef = useRef<number | null>(null);
  const manualTickRef = useRef<(() => void) | null>(null);
  const failureRef = useRef(0);
  const previousCompletedRef = useRef<string[] | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    let cancelled = false;
    api.crypto
      .overview()
      .then((data) => {
        if (cancelled) return;
        setOverview(data);
        setOverviewError(null);
        if (data.instruments.length > 0) setInstrumentId(data.instruments[0].instrument_id);
        setInterval(data.default_interval);
      })
      .catch((err) => {
        if (cancelled) return;
        setOverviewError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    const onVisibility = () => setHidden(document.hidden);
    onVisibility();
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, []);

  useEffect(() => {
    if (!instrumentId || !interval) return;
    const key = cryptoSeriesKey(instrumentId, interval);
    const identityChanged = key !== activeKeyRef.current;
    activeKeyRef.current = key;
    if (identityChanged) {
      // A new pair or interval must never inherit the previous candles, even
      // for one frame while the first response is in flight.
      setPayload(null);
      setError(null);
      setBarEvent(null);
      setBudgetFrozen(false);
      setLoading(false);
      previousCompletedRef.current = null;
      failureRef.current = 0;
    }

    let cancelled = false;
    const requestId = ++requestRef.current;
    const fallbackMinutes =
      overview?.intervals.find((option) => option.label === interval)?.minutes ?? 1;

    const run = async () => {
      if (cancelled) return;
      setLoading(true);
      try {
        const data = await api.crypto.series(instrumentId, interval, 500);
        if (
          cancelled ||
          !isCurrentCryptoSeries(key, activeKeyRef.current, requestId, requestRef.current)
        ) {
          return;
        }
        const baseline = previousCompletedRef.current === null;
        const result = detectNewCompletedBar(previousCompletedRef.current, data);
        previousCompletedRef.current = data.completed.map((candle) => candle.open_time);
        setPayload(data);
        setError(null);
        setBudgetFrozen(false);
        failureRef.current = 0;
        setBarEvent({
          baseline,
          isNew: result.isNew,
          added: result.added,
          openTime: result.open_time,
          heartbeatOnly: result.heartbeatOnly,
        });
        const delay = nextPollDelayMs({
          hidden,
          paused,
          suggestedSeconds: data.refresh.suggested_seconds,
          minimumSeconds: data.refresh.minimum_seconds,
          failureCount: 0,
        });
        if (delay !== null && !cancelled) {
          timerRef.current = window.setTimeout(run, delay);
        }
      } catch (err) {
        if (cancelled || requestRef.current !== requestId) return;
        failureRef.current += 1;
        const status = err instanceof ApiError ? err.status : null;
        const reason = refusalReason(err);
        setError(describeVenueRefusal(status, reason));
        // An exhausted local budget is not a transient venue failure: retrying
        // in a loop would only log more refusals. Freeze until the user asks
        // for one bounded read with "Check now".
        if (reason === 'budget_exhausted') {
          setBudgetFrozen(true);
          return;
        }
        const delay = nextPollDelayMs({
          hidden,
          paused,
          suggestedSeconds: Math.max(15, fallbackMinutes * 15),
          minimumSeconds: 15,
          failureCount: failureRef.current,
        });
        if (delay !== null && !cancelled) {
          timerRef.current = window.setTimeout(run, delay);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    // Kept available while paused or hidden so "Check now" can issue exactly
    // one bounded read. The captured flags keep that read from re-arming the
    // loop, so a hidden tab cannot poll.
    manualTickRef.current = run;
    if (!hidden && !paused) {
      run();
    } else {
      setLoading(false);
    }
    return () => {
      cancelled = true;
      if (manualTickRef.current === run) manualTickRef.current = null;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [instrumentId, interval, hidden, paused, overview]);

  const recheckNow = useCallback(() => {
    manualTickRef.current?.();
  }, []);

  const rows = useMemo<CandleChartRow[]>(() => {
    if (!payload) return [];
    return candlesForCryptoChart(payload).map((candle) => ({
      open_time: candle.open_time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume,
      is_final: candle.is_final,
    }));
  }, [payload]);

  const connectionState: CryptoConnectionState =
    hidden || paused
      ? 'paused'
      : budgetFrozen
        ? 'budget_limited'
        : error
          ? payload
            ? 'reconnecting'
            : 'unavailable'
          : loading && !payload
            ? 'connecting'
            : payload
              ? payload.mode === 'disconnected'
                ? 'unavailable'
                : payload.mode === 'stale' || payload.integrity.is_stale
                  ? 'stale'
                  : 'connected'
              : 'idle';
  const connectionView = describeConnection(connectionState);
  const connectionDetail = hidden
    ? 'Tab hidden. The timer is cleared and no read is scheduled until this tab is visible again.'
    : connectionState === 'reconnecting' && error
      ? `${connectionView.detail} ${error}`
      : connectionView.detail;
  const connectionTone = toneStyle(connectionView.tone);
  const modeView = payload ? describeMode(payload.mode) : null;

  const price = latestPrice(payload);
  const priceText = formatPrice(price.value, payload?.instrument.price_precision);
  const priceLabel =
    price.source === 'completed'
      ? `Last completed close · ${utcLabel(price.open_time)}`
      : price.source === 'forming'
        ? `Forming candle (not final) · opened ${utcLabel(price.open_time)}`
        : 'No completed or forming candles yet';

  const instrument = payload?.instrument;
  const redistribution = payload?.provenance.redistribution ?? overview?.redistribution;
  const termsNoteZh = payload?.provenance.terms_note_zh ?? overview?.terms_note_zh;
  const retention = payload?.retention ?? null;
  const integrity = payload?.integrity ?? null;
  const intervalOptions = overview?.intervals ?? [];
  const instruments = overview?.instruments ?? [];
  const pollState = hidden
    ? 'Tab hidden — polling paused'
    : paused
      ? 'Paused by you — no next read scheduled'
      : loading
        ? 'Reading…'
        : 'Polling with bounded backoff';

  return (
    <AppShell active="market">
      <div className="container" style={{ paddingTop: 32, paddingBottom: 40 }}>
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>Crypto · Kraken spot</h1>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
            Read-only public candles for one spot pair, refreshed by the backend. Updates are reads,
            not trades. The paper panel below can book a simulated fill against a completed candle;
            no order is sent to Kraken and no trading key is read.
          </p>
          <div
            style={{
              marginTop: 8,
              display: 'flex',
              gap: 16,
              flexWrap: 'wrap',
              fontSize: 13,
            }}
          >
            <Link href="/market-data" style={{ color: 'var(--text-secondary)' }}>
              All instruments
            </Link>
            <Link href="/market-data/intraday" style={{ color: 'var(--text-secondary)' }}>
              Stock intraday lane · internal test
            </Link>
            <Link href="/legal" style={{ color: 'var(--text-secondary)' }}>
              Data and execution details
            </Link>
          </div>
        </div>

        {overviewError && (
          <div
            role="alert"
            style={{
              border: '1px solid var(--negative)',
              backgroundColor: 'rgba(226,99,110,0.1)',
              borderRadius: 6,
              padding: 12,
              marginBottom: 20,
              fontSize: 13,
            }}
          >
            Couldn&apos;t read the crypto lane configuration: {overviewError}
          </div>
        )}

        {!overview && !overviewError && (
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 20 }}>
            Reading the declared pairs and intervals…
          </div>
        )}

        {overview && (
          <section style={sectionStyle} aria-labelledby="crypto-lane-heading">
            <h2 id="crypto-lane-heading" style={{ margin: '0 0 16px', fontSize: 15, fontWeight: 600 }}>
              Lane
            </h2>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: 16,
              }}
            >
              <Field label="Venue">
                {instrument?.exchange ?? overview.venues[0]?.exchange ?? 'Kraken'} · spot · public
                market data
              </Field>
              <Field label="Pair and quote asset">
                {instrument?.pair ?? overview.venues[0]?.pair ?? 'BTC/USD'} · quoted in{' '}
                {instrument?.quote_asset ?? overview.venues[0]?.quote_asset ?? 'USD'}
              </Field>
              <Field label="Feed">
                {payload?.provenance.feed_mode ?? 'public_market_data_only'} · read only
              </Field>
              <Field label="Orders">
                {overview.orders_supported
                  ? 'Venue orders supported'
                  : 'Simulated paper orders only — no venue order is sent'}
              </Field>
              <Field label="Data as of (UTC)">
                <span data-testid="crypto-data-as-of" className="num">
                  {payload
                    ? payload.data_as_of
                      ? utcLabel(payload.data_as_of)
                      : cryptoDataAsOfLabel(payload)
                    : '—'}
                </span>
              </Field>
              <Field label="Read at (your local time)">
                <span className="num">
                  {payload?.fetched_at ? formatDateTime(payload.fetched_at) : '—'}
                </span>
              </Field>
              <Field label="Retention">
                <span data-testid="crypto-retention">
                  {retention ? retentionSummary(retention) : overview.limits.note}
                </span>
              </Field>
              <Field label="Refresh cadence">
                {payload
                  ? `${payload.refresh.minimum_seconds}s minimum, ${payload.refresh.suggested_seconds}s suggested by the backend`
                  : 'Minimum 15s between upstream reads'}
              </Field>
              <Field label="Redistribution">
                <span data-testid="crypto-redistribution">
                  {redistribution
                    ? REDISTRIBUTION_LABELS[redistribution] ?? redistribution
                    : 'Not stated'}
                </span>
              </Field>
            </div>
            {payload?.retention.note && (
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
                {payload.retention.note}
              </div>
            )}
            {termsNoteZh && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                {termsNoteZh}
              </div>
            )}
          </section>
        )}

        <section style={sectionStyle} aria-labelledby="crypto-series-heading">
          <h2 id="crypto-series-heading" style={{ margin: '0 0 16px', fontSize: 15, fontWeight: 600 }}>
            Candles
          </h2>
          <div
            style={{
              display: 'flex',
              gap: 12,
              flexWrap: 'wrap',
              alignItems: 'flex-end',
              marginBottom: 16,
            }}
          >
            <label
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                fontSize: 12,
                color: 'var(--text-secondary)',
              }}
            >
              Pair
              <select
                aria-label="Crypto instrument"
                data-testid="crypto-instrument-select"
                value={instrumentId}
                onChange={(e) => setInstrumentId(e.target.value)}
                style={selectStyle}
              >
                {instruments.map((option) => (
                  <option key={option.instrument_id} value={option.instrument_id}>
                    {option.pair} · {option.exchange}
                  </option>
                ))}
              </select>
            </label>
            <label
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                fontSize: 12,
                color: 'var(--text-secondary)',
              }}
            >
              Interval
              <select
                aria-label="Crypto interval"
                data-testid="crypto-interval-select"
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
                style={{ minWidth: 140, padding: '7px 10px' }}
              >
                {intervalOptions.map((option) => (
                  <option key={option.label} value={option.label}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="ws-range-btn"
              onClick={recheckNow}
              data-testid="crypto-recheck"
            >
              Check now
            </button>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => setPaused((value) => !value)}
              aria-pressed={paused}
              data-testid="crypto-pause-toggle"
            >
              {paused ? 'Resume updates' : 'Pause updates'}
            </button>
            <span
              style={{ fontSize: 12, color: 'var(--text-secondary)' }}
              data-testid="crypto-poll-state"
            >
              {pollState}
            </span>
          </div>

          {error && (
            <div
              role="alert"
              style={{
                border: '1px solid var(--negative)',
                backgroundColor: 'rgba(226,99,110,0.1)',
                borderRadius: 6,
                padding: 12,
                marginBottom: 16,
                fontSize: 13,
              }}
            >
              Couldn&apos;t refresh: {error} The last candles received stay on screen and are
              labelled; no simulated prices replace them.
            </div>
          )}

          <div
            style={{
              display: 'flex',
              gap: 12,
              alignItems: 'center',
              flexWrap: 'wrap',
              marginBottom: 12,
            }}
            aria-live="polite"
          >
            <span
              data-testid="crypto-connection"
              style={{
                border: '1px solid',
                borderRadius: 999,
                padding: '3px 10px',
                fontSize: 12,
                ...connectionTone,
              }}
            >
              {connectionView.label}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{connectionDetail}</span>
            {modeView && (
              <span
                data-testid="crypto-mode"
                style={{
                  border: '1px solid',
                  borderRadius: 999,
                  padding: '3px 10px',
                  fontSize: 12,
                  ...toneStyle(modeView.tone),
                }}
              >
                {modeView.label}
              </span>
            )}
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: 16,
              padding: 16,
              border: '1px solid var(--border-subtle)',
              borderRadius: 4,
              backgroundColor: 'var(--surface-2)',
              marginBottom: 16,
            }}
          >
            <div data-testid="crypto-price">
              <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 4 }}>
                {price.source === 'completed'
                  ? 'Last completed close'
                  : price.source === 'forming'
                    ? 'Forming candle (not final)'
                    : 'Price'}
              </div>
              <div style={{ fontSize: 26, fontWeight: 600 }} className="num">
                {priceText}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                {priceLabel}
              </div>
            </div>
            <Field label="Instrument">
              {instrument ? `${instrument.pair} (${instrument.name})` : '—'}
            </Field>
            <Field label="Provider">
              {payload ? `${payload.provenance.provider_name} (${payload.provenance.provider_id})` : '—'}
            </Field>
            <Field label="Interval">
              {payload ? `${payload.interval ?? interval} · ${payload.interval_minutes} min` : '—'}
            </Field>
            <Field label="Completed bars">
              {payload
                ? `${payload.completed.length}${payload.forming ? ' + 1 forming (excluded)' : ''}`
                : '—'}
            </Field>
            <Field label="Series integrity">
              <span data-testid="crypto-integrity">
                {integrity ? cryptoIntegritySummary(integrity) : '—'}
              </span>
            </Field>
          </div>

          <div
            style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}
            data-testid="crypto-bar-event"
          >
            {!barEvent
              ? 'No read completed yet. A refresh is not a trade, and a repeated read is not a new bar.'
              : barEvent.baseline
                ? `Baseline loaded (${payload?.completed.length ?? 0} completed bars). Later reads are compared against this set.`
                : barEvent.isNew
                  ? `New completed bar at ${utcLabel(barEvent.openTime)}${
                      barEvent.added > 1 ? ` (+${barEvent.added} bars)` : ''
                    }. It appeared in a read after its close time; the read itself is not a trade.`
                  : barEvent.heartbeatOnly
                    ? 'Heartbeat: the forming candle changed. No new completed bar was appended, so this is not a new trade.'
                    : 'No change since the previous read. Identical candles are not appended twice.'}
          </div>

          {payload && (
            <>
              <div
                style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}
                data-testid="crypto-honesty-note"
              >
                Bars are timestamped at their UTC open time; the time axis is not converted to local
                time. The forming bar is drawn lighter and is excluded from strategy input until its
                close time passes. An HTTP 200, or a successful refresh, does not make the data
                real-time and does not grant the right to redistribute it. The venue serves only its
                most recent window, so history cannot be paged backwards with{' '}
                <code>since</code>; gaps older than that window stay listed, not filled.
              </div>
              <div
                style={{ height: 420, border: '1px solid var(--border-subtle)', borderRadius: 4 }}
              >
                <CandleChart
                  rows={rows}
                  identityKey={cryptoSeriesKey(instrumentId, interval)}
                  timeAxisNote={`Time axis: UTC (${instrument?.exchange ?? 'venue'} candles, not converted)`}
                  emptyTitle="No candles to show yet"
                  emptyDetail="The venue returned no completed or forming candles; the chart is never filled with simulated prices."
                  testIdPrefix="crypto"
                />
              </div>
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
                {payload.completed.length} completed bars
                {payload.forming ? ' + 1 forming' : ''}. Timestamp semantics:{' '}
                {payload.timestamp_semantics}
              </div>
              {integrity && (integrity.gaps.length > 0 || !integrity.integrity_ok) && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--warning-text)' }}>
                  Integrity is reported, not repaired: {cryptoIntegritySummary(integrity)}.
                  {integrity.gaps.length > 0
                    ? ` Gaps: ${integrity.gaps
                        .map((gap) => `${utcLabel(gap.from)} → ${utcLabel(gap.to)}`)
                        .join(', ')}.`
                    : ''}
                </div>
              )}
            </>
          )}

          {!payload && !loading && !error && overview && (
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }} data-testid="crypto-no-data">
              No candles yet. If the venue cannot be reached the backend reports the failure and
              returns nothing; it never substitutes demo bars.
            </div>
          )}
        </section>

        {instrumentId && (
          <CryptoPaperPanel
            instrumentId={instrumentId}
            instrumentLabel={instrument?.pair ?? 'BTC/USD'}
            pricePrecision={payload?.instrument.price_precision}
            intervalLabel={interval}
            intervals={intervalOptions}
          />
        )}
      </div>
    </AppShell>
  );
}
