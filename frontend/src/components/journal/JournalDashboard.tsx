'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, formatErrorDetail, ApiError } from '@/lib/api';
import AppShell from '@/components/shell/AppShell';
import JournalFilterBar from './JournalFilterBar';
import JournalSummaryCards from './JournalSummaryCards';
import JournalTradesTable from './JournalTradesTable';
import CumulativePnlChart from './CumulativePnlChart';
import EquityCurveChart from './EquityCurveChart';
import SkillAttributionPanel from './SkillAttributionPanel';
import SkillHeatmap from './SkillHeatmap';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface JournalFilters {
  instrument_id?: string;
  rule_id?: string;
  rule_family?: string;
  interval_minutes?: number;
  date_from?: string;
  date_to?: string;
  pnl_min?: string;
  pnl_max?: string;
  exit_reason?: string;
}

export interface FilterOptions {
  instruments: string[];
  intervals: number[];
  rule_ids: { rule_id: string; rule_label_zh: string | null; family: string }[];
  rule_families: string[];
  exit_reasons: string[];
}

export interface JournalTrade {
  run_id: string;
  trade_id: string;
  instrument_id: string;
  interval_minutes: number;
  starting_cash: string | null;
  trade_index: number;
  fold: number | null;
  direction: string;
  rule_id: string;
  rule_family: string;
  rule_label_zh: string | null;
  exit_id: string | null;
  exit_label_zh: string | null;
  exit_reason: string;
  outcome: string;
  signal_time: string | null;
  entry_time: string | null;
  exit_time: string | null;
  entry_price: string | null;
  exit_price: string | null;
  quantity: string | null;
  gross_pnl: string | null;
  commission: string | null;
  slippage_cost: string | null;
  total_cost: string | null;
  net_pnl: string | null;
  cash_after: string | null;
  bars_held: number | null;
  mfe_pct: string | null;
  mae_pct: string | null;
  cost_ratio_pct: string | null;
  pnl_reason_zh: string | null;
  pnl_reason_en: string | null;
}

export interface JournalSummary {
  total_trades: number;
  total_pnl: string;
  win_rate: string;
  avg_trade_pnl: string;
  profit_factor: string | null;
  max_drawdown_pct: string;
  best_trade: { trade_id: string; net_pnl: string; entry_time: string; instrument_id: string; rule_label_zh: string } | null;
  worst_trade: { trade_id: string; net_pnl: string; entry_time: string; instrument_id: string; rule_label_zh: string } | null;
}

type TabKey = 'trades' | 'cumulative-pnl' | 'equity-curve' | 'skill-attribution' | 'skill-heatmap';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'trades', label: 'Trades' },
  { key: 'cumulative-pnl', label: 'Cumulative P&L' },
  { key: 'equity-curve', label: 'Equity Curve' },
  { key: 'skill-attribution', label: 'Skill Attribution' },
  { key: 'skill-heatmap', label: 'Skill Heatmap' },
];

const TAB_DESCRIPTIONS: Record<TabKey, { title: string; description: string }> = {
  'trades': {
    title: 'Trade History',
    description: 'View all your trades in a sortable table. Click column headers to sort. Use filters above to focus on specific trades.',
  },
  'cumulative-pnl': {
    title: 'Cumulative P&L',
    description: 'This chart shows your running total profit/loss over time. An upward trend means your strategy is profitable. Hover over the line to see individual trade details.',
  },
  'equity-curve': {
    title: 'Equity Curve',
    description: 'Track your portfolio value over time (starting cash + cumulative P&L). Compare performance across different instruments. Steady growth indicates consistent profitability.',
  },
  'skill-attribution': {
    title: 'Skill Performance',
    description: 'See how each trading skill performs. Compare win rates, total P&L, and risk-adjusted returns (Sharpe ratio). Focus on skills with positive Sharpe (>1.0 is excellent).',
  },
  'skill-heatmap': {
    title: 'Skill Heatmap',
    description: 'Visual matrix showing which skills work best on which instruments. Green cells = profitable, red cells = losing. Use this to find your best instrument/skill combinations.',
  },
};

function refusalFrom(err: unknown): string {
  if (err instanceof ApiError && err.detail) {
    return formatErrorDetail(err.detail);
  }
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// PerformanceSummary – plain-language interpretation of key metrics
// ---------------------------------------------------------------------------

interface SkillRow {
  rule_id: string;
  rule_label_zh: string;
  rule_family: string;
  total_pnl: string;
  trade_count: number;
  win_count: number;
  win_rate: string;
  avg_trade_pnl: string;
  expectancy: string;
  sharpe_ratio: string | null;
  max_drawdown_pct: string;
  profit_factor: string | null;
}

function PerformanceSummary({ summary, skills }: { summary: JournalSummary; skills: SkillRow[] }) {
  const sortedSkills = [...skills].sort(
    (a, b) => Number(b.total_pnl) - Number(a.total_pnl),
  );
  const bestSkill = sortedSkills[0];
  const worstSkill = sortedSkills[sortedSkills.length - 1];

  const totalPnl = Number(summary.total_pnl);
  const winRate = Number(summary.win_rate);

  let status: string;
  let statusColor: string;

  if (totalPnl > 0 && winRate > 50) {
    status = 'Your strategies are profitable';
    statusColor = 'var(--positive, #36C7A0)';
  } else if (totalPnl > 0) {
    status = 'Your strategies are profitable, but win rate could improve';
    statusColor = 'var(--positive, #36C7A0)';
  } else if (totalPnl < -1000) {
    status = 'Your strategies are losing money';
    statusColor = 'var(--negative, #E2636E)';
  } else {
    status = 'Your strategies are slightly negative';
    statusColor = 'var(--negative, #E2636E)';
  }

  return (
    <div
      style={{
        padding: '16px 20px',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        marginBottom: 20,
        borderLeft: `4px solid ${statusColor}`,
      }}
    >
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8, color: statusColor }}>
        {status}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
        Total P&L:{' '}
        <strong style={{ color: totalPnl >= 0 ? 'var(--positive, #36C7A0)' : 'var(--negative, #E2636E)' }}>
          ${totalPnl.toFixed(2)}
        </strong>{' '}
        across {summary.total_trades} trades.
        {bestSkill && (
          <>
            {' '}Your best skill is{' '}
            <strong>{bestSkill.rule_label_zh ?? bestSkill.rule_id}</strong> with{' '}
            <span style={{ color: 'var(--positive, #36C7A0)' }}>
              ${Number(bestSkill.total_pnl).toFixed(2)}
            </span>{' '}
            profit.
          </>
        )}
        {worstSkill && Number(worstSkill.total_pnl) < 0 && (
          <>
            {' '}Consider reviewing{' '}
            <strong>{worstSkill.rule_label_zh ?? worstSkill.rule_id}</strong> (
            <span style={{ color: 'var(--negative, #E2636E)' }}>
              ${Number(worstSkill.total_pnl).toFixed(2)}
            </span>{' '}
            loss).
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PerformanceInsights – actionable recommendations based on trade data
// ---------------------------------------------------------------------------

interface InsightItem {
  type: 'success' | 'warning' | 'info';
  icon: string;
  title: string;
  message: string;
  action: string;
}

function PerformanceInsights({ summary, skills, trades }: { summary: JournalSummary; skills: SkillRow[]; trades: JournalTrade[] }) {
  const insights: InsightItem[] = [];

  // Insight 1: Best performing skill
  if (skills.length > 0) {
    const bestSkill = [...skills].sort((a, b) => Number(b.total_pnl) - Number(a.total_pnl))[0];
    if (Number(bestSkill.total_pnl) > 0) {
      insights.push({
        type: 'success',
        icon: '\uD83C\uDFC6',
        title: 'Your best skill',
        message: `${bestSkill.rule_label_zh ?? bestSkill.rule_id} generated $${Number(bestSkill.total_pnl).toFixed(2)} profit with ${Number(bestSkill.win_rate).toFixed(1)}% win rate`,
        action: `Focus more trades on ${bestSkill.rule_label_zh ?? bestSkill.rule_id}`,
      });
    }
  }

  // Insight 2: Worst performing skill
  if (skills.length > 0) {
    const worstSkill = [...skills].sort((a, b) => Number(a.total_pnl) - Number(b.total_pnl))[0];
    if (Number(worstSkill.total_pnl) < -100) {
      insights.push({
        type: 'warning',
        icon: '\u26A0\uFE0F',
        title: 'Underperforming skill',
        message: `${worstSkill.rule_label_zh ?? worstSkill.rule_id} lost $${Math.abs(Number(worstSkill.total_pnl)).toFixed(2)}`,
        action: 'Consider reviewing this skill or reducing position size',
      });
    }
  }

  // Insight 3: Best timeframe
  const tradesByInterval = trades.reduce<Record<number, JournalTrade[]>>((acc, trade) => {
    const interval = trade.interval_minutes;
    if (!acc[interval]) acc[interval] = [];
    acc[interval].push(trade);
    return acc;
  }, {});

  const intervalPerformance = Object.entries(tradesByInterval)
    .map(([interval, intervalTrades]) => {
      const totalPnl = intervalTrades.reduce((sum, t) => sum + Number(t.net_pnl ?? 0), 0);
      const winRate = (intervalTrades.filter((t) => Number(t.net_pnl) > 0).length / intervalTrades.length) * 100;
      return { interval: Number(interval), totalPnl, winRate, count: intervalTrades.length };
    })
    .sort((a, b) => b.totalPnl - a.totalPnl);

  if (intervalPerformance.length > 0 && intervalPerformance[0].totalPnl > 0) {
    const best = intervalPerformance[0];
    insights.push({
      type: 'success',
      icon: '\u23F0',
      title: 'Best timeframe',
      message: `${best.interval}m trades generated $${best.totalPnl.toFixed(2)} profit (${best.count} trades, ${best.winRate.toFixed(1)}% win rate)`,
      action: 'Consider focusing on this timeframe',
    });
  }

  // Insight 4: Win rate
  const winRate = Number(summary.win_rate);
  if (winRate >= 50 && winRate < 60) {
    insights.push({
      type: 'info',
      icon: '\u2139\uFE0F',
      title: 'Win rate',
      message: `Your ${winRate.toFixed(1)}% win rate is acceptable but could improve`,
      action: 'Review losing trades to identify patterns',
    });
  } else if (winRate > 0 && winRate < 50) {
    insights.push({
      type: 'warning',
      icon: '\u26A0\uFE0F',
      title: 'Low win rate',
      message: `Your ${winRate.toFixed(1)}% win rate is below 50%`,
      action: 'Consider adjusting entry criteria or stop losses',
    });
  }

  // Insight 5: Profit factor
  const profitFactor = summary.profit_factor ? Number(summary.profit_factor) : null;
  if (profitFactor !== null && profitFactor >= 2) {
    insights.push({
      type: 'success',
      icon: '\uD83D\uDCC8',
      title: 'Excellent profit factor',
      message: `Your profit factor of ${profitFactor.toFixed(2)} means you make $${profitFactor.toFixed(2)} for every $1 lost`,
      action: 'Keep following your current strategy',
    });
  } else if (profitFactor !== null && profitFactor < 1) {
    insights.push({
      type: 'warning',
      icon: '\u26A0\uFE0F',
      title: 'Negative profit factor',
      message: `Your profit factor of ${profitFactor.toFixed(2)} means losses exceed profits`,
      action: 'Review your risk management and position sizing',
    });
  }

  // Limit to top 3 insights to avoid overwhelm
  const topInsights = insights.slice(0, 3);

  if (topInsights.length === 0) return null;

  const borderColor = (type: InsightItem['type']) =>
    type === 'success'
      ? 'var(--positive, #36C7A0)'
      : type === 'warning'
        ? 'var(--negative, #E2636E)'
        : 'var(--primary, #3B82F6)';

  return (
    <div
      style={{
        padding: 20,
        backgroundColor: 'var(--surface-2)',
        borderRadius: 8,
        marginBottom: 20,
        border: '1px solid var(--border-subtle)',
      }}
    >
      <h3
        style={{
          fontSize: 16,
          fontWeight: 600,
          marginBottom: 16,
          color: 'var(--seed-fg, var(--text-primary))',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          margin: '0 0 16px 0',
        }}
      >
        <span>{'\uD83D\uDCA1'}</span>
        Performance Insights
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {topInsights.map((insight, idx) => (
          <div
            key={idx}
            style={{
              padding: 16,
              backgroundColor: 'var(--surface-1)',
              borderRadius: 6,
              borderLeft: `4px solid ${borderColor(insight.type)}`,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <span style={{ fontSize: 24, lineHeight: 1, flexShrink: 0 }}>{insight.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 500,
                    marginBottom: 4,
                    color: 'var(--seed-fg, var(--text-primary))',
                  }}
                >
                  {insight.title}
                </div>
                <div
                  style={{
                    fontSize: 13,
                    color: 'var(--seed-muted, var(--text-secondary))',
                    marginBottom: 8,
                    lineHeight: 1.5,
                  }}
                >
                  {insight.message}
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: 'var(--seed-primary, var(--primary))',
                    fontWeight: 500,
                    padding: '6px 10px',
                    backgroundColor: 'color-mix(in srgb, var(--seed-primary, var(--primary)) 10%, transparent)',
                    borderRadius: 4,
                    display: 'inline-block',
                  }}
                >
                  {'\u2192'} {insight.action}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty State
// ---------------------------------------------------------------------------

function EmptyState({ onNavigate }: { onNavigate: (path: string) => void }) {
  return (
    <div
      role="region"
      aria-label="Empty journal state"
      style={{
        textAlign: 'center',
        padding: '64px 24px',
        backgroundColor: 'var(--inset-bg, var(--surface-1))',
        borderRadius: 8,
        margin: '24px 0',
        border: '1px solid var(--border-subtle)',
      }}
    >
      <svg
        width="64"
        height="64"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        style={{ marginBottom: 20, opacity: 0.3 }}
        aria-hidden="true"
      >
        <path d="M3 3v18h18" />
        <path d="M7 12l4-4 4 4 4-4" />
      </svg>
      <h2
        style={{
          fontSize: 20,
          fontWeight: 500,
          marginBottom: 12,
          color: 'var(--seed-fg, var(--text-primary))',
        }}
      >
        No trades yet
      </h2>
      <p
        style={{
          fontSize: 14,
          color: 'var(--seed-muted, var(--text-secondary))',
          marginBottom: 24,
          maxWidth: 400,
          margin: '0 auto 24px',
          lineHeight: 1.5,
        }}
      >
        Your trading journal is empty. Run your first research in Strategy Lab
        to start tracking trades and analyzing performance.
      </p>
      <button
        onClick={() => onNavigate('/strategy-lab')}
        aria-label="Go to Strategy Lab to run your first research"
        style={{
          padding: '10px 20px',
          backgroundColor: 'var(--seed-primary, var(--primary))',
          color: 'white',
          border: 'none',
          borderRadius: 6,
          fontSize: 14,
          fontWeight: 500,
          cursor: 'pointer',
        }}
      >
        Go to Strategy Lab
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function JournalDashboard() {
  const router = useRouter();
  const [filters, setFilters] = useState<JournalFilters>({});
  const [appliedFilters, setAppliedFilters] = useState<JournalFilters>({});
  const [activeTab, setActiveTab] = useState<TabKey>('trades');

  const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
  const [trades, setTrades] = useState<JournalTrade[]>([]);
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [skills, setSkills] = useState<SkillRow[]>([]);

  // Load filter options on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = (await api.journal.filters()) as FilterOptions;
        if (!cancelled) setFilterOptions(data);
      } catch {
        // Filter options are non-critical; the dropdowns will just be empty.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Load trades + summary when applied filters change
  const loadTrades = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = (await api.journal.trades(appliedFilters)) as {
        trades: JournalTrade[];
        summary: JournalSummary;
        total_count: number;
      };
      setTrades(data.trades);
      setSummary(data.summary);
      setTotalCount(data.total_count);
    } catch (err) {
      setError(refusalFrom(err));
    } finally {
      setLoading(false);
    }
  }, [appliedFilters]);

  useEffect(() => {
    void loadTrades();
  }, [loadTrades]);

  // Fetch skill attribution data for the PerformanceSummary
  useEffect(() => {
    if (!summary) return;
    let cancelled = false;
    (async () => {
      try {
        const result = (await api.journal.skillAttribution(appliedFilters)) as {
          skills: SkillRow[];
        };
        if (!cancelled) setSkills(result.skills ?? []);
      } catch {
        // Non-critical; PerformanceSummary will just omit skill insights.
      }
    })();
    return () => { cancelled = true; };
  }, [summary, appliedFilters]);

  const handleApplyFilters = () => {
    setAppliedFilters({ ...filters });
  };

  const handleResetFilters = () => {
    setFilters({});
    setAppliedFilters({});
  };

  const handleDownloadCsv = async () => {
    try {
      const text = await api.journal.tradesCsv(appliedFilters);
      const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'journal-trades.csv';
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      // Silently fail for CSV download
    }
  };

  const tabStyle = (selected: boolean) => ({
    padding: '0.4rem 0.9rem',
    borderRadius: '999px',
    border: `1px solid ${selected ? 'var(--primary)' : 'var(--border-strong)'}`,
    background: selected ? 'rgba(59, 130, 246, 0.16)' : 'transparent',
    color: selected ? 'var(--text-white)' : 'var(--text-secondary)',
    fontSize: '0.875rem',
    cursor: 'pointer',
    fontWeight: selected ? 600 : 400,
  });

  return (
    <AppShell active="journal">
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
          <div>
            <h1 style={{ fontSize: '1.8rem', marginBottom: '0.25rem' }}>Trading Journal</h1>
            <p style={{ color: 'var(--text-secondary)', margin: 0, fontSize: '0.875rem' }}>
              Unified view of all strategy study trades across instruments and skills.
            </p>
          </div>
          <button
            type="button"
            className="ws-range-btn"
            aria-label="Export trades to CSV"
            onClick={() => void handleDownloadCsv()}
          >
            Export CSV
          </button>
        </div>

        {/* Filter Bar */}
        <JournalFilterBar
          filters={filters}
          onChange={setFilters}
          onApply={handleApplyFilters}
          onReset={handleResetFilters}
          options={filterOptions}
        />

        {error && (
          <div role="alert" style={{
            border: '1px solid var(--negative)',
            backgroundColor: 'rgba(226,99,110,0.1)',
            borderRadius: 6,
            padding: 12,
            marginBottom: 16,
            fontSize: 13,
          }}>
            {error}
          </div>
        )}

        {/* Empty State: shown when no trades exist and not loading */}
        {trades.length === 0 && !loading ? (
          <EmptyState onNavigate={(path) => router.push(path)} />
        ) : (
          <>
            {/* Performance Summary – plain-language interpretation */}
            {summary && skills.length > 0 && (
              <PerformanceSummary summary={summary} skills={skills} />
            )}

            {/* Performance Insights – actionable recommendations */}
            {summary && skills.length > 0 && trades.length > 0 && (
              <PerformanceInsights summary={summary} skills={skills} trades={trades} />
            )}

            {/* Summary Cards */}
            {summary && <JournalSummaryCards summary={summary} loading={loading} />}

            {/* Tab Bar */}
            <div role="tablist" aria-label="Journal views" style={{ display: 'flex', gap: '0.5rem', marginBottom: 12, flexWrap: 'wrap' }}>
              {TABS.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === tab.key}
                  aria-controls={`tabpanel-${tab.key}`}
                  id={`tab-${tab.key}`}
                  style={tabStyle(activeTab === tab.key)}
                  onClick={() => setActiveTab(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Tab Description */}
            <div
              style={{
                padding: '10px 14px',
                backgroundColor: 'var(--surface-1)',
                borderRadius: 6,
                borderLeft: '3px solid var(--primary)',
                marginBottom: '1.5rem',
              }}
            >
              <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4, color: 'var(--text-primary)' }}>
                {TAB_DESCRIPTIONS[activeTab].title}
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                {TAB_DESCRIPTIONS[activeTab].description}
              </div>
            </div>

            {/* Tab Content */}
            <div
              role="tabpanel"
              id={`tabpanel-${activeTab}`}
              aria-labelledby={`tab-${activeTab}`}
            >
              {loading && activeTab === 'trades' ? (
                <div role="status" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
                  Loading...
                </div>
              ) : (
                <>
                  {activeTab === 'trades' && (
                    <JournalTradesTable trades={trades} totalCount={totalCount} />
                  )}
                  {activeTab === 'cumulative-pnl' && (
                    <CumulativePnlChart filters={appliedFilters} />
                  )}
                  {activeTab === 'equity-curve' && (
                    <EquityCurveChart filters={appliedFilters} />
                  )}
                  {activeTab === 'skill-attribution' && (
                    <SkillAttributionPanel filters={appliedFilters} />
                  )}
                  {activeTab === 'skill-heatmap' && (
                    <SkillHeatmap filters={appliedFilters} />
                  )}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
