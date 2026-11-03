'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { chartTime, planChartUpdate } from '@/lib/intradayBars';

/** The one row shape every market chart renders, completed and forming alike. */
export interface CandleChartRow {
  open_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string | null;
  is_final: boolean;
}

/**
 * A buy/sell annotation anchored at a bar's UTC open time. Markers never
 * change the candles; they only draw the record the customer can review.
 */
export interface ChartMarker {
  time: string;
  position: 'aboveBar' | 'belowBar' | 'inBar';
  color: string;
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square';
  text?: string;
}

interface Props {
  /** Ascending, de-duplicated rows; the forming row is flagged `is_final: false`. */
  rows: CandleChartRow[];
  /** Changing this resets the chart so a new symbol cannot inherit old candles. */
  identityKey: string;
  timeAxisNote: string;
  emptyTitle: string;
  emptyDetail: string;
  /** Prefix for `-chart`, `-chart-canvas`, `-chart-empty`, `-back-to-latest`. */
  testIdPrefix: string;
  /** Buy/sell annotations; only times that land on a rendered bar are drawn. */
  markers?: ChartMarker[];
}

const UP = '#36C7A0';
const DOWN = '#FF7585';
const FORMING_UP = '#36C7A066';
const FORMING_DOWN = '#FF758566';

function toCandle(row: CandleChartRow): CandlestickData<UTCTimestamp> | null {
  const time = chartTime(row.open_time);
  if (time === null) return null;
  const up = Number(row.close) >= Number(row.open);
  const data: CandlestickData<UTCTimestamp> = {
    time: time as UTCTimestamp,
    open: Number(row.open),
    high: Number(row.high),
    low: Number(row.low),
    close: Number(row.close),
  };
  if (!row.is_final) {
    const tint = up ? FORMING_UP : FORMING_DOWN;
    data.color = tint;
    data.borderColor = tint;
    data.wickColor = tint;
  }
  return data;
}

function toVolume(row: CandleChartRow): HistogramData<UTCTimestamp> | null {
  const time = chartTime(row.open_time);
  if (time === null) return null;
  const up = Number(row.close) >= Number(row.open);
  return {
    time: time as UTCTimestamp,
    value: Number(row.volume ?? 0),
    color: !row.is_final
      ? up
        ? '#36C7A022'
        : '#FF758522'
      : up
        ? '#36C7A055'
        : '#FF758555',
  };
}

export default function CandleChart({
  rows,
  identityKey,
  timeAxisNote,
  emptyTitle,
  emptyDetail,
  testIdPrefix,
  markers,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const appliedTimesRef = useRef<number[]>([]);
  const identityRef = useRef<string>('');
  // Number of markers the plugin was actually given, not what the caller asked
  // for; the two differ when a marker's bar is outside the rendered series.
  const [appliedMarkerCount, setAppliedMarkerCount] = useState(0);

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
      timeScale: { borderColor: '#2B3746', timeVisible: true, secondsVisible: false },
      autoSize: false,
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

    chartRef.current = chart;
    candleRef.current = candles;
    volumeRef.current = volume;
    markersRef.current = createSeriesMarkers(candles, []);

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
      markersRef.current = null;
      appliedTimesRef.current = [];
      identityRef.current = '';
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleRef.current;
    const volumeSeries = volumeRef.current;
    if (!chart || !candleSeries || !volumeSeries) return;

    const identityChanged = identityRef.current !== identityKey;
    const plan = planChartUpdate(appliedTimesRef.current, rows, identityChanged);
    const byTime = new Map<number, CandleChartRow>();
    for (const row of rows) {
      const time = chartTime(row.open_time);
      if (time !== null) byTime.set(time, row);
    }

    if (plan.mode === 'reset') {
      // A full setData is unavoidable when the window moved, but the user's
      // zoom is not: capture and restore the logical range so the viewport
      // does not jump back to the right edge on a minute boundary.
      const previousRange = identityChanged ? null : chart.timeScale().getVisibleLogicalRange();
      const candleData: CandlestickData<UTCTimestamp>[] = [];
      const volumeData: HistogramData<UTCTimestamp>[] = [];
      for (const time of plan.times) {
        const row = byTime.get(time);
        if (!row) continue;
        const candle = toCandle(row);
        const volume = toVolume(row);
        if (candle) candleData.push(candle);
        if (volume) volumeData.push(volume);
      }
      candleSeries.setData(candleData);
      volumeSeries.setData(volumeData);
      appliedTimesRef.current = plan.times;
      if (previousRange) {
        try {
          chart.timeScale().setVisibleLogicalRange(previousRange);
        } catch {
          chart.timeScale().fitContent();
        }
      } else {
        chart.timeScale().fitContent();
      }
    } else {
      // `update()` only touches the newest bar, so the visible range and the
      // user's scroll position are untouched.
      for (const time of plan.times) {
        const row = byTime.get(time);
        if (!row) continue;
        const candle = toCandle(row);
        const volume = toVolume(row);
        if (candle) candleSeries.update(candle);
        if (volume) volumeSeries.update(volume);
      }
      appliedTimesRef.current = Array.from(
        new Set(appliedTimesRef.current.concat(plan.times)),
      ).sort((a, b) => a - b);
    }
    identityRef.current = identityKey;
  }, [rows, identityKey]);

  useEffect(() => {
    const plugin = markersRef.current;
    if (!plugin) return;

    // A marker outside the rendered window would invent a bar that is not on
    // screen, so only times that resolve to a visible candle are drawn.
    const barTimes = new Set<number>();
    for (const row of rows) {
      const time = chartTime(row.open_time);
      if (time !== null) barTimes.add(time);
    }

    const prepared: SeriesMarker<Time>[] = [];
    for (const marker of markers ?? []) {
      const time = chartTime(marker.time);
      if (time === null || !barTimes.has(time)) continue;
      prepared.push({
        time: time as UTCTimestamp,
        position: marker.position,
        color: marker.color,
        shape: marker.shape,
        text: marker.text,
      });
    }
    prepared.sort((a, b) => (a.time as number) - (b.time as number));
    plugin.setMarkers(prepared);
    setAppliedMarkerCount(prepared.length);
  }, [markers, rows, identityKey]);

  const scrollToLatest = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (appliedTimesRef.current.length === 0) return;
    chart.timeScale().scrollToRealTime();
  }, []);

  const empty = rows.length === 0;

  return (
    <div
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        height: '100%',
        minHeight: 320,
      }}
      data-testid={`${testIdPrefix}-chart`}
      data-marker-count={appliedMarkerCount}
    >
      <div className="ws-chart-toolbar">
        <span className="ws-muted">{timeAxisNote}</span>
        <button
          type="button"
          className="ws-range-btn"
          onClick={scrollToLatest}
          data-testid={`${testIdPrefix}-back-to-latest`}
        >
          Back to latest
        </button>
      </div>
      <div
        ref={containerRef}
        className="ws-chart-canvas"
        data-testid={`${testIdPrefix}-chart-canvas`}
      />
      {empty && (
        <div className="ws-chart-empty" data-testid={`${testIdPrefix}-chart-empty`}>
          <div className="ws-chart-empty-title">{emptyTitle}</div>
          <div className="ws-chart-empty-detail">{emptyDetail}</div>
        </div>
      )}
    </div>
  );
}
