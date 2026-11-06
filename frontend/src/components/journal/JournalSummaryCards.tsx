'use client';

import { useState, type CSSProperties } from 'react';
import type { JournalSummary } from './JournalDashboard';

// ---------------------------------------------------------------------------
// HelpIcon – small (i) with hover tooltip
// ---------------------------------------------------------------------------

function HelpIcon({ tooltip }: { tooltip: string }) {
  const [show, setShow] = useState(false);

  return (
    <span
      style={{
        position: 'relative',
        display: 'inline-block',
        marginLeft: 6,
        cursor: 'help',
        verticalAlign: 'middle',
      }}
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      aria-label={tooltip}
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" style={{ opacity: 0.45 }} aria-hidden="true">
        <path d="M8 0C3.58 0 0 3.58 0 8s3.58 8 8 8 8-3.58 8-8-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6s2.69-6 6-6 6 2.69 6 6-2.69 6-6 6z" />
        <path d="M8 4c-.55 0-1 .45-1 1v1c0 .55.45 1 1 1s1-.45 1-1V5c0-.55-.45-1-1-1zm0 8c.55 0 1-.45 1-1s-.45-1-1-1-1 .45-1 1 .45 1 1 1z" />
      </svg>
      {show && (
        <div
          role="tooltip"
          style={{
            position: 'absolute',
            bottom: '100%',
            left: '50%',
            transform: 'translateX(-50%)',
            marginBottom: 8,
            padding: '8px 12px',
            backgroundColor: 'var(--surface-2, #1e1e2e)',
            border: '1px solid var(--border-standard, var(--border-strong))',
            borderRadius: 6,
            fontSize: 12,
            lineHeight: 1.5,
            color: 'var(--text-primary, var(--seed-fg))',
            whiteSpace: 'nowrap',
            zIndex: 1000,
            boxShadow: '0 4px 12px rgba(0,0,0,0.18)',
            pointerEvents: 'none',
          }}
        >
          {tooltip}
        </div>
      )}
    </span>
  );
}

interface Props {
  summary: JournalSummary;
  loading: boolean;
}

const cardStyle: CSSProperties = {
  border: '1px solid var(--border-subtle)',
  borderRadius: 6,
  padding: 16,
  backgroundColor: 'var(--surface-1)',
};

const cardLabelStyle: CSSProperties = {
  fontSize: 12,
  color: 'var(--text-secondary)',
  marginBottom: 4,
  display: 'flex',
  alignItems: 'center',
};

const cardValueStyle: CSSProperties = {
  fontSize: 20,
  fontWeight: 600,
  fontVariantNumeric: 'tabular-nums',
};

function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return '--';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  return `$${num.toFixed(2)}`;
}

function formatPct(value: string | null | undefined): string {
  if (value === null || value === undefined) return '--';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  return `${num.toFixed(1)}%`;
}

function pnlColor(value: string | null | undefined): string | undefined {
  if (value === null || value === undefined) return undefined;
  const num = Number(value);
  if (!Number.isFinite(num)) return undefined;
  if (num > 0) return 'var(--positive, #36C7A0)';
  if (num < 0) return 'var(--negative, #E2636E)';
  return undefined;
}

export default function JournalSummaryCards({ summary, loading }: Props) {
  if (loading && !summary) {
    return (
      <div
        role="region"
        aria-label="Summary statistics loading"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: 12,
          marginBottom: 16,
        }}
      >
        {[0, 1, 2, 3].map((i) => (
          <div key={i} style={cardStyle}>
            <div style={cardLabelStyle}>Loading...</div>
            <div style={{ ...cardValueStyle, color: 'var(--text-muted)' }}>--</div>
          </div>
        ))}
      </div>
    );
  }

  const totalPnlNum = Number(summary.total_pnl);
  const avgPnlNum = Number(summary.avg_trade_pnl);
  const winRateNum = Number(summary.win_rate);
  const profitFactorNum =
    summary.profit_factor != null ? Number(summary.profit_factor) : null;
  const maxDdNum = Number(summary.max_drawdown_pct);

  return (
    <div
      role="region"
      aria-label="Trading performance summary"
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: 12,
        marginBottom: 16,
      }}
    >
      {/* Total Trades */}
      <div style={cardStyle} role="group" aria-label="Total trades summary">
        <div style={cardLabelStyle}>
          Total Trades
          <HelpIcon tooltip="Total number of trades across all your research runs" />
        </div>
        <div style={cardValueStyle} aria-label={`${summary.total_trades} trades`}>
          {summary.total_trades}
        </div>
      </div>

      {/* Total P&L — arrow indicator alongside color */}
      <div style={cardStyle} role="group" aria-label="Total profit and loss">
        <div style={cardLabelStyle}>
          Total P&L
          <HelpIcon tooltip="Your total profit or loss after all trading costs. Arrow up = profit, arrow down = loss." />
        </div>
        <div
          style={{
            ...cardValueStyle,
            color: pnlColor(summary.total_pnl),
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
          aria-label={`Total P&L ${totalPnlNum >= 0 ? 'positive' : 'negative'} ${formatMoney(summary.total_pnl)}`}
        >
          <span style={{ fontSize: 18, fontWeight: 600 }} aria-hidden="true">
            {totalPnlNum >= 0 ? '\u2191' : '\u2193'}
          </span>
          {formatMoney(summary.total_pnl)}
        </div>
      </div>

      {/* Win Rate — check/warn indicator */}
      <div style={cardStyle} role="group" aria-label="Win rate">
        <div style={cardLabelStyle}>
          Win Rate
          <HelpIcon tooltip="Percentage of trades that made a profit. Check = above 50%, warning = below 50%." />
        </div>
        <div style={cardValueStyle} aria-label={`Win rate ${formatPct(summary.win_rate)}`}>
          {formatPct(summary.win_rate)}
          <span
            style={{
              fontSize: 11,
              color:
                winRateNum >= 50
                  ? 'var(--positive, #36C7A0)'
                  : 'var(--negative, #E2636E)',
              marginLeft: 6,
              fontWeight: 500,
            }}
            aria-hidden="true"
          >
            {winRateNum >= 50 ? '\u2713' : '!'}
          </span>
        </div>
      </div>

      {/* Avg Trade P&L — +/- indicator alongside color */}
      <div style={cardStyle} role="group" aria-label="Average trade profit and loss">
        <div style={cardLabelStyle}>
          Avg Trade P&L
          <HelpIcon tooltip="Average profit or loss per trade. Plus sign = profitable on average, minus = losing." />
        </div>
        <div
          style={{
            ...cardValueStyle,
            color: pnlColor(summary.avg_trade_pnl),
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
          aria-label={`Average trade P&L ${avgPnlNum >= 0 ? 'positive' : 'negative'} ${formatMoney(summary.avg_trade_pnl)}`}
        >
          <span style={{ fontSize: 18, fontWeight: 600 }} aria-hidden="true">
            {avgPnlNum >= 0 ? '+' : '\u2212'}
          </span>
          {formatMoney(summary.avg_trade_pnl)}
        </div>
      </div>

      {/* Profit Factor — tier indicator */}
      {summary.profit_factor !== null && summary.profit_factor !== undefined && (
        <div style={cardStyle} role="group" aria-label="Profit factor">
          <div style={cardLabelStyle}>
            Profit Factor
            <HelpIcon tooltip="Total profits divided by total losses. Two checks = excellent (2+), one check = okay (1+), warning = losing." />
          </div>
          <div
            style={cardValueStyle}
            aria-label={`Profit factor ${Number(summary.profit_factor).toFixed(2)}`}
          >
            {Number(summary.profit_factor).toFixed(2)}
            {profitFactorNum !== null && Number.isFinite(profitFactorNum) && (
              <span
                style={{
                  fontSize: 11,
                  color:
                    profitFactorNum >= 2
                      ? 'var(--positive, #36C7A0)'
                      : profitFactorNum >= 1
                        ? 'var(--text-secondary)'
                        : 'var(--negative, #E2636E)',
                  marginLeft: 6,
                  fontWeight: 500,
                }}
                aria-hidden="true"
              >
                {profitFactorNum >= 2
                  ? '\u2713\u2713'
                  : profitFactorNum >= 1
                    ? '\u2713'
                    : '!'}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Max Drawdown — arrow + severity indicator */}
      <div style={cardStyle} role="group" aria-label="Maximum drawdown">
        <div style={cardLabelStyle}>
          Max Drawdown
          <HelpIcon tooltip="Largest peak-to-trough decline. Check = low risk (10% or less), warning = moderate, double warning = high risk." />
        </div>
        <div
          style={{
            ...cardValueStyle,
            color: 'var(--negative, #E2636E)',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
          aria-label={`Maximum drawdown ${formatPct(summary.max_drawdown_pct)}`}
        >
          <span style={{ fontSize: 18, fontWeight: 600 }} aria-hidden="true">
            {'\u2193'}
          </span>
          {formatPct(summary.max_drawdown_pct)}
          <span
            style={{
              fontSize: 11,
              color:
                maxDdNum <= 10
                  ? 'var(--positive, #36C7A0)'
                  : maxDdNum <= 20
                    ? 'var(--text-secondary)'
                    : 'var(--negative, #E2636E)',
              marginLeft: 'auto',
              fontWeight: 500,
            }}
            aria-hidden="true"
          >
            {maxDdNum <= 10 ? '\u2713' : maxDdNum <= 20 ? '!' : '!!'}
          </span>
        </div>
      </div>
    </div>
  );
}
