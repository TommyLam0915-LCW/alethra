'use client';

import type { PriceViewMeta } from '@/components/workspace/types';

type BadgeTone = 'replay' | 'synthetic' | 'delayed' | 'live-paper' | 'unknown';

export interface ModeMeta {
  mode?: PriceViewMeta['mode'];
  mode_label_zh?: string | null;
}

interface ResolvedMode {
  tone: BadgeTone;
  label: string;
  glyph: string;
}

/**
 * Maps the honest server-provided price-view metadata to a single, unmistakable
 * mode label. The label is the primary signal (text, not colour alone) and each
 * tone carries a distinct glyph so it never relies on red/green.
 *
 * Rule from the design brief: never render "Live" as though it were live
 * trading. A live *data* feed still drives paper simulation only, so it is
 * labelled "Live data · paper only". Unknown mode is shown as unknown — never
 * fabricated into a confident label.
 */
export function resolveMode(meta: ModeMeta | null | undefined): ResolvedMode {
  const mode = meta?.mode ?? 'unknown';

  switch (mode) {
    case 'synthetic_fixture':
      return {
        tone: 'synthetic',
        label: 'Synthetic history · not real quotes',
        glyph: '▣',
      };
    case 'historical_replay':
      return {
        tone: 'replay',
        label: 'Historical replay · not live',
        glyph: '◂◂',
      };
    case 'delayed':
      return {
        tone: 'delayed',
        label: 'Delayed · not live',
        glyph: '◷',
      };
    case 'live_realtime':
      // Live *data* still drives simulated orders here; the scope comes from the
      // caller, never from a broker configuration flag.
      return {
        tone: 'live-paper',
        label: 'Live data · paper only',
        glyph: '●',
      };
    default:
      return {
        tone: 'unknown',
        label: 'Data mode unknown',
        glyph: '?',
      };
  }
}

export default function ModeBadge({
  meta,
  executionScope,
  compact = false,
}: {
  meta: ModeMeta | null | undefined;
  /** Where this page's orders actually go, resolved by the caller from the endpoint. */
  executionScope?: string | null;
  compact?: boolean;
}) {
  const resolved = resolveMode(meta);

  const dataLabels: Record<BadgeTone, string> = {
    replay: 'Historical replay',
    synthetic: 'Synthetic data',
    delayed: 'Delayed',
    'live-paper': 'Live data',
    unknown: 'Mode unknown',
  };

  const scope = executionScope || 'Execution scope unconfirmed';
  const displayLabel = compact ? `${dataLabels[resolved.tone]} · ${scope}` : resolved.label;

  return (
    <span
      className={`mode-badge mode-badge-${resolved.tone}${compact ? ' mode-badge-compact' : ''}`}
      data-testid="mode-badge"
      data-mode={resolved.tone}
      title={`Market data: ${dataLabels[resolved.tone]} | Execution scope: ${scope}`}
    >
      <span aria-hidden="true" className="mode-badge-glyph">
        {resolved.glyph}
      </span>
      {displayLabel}
    </span>
  );
}
