/**
 * Shared English labels for backend codes.
 *
 * Glossary used across the workspace and research pages so one business object
 * is never called four different things:
 *   experiment            — a research experiment
 *   strategy version      — a saved, immutable strategy configuration
 *   revision              — a proposed change to a strategy version
 *   run                   — one backtest / training / inference execution
 *   replay session        — a historical walk-forward session
 *   paper portfolio       — a simulated (paper-only) book of positions
 *   decision review       — a recorded decision awaiting or after review
 *   dataset snapshot      — the frozen data a run was evaluated on
 *   training job          — a model training execution
 *   model version         — a trained, versioned model artifact
 *   active version        — the version currently selected for use
 *   comparison            — an evaluation across versions under the same data
 *
 * Backend codes stay the source of truth: `label()` maps a stable code to
 * English and falls back to the raw code (never to Chinese, never to a guess),
 * so an unmapped state is still traceable in the UI.
 */

export const NOT_PROVIDED = 'Not provided';
export const CANNOT_COMPUTE = 'Cannot be computed';
export const NOT_APPLICABLE = 'Not applicable';

export const GLOSSARY = {
  experiment: 'Experiment',
  strategyVersion: 'Strategy version',
  revision: 'Revision',
  run: 'Run',
  replaySession: 'Replay session',
  paperPortfolio: 'Paper portfolio',
  decisionReview: 'Decision review',
  datasetSnapshot: 'Dataset snapshot',
  trainingJob: 'Training job',
  modelVersion: 'Model version',
  activeVersion: 'Active version',
  comparison: 'Comparison',
} as const;

/** Map a backend code to English; unmapped codes are shown verbatim. */
export const label = (map: Record<string, string>, value: string | null | undefined): string => {
  if (value === null || value === undefined || value === '') return NOT_PROVIDED;
  return map[value] ?? value;
};

/* ── people and provenance ──────────────────────────────────────────── */

export const ACTOR_LABELS: Record<string, string> = {
  system: 'System rule',
  human: 'You',
  ai_draft: 'AI draft',
  human_approved_ai_draft: 'You (approved AI draft)',
  api_key: 'API client',
  engine: 'Strategy engine',
  user: 'You',
};

/* ── lifecycle ──────────────────────────────────────────────────────── */

export const ORDER_STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  filled: 'Filled',
  partially_filled: 'Partially filled',
  cancelled: 'Cancelled',
  canceled: 'Cancelled',
  rejected: 'Rejected',
};

export const POSITION_STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  closed: 'Closed',
};

export const INTENT_STATUS_LABELS: Record<string, string> = {
  pending: 'Awaiting authorization',
  authorized: 'Authorized',
  submitted: 'Submitted',
  filled: 'Filled',
  partially_filled: 'Partially filled',
  cancelled: 'Cancelled',
  canceled: 'Cancelled',
  rejected: 'Rejected',
  uncertain: 'Outcome unknown',
};

export const PORTFOLIO_STATUS_LABELS: Record<string, string> = {
  active: 'Active',
  paused: 'Paused',
  retired: 'Retired',
  completed: 'Completed',
};

export const SESSION_STATUS_LABELS: Record<string, string> = {
  pending: 'Not started',
  active: 'In progress',
  paused: 'Paused',
  completed: 'Completed',
  retired: 'Retired',
};

export const RUN_STATUS_LABELS: Record<string, string> = {
  pending: 'Not started',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
};

/* ── trade vocabulary ───────────────────────────────────────────────── */

export const SIDE_LABELS: Record<string, string> = { BUY: 'Buy', SELL: 'Sell' };

export const ACTION_LABELS: Record<string, string> = {
  buy: 'Buy',
  sell: 'Sell',
  hold: 'Hold',
};

export const RECORD_TYPE_LABELS: Record<string, string> = {
  fill: 'Fill',
  order: 'Unfilled order',
};

export const DECISION_TYPE_LABELS: Record<string, string> = {
  simulated_entry: 'Simulated entry',
  simulated_exit: 'Simulated exit',
  no_action: 'No action',
  blocked_action: 'Blocked',
  entry: 'Entry',
  exit: 'Exit',
  pause: 'Pause',
  retire: 'Retire',
  override_request: 'Override request',
};

export const CONFIDENCE_LABELS: Record<string, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  not_provided: NOT_PROVIDED,
};

/** The outcome captured on the decision when it was submitted — not the live order state. */
export const OUTCOME_STATUS_LABELS: Record<string, string> = {
  pending: 'Pending evaluation',
  evaluated: 'Evaluated',
  expired: 'Expired without evaluation',
};

/** Why a simulated position was closed; the backend's stable exit-rule codes. */
export const EXIT_REASON_LABELS: Record<string, string> = {
  stop: 'Stop-loss triggered',
  target: 'Take-profit triggered',
  time: 'Holding period elapsed',
  signal: 'Strategy signal',
  end_of_data: 'Forced close at end of data',
  session_end: 'Session-end forced exit',
};

/** How a recorded manual intent was handled; it is never executed as a real order. */
export const HUMAN_EXECUTION_STATUS_LABELS: Record<string, string> = {
  record_only: 'Recorded only',
};

/* ── research ───────────────────────────────────────────────────────── */

export const EXPERIMENT_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  exploring: 'Exploring',
  locked: 'Locked',
  archived: 'Archived',
};

export const DRAFT_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  pending_review: 'Pending review',
  accepted: 'Accepted',
  rejected: 'Returned',
};

export const STRATEGY_TEMPLATE_LABELS: Record<string, string> = {
  buy_and_hold: 'Buy & Hold',
  sma_crossover: 'SMA Crossover',
  price_change_fixed_hold: 'Price Change',
};

export const ROBUSTNESS_KIND_LABELS: Record<string, string> = {
  cost_scenarios: 'Cost scenarios (commission × slippage)',
  period_slices: 'Period slices (contiguous, non-overlapping)',
  parameter_sensitivity: 'Parameter sensitivity (one factor ±20%)',
  walk_forward: 'Walk-forward (choose parameters on training, measure on test)',
};

export const ROBUSTNESS_STUDY_STATUS_LABELS: Record<string, string> = {
  planned: 'Planned, not run',
  running: 'Running',
  completed: 'Completed',
  partial: 'Partially completed (some attempts failed or were skipped)',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export const ATTEMPT_STATUS_LABELS: Record<string, string> = {
  pending: 'Not run',
  completed: 'Completed',
  failed: 'Failed',
  skipped: 'Skipped',
};

export const ROBUSTNESS_ROLE_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  cost: 'Cost scenario',
  slice: 'Period slice',
  parameter: 'Parameter change',
  selection: 'Parameter selection (training window)',
  measurement: 'Measurement (test window)',
};

/** Why a simulated order could not be placed — the backend's own rejection codes. */
export const REJECTION_LABELS: Record<string, string> = {
  already_in_position: 'This session already holds a position',
  insufficient_cash: 'Not enough virtual cash',
  no_position_to_sell: 'No position to sell',
  quantity_exceeds_position: 'Quantity exceeds the position held',
  awaiting_execution_window: 'Waiting for an execution window',
  no_market_clock: 'No simulated account bound',
  no_quote_on_window: 'No executable quote in this window',
  portfolio_paused: 'Portfolio is paused',
  instrument_not_allowed: 'Instrument not on the risk allowlist',
  daily_entry_limit: 'Daily entry limit reached',
  position_limit: 'Single-position limit exceeded',
  risk_price_unavailable: 'No decision price — risk could not be computed',
  unsupported_side: 'Order side is not supported',
  portfolio_retired: 'Portfolio is retired',
  portfolio_completed: 'Portfolio is completed',
  decision_detail_incomplete: 'The simulated step had no complete price, so no trade was booked',
};

/* ── market data ────────────────────────────────────────────────────── */

export const DATA_NATURE_LABELS: Record<string, string> = {
  synthetic: 'Synthetic',
  real: 'Real market data',
  live: 'Live',
  delayed: 'Delayed',
  historical: 'Historical',
  replay: 'Historical replay',
  unknown: 'Unknown',
};

export const MARKET_STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  closed: 'Closed',
  pre_market: 'Pre-market',
  after_hours: 'After hours',
  stale: 'Stale',
  unknown: 'Unknown',
};

export const ADJUSTMENT_LABELS: Record<string, string> = {
  adjusted: 'Adjusted',
  unadjusted: 'Unadjusted',
  not_applicable: 'Not applicable',
  unknown: 'Unknown',
};

/** The price-view modes the backend reports for a series. */
export const PRICE_MODE_LABELS: Record<string, string> = {
  synthetic_fixture: 'Synthetic',
  historical_replay: 'Historical replay',
  latest_bar: 'Latest close',
  delayed: 'Delayed',
  live_realtime: 'Live',
  unknown: 'Unknown',
};

/* ── runtime modes (operational status) ─────────────────────────────── */

export const DATA_MODE_LABELS: Record<string, string> = {
  demo: 'Demo',
  synthetic: 'Synthetic',
  live: 'Live',
  delayed: 'Delayed',
  replay: 'Historical replay',
  historical: 'Historical',
  unknown: 'Unknown',
};

export const BROKER_MODE_LABELS: Record<string, string> = {
  'paper-only': 'Paper only',
  paper: 'Paper only',
  disconnected: 'Disconnected',
  connected: 'Connected',
  none: 'Not configured',
  unknown: 'Unknown',
};

export const AI_MODE_LABELS: Record<string, string> = {
  mock: 'Simulated',
  live: 'Live',
  offline: 'Offline',
  disabled: 'Disabled',
  unknown: 'Unknown',
};
