'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import AppShell from '@/components/shell/AppShell';
import IntradayChart from '@/components/market/IntradayChart';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import {
  dataAsOfLabel,
  describeSession,
  describeStatus,
  integritySummary,
  isCurrentSeries,
  isPollingAllowed,
  nextPollDelayMs,
  seriesKey,
  type IntradayCapability,
  type IntradaySeriesPayload,
} from '@/lib/intradayBars';

const NATURE_LABELS: Record<string, string> = {
  synthetic: 'Synthetic',
  live: 'Live feed',
  delayed: 'Delayed',
  unknown: 'Unknown (unverified)',
};

const DATA_RIGHTS_LABELS: Record<string, string> = {
  INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION:
    'Internal test only — not cleared for redistribution',
};

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
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

export default function IntradayMarketPage() {
  const router = useRouter();
  const [capability, setCapability] = useState<IntradayCapability | null>(null);
  const [instrumentId, setInstrumentId] = useState('');
  const [interval, setInterval] = useState('');
  const [payload, setPayload] = useState<IntradaySeriesPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [paused, setPaused] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);

  const requestRef = useRef(0);
  const activeKeyRef = useRef('');
  const timerRef = useRef<number | null>(null);
  const capabilityRef = useRef<IntradayCapability | null>(null);
  const manualTickRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    capabilityRef.current = capability;
  }, [capability]);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    let cancelled = false;
    api.intraday
      .capability()
      .then((cap) => {
        if (cancelled) return;
        setCapability(cap);
        setCapabilityError(null);
        if (cap.allowed_instruments.length > 0) setInstrumentId(cap.allowed_instruments[0]);
        setInterval(cap.default_interval);
      })
      .catch((err) => {
        if (cancelled) return;
        setCapabilityError(errorMessage(err));
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
    const key = seriesKey(instrumentId, interval);
    activeKeyRef.current = key;
    // A new identity must never inherit the previous symbol's candles, even for
    // one frame while the first response is in flight.
    setPayload(null);
    setError(null);

    let cancelled = false;
    const requestId = ++requestRef.current;

    const run = async () => {
      if (cancelled) return;
      setLoading(true);
      try {
        const data = await api.intraday.series(instrumentId, interval);
        if (
          cancelled ||
          !isCurrentSeries(key, activeKeyRef.current, requestId, requestRef.current)
        ) {
          return;
        }
        setPayload(data);
        setError(null);
        if (!isPollingAllowed(data)) return;
        const delay = nextPollDelayMs({
          hidden,
          paused,
          refreshSeconds:
            data.refresh.refresh_seconds ??
            capabilityRef.current?.refresh_seconds ??
            60,
          status: data.status,
          retryAfterSeconds: data.refresh.retry_after_seconds,
        });
        if (delay !== null && !cancelled) {
          timerRef.current = window.setTimeout(run, delay);
        }
      } catch (err) {
        if (cancelled || requestRef.current !== requestId) return;
        setError(errorMessage(err));
        const delay = nextPollDelayMs({
          hidden,
          paused,
          refreshSeconds: capabilityRef.current?.refresh_seconds ?? 60,
          status: 'upstream_error',
          retryAfterSeconds: null,
        });
        if (delay !== null && !cancelled) {
          timerRef.current = window.setTimeout(run, delay);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    // Available even while paused/hidden so the explicit check button can still
    // issue one bounded read. The captured `paused`/`hidden` values keep that
    // read from re-arming the loop.
    manualTickRef.current = run;
    if (!hidden && !paused) run();
    return () => {
      cancelled = true;
      if (manualTickRef.current === run) manualTickRef.current = null;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [capability, instrumentId, interval, hidden, paused]);

  const recheckNow = useCallback(() => {
    manualTickRef.current?.();
  }, []);

  const statusView = payload
    ? describeStatus(payload.status)
    : describeStatus('idle');
  const actionTone = toneStyle(statusView.tone);
  const refresh = payload?.refresh;
  const integrity = payload?.series.integrity;
  const verified = capability?.verified_intervals ?? [];
  const declared = capability?.declared_intervals ?? [];
  const intervalOptions = declared.length > 0 ? declared : capability ? [capability.default_interval] : [];

  return (
    <AppShell active="market">
      <div className="container" style={{ paddingTop: 32, paddingBottom: 40 }}>
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>Intraday bars · internal test</h1>
          <div style={{ marginTop: 8, fontSize: 13, color: 'var(--text-secondary)' }}>
            The backend controls upstream requests; the browser reads stored data and status only.
            This is not a public market-data surface.
            {payload?.data_rights && (
              <>
                {' '}
                <span data-testid="intraday-data-rights">
                  {DATA_RIGHTS_LABELS[payload.data_rights] ?? payload.data_rights}.
                </span>{' '}
                <Link href="/legal" style={{ color: 'var(--text-secondary)' }}>
                  Data and execution details
                </Link>
              </>
            )}
          </div>
        </div>

        {capabilityError && (
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
            Couldn&apos;t read backend capability: {capabilityError}
          </div>
        )}

        {capability && (
          <section style={sectionStyle} aria-labelledby="lane-heading">
            <h2 id="lane-heading" style={{ margin: '0 0 16px', fontSize: 15, fontWeight: 600 }}>
              Lane status
            </h2>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: 16,
              }}
            >
              <Field label="Enabled">
                {capability.enabled ? 'Yes' : 'No (no upstream requests are sent)'}
              </Field>
              <Field label="Provider">
                {capability.provider_name ?? capability.provider_id}
                {capability.provider_connected ? ' · connected' : ' · not connected'}
              </Field>
              <Field label="Adapter intraday support">
                {capability.adapter_supports_intraday ? 'Yes' : 'No'}
              </Field>
              <Field label="Declared intervals">
                {declared.length > 0 ? declared.join(', ') : 'None'}
              </Field>
              <Field label="Verified intervals">
                {verified.length > 0 ? verified.join(', ') : 'None — unverified intervals are not requested'}
              </Field>
              <Field label="Minimum refresh">
                {capability.refresh_seconds}s on the backend (floor {capability.min_refresh_seconds}s)
              </Field>
              <Field label="Upstream request budget (shared via database)">
                {capability.budget.hour_used}/{capability.budget.hour_limit} per hour,{' '}
                {capability.budget.day_used}/{capability.budget.day_limit} per day
              </Field>
              <Field label="Real-time">
                No (unverified) · data: {NATURE_LABELS[payload?.data_nature ?? 'unknown']}
              </Field>
              <Field label="Holiday calendar">
                {capability.market_calendar_complete ? 'Configured' : 'Not configured (session may read as unknown)'}
              </Field>
              <Field label="Allowed instruments">
                {capability.allowed_instruments.join(', ') || 'None'}
              </Field>
            </div>
            {capability.provider_error && (
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
                Provider configuration: {capability.provider_error}
              </div>
            )}
            {declared.length > 0 && verified.length === 0 && (
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--warning-text)' }}>
                Intervals are declared but none has been verified against a real account. Until then the
                backend returns <code>unverified_interval</code> instead of treating adapter support as
                permission.
              </div>
            )}
          </section>
        )}

        <section style={sectionStyle} aria-labelledby="series-heading">
          <h2 id="series-heading" style={{ margin: '0 0 16px', fontSize: 15, fontWeight: 600 }}>
            Series
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
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
              Instrument
              <select
                aria-label="Intraday instrument"
                value={instrumentId}
                onChange={(e) => setInstrumentId(e.target.value)}
                style={{ minWidth: 200, padding: '7px 10px' }}
              >
                {(capability?.allowed_instruments ?? []).map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
              Interval
              <select
                aria-label="Intraday interval"
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
                style={{ minWidth: 140, padding: '7px 10px' }}
              >
                {intervalOptions.map((label) => (
                  <option key={label} value={label}>
                    {label}
                    {verified.includes(label) ? ' (verified)' : ' (unverified)'}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="ws-range-btn" onClick={recheckNow} data-testid="intraday-recheck">
              Check now
            </button>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => setPaused((v) => !v)}
              aria-pressed={paused}
              data-testid="intraday-pause-toggle"
            >
              {paused ? 'Resume updates' : 'Pause updates'}
            </button>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }} data-testid="intraday-poll-state">
              {hidden
                ? 'Tab hidden — polling paused'
                : paused
                  ? 'Paused by you — no next read scheduled'
                  : loading
                    ? 'Reading…'
                    : 'Polling'}
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
              Couldn&apos;t refresh: {error}. The last known data is kept; nothing is replaced with
              simulated prices.
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
          >
            <span
              data-testid="intraday-status"
              style={{
                border: '1px solid',
                borderRadius: 999,
                padding: '3px 10px',
                fontSize: 12,
                ...actionTone,
              }}
            >
              {statusView.label}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{statusView.detail}</span>
          </div>

          {payload && (
            <>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                  gap: 16,
                  padding: 16,
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 4,
                  backgroundColor: 'var(--surface-2)',
                  marginBottom: 16,
                }}
              >
                <Field label="Instrument">
                  {payload.instrument.instrument_id} ({payload.instrument.name})
                </Field>
                <Field label="Provider">{payload.provider_id}</Field>
                <Field label="Interval">
                  {payload.interval} ({payload.interval_minutes} min)
                </Field>
                <Field label="Data as of (latest completed bar)">
                  <span data-testid="intraday-data-as-of" className="num">
                    {dataAsOfLabel(payload)}
                  </span>
                </Field>
                <Field label="Last successful request">
                  <span className="num">
                    {refresh?.last_success_at ? formatDateTime(refresh.last_success_at) : '—'}
                  </span>
                </Field>
                <Field label="Last attempt">
                  <span className="num">
                    {refresh?.last_attempt_at ? formatDateTime(refresh.last_attempt_at) : '—'}
                  </span>
                </Field>
                <Field label="New completed bar this request">
                  {refresh?.last_success_added_bar ? 'Yes' : 'No'}
                </Field>
                <Field label="Upstream requests this read">{refresh?.upstream_requests ?? 0}</Field>
                <Field label="Exchange timezone">
                  {payload.market.timezone} · {describeSession(payload.market.session_status)}
                </Field>
                <Field label="Series integrity">
                  {integrity ? integritySummary(integrity) : '—'}
                </Field>
              </div>

              <div
                style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}
                data-testid="intraday-honesty-note"
              >
                The last successful request is not the same as fresh data. Bars are timestamped at their
                UTC open time; an HTTP 200 does not mean real-time data and does not grant the right to
                redistribute it. Unfinished bars are drawn in a lighter tone
                {payload.series.forming_excluded_from_strategy ? ' and excluded from strategy input' : ''}.
                {!payload.market.holiday_calendar_complete &&
                  ' No holiday calendar is configured, so session state is reported as unknown rather than assumed open.'}
              </div>

              <div style={{ height: 420, border: '1px solid var(--border-subtle)', borderRadius: 4 }}>
                <IntradayChart
                  payload={payload}
                  identityKey={seriesKey(payload.instrument.instrument_id, payload.interval)}
                />
              </div>

              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
                {payload.series.completed.length} completed bars
                {payload.series.forming ? ' + 1 forming' : ''}. Timestamp semantics:{' '}
                {payload.series.timestamp_semantics}.
              </div>
              {payload.provider_error && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--negative-text)' }}>
                  Provider error: {payload.provider_error}
                </div>
              )}
            </>
          )}

          {!payload && !loading && !error && capability && (
            <div
              style={{ fontSize: 13, color: 'var(--text-secondary)' }}
              data-testid="intraday-no-data"
            >
              No response yet. If the lane is disabled or unverified, the backend reports the reason
              instead of supplying simulated bars.
            </div>
          )}
        </section>
      </div>
    </AppShell>
  );
}
