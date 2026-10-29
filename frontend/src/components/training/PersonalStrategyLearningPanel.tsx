'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '@/lib/api';

interface OwnedVersion {
  id: string;
  title: string;
}

interface ReviewLabel {
  id: string;
  revision_number: number;
  state: 'confirmed' | 'excluded';
  action_label: string | null;
  note: string | null;
  exclusion_reason: string | null;
  supersedes_label_id: string | null;
  created_at: string | null;
  recorded_action: string | null;
  action_overridden: boolean;
}

interface ReviewRow {
  decision_event_id: string;
  simulation_session_id: string;
  strategy_version_id: string | null;
  as_known_context: {
    instrument_id: string | null;
    step_number: number | null;
    snapshot_date: string | null;
    data_cutoff_at: string | null;
  };
  recorded_action: string;
  quantity: number | null;
  rationale: string | null;
  recorded_at: string | null;
  label: ReviewLabel | null;
  revision_count: number;
  usable_for_training: boolean;
}

interface ReviewResponse {
  strategy_version_id: string | null;
  summary: {
    total_current_decisions: number;
    confirmed: number;
    excluded: number;
    unreviewed: number;
  };
  decisions: ReviewRow[];
  note: string;
}

interface Reason {
  code: string;
  message_zh: string;
  measured?: unknown;
  requirement?: unknown;
}

interface SnapshotBody {
  snapshot_id: string | null;
  status: string;
  strategy_version_id: string;
  included_count: number;
  excluded_count: number;
  exclusion_breakdown: Record<string, number>;
  exclusion_labels_zh: Record<string, string>;
  class_distribution: Record<string, number>;
  date_range: { start: string | null; end: string | null };
  data_cutoff_at: string | null;
  feature_schema_version: string;
  feature_names: string[];
  checksum: string;
  can_build?: boolean;
  reasons?: Reason[];
  created_at?: string | null;
  consent?: { consent_version: string | null; in_scope: boolean };
  note?: string;
  rows_preview?: Array<{
    decision_event_id: string;
    action_label: string;
    instrument_id: string;
    snapshot_date: string;
    feature_bar_date: string;
  }>;
}

interface PersonalModel {
  model_version_id: string;
  record_id: string;
  version_number: number;
  is_active: boolean;
  created_at: string | null;
  revoked_at: string | null;
  artifact_id: string | null;
  artifact_sha256: string | null;
  snapshot_id: string | null;
  snapshot_checksum: string | null;
  training_job_id: string | null;
  classes: string[] | null;
  n_rows: number | null;
  warnings: string[] | null;
  split: {
    kind?: string;
    train?: { size: number };
    val?: { size: number };
    test?: { size: number };
    selection_source?: string;
  } | null;
  evaluation: {
    result_summary: string;
    metrics: {
      metrics?: Record<string, { accuracy?: number; macro_f1?: number }>;
      baseline?: { accuracy?: number } | null;
      split?: unknown;
      warnings?: string[];
      n_rows?: number;
    };
    warnings: string[];
    data_cutoff_at: string | null;
    engine_version: string | null;
  } | null;
  wording_zh: string;
}

interface PersonalJob {
  id: string;
  status: string;
  error_details: string | null;
  cancelled_at: string | null;
  created_at: string;
  completed_at: string | null;
}

interface Prediction {
  resolved_version: {
    model_version_id: string;
    record_id: string;
    version_number: number;
    resolved_from_active_alias: boolean;
  };
  model_estimate_zh: string;
  predicted_action: string;
  action_probabilities: Record<string, number>;
  classes: string[];
  as_of: {
    instrument_id: string;
    as_of_date: string;
    feature_bar_date: string;
  };
  snapshot: { snapshot_id: string | null; checksum: string | null };
  artifact_sha256: string | null;
  training_job_id: string | null;
  consent_currently_active: boolean;
  not_for: string[];
  disclaimer_zh: string;
}

type EditorMode = 'correct' | 'exclude' | null;

const EXCLUSION_REASONS: Array<{ value: string; label: string }> = [
  { value: 'data_entry_error', label: '輸入錯誤' },
  { value: 'test_trade', label: '純測試交易' },
  { value: 'outside_strategy', label: '不在策略範圍' },
  { value: 'other', label: '其他' },
];

const ACTION_LABELS: Record<string, string> = {
  buy: '買入',
  sell: '賣出',
  hold: '觀望',
};

const JOB_STATUS_LABELS: Record<string, string> = {
  pending: '排隊中',
  running: '執行中',
  completed: '已完成',
  failed: '失敗',
  cancelled: '已取消',
};

const WARNING_LABELS: Record<string, string> = {
  small_test_set: '測試集樣本偏少',
  small_class_support: '部分類別樣本偏少',
  missing_class_in_test: '測試集缺少部分類別',
  synthetic_market_features: '市場特徵來自合成資料（工程驗證用）',
  mixed_market_features: '市場特徵混合真實與合成資料',
};

function actionLabel(value: string | null): string {
  if (!value) return '—';
  return ACTION_LABELS[value] ?? value;
}

function shortId(value: string | null | undefined): string {
  return value ? `${value.slice(0, 8)}…` : '—';
}

function describeFailure(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message || fallback;
  }
  return err instanceof Error ? err.message : fallback;
}

export default function PersonalStrategyLearningPanel() {
  const [versions, setVersions] = useState<OwnedVersion[]>([]);
  const [versionId, setVersionId] = useState('');
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [preview, setPreview] = useState<SnapshotBody | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotBody[]>([]);
  const [models, setModels] = useState<PersonalModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [editorFor, setEditorFor] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<EditorMode>(null);
  const [editorAction, setEditorAction] = useState('hold');
  const [editorNote, setEditorNote] = useState('');
  const [editorReason, setEditorReason] = useState('data_entry_error');

  const [selectedSnapshot, setSelectedSnapshot] = useState('');
  const [modelType, setModelType] = useState('logistic_regression');
  const [job, setJob] = useState<PersonalJob | null>(null);
  const jobTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [predictForm, setPredictForm] = useState({
    instrument_id: 'US:AAPL',
    as_of_date: '',
  });
  const [prediction, setPrediction] = useState<Prediction | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const experiments = await api.experiments.list();
        if (cancelled) return;
        const owned: OwnedVersion[] = (Array.isArray(experiments) ? experiments : [])
          .filter((exp: { current_version_id?: string | null }) => exp.current_version_id)
          .map((exp: { id: string; title: string; current_version_id: string }) => ({
            id: exp.current_version_id,
            title: exp.title,
          }));
        setVersions(owned);
        if (owned.length > 0) setVersionId((current) => current || owned[0].id);
      } catch (err) {
        if (!cancelled) {
          setError(describeFailure(err, '無法載入可用的策略版本'));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadStrategy = useCallback(async (id: string) => {
    const [reviewBody, previewBody, snapshotsBody, modelsBody] = await Promise.all([
      api.ml.reviewDecisions(id),
      api.ml.previewPersonalSnapshot(id),
      api.ml.listPersonalSnapshots(id),
      api.ml.listPersonalModels(id),
    ]);
    setReview(reviewBody as ReviewResponse);
    setPreview(previewBody as SnapshotBody);
    const snapshotRows: SnapshotBody[] = (snapshotsBody as { snapshots: SnapshotBody[] })
      .snapshots;
    setSnapshots(snapshotRows);
    setSelectedSnapshot((current) =>
      snapshotRows.some((row) => row.snapshot_id === current)
        ? current
        : snapshotRows[0]?.snapshot_id ?? '',
    );
    setModels((modelsBody as { models: PersonalModel[] }).models);
  }, []);

  useEffect(() => {
    if (!versionId) return;
    let cancelled = false;
    (async () => {
      try {
        await loadStrategy(versionId);
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) setError(describeFailure(err, '無法載入個人策略學習資料'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [versionId, loadStrategy]);

  useEffect(() => {
    return () => {
      if (jobTimer.current) clearTimeout(jobTimer.current);
    };
  }, []);

  const pollJob = useCallback(
    async (jobId: string) => {
      try {
        const body = (await api.training.getJob(jobId)) as PersonalJob;
        setJob(body);
        if (body.status === 'pending' || body.status === 'running') {
          jobTimer.current = setTimeout(() => pollJob(jobId), 3000);
        } else if (versionId) {
          await loadStrategy(versionId);
        }
      } catch (err) {
        setError(describeFailure(err, '無法讀取訓練工作狀態'));
      }
    },
    [loadStrategy, versionId],
  );

  const refresh = async (id: string) => {
    await loadStrategy(id);
  };

  const runLabelAction = async (
    eventId: string,
    payload: {
      state: 'confirmed' | 'excluded';
      action_label?: string | null;
      note?: string | null;
      exclusion_reason?: string | null;
    },
    successMessage: string,
  ) => {
    if (!versionId) return;
    setBusy(eventId);
    setError(null);
    setNotice(null);
    try {
      await api.ml.recordDecisionLabel(eventId, payload);
      await refresh(versionId);
      setEditorFor(null);
      setEditorMode(null);
      setEditorNote('');
      setNotice(successMessage);
    } catch (err) {
      setError(describeFailure(err, '無法儲存決策標籤'));
    } finally {
      setBusy(null);
    }
  };

  const confirmDecision = (row: ReviewRow) =>
    runLabelAction(
      row.decision_event_id,
      { state: 'confirmed' },
      `已確認決策（記錄動作：${actionLabel(row.recorded_action)}）。`,
    );

  const saveCorrection = (row: ReviewRow) => {
    if (!editorNote.trim()) {
      setError('更正動作需要填寫原因備註。');
      return;
    }
    runLabelAction(
      row.decision_event_id,
      {
        state: 'confirmed',
        action_label: editorAction,
        note: editorNote.trim(),
      },
      `已更正為「${actionLabel(editorAction)}」，原始決策記錄未被改寫。`,
    );
  };

  const saveExclusion = (row: ReviewRow) =>
    runLabelAction(
      row.decision_event_id,
      {
        state: 'excluded',
        exclusion_reason: editorReason,
        note: editorNote.trim() || null,
      },
      '已標記排除；此決策不會進入訓練資料。',
    );

  const createSnapshot = async () => {
    if (!versionId) return;
    setBusy('snapshot');
    setError(null);
    setNotice(null);
    try {
      const body = (await api.ml.createPersonalSnapshot(versionId)) as SnapshotBody;
      setNotice(
        `已建立資料快照：納入 ${body.included_count} 筆，checksum ${shortId(body.checksum)}。`,
      );
      await refresh(versionId);
      setSelectedSnapshot(body.snapshot_id ?? '');
    } catch (err) {
      setError(describeFailure(err, '無法建立訓練資料快照'));
    } finally {
      setBusy(null);
    }
  };

  const startTraining = async () => {
    if (!selectedSnapshot) return;
    setBusy('train');
    setError(null);
    setNotice(null);
    setPrediction(null);
    try {
      const body = (await api.ml.startPersonalTrainingJob(
        selectedSnapshot,
        modelType,
      )) as { job_id: string; status: string };
      setJob({
        id: body.job_id,
        status: body.status,
        error_details: null,
        cancelled_at: null,
        created_at: new Date().toISOString(),
        completed_at: null,
      });
      setNotice('訓練工作已排隊；此環境需要 worker 執行，完成後會自動更新。');
      pollJob(body.job_id);
    } catch (err) {
      setError(describeFailure(err, '無法建立訓練工作'));
    } finally {
      setBusy(null);
    }
  };

  const cancelJob = async () => {
    if (!job) return;
    setBusy('cancel');
    setError(null);
    try {
      const body = (await api.training.cancel(job.id)) as PersonalJob;
      setJob(body);
      setNotice('已取消訓練工作；已預約的 AI 額度會釋放。');
      if (versionId) await loadStrategy(versionId);
    } catch (err) {
      setError(describeFailure(err, '無法取消訓練工作'));
    } finally {
      setBusy(null);
    }
  };

  const activate = async (model: PersonalModel) => {
    setBusy(model.model_version_id);
    setError(null);
    setNotice(null);
    try {
      await api.ml.activatePersonalModel(model.model_version_id);
      if (versionId) await loadStrategy(versionId);
      setNotice(`已將版本 ${model.version_number} 設為使用中版本。`);
    } catch (err) {
      setError(describeFailure(err, '無法啟用此模型版本'));
    } finally {
      setBusy(null);
    }
  };

  const runPrediction = async () => {
    const target = models.find((model) => model.is_active) ?? models[0];
    if (!target) {
      setError('此策略尚無可用的個人決策模型版本。');
      return;
    }
    if (!predictForm.instrument_id.trim() || predictForm.as_of_date.length !== 10) {
      setError('請填寫標的與推論日期（YYYY-MM-DD）。');
      return;
    }
    setBusy('predict');
    setError(null);
    setNotice(null);
    try {
      const body = (await api.ml.predictPersonalViaRecord(target.record_id, {
        instrument_id: predictForm.instrument_id.trim(),
        as_of_date: predictForm.as_of_date,
      })) as Prediction;
      setPrediction(body);
    } catch (err) {
      setError(describeFailure(err, '推論未執行'));
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return (
      <section className="card" data-testid="personal-learning-panel" style={{ marginBottom: '32px' }}>
        <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
          載入個人策略學習資料…
        </p>
      </section>
    );
  }

  const selectedTitle = versions.find((version) => version.id === versionId)?.title;
  const summary = review?.summary;
  const jobTerminal =
    job !== null && !['pending', 'running'].includes(job.status);

  return (
    <section className="card" data-testid="personal-learning-panel" style={{ marginBottom: '32px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '16px', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '18px' }}>個人策略學習</h2>
          <p style={{ color: 'var(--text-secondary)', margin: '6px 0 0', fontSize: '13px' }}>
            覆核你的決策 → 建立凍結快照 → 訓練專屬模型 → 以不可變版本推論。
            「模型估計你在此策略下的決策選擇」，不是市場方向預測、不是投資建議；
            分數亦不代表回報或越用越準。
          </p>
        </div>
        {versions.length > 0 && (
          <label style={{ display: 'grid', gap: '4px', fontSize: '12px' }}>
            <span style={{ color: 'var(--text-muted)' }}>策略版本</span>
            <select
              data-testid="pl-strategy-select"
              value={versionId}
              onChange={(event) => {
                setVersionId(event.target.value);
                setNotice(null);
                setError(null);
                setPrediction(null);
                setJob(null);
              }}
              style={{ minWidth: '260px', padding: '6px 8px' }}
            >
              {versions.map((version) => (
                <option key={version.id} value={version.id}>
                  {version.title} · {shortId(version.id)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && (
        <div className="ws-message-error" data-testid="pl-error" style={{ marginTop: '12px' }}>
          {error}
        </div>
      )}
      {notice && (
        <div data-testid="pl-notice" style={{ marginTop: '12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
          {notice}
        </div>
      )}

      {versions.length === 0 && (
        <p style={{ marginTop: '16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
          此帳號尚無已保存的策略版本。先建立策略並保存版本，才會有可覆核的決策與可訓練的資料。
        </p>
      )}

      {versionId && review && (
        <>
          <div
            data-testid="pl-review"
            style={{ marginTop: '22px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}
          >
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
              <strong style={{ fontSize: '14px' }}>1. 決策覆核</strong>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                {selectedTitle ? `${selectedTitle} · ` : ''}只計入你親自確認的決策
              </span>
            </div>
            <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
              {review.note}
            </p>
            {summary && (
              <div
                data-testid="pl-review-summary"
                style={{
                  marginTop: '12px',
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                  gap: '10px',
                  fontSize: '12px',
                }}
              >
                <Metric label="目前決策" value={summary.total_current_decisions} />
                <Metric label="已確認" value={summary.confirmed} />
                <Metric label="已排除" value={summary.excluded} />
                <Metric label="未覆核" value={summary.unreviewed} />
              </div>
            )}

            {review.decisions.length === 0 ? (
              <p style={{ margin: '12px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                此策略版本尚未記錄任何決策。決策會在模擬工作區以手動單或規則單記錄。
              </p>
            ) : (
              <div style={{ marginTop: '12px', display: 'grid', gap: '8px' }}>
                {review.decisions.slice(0, 25).map((row) => {
                  const active = editorFor === row.decision_event_id;
                  return (
                    <div
                      key={row.decision_event_id}
                      data-testid={`pl-review-row-${row.decision_event_id}`}
                      style={{
                        padding: '10px 12px',
                        border: '1px solid var(--border)',
                        borderRadius: '8px',
                        background: 'var(--surface-2)',
                        fontSize: '12px',
                      }}
                    >
                      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
                        <span style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                          {shortId(row.decision_event_id)}
                        </span>
                        <span>{row.as_known_context.instrument_id ?? '—'}</span>
                        <span style={{ color: 'var(--text-secondary)' }}>
                          {row.as_known_context.snapshot_date ?? '—'} · 第 {row.as_known_context.step_number ?? '—'} 步
                        </span>
                        <span>記錄動作：{actionLabel(row.recorded_action)}</span>
                        <span
                          data-testid={`pl-label-state-${row.decision_event_id}`}
                          style={{
                            padding: '1px 8px',
                            borderRadius: '10px',
                            border: '1px solid currentColor',
                            color: row.usable_for_training
                              ? 'var(--positive)'
                              : row.label?.state === 'excluded'
                                ? 'var(--warning)'
                                : 'var(--text-muted)',
                          }}
                        >
                          {row.usable_for_training
                            ? `已確認 · ${actionLabel(row.label?.action_label ?? null)}`
                            : row.label?.state === 'excluded'
                              ? '已排除'
                              : '未覆核'}
                        </span>
                        {row.revision_count > 1 && (
                          <span style={{ color: 'var(--text-muted)' }}>
                            {row.revision_count} 次修訂
                          </span>
                        )}
                      </div>

                      {row.label?.action_overridden && (
                        <div style={{ marginTop: '6px', color: 'var(--text-secondary)' }}>
                          已更正動作：{actionLabel(row.label.recorded_action)} →{' '}
                          {actionLabel(row.label.action_label)}
                          {row.label.note ? `（${row.label.note}）` : ''}
                        </div>
                      )}

                      <div style={{ marginTop: '8px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`pl-confirm-${row.decision_event_id}`}
                          onClick={() => confirmDecision(row)}
                          disabled={busy === row.decision_event_id}
                        >
                          確認
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`pl-correct-${row.decision_event_id}`}
                          onClick={() => {
                            setEditorFor(row.decision_event_id);
                            setEditorMode('correct');
                            setEditorAction(row.label?.action_label ?? row.recorded_action);
                            setEditorNote('');
                            setError(null);
                          }}
                        >
                          更正動作
                        </button>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`pl-exclude-${row.decision_event_id}`}
                          onClick={() => {
                            setEditorFor(row.decision_event_id);
                            setEditorMode('exclude');
                            setEditorReason('data_entry_error');
                            setEditorNote('');
                            setError(null);
                          }}
                        >
                          標記排除
                        </button>
                      </div>

                      {active && editorMode === 'correct' && (
                        <div style={{ marginTop: '10px', display: 'grid', gap: '8px' }}>
                          <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            更正為
                            <select
                              data-testid="pl-correction-action"
                              value={editorAction}
                              onChange={(event) => setEditorAction(event.target.value)}
                              style={{ padding: '4px 6px' }}
                            >
                              <option value="buy">買入</option>
                              <option value="sell">賣出</option>
                              <option value="hold">觀望</option>
                            </select>
                          </label>
                          <textarea
                            data-testid="pl-correction-note"
                            value={editorNote}
                            onChange={(event) => setEditorNote(event.target.value)}
                            placeholder="更正原因（必填）"
                            rows={2}
                            style={{ padding: '6px 8px', resize: 'vertical' }}
                          />
                          <div style={{ display: 'flex', gap: '8px' }}>
                            <button
                              type="button"
                              className="btn btn-primary"
                              data-testid="pl-correction-save"
                              onClick={() => saveCorrection(row)}
                              disabled={busy === row.decision_event_id}
                            >
                              儲存更正
                            </button>
                            <button
                              type="button"
                              className="btn"
                              onClick={() => {
                                setEditorFor(null);
                                setEditorMode(null);
                              }}
                            >
                              取消
                            </button>
                          </div>
                        </div>
                      )}

                      {active && editorMode === 'exclude' && (
                        <div style={{ marginTop: '10px', display: 'grid', gap: '8px' }}>
                          <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            排除原因
                            <select
                              data-testid="pl-exclusion-reason"
                              value={editorReason}
                              onChange={(event) => setEditorReason(event.target.value)}
                              style={{ padding: '4px 6px' }}
                            >
                              {EXCLUSION_REASONS.map((reason) => (
                                <option key={reason.value} value={reason.value}>
                                  {reason.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <textarea
                            data-testid="pl-exclusion-note"
                            value={editorNote}
                            onChange={(event) => setEditorNote(event.target.value)}
                            placeholder="補充說明（選填）"
                            rows={2}
                            style={{ padding: '6px 8px', resize: 'vertical' }}
                          />
                          <div style={{ display: 'flex', gap: '8px' }}>
                            <button
                              type="button"
                              className="btn btn-primary"
                              data-testid="pl-exclusion-save"
                              onClick={() => saveExclusion(row)}
                              disabled={busy === row.decision_event_id}
                            >
                              儲存排除
                            </button>
                            <button
                              type="button"
                              className="btn"
                              onClick={() => {
                                setEditorFor(null);
                                setEditorMode(null);
                              }}
                            >
                              取消
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
                {review.decisions.length > 25 && (
                  <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
                    只顯示最近 25 筆決策。
                  </p>
                )}
              </div>
            )}
          </div>

          {preview && (
            <div
              data-testid="pl-preview"
              style={{ marginTop: '22px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
                <div>
                  <strong style={{ fontSize: '14px' }}>2. 訓練資料快照</strong>
                  <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                    {preview.note}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  data-testid="pl-snapshot-create"
                  onClick={createSnapshot}
                  disabled={busy === 'snapshot' || preview.can_build === false}
                >
                  {busy === 'snapshot' ? '建立中…' : '建立凍結快照'}
                </button>
              </div>

              <div
                style={{
                  marginTop: '12px',
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                  gap: '10px',
                  fontSize: '12px',
                }}
              >
                <Metric label="納入訓練" value={preview.included_count} />
                <Metric label="已排除" value={preview.excluded_count} />
                <Metric
                  label="決策時點範圍"
                  value={`${preview.date_range.start ?? '—'} → ${preview.date_range.end ?? '—'}`}
                />
                <Metric label="特徵欄位" value={preview.feature_names.length} />
              </div>

              {Object.keys(preview.class_distribution).length > 0 && (
                <div data-testid="pl-class-distribution" style={{ marginTop: '10px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                  類別分佈：
                  {Object.entries(preview.class_distribution)
                    .map(([label, count]) => `${actionLabel(label)} ${count}`)
                    .join(' · ')}
                </div>
              )}

              {Object.keys(preview.exclusion_breakdown).length > 0 && (
                <div data-testid="pl-exclusion-breakdown" style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                  排除原因：
                  {Object.entries(preview.exclusion_breakdown)
                    .map(
                      ([code, count]) =>
                        `${preview.exclusion_labels_zh?.[code] ?? code} ${count}`,
                    )
                    .join(' · ')}
                </div>
              )}

              <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
                特徵結構 {preview.feature_schema_version} · checksum {shortId(preview.checksum)} ·
                資料截止 {preview.data_cutoff_at ?? '—'}
              </div>

              {preview.can_build === false && preview.reasons && preview.reasons.length > 0 && (
                <ul
                  data-testid="pl-preview-reasons"
                  style={{ margin: '10px 0 0', paddingLeft: '18px', fontSize: '12px', color: 'var(--text-secondary)' }}
                >
                  {preview.reasons.map((reason) => (
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

              {snapshots.length > 0 && (
                <div data-testid="pl-snapshot-list" style={{ marginTop: '14px', display: 'grid', gap: '6px', fontSize: '12px' }}>
                  <div style={{ fontWeight: 600 }}>已建立的快照（凍結後不再改變）</div>
                  {snapshots.map((snapshot) => (
                    <div
                      key={snapshot.snapshot_id ?? snapshot.checksum}
                      style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', color: 'var(--text-secondary)' }}
                    >
                      <span style={{ fontFamily: 'monospace' }}>{shortId(snapshot.snapshot_id)}</span>
                      <span>納入 {snapshot.included_count}</span>
                      <span>checksum {shortId(snapshot.checksum)}</span>
                      <span>{snapshot.created_at ? snapshot.created_at.slice(0, 19).replace('T', ' ') : ''}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div
            data-testid="pl-training"
            style={{ marginTop: '22px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}
          >
            <strong style={{ fontSize: '14px' }}>3. 訓練工作</strong>
            <div style={{ marginTop: '10px', display: 'flex', gap: '12px', alignItems: 'end', flexWrap: 'wrap', fontSize: '12px' }}>
              <label style={{ display: 'grid', gap: '4px' }}>
                <span style={{ color: 'var(--text-muted)' }}>資料快照</span>
                <select
                  data-testid="pl-snapshot-select"
                  value={selectedSnapshot}
                  onChange={(event) => setSelectedSnapshot(event.target.value)}
                  style={{ minWidth: '220px', padding: '5px 8px' }}
                >
                  {snapshots.length === 0 && <option value="">尚未建立快照</option>}
                  {snapshots.map((snapshot) => (
                    <option key={snapshot.snapshot_id} value={snapshot.snapshot_id ?? ''}>
                      {shortId(snapshot.snapshot_id)} · {snapshot.included_count} 筆
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ display: 'grid', gap: '4px' }}>
                <span style={{ color: 'var(--text-muted)' }}>模型</span>
                <select
                  data-testid="pl-model-type"
                  value={modelType}
                  onChange={(event) => setModelType(event.target.value)}
                  style={{ padding: '5px 8px' }}
                >
                  <option value="logistic_regression">邏輯回歸</option>
                  <option value="baseline">基線（最常見類別）</option>
                </select>
              </label>
              <button
                type="button"
                className="btn btn-primary"
                data-testid="pl-train-start"
                onClick={startTraining}
                disabled={busy === 'train' || !selectedSnapshot}
              >
                開始訓練
              </button>
            </div>

            {job && (
              <div data-testid="pl-train-status" style={{ marginTop: '12px', fontSize: '12px' }}>
                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
                  <span style={{ fontFamily: 'monospace' }}>{shortId(job.id)}</span>
                  <span
                    style={{
                      padding: '1px 8px',
                      borderRadius: '10px',
                      border: '1px solid currentColor',
                      color:
                        job.status === 'completed'
                          ? 'var(--positive)'
                          : job.status === 'failed' || job.status === 'cancelled'
                            ? 'var(--warning)'
                            : 'var(--text-secondary)',
                    }}
                  >
                    {JOB_STATUS_LABELS[job.status] ?? job.status}
                  </span>
                  {(job.status === 'pending' || job.status === 'running') && (
                    <button
                      type="button"
                      className="btn"
                      data-testid="pl-train-cancel"
                      onClick={cancelJob}
                      disabled={busy === 'cancel'}
                    >
                      取消
                    </button>
                  )}
                </div>
                {job.error_details && (
                  <div style={{ marginTop: '8px', color: 'var(--warning)' }}>
                    拒絕原因：{job.error_details}
                  </div>
                )}
                {!jobTerminal && (
                  <div style={{ marginTop: '6px', color: 'var(--text-muted)' }}>
                    等待 worker 執行；若長時間停留在排隊中，表示此環境沒有常駐 worker。
                  </div>
                )}
              </div>
            )}
          </div>

          <div
            data-testid="pl-models"
            style={{ marginTop: '22px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}
          >
            <strong style={{ fontSize: '14px' }}>4. 模型版本與推論</strong>
            {models.length === 0 ? (
              <p style={{ margin: '10px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                此策略尚無模型版本。完成快照與訓練後，版本會顯示在這裡。
              </p>
            ) : (
              <div style={{ marginTop: '12px', display: 'grid', gap: '10px' }}>
                {models.map((model) => {
                  const test = model.evaluation?.metrics?.metrics?.test;
                  const baseline = model.evaluation?.metrics?.baseline;
                  return (
                    <div
                      key={model.model_version_id}
                      data-testid={`pl-model-${model.model_version_id}`}
                      style={{
                        padding: '10px 12px',
                        border: '1px solid var(--border)',
                        borderRadius: '8px',
                        background: 'var(--surface-2)',
                        fontSize: '12px',
                      }}
                    >
                      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
                        <span style={{ fontWeight: 600 }}>版本 {model.version_number}</span>
                        {model.is_active && (
                          <span
                            data-testid="pl-model-active"
                            style={{
                              padding: '1px 8px',
                              borderRadius: '10px',
                              border: '1px solid var(--positive)',
                              color: 'var(--positive)',
                            }}
                          >
                            使用中
                          </span>
                        )}
                        <span style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                          artifact {shortId(model.artifact_sha256)}
                        </span>
                        <span style={{ color: 'var(--text-secondary)' }}>
                          快照 {shortId(model.snapshot_id)} · {model.n_rows ?? '—'} 筆
                        </span>
                      </div>
                      <div style={{ marginTop: '6px', color: 'var(--text-secondary)' }}>
                        {model.wording_zh}
                        {test ? (
                          <>
                            {' · '}測試集準確率 {(test.accuracy ?? 0).toFixed(3)}
                            {typeof test.macro_f1 === 'number' ? ` · macro F1 ${test.macro_f1.toFixed(3)}` : ''}
                            {baseline && typeof baseline.accuracy === 'number'
                              ? ` · 基線 ${baseline.accuracy.toFixed(3)}`
                              : ''}
                          </>
                        ) : (
                          ' · 尚無評估記錄'
                        )}
                      </div>
                      <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
                        切分：{model.split?.kind ?? '—'} · 訓練／驗證／測試{' '}
                        {model.split?.train?.size ?? '—'}／{model.split?.val?.size ?? '—'}／
                        {model.split?.test?.size ?? '—'} · 選擇來源{' '}
                        {model.split?.selection_source ?? '—'}
                      </div>
                      {model.evaluation?.result_summary && (
                        <div style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>
                          {model.evaluation.result_summary}
                        </div>
                      )}
                      {(model.warnings?.length ?? 0) > 0 && (
                        <div data-testid="pl-model-warnings" style={{ marginTop: '4px', color: 'var(--warning)' }}>
                          警告：
                          {(model.warnings ?? [])
                            .map((code) => WARNING_LABELS[code] ?? code)
                            .join(' · ')}
                        </div>
                      )}
                      <div style={{ marginTop: '8px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        <button
                          type="button"
                          className="btn"
                          data-testid={`pl-activate-${model.model_version_id}`}
                          onClick={() => activate(model)}
                          disabled={model.is_active || busy === model.model_version_id}
                        >
                          {model.is_active ? '已在使用中' : '設為使用中版本'}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {models.length > 0 && (
              <div style={{ marginTop: '14px', display: 'flex', gap: '10px', alignItems: 'end', flexWrap: 'wrap', fontSize: '12px' }}>
                <label style={{ display: 'grid', gap: '4px' }}>
                  <span style={{ color: 'var(--text-muted)' }}>標的</span>
                  <input
                    data-testid="pl-predict-instrument"
                    value={predictForm.instrument_id}
                    onChange={(event) =>
                      setPredictForm((current) => ({ ...current, instrument_id: event.target.value }))
                    }
                    style={{ padding: '5px 8px', width: '140px' }}
                  />
                </label>
                <label style={{ display: 'grid', gap: '4px' }}>
                  <span style={{ color: 'var(--text-muted)' }}>推論日期</span>
                  <input
                    type="date"
                    data-testid="pl-predict-date"
                    value={predictForm.as_of_date}
                    onChange={(event) =>
                      setPredictForm((current) => ({ ...current, as_of_date: event.target.value }))
                    }
                    style={{ padding: '5px 8px' }}
                  />
                </label>
                <button
                  type="button"
                  className="btn btn-primary"
                  data-testid="pl-predict-run"
                  onClick={runPrediction}
                  disabled={busy === 'predict'}
                >
                  以使用中版本推論
                </button>
              </div>
            )}

            {prediction && (
              <div
                data-testid="pl-predict-result"
                style={{
                  marginTop: '14px',
                  padding: '12px 14px',
                  border: '1px solid var(--border)',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
              >
                <div style={{ fontWeight: 600 }}>
                  {prediction.model_estimate_zh}：{actionLabel(prediction.predicted_action)}
                </div>
                <div style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>
                  實際解析版本 {prediction.resolved_version.version_number}
                  {prediction.resolved_version.resolved_from_active_alias
                    ? '（由使用中版本別名解析）'
                    : '（明確指定版本）'}
                </div>
                <div style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>
                  機率：
                  {Object.entries(prediction.action_probabilities)
                    .map(([label, value]) => `${actionLabel(label)} ${(value * 100).toFixed(1)}%`)
                    .join(' · ')}
                </div>
                <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
                  推論時點 {prediction.as_of.as_of_date} · 特徵 K 線 {prediction.as_of.feature_bar_date} ·
                  快照 checksum {shortId(prediction.snapshot.checksum)} · 同意目前有效：
                  {prediction.consent_currently_active ? '是' : '否'}
                </div>
                <div style={{ marginTop: '6px', color: 'var(--text-secondary)' }}>
                  {prediction.disclaimer_zh}
                </div>
                <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
                  不適用於：{prediction.not_for.join('、')}
                </div>
              </div>
            )}
          </div>
        </>
      )}
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
