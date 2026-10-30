'use client';

import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from 'lightweight-charts';
import { DATA_NATURE_LABELS, PRICE_MODE_LABELS, label } from '@/lib/copy';
import type { BarsResponse } from './types';

export const RANGES = ['3M', '6M', '1Y', 'ALL'] as const;
export type RangeKey = (typeof RANGES)[number];

/** A date window the provider has declared it can serve. */
export interface ChartPeriod {
  start: string;
  end: string;
}

interface Props {
  data: BarsResponse | null;
  loading: boolean;
  range: RangeKey;
  onRangeChange: (range: RangeKey) => void;
  availablePeriod: ChartPeriod | null;
  samplePeriodActive: boolean;
  onUseSamplePeriod: () => void;
}

export default function PriceChart({
  data,
  loading,
  range,
  onRangeChange,
  availablePeriod,
  samplePeriodActive,
  onUseSamplePeriod,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#161D27' },
        textColor: '#B6C2D2',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1D2733' },
        horzLines: { color: '#1D2733' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#8FA0B5', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#3B4A5C' },
        horzLine: { color: '#8FA0B5', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#3B4A5C' },
      },
      rightPriceScale: { borderColor: '#2B3746' },
      timeScale: { borderColor: '#2B3746', timeVisible: false },
      autoSize: false,
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: '#36C7A0',
      downColor: '#FF7585',
      borderVisible: false,
      wickUpColor: '#36C7A0',
      wickDownColor: '#FF7585',
    });

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    chartRef.current = chart;
    candleRef.current = candles;
    volumeRef.current = volume;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        chart.applyOptions({
          width: Math.floor(entry.contentRect.width),
          height: Math.floor(entry.contentRect.height),
        });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current) return;
    if (!data || data.bars.length === 0) {
      candleRef.current.setData([]);
      volumeRef.current.setData([]);
      return;
    }
    candleRef.current.setData(
      data.bars.map((b) => ({
        time: b.date as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );
    volumeRef.current.setData(
      data.bars.map((b) => ({
        time: b.date as Time,
        value: b.volume ?? 0,
        color: b.close >= b.open ? '#36C7A055' : '#FF758555',
      }))
    );
  }, [data]);

  return (
    <div
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        height: '100%',
        minHeight: 200,
      }}
      data-testid="price-chart"
    >
      <div className="ws-chart-toolbar">
        {samplePeriodActive && data && availablePeriod && (
          <span className="ws-chart-sample-note" data-testid="chart-sample-note">
            Showing dataset period {availablePeriod.start} – {availablePeriod.end} (not the most recent dates)
          </span>
        )}
        <div className="ws-range-btns" data-testid="chart-range-buttons" role="group" aria-label="Date range">
          {RANGES.map((r) => (
            <button
              key={r}
              className={`ws-range-btn ${r === range && !samplePeriodActive ? 'ws-range-btn-active' : ''}`}
              onClick={() => onRangeChange(r)}
              aria-pressed={r === range && !samplePeriodActive}
            >
              {r === 'ALL' ? 'All' : r}
            </button>
          ))}
        </div>
      </div>
      <div ref={containerRef} className="ws-chart-canvas" />
      {loading && (
        <div className="ws-muted" style={{ position: 'absolute', bottom: 8, left: 10 }} role="status">
          Loading data…
        </div>
      )}
      {!loading && data && data.bars.length === 0 && (
        <div className="ws-chart-empty" data-testid="chart-empty-state">
          <div className="ws-chart-empty-title">No data in this date range</div>
          <div className="ws-chart-empty-detail">
            {data.symbol} has no {data.timeframe ?? '1d'} bars from {data.start_date} to {data.end_date}.
          </div>
          {availablePeriod ? (
            <div className="ws-chart-empty-detail">
              Available period: <strong>{availablePeriod.start} – {availablePeriod.end}</strong>
              <br />
              Source: {data.provider} |{' '}
              {label(PRICE_MODE_LABELS, data.mode === 'unknown' ? null : data.mode) === 'Not provided'
                ? label(DATA_NATURE_LABELS, data.data_quality?.data_nature)
                : label(PRICE_MODE_LABELS, data.mode)}
            </div>
          ) : (
            <div className="ws-chart-empty-detail">
              Source: {data.provider} | {label(DATA_NATURE_LABELS, data.data_quality?.data_nature)} —
              the provider did not declare an available period (UNKNOWN).
            </div>
          )}
          {availablePeriod && !samplePeriodActive && (
            <button className="ws-btn ws-btn-accent" onClick={onUseSamplePeriod} data-testid="chart-use-sample-period">
              View available sample period
            </button>
          )}
        </div>
      )}
    </div>
  );
}
