'use client';

import { useEffect, useRef, useState } from 'react';
import { createChart, LineSeries, ColorType, CrosshairMode, type IChartApi } from 'lightweight-charts';
import { api } from '@/lib/api';
import type { JournalFilters } from './JournalDashboard';

interface Props {
  filters: JournalFilters;
}

interface EquityPoint {
  date: string;
  equity: string;
  drawdown_pct: string;
}

interface EquitySeries {
  name: string;
  points: EquityPoint[];
}

interface EquityCurveResponse {
  baseline_cash: string;
  series: EquitySeries[];
}

const SERIES_COLORS = [
  '#4F8DF7',
  '#36C7A0',
  '#FF7585',
  '#FFB74D',
  '#AB47BC',
  '#26C6DA',
  '#EC407A',
  '#66BB6A',
];

function dateToUnix(dateStr: string): number {
  const d = new Date(dateStr);
  return Math.floor(d.getTime() / 1000);
}

export default function EquityCurveChart({ filters }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [seriesNames, setSeriesNames] = useState<string[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: 'var(--text-secondary)',
      },
      grid: {
        vertLines: { color: 'var(--border-subtle)' },
        horzLines: { color: 'var(--border-subtle)' },
      },
      width: containerRef.current.clientWidth,
      height: 400,
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    chartRef.current = chart;

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const data = (await api.journal.equityCurve({
          ...filters,
          group_by: 'instrument',
        })) as EquityCurveResponse;
        if (cancelled) return;

        const chart = chartRef.current;
        if (!chart) return;

        // Clear existing series by recreating chart
        // lightweight-charts v5 doesn't have a simple removeAllSeries, so we track them
        const names = data.series.map((s) => s.name);
        setSeriesNames(names);

        // Remove old series - we need to track them
        // For simplicity, we'll just recreate the chart content
        // by removing and re-adding the container's chart
        chart.remove();

        const newChart = createChart(containerRef.current!, {
          layout: {
            background: { type: ColorType.Solid, color: 'transparent' },
            textColor: 'var(--text-secondary)',
          },
          grid: {
            vertLines: { color: 'var(--border-subtle)' },
            horzLines: { color: 'var(--border-subtle)' },
          },
          width: containerRef.current!.clientWidth,
          height: 400,
          crosshair: { mode: CrosshairMode.Normal },
          timeScale: { timeVisible: true, secondsVisible: false },
        });
        chartRef.current = newChart;

        data.series.forEach((s, idx) => {
          if (s.points.length === 0) return;
          const color = SERIES_COLORS[idx % SERIES_COLORS.length];
          const series = newChart.addSeries(LineSeries, {
            color,
            lineWidth: 2,
            title: s.name,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          });
          const chartData = s.points.map((p) => ({
            time: dateToUnix(p.date) as any,
            value: Number(p.equity),
          }));
          series.setData(chartData);
        });

        // Fit content
        newChart.timeScale().fitContent();
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to load equity curve');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [filters]);

  return (
    <div style={{
      border: '1px solid var(--border-subtle)',
      borderRadius: 6,
      padding: 16,
      backgroundColor: 'var(--surface-1)',
    }}>
      <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600 }}>Equity Curve by Instrument</h3>

      {error && (
        <div role="alert" style={{ color: 'var(--negative)', fontSize: 13, marginBottom: 8 }}>
          {error}
        </div>
      )}

      {loading ? (
        <div role="status" style={{ height: 400, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>
          Loading chart...
        </div>
      ) : (
        <>
          <div ref={containerRef} style={{ width: '100%', height: 400 }} />
          {seriesNames.length > 0 && (
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12, fontSize: 12 }}>
              {seriesNames.map((name, idx) => (
                <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{
                    display: 'inline-block',
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    backgroundColor: SERIES_COLORS[idx % SERIES_COLORS.length],
                  }} />
                  <span style={{ color: 'var(--text-secondary)' }}>{name}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
