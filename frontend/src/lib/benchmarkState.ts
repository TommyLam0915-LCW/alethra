// A null benchmark return on its own says nothing: it can mean no benchmark was
// chosen, a chosen benchmark without bars in the run window, or a run stored
// before the requested benchmark was recorded. The run detail page must say
// which of those it is, so this stays a pure function the page can share with
// its tests.

export const BENCHMARK_METRIC_KEYS = ['benchmark_total_return_pct', 'excess_return_pct'];

export interface BenchmarkStateInput {
  metrics?: Record<string, unknown> | null;
  warnings?: string[] | null;
  methodologyParameters?: Record<string, unknown> | null;
}

export interface BenchmarkState {
  recorded: boolean;
  /** Why the comparison is absent; undefined when a benchmark return is recorded. */
  missingReason?: string;
}

export function describeBenchmark({
  metrics,
  warnings,
  methodologyParameters,
}: BenchmarkStateInput): BenchmarkState {
  const recordedMetrics = metrics ?? {};
  const recorded = BENCHMARK_METRIC_KEYS.some(
    (k) => recordedMetrics[k] !== null && recordedMetrics[k] !== undefined,
  );
  if (recorded) return { recorded: true };

  // The engine names the requested benchmark in this warning, so it is the most
  // specific explanation available.
  const warning = (warnings ?? []).find(
    (w) => typeof w === 'string' && w.startsWith('Benchmark ') && w.includes('unavailable'),
  );
  if (typeof warning === 'string') {
    return { recorded: false, missingReason: warning };
  }

  if (
    methodologyParameters &&
    Object.prototype.hasOwnProperty.call(methodologyParameters, 'benchmark_id')
  ) {
    const requested = methodologyParameters.benchmark_id;
    return {
      recorded: false,
      missingReason:
        typeof requested === 'string' && requested
          ? `Benchmark ${requested} was requested, but no benchmark return was computed for this run.`
          : 'No benchmark was requested for this run, so there is no comparison series.',
    };
  }

  return {
    recorded: false,
    missingReason: 'This run does not record whether a benchmark was requested.',
  };
}

/** The stored request itself: an id, an explicit "none", or no record at all. */
export function describeRecordedBenchmark(
  methodologyParameters: Record<string, unknown> | null | undefined,
): string {
  if (
    !methodologyParameters ||
    !Object.prototype.hasOwnProperty.call(methodologyParameters, 'benchmark_id')
  ) {
    return 'Not recorded';
  }
  const requested = methodologyParameters.benchmark_id;
  return typeof requested === 'string' && requested ? requested : 'None requested';
}
