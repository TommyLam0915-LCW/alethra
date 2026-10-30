'use client';

import { useCallback, useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  createChart,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import {
  candlesForChart,
  chartTime,
  planChartUpdate,
  type IntradayCandle,
  type IntradaySeriesPayload,
} from '@/lib/intradayBars';

interface Props {
  payload: IntradaySeriesPayload | null;
  identityKey: string;
}

const UP = '#36C7A0';
const DOWN = '#FF7585';
const FORMING_UP = '#36C7A066';
const FORMING_DOWN = '#FF758566';

function toCandle(row: IntradayCandle, forming: boolean): CandlestickData<UTCTimestamp> | null {
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
  if (forming) {
    const tint = up ? FORMING_UP : FORMING_DOWN;
    data.color = tint;
    data.borderColor = tint;
    data.wickColor = tint;
  }
  return data;
}

function toVolume(row: IntradayCandle, forming: boolean): HistogramData<UTCTimestamp> | null {
  const time = chartTime(row.open_time);
  if (time === null) return null;
  const up = Number(row.close) >= Number(row.open);
  return {
    time: time as UTCTimestamp,
    value: Number(row.volume),
    color: forming ? (up ? '#36C7A022' : '#FF758522') : up ? '#36C7A055' : '#FF758555',
  };
}

export default function IntradayChart({ payload, identityKey }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const appliedTimesRef = useRef<number[]>([]);
  const identityRef = useRef<string>('');

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
      appliedTimesRef.current = [];
      identityRef.current = '';
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleRef.current;
    const volumeSeries = volumeRef.current;
    if (!chart || !candleSeries || !volumeSeries) return;

    const rows = payload ? candlesForChart(payload) : [];
    const identityChanged = identityRef.current !== identityKey;
    const plan = planChartUpdate(appliedTimesRef.current, rows, identityChanged);
    const formingTime = payload?.series.forming
      ? chartTime(payload.series.forming.open_time)
      : null;
    const byTime = new Map<number, IntradayCandle>();
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
        const isForming = formingTime !== null && time === formingTime;
        const candle = toCandle(row, isForming);
        const volume = toVolume(row, isForming);
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
        const isForming = formingTime !== null && time === formingTime;
        const candle = toCandle(row, isForming);
        const volume = toVolume(row, isForming);
        if (candle) candleSeries.update(candle);
        if (volume) volumeSeries.update(volume);
      }
      const merged = Array.from(
        new Set(appliedTimesRef.current.concat(plan.times)),
      ).sort((a, b) => a - b);
      appliedTimesRef.current = merged;
    }
    identityRef.current = identityKey;
  }, [payload, identityKey]);

  const scrollToLatest = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const data = appliedTimesRef.current;
    if (data.length === 0) return;
    chart.timeScale().scrollToRealTime();
  }, []);

  const empty = !payload || (payload.series.completed.length === 0 && !payload.series.forming);

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
      data-testid="intraday-chart"
    >
      <div className="ws-chart-toolbar">
        <span className="ws-muted">
          {payload?.market.timezone
            ? `時間軸：UTC（交易所時區 ${payload.market.timezone}，未換算）`
            : '時間軸：UTC'}
        </span>
        <button
          type="button"
          className="ws-range-btn"
          onClick={scrollToLatest}
          data-testid="intraday-back-to-latest"
        >
          回到最新
        </button>
      </div>
      <div ref={containerRef} className="ws-chart-canvas" data-testid="intraday-chart-canvas" />
      {empty && (
        <div className="ws-chart-empty" data-testid="intraday-chart-empty">
          <div className="ws-chart-empty-title">尚無可顯示的盤中 K 線</div>
          <div className="ws-chart-empty-detail">
            未收到任何已收盤或未完成的 K 線；系統不會以模擬價格填充。
          </div>
        </div>
      )}
    </div>
  );
}
