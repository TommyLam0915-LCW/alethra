'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import AppShell from '@/components/shell/AppShell';
import { api, ApiError } from '@/lib/api';
import { formatDate, formatDateTime } from '@/lib/format';
import { statusPillClass } from '@/lib/status-styles';

const SUPPORTED_RULE = 'sma_crossover';
const ACCEPT_PHRASE = 'I ACCEPT';

interface CapabilitiesPayload {
  capability: {
    key: string;
    label_zh: string;
    status: string;
    status_label_zh: string;
    description_zh: string;
    task_type: string;
    rule_strategy_scope: string;
    surface: string;
  };
  enabled_in_this_process: boolean;
  snapshot_creation: {
    implemented: boolean;
    environment_allowed: boolean;
    purpose_authorized: boolean;
    authorization_mode: string;
    real_owner_consent: boolean;
    owner_consent_mode: boolean;
    movable_to_customer_environment: boolean;
    usable_for_fixture_training: boolean;
    usable_for_owner_trial_training: boolean;
    usable_for_real_training: boolean;
  };
  training: {
    implementation: string;
    authorized_in_this_process: boolean;
    authorization_mode: string;
    local_owner_trial_authorized: boolean;
    consent_status: string;
    customer_release_training: string;
    customer_release_inference: string;
    model_activation: string;
    activation_available: boolean;
  };
}

interface ConsentStatus {
  purpose: string;
  consent_version: string;
  status: string;
  fail_closed: boolean;
  reason_code: string | null;
  accepted_at: string | null;
  accepted_by: string | null;
  acceptance_text_version?: string;
  consent_id?: string;
  revoked_at?: string | null;
  revoke_reason?: string | null;
  real_owner_consent?: boolean;
}

interface ConsentPolicyPayload {
  policy: {
    purpose: string;
    consent_version: string;
    acceptance_text_version: string;
    acceptance_text_zh: string;
    acceptance_available: boolean;
    acceptance_owner_only: boolean;
    fail_closed: boolean;
    reason_code: string;
    data_scope_zh: string;
    retention_zh: string;
    revoke_zh: string;
    output_limits_zh: string;
    training_allowed: boolean;
  };
  status: ConsentStatus;
}

interface VersionOption {
  id: string;
  experiment_id: string;
  experiment_title: string;
  version_number: number;
  locked_at: string;
  template_type: string;
  asset_ids: string[];
}

interface CandidateOutcome {
  outcome_id: string;
  version_seq: number;
  is_current: boolean;
  selection_reason: string | null;
  outcome_status: string;
  label_maturity: string | null;
  label_eligible: boolean;
  outcome_source: string | null;
  counterfactual: boolean;
  entry_execution_date: string | null;
  exit_execution_date: string | null;
  gross_return_pct: number | null;
  net_return_pct: number | null;
  exclusion_reason: string | null;
}

interface CandidateRow {
  id: string;
  instrument_id: string;
  signal_date: string;
  logged_action: string;
  created_by_actor_type: string;
  execution_feasibility: string;
  rejection_reason: string | null;
  rule_parameters: Record<string, unknown>;
  outcome: CandidateOutcome | null;
}

interface CandidatesPayload {
  strategy_version_id: string;
  candidate_count: number;
  candidates: CandidateRow[];
}

interface CollectPayload {
  strategy_version_id: string;
  instrument_id: string;
  rule_key: string;
  rule_parameters: Record<string, unknown>;
  stored_bars: number;
  created: number;
  existing: number;
  feature_excluded: number;
  execution_feasibility: Record<string, number>;
  candidates: CandidateRow[];
}

interface BuildOutcomesPayload {
  strategy_version_id: string;
  created: number;
  superseded: number;
  preserved: number;
  preserved_observed: number;
  outcome_distribution: Record<string, number>;
}

interface EligibilityReason {
  code: string;
  message_zh: string;
  measured: unknown;
  requirement: string;
}

interface EligibilityPayload {
  strategy_version_id: string;
  candidate_count: number;
  distinct_signal_dates: number;
  eligible_count: number;
  distinct_eligible_dates: number;
  outcome_distribution: Record<string, number>;
  exclusion_breakdown: Record<string, number>;
  exclusion_labels_zh: Record<string, string>;
  reasons: EligibilityReason[];
  screening_ready: boolean;
  training_ready: boolean;
  training_blocked_by: string | null;
  training_authorization: Record<string, unknown>;
  not_human_quality_evaluation: boolean;
}

interface SplitSide {
  candidate_ids: string[];
  date_start: string | null;
  date_end: string | null;
  count: number;
}

interface SplitManifest {
  policy_version: string;
  no_shuffle: boolean;
  grouping: string;
  gap_days: number;
  purge_rule: string;
  train_fraction: number;
  validation_fraction: number;
  total_eligible: number;
  splits: { train: SplitSide; validation: SplitSide; test: SplitSide };
  purged: Array<{ candidate_id: string; reason: string }>;
  refusal_reason: string | null;
}

interface PreviewRow {
  candidate_id: string;
  instrument_id: string;
  signal_date: string;
  features: Record<string, number>;
  outcome_status: string;
  outcome_source: string;
  counterfactual: boolean;
  net_return_pct: number | null;
}

interface PreviewPayload {
  strategy_version_id: string;
  status: string;
  consent: ConsentStatus;
  screening_ready: boolean;
  training_ready: boolean;
  training_blocked_by: string | null;
  feature_names: string[];
  rows: PreviewRow[];
  included_count: number;
  excluded_count: number;
  date_range_start: string | null;
  date_range_end: string | null;
  data_cutoff_at: string | null;
  exclusion_breakdown: Record<string, number>;
  exclusion_labels_zh: Record<string, string>;
  outcome_distribution: Record<string, number>;
  split_manifest: SplitManifest;
  checksum: string;
}

interface SnapshotPayload {
  id: string;
  strategy_version_id: string;
  purpose: string;
  consent_policy_version: string;
  consent_status: string;
  authorization: {
    basis: string;
    policy_version: string;
    real_owner_consent: boolean;
    movable_to_customer_environment: boolean;
  };
  status: string;
  rows: PreviewRow[];
  feature_names: string[];
  included_count: number;
  excluded_count: number;
  date_range_start: string | null;
  date_range_end: string | null;
  data_cutoff_at: string | null;
  checksum: string;
  integrity: { checksum_verified: boolean | null; recomputed_checksum?: string };
  failure_reason: string | null;
  created_at: string | null;
  usable_for_fixture_training: boolean;
  usable_for_owner_trial_training: boolean;
  usable_for_real_training: boolean;
  created?: boolean;
  deduplicated?: boolean;
}

interface TrainingJob {
  job_id: string;
  snapshot_id: string;
  strategy_version_id: string;
  status: string;
  training_contract_version: string;
  derived_label_version: string;
  dataset_checksum: string;
  hyperparameters: Record<string, unknown>;
  row_counts: Record<string, unknown> | null;
  metrics: Record<string, unknown> | null;
  lineage: Record<string, unknown> | null;
  artifact_path: string | null;
  artifact_checksum: string | null;
  activation_status: string;
  failure_reason: string | null;
  created_at: string | null;
  completed_at: string | null;
  model_activated: boolean;
  runs_inference_for_customer: boolean;
  movable_to_customer_environment: boolean;
  activation_available?: boolean;
  model_activation?: string;
}

interface InferencePayload {
  job_id: string;
  snapshot_id: string;
  decision: string;
  positive_probability: number;
  threshold: number;
  feature_names: string[];
  inference_policy_version: string;
  training_contract_version: string;
  dataset_checksum: string;
  artifact_checksum: string;
  lineage_verified: boolean;
  artifact_verified: boolean;
  activation_status: string;
  model_activated: boolean;
  runs_inference_for_customer: boolean;
  not_a_recommendation: boolean;
  not_a_profit_claim: boolean;
}

type Tone = 'ok' | 'warn' | 'err';
interface Notice {
  tone: Tone;
  text: string;
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const detail = (err.detail ?? {}) as Record<string, unknown>;
    const code = typeof detail.code === 'string' ? ` (${detail.code})` : '';
    return `${err.message}${code}`;
  }
  return err instanceof Error ? err.message : fallback;
}

function errorTone(err: unknown): Tone {
  if (err instanceof ApiError) {
    // A refusal is a decision, not a defect: consent, authorization and data
    // gates answer 409 with the reason the write did not happen.
    if (err.status === 409) return 'warn';
  }
  return 'err';
}

function toneStyle(tone: Tone): React.CSSProperties {
  if (tone === 'ok') {
    return { background: 'rgba(34,197,94,0.12)', border: '1px solid var(--positive)' };
  }
  if (tone === 'warn') {
    return { background: 'rgba(245,158,11,0.12)', border: '1px solid var(--warning)' };
  }
  return { background: 'rgba(239,68,68,0.12)', border: '1px solid var(--negative)' };
}

function NoticeBox({ notice, testId }: { notice: Notice | null; testId: string }) {
  if (!notice) return null;
  return (
    <div
      role={notice.tone === 'err' ? 'alert' : 'status'}
      data-testid={testId}
      style={{ ...toneStyle(notice.tone), padding: '12px', borderRadius: '4px', marginBottom: '16px', fontSize: '13px' }}
    >
      {notice.text}
    </div>
  );
}

function FactRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: '12px', padding: '6px 0', borderBottom: '1px solid var(--border-subtle)' }}>
      <span style={{ minWidth: '220px', color: 'var(--text-secondary)', fontSize: '13px' }}>{label}</span>
      <span style={{ fontSize: '13px', wordBreak: 'break-word' }}>{value}</span>
    </div>
  );
}

function YesNo({ value, yes = 'Yes', no = 'No' }: { value: boolean; yes?: string; no?: string }) {
  return (
    <span style={{ color: value ? 'var(--positive)' : 'var(--text-secondary)' }}>
      {value ? yes : no}
    </span>
  );
}

function formatMetricValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toFixed(6);
  }
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${value.toFixed(2)}%`;
}

function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ');
}

function MetricGrid({ data, testId }: { data: Record<string, unknown> | null; testId: string }) {
  if (!data || Object.keys(data).length === 0) {
    return <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0 }}>Nothing recorded.</p>;
  }
  return (
    <div
      data-testid={testId}
      style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '12px' }}
    >
      {Object.entries(data).map(([key, value]) => (
        <div key={key} style={{ border: '1px solid var(--border-subtle)', borderRadius: '4px', padding: '10px' }}>
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)' }}>
            {humanizeKey(key)}
          </div>
          <div style={{ fontSize: '13px', marginTop: '4px', wordBreak: 'break-word' }}>{formatMetricValue(value)}</div>
        </div>
      ))}
    </div>
  );
}

export default function AgentStrategyPage() {
  const [caps, setCaps] = useState<CapabilitiesPayload | null>(null);
  const [policy, setPolicy] = useState<ConsentPolicyPayload | null>(null);
  const [statusError, setStatusError] = useState('');
  const [statusLoading, setStatusLoading] = useState(true);

  const [ackRead, setAckRead] = useState(false);
  const [ackPhrase, setAckPhrase] = useState('');
  const [consentBusy, setConsentBusy] = useState(false);
  const [consentNotice, setConsentNotice] = useState<Notice | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [revokeReason, setRevokeReason] = useState('');

  const [versions, setVersions] = useState<VersionOption[]>([]);
  const [versionsError, setVersionsError] = useState('');
  const [versionsLoading, setVersionsLoading] = useState(true);
  const [versionId, setVersionId] = useState('');

  const [busy, setBusy] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<Notice | null>(null);
  const [collectResult, setCollectResult] = useState<CollectPayload | null>(null);
  const [outcomeResult, setOutcomeResult] = useState<BuildOutcomesPayload | null>(null);
  const [candidates, setCandidates] = useState<CandidatesPayload | null>(null);
  const [eligibility, setEligibility] = useState<EligibilityPayload | null>(null);
  const [preview, setPreview] = useState<PreviewPayload | null>(null);
  const [snapshot, setSnapshot] = useState<SnapshotPayload | null>(null);

  const [jobs, setJobs] = useState<TrainingJob[]>([]);
  const [jobsError, setJobsError] = useState('');
  const [job, setJob] = useState<TrainingJob | null>(null);
  const [featureInputs, setFeatureInputs] = useState<Record<string, string>>({});
  const [inference, setInference] = useState<InferencePayload | null>(null);

  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    setStatusError('');
    try {
      const [capability, consent] = await Promise.all([
        api.agentStrategy.capabilities() as Promise<CapabilitiesPayload>,
        api.agentStrategy.consentPolicy() as Promise<ConsentPolicyPayload>,
      ]);
      setCaps(capability);
      setPolicy(consent);
    } catch (err) {
      setStatusError(errorMessage(err, 'Could not load the screening status.'));
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const loadJobs = useCallback(async () => {
    setJobsError('');
    try {
      const response = (await api.agentStrategy.listTrainingJobs()) as { jobs: TrainingJob[] };
      setJobs(response.jobs ?? []);
    } catch (err) {
      setJobsError(errorMessage(err, 'Could not load training jobs.'));
    }
  }, []);

  const loadVersions = useCallback(async () => {
    setVersionsLoading(true);
    setVersionsError('');
    try {
      const experiments = (await api.experiments.list()) as Array<{ id: string; title: string }>;
      const lists = await Promise.all(
        experiments.slice(0, 25).map(async (experiment) => {
          const rows = (await api.experiments.versions(experiment.id)) as Array<{
            id: string;
            version_number: number;
            locked_at: string | null;
            strategy_spec: { template_type?: string; asset_ids?: string[] } | null;
          }>;
          return rows
            .filter((row) => row.locked_at)
            .map((row) => ({
              id: row.id,
              experiment_id: experiment.id,
              experiment_title: experiment.title,
              version_number: row.version_number,
              locked_at: row.locked_at as string,
              template_type: row.strategy_spec?.template_type ?? 'unknown',
              asset_ids: row.strategy_spec?.asset_ids ?? [],
            }));
        }),
      );
      const flat = lists.flat().sort((a, b) => b.locked_at.localeCompare(a.locked_at));
      setVersions(flat);
    } catch (err) {
      setVersionsError(errorMessage(err, 'Could not load your locked strategy versions.'));
    } finally {
      setVersionsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadJobs();
    void loadVersions();
  }, [loadStatus, loadJobs, loadVersions]);

  useEffect(() => {
    if (!job || job.status !== 'completed' || !snapshot) return;
    const first = snapshot.rows?.[0];
    if (!first?.features) return;
    const seed: Record<string, string> = {};
    for (const [name, value] of Object.entries(first.features)) {
      seed[name] = String(value);
    }
    setFeatureInputs(seed);
  }, [job, snapshot]);

  const selectedVersion = useMemo(
    () => versions.find((row) => row.id === versionId) ?? null,
    [versions, versionId],
  );

  const consentActive = policy?.status.status === 'active';
  const screeningEnabled = caps?.enabled_in_this_process === true;
  const acceptReady =
    Boolean(policy) &&
    consentActive === false &&
    screeningEnabled &&
    ackRead &&
    ackPhrase.trim().toUpperCase() === ACCEPT_PHRASE &&
    !consentBusy;

  const runAction = useCallback(
    async (key: string, action: () => Promise<string | null>) => {
      setBusy(key);
      setActionNotice(null);
      try {
        const message = await action();
        if (message) setActionNotice({ tone: 'ok', text: message });
      } catch (err) {
        setActionNotice({ tone: errorTone(err), text: errorMessage(err, 'The request failed.') });
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const refreshAnalysis = useCallback(async () => {
    if (!versionId) return;
    const [candidateRows, eligibilityRows] = await Promise.all([
      api.agentStrategy.candidates(versionId) as Promise<CandidatesPayload>,
      api.agentStrategy.eligibility(versionId) as Promise<EligibilityPayload>,
    ]);
    setCandidates(candidateRows);
    setEligibility(eligibilityRows);
  }, [versionId]);

  const onVersionChange = (value: string) => {
    setVersionId(value);
    setCollectResult(null);
    setOutcomeResult(null);
    setCandidates(null);
    setEligibility(null);
    setPreview(null);
    setSnapshot(null);
    setJob(null);
    setInference(null);
    setActionNotice(null);
    if (!value) return;
    void (async () => {
      try {
        const [candidateRows, eligibilityRows] = await Promise.all([
          api.agentStrategy.candidates(value) as Promise<CandidatesPayload>,
          api.agentStrategy.eligibility(value) as Promise<EligibilityPayload>,
        ]);
        setCandidates(candidateRows);
        setEligibility(eligibilityRows);
      } catch (err) {
        setActionNotice({ tone: errorTone(err), text: errorMessage(err, 'Could not load this version.') });
      }
    })();
  };

  const onAcceptConsent = async () => {
    if (!policy || !acceptReady) return;
    setConsentBusy(true);
    setConsentNotice(null);
    try {
      // The owner performs this action. The agent never signs and never fills
      // in a date: the server stamps the acceptance time from its own clock.
      await api.agentStrategy.acceptConsent({
        policy_version: policy.policy.consent_version,
        acceptance_text_version: policy.policy.acceptance_text_version,
        acknowledgement: policy.policy.acceptance_text_zh,
      });
      setAckRead(false);
      setAckPhrase('');
      await loadStatus();
      setConsentNotice({ tone: 'ok', text: 'Consent recorded for this account.' });
    } catch (err) {
      setConsentNotice({ tone: errorTone(err), text: errorMessage(err, 'Consent was not recorded.') });
    } finally {
      setConsentBusy(false);
    }
  };

  const onRevokeConsent = async () => {
    if (!revokeOpen) {
      setRevokeOpen(true);
      return;
    }
    setConsentBusy(true);
    setConsentNotice(null);
    try {
      await api.agentStrategy.revokeConsent(revokeReason.trim() || null);
      setRevokeOpen(false);
      setRevokeReason('');
      await loadStatus();
      setConsentNotice({ tone: 'ok', text: 'Consent revoked. Existing records are kept.' });
    } catch (err) {
      setConsentNotice({ tone: errorTone(err), text: errorMessage(err, 'Consent was not revoked.') });
    } finally {
      setConsentBusy(false);
    }
  };

  const onCollect = () =>
    runAction('collect', async () => {
      const result = (await api.agentStrategy.collectCandidates({
        strategy_version_id: versionId,
      })) as CollectPayload;
      setCollectResult(result);
      await refreshAnalysis();
      return `Collected ${result.created} new signal(s); ${result.existing} already recorded.`;
    });

  const onBuildOutcomes = () =>
    runAction('build', async () => {
      const result = (await api.agentStrategy.buildOutcomes({
        strategy_version_id: versionId,
      })) as BuildOutcomesPayload;
      setOutcomeResult(result);
      await refreshAnalysis();
      return `Wrote ${result.created} outcome version(s); ${result.superseded} superseded, ${result.preserved} unchanged.`;
    });

  const onPreviewSnapshot = () =>
    runAction('preview', async () => {
      const result = (await api.agentStrategy.previewSnapshot({
        strategy_version_id: versionId,
      })) as PreviewPayload;
      setPreview(result);
      return result.screening_ready
        ? 'Preview passes the screening threshold. A preview is not a frozen dataset.'
        : 'Preview loaded, but the data does not yet pass the screening threshold.';
    });

  const onCreateSnapshot = () =>
    runAction('snapshot', async () => {
      const result = (await api.agentStrategy.createSnapshot({
        strategy_version_id: versionId,
      })) as SnapshotPayload;
      setSnapshot(result);
      setJob(null);
      setInference(null);
      await loadJobs();
      await loadStatus();
      if (result.status !== 'ready') {
        return 'Request completed without a ready dataset.';
      }
      return result.created
        ? 'Frozen dataset created.'
        : 'An identical frozen dataset already existed; returned the same record.';
    });

  const onTrain = () =>
    runAction('train', async () => {
      if (!snapshot) return null;
      const result = (await api.agentStrategy.train(snapshot.id)) as TrainingJob;
      setJob(result);
      setInference(null);
      await loadJobs();
      return result.status === 'completed'
        ? 'Training job completed.'
        : `Training job finished with status ${result.status}.`;
    });

  const onInfer = () =>
    runAction('infer', async () => {
      if (!job) return null;
      const features: Record<string, number> = {};
      for (const [name, raw] of Object.entries(featureInputs)) {
        const value = Number(raw);
        if (raw.trim() === '' || !Number.isFinite(value)) {
          throw new Error(`Feature "${name}" is not a finite number.`);
        }
        features[name] = value;
      }
      const result = (await api.agentStrategy.infer(job.job_id, features)) as InferencePayload;
      setInference(result);
      return 'Inference completed for the supplied values.';
    });

  const sortedCandidates = useMemo(() => {
    const rows = [...(candidates?.candidates ?? [])];
    rows.sort((a, b) => (a.signal_date < b.signal_date ? 1 : a.signal_date > b.signal_date ? -1 : 0));
    return rows;
  }, [candidates]);

  return (
    <AppShell active="strategies">
      <div className="container" style={{ paddingTop: '40px', paddingBottom: '60px' }} data-testid="agent-strategy-page">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '16px', flexWrap: 'wrap', marginBottom: '12px' }}>
          <div>
            <h1 style={{ marginBottom: '8px' }}>Agent candidate screening</h1>
            <p style={{ color: 'var(--text-secondary)', fontSize: '14px', margin: 0, maxWidth: '760px' }}>
              For entry signals already produced by a locked rule strategy, this estimates each
              candidate&apos;s cost-adjusted result under a declared execution, exit and cost policy
              — to help judge take or skip. It is not a self-learning strategy, not imitation of
              your own decisions, and not a market-direction forecast.
            </p>
          </div>
          <div style={{ display: 'flex', gap: '12px' }}>
            <Link href="/dashboard" className="btn btn-secondary">Research</Link>
            <Link href="/strategy-templates" className="btn btn-secondary">Strategy templates</Link>
          </div>
        </div>

        {statusLoading && (
          <p data-testid="agent-strategy-status-loading" style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
            Loading screening status…
          </p>
        )}

        {statusError && (
          <NoticeBox notice={{ tone: 'err', text: statusError }} testId="agent-strategy-status-error" />
        )}

        {caps && policy && (
          <>
            <section className="card" style={{ marginBottom: '24px' }} data-testid="agent-strategy-capabilities">
              <h2 style={{ marginTop: 0 }}>Status and limits</h2>
              <FactRow label="Capability" value={`${caps.capability.label_zh} · ${caps.capability.status_label_zh}`} />
              <FactRow label="Surface" value={caps.capability.surface} />
              <FactRow label={`Enabled in this process`} value={<YesNo value={screeningEnabled} yes="Enabled" no="Off" />} />
              <FactRow label="Consent recorded" value={<span data-testid="consent-status">{policy.status.status}</span>} />
              <FactRow
                label="Dataset readiness"
                value={`environment allowed: ${caps.snapshot_creation.environment_allowed ? 'yes' : 'no'} · purpose authorized: ${caps.snapshot_creation.purpose_authorized ? 'yes' : 'no'} · mode: ${caps.snapshot_creation.authorization_mode}`}
              />
              <FactRow
                label="What a snapshot is usable for"
                value={`fixture training: ${caps.snapshot_creation.usable_for_fixture_training ? 'yes' : 'no'} · this owner's local trial: ${caps.snapshot_creation.usable_for_owner_trial_training ? 'yes' : 'no'} · real training: no`}
              />
              <FactRow
                label="Training and release"
                value={`training authorized in this process: ${caps.training.authorized_in_this_process ? 'yes' : 'no'} · customer release: ${caps.training.customer_release_training} · inference release: ${caps.training.customer_release_inference} · model activation: ${caps.training.model_activation} (unavailable)`}
              />
              <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '12px 0 0' }}>
                {caps.capability.description_zh}
              </p>
            </section>

            <section className="card" style={{ marginBottom: '24px' }}>
              <h2 style={{ marginTop: 0 }}>Research consent</h2>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: 0 }}>
                This step is signed by you, the account owner, in this interface. Candidly: the
                platform records that your authenticated account accepted the text below at the
                server&apos;s timestamp — it cannot prove you read it, and no one else can accept on
                your behalf.
              </p>

              <div
                data-testid="consent-policy-text"
                style={{
                  maxHeight: '260px',
                  overflow: 'auto',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '4px',
                  padding: '12px',
                  background: 'var(--surface-1)',
                  fontSize: '13px',
                  lineHeight: 1.7,
                  whiteSpace: 'pre-wrap',
                  marginBottom: '12px',
                }}
              >
                {policy.policy.acceptance_text_zh}
              </div>

              <div style={{ display: 'grid', gap: '8px', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                <div><strong>Data scope:</strong> {policy.policy.data_scope_zh}</div>
                <div><strong>Retention:</strong> {policy.policy.retention_zh}</div>
                <div><strong>Revocation:</strong> {policy.policy.revoke_zh}</div>
                <div><strong>Output limits:</strong> {policy.policy.output_limits_zh}</div>
                <div><strong>Acceptance text version:</strong> {policy.policy.acceptance_text_version}</div>
              </div>

              <NoticeBox notice={consentNotice} testId="consent-notice" />

              {consentActive ? (
                <div data-testid="consent-active">
                  <FactRow label="Status" value={<span className={`${statusPillClass('active')}`}>active</span>} />
                  <FactRow label="Accepted at" value={formatDateTime(policy.status.accepted_at)} />
                  <FactRow label="Accepted by" value={policy.status.accepted_by ?? '—'} />
                  <FactRow label="Consent id" value={policy.status.consent_id ?? '—'} />
                  {!revokeOpen ? (
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{ marginTop: '12px' }}
                      onClick={onRevokeConsent}
                      disabled={consentBusy}
                      data-testid="consent-revoke"
                    >
                      Revoke consent
                    </button>
                  ) : (
                    <div style={{ marginTop: '12px', display: 'grid', gap: '8px', maxWidth: '520px' }}>
                      <p style={{ fontSize: '13px', margin: 0 }}>
                        Revoking stops future training. Already-created records are kept. Confirm:
                      </p>
                      <input
                        type="text"
                        maxLength={64}
                        value={revokeReason}
                        onChange={(e) => setRevokeReason(e.target.value)}
                        placeholder="Reason (optional, max 64 chars)"
                        data-testid="consent-revoke-reason"
                        style={{ padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px' }}
                      />
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={onRevokeConsent}
                          disabled={consentBusy}
                          data-testid="consent-revoke-confirm"
                        >
                          {consentBusy ? 'Revoking…' : 'Confirm revoke'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => {
                            setRevokeOpen(false);
                            setRevokeReason('');
                          }}
                          disabled={consentBusy}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div data-testid="consent-form">
                  {policy.status.status === 'revoked' && (
                    <p style={{ fontSize: '13px', color: 'var(--warning)', marginTop: 0 }}>
                      Consent was revoked at {formatDateTime(policy.status.revoked_at)}. You may accept again below.
                    </p>
                  )}
                  {!screeningEnabled && (
                    <p style={{ fontSize: '13px', color: 'var(--warning)', marginTop: 0 }}>
                      Screening is switched off in this process, so recording consent is refused. Ask the
                      operator to enable it in an isolated development environment.
                    </p>
                  )}
                  <label style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', fontSize: '13px', marginBottom: '10px' }}>
                    <input
                      type="checkbox"
                      checked={ackRead}
                      onChange={(e) => setAckRead(e.target.checked)}
                      data-testid="consent-ack-checkbox"
                      style={{ marginTop: '3px' }}
                    />
                    <span>I have read the full consent text above.</span>
                  </label>
                  <label htmlFor="consent-ack-phrase" style={{ display: 'block', fontSize: '13px', marginBottom: '6px' }}>
                    Type <strong>{ACCEPT_PHRASE}</strong> to confirm this is your own decision.
                  </label>
                  <input
                    id="consent-ack-phrase"
                    type="text"
                    value={ackPhrase}
                    onChange={(e) => setAckPhrase(e.target.value)}
                    data-testid="consent-ack-phrase"
                    autoComplete="off"
                    style={{ width: '100%', maxWidth: '320px', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', marginBottom: '12px' }}
                  />
                  <div>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={onAcceptConsent}
                      disabled={!acceptReady}
                      data-testid="consent-accept"
                    >
                      {consentBusy ? 'Recording…' : 'Record my consent'}
                    </button>
                  </div>
                </div>
              )}
            </section>

            <section className="card" style={{ marginBottom: '24px' }}>
              <h2 style={{ marginTop: 0 }}>Locked strategy</h2>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: 0 }}>
                Screening reads the entry signals of a locked SMA-crossover version and the stored
                daily bars in its declared period. Other templates are reported as unsupported
                rather than approximated.
              </p>
              {versionsLoading ? (
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Loading locked versions…</p>
              ) : versionsError ? (
                <NoticeBox notice={{ tone: 'err', text: versionsError }} testId="agent-strategy-versions-error" />
              ) : versions.length === 0 ? (
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                  No locked strategy version yet. Lock a version on the{' '}
                  <Link href="/dashboard" style={{ color: 'var(--primary)' }}>Research</Link> page first.
                </p>
              ) : (
                <>
                  <label htmlFor="agent-strategy-version" style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px' }}>
                    Locked strategy version
                  </label>
                  <select
                    id="agent-strategy-version"
                    data-testid="strategy-version-select"
                    value={versionId}
                    onChange={(e) => onVersionChange(e.target.value)}
                    style={{ width: '100%', maxWidth: '640px', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', marginBottom: '8px' }}
                  >
                    <option value="">Select a locked version</option>
                    {versions.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.experiment_title} · v{option.version_number} · {option.template_type}
                        {option.template_type === SUPPORTED_RULE ? '' : ' (unsupported)'}
                      </option>
                    ))}
                  </select>
                  {selectedVersion && (
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
                      Locked {formatDateTime(selectedVersion.locked_at)} · assets:{' '}
                      {selectedVersion.asset_ids.length ? selectedVersion.asset_ids.join(', ') : '—'}
                      {selectedVersion.template_type === SUPPORTED_RULE
                        ? ''
                        : ' · This screening slice only executes the SMA crossover rule.'}
                    </p>
                  )}
                </>
              )}
            </section>

            {selectedVersion && (
              <>
                <section className="card" style={{ marginBottom: '24px' }}>
                  <h2 style={{ marginTop: 0 }}>Candidates and outcomes</h2>
                  <NoticeBox notice={actionNotice} testId="agent-strategy-action-notice" />
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={onCollect}
                      disabled={busy !== null}
                      data-testid="collect-candidates"
                    >
                      {busy === 'collect' ? 'Collecting…' : 'Collect entry signals'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={onBuildOutcomes}
                      disabled={busy !== null}
                      data-testid="build-outcomes"
                    >
                      {busy === 'build' ? 'Building…' : 'Build outcomes'}
                    </button>
                  </div>

                  {collectResult && (
                    <div data-testid="collect-result" style={{ fontSize: '13px', marginBottom: '16px' }}>
                      <div>{collectResult.stored_bars} stored bars read · {collectResult.created} new · {collectResult.existing} existing</div>
                      <div style={{ color: 'var(--text-secondary)' }}>
                        Execution feasibility:{' '}
                        {Object.entries(collectResult.execution_feasibility).length
                          ? Object.entries(collectResult.execution_feasibility).map(([key, value]) => `${humanizeKey(key)} ${value}`).join(' · ')
                          : 'no new signal'}
                      </div>
                    </div>
                  )}

                  {outcomeResult && (
                    <div data-testid="outcomes-result" style={{ fontSize: '13px', marginBottom: '16px' }}>
                      <div>
                        {outcomeResult.created} created · {outcomeResult.superseded} superseded ·{' '}
                        {outcomeResult.preserved} unchanged · {outcomeResult.preserved_observed} observed results kept
                      </div>
                      <div style={{ color: 'var(--text-secondary)' }}>
                        Distribution: {Object.entries(outcomeResult.outcome_distribution).map(([key, value]) => `${key} ${value}`).join(' · ') || '—'}
                      </div>
                    </div>
                  )}

                  {eligibility && (
                    <div style={{ marginBottom: '16px' }} data-testid="eligibility-summary">
                      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px', marginBottom: '8px' }}>
                        <span>candidates: <strong>{eligibility.candidate_count}</strong></span>
                        <span>eligible outcomes: <strong>{eligibility.eligible_count}</strong></span>
                        <span>eligible signal dates: <strong>{eligibility.distinct_eligible_dates}</strong></span>
                        <span className={statusPillClass(eligibility.screening_ready ? 'active' : 'pending')}>
                          {eligibility.screening_ready ? 'screening threshold met' : 'below screening threshold'}
                        </span>
                      </div>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                        Distribution:{' '}
                        {Object.entries(eligibility.outcome_distribution).map(([key, value]) => `${key} ${value}`).join(' · ') || '—'}
                        {Object.keys(eligibility.exclusion_breakdown).length > 0 && (
                          <>
                            {' '}· excluded:{' '}
                            {Object.entries(eligibility.exclusion_breakdown)
                              .map(([key, value]) => `${eligibility.exclusion_labels_zh[key] ?? key} ${value}`)
                              .join(' · ')}
                          </>
                        )}
                      </div>
                      {eligibility.reasons.length > 0 && (
                        <ul style={{ margin: '8px 0 0', paddingLeft: '20px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                          {eligibility.reasons.map((reason) => (
                            <li key={reason.code}>
                              <strong>{reason.code}</strong>: {reason.message_zh} (measured: {formatMetricValue(reason.measured)}; required: {reason.requirement})
                            </li>
                          ))}
                        </ul>
                      )}
                      <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '8px 0 0' }}>
                        This is a screening quality check of candidate outcomes, not an evaluation of
                        your personal decision quality.
                      </p>
                    </div>
                  )}

                  {sortedCandidates.length > 0 && (
                    <div style={{ overflowX: 'auto' }} data-testid="candidate-table">
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                        <thead>
                          <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border-strong)' }}>
                            <th style={{ padding: '8px' }}>Signal date</th>
                            <th style={{ padding: '8px' }}>Instrument</th>
                            <th style={{ padding: '8px' }}>Feasibility</th>
                            <th style={{ padding: '8px' }}>Outcome</th>
                            <th style={{ padding: '8px' }}>Source</th>
                            <th style={{ padding: '8px', textAlign: 'right' }}>Net return</th>
                            <th style={{ padding: '8px' }}>Eligible</th>
                            <th style={{ padding: '8px' }}>Exclusion</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sortedCandidates.slice(0, 50).map((row) => (
                            <tr key={row.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                              <td style={{ padding: '8px' }}>{formatDate(row.signal_date)}</td>
                              <td style={{ padding: '8px' }}>{row.instrument_id}</td>
                              <td style={{ padding: '8px' }}>{row.execution_feasibility}</td>
                              <td style={{ padding: '8px' }}>
                                {row.outcome ? (
                                  <span className={statusPillClass(row.outcome.outcome_status)}>{row.outcome.outcome_status}</span>
                                ) : (
                                  <span style={{ color: 'var(--text-muted)' }}>no outcome</span>
                                )}
                              </td>
                              <td style={{ padding: '8px' }}>{row.outcome?.outcome_source ?? '—'}</td>
                              <td style={{ padding: '8px', textAlign: 'right' }}>{formatPercent(row.outcome?.net_return_pct)}</td>
                              <td style={{ padding: '8px' }}>{row.outcome ? (row.outcome.label_eligible ? 'yes' : 'no') : '—'}</td>
                              <td style={{ padding: '8px' }}>{row.outcome?.exclusion_reason ?? row.rejection_reason ?? '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {sortedCandidates.length > 50 && (
                        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
                          Showing the latest 50 of {sortedCandidates.length} candidates.
                        </p>
                      )}
                    </div>
                  )}
                </section>

                <section className="card" style={{ marginBottom: '24px' }}>
                  <h2 style={{ marginTop: 0 }}>Dataset snapshot</h2>
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: 0 }}>
                    A snapshot freezes the eligible rows, the exact outcome version each row used,
                    and a chronological train/validation/test split with a gap and label-window
                    purge. A preview never authorizes training by itself.
                  </p>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={onPreviewSnapshot}
                      disabled={busy !== null}
                      data-testid="preview-snapshot"
                    >
                      {busy === 'preview' ? 'Previewing…' : 'Preview dataset'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={onCreateSnapshot}
                      disabled={busy !== null}
                      data-testid="create-snapshot"
                    >
                      {busy === 'snapshot' ? 'Creating…' : 'Freeze dataset'}
                    </button>
                  </div>

                  {preview && (
                    <div data-testid="preview-result" style={{ marginBottom: '16px', fontSize: '13px' }}>
                      <FactRow label="Included / excluded rows" value={`${preview.included_count} / ${preview.excluded_count}`} />
                      <FactRow label="Signal date range" value={`${formatDate(preview.date_range_start)} – ${formatDate(preview.date_range_end)}`} />
                      <FactRow label="Data cutoff" value={formatDateTime(preview.data_cutoff_at)} />
                      <FactRow label="Preview checksum" value={<code style={{ fontSize: '11px' }}>{preview.checksum}</code>} />
                      <FactRow
                        label="Split sizes"
                        value={`train ${preview.split_manifest.splits.train.count} · validation ${preview.split_manifest.splits.validation.count} · test ${preview.split_manifest.splits.test.count} (purged ${preview.split_manifest.purged.length})`}
                      />
                      <FactRow label="Split refusal" value={preview.split_manifest.refusal_reason ?? 'none'} />
                      <FactRow label="Training ready" value={<YesNo value={preview.training_ready} yes="Yes" no={`No (${preview.training_blocked_by ?? 'authorization missing'})`} />} />
                    </div>
                  )}

                  {snapshot && (
                    <div data-testid="snapshot-result" style={{ fontSize: '13px' }}>
                      <FactRow label="Snapshot id" value={<code style={{ fontSize: '11px' }}>{snapshot.id}</code>} />
                      <FactRow label="Status" value={<span className={statusPillClass(snapshot.status)}>{snapshot.status}</span>} />
                      <FactRow label="Rows" value={`${snapshot.included_count} included · ${snapshot.excluded_count} excluded`} />
                      <FactRow label="Checksum" value={<code style={{ fontSize: '11px' }}>{snapshot.checksum}</code>} />
                      <FactRow label="Checksum verified" value={<YesNo value={snapshot.integrity.checksum_verified === true} yes="Verified" no={snapshot.integrity.checksum_verified === false ? 'Mismatch' : 'Not applicable'} />} />
                      <FactRow label="Authorization basis" value={`${snapshot.authorization.basis} · policy ${snapshot.authorization.policy_version} · owner consent: ${snapshot.authorization.real_owner_consent ? 'yes' : 'no'}`} />
                      <FactRow
                        label="Usable for"
                        value={`fixture training: ${snapshot.usable_for_fixture_training ? 'yes' : 'no'} · owner local trial: ${snapshot.usable_for_owner_trial_training ? 'yes' : 'no'} · real training: no`}
                      />
                      <FactRow label="Created" value={formatDateTime(snapshot.created_at)} />
                      {snapshot.status !== 'ready' && (
                        <p style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '8px' }}>
                          This record is a refusal, not a training set: {snapshot.failure_reason ?? 'no reason recorded'}.
                        </p>
                      )}
                    </div>
                  )}

                  {!preview && !snapshot && (
                    <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
                      No preview or frozen dataset for this version yet.
                    </p>
                  )}
                </section>

                <section className="card" style={{ marginBottom: '24px' }}>
                  <h2 style={{ marginTop: 0 }}>Training and inference</h2>
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: 0 }}>
                    One bounded run fits a preprocessing pipeline on the train split only and keeps
                    the all-take and all-skip baselines on the test split. A completed job produces
                    an artifact and a version readback; inference scores one supplied feature row.
                    The model is never activated and never serves customers here.
                  </p>

                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={onTrain}
                      disabled={busy !== null || !snapshot || snapshot.status !== 'ready'}
                      data-testid="train-model"
                    >
                      {busy === 'train' ? 'Training…' : 'Run one training job'}
                    </button>
                  </div>
                  {!snapshot && (
                    <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: 0 }}>
                      Freeze a ready dataset first; a preview cannot be trained on.
                    </p>
                  )}

                  {jobsError && <NoticeBox notice={{ tone: 'err', text: jobsError }} testId="training-jobs-error" />}

                  {job && (
                    <div data-testid="training-job" style={{ marginBottom: '16px' }}>
                      <FactRow label="Job id" value={<code style={{ fontSize: '11px' }}>{job.job_id}</code>} />
                      <FactRow label="Status" value={<span className={statusPillClass(job.status)}>{job.status}</span>} />
                      <FactRow label="Dataset checksum" value={<code style={{ fontSize: '11px' }}>{job.dataset_checksum}</code>} />
                      <FactRow label="Artifact checksum" value={<code style={{ fontSize: '11px' }}>{job.artifact_checksum ?? '—'}</code>} />
                      <FactRow label="Artifact path" value={
                        job.artifact_path ? (
                          // The path is local to the backend process; showing it is a
                          // readback of where the artifact is, not a download link.
                          <code style={{ fontSize: '11px', wordBreak: 'break-all' }}>{job.artifact_path}</code>
                        ) : '—'}
                      />
                      <FactRow label="Activation" value={`${job.activation_status} — not activated`} />
                      <FactRow label="Completed" value={formatDateTime(job.completed_at)} />
                      {job.failure_reason && (
                        <p style={{ fontSize: '12px', color: 'var(--negative)', marginTop: '8px' }}>
                          Failure reason: {job.failure_reason}
                        </p>
                      )}
                      <div style={{ marginTop: '12px' }}>
                        <h3 style={{ fontSize: '14px', marginBottom: '8px' }}>Row counts</h3>
                        <MetricGrid data={job.row_counts} testId="training-row-counts" />
                      </div>
                      <div style={{ marginTop: '12px' }}>
                        <h3 style={{ fontSize: '14px', marginBottom: '8px' }}>Metrics</h3>
                        <MetricGrid data={job.metrics} testId="training-metrics" />
                      </div>
                    </div>
                  )}

                  {job && job.status === 'completed' && (
                    <div data-testid="inference-form" style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '16px' }}>
                      <h3 style={{ fontSize: '14px', marginBottom: '8px' }}>One controlled inference</h3>
                      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: 0 }}>
                        Values are prefilled from the first frozen row only as a convenience; edit
                        them before running. The result is a screening signal under the declared
                        policy, not a recommendation or a profit claim.
                      </p>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '10px', marginBottom: '12px' }}>
                        {Object.keys(featureInputs).length === 0 ? (
                          <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
                            No feature names were recorded for this artifact.
                          </p>
                        ) : (
                          Object.entries(featureInputs).map(([name, value]) => (
                            <label key={name} style={{ display: 'grid', gap: '4px', fontSize: '12px' }}>
                              <span style={{ color: 'var(--text-secondary)' }}>{name}</span>
                              <input
                                type="number"
                                step="any"
                                value={value}
                                onChange={(e) => setFeatureInputs((prev) => ({ ...prev, [name]: e.target.value }))}
                                data-testid={`feature-input-${name}`}
                                style={{ padding: '6px 10px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px' }}
                              />
                            </label>
                          ))
                        )}
                      </div>
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={onInfer}
                        disabled={busy !== null || Object.keys(featureInputs).length === 0}
                        data-testid="run-inference"
                      >
                        {busy === 'infer' ? 'Scoring…' : 'Run inference'}
                      </button>

                      {inference && (
                        <div data-testid="inference-result" style={{ marginTop: '16px' }}>
                          <FactRow label="Decision" value={<span className={statusPillClass(inference.decision)}>{inference.decision}</span>} />
                          <FactRow label="Positive probability" value={inference.positive_probability} />
                          <FactRow label="Threshold" value={inference.threshold} />
                          <FactRow label="Lineage verified" value={<YesNo value={inference.lineage_verified} />} />
                          <FactRow label="Artifact verified" value={<YesNo value={inference.artifact_verified} />} />
                          <FactRow label="Activation" value={`${inference.activation_status} — not activated`} />
                          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
                            This is not a recommendation and not a profit claim.
                          </p>
                        </div>
                      )}
                    </div>
                  )}

                  {jobs.length > 0 && (
                    <div style={{ marginTop: '20px' }}>
                      <h3 style={{ fontSize: '14px', marginBottom: '8px' }}>Recorded training jobs</h3>
                      <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                          <thead>
                            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border-strong)' }}>
                              <th style={{ padding: '8px' }}>Created</th>
                              <th style={{ padding: '8px' }}>Status</th>
                              <th style={{ padding: '8px' }}>Snapshot</th>
                              <th style={{ padding: '8px' }}>Dataset checksum</th>
                              <th style={{ padding: '8px' }}>Activation</th>
                            </tr>
                          </thead>
                          <tbody>
                            {jobs.map((row) => (
                              <tr key={row.job_id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                <td style={{ padding: '8px' }}>{formatDateTime(row.created_at)}</td>
                                <td style={{ padding: '8px' }}>
                                  <span className={statusPillClass(row.status)}>{row.status}</span>
                                </td>
                                <td style={{ padding: '8px' }}>{row.snapshot_id.slice(0, 8)}…</td>
                                <td style={{ padding: '8px' }}>{row.dataset_checksum?.slice(0, 12) ?? '—'}…</td>
                                <td style={{ padding: '8px' }}>{row.activation_status}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </section>
              </>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
