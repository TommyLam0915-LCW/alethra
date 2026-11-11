// View model for the 年化計算基準 panel on the run detail page.
//
// Kept out of the component so the three states the owner asked to see — a
// usable basis, a basis that refuses, and an old run that never recorded one —
// are decided in one place and can be tested without a browser.

export interface AnnualizationBasis {
  available?: boolean;
  method_version?: string;
  frequency?: string;
  market?: string | null;
  interval_minutes?: number | null;
  observations_per_year?: number | null;
  trading_days_per_year?: number | null;
  bars_per_trading_day?: number | null;
  session_minutes_per_day?: number | null;
  elapsed_years?: number | null;
  first_observation?: string | null;
  last_observation?: string | null;
  observation_count?: number | null;
  expected_observations?: number | null;
  coverage_pct?: number | null;
  calendar_source?: string;
  frequency_source?: string;
  reason?: string | null;
}

export interface BasisRow {
  testid: string;
  label: string;
  value: string;
  note?: string;
}

export interface BasisDerived {
  testid: string;
  label: string;
  value: string;
  note: string;
  reason?: string;
}

export interface BasisView {
  state: 'unrecorded' | 'available' | 'unavailable';
  heading: string;
  reason?: string;
  rows: BasisRow[];
  derived: BasisDerived[];
}

const DASH = '—';

const fmt = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return DASH;
  if (typeof v === 'number') {
    return Number.isFinite(v) ? v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : String(v);
  }
  if (typeof v === 'object') return DASH;
  return String(v);
};

export const fmtStamp = (v: string | null | undefined): string => {
  if (!v) return DASH;
  if (v.length === 10) return v;
  const [day, rest] = v.split('T');
  return rest ? `${day} ${rest.slice(0, 5)}` : day;
};

const FREQUENCY_LABELS: Record<string, string> = {
  daily: '每日',
  intraday: '盤中',
  'coarser-than-daily': '長於每日',
  unknown: '未知',
};

// A derived figure is reported as what it is: the method that produced it, and
// whether an exchange calendar or only the series' own stamps stood behind the
// frequency it was scaled by.
function derivedNote(basis: AnnualizationBasis): string {
  const method = basis.method_version ?? 'unknown';
  const cadence =
    basis.frequency_source === 'calendar'
      ? `${basis.calendar_source ?? 'calendar'} 日曆`
      : basis.frequency_source === 'measured'
        ? '由樣本身期量測'
        : '頻率來源未知';
  return `derived · ${method} · ${cadence}`;
}

const DERIVED_FIELDS: [string, string][] = [
  ['annualized_return_pct', '年化報酬（%)'],
  ['annualized_volatility_pct', '年化波動（%)'],
];

export function describeBasis(
  basis: AnnualizationBasis | null | undefined,
  metrics: Record<string, unknown> = {}
): BasisView {
  // An old run carries no block at all. Saying so is the only honest option:
  // nothing here may reconstruct a basis the run never recorded.
  if (!basis || typeof basis !== 'object') {
    return { state: 'unrecorded', heading: '未記錄年化基準', rows: [], derived: [] };
  }

  const rows: BasisRow[] = [
    {
      testid: 'annualization-basis-method',
      label: '方法版本',
      value: basis.method_version ? String(basis.method_version) : DASH,
    },
    {
      testid: 'annualization-basis-market',
      label: '市場／日曆',
      value: basis.market ? String(basis.market) : '未識別市場',
      note: basis.calendar_source ?? 'no calendar source',
    },
    {
      testid: 'annualization-basis-window',
      label: '樣本起訖',
      value:
        basis.first_observation || basis.last_observation
          ? `${fmtStamp(basis.first_observation)} → ${fmtStamp(basis.last_observation)}`
          : '未記錄樣本起訖',
    },
    {
      testid: 'annualization-basis-elapsed',
      label: '經過年數',
      value: fmt(basis.elapsed_years),
    },
    {
      testid: 'annualization-basis-frequency',
      label: '觀測頻率',
      value:
        (FREQUENCY_LABELS[basis.frequency ?? 'unknown'] ?? basis.frequency ?? 'unknown') +
        (basis.interval_minutes != null ? ` · ${basis.interval_minutes} 分鐘／根` : ''),
    },
    {
      testid: 'annualization-basis-rate',
      label: '每年期數',
      value: fmt(basis.observations_per_year),
      note:
        basis.trading_days_per_year != null
          ? `每年 ${fmt(basis.trading_days_per_year)} 個交易日` +
            (basis.bars_per_trading_day != null ? ` · 每日 ${fmt(basis.bars_per_trading_day)} 根` : '')
          : undefined,
    },
    {
      testid: 'annualization-basis-coverage',
      label: '樣本數／覆蓋率',
      value:
        basis.observation_count != null
          ? `${basis.observation_count} 筆` +
            (basis.expected_observations != null ? ` / 預期 ${fmt(basis.expected_observations)}` : '')
          : DASH,
      note: basis.coverage_pct != null ? `${fmt(basis.coverage_pct)}%` : undefined,
    },
  ];

  const derived: BasisDerived[] = DERIVED_FIELDS.map(([key, label]) => {
    const raw = metrics[key];
    const isStruct = !!raw && typeof raw === 'object';
    const value = isStruct ? (raw as { value?: unknown }).value : raw;
    const reason = isStruct ? (raw as { reason?: string }).reason : undefined;
    // available=true never renders "Not computed": the basis is usable, so an
    // absent figure is shown as unknown with whatever reason the engine gave.
    return {
      testid: `metric-${key}`,
      label,
      value: value === null || value === undefined || value === '' ? DASH : fmt(value),
      note: derivedNote(basis),
      reason: typeof reason === 'string' && reason.length > 0 ? reason : undefined,
    };
  });

  return basis.available
    ? { state: 'available', heading: '可用', rows, derived }
    : {
        state: 'unavailable',
        heading: '不可用',
        reason: basis.reason && basis.reason.length > 0 ? basis.reason : '引擎未提供理由',
        rows,
        derived,
      };
}
