'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import AppShell from '@/components/shell/AppShell';
import { api, ApiError } from '@/lib/api';
import {
  acceptActivationRead,
  activationWriteNotice,
  beginActivationRead,
  isDisplayedActivationJob,
  NO_ACTIVATION_READ,
  verificationOf,
  type ActivationReadId,
  type ActivationReadResult,
} from '@/lib/activationPanel';
import {
  acceptPaperRead,
  approvalScopeError,
  approvalScopeNotice,
  approvalScopeOf,
  approvalScopeSentence,
  beginPaperRead,
  isDisplayedPaperContext,
  ledgerOfContext,
  NO_PAPER_READ,
  paperContextKey,
  paperReadbackNotice,
  paperRefusalNotice,
  readbackRowOf,
  submitBlockedReason,
  tradeLinksZH,
  executionOutcomeZH,
  type ApprovalChoice,
  type PaperContext,
  type PaperLedgerRow,
  type PaperReadback,
  type PaperReadId,
} from '@/lib/paperExecutionPanel';
import { formatDate, formatDateTime } from '@/lib/format';
import { statusPillClass } from '@/lib/status-styles';

const SUPPORTED_RULE = 'sma_crossover';
const ACCEPT_PHRASE = 'I ACCEPT';

// Mirrors the comparison the server itself makes in
// ``model_activation._normalise_ack``: the owner has to submit the whole text,
// spacing aside.
function normaliseAck(text: string): string {
  return text.replace(/\s+/g, '');
}

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
  activation_approval_id: string | null;
  runs_inference_for_customer: boolean;
  not_a_recommendation: boolean;
  not_a_profit_claim: boolean;
}

interface ActivationRequirements {
  job_id: string;
  strategy_version_id: string;
  artifact_checksum: string | null;
  dataset_checksum: string;
  training_contract_version: string;
  approval_policy_version: string;
  acknowledgement_text_version: string;
  acknowledgement_text_zh: string;
  consent_status: string;
}

interface ActivationStatePayload {
  activation_status: string;
  reason_code: string | null;
  approval_id: string | null;
  bindings: Record<string, boolean>;
  movable_to_customer_environment: boolean;
  activation_policy_version: string;
  job_id?: string;
  job_status?: string;
  approved_at?: string;
  artifact_checksum?: string;
  skill_unit_id?: string | null;
  skill_unit_scope?: 'specific' | 'none';
}

interface ActivationPayload {
  requirements: ActivationRequirements;
  state: ActivationStatePayload;
}

/** The part of one row of ``GET /paper-portfolio`` this panel reads. */
interface SkillUnitRow {
  id: string;
  status: string;
  currency: string;
  created_at: string;
  current_virtual_cash: number;
}

/** The part of ``GET .../paper-executions`` the ledger read is taken from. */
interface PaperLedgerPayload {
  executions?: PaperLedgerRow[];
  count?: number;
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

function errorTone(err: unknown): 'warn' | 'err' {
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

/**
 * The owner's own skill accounts, read from the formal portfolio list.
 *
 * Nothing is preselected: an empty value is the only state in which neither the
 * approval form nor the paper panel may act, and a list that failed to load says
 * so instead of presenting "no accounts" as a fact.
 */
function SkillUnitSelect({
  units,
  value,
  onChange,
  testId,
  error,
}: {
  units: SkillUnitRow[];
  value: string;
  onChange: (value: string) => void;
  testId: string;
  error: string;
}) {
  return (
    <div style={{ display: 'grid', gap: '6px', maxWidth: '640px' }}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
        style={{ padding: '8px 10px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px' }}
      >
        <option value="">— 還沒有明選技能帳號 —</option>
        {units.map((unit) => (
          <option key={unit.id} value={unit.id} data-testid={`${testId}-option-${unit.id}`}>
            {unit.id.slice(0, 8)}… · {unit.currency} · {unit.status} · 現金 {unit.current_virtual_cash.toFixed(2)}
          </option>
        ))}
      </select>
      {error ? (
        <p data-testid={`${testId}-error`} style={{ fontSize: '12px', color: 'var(--negative)', margin: 0 }}>
          {error}
        </p>
      ) : units.length === 0 ? (
        <p data-testid={`${testId}-empty`} style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>
          這個帳號底下目前沒有任何技能帳號可選。這裡不會代你建立一個。
        </p>
      ) : null}
    </div>
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

  const [activation, setActivation] = useState<ActivationPayload | null>(null);
  const [activationLoading, setActivationLoading] = useState(false);
  const [activationError, setActivationError] = useState('');
  const [activationBusy, setActivationBusy] = useState(false);
  const [activationNotice, setActivationNotice] = useState<Notice | null>(null);
  const [activationRead, setActivationRead] = useState(false);
  const [activationAck, setActivationAck] = useState('');
  const [revokeActivationOpen, setRevokeActivationOpen] = useState(false);
  const [revokeActivationReason, setRevokeActivationReason] = useState('');

  // The paper lane's own context: which of the owner's skill accounts the next
  // press belongs to, and what the ledger says about it. Nothing here is
  // prefilled from the approval — an empty selection means "not chosen yet".
  const [skillUnits, setSkillUnits] = useState<SkillUnitRow[]>([]);
  const [skillUnitsError, setSkillUnitsError] = useState('');
  const [skillUnitId, setSkillUnitId] = useState('');
  const [approvalChoice, setApprovalChoice] = useState<ApprovalChoice>('job_only');
  const [paperRows, setPaperRows] = useState<PaperLedgerRow[]>([]);
  const [paperBusy, setPaperBusy] = useState(false);
  const [paperNotice, setPaperNotice] = useState<Notice | null>(null);
  const [paperReadError, setPaperReadError] = useState('');

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

  // Which read the panel owes its state to: the newest one, for the artifact on
  // screen. Anything else is dropped instead of written, so an answer about job
  // A cannot land on job B.
  const activationReadRef = useRef<ActivationReadId>(NO_ACTIVATION_READ);

  const loadActivation = useCallback(
    async (jobId: string): Promise<ActivationReadResult> => {
      if (!isDisplayedActivationJob(activationReadRef.current, jobId)) {
        // The owner has moved to another artifact. Asking for this one would
        // retire the read that artifact is still waiting for, and its answer has
        // no panel to write to anyway.
        return { outcome: 'stale' };
      }
      const read = beginActivationRead(jobId, activationReadRef.current);
      activationReadRef.current = read;
      setActivationLoading(true);
      setActivationError('');
      try {
        const payload = (await api.agentStrategy.jobActivation(jobId)) as ActivationPayload;
        if (
          !acceptActivationRead(
            activationReadRef.current,
            read,
            payload?.requirements?.job_id ?? jobId,
          )
        ) {
          return { outcome: 'stale' };
        }
        setActivation(payload);
        return { outcome: 'loaded', state: payload.state };
      } catch (err) {
        // The refusal names the artifact it refused. If the owner has already
        // moved on, its message is not this panel's business.
        const answered = acceptActivationRead(activationReadRef.current, read);
        if (answered) {
          setActivation(null);
          setActivationError(
            errorMessage(err, 'Could not read the activation of this artifact.'),
          );
        }
        return answered ? { outcome: 'failed' } : { outcome: 'stale' };
      } finally {
        if (acceptActivationRead(activationReadRef.current, read)) {
          setActivationLoading(false);
        }
      }
    },
    [],
  );

  // Which read of which triple (job, skill account, artifact) the paper panel
  // owes its state to. Same rule as the activation panel: an answer about the
  // triple the owner has left cannot write into the one now on screen.
  const paperReadRef = useRef<PaperReadId>(NO_PAPER_READ);

  const loadSkillUnits = useCallback(async () => {
    setSkillUnitsError('');
    try {
      const rows = (await api.paperPortfolio.list()) as SkillUnitRow[];
      setSkillUnits(Array.isArray(rows) ? rows : []);
    } catch (err) {
      // An unreadable account list is not an empty list: the panel keeps the
      // selection empty and says why, rather than offering nothing silently.
      setSkillUnits([]);
      setSkillUnitsError(errorMessage(err, '讀不到你的技能帳號清單。'));
    }
  }, []);

  /**
   * The formal read of one job's paper ledger.
   *
   * The door is per job, so the answer may legitimately name other skill
   * accounts of the same artifact; rows of another triple are excluded locally by
   * the identity key, which is also what makes the counted number an isolation
   * check rather than a courtesy.
   */
  const loadPaperLedger = useCallback(
    async (
      context: PaperContext,
      executionId: string | null = null,
    ): Promise<PaperReadback> => {
      if (!context.jobId) return { state: 'stale' };
      const key = paperContextKey(context);
      if (!isDisplayedPaperContext(paperReadRef.current, key)) {
        return { state: 'stale' };
      }
      const read = beginPaperRead(key, paperReadRef.current);
      paperReadRef.current = read;
      setPaperReadError('');
      try {
        const payload = (await api.agentStrategy.paperExecutions(
          context.jobId,
        )) as PaperLedgerPayload;
        const rows = Array.isArray(payload?.executions) ? payload.executions : [];
        const jobLegs = new Set(rows.map((row) => row.job_id).filter(Boolean));
        const answered =
          jobLegs.size === 0
            ? null
            : jobLegs.size === 1 && jobLegs.has(context.jobId)
              ? key
              : `${context.jobId}#foreign`;
        if (!acceptPaperRead(paperReadRef.current, read, answered)) {
          return { state: 'stale' };
        }
        setPaperRows(rows);
        return {
          state: 'read',
          row: readbackRowOf(rows, context, executionId),
          counted: ledgerOfContext(rows, context).length,
        };
      } catch (err) {
        const stillMine = acceptPaperRead(paperReadRef.current, read);
        if (stillMine) {
          setPaperRows([]);
          setPaperReadError(errorMessage(err, '讀不回這個 job 的紙上執行 ledger。'));
        }
        return stillMine ? { state: 'read_failed' } : { state: 'stale' };
      }
    },
    [],
  );

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
    void loadSkillUnits();
  }, [loadStatus, loadJobs, loadVersions, loadSkillUnits]);

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

  const activationJobId = job && job.status === 'completed' ? job.job_id : '';

  // One artifact on screen, one answer from the server. Switching artifacts
  // retires the previous read first — including when nothing is on screen — so a
  // late answer about job A cannot write job B's status, acknowledgement text or
  // notice, and a decision typed for A can never be sent for B.
  useEffect(() => {
    activationReadRef.current = beginActivationRead(
      activationJobId,
      activationReadRef.current,
    );
    setActivation(null);
    setActivationError('');
    setActivationNotice(null);
    setActivationRead(false);
    setActivationAck('');
    setActivationBusy(false);
    setRevokeActivationOpen(false);
    setRevokeActivationReason('');
    if (!activationJobId) return;
    void loadActivation(activationJobId);
  }, [activationJobId, loadActivation]);

  // The three legs one paper press is about. The artifact leg comes from the
  // server's own read of this job, never from a value typed into this form.
  const paperContext = useMemo<PaperContext>(
    () => ({
      jobId: activation?.requirements.job_id ?? '',
      skillUnitId,
      artifactChecksum: activation?.requirements.artifact_checksum ?? '',
    }),
    [activation, skillUnitId],
  );

  // Switching job, skill account or artifact retires the previous read before
  // anything else, so an in-flight ledger answer about the old triple is dropped
  // rather than shown as the new one's result.
  useEffect(() => {
    const key = paperContext.jobId ? paperContextKey(paperContext) : '';
    paperReadRef.current = beginPaperRead(key, paperReadRef.current);
    setPaperRows([]);
    setPaperNotice(null);
    setPaperReadError('');
    setPaperBusy(false);
    if (!key) return;
    void loadPaperLedger(paperContext);
  }, [paperContext, loadPaperLedger]);

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

  /**
   * Put one recorded job on screen.
   *
   * The job's own snapshot is re-read so the inference prefill belongs to the
   * artifact now selected: leaving the previous dataset in state would let the
   * form offer numbers that never came from this run.
   */
  const onSelectJob = (jobId: string) =>
    runAction('select-job', async () => {
      const result = (await api.agentStrategy.getTrainingJob(jobId)) as TrainingJob;
      setJob(result);
      setInference(null);
      setFeatureInputs({});
      setSnapshot(null);
      const loaded = (await api.agentStrategy.getSnapshot(result.snapshot_id)) as SnapshotPayload;
      setSnapshot(loaded);
      // The activation read is left to the switching effect: while this press is in
      // flight the artifact on screen is still the previous one, and reading the
      // new job from here would retire the read that artifact is waiting for.
      return result.status === 'completed'
        ? `已選取 job ${result.job_id.slice(0, 8)}…，並以該資料集第一列預填推論欄位（可自行修改）。`
        : `已選取 job ${result.job_id.slice(0, 8)}…，狀態 ${result.status}：這個產物尚未完成，沒有可批准或可執行的模型。`;
    });

  const activationText = activation?.requirements.acknowledgement_text_zh ?? '';
  const approveReady =
    Boolean(activation?.requirements.artifact_checksum) &&
    screeningEnabled &&
    activationRead &&
    activationText !== '' &&
    normaliseAck(activationAck) === normaliseAck(activationText) &&
    !activationBusy;

  // What the live approval is bound to, and whether this press may leave: both
  // come from the same functions the offline tests exercise, so the sentence
  // beside the control and the gate in front of the request cannot disagree.
  const approvalScope = approvalScopeOf(activation?.state);
  const scopeChoiceError = approvalScopeError(approvalChoice, skillUnitId);
  const paperBlockReason = submitBlockedReason(paperContext, activation?.state);
  const paperRowsOfContext = useMemo(
    () => ledgerOfContext(paperRows, paperContext),
    [paperRows, paperContext],
  );

  /** A click's answer belongs to this panel only while its artifact is on screen. */
  const stillOnActivationJob = (jobId: string) =>
    isDisplayedActivationJob(activationReadRef.current, jobId);

  const onApproveActivation = async () => {
    if (!activation || !approveReady) return;
    const jobId = activation.requirements.job_id;
    // The scope is the owner's own decision, stated in this form before the
    // request leaves: an approval never gains a skill account on the way out.
    const scopeError = approvalScopeError(approvalChoice, skillUnitId);
    if (scopeError) {
      setActivationNotice({ tone: 'warn', text: `${scopeError} 這一筆沒有送出。` });
      return;
    }
    setActivationBusy(true);
    setActivationNotice(null);
    let refusal: { message: string; tone: 'warn' | 'err' } | null = null;
    try {
      await api.agentStrategy.activateJob(jobId, {
        // Both values came from the server read of this artifact, so the
        // approval binds the checksum on screen and carries to no other.
        artifact_checksum: activation.requirements.artifact_checksum as string,
        policy_version: activation.requirements.approval_policy_version,
        acknowledgement_text_version: activation.requirements.acknowledgement_text_version,
        acknowledgement: activationAck,
        // NULL says "no skill-unit scope", which is not "every skill unit": a
        // job-only approval covers the artifact in isolation and authorises no
        // paper execution on any account.
        skill_unit_id: approvalChoice === 'skill_unit' ? skillUnitId : null,
      });
    } catch (err) {
      refusal = {
        message: errorMessage(err, 'The approval was not recorded.'),
        tone: errorTone(err),
      };
    }
    if (!stillOnActivationJob(jobId)) return;
    if (refusal) {
      setActivationNotice(activationWriteNotice('approve', { refused: refusal }));
      setActivationBusy(false);
      return;
    }
    setActivationRead(false);
    setActivationAck('');
    // A resolved write is a claim, not a state: the approval counts once this
    // same artifact reads back as APPROVED with a record behind it.
    const read = await loadActivation(jobId);
    await loadJobs();
    if (!stillOnActivationJob(jobId)) return;
    setActivationNotice(
      activationWriteNotice('approve', { verified: verificationOf('approve', read) }),
    );
    setActivationBusy(false);
  };

  const onRevokeActivation = async () => {
    if (!activation || !activation.state.approval_id) return;
    const jobId = activation.requirements.job_id;
    if (!revokeActivationOpen) {
      setRevokeActivationOpen(true);
      return;
    }
    setActivationBusy(true);
    setActivationNotice(null);
    let refusal: { message: string; tone: 'warn' | 'err' } | null = null;
    try {
      await api.agentStrategy.revokeJobActivation(
        jobId,
        revokeActivationReason.trim() || null,
      );
    } catch (err) {
      refusal = {
        message: errorMessage(err, 'The approval was not withdrawn.'),
        tone: errorTone(err),
      };
    }
    if (!stillOnActivationJob(jobId)) return;
    if (refusal) {
      setActivationNotice(activationWriteNotice('withdraw', { refused: refusal }));
      setActivationBusy(false);
      return;
    }
    setRevokeActivationOpen(false);
    setRevokeActivationReason('');
    // Re-read after withdrawal instead of asserting the new state here: the
    // record is kept by the server and only the gate knows what it now says.
    const read = await loadActivation(jobId);
    await loadJobs();
    if (!stillOnActivationJob(jobId)) return;
    setActivationNotice(
      activationWriteNotice('withdraw', { verified: verificationOf('withdraw', read) }),
    );
    setActivationBusy(false);
  };

  /** Whether the triple one press was made on is still the one on screen. */
  const stillOnPaperContext = (context: string) =>
    isDisplayedPaperContext(paperReadRef.current, context);

  const onRunPaperExecution = async () => {
    if (!activation) return;
    const jobId = activation.requirements.job_id;
    const blocked = submitBlockedReason(paperContext, activation.state);
    if (blocked) {
      setPaperNotice({ tone: 'warn', text: `${blocked} 這一筆沒有送出。` });
      return;
    }
    const context = paperContextKey(paperContext);
    if (!stillOnPaperContext(context)) return;
    setPaperBusy(true);
    setPaperNotice(null);
    let pressed: PaperLedgerRow | null = null;
    let refusal: Notice | null = null;
    let uncertain = '';
    try {
      pressed = (await api.agentStrategy.paperExecution(jobId, {
        skill_unit_id: paperContext.skillUnitId,
      })) as PaperLedgerRow;
    } catch (err) {
      const named = paperRefusalNotice(err instanceof ApiError ? err.detail : null);
      if (named) {
        refusal = named;
      } else {
        // No code means the door's verdict is unknown, which is not a licence to
        // mint another intent and try again: the read below is what is reported.
        uncertain = errorMessage(err, '這一筆紙上執行沒有可辨識的回報，結果不明。');
      }
    }
    if (!stillOnPaperContext(context)) return;
    if (refusal) {
      // A named refusal wrote nothing, so the door's own sentence stands. The
      // ledger is still re-read so the table below says what the book holds now.
      setPaperNotice(refusal);
      setPaperBusy(false);
      await loadPaperLedger(paperContext);
      return;
    }
    // Whether the door answered, threw or stayed silent, the outcome shown here is
    // the row the formal GET returned — never a status inferred from the press.
    const read = await loadPaperLedger(
      paperContext,
      pressed ? pressed.execution_id : null,
    );
    if (!stillOnPaperContext(context)) return;
    const notice = paperReadbackNotice(pressed, read);
    setPaperNotice(
      uncertain && !pressed
        ? { tone: notice.tone, text: `${uncertain} ${notice.text}` }
        : notice,
    );
    setPaperBusy(false);
  };

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
                value={`training authorized in this process: ${caps.training.authorized_in_this_process ? 'yes' : 'no'} · customer release: ${caps.training.customer_release_training} · inference release: ${caps.training.customer_release_inference} · model activation: ${caps.training.model_activation} (no artifact bound here; each job row below carries its own)`}
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
                    Scoring that artifact is a second act of its own: it stays refused until the
                    owner approves this exact checksum below, and an approval switches the model on
                    inside this account only — it is not a customer release and places no order.
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
                      <FactRow
                        label="Activation"
                        value={activation ? activation.state.activation_status : job.activation_status}
                      />
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

                      <div
                        data-testid="activation-panel"
                        style={{ marginTop: '16px', borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}
                      >
                        <h3 style={{ fontSize: '14px', marginBottom: '8px' }}>Model activation</h3>
                        <NoticeBox notice={activationNotice} testId="activation-notice" />
                        {activationLoading && !activation ? (
                          <p data-testid="activation-loading" style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0 }}>
                            Reading the activation state from the server…
                          </p>
                        ) : activationError ? (
                          <NoticeBox notice={{ tone: 'err', text: activationError }} testId="activation-read-error" />
                        ) : activation ? (
                          <>
                            <FactRow
                              label="Activation status"
                              value={
                                <span className={statusPillClass(activation.state.activation_status)}>
                                  {activation.state.activation_status}
                                </span>
                              }
                            />
                            <FactRow label="Reason code" value={activation.state.reason_code ?? '—'} />
                            <FactRow
                              label="Approval record"
                              value={
                                activation.state.approval_id ? (
                                  <code style={{ fontSize: '11px' }}>{activation.state.approval_id}</code>
                                ) : (
                                  'none recorded for this artifact'
                                )
                              }
                            />
                            <FactRow
                              label="Approval scope"
                              value={
                                <span data-testid="activation-scope">
                                  {approvalScope === 'specific'
                                    ? `綁定技能帳號 ${activation.state.skill_unit_id ?? '—'}：只有這個帳號可以拿這個產物紙上執行`
                                    : approvalScope === 'job_only'
                                      ? 'job-only（skill_unit_id 為 NULL）：綁這個產物本身，不綁任何技能帳號'
                                      : approvalScope === 'no_approval'
                                        ? '沒有生效中的同意'
                                        : '讀不到綁定範圍（讀不到不等於已綁定）'}
                                </span>
                              }
                            />
                            {activation.state.approved_at && (
                              <FactRow label="Approved at" value={formatDateTime(activation.state.approved_at)} />
                            )}
                            <FactRow
                              label="Movable to a customer environment"
                              value={<YesNo value={activation.state.movable_to_customer_environment} />}
                            />
                            {Object.keys(activation.state.bindings ?? {}).length > 0 && (
                              <div style={{ marginTop: '10px' }}>
                                <h4 style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                                  Conditions the gate checked
                                </h4>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                                  {Object.entries(activation.state.bindings).map(([name, met]) => (
                                    <span
                                      key={name}
                                      data-testid={`activation-binding-${name}`}
                                      style={{
                                        fontSize: '12px',
                                        border: `1px solid ${met ? 'var(--positive)' : 'var(--negative)'}`,
                                        borderRadius: '4px',
                                        padding: '3px 8px',
                                      }}
                                    >
                                      {humanizeKey(name)}: {met ? 'met' : 'not met'}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}

                            {activation.state.activation_status !== 'APPROVED' && (
                              activation.requirements.artifact_checksum ? (
                                <div data-testid="activation-approve-form" style={{ marginTop: '14px' }}>
                                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: 0 }}>
                                    Approving records your own decision, on this account, for the one
                                    artifact whose checksum is shown below. It does not authorize any
                                    other run and does not place a trade. What it does cover is the
                                    scope you choose below: a job-only approval binds the artifact and
                                    authorises no skill account, while an account-bound approval
                                    authorises that one account and no other.
                                  </p>
                                  <FactRow
                                    label="Artifact this approval binds"
                                    value={<code style={{ fontSize: '11px', wordBreak: 'break-all' }}>{activation.requirements.artifact_checksum}</code>}
                                  />
                                  <FactRow label="Dataset checksum" value={<code style={{ fontSize: '11px' }}>{activation.requirements.dataset_checksum}</code>} />
                                  <FactRow label="Training contract" value={activation.requirements.training_contract_version} />
                                  <FactRow label="Activation policy version" value={activation.requirements.approval_policy_version} />
                                  <FactRow label="Acknowledgement text version" value={activation.requirements.acknowledgement_text_version} />
                                  <div data-testid="approval-scope-choice" style={{ margin: '14px 0' }}>
                                    <p style={{ fontSize: '13px', margin: '0 0 6px' }}>這份同意要綁到哪裡？</p>
                                    <label style={{ display: 'flex', gap: '8px', alignItems: 'center', fontSize: '13px', marginBottom: '6px' }}>
                                      <input
                                        type="radio"
                                        name="approval-scope"
                                        value="job_only"
                                        checked={approvalChoice === 'job_only'}
                                        onChange={() => setApprovalChoice('job_only')}
                                        data-testid="approval-scope-job-only"
                                      />
                                      <span>只綁這個產物（job-only）：可對它推論，不授權任何技能帳號紙上執行</span>
                                    </label>
                                    <label style={{ display: 'flex', gap: '8px', alignItems: 'center', fontSize: '13px', marginBottom: '10px' }}>
                                      <input
                                        type="radio"
                                        name="approval-scope"
                                        value="skill_unit"
                                        checked={approvalChoice === 'skill_unit'}
                                        onChange={() => setApprovalChoice('skill_unit')}
                                        data-testid="approval-scope-skill-unit"
                                      />
                                      <span>綁定我在下面明選的那一個技能帳號：只有它可以拿這個產物紙上執行</span>
                                    </label>
                                    <SkillUnitSelect
                                      units={skillUnits}
                                      value={skillUnitId}
                                      onChange={setSkillUnitId}
                                      testId="approval-skill-unit"
                                      error={skillUnitsError}
                                    />
                                    <p
                                      data-testid="approval-scope-sentence"
                                      style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '8px 0 0' }}
                                    >
                                      {approvalScopeSentence(approvalChoice, skillUnitId)}
                                    </p>
                                    {scopeChoiceError && (
                                      <p data-testid="approval-scope-error" style={{ fontSize: '12px', color: 'var(--negative)', margin: '6px 0 0' }}>
                                        {scopeChoiceError}
                                      </p>
                                    )}
                                  </div>
                                  <div
                                    data-testid="activation-ack-text"
                                    style={{
                                      fontSize: '13px',
                                      border: '1px solid var(--border)',
                                      borderRadius: '4px',
                                      padding: '10px 12px',
                                      margin: '10px 0',
                                      background: 'var(--surface-secondary, rgba(0,0,0,0.02))',
                                    }}
                                  >
                                    {activation.requirements.acknowledgement_text_zh}
                                  </div>
                                  {!screeningEnabled && (
                                    <p style={{ fontSize: '13px', color: 'var(--warning)', marginTop: 0 }}>
                                      Screening is switched off in this process, so an approval is refused.
                                      Ask the operator to enable it in an isolated development environment.
                                    </p>
                                  )}
                                  <label style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', fontSize: '13px', marginBottom: '10px' }}>
                                    <input
                                      type="checkbox"
                                      checked={activationRead}
                                      onChange={(e) => setActivationRead(e.target.checked)}
                                      data-testid="activation-ack-checkbox"
                                      style={{ marginTop: '3px' }}
                                    />
                                    <span>I have read the activation text above.</span>
                                  </label>
                                  <label htmlFor="activation-ack-entry" style={{ display: 'block', fontSize: '13px', marginBottom: '6px' }}>
                                    Submit that text yourself to confirm this is your own decision.
                                  </label>
                                  <textarea
                                    id="activation-ack-entry"
                                    value={activationAck}
                                    onChange={(e) => setActivationAck(e.target.value)}
                                    data-testid="activation-ack-entry"
                                    rows={3}
                                    style={{ width: '100%', maxWidth: '640px', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', marginBottom: '12px' }}
                                  />
                                  <div>
                                    <button
                                      type="button"
                                      className="btn btn-primary"
                                      onClick={onApproveActivation}
                                      disabled={!approveReady || scopeChoiceError !== null}
                                      data-testid="activation-approve"
                                    >
                                      {activationBusy ? 'Recording…' : 'Record my approval'}
                                    </button>
                                  </div>
                                </div>
                              ) : (
                                <p data-testid="activation-no-artifact" style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: 0 }}>
                                  This job recorded no artifact checksum, so there is nothing for an
                                  approval to bind and no approval form is offered.
                                </p>
                              )
                            )}

                            {activation.state.approval_id && (
                              <div data-testid="activation-revoke-block" style={{ marginTop: '14px' }}>
                                {!revokeActivationOpen ? (
                                  <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={onRevokeActivation}
                                    disabled={activationBusy}
                                    data-testid="activation-revoke"
                                  >
                                    Revoke this approval
                                  </button>
                                ) : (
                                  <div style={{ border: '1px solid var(--border)', borderRadius: '4px', padding: '12px', maxWidth: '640px' }}>
                                    <p style={{ fontSize: '13px', marginTop: 0 }}>
                                      Withdrawal is not gated — you can always switch this model back off.
                                      The approval record is kept as history and inference is refused again.
                                    </p>
                                    <label htmlFor="activation-revoke-reason" style={{ display: 'block', fontSize: '13px', marginBottom: '6px' }}>
                                      Reason (optional, kept with the record)
                                    </label>
                                    <input
                                      id="activation-revoke-reason"
                                      type="text"
                                      value={revokeActivationReason}
                                      onChange={(e) => setRevokeActivationReason(e.target.value)}
                                      data-testid="activation-revoke-reason"
                                      maxLength={64}
                                      autoComplete="off"
                                      style={{ width: '100%', padding: '8px 12px', border: '1px solid var(--border)', borderRadius: '4px', fontSize: '13px', marginBottom: '10px' }}
                                    />
                                    <div style={{ display: 'flex', gap: '8px' }}>
                                      <button
                                        type="button"
                                        className="btn btn-primary"
                                        onClick={onRevokeActivation}
                                        disabled={activationBusy}
                                        data-testid="activation-revoke-confirm"
                                      >
                                        {activationBusy ? 'Withdrawing…' : 'Confirm withdrawal'}
                                      </button>
                                      <button
                                        type="button"
                                        className="btn btn-secondary"
                                        onClick={() => {
                                          setRevokeActivationOpen(false);
                                          setRevokeActivationReason('');
                                        }}
                                        disabled={activationBusy}
                                      >
                                        Cancel
                                      </button>
                                    </div>
                                  </div>
                                )}
                              </div>
                            )}
                          </>
                        ) : null}
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
                          {/* The door refuses an un-activated artifact, so this can only read APPROVED. */}
                          <FactRow
                            label="Activation"
                            value={`${inference.activation_status} · approval ${inference.activation_approval_id ?? '—'}`}
                          />
                          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
                            This is not a recommendation and not a profit claim.
                          </p>
                        </div>
                      )}
                    </div>
                  )}

                  {activation && job && job.status === 'completed' && (
                    <div
                      data-testid="paper-execution-panel"
                      style={{ borderTop: '1px solid var(--border-subtle)', marginTop: '16px', paddingTop: '16px' }}
                    >
                      <h3 style={{ fontSize: '14px', marginBottom: '8px' }}>Paper execution on one of your skill accounts</h3>
                      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: 0 }}>
                        This door writes into your own paper book only: it reaches no broker and places
                        no live order (runs_live_trading: no). The result shown below is the ledger row
                        read back from the formal endpoint after your press — never a status this page
                        computes for itself.
                      </p>
                      <FactRow
                        label="Artifact this press binds"
                        value={<code style={{ fontSize: '11px', wordBreak: 'break-all' }}>{activation.requirements.artifact_checksum ?? '—'}</code>}
                      />
                      <div style={{ margin: '12px 0' }}>
                        <p style={{ fontSize: '13px', margin: '0 0 6px' }}>要在哪一個你的技能帳號上執行？</p>
                        <SkillUnitSelect
                          units={skillUnits}
                          value={skillUnitId}
                          onChange={setSkillUnitId}
                          testId="paper-skill-unit"
                          error={skillUnitsError}
                        />
                      </div>
                      <NoticeBox
                        notice={approvalScopeNotice(activation.state, skillUnitId)}
                        testId="paper-scope-notice"
                      />
                      <NoticeBox notice={paperNotice} testId="paper-notice" />
                      {paperReadError && (
                        <NoticeBox notice={{ tone: 'err', text: paperReadError }} testId="paper-read-error" />
                      )}
                      {paperBlockReason && (
                        <p data-testid="paper-block-reason" style={{ fontSize: '12px', color: 'var(--warning)', margin: '0 0 10px' }}>
                          {paperBlockReason}（這一筆不會送出）
                        </p>
                      )}
                      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        <button
                          type="button"
                          className="btn btn-primary"
                          onClick={onRunPaperExecution}
                          disabled={paperBlockReason !== null || paperBusy || busy !== null}
                          data-testid="paper-execute"
                        >
                          {paperBusy ? '提交中…' : '提交一次紙上決策'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => void loadPaperLedger(paperContext)}
                          disabled={paperBusy || busy !== null}
                          data-testid="paper-reload"
                        >
                          重新讀回 ledger（不重送意圖）
                        </button>
                      </div>

                      <p data-testid="paper-ledger-summary" style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '14px 0 6px' }}>
                        正式讀回這個 job 的 ledger 共 {paperRows.length} 列，其中屬於目前上下文（這個 job × 這個帳號 × 這個產物）的 {paperRowsOfContext.length} 列；
                        其他帳號或其他產物的列不列入下方，也不會被這一次解讀成你的結果。
                      </p>
                      {paperRowsOfContext.length === 0 ? (
                        <p data-testid="paper-ledger-empty" style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
                          這個上下文目前沒有紙上執行紀錄（0 列）。讀到 0 列不等於曾經執行過，也不等於執行失敗。
                        </p>
                      ) : (
                        <div style={{ overflowX: 'auto' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                            <thead>
                              <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border-strong)' }}>
                                <th style={{ padding: '8px' }}>Execution</th>
                                <th style={{ padding: '8px' }}>Signal</th>
                                <th style={{ padding: '8px' }}>Qty · price</th>
                                <th style={{ padding: '8px' }}>What the ledger says</th>
                              </tr>
                            </thead>
                            <tbody>
                              {paperRowsOfContext.map((row) => (
                                <tr
                                  key={row.execution_id}
                                  data-testid={`paper-ledger-row-${row.execution_id}`}
                                  style={{ borderBottom: '1px solid var(--border-subtle)' }}
                                >
                                  <td style={{ padding: '8px', verticalAlign: 'top' }}>
                                    <code style={{ fontSize: '11px' }}>{row.execution_id.slice(0, 8)}…</code>
                                    <div style={{ color: 'var(--text-muted)' }}>{formatDateTime(row.created_at ?? null)}</div>
                                    <span className={statusPillClass(row.execution_status)}>{row.execution_status}</span>
                                  </td>
                                  <td style={{ padding: '8px', verticalAlign: 'top' }}>
                                    {row.signal_date ?? '—'}
                                    <div>
                                      {row.quantity ?? '—'} × {row.execution_price ?? '—'}
                                    </div>
                                  </td>
                                  <td style={{ padding: '8px', verticalAlign: 'top' }}>
                                    {executionOutcomeZH(row)}
                                    <div style={{ color: 'var(--text-muted)', marginTop: '4px' }}>{tradeLinksZH(row)}</div>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
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
                              <th style={{ padding: '8px' }}>This artifact</th>
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
                                <td style={{ padding: '8px' }}>
                                  {/* Selection is the owner's act: nothing here moves the panel onto a
                                      job by itself, and the read follows only once it is on screen. */}
                                  <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={() => onSelectJob(row.job_id)}
                                    disabled={busy !== null || row.job_id === (job?.job_id ?? '')}
                                    data-testid={`select-job-${row.job_id}`}
                                  >
                                    {row.job_id === (job?.job_id ?? '') ? 'On screen' : 'Review this job'}
                                  </button>
                                </td>
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
