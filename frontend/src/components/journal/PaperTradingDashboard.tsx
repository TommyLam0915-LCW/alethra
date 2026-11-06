'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface PaperTradingRun {
  run_id: string;
  skill: string;
  instrument_id: string;
  interval_minutes: number;
  status: string;
  initial_capital: number;
  final_capital: number | null;
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  start_date: string | null;
  end_date: string | null;
}

interface DailyReport {
  date: string;
  run_id: string;
  skill: string;
  instrument: string;
  portfolio: {
    starting_capital: number;
    current_value: number;
    daily_pnl: number;
    total_pnl: number;
  };
  trades: {
    count: number;
    wins: number;
    losses: number;
    win_rate: number;
  };
  risk: {
    max_drawdown_pct: number;
    sharpe_7d: number;
  };
}

export function PaperTradingDashboard() {
  const [runs, setRuns] = useState<PaperTradingRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [report, setReport] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadHistory();
  }, []);

  useEffect(() => {
    if (selectedRun) {
      loadDailyReport(selectedRun);
    }
  }, [selectedRun]);

  const loadHistory = async () => {
    try {
      setLoading(true);
      const data = await api.paperTrading.history(50);
      setRuns(data);
      if (data.length > 0 && !selectedRun) {
        setSelectedRun(data[0].run_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history');
    } finally {
      setLoading(false);
    }
  };

  const loadDailyReport = async (runId: string) => {
    try {
      const data = await api.paperTrading.dailyReport(runId);
      setReport(data);
    } catch (err) {
      console.error('Failed to load daily report:', err);
    }
  };

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  const formatPercent = (value: number) => {
    return `${(value * 100).toFixed(1)}%`;
  };

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
          <div
            className={`text-2xl font-bold mt-1 ${
              runs.reduce((sum, r) => sum + r.total_pnl, 0) >= 0
                ? 'text-green-600'
                : 'text-red-600'
            }`}
          >
            {formatCurrency(runs.reduce((sum, r) => sum + r.total_pnl, 0))}
          </div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <div className="text-sm text-gray-500">Total Trades</div>
          <div className="text-2xl font-bold mt-1">
            {runs.reduce((sum, r) => sum + r.total_trades, 0)}
          </div>
        </div>
      </div>

      {/* Run Selection */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="text-lg font-semibold mb-3">Select Run</h3>
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {runs.map((run) => (
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
                  <div
                    className={`font-semibold ${
                      run.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'
                    }`}
                  >
                    {formatCurrency(run.total_pnl)}
                  </div>
                  <div className="text-xs text-gray-500">
                    {run.total_trades} trades · {formatPercent(run.win_rate / 100)} WR
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
              </div>
            </button>
          ))}
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
                <div
                  className={`text-lg font-semibold ${
                    report.portfolio.total_pnl >= 0
                      ? 'text-green-600'
                      : 'text-red-600'
                  }`}
                >
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
                  {formatPercent(report.trades.win_rate / 100)}
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
                  {formatPercent(report.risk.max_drawdown_pct / 100)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500">Sharpe Ratio (7d)</div>
                <div className="text-lg font-semibold">
                  {report.risk.sharpe_7d.toFixed(2)}
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
              {runs.map((run) => (
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
                  </td>
                  <td className="px-4 py-3 text-sm text-right text-gray-900">
                    {run.total_trades}
                  </td>
                  <td className="px-4 py-3 text-sm text-right text-gray-900">
                    {formatPercent(run.win_rate / 100)}
                  </td>
                  <td
                    className={`px-4 py-3 text-sm text-right font-semibold ${
                      run.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'
                    }`}
                  >
                    {formatCurrency(run.total_pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
