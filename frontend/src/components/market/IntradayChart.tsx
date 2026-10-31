'use client';

import { useMemo } from 'react';
import CandleChart, { type CandleChartRow } from '@/components/market/CandleChart';
import { candlesForChart, type IntradaySeriesPayload } from '@/lib/intradayBars';

interface Props {
  payload: IntradaySeriesPayload | null;
  identityKey: string;
}

export default function IntradayChart({ payload, identityKey }: Props) {
  const rows = useMemo<CandleChartRow[]>(
    () => (payload ? candlesForChart(payload) : []),
    [payload],
  );

  const timeAxisNote = payload?.market.timezone
    ? `Time axis: UTC (exchange timezone ${payload.market.timezone}, not converted)`
    : 'Time axis: UTC';

  return (
    <CandleChart
      rows={rows}
      identityKey={identityKey}
      timeAxisNote={timeAxisNote}
      emptyTitle="No intraday bars to show yet"
      emptyDetail="No closed or forming bars have been received; the chart is never filled with simulated prices."
      testIdPrefix="intraday"
    />
  );
}
