'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { formatDateTime } from '@/lib/format';

interface SimulationSession {
  id: string;
  experiment_version_id: string;
  status: string;
  initial_virtual_cash: number;
  data_cutoff_at: string;
  current_snapshot_date: string | null;
  paper_equity: number | null;
  current_position_json: any;
  warnings: string[] | null;
  total_decisions: number;
  observed_days: number;
  state_version: number;
  provider_id: string | null;
  timeframe: string | null;
  current_bar_timestamp: string | null;
  created_at: string;
}

interface AvailableProvider {
  provider_id: string;
  bar_count: number;
  min_date: string;
  max_date: string;
  requested_start: string;
  requested_end: string;
}

interface Decision {
  id: string;
  step_number: number;
  snapshot_date: string;
  bar_timestamp: string | null;
  decision_type: string;
  decision_detail: any;
  paper_cash: number;
  paper_position: any;
  paper_equity: number;
  warnings: string[] | null;
  engine_version: string;
  data_cutoff_at: string;
  created_at: string;
}

interface Experiment {
  id: string;
  title: string;
  status: string;
  current_version_id: string | null;
}

interface LinkedPortfolio {
  id: string;
  status: string;
  simulation_session_id: string | null;
  initial_virtual_cash: number;
  current_virtual_cash: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number | null;
  currency: string;
}

const DECISION_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  no_action: { label: 'No action', color: 'var(--text-secondary)', bg: 'var(--surface-2)' },
  simulated_entry: { label: 'Simulated buy', color: 'var(--positive)', bg: 'rgba(34,197,94,0.14)' },
  simulated_exit: { label: 'Simulated sell', color: 'var(--negative)', bg: 'rgba(239,68,68,0.12)' },
  forced_exit: { label: 'Forced exit', color: 'var(--warning)', bg: 'rgba(245,158,11,0.14)' },
  blocked_action: { label: 'Blocked', color: 'var(--warning)', bg: 'rgba(245,158,11,0.14)' },
};

// A submitted manual order that is queued for the next executable bar is stored
// as blocked_action (native enum reuse) with reason awaiting_execution_window.
// Rendering it as Blocked would report an accepted order as a refusal.
const PENDING_ORDER_STYLE = { label: 'Pending fill', color: 'var(--primary-text)', bg: 'rgba(59,130,246,0.16)' };

function isPendingOrder(d: Decision): boolean {
  return d.decision_detail?.rejection?.reason === 'awaiting_execution_window';
}

function decisionStyle(d: Decision) {
  if (isPendingOrder(d)) return PENDING_ORDER_STYLE;
  return DECISION_LABELS[d.decision_type] || DECISION_LABELS.no_action;
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Not started',
  active: 'In progress',
  paused: 'Paused',
  completed: 'Completed',
  retired: 'Retired',
};

// A session whose timeframe is one of these replays bar-by-bar (intraday); the
// daily path ('1d' or null) keeps the original "advance one day" semantics.
const INTRADAY_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h'];

const TIMEFRAME_LABELS: Record<string, string> = {
  '1m': '1 minute',
  '5m': '5 minutes',
  '15m': '15 minutes',
  '30m': '30 minutes',
  '1h': '1 hour',
  '1d': 'Daily',
};

// bar_timestamp is a naive exchange-local ISO instant (e.g. 2024-03-04T09:50:00).
// Render only the HH:MM time portion; the local date is shown separately.
function formatBarTime(barTimestamp: string | null | undefined): string {
  if (!barTimestamp) return '';
  const m = /T(\d{2}:\d{2})/.exec(barTimestamp);
  return m ? m[1] : barTimestamp;
}

export default function SimulationPage() {
  const router = useRouter();
  const params = useParams();
  const experimentId = params.id as string;

  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [session, setSession] = useState<SimulationSession | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [advancing, setAdvancing] = useState(false);
  const [error, setError] = useState('');
  const [cashInput, setCashInput] = useState('10000');
  const [creating, setCreating] = useState(false);
  const [showDetail, setShowDetail] = useState<number | null>(null);
  const [manualMode, setManualMode] = useState(false);
  const [manualAction, setManualAction] = useState<'buy' | 'sell' | 'hold'>('hold');
  const [manualQuantity, setManualQuantity] = useState('100');
  const [manualRationale, setManualRationale] = useState('');
  const [manualReflection, setManualReflection] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [availableProviders, setAvailableProviders] = useState<AvailableProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [loadingProviders, setLoadingProviders] = useState(false);
  const [editingReflection, setEditingReflection] = useState<string | null>(null);
  const [reflectionInput, setReflectionInput] = useState('');
  const [updatingReflection, setUpdatingReflection] = useState(false);
  const [pendingDecision, setPendingDecision] = useState<{
    idempotency_key: string;
    action: 'buy' | 'sell' | 'hold';
    quantity?: number;
    rationale?: string;
    reflection?: string;
    expected_version: number;
  } | null>(null);
  const [linkedPortfolio, setLinkedPortfolio] = useState<LinkedPortfolio | null>(null);
  const [linking, setLinking] = useState(false);
  const [priorSessions, setPriorSessions] = useState<SimulationSession[]>([]);
  const [showCreateForm, setShowCreateForm] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadExperiment();
  }, [experimentId, router]);

  const loadExperiment = async () => {
    try {
      const exp = await api.experiments.get(experimentId);
      setExperiment(exp);
      if (exp.status !== 'locked') {
        setError('The experiment must be locked before a walk-forward simulation can run.');
      }
      // Fetch available providers for this version
      if (exp.current_version_id) {
        loadAvailableProviders(exp.current_version_id);
        loadVersionSessions(exp.current_version_id);
      }
    } catch (err: any) {
      setError(err.message || 'Could not load the experiment.');
    } finally {
      setLoading(false);
    }
  };

  const loadLinkedPortfolio = async (sessionId: string) => {
    try {
      const portfolios: LinkedPortfolio[] = await api.paperPortfolio.list();
      setLinkedPortfolio(
        (portfolios || []).find((p) => p.simulation_session_id === sessionId) || null
      );
    } catch {
      setLinkedPortfolio(null);
    }
  };

  const loadVersionSessions = async (versionId: string) => {
    try {
      const list: SimulationSession[] = (await api.simulations.listForVersion(versionId)) || [];
      setPriorSessions(list);
      const resumable = list.find((s) =>
        ['pending', 'active', 'paused'].includes(s.status)
      );
      if (!resumable) return;
      setSession(resumable);
      setDecisions((await api.simulations.decisions(resumable.id)) || []);
      loadLinkedPortfolio(resumable.id);
    } catch (err: any) {
      setError(err.message || 'Could not load the replay sessions.');
    }
  };

  const loadAvailableProviders = async (versionId: string) => {
    setLoadingProviders(true);
    try {
      const result = await api.simulations.getAvailableProviders(versionId);
      setAvailableProviders(result.available_providers || []);
      // Auto-select if only one provider available
      if (result.available_providers?.length === 1) {
        setSelectedProvider(result.available_providers[0].provider_id);
      }
    } catch (err: any) {
      console.error('Failed to load providers:', err);
    } finally {
      setLoadingProviders(false);
    }
  };

  const loadSession = useCallback(async () => {
    if (!session?.id) return;
    try {
      const [s, d] = await Promise.all([
        api.simulations.get(session.id),
        api.simulations.decisions(session.id),
      ]);
      setSession(s);
      setDecisions(d);
    } catch (err: any) {
      setError(err.message);
    }
  }, [session?.id]);

  const handleCreate = async () => {
    if (!experiment?.current_version_id) return;
    const cash = parseFloat(cashInput);
    if (isNaN(cash) || cash <= 0) {
      setError('Enter a valid initial virtual cash amount.');
      return;
    }
    if (!selectedProvider) {
      setError('Select a data source.');
      return;
    }
    setCreating(true);
    setError('');
    try {
      const s = await api.simulations.create(experiment.current_version_id, {
        initial_virtual_cash: cash,
        provider_id: selectedProvider,
      });
      setSession(s);
      setDecisions([]);
      setLinkedPortfolio(null);
      setPriorSessions((prev) => [s, ...prev.filter((p) => p.id !== s.id)]);
      setShowCreateForm(false);
    } catch (err: any) {
      setError(err.message || 'Could not create the replay session.');
    } finally {
      setCreating(false);
    }
  };

  const handleAdvance = async () => {
    if (!session?.id) return;
    setAdvancing(true);
    setError('');
    try {
      const result = await api.simulations.advance(session.id);
      setSession(result.session);
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      loadLinkedPortfolio(session.id);
    } catch (err: any) {
      setError(err.message || 'Could not advance the replay session.');
    } finally {
      setAdvancing(false);
    }
  };

  const handlePause = async () => {
    if (!session?.id) return;
    try {
      const s = await api.simulations.pause(session.id);
      setSession(s);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleResume = async () => {
    if (!session?.id) return;
    try {
      const s = await api.simulations.resume(session.id);
      setSession(s);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleRetire = async () => {
    if (!session?.id) return;
    if (
      !confirm(
        'Retire this replay session? This cannot be undone and the session cannot be advanced further. ' +
          'Retiring only changes status: no position is sold, no order is placed, and paper holdings stay as they are.',
      )
    )
      return;
    try {
      const s = await api.simulations.retire(session.id);
      setSession(s);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleLinkPortfolio = async () => {
    if (!session?.id) return;
    setLinking(true);
    setError('');
    try {
      const created: LinkedPortfolio = await api.paperPortfolio.create({
        initial_virtual_cash: session.initial_virtual_cash,
        simulation_session_id: session.id,
      });
      setLinkedPortfolio(created);
    } catch (err: any) {
      setError(err.message || 'Could not create the paper portfolio.');
    } finally {
      setLinking(false);
    }
  };

  const handlePortfolioStatus = async (action: 'pause' | 'resume') => {
    if (!linkedPortfolio || !session?.id) return;
    setLinking(true);
    setError('');
    try {
      await api.paperPortfolio[action](linkedPortfolio.id);
      loadLinkedPortfolio(session.id);
    } catch (err: any) {
      setError(err.message || 'Could not update the paper portfolio status.');
    } finally {
      setLinking(false);
    }
  };

  const handleReopenSession = async (target: SimulationSession) => {
    setError('');
    setShowCreateForm(false);
    try {
      const [s, d] = await Promise.all([
        api.simulations.get(target.id),
        api.simulations.decisions(target.id),
      ]);
      setSession(s);
      setDecisions(d || []);
      loadLinkedPortfolio(s.id);
    } catch (err: any) {
      setError(err.message || 'Could not open the replay session.');
    }
  };

  const handleManualDecision = async () => {
    if (!session?.id) return;
    setSubmitting(true);
    setError('');

    let payload: {
      idempotency_key: string;
      action: 'buy' | 'sell' | 'hold';
      quantity?: number;
      rationale?: string;
      reflection?: string;
      expected_version: number;
    };

    if (pendingDecision) {
      payload = pendingDecision;
    } else {
      const key = `manual-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      const p: typeof payload = {
        action: manualAction,
        idempotency_key: key,
        expected_version: session.state_version,
        rationale: manualRationale || undefined,
        reflection: manualReflection || undefined,
      };
      if (manualAction === 'buy') {
        const qty = parseInt(manualQuantity);
        if (isNaN(qty) || qty <= 0) {
          setError('Enter a valid quantity.');
          setSubmitting(false);
          return;
        }
        p.quantity = qty;
      }
      payload = p;
      setPendingDecision(payload);
    }

    try {
      const result = await api.simulations.manualDecision(session.id, payload);
      setSession(result.session);
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      setPendingDecision(null);
      setManualRationale('');
      setManualReflection('');
    } catch (err: any) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.message || '';
        if (detail.includes('State version conflict')) {
          // Stale-version conflict: reload state, preserve form, require explicit new submit
          setPendingDecision(null);
          try {
            const [s, d] = await Promise.all([
              api.simulations.get(session.id),
              api.simulations.decisions(session.id),
            ]);
            setSession(s);
            setDecisions(d);
          } catch { /* refresh best-effort */ }
          setError('State version conflict: another action changed the state. Check the current state and submit again.');
        } else if (detail.includes('Idempotency key conflict') && detail.includes('different payload')) {
          // Payload conflict: same key but different payload - show specific error
          setPendingDecision(null);
          setError('Idempotency key conflict: the same key was sent with a different payload. Start a new decision, or cancel the pending retry.');
        } else {
          // Other 409: clear pending, show generic
          setPendingDecision(null);
          setError(`Conflict (409): ${detail}`);
        }
      } else {
        // Network failure or other error: preserve pendingDecision for retry
        setError(err.message || 'The decision was not confirmed. Use Retry to resend the same payload.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetryDecision = async () => {
    if (!pendingDecision) return;
    await handleManualDecision();
  };

  const handleUpdateReflection = async (decisionId: string) => {
    if (!session || !reflectionInput.trim()) return;
    
    setUpdatingReflection(true);
    try {
      await api.simulations.updateReflection(session.id, decisionId, reflectionInput.trim());
      const d = await api.simulations.decisions(session.id);
      setDecisions(d);
      setEditingReflection(null);
      setReflectionInput('');
    } catch (err: any) {
      setError(err.message || 'Could not update the reflection.');
    } finally {
      setUpdatingReflection(false);
    }
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px' }}>Loading…</div>;
  }

  const isIntraday = !!session?.timeframe && INTRADAY_TIMEFRAMES.includes(session.timeframe);
  const tfLabel = session?.timeframe ? (TIMEFRAME_LABELS[session.timeframe] || session.timeframe) : '';

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href={`/experiments/${experimentId}`} style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          &larr; Back to experiment
        </Link>
      </div>

      <h1 style={{ marginBottom: '8px' }}>Forward simulation</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '24px' }}>
        {experiment?.title}
      </p>

      <div className="alert alert-info" style={{ marginBottom: '24px' }}>
        <strong>Methodology:</strong> this simulation replays the locked strategy
        specification one stored data snapshot at a time. Each step sees only the data
        available at that moment, never later data (no hindsight). All trades are
        simulated; no real money is involved.
      </div>

      {error && <div className="alert alert-warning">{error}</div>}

      {(!session || showCreateForm) && experiment?.status === 'locked' && (
        <div className="card" style={{ marginBottom: '24px' }} data-testid="sim-create-card">
          <h3 style={{ marginBottom: '16px' }}>
            {session ? 'Start a new replay session' : 'Start a replay session'}
          </h3>
          {session && (
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
              A new session restarts from the locked specification; existing replay history is
              left untouched.
            </p>
          )}
          <div className="form-group">
            <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
              Starting virtual cash (USD)
            </label>
            <input
              type="number"
              className="input"
              value={cashInput}
              onChange={(e) => setCashInput(e.target.value)}
              min="1"
              step="100"
            />
          </div>
          
          {loadingProviders ? (
            <div style={{ padding: '12px', color: 'var(--text-secondary)' }}>Loading data sources…</div>
          ) : availableProviders.length === 0 ? (
            <div className="alert alert-warning" style={{ marginTop: '12px' }}>
              No stored data is available for this experiment&apos;s instruments.
            </div>
          ) : (
            <div className="form-group" style={{ marginTop: '16px' }}>
              <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                Data source
              </label>
              {availableProviders.length === 1 ? (
                <div style={{ padding: '10px', background: 'var(--surface-2)', borderRadius: '4px', border: '1px solid var(--border-strong)' }}>
                  <div style={{ fontWeight: 600 }}>{availableProviders[0].provider_id}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                    {availableProviders[0].bar_count} bars · coverage{' '}
                    {availableProviders[0].min_date} to {availableProviders[0].max_date}
                  </div>
                  {availableProviders[0].min_date > availableProviders[0].requested_start || 
                   availableProviders[0].max_date < availableProviders[0].requested_end ? (
                    <div style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '4px' }}>
                      Partial coverage: requested {availableProviders[0].requested_start} to{' '}
                      {availableProviders[0].requested_end}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {availableProviders.map((p) => (
                    <label key={p.provider_id} style={{ 
                      display: 'flex', 
                      alignItems: 'flex-start', 
                      padding: '10px', 
                      background: selectedProvider === p.provider_id ? 'rgba(59,130,246,0.16)' : 'var(--surface-2)',
                      borderRadius: '4px', 
                      border: selectedProvider === p.provider_id ? '2px solid var(--primary-text)' : '1px solid var(--border-strong)',
                      cursor: 'pointer',
                    }}>
                      <input
                        type="radio"
                        name="provider"
                        value={p.provider_id}
                        checked={selectedProvider === p.provider_id}
                        onChange={(e) => setSelectedProvider(e.target.value)}
                        style={{ marginTop: '2px', marginRight: '8px' }}
                      />
                      <div>
                        <div style={{ fontWeight: 600 }}>{p.provider_id}</div>
                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                          {p.bar_count} bars · coverage {p.min_date} to {p.max_date}
                        </div>
                        {p.min_date > p.requested_start || p.max_date < p.requested_end ? (
                          <div style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '2px' }}>
                            Partial coverage: requested {p.requested_start} to {p.requested_end}
                          </div>
                        ) : null}
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
          
          <button
            className="btn btn-primary"
            data-testid="sim-create-btn"
            onClick={handleCreate}
            disabled={creating || !selectedProvider || availableProviders.length === 0}
            style={{ marginTop: '12px' }}
          >
            {creating ? 'Creating…' : 'Start simulation'}
          </button>
          {session && (
            <button
              className="btn"
              data-testid="sim-create-cancel"
              onClick={() => setShowCreateForm(false)}
              style={{ marginTop: '12px', marginLeft: '12px' }}
            >
              Cancel
            </button>
          )}
        </div>
      )}

      {session && (
        <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '12px' }}>
            <button
              className="btn btn-secondary"
              data-testid="sim-new-session"
              onClick={() => {
                setError('');
                setShowCreateForm(true);
              }}
            >
              Start a new replay session
            </button>
          </div>
          {isIntraday && (
            <div
              data-testid="sim-intraday-badge"
              className="alert"
              style={{
                marginBottom: '16px',
                background: 'rgba(239,68,68,0.12)',
                border: '1px solid rgba(239,68,68,0.34)',
                color: 'var(--negative)',
                fontSize: '13px',
                lineHeight: 1.6,
              }}
            >
              <strong>Synthetic data · Historical replay · Not a live feed</strong>
              <span style={{ marginLeft: '8px' }}>
                Timeframe: <span data-testid="sim-intraday-timeframe">{tfLabel} bars</span>
                {session.provider_id && (
                  <> · Source: <span data-testid="sim-intraday-provider">{session.provider_id}</span></>
                )}
                {session.current_bar_timestamp && (
                  <>
                    {' '}· Replaying at:{' '}
                    <span data-testid="sim-current-bar">
                      {session.current_bar_timestamp.slice(0, 10)} {formatBarTime(session.current_bar_timestamp)} (exchange local time)
                    </span>
                  </>
                )}
              </span>
              <div style={{ marginTop: '4px', color: 'var(--text-secondary)' }}>
                Each advance releases only the next completed bar. A signal forms at that bar&apos;s
                close and fills at the next bar&apos;s open. Profits on fixed synthetic data do not
                show that the strategy works.
              </div>
            </div>
          )}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: '16px',
            marginBottom: '24px',
          }}>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Status</div>
              <div style={{ fontSize: '20px', fontWeight: 700 }}>
                {STATUS_LABELS[session.status] || session.status}
              </div>
            </div>
            <div className="card" style={{ padding: '16px' }} data-testid="sim-paper-equity">
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Virtual equity</div>
              {session.paper_equity === null ? (
                <>
                  <div style={{ fontSize: '20px', fontWeight: 700 }}>—</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>No paper portfolio linked</div>
                </>
              ) : (
                <>
                  <div style={{ fontSize: '20px', fontWeight: 700 }}>
                    ${session.paper_equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                  <div style={{ fontSize: '12px', color: session.paper_equity >= session.initial_virtual_cash ? 'var(--positive)' : 'var(--negative)' }}>
                    {session.paper_equity >= session.initial_virtual_cash ? '+' : ''}
                    {((session.paper_equity - session.initial_virtual_cash) / session.initial_virtual_cash * 100).toFixed(2)}%
                  </div>
                </>
              )}
            </div>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                {isIntraday ? 'Bars observed' : 'Days observed'}
              </div>
              <div data-testid="sim-observed-count" style={{ fontSize: '20px', fontWeight: 700 }}>{session.observed_days}</div>
            </div>
            <div className="card" style={{ padding: '16px' }}>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Decisions</div>
              <div style={{ fontSize: '20px', fontWeight: 700 }}>{session.total_decisions}</div>
            </div>
            {session.provider_id && (
              <div className="card" style={{ padding: '16px' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Data source</div>
                <div style={{ fontSize: '16px', fontWeight: 700 }}>{session.provider_id}</div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>Stored data</div>
              </div>
            )}
          </div>

          {session.current_position_json && (
            <div className="card" style={{ marginBottom: '24px', borderLeft: '4px solid var(--primary)' }}>
              <h3 style={{ marginBottom: '8px' }}>Current position</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px' }}>
                <div>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Instrument</span>
                  <div style={{ fontWeight: 600 }}>{session.current_position_json.symbol}</div>
                </div>
                <div>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Shares</span>
                  <div style={{ fontWeight: 600 }}>{session.current_position_json.shares}</div>
                </div>
                <div>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Entry price</span>
                  <div style={{ fontWeight: 600 }}>${session.current_position_json.entry_price?.toFixed(2)}</div>
                </div>
                <div>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Entry date</span>
                  <div style={{ fontWeight: 600 }}>{session.current_position_json.entry_date}</div>
                </div>
                {session.current_position_json.unrealized_pnl !== undefined && (
                  <div>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Unrealized P&amp;L</span>
                    <div style={{
                      fontWeight: 600,
                      color: session.current_position_json.unrealized_pnl >= 0 ? 'var(--positive)' : 'var(--negative)',
                    }}>
                      ${session.current_position_json.unrealized_pnl?.toFixed(2)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: '8px', marginBottom: '24px', flexWrap: 'wrap' }}>
            {(session.status === 'pending' || session.status === 'active') && (
              <>
                <button
                  className="btn btn-primary"
                  data-testid="sim-advance-btn"
                  onClick={handleAdvance}
                  disabled={advancing || manualMode}
                >
                  {advancing ? 'Advancing…' : isIntraday ? `Advance one ${tfLabel} bar` : 'Advance one day'}
                </button>
                <button
                  className="btn"
                  style={{ backgroundColor: manualMode ? 'var(--primary-solid)' : 'var(--surface-2)', color: manualMode ? 'var(--text-white)' : 'var(--text-primary)' }}
                  onClick={() => setManualMode(!manualMode)}
                >
                  {manualMode ? 'Switch to automatic mode' : 'Switch to manual mode'}
                </button>
              </>
            )}
            {session.status === 'active' && (
              <button className="btn" style={{ backgroundColor: 'var(--surface-2)' }} onClick={handlePause}>
                Pause
              </button>
            )}
            {session.status === 'paused' && (
              <button className="btn btn-primary" onClick={handleResume}>
                Resume
              </button>
            )}
            {!['completed', 'retired'].includes(session.status) && (
              <button
                className="btn"
                style={{ backgroundColor: 'rgba(239,68,68,0.12)', color: 'var(--negative)' }}
                onClick={handleRetire}
              >
                Retire
              </button>
            )}
          </div>

          <div
            data-testid="sim-linked-portfolio"
            className="card"
            style={{ marginBottom: '24px', borderLeft: '4px solid var(--primary)' }}
          >
            <h3 style={{ marginBottom: '8px' }}>Linked paper portfolio</h3>
            {!linkedPortfolio ? (
              <>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                  No paper portfolio is linked yet. Once linked, entries and exits triggered by the
                  locked strategy are written to that portfolio&apos;s orders, fills, positions, cash
                  and decision log. While unlinked, this replay records only the research log and
                  creates no portfolio data.
                </p>
                <button
                  data-testid="sim-link-portfolio-btn"
                  className="btn btn-primary"
                  onClick={handleLinkPortfolio}
                  disabled={linking}
                >
                  {linking ? 'Creating…' : `Create and link paper portfolio (starting cash $${session.initial_virtual_cash.toLocaleString()})`}
                </button>
              </>
            ) : (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px', marginBottom: '12px' }}>
                  <div>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Portfolio status</span>
                    <div data-testid="sim-portfolio-status" style={{ fontWeight: 600 }}>
                      {STATUS_LABELS[linkedPortfolio.status] || linkedPortfolio.status}
                    </div>
                  </div>
                  <div>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Available virtual cash</span>
                    <div data-testid="sim-portfolio-cash" style={{ fontWeight: 600 }}>
                      ${linkedPortfolio.current_virtual_cash.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                  <div>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Realized P&amp;L</span>
                    <div data-testid="sim-portfolio-realized" style={{
                      fontWeight: 600,
                      color: linkedPortfolio.total_realized_pnl >= 0 ? 'var(--positive)' : 'var(--negative)',
                    }}>
                      ${linkedPortfolio.total_realized_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                  </div>
                  <div>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Unrealized P&amp;L</span>
                    <div data-testid="sim-portfolio-unrealized" style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>
                      {linkedPortfolio.total_unrealized_pnl === null
                        ? 'Cannot be valued'
                        : `$${linkedPortfolio.total_unrealized_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                  <Link
                    data-testid="sim-portfolio-link"
                    href={`/paper-portfolio/${linkedPortfolio.id}`}
                    className="btn"
                    style={{ backgroundColor: 'var(--surface-2)', textDecoration: 'none' }}
                  >
                    Open portfolio (orders, fills, log)
                  </Link>
                  {linkedPortfolio.status === 'active' && (
                    <button
                      data-testid="sim-portfolio-pause"
                      className="btn"
                      style={{ backgroundColor: 'rgba(245,158,11,0.14)', color: 'var(--warning)' }}
                      onClick={() => handlePortfolioStatus('pause')}
                      disabled={linking}
                    >
                      Pause portfolio
                    </button>
                  )}
                  {linkedPortfolio.status === 'paused' && (
                    <button
                      data-testid="sim-portfolio-resume"
                      className="btn btn-primary"
                      onClick={() => handlePortfolioStatus('resume')}
                      disabled={linking}
                    >
                      Resume portfolio
                    </button>
                  )}
                </div>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '12px' }}>
                  While the portfolio is paused, rule-triggered <strong>new entries</strong> are
                  recorded as blocked and do not fill; risk exits such as stop-loss and take-profit
                  still run. A retired portfolio accepts no further rule orders. Manual buy and sell
                  decisions place an order in this portfolio and fill at the next executable bar in
                  the bound dataset; HOLD is written to the replay log only and leaves the portfolio
                  untouched.
                </p>
              </>
            )}
          </div>

          {priorSessions.length > 1 && (
            <div className="card" style={{ marginBottom: '24px' }}>
              <h3 style={{ marginBottom: '8px' }}>Replay sessions for this version</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {priorSessions.map((s) => (
                  <button
                    key={s.id}
                    data-testid={`sim-session-${s.status}`}
                    className="btn"
                    style={{
                      justifyContent: 'flex-start',
                      backgroundColor: s.id === session?.id ? 'rgba(59,130,246,0.16)' : 'transparent',
                      border: '1px solid var(--border-strong)',
                      fontSize: '13px',
                    }}
                    onClick={() => handleReopenSession(s)}
                    disabled={s.id === session?.id}
                  >
                    {formatDateTime(s.created_at)} — {STATUS_LABELS[s.status] || s.status}
                    {s.current_snapshot_date ? ` (observation day ${s.current_snapshot_date})` : ' (not advanced yet)'}
                  </button>
                ))}
              </div>
            </div>
          )}

          {manualMode && (session.status === 'pending' || session.status === 'active') && (
            <div className="card" style={{ marginBottom: '24px', borderLeft: '4px solid var(--primary)' }}>
              <h3 style={{ marginBottom: '16px' }}>Manual decision</h3>
              <div className="alert alert-info" style={{ marginBottom: '16px', fontSize: '13px' }}>
                <strong>Execution timing:</strong> a decision is made after the observation day&apos;s
                close and fills at the next available session&apos;s open. Buy and sell follow the
                same rule, so the replay cannot look ahead.
              </div>
              <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', flexWrap: 'wrap' }}>
                <button
                  className="btn"
                  style={{
                    backgroundColor: manualAction === 'buy' ? 'var(--positive)' : 'var(--surface-2)',
                    color: manualAction === 'buy' ? 'var(--text-on-bright)' : 'var(--text-primary)',
                    minWidth: '100px',
                  }}
                  onClick={() => setManualAction('buy')}
                >
                  Buy (BUY)
                </button>
                <button
                  className="btn"
                  style={{
                    backgroundColor: manualAction === 'sell' ? 'var(--negative)' : 'var(--surface-2)',
                    color: manualAction === 'sell' ? 'var(--text-on-bright)' : 'var(--text-primary)',
                    minWidth: '100px',
                  }}
                  onClick={() => setManualAction('sell')}
                >
                  Sell (SELL)
                </button>
                <button
                  className="btn"
                  style={{
                    backgroundColor: manualAction === 'hold' ? 'var(--text-secondary)' : 'var(--surface-2)',
                    color: manualAction === 'hold' ? 'var(--text-on-bright)' : 'var(--text-primary)',
                    minWidth: '100px',
                  }}
                  onClick={() => setManualAction('hold')}
                >
                  Hold (HOLD)
                </button>
              </div>
              {manualAction === 'buy' && (
                <div className="form-group" style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                    Quantity (shares)
                  </label>
                  <input
                    type="number"
                    className="input"
                    value={manualQuantity}
                    onChange={(e) => setManualQuantity(e.target.value)}
                    min="1"
                    step="1"
                  />
                </div>
              )}
              <div className="form-group" style={{ marginBottom: '16px' }}>
                <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                  Rationale (optional)
                </label>
                <textarea
                  className="input"
                  value={manualRationale}
                  onChange={(e) => setManualRationale(e.target.value)}
                  placeholder="Why you are making this decision…"
                  rows={2}
                  style={{ resize: 'vertical' }}
                />
              </div>
              <div className="form-group" style={{ marginBottom: '16px' }}>
                <label style={{ display: 'block', marginBottom: '6px', fontWeight: 600 }}>
                  Reflection (optional, can be added later)
                </label>
                <textarea
                  className="input"
                  value={manualReflection}
                  onChange={(e) => setManualReflection(e.target.value)}
                  placeholder="What you learned from this decision…"
                  rows={2}
                  style={{ resize: 'vertical' }}
                />
              </div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button
                  className="btn btn-primary"
                  onClick={handleManualDecision}
                  disabled={submitting || !!pendingDecision}
                >
                  {submitting ? 'Submitting…' : 'Submit decision'}
                </button>
                {pendingDecision && (
                  <button
                    className="btn"
                    style={{ backgroundColor: 'var(--warning)', color: 'var(--text-primary)' }}
                    onClick={handleRetryDecision}
                    disabled={submitting}
                  >
                    {submitting ? 'Retrying…' : 'Retry (same payload)'}
                  </button>
                )}
                {pendingDecision && (
                  <button
                    className="btn"
                    style={{ backgroundColor: 'var(--surface-2)', color: 'var(--text-primary)' }}
                    onClick={() => {
                      if (confirm('Abandoning the retry does not cancel a trade the backend already accepted. If the decision was submitted successfully, it still stands. Abandon the retry and reload the current state?')) {
                        setPendingDecision(null);
                        setError('');
                        // Refresh state to reconcile
                        loadSession();
                      }
                    }}
                    disabled={submitting}
                  >
                    Abandon retry
                  </button>
                )}
              </div>
              {pendingDecision && (
                <div className="alert" style={{ marginTop: '12px', fontSize: '12px', background: 'rgba(245,158,11,0.14)', border: '1px solid rgba(245,158,11,0.34)' }}>
                  <strong>Decision awaiting retry:</strong>
                  idempotency_key=<code>{pendingDecision.idempotency_key}</code>,
                  action={pendingDecision.action},
                  expected_version={pendingDecision.expected_version}
                  {pendingDecision.quantity != null && `, quantity=${pendingDecision.quantity}`}
                </div>
              )}
            </div>
          )}

          {session.warnings && session.warnings.length > 0 && (
            <div className="alert alert-warning" style={{ marginBottom: '24px' }}>
              <strong>Warnings ({session.warnings.length}):</strong>
              <ul style={{ marginTop: '8px', paddingLeft: '20px' }}>
                {session.warnings.slice(-5).map((w, i) => (
                  <li key={i} style={{ marginBottom: '4px' }}>{w}</li>
                ))}
                {session.warnings.length > 5 && (
                  <li style={{ color: 'var(--text-muted)' }}>…and {session.warnings.length - 5} more</li>
                )}
              </ul>
            </div>
          )}

          <div className="card">
            <h3 style={{ marginBottom: '16px' }}>Decision timeline</h3>
            {decisions.length === 0 ? (
              <p style={{ color: 'var(--text-muted)' }}>
                No decisions yet. Choose “{isIntraday ? `Advance one ${tfLabel} bar` : 'Advance one day'}” to start the simulation.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {decisions.map((d) => {
                  const style = decisionStyle(d);
                  const isExpanded = showDetail === d.step_number;
                  return (
                    <div
                      key={d.id}
                      style={{
                        border: '1px solid var(--border-strong)',
                        borderLeft: `4px solid ${style.color}`,
                        borderRadius: '6px',
                        padding: '12px 16px',
                        backgroundColor: isExpanded ? 'var(--surface-2)' : 'var(--surface-1)',
                        cursor: 'pointer',
                      }}
                      onClick={() => setShowDetail(isExpanded ? null : d.step_number)}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                          <span style={{ fontSize: '12px', color: 'var(--text-muted)', minWidth: '24px' }}>
                            #{d.step_number}
                          </span>
                          <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                            {d.snapshot_date}
                          </span>
                          {d.bar_timestamp && (
                            <span
                              data-testid="sim-decision-bar-ts"
                              title="Bar timestamp in exchange local time"
                              style={{
                                fontSize: '12px',
                                fontWeight: 600,
                                color: 'var(--negative)',
                                background: 'rgba(239,68,68,0.12)',
                                border: '1px solid rgba(239,68,68,0.34)',
                                borderRadius: '10px',
                                padding: '1px 8px',
                              }}
                            >
                              {formatBarTime(d.bar_timestamp)}
                            </span>
                          )}
                          <span style={{
                            padding: '2px 10px',
                            borderRadius: '12px',
                            fontSize: '12px',
                            fontWeight: 600,
                            color: style.color,
                            backgroundColor: style.bg,
                          }}>
                            {style.label}
                          </span>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <span style={{ fontSize: '14px', fontWeight: 600 }}>
                            ${d.paper_equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                        </div>
                      </div>

                      {isExpanded && d.decision_detail && (
                        <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid var(--border-strong)' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '8px', fontSize: '13px' }}>
                            {d.decision_detail.symbol && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Instrument:</span> {d.decision_detail.symbol}</div>
                            )}
                            {d.decision_detail.action && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Action:</span> {d.decision_detail.action.toUpperCase()}</div>
                            )}
                            {d.decision_detail.fill?.quantity && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Quantity:</span> {d.decision_detail.fill.quantity}</div>
                            )}
                            {d.decision_detail.fill?.observed_date && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Observation day:</span> {d.decision_detail.fill.observed_date}</div>
                            )}
                            {d.decision_detail.fill?.observed_close && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Observed close:</span> ${d.decision_detail.fill.observed_close?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.execution_date && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Fill date:</span> {d.decision_detail.fill.execution_date}</div>
                            )}
                            {d.decision_detail.fill?.price && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Fill price:</span> ${d.decision_detail.fill.price?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.execution_rule && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Execution rule:</span> {d.decision_detail.fill.execution_rule}</div>
                            )}
                            {d.decision_detail.fill?.commission !== undefined && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Commission:</span> ${d.decision_detail.fill.commission?.toFixed(4)}</div>
                            )}
                            {d.decision_detail.fill?.slippage !== undefined && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Slippage:</span> ${d.decision_detail.fill.slippage?.toFixed(4)}</div>
                            )}
                            {d.decision_detail.fill?.total_cost && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Total cost:</span> ${d.decision_detail.fill.total_cost?.toFixed(2)}</div>
                            )}
                            {d.decision_detail.fill?.net_proceeds && (
                              <div><span style={{ color: 'var(--text-secondary)' }}>Net proceeds:</span> ${d.decision_detail.fill.net_proceeds?.toFixed(2)}</div>
                            )}
                            {(d.decision_detail.fill?.gross_pnl !== undefined || d.decision_detail.fill?.realized_pnl !== undefined) && (
                              <div>
                                <span style={{ color: 'var(--text-secondary)' }}>Price P&amp;L:</span> 
                                <span style={{ 
                                  color: (d.decision_detail.fill.gross_pnl ?? d.decision_detail.fill.realized_pnl) >= 0 ? 'var(--positive)' : 'var(--negative)', 
                                  fontWeight: 600 
                                }}>
                                  {' '}${(d.decision_detail.fill.gross_pnl ?? d.decision_detail.fill.realized_pnl)?.toFixed(2)}
                                </span>
                              </div>
                            )}
                            {d.decision_detail.rejection && (
                              <div
                                data-testid="sim-decision-rejection"
                                style={{
                                  gridColumn: '1 / -1',
                                  padding: '8px',
                                  borderRadius: '4px',
                                  backgroundColor: isPendingOrder(d) ? PENDING_ORDER_STYLE.bg : 'rgba(245,158,11,0.14)',
                                  color: isPendingOrder(d) ? 'var(--primary-text)' : 'var(--warning)',
                                }}
                              >
                                <span style={{ color: isPendingOrder(d) ? PENDING_ORDER_STYLE.color : 'var(--warning)', fontWeight: 600 }}>
                                  {isPendingOrder(d) ? 'Pending fill:' : 'Rejected:'}
                                </span>{' '}
                                {d.decision_detail.rejection.message || d.decision_detail.rejection.reason}
                                {d.decision_detail.rejection.message && (
                                  <span style={{ color: 'var(--text-muted)', fontSize: '11px', fontFamily: 'monospace' }}>
                                    {' '}({d.decision_detail.rejection.reason})
                                  </span>
                                )}
                                {d.decision_detail.rejection.required && (
                                  <span> (required: ${d.decision_detail.rejection.required.toFixed(2)}, available: ${d.decision_detail.rejection.available.toFixed(2)})</span>
                                )}
                              </div>
                            )}
                            {d.decision_detail.rationale && (
                              <div style={{ gridColumn: '1 / -1' }}><span style={{ color: 'var(--text-secondary)' }}>Rationale:</span> {d.decision_detail.rationale}</div>
                            )}
                            {d.decision_detail.reflection && (
                              <div style={{ gridColumn: '1 / -1' }}>
                                <span style={{ color: 'var(--text-secondary)' }}>Reflection:</span> {d.decision_detail.reflection}
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setEditingReflection(d.id);
                                    setReflectionInput(d.decision_detail.reflection || '');
                                  }}
                                  style={{
                                    marginLeft: '8px',
                                    padding: '2px 8px',
                                    fontSize: '11px',
                                    border: '1px solid var(--border-strong)',
                                    borderRadius: '3px',
                                    backgroundColor: 'var(--surface-1)',
                                    cursor: 'pointer',
                                  }}
                                >
                                  Edit
                                </button>
                              </div>
                            )}
                            {!d.decision_detail.reflection && (
                              <div style={{ gridColumn: '1 / -1' }}>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setEditingReflection(d.id);
                                    setReflectionInput('');
                                  }}
                                  style={{
                                    padding: '4px 12px',
                                    fontSize: '12px',
                                    border: '1px solid var(--primary-text)',
                                    borderRadius: '4px',
                                    backgroundColor: 'var(--surface-1)',
                                    color: 'var(--primary-text)',
                                    cursor: 'pointer',
                                  }}
                                >
                                  Add reflection
                                </button>
                              </div>
                            )}
                            {editingReflection === d.id && (
                              <div style={{ gridColumn: '1 / -1', marginTop: '8px' }} onClick={(e) => e.stopPropagation()}>
                                <textarea
                                  value={reflectionInput}
                                  onChange={(e) => setReflectionInput(e.target.value)}
                                  placeholder="Write your reflection…"
                                  style={{
                                    width: '100%',
                                    minHeight: '80px',
                                    padding: '8px',
                                    border: '1px solid var(--border-strong)',
                                    borderRadius: '4px',
                                    fontSize: '13px',
                                    fontFamily: 'inherit',
                                    resize: 'vertical',
                                  }}
                                />
                                <div style={{ marginTop: '8px', display: 'flex', gap: '8px' }}>
                                  <button
                                    onClick={() => handleUpdateReflection(d.id)}
                                    disabled={updatingReflection || !reflectionInput.trim()}
                                    style={{
                                      padding: '6px 16px',
                                      fontSize: '12px',
                                      border: 'none',
                                      borderRadius: '4px',
                                      backgroundColor: 'var(--primary-solid)',
                                      color: 'var(--text-white)',
                                      cursor: updatingReflection || !reflectionInput.trim() ? 'not-allowed' : 'pointer',
                                      opacity: updatingReflection || !reflectionInput.trim() ? 0.6 : 1,
                                    }}
                                  >
                                    {updatingReflection ? 'Saving…' : 'Save'}
                                  </button>
                                  <button
                                    onClick={() => {
                                      setEditingReflection(null);
                                      setReflectionInput('');
                                    }}
                                    style={{
                                      padding: '6px 16px',
                                      fontSize: '12px',
                                      border: '1px solid var(--border-strong)',
                                      borderRadius: '4px',
                                      backgroundColor: 'var(--surface-1)',
                                      cursor: 'pointer',
                                    }}
                                  >
                                    Cancel
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                          <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                            Cash: ${d.paper_cash.toFixed(2)} | Engine: {d.engine_version} | Data as of: {d.data_cutoff_at}
                          </div>
                        </div>
                      )}

                      {d.warnings && d.warnings.length > 0 && (
                        <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--warning)' }}>
                          {d.warnings.join('; ')}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div style={{ marginTop: '24px', padding: '16px', backgroundColor: 'var(--surface-1)', borderRadius: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            <strong>Methodology status:</strong>
            {manualMode ? 'manual_replay' :
             session.status === 'completed' ? 'historical_test_complete' :
             session.status === 'retired' ? 'retired' :
             'historical_replay'}
            {' '}&mdash; 
            {manualMode ? 'Manual replay: you choose buy, sell or hold at each step.' :
             'Automatic strategy replay: the engine decides from the strategy rules.'}
            {' '}Data source: {isIntraday
              ? `the bound synthetic/historical ${tfLabel} bar dataset (historical replay, not a live feed; zero live API requests)`
              : 'stored historical data (zero live API requests)'}.
            Data cutoff: {session.data_cutoff_at}.
            <br />
            <strong>Execution timing:</strong>
            {isIntraday
              ? `a signal forms at the close of a completed ${tfLabel} bar and fills at the next bar's open (exchange local time; the same rule applies to buys and sells).`
              : 'a decision is made after the observation day\'s close and fills at the next available session\'s open (the same rule applies to buys and sells).'}
          </div>
        </>
      )}
    </div>
  );
}
