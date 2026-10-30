'use client';

import { useState } from 'react';
import ModeBadge from './ModeBadge';
import type { OperationalStatus } from './useOperationalStatus';
import type { PriceViewMeta } from '@/components/workspace/types';

export interface ContextPortfolio {
  id: string;
  currency: string;
  current_virtual_cash: number;
  status: string;
  simulation_session_id?: string | null;
}

export interface ContextMeta {
  mode?: PriceViewMeta['mode'];
  mode_label_zh?: string | null;
  market_status?: PriceViewMeta['market_status'];
  market_status_label_zh?: string | null;
  data_as_of?: string | null;
  provider?: string;
  feed_mode?: string;
}

const DATA_MODE_LABELS: Record<string, string> = {
  demo: 'Demo data',
  live: 'Live data',
};

/**
 * broker_mode is a *configuration* reading, not proof that this portfolio's order
 * would reach an exchange, so `live` is echoed as a raw config value plus a
 * mismatch notice instead of being promoted into an execution capability.
 */
const BROKER_CONFIG_LABELS: Record<string, string> = {
  'paper-only': 'Paper only',
  sandbox: 'Sandbox',
};

const AI_MODE_LABELS: Record<string, string> = {
  mock: 'AI simulated',
  live: 'AI live',
};

/** Backend PortfolioStatus — historical accounts remain viewable, only active ones order. */
const PORTFOLIO_STATUS_LABELS: Record<string, string> = {
  active: 'Active',
  paused: 'Paused',
  retired: 'Retired',
  completed: 'Completed',
};

const UNAVAILABLE = 'Not provided';

/**
 * This page's orders go to the replay manual-decision endpoint or the controlled
 * executor intent endpoint; both settle on the local paper ledger. That is what
 * licenses the "Paper only" execution scope here — not a broker config flag.
 */
const EXECUTION_SCOPE = 'Paper only';

type PanelKey = 'data' | 'system';

function configValue(labels: Record<string, string>, value?: string | null): string {
  if (!value) return UNAVAILABLE;
  return labels[value] ?? `${value} (raw)`;
}

export default function WorkspaceContextBar({
  symbol,
  name,
  portfolio,
  portfolioHint,
  meta,
  status,
  usageCount,
  replayTimestamp,
  replayInstrumentNote,
}: {
  symbol?: string | null;
  name?: string | null;
  portfolio?: ContextPortfolio | null;
  portfolioHint?: string | null;
  meta?: ContextMeta | null;
  status?: OperationalStatus | null;
  usageCount?: number | null;
  replayTimestamp?: string | null;
  /** Set when the session cursor belongs to another instrument than the plotted one. */
  replayInstrumentNote?: string | null;
}) {
  const [openPanel, setOpenPanel] = useState<PanelKey | null>(null);
  const toggle = (key: PanelKey) => setOpenPanel((cur) => (cur === key ? null : key));

  const provider = meta?.provider ?? null;
  const asOf = meta?.data_as_of ?? null;
  const marketLabel = meta?.market_status_label_zh ?? meta?.market_status ?? null;
  const feedMode = meta?.feed_mode ?? null;
  const dataMode = status?.data_mode ?? null;
  const brokerMode = status?.broker_mode ?? null;
  const aiMode = status?.ai_mode ?? null;
  // A live broker config next to paper-only endpoints is a contradiction the user
  // must see; it is never resolved by assuming the safer of the two.
  const configConflicts = brokerMode === 'live';

  return (
    <div className="ctx-strip" data-testid="workspace-context-strip">
      <span
        className="ctx-strip-portfolio"
        title={
          portfolio
            ? `Paper portfolio ${portfolio.id} · ${portfolio.status}${portfolioHint ? ` | ${portfolioHint}` : ''}`
            : portfolioHint ?? 'No paper portfolio selected'
        }
        data-testid="ctx-portfolio-identity"
      >
        {portfolio
          ? `${portfolio.currency} portfolio ${portfolio.id.slice(0, 8)}${
              portfolio.status && portfolio.status !== 'active'
                ? ` (${PORTFOLIO_STATUS_LABELS[portfolio.status] ?? portfolio.status})`
                : ''
            }`
          : 'No paper portfolio selected'}
      </span>
      <span className="ctx-strip-sep">·</span>
      <span className="ctx-strip-symbol" title={name ?? undefined}>
        {symbol ?? 'No instrument'}
      </span>
      <span className="ctx-strip-sep">·</span>
      <span className="ctx-strip-mode">
        <ModeBadge meta={meta ?? null} executionScope={EXECUTION_SCOPE} compact />
      </span>
      {replayTimestamp && (
        <>
          <span className="ctx-strip-sep">·</span>
          <span
            className="ctx-strip-replay"
            data-testid="ctx-replay-clock"
            title={replayInstrumentNote ?? 'Replay clock (not live)'}
          >
            {replayTimestamp}
          </span>
          {replayInstrumentNote && (
            <span
              className="ws-chip ws-chip-warn"
              data-testid="ctx-replay-mismatch"
              title={replayInstrumentNote}
            >
              Cursor is on another instrument
            </span>
          )}
        </>
      )}

      <span className="ctx-strip-status" data-testid="status-chips">
        <span
          className="ws-chip"
          title="Market data mode (source: server operational status endpoint)"
          data-testid="status-data-mode"
        >
          Data: {configValue(DATA_MODE_LABELS, dataMode)}
        </span>
        <span
          className="ws-chip"
          title="Orders from this page write to the local paper ledger only: manual decisions queue a pending order (no position, no cash movement) that the replay clock fills once at the next executable bar's open; execution intents go to the controlled executor's offline simulation. Neither connects to a live broker."
          data-testid="status-execution-scope"
        >
          Execution: {EXECUTION_SCOPE}
        </span>
        <span
          className={`ws-chip ${configConflicts ? 'ws-chip-warn' : 'ws-chip-muted'}`}
          title="Broker connection configuration value; not proof that this portfolio would trade live"
          data-testid="status-broker-mode"
        >
          Broker: {configValue(BROKER_CONFIG_LABELS, brokerMode)}
        </span>
      </span>

      <button
        type="button"
        className="ctx-strip-details-btn"
        aria-expanded={openPanel === 'data'}
        aria-controls="ctx-details-panel"
        onClick={() => toggle('data')}
        data-testid="ctx-details-toggle"
      >
        Data details
      </button>
      <button
        type="button"
        className="ctx-strip-details-btn"
        aria-expanded={openPanel === 'system'}
        aria-controls="ctx-system-panel"
        onClick={() => toggle('system')}
        data-testid="ctx-system-toggle"
      >
        System status
      </button>

      {openPanel === 'data' && (
        <div className="ctx-details-panel" id="ctx-details-panel" data-testid="ctx-details-panel">
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Source</span>
            <span className="ctx-detail-value">{provider ?? 'Unknown'}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Feed mode</span>
            <span className="ctx-detail-value">{feedMode ?? UNAVAILABLE}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">As of</span>
            <span className="ctx-detail-value">{asOf ?? 'Unknown'}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Market status</span>
            <span className="ctx-detail-value">{marketLabel ?? UNAVAILABLE}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Replay cut-off</span>
            <span className="ctx-detail-value" data-testid="ctx-replay-cut">
              {replayInstrumentNote ?? replayTimestamp ?? 'Not in replay (no replay cursor)'}
            </span>
          </span>
        </div>
      )}

      {openPanel === 'system' && (
        <div className="ctx-details-panel" id="ctx-system-panel" data-testid="ctx-system-panel">
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">System mode</span>
            <span className="ctx-detail-value">{status?.mode ?? UNAVAILABLE}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Data mode</span>
            <span className="ctx-detail-value">{configValue(DATA_MODE_LABELS, dataMode)}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Execution scope</span>
            <span className="ctx-detail-value">{EXECUTION_SCOPE} (this page&apos;s order endpoint)</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Broker config</span>
            <span className="ctx-detail-value">{configValue(BROKER_CONFIG_LABELS, brokerMode)}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">AI config</span>
            <span className="ctx-detail-value">{configValue(AI_MODE_LABELS, aiMode)}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">AI usage</span>
            <span className="ctx-detail-value">
              {typeof usageCount === 'number' ? `${usageCount} calls` : UNAVAILABLE}
            </span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Portfolio</span>
            <span className="ctx-detail-value">
              {portfolioHint ?? (portfolio ? `Bound to portfolio ${portfolio.id.slice(0, 8)}` : UNAVAILABLE)}
            </span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Portfolio status</span>
            <span className="ctx-detail-value" title={portfolio?.status ?? undefined}>
              {portfolio
                ? `${PORTFOLIO_STATUS_LABELS[portfolio.status] ?? portfolio.status} · ${portfolio.status}`
                : 'No paper portfolio selected'}
            </span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">Replay binding</span>
            <span className="ctx-detail-value">
              {portfolio?.simulation_session_id ? portfolio.simulation_session_id.slice(0, 8) : 'Not bound'}
            </span>
          </span>
          <p className="ctx-detail-note" data-testid="ctx-config-consistency">
            {status
              ? configConflicts
                ? 'Configuration mismatch: the broker configuration reads live, but this workspace’s order endpoint still settles on the local paper ledger. Live execution and agent deployment are unconfirmed — verify separately; this page does not place live orders.'
                : 'Configuration consistent: the broker configuration and this page’s order endpoint are both paper. This applies to this workspace endpoint only, not to other processes or deployments.'
              : 'The operational status endpoint could not be read. Data, broker and AI configuration are all reported as not provided; no default value is substituted for real status.'}
          </p>
        </div>
      )}
    </div>
  );
}
