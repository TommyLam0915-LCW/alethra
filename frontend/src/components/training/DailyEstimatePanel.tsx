'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
  CONSENT_PROVENANCE_ZH,
  ESTIMATE_STATE_LABELS_ZH,
  NEVER_ESTIMATED_NOTE_ZH,
  UNCONFIRMED_NOTE_ZH,
  WITHDRAWAL_TIMING_ZH,
  estimateDisplay,
  estimateState,
  failureNotice,
  pressNotice,
  type FailureRow,
} from '@/lib/dailyEstimate';

interface SkillScope {
  skill_unit_id: string;
  instrument_ids: string[];
  max_data_age_days: number | null;
}

interface Consent {
  enabled: boolean;
  purpose: string;
  consent_id: string | null;
  consent_version: string | null;
  granted_at: string | null;
  skill_units: SkillScope[];
  limits: { skill_units_per_owner: number; instruments_per_skill: number };
  not_for: string[];
}

interface LastEstimate {
  skill_unit_id: string;
  instrument_id: string;
  inference_id: string;
  model_version_id: string;
  artifact_sha256: string | null;
  as_of_date: string;
  feature_bar_date: string | null;
  /** The four fields below are the run's own reading of the market. A row
   *  rebuilt from the billing ledger does not carry them, which is why they are
   *  optional here rather than nullable: absent is not the same as unknown. */
  data_age_days?: number | null;
  max_data_age_days?: number | null;
  provider_id?: string | null;
  data_nature?: string | null;
  predicted_action: string | null;
  /** False only on an entry read back off the bill: the estimate exists and was
   *  paid for once, and its verdict lives in the run that made it, not in the
   *  billing row. */
  verdict_durable?: boolean;
  billed_at?: string | null;
}

interface LastRefusal {
  skill_unit_id: string;
  instrument_id?: string;
  reason: string;
  refused_by?: string | null;
  detail?: string | null;
  http_status?: number | null;
  message_zh?: string;
}

interface LastRun {
  job_id: string;
  window: string | null;
  scope: string | null;
  status: string;
  completed_at: string | null;
}

interface SkillState {
  skill_unit_id: string;
  declared: boolean;
  instrument_ids: string[];
  max_data_age_days: number | null;
  skill_status: string | null;
  enabled: boolean;
  disabled_reason: string | null;
  current_model_version_id: string | null;
  last_run: LastRun | null;
  last_estimate: LastEstimate | null;
  last_refusal: LastRefusal | null;
  last_failure: FailureRow | null;
  /** The UTC day this card describes, and whether a run opened it without
   *  leaving a bill, an estimate or a refusal behind. A claim row proves the tick
   *  existed — never that it is still going. */
  window: string;
  window_claimed: boolean;
  estimate_matches_current_selection: boolean;
  not_for: string[];
  disclaimer_zh: string;
}

interface SkillAccount {
  id: string;
  name?: string;
  status?: string;
}

const DISABLED_LABELS: Record<string, string> = {
  not_declared: '目前未啟用（從未聲明）',
  consent_withdrawn: '本人已撤回（先前曾聲明）',
  skill_paused: '此技能帳號已暫停',
  skill_retired: '此技能帳號已退休',
  skill_completed: '此技能帳號已完成',
  skill_status_unreadable: '無法辨識此技能帳號的狀態',
  skill_unit_not_found: '找不到此技能帳號',
};

export default function DailyEstimatePanel() {
  const [consent, setConsent] = useState<Consent | null>(null);
  const [skills, setSkills] = useState<SkillState[]>([]);
  const [accounts, setAccounts] = useState<SkillAccount[]>([]);
  const [draft, setDraft] = useState<Record<string, { instruments: string; maxAge: string }>>({});
  const [named, setNamed] = useState<string[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [consentState, statusState, portfolios] = await Promise.all([
        api.ml.dailyEstimateConsent(),
        api.ml.dailyEstimateStatus(),
        api.paperPortfolio.list().catch(() => []),
      ]);
      setConsent(consentState);
      setSkills(statusState.skills || []);
      const list = Array.isArray(portfolios) ? portfolios : portfolios?.items || [];
      setAccounts(list);
      setNamed(
        ((consentState.skill_units || []) as SkillScope[]).map((unit) => unit.skill_unit_id),
      );
      setDraft((current) => {
        const next = { ...current };
        for (const unit of consentState.skill_units || []) {
          next[unit.skill_unit_id] = {
            instruments: (unit.instrument_ids || []).join(', '),
            maxAge: unit.max_data_age_days == null ? '' : String(unit.max_data_age_days),
          };
        }
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法載入每日估計範圍');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleNamed = (skillUnitId: string) => {
    setNamed((current) =>
      current.includes(skillUnitId)
        ? current.filter((value) => value !== skillUnitId)
        : [...current, skillUnitId],
    );
    setDraft((current) => ({
      ...current,
      [skillUnitId]: current[skillUnitId] || { instruments: '', maxAge: '' },
    }));
  };

  const save = async () => {
    if (busy || named.length === 0 || !acknowledged) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const skillUnits = named.map((skillUnitId) => {
        const entry = draft[skillUnitId] || { instruments: '', maxAge: '' };
        const maxAge = entry.maxAge.trim() === '' ? null : Number(entry.maxAge);
        return {
          skill_unit_id: skillUnitId,
          instrument_ids: entry.instruments
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean),
          ...(maxAge === null ? {} : { max_data_age_days: maxAge }),
        };
      });
      await api.ml.grantDailyEstimateConsent(skillUnits);
      // The write only answers for the accounts in the scope. Reloading keeps the
      // accounts outside it on the list, because "未聲明（預設關閉）" is a fact the
      // owner has to be able to see right after saving.
      await load();
      setNotice('已記錄每日估計範圍。每日服務只在這個 UTC 日為這些技能帳號各產生一次估計。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法記錄每日估計範圍');
    } finally {
      setBusy(false);
    }
  };

  const withdraw = async (skillUnitId?: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.ml.revokeDailyEstimateConsent(skillUnitId);
      await load();
      // The route answers whether a training-consent row is live; that is a
      // present/absent fact, not a promise that nothing else moved, so the
      // panel says which of the two it knows.
      const training =
        typeof result?.training_consent_untouched === 'boolean'
          ? result.training_consent_untouched
            ? '訓練同意：仍有有效列。'
            : '訓練同意：目前沒有有效列。'
          : '';
      setNotice(
        (skillUnitId
          ? `已撤回技能帳號 ${skillUnitId.slice(0, 8)}… 的每日估計；其他帳號的聲明範圍未改變，狀態以下方重新讀取為準。`
          : '已撤回全部每日估計；從這次撤回之後開始的排程與手動要求都不再為任何帳號定價。') +
          WITHDRAWAL_TIMING_ZH +
          training,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法撤回每日估計');
    } finally {
      setBusy(false);
    }
  };

  const runNow = async (skillUnitId: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.ml.runDailyEstimate(skillUnitId);
      await load();
      // The 2xx only says the attempt row was created. What that attempt
      // brought back is the job's own counts plus the reason this account's
      // status reports -- so the facts are assembled here and worded in the lib.
      const facts = {
        estimated: result.job?.estimated ?? 0,
        refused: result.job?.refused ?? 0,
        failed: result.job?.failed ?? 0,
        refusal_reason: result.status?.last_refusal?.reason ?? null,
      };
      setNotice(pressNotice(facts));
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法執行每日估計');
    } finally {
      setBusy(false);
    }
  };

  const instrumentError = named.some(
    (skillUnitId) =>
      (draft[skillUnitId]?.instruments || '')
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean).length === 0,
  );
  const limits = consent?.limits;
  const tooMany = limits ? named.length > limits.skill_units_per_owner : false;

  return (
    <section
      className="card"
      data-testid="daily-estimate-panel"
      style={{ marginBottom: '32px' }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          gap: '16px',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: '18px' }}>每日模型估計</h2>
          <p style={{ color: 'var(--text-secondary)', margin: '6px 0 0', fontSize: '13px' }}>
            預設關閉。只有在下面被點名的技能帳號，每天 UTC 一個視窗估計一次；產出的是對你本人決策的估計，
            不會下單、不會記錄決策，也不會替你執行任何交易。
          </p>
        </div>
        <span
          data-testid="daily-estimate-state"
          style={{
            fontSize: '12px',
            padding: '2px 10px',
            borderRadius: '10px',
            border: '1px solid currentColor',
            color: consent?.enabled ? 'var(--positive)' : 'var(--text-muted)',
          }}
        >
          {consent?.enabled ? `每日服務已啟用（${consent.skill_units.length} 個技能帳號）` : '每日服務未啟用'}
        </span>
      </div>

      {error && (
        <p data-testid="daily-estimate-error" style={{ color: 'var(--negative)', fontSize: '13px' }}>
          {error}
        </p>
      )}
      {notice && (
        <p data-testid="daily-estimate-notice" style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
          {notice}
        </p>
      )}

      <div
        data-testid="daily-estimate-status-list"
        style={{ marginTop: '16px', display: 'grid', gap: '10px' }}
      >
        {skills.length === 0 && (
          <p data-testid="daily-estimate-no-skills" style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            此帳號還沒有技能帳號。先在「個人策略學習」建立一個技能帳號並選定模型版本。
          </p>
        )}
        {skills.map((state) => {
          const display = state.last_estimate
            ? estimateDisplay(state.last_estimate)
            : null;
          const windowState = estimateState({
            has_estimate: state.last_estimate != null,
            reused: display?.reused ?? false,
            refused: state.last_refusal != null,
            window_claimed: state.window_claimed === true,
          });
          return (
          <div
            key={state.skill_unit_id}
            data-testid={`daily-estimate-skill-${state.skill_unit_id}`}
            style={{
              padding: '12px 14px',
              border: '1px solid var(--border)',
              borderRadius: '8px',
              background: 'var(--surface-2)',
            }}
          >
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
              <strong style={{ fontFamily: 'monospace', fontSize: '13px' }}>
                {state.skill_unit_id.slice(0, 8)}…
              </strong>
              <span
                data-testid={`daily-estimate-enabled-${state.skill_unit_id}`}
                style={{
                  fontSize: '11px',
                  padding: '1px 8px',
                  borderRadius: '10px',
                  border: '1px solid currentColor',
                  color: state.enabled ? 'var(--positive)' : 'var(--warning)',
                }}
              >
                {state.enabled
                  ? '每日排程已開啟'
                  : state.disabled_reason === 'not_declared' ||
                    state.disabled_reason === 'consent_withdrawn'
                    ? DISABLED_LABELS[state.disabled_reason]
                    : `未執行：${DISABLED_LABELS[state.disabled_reason || ''] || state.disabled_reason}`}
              </span>
              <span
                data-testid={`daily-estimate-state-${state.skill_unit_id}`}
                style={{
                  fontSize: '11px',
                  padding: '1px 8px',
                  borderRadius: '10px',
                  border: '1px solid var(--border)',
                  color:
                    windowState === 'refused' ||
                    windowState === 'never_estimated' ||
                    windowState === 'unconfirmed'
                      ? 'var(--text-secondary)'
                      : 'var(--positive)',
                }}
              >
                {ESTIMATE_STATE_LABELS_ZH[windowState]}
              </span>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                技能狀態：{state.skill_status || '未知'}
              </span>
              {state.declared && (
                <button
                  type="button"
                  className="btn"
                  data-testid={`daily-estimate-run-${state.skill_unit_id}`}
                  onClick={() => runNow(state.skill_unit_id)}
                  disabled={busy}
                  style={{ marginLeft: 'auto', fontSize: '12px', padding: '2px 10px' }}
                >
                  立即估計
                </button>
              )}
              {state.declared && (
                <button
                  type="button"
                  className="btn"
                  data-testid={`daily-estimate-withdraw-${state.skill_unit_id}`}
                  onClick={() => withdraw(state.skill_unit_id)}
                  disabled={busy}
                  style={{ fontSize: '12px', padding: '2px 10px' }}
                >
                  撤回此帳號
                </button>
              )}
            </div>

            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '8px' }}>
              <div>
                聲明範圍：{state.instrument_ids.length ? state.instrument_ids.join('、') : '（無標的）'}
                {state.declared &&
                  ` · 新鮮度上限 ${state.max_data_age_days == null ? '未設定' : `${state.max_data_age_days} 天`}`}
              </div>
              <div style={{ marginTop: '4px', fontFamily: 'monospace' }}>
                目前選用模型版本：{state.current_model_version_id || '尚未選擇'}
              </div>
            </div>

            {state.last_run && (
              <div
                data-testid={`daily-estimate-last-run-${state.skill_unit_id}`}
                style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px' }}
              >
                最近一次：{state.last_run.window || '手動要求'} · {state.last_run.scope} ·{' '}
                {state.last_run.status}
                {state.last_run.completed_at ? ` · ${state.last_run.completed_at}` : ''}
              </div>
            )}

            {state.last_estimate && display && (
              <div
                data-testid={`daily-estimate-last-${state.skill_unit_id}`}
                style={{
                  marginTop: '8px',
                  padding: '8px 10px',
                  borderRadius: '6px',
                  border: '1px solid var(--border)',
                  background: 'var(--surface)',
                  fontSize: '12px',
                }}
              >
                <div style={{ fontWeight: 600 }}>
                  最後估計：{state.last_estimate.instrument_id}
                  {display.verdict}
                </div>
                {display.reusedNote && (
                  <div
                    data-testid={`daily-estimate-reused-${state.skill_unit_id}`}
                    style={{
                      marginTop: '4px',
                      padding: '4px 6px',
                      borderRadius: '4px',
                      border: '1px solid var(--border)',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    {display.reusedNote}
                  </div>
                )}
                <div style={{ fontFamily: 'monospace', marginTop: '4px', color: 'var(--text-secondary)' }}>
                  inference_id {state.last_estimate.inference_id}
                </div>
                <div style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>
                  模型版本 {state.last_estimate.model_version_id} · artifact{' '}
                  {(state.last_estimate.artifact_sha256 || '').slice(0, 12)}…
                </div>
                <div style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>
                  決策時點 {state.last_estimate.as_of_date} · 資料截至{' '}
                  {state.last_estimate.feature_bar_date || '未知'}（{display.dataAge}）
                  {state.last_estimate.provider_id
                    ? ` · 來源 ${state.last_estimate.provider_id}/${state.last_estimate.data_nature || '未知'}`
                    : ''}
                </div>
                {!state.estimate_matches_current_selection && (
                  <div
                    data-testid={`daily-estimate-version-drift-${state.skill_unit_id}`}
                    style={{ color: 'var(--warning)', marginTop: '6px' }}
                  >
                    此估計固定在你後來已替換的模型版本上；它不是你現在所選模型的輸出。
                  </div>
                )}
              </div>
            )}

            {state.last_refusal && (
              <div
                data-testid={`daily-estimate-refusal-${state.skill_unit_id}`}
                style={{ color: 'var(--warning)', marginTop: '8px', fontSize: '12px' }}
              >
                本次拒絕原因：{state.last_refusal.message_zh || state.last_refusal.reason}（
                <span style={{ fontFamily: 'monospace' }}>{state.last_refusal.reason}</span>）
                {state.last_refusal.instrument_id ? ` · 標的 ${state.last_refusal.instrument_id}` : ''}
                {' · '}這條拒絕本身沒有新增扣額；這個視窗是否已經付過一次，看的是上面「最後估計」那一列的 inference_id，不是這條拒絕。
              </div>
            )}

            {state.last_failure && (
              <div
                data-testid={`daily-estimate-failure-${state.skill_unit_id}`}
                style={{ color: 'var(--negative)', marginTop: '8px', fontSize: '12px' }}
              >
                {failureNotice(state.last_failure)}（
                <span style={{ fontFamily: 'monospace' }}>{state.last_failure.reason}</span>）
              </div>
            )}

            {windowState === 'never_estimated' && (
              <div
                data-testid={`daily-estimate-none-${state.skill_unit_id}`}
                style={{
                  marginTop: '8px',
                  fontSize: '12px',
                  color: 'var(--text-secondary)',
                  padding: '6px 8px',
                  border: '1px dashed var(--border)',
                  borderRadius: '6px',
                }}
              >
                {NEVER_ESTIMATED_NOTE_ZH}
              </div>
            )}

            {windowState === 'unconfirmed' && (
              <div
                data-testid={`daily-estimate-unconfirmed-${state.skill_unit_id}`}
                style={{
                  marginTop: '8px',
                  fontSize: '12px',
                  color: 'var(--text-secondary)',
                  padding: '6px 8px',
                  border: '1px dashed var(--border)',
                  borderRadius: '6px',
                }}
              >
                {UNCONFIRMED_NOTE_ZH}
                {' '}（視窗 {state.window}）
              </div>
            )}

            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '8px' }}>
              {state.disclaimer_zh} 不適用於：{state.not_for.join('、')}
            </div>
          </div>
          );
        })}
      </div>

      <div
        data-testid="daily-estimate-scope-editor"
        style={{
          marginTop: '18px',
          padding: '14px',
          border: '1px solid var(--border)',
          borderRadius: '8px',
          background: 'var(--surface-2)',
        }}
      >
        <div style={{ fontWeight: 600, fontSize: '13px' }}>每日執行的同意範圍</div>
        <p style={{ margin: '4px 0 10px', fontSize: '12px', color: 'var(--text-muted)' }}>
          訓練同意、模型選擇與每日執行同意是三件事。這裡只決定每日排程是否替這個技能帳號執行，
          不會替你同意訓練，也不會改變你選定的模型版本。
        </p>
        <p
          data-testid="daily-estimate-consent-provenance"
          style={{ margin: '0 0 10px', fontSize: '12px', color: 'var(--text-secondary)' }}
        >
          {CONSENT_PROVENANCE_ZH}
        </p>

        {accounts.length === 0 ? (
          <p data-testid="daily-estimate-no-accounts" style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0 }}>
            此帳號尚無技能帳號可聲明。建立技能帳號後才會出現可勾選的項目。
          </p>
        ) : (
          <div style={{ display: 'grid', gap: '10px' }}>
            {accounts.map((account) => {
              const isNamed = named.includes(account.id);
              const entry = draft[account.id] || { instruments: '', maxAge: '' };
              return (
                <div
                  key={account.id}
                  data-testid={`daily-estimate-option-${account.id}`}
                  style={{
                    padding: '10px 12px',
                    borderRadius: '6px',
                    border: '1px solid var(--border)',
                    background: 'var(--surface)',
                  }}
                >
                  <label style={{ display: 'flex', gap: '10px', alignItems: 'center', fontSize: '12px' }}>
                    <input
                      type="checkbox"
                      data-testid={`daily-estimate-pick-${account.id}`}
                      checked={isNamed}
                      onChange={() => toggleNamed(account.id)}
                    />
                    <span style={{ fontFamily: 'monospace' }}>{account.id.slice(0, 8)}…</span>
                    <span>{account.name || '技能帳號'}</span>
                    <span style={{ color: 'var(--text-muted)' }}>狀態：{account.status || 'active'}</span>
                  </label>
                  {isNamed && (
                    <div
                      style={{
                        display: 'flex',
                        gap: '12px',
                        marginTop: '8px',
                        flexWrap: 'wrap',
                        alignItems: 'center',
                      }}
                    >
                      <label style={{ fontSize: '12px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                        標的（逗號分隔，最多 {limits?.instruments_per_skill ?? 8} 個）
                        <input
                          data-testid={`daily-estimate-instruments-${account.id}`}
                          type="text"
                          value={entry.instruments}
                          onChange={(event) =>
                            setDraft((current) => ({
                              ...current,
                              [account.id]: {
                                ...current[account.id],
                                instruments: event.target.value,
                              },
                            }))
                          }
                          style={{ minWidth: '220px', fontSize: '12px', padding: '4px 6px' }}
                        />
                      </label>
                      <label style={{ fontSize: '12px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                        資料新鮮度上限（天）
                        <input
                          data-testid={`daily-estimate-maxage-${account.id}`}
                          type="number"
                          min={1}
                          value={entry.maxAge}
                          onChange={(event) =>
                            setDraft((current) => ({
                              ...current,
                              [account.id]: {
                                ...current[account.id],
                                maxAge: event.target.value,
                              },
                            }))
                          }
                          style={{ width: '80px', fontSize: '12px', padding: '4px 6px' }}
                        />
                      </label>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        留空代表不設上限；設了上限，資料超過天數時該標的會被拒絕而且不消耗額度。
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {accounts.length > 0 && (
          <div
            style={{
              marginTop: '12px',
              display: 'flex',
              gap: '12px',
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            <label style={{ display: 'flex', gap: '8px', alignItems: 'center', fontSize: '12px' }}>
              <input
                type="checkbox"
                data-testid="daily-estimate-acknowledge"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
              我確認要為點名的技能帳號開啟每日估計（只產生估計，不下單）
            </label>
            <button
              type="button"
              className="btn btn-primary"
              data-testid="daily-estimate-grant"
              onClick={save}
              disabled={busy || !acknowledged || named.length === 0 || instrumentError || tooMany}
            >
              {consent?.enabled ? '更新每日範圍' : '記錄每日範圍'}
            </button>
            {consent?.consent_id && (
              <button
                type="button"
                className="btn"
                data-testid="daily-estimate-revoke-all"
                onClick={() => withdraw()}
                disabled={busy}
              >
                撤回全部每日估計
              </button>
            )}
          </div>
        )}

        {(instrumentError || tooMany) && (
          <p data-testid="daily-estimate-scope-warning" style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '8px' }}>
            {tooMany
              ? `每個業主最多可聲明 ${limits?.skill_units_per_owner ?? 10} 個技能帳號。`
              : '每個被點名的技能帳號都要申報至少一個標的，否則無法儲存範圍。'}
          </p>
        )}

        {consent?.consent_version && (
          <p
            data-testid="daily-estimate-consent-state"
            style={{ margin: '10px 0 0', fontSize: '12px', color: 'var(--text-secondary)' }}
          >
            目前有效的每日同意：{consent.consent_version} · 範圍 {consent.skill_units.length} 個技能帳號
            {consent.granted_at ? ` · 記錄於 ${consent.granted_at}` : ''}
          </p>
        )}
      </div>
    </section>
  );
}
