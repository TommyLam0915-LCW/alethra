'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, api, formatErrorDetail } from '@/lib/api';

interface Props {
  instrumentId: string;
  instrumentLabel: string;
}

interface Refusal {
  code: string;
  message: string;
}

function refusalFrom(err: unknown): Refusal {
  if (err instanceof ApiError && err.detail && typeof err.detail === 'object') {
    const detail = err.detail as { code?: unknown; message?: unknown };
    if (typeof detail.code === 'string') {
      return {
        code: detail.code,
        message:
          typeof detail.message === 'string' ? detail.message : formatErrorDetail(err.detail),
      };
    }
    return { code: 'request_refused', message: formatErrorDetail(err.detail) };
  }
  return { code: 'request_failed', message: err instanceof Error ? err.message : String(err) };
}

function utcLabel(value: string | null | undefined): string {
  if (!value) return '—';
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)/.exec(value);
  if (!match) return value;
  return `${match[1]} ${match[2]} UTC`;
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
  fontSize: 13,
  textAlign: 'right',
  fontVariantNumeric: 'tabular-nums',
} as const;

const headCellStyle = {
  ...cellStyle,
  color: 'var(--text-secondary)',
  fontWeight: 500,
} as const;

export default function StrategyLabPanel({ instrumentId, instrumentLabel }: Props) {
  const [plan, setPlan] = useState<Record<string, any> | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [trades, setTrades] = useState('1000');
  const [startingCash, setStartingCash] = useState('10000');
  const [notional, setNotional] = useState('1000');
  const [result, setResult] = useState<Record<string, any> | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = (await api.strategyLab.plan()) as Record<string, any>;
        if (!cancelled) {
          setPlan(data);
          setStartingCash(String(data.settings?.starting_cash_per_strategy ?? '10000'));
          setNotional(String(data.settings?.notional_per_trade ?? '1000'));
          setPlanError(null);
        }
      } catch (err) {
        if (!cancelled) setPlanError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const run = useCallback(async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const data = (await api.strategyLab.run({
        instrument_id: instrumentId,
        trades_per_strategy: Number(trades),
        starting_cash: startingCash,
        notional_per_trade: notional,
      })) as Record<string, any>;
      setResult(data);
    } catch (err) {
      setResult(null);
      setRefusal(refusalFrom(err));
    } finally {
      setBusy(false);
    }
  }, [instrumentId, trades, startingCash, notional]);

  const rows = useMemo<Array<Record<string, any>>>(
    () => (Array.isArray(result?.strategies) ? result?.strategies : []),
    [result],
  );

  const costModel = plan?.cost_model
    ? `${plan.cost_model.commission}; ${plan.cost_model.slippage}. ${plan.cost_model.assumption_note}`
    : null;
  const dataset = result?.dataset;

  return (
    <section
      aria-labelledby="strategy-lab-heading"
      data-testid="strategy-lab-panel"
      style={{
        border: '1px solid var(--border-subtle)',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 6,
        padding: 20,
        marginBottom: 20,
      }}
    >
      <h2 id="strategy-lab-heading" style={{ margin: '0 0 8px', fontSize: 15, fontWeight: 600 }}>
        Strategy lab · {instrumentLabel}
      </h2>
      <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--text-secondary)' }}>
        Five signal families run as separate one-bar round trips on real completed venue bars.
        Every strategy gets its own wallet and ledger, so no balance or position is shared.
        This measures trade accounting and balance after the requested number of trades — it is not
        a profitability or suitability claim.
      </p>

      {planError && (
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
          Couldn&apos;t read the strategy plan: {planError}
        </div>
      )}

      {plan && (
        <div
          data-testid="strategy-lab-plan"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 12,
            marginBottom: 16,
          }}
        >
          {(plan.strategies as Array<Record<string, any>>).map((strategy) => (
            <div
              key={strategy.strategy_id}
              data-testid={`strategy-lab-plan-${strategy.strategy_id}`}
              style={{
                border: '1px solid var(--border-subtle)',
                borderRadius: 4,
                padding: 12,
                fontSize: 12,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
                {strategy.label}
              </div>
              <div style={{ color: 'var(--text-secondary)', marginBottom: 6 }}>
                {strategy.family}
              </div>
              <div style={{ marginBottom: 6 }}>{strategy.thesis}</div>
              <div style={{ color: 'var(--text-secondary)' }}>{strategy.rule}</div>
            </div>
          ))}
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 12,
          marginBottom: 16,
        }}
      >
        <label style={labelStyle}>
          Trades per strategy
          <input
            aria-label="Trades per strategy"
            data-testid="strategy-lab-trades"
            value={trades}
            inputMode="numeric"
            onChange={(event) => setTrades(event.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={labelStyle}>
          Starting cash per wallet (USD)
          <input
            aria-label="Starting cash"
            data-testid="strategy-lab-cash"
            value={startingCash}
            inputMode="decimal"
            onChange={(event) => setStartingCash(event.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={labelStyle}>
          Notional per trade (USD)
          <input
            aria-label="Notional per trade"
            data-testid="strategy-lab-notional"
            value={notional}
            inputMode="decimal"
            onChange={(event) => setNotional(event.target.value)}
            style={inputStyle}
          />
        </label>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
        <button
          type="button"
          className="ws-range-btn"
          onClick={() => void run()}
          disabled={busy}
          data-testid="strategy-lab-run"
        >
          {busy ? 'Running strategies…' : `Run ${trades || '0'} trades per strategy`}
        </button>
        {costModel && (
          <span data-testid="strategy-lab-cost-model" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {costModel}
          </span>
        )}
      </div>

      {refusal && (
        <div
          role="alert"
          data-testid="strategy-lab-error"
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

      {result && (
        <div data-testid="strategy-lab-result">
          <div
            style={{
              display: 'flex',
              gap: 8,
              flexWrap: 'wrap',
              alignItems: 'center',
              marginBottom: 8,
            }}
          >
            <strong style={{ fontSize: 13 }}>Remaining funds after the requested trades</strong>
            <span
              style={{
                fontSize: 12,
                border: '1px solid var(--border-subtle)',
                borderRadius: 999,
                padding: '2px 10px',
                color: 'var(--warning-text)',
              }}
            >
              {result.data_nature} bars · simulated fills
            </span>
          </div>

          {dataset && (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
              {dataset.total_bars} completed bars from {dataset.segments?.length ?? 0} non-overlapping
              windows · forming candles excluded ·{' '}
              {dataset.non_overlapping ? 'no overlap' : 'overlap reported'} ·{' '}
              {dataset.segments?.every((segment: Record<string, any>) => segment.integrity_ok)
                ? 'segment integrity ok'
                : 'segment gaps reported'}
            </div>
          )}

          <div style={{ overflowX: 'auto', marginBottom: 16 }}>
            <table
              data-testid="strategy-lab-funds-table"
              style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}
            >
              <thead>
                <tr>
                  <th style={{ ...headCellStyle, textAlign: 'left' }}>Strategy</th>
                  <th style={headCellStyle}>Trades</th>
                  <th style={headCellStyle}>Start</th>
                  <th style={headCellStyle}>Remaining</th>
                  <th style={headCellStyle}>Net change</th>
                  <th style={headCellStyle}>Win rate</th>
                  <th style={headCellStyle}>Max drawdown</th>
                  <th style={headCellStyle}>Balance check</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.strategy_id}>
                    <td style={{ ...cellStyle, textAlign: 'left' }}>
                      <div style={{ fontWeight: 600 }}>{row.label}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{row.family}</div>
                    </td>
                    <td style={cellStyle}>
                      {row.trades_executed} / {row.trades_requested}
                      {row.halted_reason ? (
                        <div style={{ fontSize: 11, color: 'var(--warning-text)' }}>
                          halted: {row.halted_reason}
                        </div>
                      ) : null}
                    </td>
                    <td style={cellStyle}>{row.starting_cash}</td>
                    <td style={{ ...cellStyle, fontWeight: 600 }}>{row.ending_cash}</td>
                    <td style={cellStyle}>{row.net_change}</td>
                    <td style={cellStyle}>{(Number(row.win_rate) * 100).toFixed(2)}%</td>
                    <td style={cellStyle}>{row.max_drawdown_pct}%</td>
                    <td style={cellStyle}>
                      {row.accounting?.ending_equals_start_plus_net ? 'exact' : 'MISMATCH'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}>
            Window {utcLabel(dataset?.first_open_time)} → {utcLabel(dataset?.last_close_time)} · generated{' '}
            {utcLabel(result.generated_at)}
          </div>

          {result.isolation && (
            <div
              data-testid="strategy-lab-isolation"
              style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 12 }}
            >
              Isolation: {result.isolation.shared_cash ? 'SHARED CASH' : 'separate wallets'} ·{' '}
              {result.isolation.per_strategy_ledgers ? 'separate ledgers' : 'shared ledger'} ·{' '}
              {result.isolation.cross_wallet_transfers === 0 ? 'no cross-wallet transfers' : 'transfer detected'} ·
              one trade per signal bar, no crossing between windows.
            </div>
          )}

          {Array.isArray(result.disclosures_zh) && (
            <ul
              data-testid="strategy-lab-disclosures"
              style={{
                margin: '12px 0 0',
                paddingLeft: 18,
                fontSize: 12,
                color: 'var(--text-secondary)',
              }}
            >
              {result.disclosures_zh.map((line: string, index: number) => (
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
