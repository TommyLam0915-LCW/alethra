'use client';

import { useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { formatDateTime } from '@/lib/format';

interface StrategyVersion {
  id: string;
  version_number: number;
  strategy_spec: any;
  assumptions?: any;
  change_reason: string | null;
  config_hash: string | null;
  locked_at: string | null;
  created_at: string;
}

interface RunRow {
  id: string;
  experiment_version_id: string;
  status: string;
  metrics?: any;
  completed_at?: string | null;
}

interface RevisionMeta {
  of_version_id: string;
  of_version_number: number;
  expectation: string | null;
  changed_fields: string[];
  change_kind: string;
  outcome: string | null;
  met_expectation: boolean | null;
  outcome_recorded_at: string | null;
}

interface Props {
  experimentId: string;
  experimentStatus: string;
  currentVersionId: string | null;
  versions: StrategyVersion[];
  runs: RunRow[];
  onChanged: () => Promise<void> | void;
}

const TEMPLATES = [
  { value: 'buy_and_hold', label: '買入並持有' },
  { value: 'sma_crossover', label: '均線交叉' },
  { value: 'price_change_fixed_hold', label: '跌幅定持' },
];

const CHANGE_KIND_LABEL: Record<string, string> = {
  strategy_only: '策略設定變更',
  period_only: '只改研究期間',
  strategy_and_period: '策略設定與研究期間同時變更',
};

const IGNORED_FIELDS = new Set(['user_approved_at']);

// Defaults applied by the backend schema. Older versions predate them, so
// comparing raw objects would report schema_version/frequency as edits.
const SPEC_DEFAULTS: Record<string, any> = {
  schema_version: '1.0',
  frequency: '1d',
  signal_parameters: {},
  entry_rule: {},
  exit_rule: {},
  sizing_rule: {},
  risk_parameters: {},
  execution_timing: { execute_at: 'next_open' },
  commission_rate: 0,
  slippage_rate: 0,
  benchmark_id: null,
  currency: 'USD',
};

const SPEC_FIELDS = new Set([
  'schema_version', 'template_type', 'asset_ids', 'frequency', 'start_date',
  'end_date', 'initial_cash', 'signal_parameters', 'entry_rule', 'exit_rule',
  'sizing_rule', 'risk_parameters', 'execution_timing', 'commission_rate',
  'slippage_rate', 'benchmark_id', 'currency',
]);

function canonicalSpec(spec: any): Record<string, any> {
  const merged = { ...SPEC_DEFAULTS, ...(spec ?? {}) };
  return Object.fromEntries(Object.entries(merged).filter(([key]) => SPEC_FIELDS.has(key)));
}

function isUnsupportedSpec(spec: any): { blocked: boolean; reason: string } {
  if (!spec) return { blocked: true, reason: '來源版本缺少策略設定，無法建立修訂。' };
  const frequency = String(spec.frequency ?? '1d');
  if (frequency !== '1d') {
    return {
      blocked: true,
      reason: `此版本的頻率為 ${frequency}，目前修訂表單只支援日線（1d）。後端亦不接受其他頻率，因此不會自動降級為日線。`,
    };
  }
  if (spec.session || spec.timeframe) {
    return {
      blocked: true,
      reason: '此版本含盤中（session/timeframe）設定，超出目前修訂表單可授權的範圍。請勿在此修改，以免與執行語義不一致。',
    };
  }
  return { blocked: false, reason: '' };
}

function flatten(spec: any, prefix = ''): Record<string, any> {
  const flat: Record<string, any> = {};
  for (const [key, value] of Object.entries(spec ?? {})) {
    const path = `${prefix}${key}`;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      Object.assign(flat, flatten(value, `${path}.`));
    } else {
      flat[path] = value;
    }
  }
  return flat;
}

function diffSpecs(oldSpec: any, newSpec: any): { fields: string[]; kind: string } {
  const a = flatten(canonicalSpec(oldSpec));
  const b = flatten(canonicalSpec(newSpec));
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  // Array leaves (asset_ids) must compare by value: two identical arrays built
  // separately are different references, which reported a phantom edit.
  const serialize = (value: any) => (value === undefined ? '\u0000' : JSON.stringify(value));
  const fields = Array.from(keys)
    .filter((key) => !IGNORED_FIELDS.has(key) && serialize(a[key]) !== serialize(b[key]))
    .sort();
  const period = fields.some((f) => f === 'start_date' || f === 'end_date');
  const strategy = fields.some((f) => f !== 'start_date' && f !== 'end_date');
  const kind = period && strategy ? 'strategy_and_period' : period ? 'period_only' : 'strategy_only';
  return { fields, kind };
}

function num(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function latestCompletedRun(runs: RunRow[], versionId: string): RunRow | null {
  return (
    runs.find((r) => r.experiment_version_id === versionId && r.status === 'completed') ??
    null
  );
}

// The quota guard answers with a machine code, which is useless inline: say what
// did not happen, and where the owner can see why.
function describeError(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback;
  if (err.status === 429 && err.message === 'quota_exceeded') {
    return '本次回測沒有開始，也沒有產生結果：所屬方案的研究執行額度已用完。請於「方案與用量」查看已用與上限，或稍後再試。';
  }
  return err.message;
}

export default function RevisionPanel({
  experimentId,
  experimentStatus,
  currentVersionId,
  versions,
  runs,
  onChanged,
}: Props) {
  const revisionVersions = useMemo(
    () =>
      versions
        .filter((v) => v.assumptions?.revision)
        .sort((a, b) => b.version_number - a.version_number),
    [versions]
  );

  const defaultBaseId = currentVersionId ?? versions[0]?.id ?? '';
  const [baseId, setBaseId] = useState(defaultBaseId);
  const base = versions.find((v) => v.id === baseId) ?? versions[0] ?? null;

  const [formOpen, setFormOpen] = useState(false);
  const [templateType, setTemplateType] = useState('');
  const [assetIds, setAssetIds] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [initialCash, setInitialCash] = useState('');
  const [commissionRate, setCommissionRate] = useState('');
  const [slippageRate, setSlippageRate] = useState('');
  const [shortWindow, setShortWindow] = useState('');
  const [longWindow, setLongWindow] = useState('');
  const [lookbackDays, setLookbackDays] = useState('');
  const [thresholdPct, setThresholdPct] = useState('');
  const [holdingDays, setHoldingDays] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [stopPct, setStopPct] = useState('');
  const [targetPrice, setTargetPrice] = useState('');
  const [targetPct, setTargetPct] = useState('');
  const [changeReason, setChangeReason] = useState('');
  const [expectation, setExpectation] = useState('');
  const [requestId, setRequestId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');

  const [runningVersionId, setRunningVersionId] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState('');
  const [actionError, setActionError] = useState('');

  const [outcomeDraft, setOutcomeDraft] = useState<Record<string, { met: string; text: string }>>({});
  const [savingOutcomeId, setSavingOutcomeId] = useState<string | null>(null);

  if (!base) return null;

  const unsupported = isUnsupportedSpec(base.strategy_spec);
  const spec = base.strategy_spec ?? {};

  const seedForm = (version: StrategyVersion) => {
    const s = version.strategy_spec ?? {};
    setTemplateType(s.template_type ?? 'buy_and_hold');
    setAssetIds((s.asset_ids ?? []).join(', '));
    setStartDate(s.start_date ?? '');
    setEndDate(s.end_date ?? '');
    setInitialCash(String(s.initial_cash ?? ''));
    setCommissionRate(String(s.commission_rate ?? 0));
    setSlippageRate(String(s.slippage_rate ?? 0));
    setShortWindow(String(s.signal_parameters?.short_window ?? ''));
    setLongWindow(String(s.signal_parameters?.long_window ?? ''));
    setLookbackDays(String(s.signal_parameters?.lookback_days ?? ''));
    setThresholdPct(String(s.signal_parameters?.threshold_pct ?? ''));
    setHoldingDays(String(s.signal_parameters?.holding_days ?? ''));
    setStopPrice(String(s.risk_parameters?.stop_price ?? ''));
    setStopPct(String(s.risk_parameters?.stop_pct ?? ''));
    setTargetPrice(String(s.risk_parameters?.target_price ?? ''));
    setTargetPct(String(s.risk_parameters?.target_pct ?? ''));
    setChangeReason('');
    setExpectation('');
    setRequestId(newRequestId());
    setFormError('');
  };

  const openForm = () => {
    if (unsupported.blocked) return;
    seedForm(base);
    setFormOpen(true);
  };

  const selectBase = (id: string) => {
    setBaseId(id);
    const next = versions.find((v) => v.id === id);
    if (next && formOpen) seedForm(next);
  };

  const buildSignalParameters = (): Record<string, number> => {
    if (templateType === 'sma_crossover') {
      const params: Record<string, number> = {};
      const short = num(shortWindow);
      const long = num(longWindow);
      if (short !== null) params.short_window = short;
      if (long !== null) params.long_window = long;
      return params;
    }
    if (templateType === 'price_change_fixed_hold') {
      const params: Record<string, number> = {};
      const lookback = num(lookbackDays);
      const threshold = num(thresholdPct);
      const holding = num(holdingDays);
      if (lookback !== null) params.lookback_days = lookback;
      if (threshold !== null) params.threshold_pct = threshold;
      if (holding !== null) params.holding_days = holding;
      return params;
    }
    return {};
  };

  const buildRiskParameters = (): Record<string, number> => {
    const params: Record<string, number> = { ...(spec.risk_parameters ?? {}) };
    const entries: Array<[string, string]> = [
      ['stop_price', stopPrice],
      ['stop_pct', stopPct],
      ['target_price', targetPrice],
      ['target_pct', targetPct],
    ];
    for (const [key, raw] of entries) {
      if (raw.trim() === '') {
        delete params[key];
        continue;
      }
      const value = num(raw);
      if (value !== null) params[key] = value;
    }
    return params;
  };

  const draftSpec = {
    ...spec,
    schema_version: spec.schema_version ?? '1.0',
    template_type: templateType || spec.template_type,
    asset_ids: assetIds
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    frequency: '1d',
    start_date: startDate,
    end_date: endDate,
    initial_cash: num(initialCash) ?? spec.initial_cash,
    signal_parameters: buildSignalParameters(),
    risk_parameters: buildRiskParameters(),
    commission_rate: num(commissionRate) ?? 0,
    slippage_rate: num(slippageRate) ?? 0,
  };

  const preview = diffSpecs(spec, draftSpec);
  const baseFlat = flatten(canonicalSpec(spec));
  const draftFlat = flatten(canonicalSpec(draftSpec));

  const submitRevision = async () => {
    setFormError('');
    if (!changeReason.trim()) {
      setFormError('請填寫變更原因：之後只靠版本號無法還原當時的研究意圖。');
      return;
    }
    if (preview.fields.length === 0) {
      setFormError('目前沒有任何變更；請修改至少一個欄位。');
      return;
    }
    if (!draftSpec.asset_ids.length) {
      setFormError('請至少輸入一個研究標的。');
      return;
    }
    if (!draftSpec.start_date || !draftSpec.end_date) {
      setFormError('請填寫研究期間的開始與結束日期。');
      return;
    }

    setSubmitting(true);
    try {
      await api.revisions.create(experimentId, {
        base_version_id: base.id,
        strategy_spec: draftSpec,
        change_reason: changeReason.trim(),
        expectation: expectation.trim() || null,
        request_id: requestId || undefined,
      });
      setFormOpen(false);
      setActionError('');
      setActionNotice(
        `已建立修訂版本（來源 v${base.version_number}）。來源版本未被改寫。`
      );
      await onChanged();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : '建立修訂失敗';
      setFormError(`${detail}（輸入內容已保留，可直接重試）`);
    } finally {
      setSubmitting(false);
    }
  };

  const runVersion = async (version: VersionRowForRun) => {
    setRunningVersionId(version.id);
    setActionError('');
    setActionNotice('');
    try {
      const run = await api.runs.create(version.id);
      const ret = run?.metrics?.total_return_pct;
      setActionNotice(
        run?.status === 'completed'
          ? `v${version.version_number} 回測完成${ret != null ? `：總報酬 ${Number(ret).toFixed(2)}%` : ''}。`
          : `v${version.version_number} 回測狀態：${run?.status ?? '未知'}。`
      );
      await onChanged();
    } catch (err) {
      setActionError(describeError(err, '執行回測失敗'));
    } finally {
      setRunningVersionId(null);
    }
  };

  const saveOutcome = async (version: StrategyVersion, meta: RevisionMeta) => {
    const draft = outcomeDraft[version.id] ?? {
      met: meta.met_expectation === null ? '' : String(meta.met_expectation),
      text: meta.outcome ?? '',
    };
    if (draft.met === '' || !draft.text.trim()) {
      setActionError('請選擇是否符合預期，並填寫結論。');
      return;
    }
    setSavingOutcomeId(version.id);
    setActionError('');
    try {
      await api.revisions.recordOutcome(experimentId, version.id, {
        met_expectation: draft.met === 'true',
        outcome: draft.text.trim(),
      });
      setActionNotice(`已記錄 v${version.version_number} 的修訂結果。`);
      await onChanged();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : '記錄結果失敗';
      setActionError(detail);
    } finally {
      setSavingOutcomeId(null);
    }
  };

  return (
    <div className="card" style={{ marginBottom: '20px' }} data-testid="revision-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ margin: 0 }}>策略修訂</h3>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '13px' }}
          data-testid="revision-open"
          onClick={openForm}
          disabled={unsupported.blocked || submitting}
        >
          {formOpen ? '重新整理表格' : '建立修訂'}
        </button>
      </div>

      <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: 0 }}>
        修訂會建立新版本，來源版本不會被改寫；來源版本、變更原因與預期結果都會保留在版本紀錄中。
      </p>

      {unsupported.blocked && (
        <div
          style={{ padding: '12px', backgroundColor: 'rgba(245,158,11,0.14)', borderRadius: '6px', fontSize: '13px', color: 'var(--warning)', marginBottom: '12px' }}
          data-testid="revision-unsupported"
        >
          <strong>此版本暫不支援 UI 修訂：</strong>{unsupported.reason}
        </div>
      )}

      {!formOpen && (
        <div style={{ marginBottom: '12px' }}>
          <label htmlFor="revision-base-select" style={{ display: 'block', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
            修訂來源版本
          </label>
          <select
            id="revision-base-select"
            className="input"
            style={{ maxWidth: '260px', fontSize: '13px' }}
            value={base.id}
            onChange={(e) => selectBase(e.target.value)}
            data-testid="revision-base-select"
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_number}
                {v.id === currentVersionId ? '（目前）' : ''}
                {v.locked_at ? ' · 已鎖定' : ''}
              </option>
            ))}
          </select>
        </div>
      )}

      {formOpen && (
        <div
          style={{ padding: '16px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', marginBottom: '16px' }}
          data-testid="revision-form"
        >
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
            來源：v{base.version_number}
            {base.locked_at ? '（已鎖定，修訂後仍保留原版本）' : ''}
            {experimentStatus === 'locked' ? ' · 此實驗已鎖定，修訂不會取代目前版本指標' : ''}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
            <Field label="策略模板">
              <select
                className="input"
                value={templateType}
                onChange={(e) => setTemplateType(e.target.value)}
                data-testid="revision-template"
                style={{ width: '100%' }}
              >
                {TEMPLATES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </Field>
            <Field label="研究標的（逗號分隔）">
              <input
                className="input"
                value={assetIds}
                onChange={(e) => setAssetIds(e.target.value)}
                data-testid="revision-assets"
                style={{ width: '100%' }}
              />
            </Field>
            <Field label="開始日期">
              <input type="date" className="input" value={startDate}
                onChange={(e) => setStartDate(e.target.value)} data-testid="revision-start" style={{ width: '100%' }} />
            </Field>
            <Field label="結束日期">
              <input type="date" className="input" value={endDate}
                onChange={(e) => setEndDate(e.target.value)} data-testid="revision-end" style={{ width: '100%' }} />
            </Field>
            <Field label="初始資金">
              <input type="number" className="input" value={initialCash}
                onChange={(e) => setInitialCash(e.target.value)} data-testid="revision-cash" style={{ width: '100%' }} />
            </Field>
            <Field label="手續費率">
              <input type="number" step="0.0001" className="input" value={commissionRate}
                onChange={(e) => setCommissionRate(e.target.value)} data-testid="revision-commission" style={{ width: '100%' }} />
            </Field>
            <Field label="滑價率">
              <input type="number" step="0.0001" className="input" value={slippageRate}
                onChange={(e) => setSlippageRate(e.target.value)} data-testid="revision-slippage" style={{ width: '100%' }} />
            </Field>

            {templateType === 'sma_crossover' && (
              <>
                <Field label="短均線窗口">
                  <input type="number" className="input" value={shortWindow}
                    onChange={(e) => setShortWindow(e.target.value)} data-testid="revision-short-window" style={{ width: '100%' }} />
                </Field>
                <Field label="長均線窗口">
                  <input type="number" className="input" value={longWindow}
                    onChange={(e) => setLongWindow(e.target.value)} data-testid="revision-long-window" style={{ width: '100%' }} />
                </Field>
              </>
            )}

            {templateType === 'price_change_fixed_hold' && (
              <>
                <Field label="回看天數">
                  <input type="number" className="input" value={lookbackDays}
                    onChange={(e) => setLookbackDays(e.target.value)} data-testid="revision-lookback" style={{ width: '100%' }} />
                </Field>
                <Field label="跌幅門檻（比例）">
                  <input type="number" step="0.001" className="input" value={thresholdPct}
                    onChange={(e) => setThresholdPct(e.target.value)} data-testid="revision-threshold" style={{ width: '100%' }} />
                </Field>
                <Field label="持有天數">
                  <input type="number" className="input" value={holdingDays}
                    onChange={(e) => setHoldingDays(e.target.value)} data-testid="revision-holding" style={{ width: '100%' }} />
                </Field>
              </>
            )}

            <Field label="停損價（可留空）">
              <input type="number" step="0.01" className="input" value={stopPrice}
                onChange={(e) => setStopPrice(e.target.value)} data-testid="revision-stop-price" style={{ width: '100%' }} />
            </Field>
            <Field label="停損比例（可留空）">
              <input type="number" step="0.001" className="input" value={stopPct}
                onChange={(e) => setStopPct(e.target.value)} data-testid="revision-stop-pct" style={{ width: '100%' }} />
            </Field>
            <Field label="目標價（可留空）">
              <input type="number" step="0.01" className="input" value={targetPrice}
                onChange={(e) => setTargetPrice(e.target.value)} data-testid="revision-target-price" style={{ width: '100%' }} />
            </Field>
            <Field label="目標比例（可留空）">
              <input type="number" step="0.001" className="input" value={targetPct}
                onChange={(e) => setTargetPct(e.target.value)} data-testid="revision-target-pct" style={{ width: '100%' }} />
            </Field>
          </div>

          <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            頻率固定為日線（1d）。盤中策略目前不能由此表單建立，後端亦會拒絕非 1d 的設定，不會自動降級。
          </div>

          <div style={{ marginTop: '16px' }}>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
              變更原因（必填）
            </label>
            <textarea
              className="input"
              rows={2}
              value={changeReason}
              onChange={(e) => setChangeReason(e.target.value)}
              data-testid="revision-change-reason"
              placeholder="為何要改這個設定？"
              style={{ width: '100%', resize: 'vertical' }}
            />
          </div>

          <div style={{ marginTop: '12px' }}>
            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '4px' }}>
              預期結果（選填）
            </label>
            <textarea
              className="input"
              rows={2}
              value={expectation}
              onChange={(e) => setExpectation(e.target.value)}
              data-testid="revision-expectation"
              placeholder="改完之後，你預期會看到什麼？"
              style={{ width: '100%', resize: 'vertical' }}
            />
          </div>

          <div
            style={{ marginTop: '16px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', fontSize: '13px' }}
            data-testid="revision-preview"
          >
            <strong>
              {preview.fields.length === 0
                ? '目前沒有變更'
                : `${CHANGE_KIND_LABEL[preview.kind]} · ${preview.fields.length} 個欄位`}
            </strong>
            {preview.fields.length > 0 && (
              <div style={{ marginTop: '6px', color: 'var(--text-muted)', lineHeight: 1.6 }}>
                {preview.fields.map((field) => (
                  <div key={field}>
                    {field}：{JSON.stringify(baseFlat[field])} → {JSON.stringify(draftFlat[field])}
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: '6px', color: 'var(--text-muted)', fontSize: '12px' }}>
              資料期間、供應商與成本的差異會在執行與比較頁中顯示；如果只改期間，會標示為「只改研究期間」。
            </div>
          </div>

          {formError && (
            <p role="alert" data-testid="revision-form-error" style={{ color: 'var(--negative)', fontSize: '13px', marginTop: '12px' }}>
              {formError}
            </p>
          )}

          <div style={{ display: 'flex', gap: '8px', marginTop: '16px' }}>
            <button
              type="button"
              className="btn btn-primary"
              data-testid="revision-submit"
              onClick={submitRevision}
              disabled={submitting}
            >
              {submitting ? '建立中...' : '建立修訂'}
            </button>
            <button
              type="button"
              className="btn"
              style={{ backgroundColor: 'var(--surface-2)' }}
              onClick={() => { setFormOpen(false); setFormError(''); }}
              disabled={submitting}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {actionNotice && (
        <p data-testid="revision-notice" style={{ color: 'var(--positive)', fontSize: '13px', margin: '0 0 12px' }}>
          {actionNotice}
        </p>
      )}
      {actionError && (
        <p role="alert" data-testid="revision-action-error" style={{ color: 'var(--negative)', fontSize: '13px', margin: '0 0 12px' }}>
          {actionError}
        </p>
      )}

      {revisionVersions.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '13px', margin: 0 }}>
          尚未建立任何修訂版本。建立修訂後，可在這裡執行回測、與來源版本比較，並記錄結果是否符合預期。
        </p>
      ) : (
        <div style={{ display: 'grid', gap: '12px' }}>
          {revisionVersions.map((version) => {
            const meta = version.assumptions?.revision as RevisionMeta | undefined;
            if (!meta) return null;
            const source = versions.find((v) => v.id === meta.of_version_id) ?? null;
            const revisionRun = latestCompletedRun(runs, version.id);
            const sourceRun = source ? latestCompletedRun(runs, source.id) : null;
            const compareHref =
              revisionRun && sourceRun
                ? `/experiments/${experimentId}/runs/compare?run_a=${sourceRun.id}&run_b=${revisionRun.id}`
                : null;
            const draft = outcomeDraft[version.id] ?? {
              met: meta.met_expectation === null || meta.met_expectation === undefined ? '' : String(meta.met_expectation),
              text: meta.outcome ?? '',
            };

            return (
              <div
                key={version.id}
                data-testid={`revision-row-${version.id}`}
                style={{ padding: '14px', border: '1px solid var(--border-strong)', borderRadius: '6px', backgroundColor: 'var(--surface-1)' }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', gap: '12px' }}>
                  <div>
                    <strong>v{version.version_number}</strong>
                    <span style={{ color: 'var(--text-secondary)', marginLeft: '8px', fontSize: '13px' }}>
                      修訂自 v{meta.of_version_number}
                      {version.id === currentVersionId ? ' · 目前版本' : ''}
                    </span>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                      {CHANGE_KIND_LABEL[meta.change_kind] ?? meta.change_kind}：
                      {meta.changed_fields?.length ? meta.changed_fields.join('、') : '未記錄'}
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                      變更原因：{version.change_reason ?? '未提供'}
                    </div>
                    {meta.expectation && (
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                        預期：{meta.expectation}
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    <button
                      type="button"
                      className="btn"
                      style={{ backgroundColor: 'var(--surface-2)', fontSize: '12px' }}
                      data-testid={`revision-run-${version.id}`}
                      onClick={() => runVersion({ id: version.id, version_number: version.version_number })}
                      disabled={runningVersionId !== null}
                    >
                      {runningVersionId === version.id ? '執行中...' : '執行回測'}
                    </button>
                    {compareHref ? (
                      <Link
                        href={compareHref}
                        className="btn btn-primary"
                        style={{ fontSize: '12px', textDecoration: 'none' }}
                        data-testid={`revision-compare-${version.id}`}
                      >
                        與來源版本比較
                      </Link>
                    ) : (
                      <span style={{ fontSize: '12px', color: 'var(--text-muted)', alignSelf: 'center' }}>
                        兩邊都有完成回測後才可比較
                      </span>
                    )}
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '12px', color: 'var(--text-muted)', marginTop: '10px' }}>
                  <span>
                    修訂回測：{revisionRun ? '已完成' : '未完成'}
                    {revisionRun?.metrics?.total_return_pct != null &&
                      ` · 總報酬 ${Number(revisionRun.metrics.total_return_pct).toFixed(2)}%`}
                  </span>
                  <span>
                    來源回測：{sourceRun ? '已完成' : '未完成'}
                    {sourceRun?.metrics?.total_return_pct != null &&
                      ` · 總報酬 ${Number(sourceRun.metrics.total_return_pct).toFixed(2)}%`}
                  </span>
                </div>

                <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-strong)' }}>
                  {meta.outcome ? (
                    <div data-testid={`revision-outcome-value-${version.id}`} style={{ fontSize: '13px' }}>
                      <strong style={{ color: meta.met_expectation ? 'var(--positive)' : 'var(--warning)' }}>
                        {meta.met_expectation ? '符合預期' : '未符合預期'}
                      </strong>
                      <span style={{ marginLeft: '8px', color: 'var(--text-muted)' }}>{meta.outcome}</span>
                      {meta.outcome_recorded_at && (
                        <span style={{ marginLeft: '8px', color: 'var(--text-muted)', fontSize: '11px' }}>
                          · {formatDateTime(meta.outcome_recorded_at)}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>尚未記錄修訂結果</div>
                  )}

                  <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                    <select
                      className="input"
                      style={{ width: '140px', fontSize: '12px' }}
                      data-testid={`revision-outcome-met-${version.id}`}
                      value={draft.met}
                      onChange={(e) =>
                        setOutcomeDraft((prev) => ({
                          ...prev,
                          [version.id]: { ...draft, met: e.target.value },
                        }))
                      }
                    >
                      <option value="">是否符合預期</option>
                      <option value="true">符合預期</option>
                      <option value="false">未符合預期</option>
                    </select>
                    <input
                      className="input"
                      style={{ flex: 1, minWidth: '220px', fontSize: '12px' }}
                      placeholder="記錄結論（例如：成本下降確實改善淨報酬）"
                      data-testid={`revision-outcome-text-${version.id}`}
                      value={draft.text}
                      onChange={(e) =>
                        setOutcomeDraft((prev) => ({
                          ...prev,
                          [version.id]: { ...draft, text: e.target.value },
                        }))
                      }
                    />
                    <button
                      type="button"
                      className="btn"
                      style={{ backgroundColor: 'var(--surface-2)', fontSize: '12px' }}
                      data-testid={`revision-outcome-save-${version.id}`}
                      onClick={() => saveOutcome(version, meta)}
                      disabled={savingOutcomeId !== null}
                    >
                      {savingOutcomeId === version.id ? '儲存中...' : meta.outcome ? '更新結果' : '記錄結果'}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>{label}</label>
      {children}
    </div>
  );
}

interface VersionRowForRun {
  id: string;
  version_number: number;
}
