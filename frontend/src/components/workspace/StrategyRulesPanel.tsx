'use client';

import type { ReplaySession } from './ReplayController';
import { NOT_PROVIDED, type RulesState } from './strategyRules';

const STATUS_LABELS: Record<string, string> = {
  pending: '待開始',
  active: '進行中',
  paused: '已暫停',
  completed: '已完成',
  retired: '已停用',
};

function formatScalar(value: unknown): string {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value;
  return formatValue(value);
}

/** Parameters arrive as nested objects/arrays; they are printed as-is, never expanded. */
function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  if (Array.isArray(value)) {
    return value.length === 0 ? '（空）' : value.map((v) => formatScalar(v)).join('、');
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return '（空）';
    return entries.map(([k, v]) => `${k}=${formatScalar(v)}`).join('、');
  }
  return formatScalar(value);
}

const EXPLICIT_KEYS = new Set([
  'template_type',
  'asset_ids',
  'start_date',
  'end_date',
  'initial_cash',
  'commission_rate',
  'slippage_rate',
  'timeframe',
  'dataset',
  'risk_parameters',
  'signal_parameters',
  'synthetic',
]);

function Row({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <tr>
      <td className="ws-muted">{label}</td>
      <td data-testid={testId}>{value}</td>
    </tr>
  );
}

/**
 * Read-only view of the rules a replay session actually runs on. Every line comes
 * from the session response or the matched experiment version; fields the API does
 * not carry are shown as 未提供 rather than filled with engine defaults.
 */
export default function StrategyRulesPanel({
  session,
  rules,
  hideHeader,
}: {
  session: ReplaySession | null;
  rules: RulesState;
  hideHeader?: boolean;
}) {
  const spec = rules.status === 'resolved' ? rules.version.strategy_spec ?? null : null;
  const remaining = spec
    ? Object.entries(spec).filter(([key]) => !EXPLICIT_KEYS.has(key))
    : [];

  return (
    <section className="ws-panel ws-panel-fill" data-testid="strategy-rules-panel" aria-label="策略規則">
      {!hideHeader && <div className="ws-panel-header">策略規則（唯讀）</div>}
      <div className="ws-panel-body" style={{ padding: 10 }}>
        {!session ? (
          <p className="ws-muted" data-testid="rules-no-session">
            目前沒有綁定歷史回放工作階段，因此沒有生效中的策略版本可顯示。策略規則需先由「策略與實驗」建立工作階段。
          </p>
        ) : rules.status === 'loading' ? (
          <p className="ws-muted" data-testid="rules-loading">
            正在比對版本 {rules.versionId.slice(0, 8)}…
          </p>
        ) : rules.status === 'error' ? (
          <div className="ws-message-error" role="alert" data-testid="rules-error">
            策略規則無法載入：{rules.message}
          </div>
        ) : rules.status === 'unresolved' ? (
          <div data-testid="rules-unresolved">
            <p className="ws-message-error" role="alert">
              策略規則：{NOT_PROVIDED}
            </p>
            <p className="ws-muted">
              生效版本 {rules.versionId.slice(0, 8)}。{rules.reason}
              不以此版本推斷規則內容。
            </p>
          </div>
        ) : rules.status === 'resolved' ? (
          <table className="ws-table" data-testid="rules-table">
            <tbody>
              <Row
                label="實驗"
                value={`${rules.experiment.title}（${rules.experiment.status}）`}
                testId="rules-experiment"
              />
              <Row
                label="生效版本（工作階段）"
                value={`${rules.versionId.slice(0, 8)} · v${rules.version.version_number}`}
                testId="rules-effective-version"
              />
              <Row
                label="已儲存版本（實驗）"
                value={
                  rules.experiment.current_version_id
                    ? `${rules.experiment.current_version_id.slice(0, 8)}`
                    : NOT_PROVIDED
                }
                testId="rules-saved-version"
              />
              <Row label="策略模板" value={formatScalar(spec?.template_type)} testId="rules-template" />
              <Row label="綁定標的" value={formatValue(spec?.asset_ids)} testId="rules-assets" />
              <Row
                label="研究期間"
                value={
                  spec?.start_date || spec?.end_date
                    ? `${formatScalar(spec?.start_date)} ~ ${formatScalar(spec?.end_date)}`
                    : NOT_PROVIDED
                }
                testId="rules-period"
              />
              <Row label="資料集" value={formatScalar(spec?.dataset)} testId="rules-dataset" />
              <Row
                label="資料性質"
                value={
                  spec?.synthetic === true
                    ? '合成（synthetic=true）'
                    : spec?.synthetic === false
                      ? '非合成（synthetic=false）'
                      : NOT_PROVIDED
                }
                testId="rules-data-nature"
              />
              <Row label="週期（規則）" value={formatScalar(spec?.timeframe)} testId="rules-spec-timeframe" />
              <Row label="起始虛擬資金" value={formatScalar(spec?.initial_cash)} testId="rules-initial-cash" />
              <Row label="手續費率" value={formatScalar(spec?.commission_rate)} testId="rules-commission-rate" />
              <Row label="滑點率" value={formatScalar(spec?.slippage_rate)} testId="rules-slippage-rate" />
              <Row label="風控參數" value={formatValue(spec?.risk_parameters)} testId="rules-risk-parameters" />
              <Row label="訊號參數" value={formatValue(spec?.signal_parameters)} testId="rules-signal-parameters" />
              {remaining.map(([key, value]) => (
                <Row key={key} label={`其他：${key}`} value={formatValue(value)} />
              ))}
              <Row label="設定雜湊" value={formatScalar(rules.version.config_hash)} testId="rules-config-hash" />
              <Row label="鎖定時間" value={formatScalar(rules.version.locked_at)} testId="rules-locked-at" />
              <Row
                label="人工核准時間"
                value={formatScalar(rules.version.user_approved_at)}
                testId="rules-approved-at"
              />
              <Row label="變更原因" value={formatScalar(rules.version.change_reason)} testId="rules-change-reason" />
              <Row
                label="研究假設"
                value={formatValue(rules.version.assumptions)}
                testId="rules-assumptions"
              />
            </tbody>
          </table>
        ) : (
          <p className="ws-muted" data-testid="rules-idle">
            規則查詢尚未開始（狀態：{rules.status}）。
          </p>
        )}

        {session && rules.status === 'resolved' && (
          <p className="ws-muted ws-small" data-testid="rules-mapping-note">
            對應方式：工作階段回應只給出 experiment_version_id，沒有實驗 id；此表以實驗的
            current_version_id 比對得出（生效版本 {rules.versionId.slice(0, 8)} ＝已儲存版本{' '}
            {rules.experiment.current_version_id?.slice(0, 8) ?? NOT_PROVIDED}）。
            工作階段狀態：{STATUS_LABELS[session.status] ?? session.status}。
          </p>
        )}
      </div>
    </section>
  );
}
