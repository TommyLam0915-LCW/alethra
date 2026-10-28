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
 * Rule from the design brief: never render "Live"/"即時" as though it were live
 * trading. A live *data* feed still drives paper simulation only, so it is
 * labelled "即時數據 · 僅紙上模擬". Unknown mode is shown as unknown — never
 * fabricated into a confident label.
 */
export function resolveMode(meta: ModeMeta | null | undefined): ResolvedMode {
  const mode = meta?.mode ?? 'unknown';

  switch (mode) {
    case 'synthetic_fixture':
      return {
        tone: 'synthetic',
        label: meta?.mode_label_zh || '合成歷史回放 · 非真實行情',
        glyph: '▣',
      };
    case 'historical_replay':
      return {
        tone: 'replay',
        label: meta?.mode_label_zh || '歷史行情回放 · 非即時',
        glyph: '◂◂',
      };
    case 'delayed':
      return {
        tone: 'delayed',
        label: meta?.mode_label_zh || '延遲行情 · 非即時',
        glyph: '◷',
      };
    case 'live_realtime':
      // Live *data* still drives simulated orders here; the scope comes from the
      // caller, never from a broker configuration flag.
      return {
        tone: 'live-paper',
        label: meta?.mode_label_zh || '即時數據 · 僅紙上模擬',
        glyph: '●',
      };
    default:
      return {
        tone: 'unknown',
        label: meta?.mode_label_zh || '數據模式未知',
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
    replay: '歷史回放',
    synthetic: '合成數據',
    delayed: '延遲行情',
    'live-paper': '即時行情',
    unknown: '模式未知',
  };

  const scope = executionScope || '執行範圍未確認';
  const displayLabel = compact ? `${dataLabels[resolved.tone]} · ${scope}` : resolved.label;

  return (
    <span
      className={`mode-badge mode-badge-${resolved.tone}${compact ? ' mode-badge-compact' : ''}`}
      data-testid="mode-badge"
      data-mode={resolved.tone}
      title={`市場數據：${dataLabels[resolved.tone]}｜執行範圍：${scope}`}
    >
      <span aria-hidden="true" className="mode-badge-glyph">
        {resolved.glyph}
      </span>
      {displayLabel}
    </span>
  );
}
