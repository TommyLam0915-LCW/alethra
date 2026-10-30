'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
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
  synthetic: '模擬資料',
  live: '即時資料',
  delayed: '延遲資料',
  unknown: '未知（未驗證）',
};

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 14 }}>{children}</div>
    </div>
  );
}

function toneStyle(tone: 'ok' | 'warn' | 'bad') {
  if (tone === 'ok') return { color: '#36C7A0', borderColor: 'rgba(54,199,160,0.5)' };
  if (tone === 'bad') return { color: '#FF7585', borderColor: 'rgba(255,117,133,0.5)' };
  return { color: '#E8B65A', borderColor: 'rgba(232,182,90,0.5)' };
}

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

    // Available even while paused/hidden so the explicit "檢查一次" button can
    // still issue one bounded read. The captured `paused`/`hidden` values keep
    // that read from re-arming the loop.
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
      <div className="container" style={{ paddingTop: 40, paddingBottom: 40 }}>
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ margin: 0 }}>盤中行情（內部測試）</h1>
          <div className="ws-muted" style={{ marginTop: 8 }}>
            由後端控制上游請求；瀏覽器只讀取後端資料與狀態。此頁非公開行情展示。
          </div>
        </div>

        <div className="disclaimer" style={{ marginBottom: 20 }}>
          <strong>資料權利：</strong>
          {payload?.data_rights_note_zh ??
            '現階段只供已授權之本機／內部測試使用。擁有 API key 不等於可對外分發行情。'}
        </div>

        {capabilityError && (
          <div
            style={{
              border: '1px solid var(--negative)',
              backgroundColor: 'rgba(239,68,68,0.12)',
              borderRadius: 6,
              padding: 16,
              marginBottom: 20,
            }}
          >
            無法讀取後端能力資訊：{capabilityError}
          </div>
        )}

        {capability && (
          <div className="card" style={{ marginBottom: 20 }}>
            <h3 style={{ marginTop: 0 }}>通道能力</h3>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: 16,
              }}
            >
              <Field label="啟用">
                {capability.enabled ? '已啟用' : '未啟用'}
                {!capability.enabled && '（後端不會發出上游請求）'}
              </Field>
              <Field label="供應商">
                {capability.provider_name ?? capability.provider_id}
                {capability.provider_connected ? '｜已連線' : '｜未連線'}
              </Field>
              <Field label="adapter 盤中支援">
                {capability.adapter_supports_intraday ? '有' : '無'}
              </Field>
              <Field label="宣告週期">
                {declared.length > 0 ? declared.join('、') : '無'}
              </Field>
              <Field label="帳戶已驗證週期">
                {verified.length > 0 ? verified.join('、') : '無（未驗證即不請求真實資料）'}
              </Field>
              <Field label="最短更新間隔">
                後端 {capability.refresh_seconds} 秒（下限 {capability.min_refresh_seconds} 秒）
              </Field>
              <Field label="本機請求預算">
                {capability.budget.hour_used}/{capability.budget.hour_limit} 每小時、
                {capability.budget.day_used}/{capability.budget.day_limit} 每日（程序範圍）
              </Field>
              <Field label="即時性">
                非即時（未驗證）；資料性質：{NATURE_LABELS[payload?.data_nature ?? 'unknown']}
              </Field>
              <Field label="假日曆">
                {capability.market_calendar_complete ? '已配置' : '未配置（休市狀態可能顯示未知）'}
              </Field>
              <Field label="允許標的">{capability.allowed_instruments.join('、') || '無'}</Field>
            </div>
            {capability.provider_error && (
              <div style={{ marginTop: 12, fontSize: 13, color: 'var(--text-secondary)' }}>
                供應商設定：{capability.provider_error}
              </div>
            )}
            {declared.length > 0 && verified.length === 0 && (
              <div
                style={{
                  marginTop: 12,
                  padding: 12,
                  border: '1px solid rgba(232,182,90,0.5)',
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                adapter 宣告了週期，但尚未以實際帳戶驗證任何週期。未驗證前，後端只會回報
                <code>unverified_interval</code>，不會把 adapter 支援當成可用權限。
              </div>
            )}
          </div>
        )}

        <div className="card" style={{ marginBottom: 20 }}>
          <div
            style={{
              display: 'flex',
              gap: 16,
              flexWrap: 'wrap',
              alignItems: 'flex-end',
              marginBottom: 16,
            }}
          >
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
              標的
              <select
                aria-label="盤中標的"
                value={instrumentId}
                onChange={(e) => setInstrumentId(e.target.value)}
                style={{ minWidth: 200, padding: '8px 10px' }}
              >
                {(capability?.allowed_instruments ?? []).map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
              週期
              <select
                aria-label="盤中週期"
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
                style={{ minWidth: 140, padding: '8px 10px' }}
              >
                {intervalOptions.map((label) => (
                  <option key={label} value={label}>
                    {label}
                    {verified.includes(label) ? '（已驗證）' : '（未驗證）'}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="ws-range-btn" onClick={recheckNow} data-testid="intraday-recheck">
              立即檢查
            </button>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => setPaused((v) => !v)}
              aria-pressed={paused}
              data-testid="intraday-pause-toggle"
            >
              {paused ? '繼續更新' : '暫停更新'}
            </button>
            <span className="ws-muted" data-testid="intraday-poll-state">
              {hidden
                ? '分頁隱藏，已暫停輪詢'
                : paused
                  ? '使用者暫停，未安排下一次讀取'
                  : loading
                    ? '讀取中…'
                    : '輪詢中'}
            </span>
          </div>

          {error && (
            <div
              style={{
                border: '1px solid var(--negative)',
                backgroundColor: 'rgba(239,68,68,0.12)',
                borderRadius: 6,
                padding: 12,
                marginBottom: 16,
                fontSize: 13,
              }}
            >
              讀取失敗：{error}（保留上一次已知資料，不會以模擬價格替代）
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
                padding: '4px 12px',
                fontSize: 13,
                ...actionTone,
              }}
            >
              {statusView.labelZh}
            </span>
            <span className="ws-muted">{statusView.detailZh}</span>
          </div>

          {payload && (
            <>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                  gap: 16,
                  padding: 16,
                  border: '1px solid var(--border-strong)',
                  borderRadius: 6,
                  backgroundColor: 'var(--surface-2)',
                  marginBottom: 16,
                }}
              >
                <Field label="標的">{payload.instrument.instrument_id}（{payload.instrument.name}）</Field>
                <Field label="供應商">{payload.provider_id}</Field>
                <Field label="週期">{payload.interval}（{payload.interval_minutes} 分鐘）</Field>
                <Field label="資料截至（最新已收盤 K 線）">
                  <span data-testid="intraday-data-as-of">{dataAsOfLabel(payload)}</span>
                </Field>
                <Field label="最近一次成功請求">
                  {refresh?.last_success_at ? formatDateTime(refresh.last_success_at) : '—'}
                </Field>
                <Field label="最近一次請求">
                  {refresh?.last_attempt_at ? formatDateTime(refresh.last_attempt_at) : '—'}
                </Field>
                <Field label="本次是否新增已收盤 K 線">
                  {refresh?.last_success_added_bar ? '是' : '否'}
                </Field>
                <Field label="本次上游請求數">{refresh?.upstream_requests ?? 0}</Field>
                <Field label="交易所時區">
                  {payload.market.timezone}｜{describeSession(payload.market.session_status)}
                </Field>
                <Field label="序列完整性">
                  {integrity ? integritySummary(integrity) : '—'}
                </Field>
              </div>

              <div className="ws-muted" style={{ marginBottom: 12 }} data-testid="intraday-honesty-note">
                「最近一次成功請求」不是「資料是新鮮的」。K 線時間以 UTC 的開盤時間表示；
                HTTP 200 不代表即時行情，亦不代表已取得對外展示權利。
                {' '}未完成 K 線以較淡顏色顯示：
                {payload.series.forming_excluded_from_strategy ? '不納入策略輸入。' : ''}
                {!payload.market.holiday_calendar_complete &&
                  ' 假日曆未配置：交易日的休市一律顯示為未知，不強行判定開市。'}
              </div>

              <div style={{ height: 420, border: '1px solid var(--border-strong)', borderRadius: 6 }}>
                <IntradayChart
                  payload={payload}
                  identityKey={seriesKey(payload.instrument.instrument_id, payload.interval)}
                />
              </div>

              <div className="ws-muted" style={{ marginTop: 12 }}>
                顯示 {payload.series.completed.length} 根已收盤 K 線
                {payload.series.forming ? ' + 1 根未完成' : ''}。時間語意：{payload.series.timestamp_semantics}
              </div>
              {payload.provider_error && (
                <div className="ws-muted" style={{ marginTop: 8 }}>
                  供應商錯誤：{payload.provider_error}
                </div>
              )}
            </>
          )}

          {!payload && !loading && !error && capability && (
            <div className="ws-muted" data-testid="intraday-no-data">
              尚無回應。若通道未啟用或未驗證，後端會回報原因而不會提供模擬 K 線。
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
