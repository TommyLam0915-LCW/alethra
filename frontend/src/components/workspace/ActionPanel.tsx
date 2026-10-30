'use client';

import { useRef, useState } from 'react';
import OrderPreviewPanel from './OrderPreviewPanel';
import StrategyRulesPanel from './StrategyRulesPanel';
import type { ContextPortfolio } from '@/components/shell/WorkspaceContextBar';
import type { ReplaySession } from './ReplayController';
import type { RulesState } from './strategyRules';
import type { BarsResponse, InstrumentDetail } from './types';

type ActionTabKey = 'rules' | 'order';

const TABS: { key: ActionTabKey; label: string }[] = [
  { key: 'order', label: 'Manual paper order' },
  { key: 'rules', label: 'Strategy rules' },
];

/**
 * The two tabs share one container so the order form and the rules it is judged
 * against are never in different places. Both panels stay mounted and the
 * inactive one is hidden: switching tabs to check a rule must not discard the
 * order review the user already built.
 */
export default function ActionPanel({
  instrument,
  bars,
  instrumentId,
  session,
  rules,
  onSessionChange,
  onPortfolioChange,
  onBookChanged,
}: {
  instrument: InstrumentDetail | null;
  bars: BarsResponse | null;
  instrumentId: string;
  session: ReplaySession | null;
  /** Resolved by the workspace shell, so ⑥'s cut and ⑦'s submit target read one source. */
  rules: RulesState;
  onSessionChange?: (session: ReplaySession) => void;
  onPortfolioChange?: (portfolio: ContextPortfolio | null) => void;
  /** Tells ⑧ to re-read the account after a write that moved neither replay clock. */
  onBookChanged?: () => void;
}) {
  const [tab, setTab] = useState<ActionTabKey>('order');
  const tabRefs = useRef<Record<ActionTabKey, HTMLButtonElement | null>>({
    order: null,
    rules: null,
  });

  const moveFocus = (delta: number) => {
    const index = TABS.findIndex((t) => t.key === tab);
    const next = TABS[(index + delta + TABS.length) % TABS.length];
    setTab(next.key);
    tabRefs.current[next.key]?.focus();
  };

  return (
    <section className="ws-panel ws-panel-fill" data-testid="action-panel">
      <div className="ws-panel-header">Actions</div>
      <div
        className="ws-tabs"
        role="tablist"
        aria-label="Action panel"
        data-testid="action-tabs"
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') {
            e.preventDefault();
            moveFocus(1);
          } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            moveFocus(-1);
          } else if (e.key === 'Home') {
            e.preventDefault();
            setTab(TABS[0].key);
            tabRefs.current[TABS[0].key]?.focus();
          } else if (e.key === 'End') {
            e.preventDefault();
            const last = TABS[TABS.length - 1].key;
            setTab(last);
            tabRefs.current[last]?.focus();
          }
        }}
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            ref={(el) => {
              tabRefs.current[t.key] = el;
            }}
            type="button"
            role="tab"
            id={`action-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`action-tabpanel-${t.key}`}
            tabIndex={tab === t.key ? 0 : -1}
            className={`ws-tab ${tab === t.key ? 'ws-tab-active' : ''}`}
            onClick={() => setTab(t.key)}
            data-testid={`action-tab-${t.key}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id="action-tabpanel-order"
        aria-labelledby="action-tab-order"
        data-testid="action-tabpanel-order"
        style={{
          flex: 1,
          minHeight: 0,
          display: tab === 'order' ? 'flex' : 'none',
          flexDirection: 'column',
        }}
      >
        <OrderPreviewPanel
          instrument={instrument}
          bars={bars}
          instrumentId={instrumentId}
          session={session}
          rules={rules}
          onSessionChange={onSessionChange}
          onPortfolioChange={onPortfolioChange}
          onBookChanged={onBookChanged}
          hideHeader
        />
      </div>

      <div
        role="tabpanel"
        id="action-tabpanel-rules"
        aria-labelledby="action-tab-rules"
        data-testid="action-tabpanel-rules"
        style={{
          flex: 1,
          minHeight: 0,
          display: tab === 'rules' ? 'flex' : 'none',
          flexDirection: 'column',
        }}
      >
        <StrategyRulesPanel session={session} rules={rules} hideHeader />
      </div>
    </section>
  );
}
