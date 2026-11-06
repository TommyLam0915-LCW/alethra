'use client';

import { useEffect, useRef, useState } from 'react';
import { createChart, LineSeries, LineStyle, ColorType, CrosshairMode, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import { api } from '@/lib/api';
import type { JournalFilters } from './JournalDashboard';

interface Props {
  filters: JournalFilters;
}

interface CumPnlPoint {
  trade_date: string;
  net_pnl: string;
  cumulative_pnl: string;
  trade_id: string;
  instrument_id: string;
  rule_label_zh: string | null;
}

interface CumPnlResponse {
  points: CumPnlPoint[];
  total_pnl: string;
}

function dateToUnix(dateStr: string): number {
  const d = new Date(dateStr);
  return Math.floor(d.getTime() / 1000);
}

export default function CumulativePnlChart({ filters }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [totalPnl, setTotalPnl] = useState<string>('0');

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

    const series = chart.addSeries(LineSeries, {
      color: '#4F8DF7',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    chartRef.current = chart;
    seriesRef.current = series;

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
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const data = (await api.journal.cumulativePnl(filters)) as CumPnlResponse;
        if (cancelled) return;

        setTotalPnl(data.total_pnl);

        if (seriesRef.current && data.points.length > 0) {
          const chartData = data.points.map((p) => ({
            time: dateToUnix(p.trade_date) as any,
            value: Number(p.cumulative_pnl),
          }));
          seriesRef.current.setData(chartData);
          seriesRef.current.createPriceLine({
            price: 0,
            color: '#888',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: 'Break-even',
          });
        }
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to load cumulative P&L');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [filters]);

  const pnlNum = Number(totalPnl);
  const pnlColor = pnlNum >= 0 ? 'var(--positive, #36C7A0)' : 'var(--negative, #E2636E)';

  return (
    <div style={{
      border: '1px solid var(--border-subtle)',
      borderRadius: 6,
      padding: 16,
      backgroundColor: 'var(--surface-1)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>Cumulative P&L</h3>
        {!loading && (
          <span style={{ fontSize: 16, fontWeight: 600, color: pnlColor }}>
            ${pnlNum.toFixed(2)}
          </span>
        )}
      </div>

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
        <div ref={containerRef} style={{ width: '100%', height: 400 }} />
      )}
    </div>
  );
}
