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
import type { BarsResponse } from './types';

const NATURE_LABELS: Record<string, string> = {
  synthetic: '模擬數據',
  live: '即時數據',
  delayed: '延遲數據',
  unknown: '數據性質未知',
};

const NATURE_CLASSES: Record<string, string> = {
  synthetic: 'ws-nature-badge ws-nature-synthetic',
  live: 'ws-nature-badge ws-nature-live',
  delayed: 'ws-nature-badge ws-nature-delayed',
  unknown: 'ws-nature-badge ws-nature-unknown',
};

export const RANGES = ['3M', '6M', '1Y', 'ALL'] as const;
export type RangeKey = (typeof RANGES)[number];

interface Props {
  data: BarsResponse | null;
  loading: boolean;
  range: RangeKey;
  onRangeChange: (range: RangeKey) => void;
}

export default function PriceChart({ data, loading, range, onRangeChange }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#131722' },
        textColor: '#b2b5be',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1e222d' },
        horzLines: { color: '#1e222d' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#758696', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#363a45' },
        horzLine: { color: '#758696', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#363a45' },
      },
      rightPriceScale: { borderColor: '#2a2e39' },
      timeScale: { borderColor: '#2a2e39', timeVisible: false },
      autoSize: false,
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: '#089981',
      downColor: '#f23645',
      borderVisible: false,
      wickUpColor: '#089981',
      wickDownColor: '#f23645',
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
        color: b.close >= b.open ? '#08998155' : '#f2364555',
      }))
    );
  }, [data]);

  const nature = data?.data_quality?.data_nature;
  const isStale = data?.data_quality?.is_stale;
  const lastBar = data && data.bars.length > 0 ? data.bars[data.bars.length - 1] : null;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', minHeight: 200 }} data-testid="price-chart">
      <div className="ws-chart-legend" data-testid="chart-legend">
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <strong style={{ color: '#fff', fontSize: 13 }}>
            {data ? `${data.symbol} · ${data.name}` : '—'}
          </strong>
          {nature && (
            <span className={NATURE_CLASSES[nature] || NATURE_CLASSES.unknown} data-testid="chart-nature-badge">
              {NATURE_LABELS[nature] || nature}
            </span>
          )}
          {isStale && (
            <span className="ws-nature-badge ws-nature-delayed" data-testid="chart-stale-badge">
              陳舊
            </span>
          )}
        </div>
        {lastBar && (
          <span style={{ color: lastBar.close >= lastBar.open ? '#089981' : '#f23645', fontWeight: 600 }}>
            {lastBar.close.toFixed(2)} {data?.currency}
          </span>
        )}
        {data && (
          <span className="ws-muted">
            {data.bar_count} 條 · 供應商 {data.provider} · 截至 {lastBar?.date || '—'}
            {data.data_quality?.adjustment ? ` · ${data.data_quality.adjustment}` : ''}
          </span>
        )}
      </div>
      <div className="ws-range-btns" data-testid="chart-range-buttons">
        {RANGES.map((r) => (
          <button
            key={r}
            className={`ws-range-btn ${r === range ? 'ws-range-btn-active' : ''}`}
            onClick={() => onRangeChange(r)}
          >
            {r === 'ALL' ? '全部' : r}
          </button>
        ))}
      </div>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {loading && (
        <div className="ws-muted" style={{ position: 'absolute', bottom: 8, left: 10 }}>
          載入數據中…
        </div>
      )}
      {!loading && data && data.bars.length === 0 && (
        <div className="ws-muted" style={{ position: 'absolute', bottom: 8, left: 10 }}>
          此日期範圍沒有數據
        </div>
      )}
    </div>
  );
}
