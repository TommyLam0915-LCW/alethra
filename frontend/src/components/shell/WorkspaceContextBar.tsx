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
  demo: '演示數據',
  live: '即時數據',
};

/**
 * broker_mode is a *configuration* reading, not proof that this portfolio's order
 * would reach an exchange, so `live` is echoed as a raw config value plus a
 * mismatch notice instead of being promoted into an execution capability.
 */
const BROKER_CONFIG_LABELS: Record<string, string> = {
  'paper-only': '僅紙上模擬',
  sandbox: '沙盒',
};

const AI_MODE_LABELS: Record<string, string> = {
  mock: 'AI 模擬',
  live: 'AI 即時',
};

/** Backend PortfolioStatus — historical accounts remain viewable, only active ones order. */
const PORTFOLIO_STATUS_LABELS: Record<string, string> = {
  active: '進行中',
  paused: '已暫停',
  retired: '已停用',
  completed: '已完成',
};

const UNAVAILABLE = '未提供';

/**
 * This page's orders go to the replay manual-decision endpoint or the controlled
 * executor intent endpoint; both settle on the local paper ledger. That is what
 * licenses the 「紙上模擬」 execution scope here — not a broker config flag.
 */
const EXECUTION_SCOPE = '紙上模擬';

type PanelKey = 'data' | 'system';

function configValue(labels: Record<string, string>, value?: string | null): string {
  if (!value) return UNAVAILABLE;
  return labels[value] ?? `${value}（原值）`;
}

/**
 * Inline context strip — content-led design.
 * Left: what the user is trading (portfolio identity, symbol) and how to read the
 * prices (data mode · execution scope, replay clock). Right: the three separate
 * facts an order depends on — 數據模式 / 執行範圍 / 系統經紀配置 — plus two
 * keyboard-operable disclosures: 資料詳情 for price provenance, 系統狀態 for
 * AI/usage and configuration-consistency notes.
 */
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
            ? `模擬組合 ${portfolio.id} · ${portfolio.status}${portfolioHint ? `｜${portfolioHint}` : ''}`
            : portfolioHint ?? '未選擇模擬組合'
        }
        data-testid="ctx-portfolio-identity"
      >
        {portfolio
          ? `${portfolio.currency} 組合 ${portfolio.id.slice(0, 8)}${
              portfolio.status && portfolio.status !== 'active'
                ? `（${PORTFOLIO_STATUS_LABELS[portfolio.status] ?? portfolio.status}）`
                : ''
            }`
          : '未選擇模擬組合'}
      </span>
      <span className="ctx-strip-sep">·</span>
      <span className="ctx-strip-symbol" title={name ?? undefined}>
        {symbol ?? '未選擇'}
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
            title={replayInstrumentNote ?? '回放時鐘（非即時）'}
          >
            {replayTimestamp}
          </span>
          {replayInstrumentNote && (
            <span
              className="ws-chip ws-chip-warn"
              data-testid="ctx-replay-mismatch"
              title={replayInstrumentNote}
            >
              游標非本標的
            </span>
          )}
        </>
      )}

      <span className="ctx-strip-status" data-testid="status-chips">
        <span
          className="ws-chip"
          title="市場數據模式（來源：伺服器運行狀態端點）"
          data-testid="status-data-mode"
        >
          數據：{configValue(DATA_MODE_LABELS, dataMode)}
        </span>
        <span
          className="ws-chip"
          title="本頁送單只寫本地紙上帳簿：manual-decision 提交的是 pending 掛單（不計入持股、不動現金），由重放時鐘在下一根可執行 K 線以開盤價成交一次；execution intent 走受控執行器的離線模擬。兩者都不連接實盤經紀。"
          data-testid="status-execution-scope"
        >
          執行範圍：{EXECUTION_SCOPE}
        </span>
        <span
          className={`ws-chip ${configConflicts ? 'ws-chip-warn' : 'ws-chip-muted'}`}
          title="經紀連接的系統配置值，非此組合的實盤執行證明"
          data-testid="status-broker-mode"
        >
          經紀：{configValue(BROKER_CONFIG_LABELS, brokerMode)}
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
        資料詳情
      </button>
      <button
        type="button"
        className="ctx-strip-details-btn"
        aria-expanded={openPanel === 'system'}
        aria-controls="ctx-system-panel"
        onClick={() => toggle('system')}
        data-testid="ctx-system-toggle"
      >
        系統狀態
      </button>

      {openPanel === 'data' && (
        <div className="ctx-details-panel" id="ctx-details-panel" data-testid="ctx-details-panel">
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">數據來源</span>
            <span className="ctx-detail-value">{provider ?? '未知'}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">推送模式</span>
            <span className="ctx-detail-value">{feedMode ?? UNAVAILABLE}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">數據截至</span>
            <span className="ctx-detail-value">{asOf ?? '未知'}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">市場狀態</span>
            <span className="ctx-detail-value">{marketLabel ?? UNAVAILABLE}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">回放截止</span>
            <span className="ctx-detail-value" data-testid="ctx-replay-cut">
              {replayInstrumentNote ?? replayTimestamp ?? '非回放（無回放游標）'}
            </span>
          </span>
        </div>
      )}

      {openPanel === 'system' && (
        <div className="ctx-details-panel" id="ctx-system-panel" data-testid="ctx-system-panel">
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">系統模式</span>
            <span className="ctx-detail-value">{status?.mode ?? UNAVAILABLE}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">數據模式</span>
            <span className="ctx-detail-value">{configValue(DATA_MODE_LABELS, dataMode)}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">執行範圍</span>
            <span className="ctx-detail-value">{EXECUTION_SCOPE}（此頁送單端點）</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">經紀配置</span>
            <span className="ctx-detail-value">{configValue(BROKER_CONFIG_LABELS, brokerMode)}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">AI 配置</span>
            <span className="ctx-detail-value">{configValue(AI_MODE_LABELS, aiMode)}</span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">AI 用量</span>
            <span className="ctx-detail-value">
              {typeof usageCount === 'number' ? `${usageCount} 次` : UNAVAILABLE}
            </span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">組合選擇</span>
            <span className="ctx-detail-value">
              {portfolioHint ?? (portfolio ? `已綁定組合 ${portfolio.id.slice(0, 8)}` : UNAVAILABLE)}
            </span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">組合狀態</span>
            <span className="ctx-detail-value" title={portfolio?.status ?? undefined}>
              {portfolio
                ? `${PORTFOLIO_STATUS_LABELS[portfolio.status] ?? portfolio.status} · ${portfolio.status}`
                : '未選擇模擬組合'}
            </span>
          </span>
          <span className="ctx-detail-item">
            <span className="ctx-detail-label">回放綁定</span>
            <span className="ctx-detail-value">
              {portfolio?.simulation_session_id ? portfolio.simulation_session_id.slice(0, 8) : '未綁定'}
            </span>
          </span>
          <p className="ctx-detail-note" data-testid="ctx-config-consistency">
            {status
              ? configConflicts
                ? '配置不一致：經紀配置為 live，但此工作區送單端點仍在本地紙上帳簿成交。實盤執行與 agent 部署狀態未確認，如需實盤請另行核對，本頁面不提供實盤下單。'
                : '配置一致：經紀配置與此頁送單端點同為紙上模擬。此判斷只適用於本工作區端點，不代表其他程序或部署的執行能力。'
              : '運行狀態端點未能讀取：數據／經紀／AI 配置皆為未提供，不以任何預設值代替真實狀態。'}
          </p>
        </div>
      )}
    </div>
  );
}
