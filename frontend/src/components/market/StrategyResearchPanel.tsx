'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, api, formatErrorDetail } from '@/lib/api';
import type { ChartMarker } from '@/components/market/CandleChart';

interface Props {
  instrumentId: string;
  instrumentLabel: string;
  /** Minutes per candle on the chart above; markers only fit when they match. */
  chartIntervalMinutes: number;
  chartIntervalLabel: string;
  onMarkersChange: (markers: ChartMarker[]) => void;
}

interface Refusal {
  code: string;
  message: string;
}

interface StudyTrade {
  trade_index: number;
  fold: number | null;
  direction: string;
  rule_label_zh: string | null;
  exit_label_zh: string | null;
  exit_reason: string;
  outcome: string;
  entry_time: string;
  exit_time: string;
  entry_price: string;
  exit_price: string;
  quantity: string;
  net_pnl: string;
  total_cost: string;
  cash_after: string;
  bars_held: number;
  mfe_pct: string | null;
  mae_pct: string | null;
  cost_ratio_pct: string | null;
  pnl_reason_en: string;
  pnl_reason_zh: string;
}

interface StudySummary {
  id: string;
  created_at: string;
  interval_minutes: number;
  bar_count: number;
  trade_count: number;
  first_open_time: string;
  last_open_time: string;
  dataset_fingerprint: string | null;
  dataset_integrity_ok: boolean | null;
  dataset_gap_count: number | null;
  data_nature: string;
  walk_forward: Record<string, any>;
  settings: Record<string, any>;
  highlights: Record<string, any> | null;
}

interface StudyDetail extends StudySummary {
  disclosures: string[];
  trades: StudyTrade[];
}

const INTERVAL_OPTIONS = [
  { minutes: 1440, label: 'Daily (1440m)' },
  { minutes: 240, label: '4 hours (240m)' },
  { minutes: 60, label: '1 hour (60m)' },
];

const BUY_COLOR = '#36C7A0';
const SELL_COLOR = '#FF7585';

function refusalFrom(err: unknown): Refusal {
  if (err instanceof ApiError && err.detail && typeof err.detail === 'object') {
    const detail = err.detail as { reason?: unknown; code?: unknown; message?: unknown };
    const code =
      typeof detail.reason === 'string'
        ? detail.reason
        : typeof detail.code === 'string'
          ? detail.code
          : 'request_refused';
    return {
      code,
      message:
        typeof detail.message === 'string' ? detail.message : formatErrorDetail(err.detail),
    };
  }
  return { code: 'request_failed', message: err instanceof Error ? err.message : String(err) };
}

function utcLabel(value: string | null | undefined): string {
  if (!value) return '—';
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/.exec(value);
  if (!match) return value;
  return `${match[1]} ${match[2]} UTC`;
}

function intervalLabel(minutes: number): string {
  return INTERVAL_OPTIONS.find((option) => option.minutes === minutes)?.label ?? `${minutes}m`;
}

function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return number.toFixed(2);
}

function pct(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return `${number.toFixed(2)}%`;
}

/** Every simulated buy and sell, drawn at the bar its fill belongs to. */
function buildMarkers(detail: StudyDetail): ChartMarker[] {
  const markers: ChartMarker[] = [];
  for (const trade of detail.trades) {
    const entryBuy = trade.direction === 'long';
    markers.push({
      time: trade.entry_time,
      position: entryBuy ? 'belowBar' : 'aboveBar',
      color: entryBuy ? BUY_COLOR : SELL_COLOR,
      shape: entryBuy ? 'arrowUp' : 'arrowDown',
      text: entryBuy ? 'BUY' : 'SELL',
    });
    markers.push({
      time: trade.exit_time,
      position: entryBuy ? 'aboveBar' : 'belowBar',
      color: entryBuy ? SELL_COLOR : BUY_COLOR,
      shape: entryBuy ? 'arrowDown' : 'arrowUp',
      text: entryBuy ? 'SELL' : 'BUY',
    });
  }
  return markers;
}

const inputStyle = {
  width: '100%',
  padding: '7px 10px',
  border: '1px solid var(--border-subtle)',
  borderRadius: 4,
  backgroundColor: 'var(--surface-2)',
  color: 'var(--text-primary)',
  fontSize: 13,
} as const;

const labelStyle = {
  display: 'flex',
  flexDirection: 'column',
  gap: 6,
  fontSize: 12,
  color: 'var(--text-secondary)',
} as const;

const cellStyle = {
  padding: '7px 10px',
  borderBottom: '1px solid var(--border-subtle)',
  fontSize: 12,
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
  verticalAlign: 'top',
} as const;

const headCellStyle = {
  ...cellStyle,
  color: 'var(--text-secondary)',
  fontWeight: 500,
} as const;

export default function StrategyResearchPanel({
  instrumentId,
  instrumentLabel,
  chartIntervalMinutes,
  chartIntervalLabel,
  onMarkersChange,
}: Props) {
  const [intervalMinutes, setIntervalMinutes] = useState(1440);
  const [folds, setFolds] = useState('4');
  const [startingCash, setStartingCash] = useState('10000');
  const [notional, setNotional] = useState('1000');
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [records, setRecords] = useState<StudySummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<StudyDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [markersOn, setMarkersOn] = useState(true);

  // A stored study belongs to one instrument, so switching the chart instrument
  // must drop the selection before the records for the new instrument load.
  useEffect(() => {
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
  }, [instrumentId]);

  const loadRecords = useCallback(async () => {
    const data = (await api.strategyLab.researchRuns({
      instrument_id: instrumentId,
      limit: 50,
    })) as { records?: StudySummary[] };
    const list = Array.isArray(data.records) ? data.records : [];
    setRecords(list);
    return list;
  }, [instrumentId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await loadRecords();
        if (cancelled) return;
        // Prefer a stored study that matches the chart the customer is looking
        // at; otherwise show the newest one and state the interval mismatch.
        const preferred =
          list.find((record) => record.interval_minutes === chartIntervalMinutes) ?? list[0];
        setSelectedId((current) => current ?? preferred?.id ?? null);
      } catch (err) {
        if (!cancelled) setDetailError(refusalFrom(err).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [instrumentId, chartIntervalMinutes, loadRecords]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = (await api.strategyLab.researchRunDetail(selectedId)) as StudyDetail;
        if (!cancelled) {
          setDetail(data);
          setDetailError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setDetail(null);
          setDetailError(refusalFrom(err).message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const markersVisible =
    !!detail && markersOn && detail.interval_minutes === chartIntervalMinutes;

  useEffect(() => {
    onMarkersChange(markersVisible && detail ? buildMarkers(detail) : []);
    return () => onMarkersChange([]);
  }, [detail, markersVisible, onMarkersChange]);

  const run = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const data = (await api.strategyLab.researchRun({
        instrument_id: instrumentId,
        interval_minutes: intervalMinutes,
        folds: Number(folds),
        starting_cash: startingCash,
        notional_per_trade: notional,
      })) as StudyDetail;
      setDetail(data);
      setSelectedId(data.id);
      setDetailError(null);
      await loadRecords();
    } catch (err) {
      setRefusal(refusalFrom(err));
    } finally {
      setBusy(false);
    }
  }, [instrumentId, intervalMinutes, folds, startingCash, notional, loadRecords]);

  const downloadCsv = useCallback(async () => {
    if (!selectedId) return;
    try {
      const text = await api.strategyLab.researchRunTradesCsv(selectedId);
      const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `strategy-study-${selectedId}-trades.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setRefusal(refusalFrom(err));
    }
  }, [selectedId]);

  const oos = (detail?.walk_forward?.out_of_sample ?? null) as Record<string, any> | null;

  const markerNote = useMemo(() => {
    if (!detail) return 'No stored study selected, so the chart shows no buy/sell markers.';
    if (!markersOn) return 'Markers hidden by you. The stored trades are still listed below.';
    if (detail.interval_minutes !== chartIntervalMinutes) {
      return `This study used ${intervalLabel(detail.interval_minutes)} bars, but the chart shows ${chartIntervalLabel}. Markers stay off rather than pinning trades to different candles.`;
    }
    return `Buy and sell markers are drawn on the chart from this study's ${detail.trades.length} out-of-sample trades.`;
  }, [detail, markersOn, chartIntervalMinutes, chartIntervalLabel]);

  return (
    <section
      aria-labelledby="strategy-research-heading"
      data-testid="strategy-research-panel"
      style={{
        border: '1px solid var(--border-subtle)',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 6,
        padding: 20,
        marginBottom: 20,
      }}
    >
      <h2
        id="strategy-research-heading"
        style={{ margin: '0 0 8px', fontSize: 15, fontWeight: 600 }}
      >
        Strategy research record · {instrumentLabel}
      </h2>
      <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--text-secondary)' }}>
        Runs one cost-aware walk-forward study on completed venue bars and stores it with every
        out-of-sample buy and sell: entry and exit time, prices, costs, exit rule and the mechanical
        reason for the gain or loss. The chart above draws those fills as markers. This is simulated
        research — no order is sent to the venue and no real money moves.
      </p>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 12,
          marginBottom: 16,
        }}
      >
        <label style={labelStyle}>
          Bar interval
          <select
            aria-label="Research bar interval"
            data-testid="strategy-research-interval"
            value={intervalMinutes}
            onChange={(event) => setIntervalMinutes(Number(event.target.value))}
            style={inputStyle}
          >
            {INTERVAL_OPTIONS.map((option) => (
              <option key={option.minutes} value={option.minutes}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label style={labelStyle}>
          Walk-forward folds
          <input
            aria-label="Walk-forward folds"
            data-testid="strategy-research-folds"
            value={folds}
            inputMode="numeric"
            onChange={(event) => setFolds(event.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={labelStyle}>
          Starting cash (USD)
          <input
            aria-label="Research starting cash"
            data-testid="strategy-research-cash"
            value={startingCash}
            inputMode="decimal"
            onChange={(event) => setStartingCash(event.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={labelStyle}>
          Notional per trade (USD)
          <input
            aria-label="Research notional per trade"
            data-testid="strategy-research-notional"
            value={notional}
            inputMode="decimal"
            onChange={(event) => setNotional(event.target.value)}
            style={inputStyle}
          />
        </label>
      </div>

      <div
        style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}
      >
        <button
          type="button"
          className="ws-range-btn"
          onClick={() => void run()}
          disabled={busy}
          data-testid="strategy-research-run"
        >
          {busy ? 'Reading the venue and recording…' : 'Run and save study'}
        </button>
        <label
          style={{
            display: 'flex',
            gap: 6,
            alignItems: 'center',
            fontSize: 12,
            color: 'var(--text-secondary)',
          }}
        >
          <input
            type="checkbox"
            checked={markersOn}
            onChange={(event) => setMarkersOn(event.target.checked)}
            data-testid="strategy-research-markers-toggle"
          />
          Show buy/sell markers on the chart
        </label>
        <span
          data-testid="strategy-research-marker-note"
          style={{ fontSize: 12, color: 'var(--text-secondary)' }}
        >
          {markerNote}
        </span>
      </div>

      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 16 }}>
        Each run is one bounded venue read; the shared upstream budget and the research rate limit
        still apply. Existing studies are never rewritten, so an old run keeps the exact bars it
        read.
      </div>

      {refusal && (
        <div
          role="alert"
          data-testid="strategy-research-error"
          style={{
            border: '1px solid var(--negative)',
            backgroundColor: 'rgba(226,99,110,0.1)',
            borderRadius: 6,
            padding: 12,
            marginBottom: 16,
            fontSize: 13,
          }}
        >
          <strong>Refused ({refusal.code}):</strong> {refusal.message}
        </div>
      )}

      {detailError && (
        <div
          role="alert"
          style={{
            border: '1px solid var(--negative)',
            borderRadius: 6,
            padding: 12,
            marginBottom: 16,
            fontSize: 13,
          }}
        >
          Couldn&apos;t read the stored study: {detailError}
        </div>
      )}

      <div
        data-testid="strategy-research-records"
        style={{
          border: '1px solid var(--border-subtle)',
          borderRadius: 4,
          padding: 12,
          marginBottom: 16,
        }}
      >
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
          Stored studies for this instrument ({records.length})
        </div>
        {records.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            Nothing stored yet. A refused study writes no record.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {records.map((record) => (
              <button
                key={record.id}
                type="button"
                onClick={() => setSelectedId(record.id)}
                data-testid={`strategy-research-record-${record.id}`}
                style={{
                  textAlign: 'left',
                  padding: '8px 10px',
                  borderRadius: 4,
                  border:
                    record.id === selectedId
                      ? '1px solid var(--accent, #4F8DF7)'
                      : '1px solid var(--border-subtle)',
                  backgroundColor: 'var(--surface-2)',
                  color: 'var(--text-primary)',
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                <span style={{ fontWeight: 600 }}>{utcLabel(record.created_at)}</span>
                {' · '}
                {intervalLabel(record.interval_minutes)} · {record.bar_count} bars ·{' '}
                {record.trade_count} out-of-sample trades · {record.data_nature}
              </button>
            ))}
          </div>
        )}
      </div>

      {detail && (
        <div data-testid="strategy-research-detail">
          <div
            style={{
              display: 'flex',
              gap: 12,
              flexWrap: 'wrap',
              alignItems: 'center',
              marginBottom: 8,
            }}
          >
            <strong style={{ fontSize: 13 }}>Out-of-sample result</strong>
            <span
              style={{
                fontSize: 12,
                border: '1px solid var(--border-subtle)',
                borderRadius: 999,
                padding: '2px 10px',
                color: 'var(--warning-text)',
              }}
            >
              {detail.data_nature} bars · simulated fills
            </span>
            <button
              type="button"
              className="ws-range-btn"
              onClick={() => void downloadCsv()}
              data-testid="strategy-research-csv"
            >
              Download trades (CSV)
            </button>
          </div>

          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
            {intervalLabel(detail.interval_minutes)} · {detail.bar_count} completed bars ·{' '}
            {utcLabel(detail.first_open_time)} → {utcLabel(detail.last_open_time)}
            {detail.dataset_integrity_ok === false
              ? ` · ${detail.dataset_gap_count ?? 0} gap(s) reported`
              : ' · integrity ok'}
          </div>

          {oos && (
            <div
              data-testid="strategy-research-oos"
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
                gap: 12,
                marginBottom: 16,
                fontSize: 12,
              }}
            >
              <div>
                <div style={{ color: 'var(--text-secondary)' }}>Trades</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{oos.trades}</div>
              </div>
              <div>
                <div style={{ color: 'var(--text-secondary)' }}>Net P&amp;L</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{money(oos.net_pnl)}</div>
              </div>
              <div>
                <div style={{ color: 'var(--text-secondary)' }}>Win rate</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>
                  {oos.win_rate ? `${(Number(oos.win_rate) * 100).toFixed(2)}%` : '—'}
                </div>
              </div>
              <div>
                <div style={{ color: 'var(--text-secondary)' }}>Expectancy / trade</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>
                  {money(oos.expectancy_per_trade)}
                </div>
              </div>
              <div>
                <div style={{ color: 'var(--text-secondary)' }}>Max drawdown</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{pct(oos.max_drawdown_pct)}</div>
              </div>
              <div>
                <div style={{ color: 'var(--text-secondary)' }}>Ending cash</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{money(oos.ending_cash)}</div>
              </div>
            </div>
          )}

          <div style={{ overflowX: 'auto', marginBottom: 16 }}>
            <table
              data-testid="strategy-research-trades"
              style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1080 }}
            >
              <thead>
                <tr>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>#</th>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>Fold</th>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>Side</th>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>Entry</th>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>Exit</th>
                  <th style={headCellStyle}>Entry px</th>
                  <th style={headCellStyle}>Exit px</th>
                  <th style={headCellStyle}>Qty</th>
                  <th style={headCellStyle}>Net P&amp;L</th>
                  <th style={headCellStyle}>Cost</th>
                  <th style={headCellStyle}>MFE / MAE</th>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>Exit rule</th>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>Earn / loss reason</th>
                </tr>
              </thead>
              <tbody>
                {detail.trades.length === 0 && (
                  <tr>
                    <td colSpan={13} style={{ ...cellStyle, textAlign: 'left' }}>
                      The study executed no out-of-sample trades. A refused or empty fold is
                      recorded as such; nothing is filled in.
                    </td>
                  </tr>
                )}
                {detail.trades.map((trade) => (
                  <tr key={trade.trade_index}>
                    <td style={{ ...cellStyle, textAlign: 'left' }}>{trade.trade_index}</td>
                    <td style={{ ...cellStyle, textAlign: 'left' }}>{trade.fold ?? '—'}</td>
                    <td style={{ ...cellStyle, textAlign: 'left' }}>
                      {trade.direction === 'long' ? 'Long' : 'Short'}
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                        {trade.rule_label_zh ?? ''}
                      </div>
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'left' }}>
                      {utcLabel(trade.entry_time)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'left' }}>{utcLabel(trade.exit_time)}</td>
                    <td style={cellStyle}>{money(trade.entry_price)}</td>
                    <td style={cellStyle}>{money(trade.exit_price)}</td>
                    <td style={cellStyle}>{trade.quantity}</td>
                    <td
                      style={{
                        ...cellStyle,
                        fontWeight: 600,
                        color:
                          Number(trade.net_pnl) > 0
                            ? 'var(--positive, #36C7A0)'
                            : Number(trade.net_pnl) < 0
                              ? 'var(--negative)'
                              : undefined,
                      }}
                    >
                      {money(trade.net_pnl)}
                    </td>
                    <td style={cellStyle}>{money(trade.total_cost)}</td>
                    <td style={cellStyle}>
                      {pct(trade.mfe_pct)} / {pct(trade.mae_pct)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'left' }}>
                      {trade.exit_reason}
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                        {trade.exit_label_zh ?? ''} · held {trade.bars_held} bar(s)
                      </div>
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'left', maxWidth: 320 }}>
                      {trade.pnl_reason_en}
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                        {trade.pnl_reason_zh}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {Array.isArray(detail.disclosures) && detail.disclosures.length > 0 && (
            <ul
              data-testid="strategy-research-disclosures"
              style={{
                margin: '12px 0 0',
                paddingLeft: 18,
                fontSize: 12,
                color: 'var(--text-secondary)',
              }}
            >
              {detail.disclosures.map((line, index) => (
                <li key={index} style={{ marginBottom: 4 }}>
                  {line}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
