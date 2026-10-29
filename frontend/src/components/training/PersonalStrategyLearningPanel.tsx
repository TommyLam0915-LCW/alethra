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

interface ComparabilityCheck {
  code: string;
  ok: boolean;
  required: boolean;
  message_zh: string;
  detail?: unknown;
}

interface ComparabilityResponse {
  comparable: boolean;
  same_evaluation_set: boolean;
  checks: ComparabilityCheck[];
  not_comparable_reasons: Array<{ code: string; message_zh: string; detail?: unknown }>;
  quota?: { quota_type: string; amount: number };
  note_zh?: string;
}

interface ComparisonMetrics {
  test_size: number;
  accuracy: number;
  macro_f1: number;
  weighted_f1: number;
  per_class: Record<
    string,
    { precision: number; recall: number; f1: number; support: number }
  >;
  confusion_matrix: { labels: string[]; matrix: number[][] };
  class_distribution: Record<string, number>;
}

interface ComparisonWindow {
  index: number;
  start_decision_instant: string | null;
  end_decision_instant: string | null;
  n_rows: number;
  n_groups: number;
  accuracy: number;
  macro_f1: number;
  class_distribution: Record<string, number>;
  insufficient: boolean;
  insufficient_reasons: string[];
}

interface ComparisonVersion {
  model_version_id: string;
  version_number: number;
  model_type: string;
  artifact_sha256: string;
  artifact_verified: boolean;
  snapshot_id: string;
  snapshot_checksum: string;
  trained_at: string | null;
  is_active: boolean;
  revoked: boolean;
  evaluation_set: {
    test_size?: number;
    date_range_start?: string | null;
    date_range_end?: string | null;
    start_decision_instant?: string | null;
    end_decision_instant?: string | null;
    data_cutoff_at?: string | null;
    test_start_event_id?: string;
    test_end_event_id?: string;
  };
  metrics: ComparisonMetrics;
  baseline: { label?: string; accuracy?: number } | null;
  windows: ComparisonWindow[];
  warnings: string[];
  evaluation_record_id?: string;
}

interface ComparisonResult {
  comparison_run_id: string;
  mode: 'same_evaluation_set' | 'per_version_test_window';
  comparable: boolean;
  same_evaluation_set: boolean;
  rankable: boolean;
  checks: ComparabilityCheck[];
  versions: { a: ComparisonVersion; b: ComparisonVersion };
  delta: {
    accuracy: number;
    macro_f1: number;
    direction: string;
    note_zh: string;
  } | null;
  disagreement: {
    count: number;
    share: number;
    n_rows: number;
    note_zh: string;
  } | null;
  warnings: string[];
  caveats_zh: string[];
  summary_zh: string;
  evaluation_record_ids: Record<string, string>;
  created_at: string;
  operation_key?: string | null;
  idempotent_replay?: boolean;
  quota?: { quota_type: string; amount: number; settled?: boolean };
  not_for: string[];
}

interface SelectionEntry {
  selection_id: string;
  action: 'select' | 'rollback';
  version_id: string;
  version_number: number | null;
  previous_version_id: string | null;
  previous_version_number: number | null;
  comparison_run_id: string | null;
  rationale: string | null;
  created_at: string | null;
}

interface SelectionResponse {
  already_active: boolean;
  selection: SelectionEntry | null;
  active_version: PersonalModel;
  message_zh?: string;
}

interface SelectionHistoryResponse {
  record_id: string;
  active_version_id: string | null;
  selections: SelectionEntry[];
}

interface ComparisonSummaryEntry {
  comparison_run_id: string;
  record_id: string;
  mode: string;
  created_at: string | null;
  version_a: { model_version_id: string; version_number: number | null };
  version_b: { model_version_id: string; version_number: number | null };
  same_evaluation_set: boolean | null;
  rankable: boolean | null;
  summary_zh: string | null;
  warnings: string[];
  delta: ComparisonResult['delta'];
  disagreement: ComparisonResult['disagreement'];
}


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

const WINDOW_REASON_LABELS: Record<string, string> = {
  small_window: '此期間樣本偏少',
  missing_class_in_window: '此期間缺少部分類別',
};

const SELECTION_ACTION_LABELS: Record<string, string> = {
  select: '選用',
  rollback: '回退',
};

function actionLabel(value: string | null): string {
  if (!value) return '—';
  return ACTION_LABELS[value] ?? value;
}

function shortId(value: string | null | undefined): string {
  return value ? `${value.slice(0, 8)}…` : '—';
}

function formatScore(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(3) : '—';
}

const thStyle = {
  textAlign: 'left' as const,
  padding: '3px 6px',
  borderBottom: '1px solid var(--border)',
  fontWeight: 500,
};

const tdStyle = {
  padding: '3px 6px',
  borderBottom: '1px solid var(--border)',
};

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

  const [compA, setCompA] = useState('');
  const [compB, setCompB] = useState('');
  const [comparability, setComparability] = useState<ComparabilityResponse | null>(null);
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [rationale, setRationale] = useState('');
  const [selections, setSelections] = useState<SelectionEntry[]>([]);
  const [activeVersionId, setActiveVersionId] = useState<string | null>(null);
  const [comparisons, setComparisons] = useState<ComparisonSummaryEntry[]>([]);

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
    const modelRows = (modelsBody as { models: PersonalModel[] }).models;
    setModels(modelRows);
    const recordId = modelRows[0]?.record_id;
    if (recordId) {
      const [comparisonsBody, selectionsBody] = await Promise.all([
        api.ml.listPersonalComparisons(id),
        api.ml.listPersonalSelections(recordId),
      ]);
      setComparisons(
        (comparisonsBody as { comparisons: ComparisonSummaryEntry[] }).comparisons ?? [],
      );
      const history = selectionsBody as SelectionHistoryResponse;
      setSelections(history.selections ?? []);
      setActiveVersionId(history.active_version_id ?? null);
    } else {
      setComparisons([]);
      setSelections([]);
      setActiveVersionId(null);
    }
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

  useEffect(() => {
    const ids = models.map((model) => model.model_version_id);
    if (ids.length === 0) {
      if (compA) setCompA('');
      if (compB) setCompB('');
      return;
    }
    // Keep an explicit choice across refreshes: comparing resets nothing, and a
    // reload must not silently turn B into A.
    const nextA = compA && ids.includes(compA) ? compA : ids[0];
    const nextB =
      compB && ids.includes(compB) && compB !== nextA
        ? compB
        : ids.find((id) => id !== nextA) ?? '';
    if (nextA !== compA) setCompA(nextA);
    if (nextB !== compB) setCompB(nextB);
  }, [models, compA, compB]);

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

  const checkComparability = async () => {
    if (!compA || !compB) {
      setError('請先選擇兩個模型版本作比較。');
      return;
    }
    setBusy('comparability');
    setError(null);
    setNotice(null);
    setComparison(null);
    try {
      const body = (await api.ml.checkPersonalModelComparability(
        compA,
        compB,
      )) as ComparabilityResponse;
      setComparability(body);
      setNotice(
        body.comparable
          ? body.same_evaluation_set
            ? '兩個版本可比，並共用同一評估集。'
            : '兩個版本可比，但測試窗口不同；只可並列參考，不可以排名。'
          : '兩個版本目前不可比；原因已列在下方。',
      );
    } catch (err) {
      setError(describeFailure(err, '無法檢查版本可比性'));
    } finally {
      setBusy(null);
    }
  };

  const runComparison = async () => {
    if (!compA || !compB) {
      setError('請先選擇兩個模型版本作比較。');
      return;
    }
    setBusy('comparison');
    setError(null);
    setNotice(null);
    try {
      const body = (await api.ml.runPersonalModelComparison(
        compA,
        compB,
      )) as ComparisonResult;
      setComparison(body);
      setComparability({
        comparable: body.comparable,
        same_evaluation_set: body.same_evaluation_set,
        checks: body.checks,
        not_comparable_reasons: [],
      });
      setNotice(
        body.idempotent_replay
          ? '已回傳先前保存的比較結果，沒有再扣額度。'
          : '比較完成；結果與評估記錄已保存，耗用 1 個 AI 使用額度。',
      );
      if (versionId) await loadStrategy(versionId);
    } catch (err) {
      setError(describeFailure(err, '版本比較未完成'));
    } finally {
      setBusy(null);
    }
  };

  const selectVersion = async (role: 'a' | 'b') => {
    const targetId = role === 'a' ? compA : compB;
    const target = models.find((model) => model.model_version_id === targetId);
    if (!target) {
      setError('請先選擇要使用的版本。');
      return;
    }
    if (rationale.trim().length < 2) {
      setError('選用版本需要填寫理由（至少 2 個字）。');
      return;
    }
    const runMatchesTarget =
      comparison &&
      [comparison.versions.a.model_version_id, comparison.versions.b.model_version_id].includes(
        targetId,
      )
        ? comparison.comparison_run_id
        : null;
    setBusy(`select-${role}`);
    setError(null);
    setNotice(null);
    try {
      const body = (await api.ml.selectPersonalModel(targetId, {
        rationale: rationale.trim(),
        comparison_run_id: runMatchesTarget,
      })) as SelectionResponse;
      setNotice(body.message_zh ?? `已選用版本 ${target.version_number}。`);
      setRationale('');
      if (versionId) await loadStrategy(versionId);
    } catch (err) {
      setError(describeFailure(err, '無法切換使用中版本'));
    } finally {
      setBusy(null);
    }
  };

  const rollbackVersion = async () => {
    const recordId = models[0]?.record_id;
    if (!recordId) {
      setError('找不到可回退的模型記錄。');
      return;
    }
    setBusy('rollback');
    setError(null);
    setNotice(null);
    try {
      const body = (await api.ml.rollbackPersonalModel(
        recordId,
        rationale.trim() || null,
      )) as { selection: SelectionEntry; active_version: PersonalModel; message_zh?: string };
      setNotice(body.message_zh ?? '已回退到上一個版本。');
      setRationale('');
      if (versionId) await loadStrategy(versionId);
    } catch (err) {
      setError(describeFailure(err, '無法回退版本'));
    } finally {
      setBusy(null);
    }
  };

  const loadSavedComparison = async (runId: string) => {
    setBusy(`load-${runId}`);
    setError(null);
    setNotice(null);
    try {
      const body = (await api.ml.getPersonalComparison(runId)) as ComparisonResult;
      setComparison(body);
      setCompA(body.versions.a.model_version_id);
      setCompB(body.versions.b.model_version_id);
      setComparability({
        comparable: body.comparable,
        same_evaluation_set: body.same_evaluation_set,
        checks: body.checks,
        not_comparable_reasons: [],
      });
      setNotice('已載入先前保存的比較結果。');
    } catch (err) {
      setError(describeFailure(err, '無法載入已保存的比較'));
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
          <div
            data-testid="pl-compare"
            style={{ marginTop: '22px', paddingTop: '16px', borderTop: '1px solid var(--border)' }}
          >
            <strong style={{ fontSize: '14px' }}>5. 版本比較與選用</strong>
            <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-secondary)' }}>
              只比較同一任務、同一策略範圍、同一特徵與同一評估方法的個人模型版本。可比性檢查屬唯讀、不扣額度；
              執行比較會在凍結快照的測試列上重新載入 artifact 並計算指標，消耗 1 個 AI 使用額度。
              分數只反映模仿本人已確認決策的能力，不是投資回報，也不代表越用越準。
            </p>

            {models.length < 2 ? (
              <p style={{ margin: '10px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                此策略目前只有 {models.length} 個模型版本；至少需要兩個版本才能比較與選用。
              </p>
            ) : (
              <>
                <div
                  style={{
                    marginTop: '10px',
                    display: 'flex',
                    gap: '12px',
                    alignItems: 'end',
                    flexWrap: 'wrap',
                    fontSize: '12px',
                  }}
                >
                  <label style={{ display: 'grid', gap: '4px' }}>
                    <span style={{ color: 'var(--text-muted)' }}>版本 A</span>
                    <select
                      data-testid="pl-compare-a"
                      value={compA}
                      onChange={(event) => {
                        setCompA(event.target.value);
                        setComparability(null);
                        setComparison(null);
                      }}
                      style={{ minWidth: '200px', padding: '5px 8px' }}
                    >
                      {models.map((model) => (
                        <option key={model.model_version_id} value={model.model_version_id}>
                          版本 {model.version_number}
                          {model.is_active ? ' · 使用中' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label style={{ display: 'grid', gap: '4px' }}>
                    <span style={{ color: 'var(--text-muted)' }}>版本 B</span>
                    <select
                      data-testid="pl-compare-b"
                      value={compB}
                      onChange={(event) => {
                        setCompB(event.target.value);
                        setComparability(null);
                        setComparison(null);
                      }}
                      style={{ minWidth: '200px', padding: '5px 8px' }}
                    >
                      {models.map((model) => (
                        <option key={model.model_version_id} value={model.model_version_id}>
                          版本 {model.version_number}
                          {model.is_active ? ' · 使用中' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    className="btn"
                    data-testid="pl-compare-check"
                    onClick={checkComparability}
                    disabled={busy !== null || !compA || !compB}
                  >
                    檢查可比性（免費）
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    data-testid="pl-compare-run"
                    onClick={runComparison}
                    disabled={busy !== null || !compA || !compB}
                  >
                    {busy === 'comparison' ? '比較中…' : '執行比較（消耗 1 AI 額度）'}
                  </button>
                </div>

                {comparability && (
                  <div data-testid="pl-compare-checks" style={{ marginTop: '10px', fontSize: '12px' }}>
                    <div
                      style={{
                        fontWeight: 600,
                        color: comparability.comparable ? 'var(--positive)' : 'var(--warning)',
                      }}
                    >
                      {comparability.comparable ? '可比' : '不可比'}
                      {comparability.comparable &&
                        (comparability.same_evaluation_set
                          ? ' · 同一評估集'
                          : ' · 各自測試窗口（只可並列參考）')}
                    </div>
                    <ul style={{ margin: '6px 0 0', paddingLeft: '18px', color: 'var(--text-secondary)' }}>
                      {comparability.checks.map((check) => (
                        <li
                          key={check.code}
                          style={{
                            color: check.ok
                              ? 'var(--text-secondary)'
                              : check.required
                                ? 'var(--warning)'
                                : 'var(--text-muted)',
                          }}
                        >
                          {check.ok ? '✓' : '✗'} {check.message_zh}
                          {!check.required && !check.ok ? '（非必要）' : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {comparison && (
                  <div data-testid="pl-comparison-result" style={{ marginTop: '14px' }}>
                    <div
                      style={{
                        display: 'flex',
                        gap: '8px',
                        flexWrap: 'wrap',
                        alignItems: 'center',
                        fontSize: '12px',
                      }}
                    >
                      <strong>{comparison.summary_zh}</strong>
                      <span
                        style={{
                          padding: '1px 8px',
                          borderRadius: '10px',
                          border: '1px solid currentColor',
                          color: comparison.rankable ? 'var(--positive)' : 'var(--text-secondary)',
                        }}
                      >
                        {comparison.mode === 'same_evaluation_set' ? '同一評估集' : '各自測試窗口'}
                      </span>
                      {!comparison.rankable && (
                        <span style={{ color: 'var(--warning)' }}>不可排名：兩邊測試列不同</span>
                      )}
                      {comparison.idempotent_replay && (
                        <span style={{ color: 'var(--text-muted)' }}>重複請求，已回傳保存結果</span>
                      )}
                    </div>

                    <div
                      style={{
                        marginTop: '10px',
                        display: 'grid',
                        gap: '12px',
                        gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
                      }}
                    >
                      <ComparisonVersionCard label="A" version={comparison.versions.a} />
                      <ComparisonVersionCard label="B" version={comparison.versions.b} />
                    </div>

                    {comparison.delta && (
                      <div
                        data-testid="pl-comparison-delta"
                        style={{ marginTop: '10px', fontSize: '12px' }}
                      >
                        <strong>差值（A − B）：</strong> 準確率{' '}
                        {comparison.delta.accuracy >= 0 ? '+' : ''}
                        {comparison.delta.accuracy.toFixed(4)} · macro F1{' '}
                        {comparison.delta.macro_f1 >= 0 ? '+' : ''}
                        {comparison.delta.macro_f1.toFixed(4)}
                        <div style={{ color: 'var(--text-muted)', marginTop: '2px' }}>
                          {comparison.delta.note_zh}
                        </div>
                      </div>
                    )}

                    {comparison.disagreement && (
                      <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                        不一致：{comparison.disagreement.count}／{comparison.disagreement.n_rows} 列（
                        {(comparison.disagreement.share * 100).toFixed(1)}%）。
                        {comparison.disagreement.note_zh}
                      </div>
                    )}

                    {comparison.warnings.length > 0 && (
                      <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--warning)' }}>
                        警告：{comparison.warnings.map((code) => WARNING_LABELS[code] ?? code).join(' · ')}
                      </div>
                    )}

                    <ul
                      style={{
                        margin: '8px 0 0',
                        paddingLeft: '18px',
                        fontSize: '12px',
                        color: 'var(--text-muted)',
                      }}
                    >
                      {comparison.caveats_zh.map((caveat) => (
                        <li key={caveat}>{caveat}</li>
                      ))}
                    </ul>
                    <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
                      不適用於：{comparison.not_for.join('、')}。
                    </div>
                  </div>
                )}

                <div
                  style={{
                    marginTop: '14px',
                    paddingTop: '12px',
                    borderTop: '1px dashed var(--border)',
                    fontSize: '12px',
                  }}
                >
                  <strong>選用或回退版本</strong>
                  <div style={{ marginTop: '6px', color: 'var(--text-muted)' }}>
                    切換使用中版本不會刪除任何版本、artifact 或已保存的評估；使用中別名推論會回傳解析後的固定版本。
                  </div>
                  <label style={{ display: 'grid', gap: '4px', marginTop: '8px' }}>
                    <span style={{ color: 'var(--text-muted)' }}>選用理由（至少 2 個字，會永久保存）</span>
                    <textarea
                      data-testid="pl-selection-rationale"
                      value={rationale}
                      onChange={(event) => setRationale(event.target.value)}
                      rows={2}
                      style={{ padding: '6px 8px', resize: 'vertical' }}
                    />
                  </label>
                  <div style={{ marginTop: '8px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      className="btn"
                      data-testid="pl-select-a"
                      onClick={() => selectVersion('a')}
                      disabled={busy !== null || !comparison}
                    >
                      選用版本 A
                    </button>
                    <button
                      type="button"
                      className="btn"
                      data-testid="pl-select-b"
                      onClick={() => selectVersion('b')}
                      disabled={busy !== null || !comparison}
                    >
                      選用版本 B
                    </button>
                    <button
                      type="button"
                      className="btn"
                      data-testid="pl-rollback"
                      onClick={rollbackVersion}
                      disabled={busy !== null || !selections[0]?.previous_version_id}
                    >
                      回退到上一個版本
                    </button>
                  </div>
                  {activeVersionId && (
                    <div style={{ marginTop: '6px', color: 'var(--text-secondary)' }}>
                      目前使用中：版本{' '}
                      {models.find((model) => model.model_version_id === activeVersionId)
                        ?.version_number ?? shortId(activeVersionId)}
                    </div>
                  )}
                </div>

                {comparisons.length > 0 && (
                  <div style={{ marginTop: '14px', fontSize: '12px' }}>
                    <strong>已保存的比較</strong>
                    <div style={{ display: 'grid', gap: '6px', marginTop: '6px' }}>
                      {comparisons.slice(0, 5).map((run) => (
                        <div
                          key={run.comparison_run_id}
                          data-testid={`pl-comparison-history-${run.comparison_run_id}`}
                          style={{
                            display: 'flex',
                            gap: '8px',
                            flexWrap: 'wrap',
                            alignItems: 'center',
                            padding: '6px 8px',
                            border: '1px solid var(--border)',
                            borderRadius: '6px',
                          }}
                        >
                          <span style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                            {shortId(run.comparison_run_id)}
                          </span>
                          <span>
                            版本 {run.version_a.version_number ?? '—'} vs{' '}
                            {run.version_b.version_number ?? '—'}
                          </span>
                          <span style={{ color: run.rankable ? 'var(--positive)' : 'var(--text-muted)' }}>
                            {run.rankable ? '同一評估集' : '各自測試窗口'}
                          </span>
                          <span style={{ color: 'var(--text-muted)' }}>
                            {run.created_at ? run.created_at.slice(0, 19).replace('T', ' ') : '—'}
                          </span>
                          <button
                            type="button"
                            className="btn"
                            data-testid={`pl-comparison-load-${run.comparison_run_id}`}
                            onClick={() => loadSavedComparison(run.comparison_run_id)}
                            disabled={busy !== null}
                          >
                            載入結果
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {selections.length > 0 && (
                  <div style={{ marginTop: '12px', fontSize: '12px' }}>
                    <strong>版本選用紀錄</strong>
                    <div style={{ display: 'grid', gap: '4px', marginTop: '6px' }}>
                      {selections.map((entry) => (
                        <div
                          key={entry.selection_id}
                          data-testid={`pl-selection-${entry.selection_id}`}
                          style={{ color: 'var(--text-secondary)' }}
                        >
                          {entry.created_at ? entry.created_at.slice(0, 19).replace('T', ' ') : '—'} ·{' '}
                          {SELECTION_ACTION_LABELS[entry.action] ?? entry.action} 版本{' '}
                          {entry.version_number ?? shortId(entry.version_id)}
                          {entry.previous_version_number !== null
                            ? `（原版本 ${entry.previous_version_number}）`
                            : ''}
                          {entry.rationale ? ` · 理由：${entry.rationale}` : ''}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
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

function ComparisonVersionCard({
  label,
  version,
}: {
  label: string;
  version: ComparisonVersion;
}) {
  const metrics = version.metrics;
  const matrix = metrics.confusion_matrix;
  return (
    <div
      data-testid={`pl-comparison-version-${label}`}
      style={{
        padding: '12px',
        border: '1px solid var(--border)',
        borderRadius: '8px',
        background: 'var(--surface-2)',
        fontSize: '12px',
        minWidth: 0,
      }}
    >
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
        <strong>
          {label} · 版本 {version.version_number}
        </strong>
        <span style={{ color: 'var(--text-muted)' }}>{version.model_type}</span>
        {version.is_active && <span style={{ color: 'var(--positive)' }}>使用中</span>}
        {version.revoked && <span style={{ color: 'var(--warning)' }}>已撤銷</span>}
        <span style={{ fontFamily: 'monospace', color: 'var(--text-muted)' }}>
          artifact {shortId(version.artifact_sha256)}
          {version.artifact_verified ? '（已核對）' : '（未核對）'}
        </span>
      </div>

      <div style={{ marginTop: '6px', color: 'var(--text-secondary)' }}>
        測試列 {metrics.test_size} 筆 · 準確率 {formatScore(metrics.accuracy)} · macro F1{' '}
        {formatScore(metrics.macro_f1)} · weighted F1 {formatScore(metrics.weighted_f1)}
        {version.baseline && typeof version.baseline.accuracy === 'number'
          ? ` · 訓練基線 ${formatScore(version.baseline.accuracy)}`
          : ''}
      </div>
      <div style={{ marginTop: '4px', color: 'var(--text-muted)' }}>
        測試期 {version.evaluation_set.date_range_start ?? '—'} 至{' '}
        {version.evaluation_set.date_range_end ?? '—'} · 資料截止{' '}
        {version.evaluation_set.data_cutoff_at ?? '—'} · 快照 {shortId(version.snapshot_checksum)}
      </div>

      <div style={{ marginTop: '8px', overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: '11px', minWidth: '320px' }}>
          <thead>
            <tr style={{ color: 'var(--text-muted)' }}>
              <th style={thStyle}>類別</th>
              <th style={thStyle}>精確率</th>
              <th style={thStyle}>召回率</th>
              <th style={thStyle}>F1</th>
              <th style={thStyle}>樣本</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(metrics.per_class).map(([className, row]) => (
              <tr key={className}>
                <td style={tdStyle}>{actionLabel(className)}</td>
                <td style={tdStyle}>{formatScore(row.precision)}</td>
                <td style={tdStyle}>{formatScore(row.recall)}</td>
                <td style={tdStyle}>{formatScore(row.f1)}</td>
                <td style={tdStyle}>{row.support}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: '8px' }}>
        <div style={{ color: 'var(--text-muted)' }}>混淆矩陣（列＝實際，欄＝預測）</div>
        <div style={{ overflowX: 'auto', marginTop: '4px' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: '11px' }}>
            <thead>
              <tr>
                <th style={thStyle}></th>
                {matrix.labels.map((rowLabel) => (
                  <th key={rowLabel} style={thStyle}>
                    {actionLabel(rowLabel)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.matrix.map((row, rowIndex) => (
                <tr key={matrix.labels[rowIndex]}>
                  <th style={thStyle}>{actionLabel(matrix.labels[rowIndex])}</th>
                  {row.map((count, columnIndex) => (
                    <td key={matrix.labels[columnIndex]} style={tdStyle}>
                      {count}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {version.windows.length > 0 && (
        <div style={{ marginTop: '8px' }}>
          <div style={{ color: 'var(--text-muted)' }}>分期間表現（測試期內按決策時點分組）</div>
          <div style={{ display: 'grid', gap: '6px', marginTop: '4px' }}>
            {version.windows.map((window) => (
              <div
                key={window.index}
                data-testid={`pl-comparison-window-${label}-${window.index}`}
                style={{ padding: '6px 8px', border: '1px solid var(--border)', borderRadius: '6px' }}
              >
                <div style={{ color: 'var(--text-secondary)' }}>
                  期間 {window.index}：{window.start_decision_instant ?? '—'} →{' '}
                  {window.end_decision_instant ?? '—'} · {window.n_rows} 列／{window.n_groups} 組
                </div>
                <div>
                  準確率 {formatScore(window.accuracy)} · macro F1 {formatScore(window.macro_f1)}
                </div>
                {window.insufficient && (
                  <div style={{ color: 'var(--warning)' }}>
                    {window.insufficient_reasons
                      .map((code) => WINDOW_REASON_LABELS[code] ?? code)
                      .join(' · ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {version.warnings.length > 0 && (
        <div style={{ marginTop: '6px', color: 'var(--warning)' }}>
          警告：{version.warnings.map((code) => WARNING_LABELS[code] ?? code).join(' · ')}
        </div>
      )}
    </div>
  );
}
