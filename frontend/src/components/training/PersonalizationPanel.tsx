'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface Capability {
  key: string;
  label_zh: string;
  status: string;
  description_zh: string;
  surface: string;
}

interface EligibilityReason {
  code: string;
  message_zh: string;
  measured: unknown;
  requirement: unknown;
}

interface StrategyRow {
  strategy_version_id: string;
  recorded_decisions: number;
  confirmed_decisions: number;
  distinct_sessions: number;
  distinct_snapshot_dates: number;
  action_counts: Record<string, number>;
  in_consent_scope: boolean;
}

interface OwnedVersion {
  id: string;
  title: string;
}

interface Eligibility {
  capability_status: string;
  capability_status_zh: string;
  eligible: boolean;
  status: string;
  reasons: EligibilityReason[];
  requirements: Record<string, number | string>;
  consent: {
    granted: boolean;
    consent_version: string | null;
    scope: { strategy_version_ids: string[] };
    granted_at: string | null;
  };
  dataset: {
    total_recorded_events: number;
    eligible_events: number;
    excluded: {
      superseded_revisions: number;
      outside_consent_scope: number;
      missing_reason: number;
      missing_snapshot_context: number;
    };
    distinct_sessions: number;
    distinct_snapshot_dates: number;
    action_counts: Record<string, number>;
    feature_coverage: { covered: number; considered: number; ratio: number | null };
    future_leakage_events: number;
  };
  strategies: StrategyRow[];
}

const STATUS_LABELS: Record<string, string> = {
  AVAILABLE: '已提供',
  NOT_IMPLEMENTED: '尚未建立',
};

export default function PersonalizationPanel() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [eligibility, setEligibility] = useState<Eligibility | null>(null);
  const [ownedVersions, setOwnedVersions] = useState<OwnedVersion[]>([]);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [caps, elig, experiments] = await Promise.all([
          api.ml.capabilities(),
          api.ml.personalizationEligibility(),
          api.experiments.list().catch(() => []),
        ]);
        if (cancelled) return;
        setCapabilities(caps.capabilities || []);
        setEligibility(elig);
        const owned: OwnedVersion[] = (Array.isArray(experiments) ? experiments : [])
          .filter((exp: { current_version_id?: string | null }) => exp.current_version_id)
          .map((exp: { id: string; title: string; current_version_id: string }) => ({
            id: exp.current_version_id,
            title: exp.title,
          }));
        setOwnedVersions(owned);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '無法載入個人化資格');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const activeScope = eligibility?.consent.scope.strategy_version_ids ?? [];
  const strategyRows = eligibility?.strategies ?? [];
  const ownedTitleById = new Map(ownedVersions.map((version) => [version.id, version.title]));
  // Consent scope is a set of owned strategy versions, not only versions that
  // already have decisions. Without this union a user could not grant consent
  // in advance, and an active consent could not be edited.
  const scopeOptionIds = Array.from(
    new Set([
      ...activeScope,
      ...strategyRows.map((strategy) => strategy.strategy_version_id),
      ...ownedVersions.map((version) => version.id),
    ]),
  );
  const checked = selected ?? activeScope;

  const toggle = (id: string) => {
    const next = checked.includes(id)
      ? checked.filter((value) => value !== id)
      : [...checked, id];
    setSelected(next);
  };

  const grant = async () => {
    if (!acknowledged || checked.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.ml.grantPersonalizationConsent(checked);
      setEligibility(result.eligibility);
      setSelected(null);
      setAcknowledged(false);
      setNotice('已記錄個人化訓練同意。資格檢查仍會逐項判斷資料是否足夠。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法記錄同意');
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.ml.revokePersonalizationConsent();
      setEligibility(result.eligibility);
      setSelected(null);
      setAcknowledged(false);
      setNotice('已撤銷個人化訓練同意。已撤銷的資料不再計入資格。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '無法撤銷同意');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" data-testid="personalization-panel" style={{ marginBottom: '32px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '16px', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '18px' }}>能力分類與個人化</h2>
          <p style={{ color: 'var(--text-secondary)', margin: '6px 0 0', fontSize: '13px' }}>
            三個能力分開標示。個人化模型尚未建立，本區只提供訓練資格檢查與同意範圍。
          </p>
        </div>
      </div>

      <div data-testid="capability-map" style={{ marginTop: '18px', display: 'grid', gap: '10px' }}>
        {capabilities.map((cap) => (
          <div
            key={cap.key}
            data-testid={`capability-${cap.key}`}
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(150px, 200px) 1fr',
              gap: '12px',
              alignItems: 'start',
              padding: '12px 14px',
              border: '1px solid var(--border)',
              borderRadius: '8px',
              background: 'var(--surface-2)',
            }}
          >
            <div>
              <div style={{ fontWeight: 600, fontSize: '14px' }}>{cap.label_zh}</div>
              <div
                style={{
                  marginTop: '6px',
                  display: 'inline-block',
                  padding: '2px 8px',
                  borderRadius: '10px',
                  fontSize: '11px',
                  border: '1px solid currentColor',
                  color: cap.status === 'AVAILABLE' ? 'var(--positive)' : 'var(--warning)',
                }}
              >
                {STATUS_LABELS[cap.status] || cap.status}
              </div>
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              <div>{cap.description_zh}</div>
              <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>位置：{cap.surface}</div>
            </div>
          </div>
        ))}
      </div>

      <div
        data-testid="personalization-eligibility"
        style={{
          marginTop: '20px',
          paddingTop: '16px',
          borderTop: '1px solid var(--border)',
        }}
      >
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: '14px' }}>個人化訓練資格</strong>
          {eligibility && (
            <span
              data-testid="personalization-status"
              style={{
                fontSize: '12px',
                padding: '2px 10px',
                borderRadius: '10px',
                border: '1px solid currentColor',
                color: eligibility.eligible ? 'var(--positive)' : 'var(--warning)',
              }}
            >
              {eligibility.eligible ? '資料門檻已符合' : '不符合（已拒絕訓練）'}
            </span>
          )}
        </div>

        {error && (
          <div className="ws-message-error" style={{ marginTop: '10px' }}>{error}</div>
        )}
        {notice && (
          <div style={{ marginTop: '10px', fontSize: '13px', color: 'var(--text-secondary)' }}>{notice}</div>
        )}

        {eligibility && (
          <>
            <p style={{ margin: '10px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
              {eligibility.capability_status_zh}
            </p>

            <div
              data-testid="personalization-counts"
              style={{
                marginTop: '12px',
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                gap: '10px',
                fontSize: '12px',
              }}
            >
              <Metric label="已記錄決策" value={eligibility.dataset.total_recorded_events} />
              <Metric label="可用標籤" value={eligibility.dataset.eligible_events} />
              <Metric label="工作階段" value={eligibility.dataset.distinct_sessions} />
              <Metric label="決策時點" value={eligibility.dataset.distinct_snapshot_dates} />
              <Metric
                label="特徵覆蓋"
                value={
                  eligibility.dataset.feature_coverage.ratio === null
                    ? '未評估'
                    : `${eligibility.dataset.feature_coverage.covered}/${eligibility.dataset.feature_coverage.considered}`
                }
              />
            </div>

            {eligibility.reasons.length > 0 && (
              <ul
                data-testid="personalization-reasons"
                style={{ margin: '12px 0 0', paddingLeft: '18px', fontSize: '13px', color: 'var(--text-secondary)' }}
              >
                {eligibility.reasons.map((reason) => (
                  <li key={reason.code} style={{ marginBottom: '4px' }}>
                    <span style={{ fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-muted)' }}>
                      {reason.code}
                    </span>
                    {' — '}
                    {reason.message_zh}
                  </li>
                ))}
              </ul>
            )}

            <div style={{ marginTop: '16px' }}>
              <div style={{ fontWeight: 600, fontSize: '13px' }}>訓練同意範圍</div>
              <p style={{ margin: '4px 0 8px', fontSize: '12px', color: 'var(--text-muted)' }}>
                只計入已勾選策略版本內、具理由且未撤回的決策。保存研究記錄或其他 opt-in 不等於訓練同意。
              </p>

              {scopeOptionIds.length === 0 ? (
                <p data-testid="personalization-no-strategies" style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
                  此帳號尚無可授權的策略版本。先建立並保存一個策略版本，才會有可勾選的同意範圍。
                </p>
              ) : (
                <div style={{ display: 'grid', gap: '8px' }}>
                  {scopeOptionIds.map((versionId) => {
                    const strategy = strategyRows.find(
                      (row) => row.strategy_version_id === versionId,
                    );
                    return (
                      <label
                        key={versionId}
                        style={{
                          display: 'flex',
                          gap: '10px',
                          alignItems: 'center',
                          fontSize: '12px',
                          padding: '8px 10px',
                          border: '1px solid var(--border)',
                          borderRadius: '6px',
                          background: 'var(--surface-2)',
                        }}
                      >
                        <input
                          type="checkbox"
                          data-testid={`personalization-scope-${versionId}`}
                          checked={checked.includes(versionId)}
                          onChange={() => toggle(versionId)}
                        />
                        <span style={{ fontFamily: 'monospace' }}>{versionId.slice(0, 8)}…</span>
                        {ownedTitleById.get(versionId) && (
                          <span>{ownedTitleById.get(versionId)}</span>
                        )}
                        {strategy ? (
                          <span style={{ color: 'var(--text-secondary)' }}>
                            已記錄 {strategy.recorded_decisions} · 可用標籤 {strategy.confirmed_decisions} · 工作階段{' '}
                            {strategy.distinct_sessions} · 時點 {strategy.distinct_snapshot_dates}
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)' }}>尚無人工決策記錄</span>
                        )}
                      </label>
                    );
                  })}
                </div>
              )}

              {scopeOptionIds.length > 0 && (
                <div style={{ marginTop: '12px', display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                  <label style={{ display: 'flex', gap: '8px', alignItems: 'center', fontSize: '12px' }}>
                    <input
                      type="checkbox"
                      data-testid="personalization-acknowledge"
                      checked={acknowledged}
                      onChange={(event) => setAcknowledged(event.target.checked)}
                    />
                    我確認同意以所選策略的個人決策記錄訓練專屬於我的模型
                  </label>
                  <button
                    type="button"
                    className="btn btn-primary"
                    data-testid="personalization-grant"
                    onClick={grant}
                    disabled={busy || !acknowledged || checked.length === 0}
                  >
                    {eligibility.consent.granted ? '更新同意範圍' : '記錄同意'}
                  </button>
                </div>
              )}

              {eligibility.consent.granted && (
                <button
                  type="button"
                  className="btn"
                  data-testid="personalization-revoke"
                  onClick={revoke}
                  disabled={busy}
                  style={{ marginTop: '12px' }}
                >
                  撤銷同意
                </button>
              )}

              {eligibility.consent.granted && (
                <p data-testid="personalization-consent-state" style={{ margin: '10px 0 0', fontSize: '12px', color: 'var(--text-secondary)' }}>
                  目前有效同意：{eligibility.consent.consent_version} · 範圍{' '}
                  {eligibility.consent.scope.strategy_version_ids.length} 個策略版本
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div style={{ padding: '8px 10px', border: '1px solid var(--border)', borderRadius: '6px' }}>
      <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{label}</div>
      <div style={{ fontSize: '15px', fontWeight: 600, marginTop: '2px' }}>{value}</div>
    </div>
  );
}
