'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';

interface StrategyVersion {
  id: string;
  version_number: number;
  strategy_spec: any;
  locked_at: string | null;
}

interface Props {
  experimentId: string;
  currentVersionId: string | null;
  versions: StrategyVersion[];
  onChanged: () => Promise<void> | void;
}

interface PlanCell {
  cell_key: string;
  label: string;
  role: string;
  overrides: Record<string, any>;
  period_start: string;
  period_end: string;
}

interface Plan {
  kind: string;
  version_id: string;
  version_number: number;
  template_type: string;
  source_period: { start_date: string; end_date: string };
  calendar: {
    source: string;
    providers: string[];
    natures: Record<string, string>;
    first_date: string;
    last_date: string;
    bars: number;
  };
  required_bars: number;
  estimated_attempts: number;
  max_attempts: number;
  config: Record<string, number>;
  cells: PlanCell[];
  selection_rule: string;
  disclosures: string[];
  quota: {
    required: number;
    sufficient: boolean;
    unlimited?: boolean;
    limit?: number | null;
    used?: number | null;
    remaining?: number | null;
  };
  ready: boolean;
}

interface Attempt {
  id: string;
  cell_key: string;
  label: string;
  role: string;
  overrides: Record<string, any>;
  period_start: string;
  period_end: string;
  run_id: string | null;
  status: string;
  metrics: any;
  dataset_checksum: string | null;
  data_identity: any;
  error_details: string | null;
  completed_at: string | null;
}

interface Aggregate {
  scope: string;
  attempts_total: number;
  counts: Record<string, number>;
  return_stats: {
    count: number;
    median: number;
    mean: number;
    min: number;
    max: number;
    spread: number;
    positive_count: number;
  } | null;
  drawdown_median: number | null;
  best_attempt: { cell_key: string; label: string; total_return_pct: number } | null;
  worst_attempt: { cell_key: string; label: string; total_return_pct: number } | null;
  selection_rule: string;
  note: string;
  folds:
    | {
        fold: string | null;
        period: { start_date: string; end_date: string };
        selected: any;
        selected_from: string | null;
        status: string;
        total_return_pct: number | null;
      }[]
    | null;
}

interface Study {
  id: string;
  version_id: string;
  kind: string;
  status: string;
  config: Record<string, number>;
  plan: Plan;
  aggregate: Aggregate;
  error_details: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  attempts: Attempt[];
}

const KINDS = [
  { value: 'cost_scenarios', label: '成本情境（手續費 × 滑價）' },
  { value: 'period_slices', label: '期間切分（連續不重疊）' },
  { value: 'parameter_sensitivity', label: '參數敏感度（單因子 ±20%）' },
  { value: 'walk_forward', label: 'Walk-forward（訓練選參數、測試期量測）' },
];

const KIND_LABEL: Record<string, string> = Object.fromEntries(
  KINDS.map((k) => [k.value, k.label])
);

const STATUS_LABEL: Record<string, string> = {
  planned: '已計劃，未執行',
  running: '執行中',
  completed: '已完成',
  partial: '部分完成（有嘗試失敗或被跳過）',
  failed: '失敗',
  cancelled: '已取消',
};

const ATTEMPT_STATUS_LABEL: Record<string, string> = {
  pending: '未執行',
  completed: '完成',
  failed: '失敗',
  skipped: '跳過',
};

const ROLE_LABEL: Record<string, string> = {
  baseline: '基準',
  cost: '成本情境',
  slice: '期間',
  parameter: '參數變動',
  selection: '訓練期選參數',
  measurement: '測試期量測',
};

const th: React.CSSProperties = {
  padding: '6px 10px',
  textAlign: 'right',
  borderBottom: '2px solid var(--border-strong)',
  whiteSpace: 'nowrap',
  fontSize: '12px',
  color: 'var(--text-muted)',
};
const td: React.CSSProperties = {
  padding: '5px 10px',
  textAlign: 'right',
  borderBottom: '1px solid var(--border-strong)',
  whiteSpace: 'nowrap',
  fontSize: '13px',
};

function pct(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return `${value.toFixed(2)}%`;
}

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return fallback;
}

export default function RobustnessPanel({
  experimentId,
  currentVersionId,
  versions,
  onChanged,
}: Props) {
  const dailyVersions = useMemo(
    () =>
      versions.filter((v) => {
        const frequency = String(v.strategy_spec?.frequency ?? '1d');
        return frequency === '1d';
      }),
    [versions]
  );

  const [versionId, setVersionId] = useState(
    currentVersionId ?? dailyVersions[0]?.id ?? ''
  );
  const [kind, setKind] = useState('cost_scenarios');
  const [slices, setSlices] = useState('3');
  const [folds, setFolds] = useState('2');
  const [maxAttempts, setMaxAttempts] = useState('12');

  const [plan, setPlan] = useState<Plan | null>(null);
  const [planError, setPlanError] = useState('');
  const [planLoading, setPlanLoading] = useState(false);

  const [studies, setStudies] = useState<Study[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');

  const selected = studies.find((s) => s.id === selectedId) ?? studies[0] ?? null;

  const refreshStudies = async (preferId?: string) => {
    const list: Study[] = await api.robustness.list(experimentId);
    setStudies(list);
    setSelectedId((prev) => {
      const wanted = preferId ?? prev;
      if (wanted && list.some((s) => s.id === wanted)) return wanted;
      return list[0]?.id ?? null;
    });
  };

  useEffect(() => {
    refreshStudies().catch(() => setActionError('載入穩健性研究失敗'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId]);

  const requestBody = () => ({
    version_id: versionId,
    kind,
    config: {
      slices: Number(slices) || 3,
      folds: Number(folds) || 2,
      max_attempts: Number(maxAttempts) || 12,
    },
  });

  const handlePlan = async () => {
    if (!versionId) return;
    setPlanLoading(true);
    setPlanError('');
    setPlan(null);
    setNotice('');
    try {
      const result = await api.robustness.plan(experimentId, requestBody());
      setPlan(result);
    } catch (err) {
      setPlanError(describeError(err, '無法建立計劃'));
    } finally {
      setPlanLoading(false);
    }
  };

  const handleStart = async () => {
    if (!versionId) return;
    setBusy(true);
    setPlanError('');
    setActionError('');
    setNotice('');
    try {
      const study = await api.robustness.create(experimentId, {
        ...requestBody(),
        request_id: newRequestId(),
      });
      await refreshStudies(study.id);
      setNotice(
        `已建立研究（${KIND_LABEL[study.kind] ?? study.kind}），共 ${study.plan.estimated_attempts} 個嘗試；按「執行研究」才會開始回測。`
      );
    } catch (err) {
      setPlanError(describeError(err, '無法建立研究'));
    } finally {
      setBusy(false);
    }
  };

  const afterStudyChange = async (studyId: string) => {
    await refreshStudies(studyId);
    await onChanged();
  };

  const handleRun = async (study: Study) => {
    setBusy(true);
    setActionError('');
    setNotice('');
    try {
      const detail = await api.robustness.run(study.id);
      await afterStudyChange(detail.id);
      setNotice(
        `研究已執行：${detail.aggregate.counts.completed} 個嘗試完成，` +
          `${detail.aggregate.counts.failed} 個失敗，${detail.aggregate.counts.skipped} 個跳過。`
      );
    } catch (err) {
      setActionError(describeError(err, '執行研究失敗'));
      await refreshStudies(study.id).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async (study: Study) => {
    setBusy(true);
    setActionError('');
    setNotice('');
    try {
      await api.robustness.cancel(study.id);
      await afterStudyChange(study.id);
      setNotice('研究已取消；已完成的嘗試與結果仍會保留。');
    } catch (err) {
      setActionError(describeError(err, '取消研究失敗'));
    } finally {
      setBusy(false);
    }
  };

  const canRun =
    selected != null &&
    ['planned', 'partial', 'failed', 'running'].includes(selected.status);
  const canCancel =
    selected != null && ['planned', 'partial', 'failed', 'running'].includes(selected.status);

  return (
    <div className="card" style={{ marginBottom: '20px' }} data-testid="robustness-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '12px',
        }}
      >
        <h3 style={{ margin: 0 }}>進階研究與穩健性</h3>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          每次嘗試都是一次真實回測，會計入研究執行額度
        </span>
      </div>

      <p style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: 0 }}>
        先預覽計劃（嘗試數、資料日曆、額度是否足夠），再建立並執行。研究不會修改策略版本；
        全部嘗試（包括失敗與跳過）都會列出，最佳單一結果不會被當成整體穩健性。
      </p>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
          marginBottom: '12px',
        }}
      >
        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          策略版本
          <select
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={versionId}
            onChange={(e) => {
              setVersionId(e.target.value);
              setPlan(null);
              setPlanError('');
            }}
            data-testid="robustness-version"
          >
            {dailyVersions.length === 0 && <option value="">沒有日線版本</option>}
            {dailyVersions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_number}
                {v.id === currentVersionId ? '（目前）' : ''}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          研究種類
          <select
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setPlan(null);
              setPlanError('');
            }}
            data-testid="robustness-kind"
          >
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          分段數（2–4）
          <input
            type="number"
            min={2}
            max={4}
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={slices}
            onChange={(e) => setSlices(e.target.value)}
            data-testid="robustness-slices"
          />
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          折數（2–3）
          <input
            type="number"
            min={2}
            max={3}
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={folds}
            onChange={(e) => setFolds(e.target.value)}
            data-testid="robustness-folds"
          />
        </label>

        <label style={{ display: 'block', fontSize: '13px', color: 'var(--text-muted)' }}>
          嘗試上限（1–24）
          <input
            type="number"
            min={1}
            max={24}
            className="input"
            style={{ width: '100%', marginTop: '4px' }}
            value={maxAttempts}
            onChange={(e) => setMaxAttempts(e.target.value)}
            data-testid="robustness-max-attempts"
          />
        </label>
      </div>

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
        <button
          type="button"
          className="btn"
          style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
          onClick={handlePlan}
          disabled={planLoading || busy || !versionId}
          data-testid="robustness-plan"
        >
          {planLoading ? '計算中…' : '預覽計劃'}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '13px' }}
          onClick={handleStart}
          disabled={busy || !versionId || !plan?.ready}
          title={plan?.ready ? '' : '請先預覽計劃，並確認額度足夠'}
          data-testid="robustness-start"
        >
          {busy ? '處理中…' : '建立研究'}
        </button>
      </div>

      {planError && (
        <div
          role="alert"
          data-testid="robustness-plan-error"
          style={{
            padding: '10px 12px',
            backgroundColor: 'rgba(239,68,68,0.12)',
            color: 'var(--negative)',
            borderRadius: '6px',
            fontSize: '13px',
            marginBottom: '12px',
            whiteSpace: 'pre-wrap',
          }}
        >
          {planError}
        </div>
      )}

      {notice && (
        <p data-testid="robustness-notice" style={{ color: 'var(--positive)', fontSize: '13px', margin: '0 0 12px' }}>
          {notice}
        </p>
      )}

      {actionError && (
        <p role="alert" data-testid="robustness-action-error" style={{ color: 'var(--negative)', fontSize: '13px', margin: '0 0 12px' }}>
          {actionError}
        </p>
      )}

      {plan && (
        <div
          style={{ padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px', marginBottom: '16px' }}
          data-testid="robustness-plan-preview"
        >
          <div style={{ fontSize: '13px', marginBottom: '8px' }} data-testid="robustness-estimated">
            <strong>{KIND_LABEL[plan.kind] ?? plan.kind}</strong> · v{plan.version_number} ·{' '}
            預計 <strong>{plan.estimated_attempts}</strong> 個嘗試（上限 {plan.max_attempts}） ·{' '}
            每段至少需要 {plan.required_bars} 個交易日
          </div>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '8px' }}>
            資料日曆：
            {plan.calendar.source === 'stored' ? '已儲存市場資料' : '內建合成示範資料（非真實市場價格）'}
            {' · '}
            {plan.calendar.first_date} → {plan.calendar.last_date}（{plan.calendar.bars} 個交易日）
            {' · 供應商：'}
            {plan.calendar.providers.join('、') || '—'}
          </div>
          <div style={{ fontSize: '13px', marginBottom: '8px' }} data-testid="robustness-quota">
            額度：此計劃需要 {plan.quota.required} 次
            {plan.quota.unlimited
              ? '；目前方案不限次數。'
              : `；已用 ${plan.quota.used ?? '—'}／上限 ${plan.quota.limit ?? '—'}，剩餘 ${
                  plan.quota.remaining ?? '—'
                } 次。`}
            {plan.ready ? (
              <span style={{ color: 'var(--positive)' }}> 額度足夠，可以建立研究。</span>
            ) : (
              <span style={{ color: 'var(--negative)' }}>
                {' '}
                額度不足，後端會拒絕建立研究，不會執行任何回測。
              </span>
            )}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
            選取規則：{plan.selection_rule}
          </div>
          <ul style={{ margin: '6px 0 0 18px', padding: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
            {plan.disclosures.map((line, i) => (
              <li key={i} style={{ marginBottom: '3px' }}>
                {line}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ marginBottom: '16px' }} data-testid="robustness-studies">
        <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '6px' }}>
          此實驗的研究紀錄（{studies.length}）
        </div>
        {studies.length === 0 ? (
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
            尚未建立穩健性研究。先預覽計劃，再建立研究。
          </p>
        ) : (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {studies.map((s) => (
              <button
                key={s.id}
                type="button"
                className="btn"
                onClick={() => {
                  setSelectedId(s.id);
                  setActionError('');
                  setNotice('');
                }}
                data-testid={`robustness-study-${s.id}`}
                style={{
                  fontSize: '12px',
                  backgroundColor: s.id === selected?.id ? 'rgba(59,130,246,0.16)' : 'var(--surface-2)',
                  border: s.id === selected?.id ? '1px solid var(--primary)' : '1px solid var(--border-strong)',
                  textAlign: 'left',
                }}
              >
                {KIND_LABEL[s.kind] ?? s.kind}
                <br />
                <span style={{ color: 'var(--text-secondary)' }}>
                  {STATUS_LABEL[s.status] ?? s.status} · {s.attempts.length}/{s.plan.estimated_attempts} 個嘗試
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <div data-testid="robustness-detail">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: '8px',
              marginBottom: '10px',
            }}
          >
            <div style={{ fontSize: '14px' }}>
              <strong>{KIND_LABEL[selected.kind] ?? selected.kind}</strong> · v
              {selected.plan.version_number} · 狀態：{STATUS_LABEL[selected.status] ?? selected.status}
              {selected.error_details && (
                <span style={{ color: 'var(--negative)' }}> · {selected.error_details}</span>
              )}
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              {canRun && (
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ fontSize: '13px' }}
                  onClick={() => handleRun(selected)}
                  disabled={busy}
                  data-testid="robustness-run"
                >
                  {selected.status === 'running'
                    ? '繼續執行未完成的嘗試'
                    : selected.attempts.some((a) => a.status === 'completed')
                    ? '繼續／重跑未完成嘗試'
                    : '執行研究'}
                </button>
              )}
              {canCancel && (
                <button
                  type="button"
                  className="btn"
                  style={{ backgroundColor: 'var(--surface-2)', fontSize: '13px' }}
                  onClick={() => handleCancel(selected)}
                  disabled={busy}
                  data-testid="robustness-cancel"
                >
                  取消研究
                </button>
              )}
            </div>
          </div>

          {selected.attempts.length === 0 ? (
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: '0 0 12px' }}>
              尚未執行任何嘗試；按「執行研究」會逐格回測，每個嘗試都使用來源版本的規則。
            </p>
          ) : (
            <div style={{ overflowX: 'auto' }} data-testid="robustness-attempts">
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ ...th, textAlign: 'left' }}>嘗試</th>
                    <th style={th}>角色</th>
                    <th style={th}>期間</th>
                    <th style={th}>設定差異</th>
                    <th style={th}>狀態</th>
                    <th style={th}>總報酬</th>
                    <th style={th}>最大回撤</th>
                    <th style={th}>成交筆數</th>
                    <th style={th}>資料</th>
                    <th style={th}>回測</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.attempts.map((a) => {
                    const identity = a.data_identity ?? {};
                    const natures = identity.data_nature
                      ? Object.values(identity.data_nature).join('/')
                      : '—';
                    const overrides =
                      Object.keys(a.overrides ?? {}).length === 0
                        ? '原文設定'
                        : Object.entries(a.overrides)
                            .filter(([k]) => k !== 'start_date' && k !== 'end_date')
                            .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                            .join('、') || '只改期間';
                    return (
                      <tr key={a.id} data-testid={`robustness-attempt-${a.cell_key}`}>
                        <td style={{ ...td, textAlign: 'left' }}>{a.label}</td>
                        <td style={td}>{ROLE_LABEL[a.role] ?? a.role}</td>
                        <td style={td}>
                          {a.period_start} → {a.period_end}
                        </td>
                        <td style={{ ...td, textAlign: 'left', maxWidth: '240px', whiteSpace: 'normal' }}>
                          {overrides}
                        </td>
                        <td style={td}>
                          {ATTEMPT_STATUS_LABEL[a.status] ?? a.status}
                          {a.error_details ? `：${a.error_details}` : ''}
                        </td>
                        <td style={td}>{pct(a.metrics?.total_return_pct)}</td>
                        <td style={td}>{pct(a.metrics?.max_drawdown_pct)}</td>
                        <td style={td}>
                          {typeof a.metrics?.total_trades === 'number' ? a.metrics.total_trades : '—'}
                        </td>
                        <td style={{ ...td, textAlign: 'left', fontSize: '12px', color: 'var(--text-secondary)' }}>
                          {identity.data_source_used ?? '—'}／{natures}
                          {a.dataset_checksum ? (
                            <span title={a.dataset_checksum}>
                              {' '}
                              · {a.dataset_checksum.slice(0, 8)}
                            </span>
                          ) : null}
                        </td>
                        <td style={td}>
                          {a.run_id ? (
                            <Link href={`/experiments/${experimentId}/runs/${a.run_id}`} style={{ fontSize: '12px' }}>
                              查看
                            </Link>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div
            style={{ marginTop: '12px', padding: '12px', backgroundColor: 'var(--surface-2)', borderRadius: '6px' }}
            data-testid="robustness-aggregate"
          >
            <div style={{ fontSize: '13px', fontWeight: 'bold', marginBottom: '6px' }}>
              匯總（範圍：
              {selected.aggregate.scope === 'measurement_only' ? '只計測試期量測' : '全部嘗試'}）
            </div>
            {selected.aggregate.return_stats ? (
              <div style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                完成 {selected.aggregate.return_stats.count} 次 · 中位數{' '}
                {pct(selected.aggregate.return_stats.median)} · 平均{' '}
                {pct(selected.aggregate.return_stats.mean)} · 最差{' '}
                {pct(selected.aggregate.return_stats.min)} · 最好{' '}
                {pct(selected.aggregate.return_stats.max)} · 分散{' '}
                {pct(selected.aggregate.return_stats.spread)} · 正報酬{' '}
                {selected.aggregate.return_stats.positive_count}／
                {selected.aggregate.return_stats.count} · 回撤中位數{' '}
                {pct(selected.aggregate.drawdown_median)}
              </div>
            ) : (
              <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>沒有完成的嘗試，無法匯總。</div>
            )}
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '6px' }}>
              嘗試狀態：完成 {selected.aggregate.counts.completed ?? 0} · 失敗{' '}
              {selected.aggregate.counts.failed ?? 0} · 跳過{' '}
              {selected.aggregate.counts.skipped ?? 0} · 未執行{' '}
              {selected.aggregate.counts.pending ?? 0}
            </div>
            {selected.aggregate.best_attempt && (
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                最好單一結果：{selected.aggregate.best_attempt.label}（
                {pct(selected.aggregate.best_attempt.total_return_pct)}）；最差：
                {selected.aggregate.worst_attempt?.label}（
                {pct(selected.aggregate.worst_attempt?.total_return_pct)}）
              </div>
            )}
            <div style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '6px' }}>
              {selected.aggregate.note}
            </div>
            {selected.aggregate.folds && selected.aggregate.folds.length > 0 && (
              <div style={{ marginTop: '8px' }}>
                <div style={{ fontSize: '12px', fontWeight: 'bold', marginBottom: '4px' }}>各折結果</div>
                <ul style={{ margin: '0 0 0 18px', padding: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
                  {selected.aggregate.folds.map((fold, i) => (
                    <li key={i}>
                      第 {fold.fold} 折 測試期 {fold.period.start_date} → {fold.period.end_date}：{' '}
                      {fold.status === 'completed'
                        ? `報酬 ${pct(fold.total_return_pct)}`
                        : ATTEMPT_STATUS_LABEL[fold.status] ?? fold.status}
                      {fold.selected ? ` · 選用參數 ${JSON.stringify(fold.selected)}` : ''}
                      {fold.selected_from ? `（來自 ${fold.selected_from}）` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
