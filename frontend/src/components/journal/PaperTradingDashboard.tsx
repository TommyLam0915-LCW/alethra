'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { createRequestGuard } from '@/lib/latestRequest';
import { failureReasonText } from '@/lib/paperRunReason';
import { formatNumber, formatPercentPoints } from '@/lib/format';

interface PaperTradingRun {
  run_id: string;
  skill: string;
  instrument_id: string;
  interval_minutes: number;
  status: string;
  initial_capital: number;
  // Written only by the completion UPDATE, and nullable in the DDL
  // (backend/migrations/versions/p4p5t6r7n8g9_add_paper_trading_runs.py:35-38), so
  // `/history` sends null for a run that has not completed. 0 is a measurement —
  // "this run broke even" — and cannot stand in for the absence of one.
  final_capital: number | null;
  total_pnl: number | null;
  total_trades: number;
  // Percent points (62.5 means 62.5%): the writer stores `percent_ratio(...)`
  // (backend/app/services/paper_trading.py:966), not a 0-1 fraction.
  win_rate: number | null;
  start_date: string | null;
  end_date: string | null;
  run_type: string;
  // Written only by the failure branch of the replay's terminal UPDATE
  // (backend/app/services/paper_trading.py:1086) and nullable in the DDL
  // (backend/migrations/versions/f2a3b4c5d6e7_add_paper_trading_run_failure_reason.py:40),
  // so null means "this run recorded no failure" — including every run that
  // failed before the column shipped.
  failure_reason: string | null;
}

interface DailyReport {
  date: string;
  run_id: string;
  skill: string;
  instrument_id: string;
  portfolio: {
    starting_capital: number;
    current_value: number;
    daily_pnl: number;
    total_pnl: number | null;
  };
  trades: {
    count: number;
    wins: number;
    losses: number;
    // Percent points (62.5 means 62.5%), null when the day had no trades.
    win_rate: number | null;
  };
  risk: {
    // Both are `measured(...)` over nullable columns
    // (backend/app/services/paper_trading.py:1549-1550). The replay path never
    // writes `run.max_drawdown_pct`, so it reads back null rather than a
    // flattering "never drew down" 0.00%. Drawdown is percent points.
    max_drawdown_pct: number | null;
    sharpe_7d: number | null;
  };
}

// The summary cards total the page this component requests, so the window printed
// in the caption is the same constant the request sends.
const HISTORY_LIMIT = 50;

export function PaperTradingDashboard() {
  const [runs, setRuns] = useState<PaperTradingRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [report, setReport] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const historyRequestGuard = useRef(createRequestGuard()).current;
  const dailyReportRequestGuard = useRef(createRequestGuard()).current;

  useEffect(() => {
    loadHistory();
  }, []);

  useEffect(() => {
    if (selectedRun) {
      loadDailyReport(selectedRun);
    }
  }, [selectedRun]);

  const loadHistory = async () => {
    const token = historyRequestGuard.begin();
    try {
      setLoading(true);
      const data = await api.paperTrading.history(HISTORY_LIMIT);
      if (!historyRequestGuard.isCurrent(token)) return;
      setRuns(data);
      if (data.length > 0 && !selectedRun) {
        setSelectedRun(data[0].run_id);
      }
    } catch (err) {
      if (!historyRequestGuard.isCurrent(token)) return;
      setError(err instanceof Error ? err.message : 'Failed to load history');
    } finally {
      if (historyRequestGuard.isCurrent(token)) setLoading(false);
    }
  };

  const loadDailyReport = async (runId: string) => {
    const token = dailyReportRequestGuard.begin();
    try {
      const data = await api.paperTrading.dailyReport(runId);
      if (!dailyReportRequestGuard.isCurrent(token)) return;
      setReport(data);
    } catch (err) {
      if (!dailyReportRequestGuard.isCurrent(token)) return;
      console.error('Failed to load daily report:', err);
    }
  };

  // A null means nothing was measured, and JavaScript turns it into a number
  // everywhere it is read: `null / 100` is 0 and `null >= 0` is true, so an
  // unfinished run would render as a green "$0.00" that broke even and a "0.0%"
  // win rate that lost every trade. Both are claims the API deliberately withheld.
  const formatCurrency = (value: number | null | undefined) => {
    if (value === null || value === undefined || !Number.isFinite(value)) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  const pnlColorClass = (value: number | null | undefined) => {
    if (value === null || value === undefined || !Number.isFinite(value)) return 'text-gray-500';
    return value >= 0 ? 'text-green-600' : 'text-red-600';
  };

  // Nulls are skipped rather than added as 0, so the card sums the runs that have
  // a measured P&L; only when none is measured is there no total to report.
  const measuredTotalPnl = runs.reduce<number | null>(
    (sum, run) => (run.total_pnl === null ? sum : (sum ?? 0) + run.total_pnl),
    null,
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-500">Loading paper trading data...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <div className="text-red-800 font-semibold">Error</div>
        <div className="text-red-600 text-sm mt-1">{error}</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">Total Runs</div>
          <div className="text-2xl font-bold mt-1">{runs.length}</div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">Active Runs</div>
          <div className="text-2xl font-bold mt-1 text-green-600">
            {runs.filter((r) => r.status === 'running').length}
          </div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">Total PnL</div>
          <div className={`text-2xl font-bold mt-1 ${pnlColorClass(measuredTotalPnl)}`}>
            {formatCurrency(measuredTotalPnl)}
          </div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">Total Trades</div>
          <div className="text-2xl font-bold mt-1">
            {runs.reduce((sum, r) => sum + r.total_trades, 0)}
          </div>
        </div>
      </div>
      <div className="text-xs text-gray-500">
        These cards total only the latest {HISTORY_LIMIT} runs the history endpoint
        returns, and exclude runs whose P&L has not been measured.
      </div>

      {/* Run Selection */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="text-lg font-semibold mb-3">Select Run</h3>
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {runs.map((run) => {
            const reason = failureReasonText(run.status, run.failure_reason);
            return (
              <button
                key={run.run_id}
                onClick={() => setSelectedRun(run.run_id)}
                className={`w-full text-left p-3 rounded border transition ${
                  selectedRun === run.run_id
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <div className="flex justify-between items-start">
                  <div>
                    <div className="font-medium">{run.skill}</div>
                    <div className="text-sm text-gray-500">
                      {run.instrument_id} @ {run.interval_minutes}m
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`font-semibold ${pnlColorClass(run.total_pnl)}`}>
                      {formatCurrency(run.total_pnl)}
                    </div>
                    <div className="text-xs text-gray-500">
                      {run.total_trades} trades · {formatPercentPoints(run.win_rate)} WR
                    </div>
                  </div>
                </div>
                <div className="mt-2">
                  <span
                    className={`inline-block px-2 py-1 text-xs rounded ${
                      run.status === 'running'
                        ? 'bg-green-100 text-green-800'
                        : run.status === 'completed'
                        ? 'bg-blue-100 text-blue-800'
                        : 'bg-gray-100 text-gray-800'
                    }`}
                  >
                    {run.status}
                  </span>
                  {reason && (
                    <span className="block mt-1 text-xs text-red-700">{reason}</span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Daily Report */}
      {report && selectedRun && (
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-semibold mb-4">
            Daily Report - {report.skill}
          </h3>

          {/* Portfolio Summary */}
          <div className="mb-6">
            <h4 className="text-sm font-medium text-gray-700 mb-2">
              Portfolio Summary
            </h4>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-xs text-gray-500">Starting Capital</div>
                <div className="text-lg font-semibold">
                  {formatCurrency(report.portfolio.starting_capital)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Current Value</div>
                <div className="text-lg font-semibold">
                  {formatCurrency(report.portfolio.current_value)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Daily PnL</div>
                <div
                  className={`text-lg font-semibold ${
                    report.portfolio.daily_pnl >= 0
                      ? 'text-green-600'
                      : 'text-red-600'
                  }`}
                >
                  {formatCurrency(report.portfolio.daily_pnl)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Total PnL</div>
                <div className={`text-lg font-semibold ${pnlColorClass(report.portfolio.total_pnl)}`}>
                  {formatCurrency(report.portfolio.total_pnl)}
                </div>
              </div>
            </div>
          </div>

          {/* Trade Activity */}
          <div className="mb-6">
            <h4 className="text-sm font-medium text-gray-700 mb-2">
              Trade Activity
            </h4>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-xs text-gray-500">Trades</div>
                <div className="text-lg font-semibold">{report.trades.count}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Wins</div>
                <div className="text-lg font-semibold text-green-600">
                  {report.trades.wins}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Losses</div>
                <div className="text-lg font-semibold text-red-600">
                  {report.trades.losses}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Win Rate</div>
                <div className="text-lg font-semibold">
                  {formatPercentPoints(report.trades.win_rate)}
                </div>
              </div>
            </div>
          </div>

          {/* Risk Metrics */}
          <div>
            <h4 className="text-sm font-medium text-gray-700 mb-2">
              Risk Metrics
            </h4>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="text-xs text-gray-500">Max Drawdown</div>
                <div className="text-lg font-semibold text-red-600">
                  {formatPercentPoints(report.risk.max_drawdown_pct)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Sharpe Ratio (7d)</div>
                <div className="text-lg font-semibold">
                  {formatNumber(report.risk.sharpe_7d, 2)}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Runs Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-200">
          <h3 className="text-lg font-semibold">All Runs</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Skill
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Instrument
                </th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Trades
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Win Rate
                </th>
                <th className="px-4 py-2 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  PnL
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {runs.map((run) => {
                const reason = failureReasonText(run.status, run.failure_reason);
                return (
                  <tr key={run.run_id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm font-medium text-gray-900">
                      {run.skill}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500">
                      {run.instrument_id}
                      <br />
                      <span className="text-xs">{run.interval_minutes}m</span>
                    </td>
                    <td className="px-4 py-3 text-sm">
                      <span
                        className={`inline-block px-2 py-1 text-xs rounded ${
                          run.status === 'running'
                            ? 'bg-green-100 text-green-800'
                            : run.status === 'completed'
                            ? 'bg-blue-100 text-blue-800'
                            : 'bg-gray-100 text-gray-800'
                        }`}
                      >
                        {run.status}
                      </span>
                      {reason && (
                        <span className="block mt-1 text-xs text-red-700">{reason}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-right text-gray-900">
                      {run.total_trades}
                    </td>
                    <td className="px-4 py-3 text-sm text-right text-gray-900">
                      {formatPercentPoints(run.win_rate)}
                    </td>
                    <td
                      className={`px-4 py-3 text-sm text-right font-semibold ${pnlColorClass(
                        run.total_pnl
                      )}`}
                    >
                      {formatCurrency(run.total_pnl)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
