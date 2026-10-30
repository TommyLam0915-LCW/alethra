/**
 * Workspace Shell — Layout, Symbol Sync, Watchlist, Order Flow Tests
 *
 * Tests the shared workspace (TradingView/Futu-style shell) using
 * Playwright network interception. Verifies:
 * - Core shell renders (chart canvas, nature badge, status chips, tabs)
 * - Symbol search synchronises chart + details
 * - Layout persistence (symbol/tab/range/panel size) across reload
 * - Watchlist add/remove
 * - Order preview panel intent lifecycle (create → authorize → submit)
 * - Mobile responsive navigation
 */

import { readFileSync } from 'fs';

import { test, expect, type Page, type Route } from '@playwright/test';

import { DEFAULT_LAYOUT } from '../src/components/workspace/types';

const FRONTEND_URL = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3001';
const MOCK_TOKEN = 'mock-test-token';

const OP_STATUS_RESPONSE = {
  feature_switches: {},
  mode: 'test',
  data_mode: 'demo',
  broker_mode: 'paper-only',
  ai_mode: 'mock',
};

function makeBars(count: number): { date: string; open: number; high: number; low: number; close: number; volume: number }[] {
  const bars = [];
  const today = new Date();
  for (let i = count - 1; i >= 0; i--) {
    const d = new Date(today.getTime() - i * 24 * 60 * 60 * 1000);
    const close = 180 + ((i * 7) % 40);
    bars.push({
      date: d.toISOString().slice(0, 10),
      open: close - 1,
      high: close + 2,
      low: close - 3,
      close,
      volume: 1_000_000 + ((i * 137) % 500_000),
    });
  }
  return bars;
}

const BARS = makeBars(90);

const INSTRUMENT_AAPL = {
  instrument_id: 'US:AAPL',
  symbol: 'AAPL',
  name: 'Apple Inc.',
  market: 'US',
  exchange: 'NASDAQ',
  asset_type: 'STOCK',
  currency: 'USD',
  timezone: 'America/New_York',
  lot_size: 1,
  lot_size_verified: true,
  latest_bar: {
    date: BARS[BARS.length - 1].date,
    open: BARS[BARS.length - 1].open,
    high: BARS[BARS.length - 1].high,
    low: BARS[BARS.length - 1].low,
    close: BARS[BARS.length - 1].close,
    volume: BARS[BARS.length - 1].volume,
  },
  data_quality: {
    data_nature: 'synthetic',
    adjustment: 'not_applicable',
    timezone: 'America/New_York',
    note_zh: '模擬數據',
    latest_bar_date: BARS[BARS.length - 1].date,
    bar_age_days: 0,
    is_stale: false,
    stale_threshold_days: 7,
  },
  data_source: 'Demo Provider',
  feed_mode: 'demo',
  data_window: {
    timeframe: '1d',
    available_start: '2023-01-03',
    available_end: '2025-12-31',
    availability_source: 'provider_declared',
  },
};

const INSTRUMENT_0700 = {
  ...INSTRUMENT_AAPL,
  instrument_id: 'HK:0700',
  symbol: '0700',
  name: 'Tencent Holdings',
  market: 'HK',
  exchange: 'HKEX',
  currency: 'HKD',
  timezone: 'Asia/Hong_Kong',
  lot_size: 100,
  // A provider that declares nothing must render as UNKNOWN, not as AAPL's window.
  data_window: null,
  latest_bar: {
    date: BARS[BARS.length - 1].date,
    open: 380,
    high: 392,
    low: 375,
    close: 388,
    volume: 12_000_000,
  },
};

const BARS_RESPONSE_AAPL = {
  instrument_id: 'US:AAPL',
  symbol: 'AAPL',
  name: 'Apple Inc.',
  currency: 'USD',
  provider: 'Demo Provider',
  feed_mode: 'demo',
  start_date: '2000-01-01',
  end_date: '2099-01-01',
  bar_count: BARS.length,
  data_quality: {
    data_nature: 'synthetic',
    adjustment: 'not_applicable',
    timezone: 'America/New_York',
    latest_bar_date: BARS[BARS.length - 1].date,
    is_stale: false,
  },
  bars: BARS,
  disclaimer: 'Data may be synthetic or demo-only.',
  disclaimer_zh: '數據可能為模擬或演示數據。',
};

const BARS_RESPONSE_0700 = {
  ...BARS_RESPONSE_AAPL,
  instrument_id: 'HK:0700',
  symbol: '0700',
  name: 'Tencent Holdings',
  currency: 'HKD',
};

const WATCHLIST_RESPONSE = {
  watchlists: [
    {
      id: 'wl-1',
      name: 'Default',
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
      items: [
        { symbol: 'US:AAPL', position: 0, added_at: '2026-10-01T00:00:00Z' },
        { symbol: 'HK:0700', position: 1, added_at: '2026-10-02T00:00:00Z' },
      ],
    },
  ],
};

const AI_STATUS_RESPONSE = {
  provider: 'mock',
  model: 'mock-model',
  mode: 'mock',
  api_key_configured: false,
  base_url: null,
  timeout_seconds: 30,
  max_output_tokens: 1024,
  advisory_notice: 'AI 回覆僅供研究參考，不構成交易授權',
};

const AI_CONVERSATION = {
  id: '11111111-1111-1111-1111-111111111111',
  title: '研究：AAPL 動能',
  context: { research_draft_ids: [], memory_ids: [], instrument_symbols: ['US:AAPL'] },
  created_at: '2026-10-17T00:00:00Z',
  updated_at: '2026-10-17T00:00:00Z',
};

const AI_ASSISTANT_REPLY = {
  id: '22222222-2222-2222-2222-222222222222',
  role: 'assistant',
  content: '（模擬 AI 助手 — 確定性回覆）就 [symbol:US:AAPL] 的動能假說而言，建議先回顧近期回撤幅度。',
  status: 'completed',
  error_detail: null,
  provider: 'mock',
  model: 'mock-model',
  prompt_tokens: 120,
  completion_tokens: 80,
  quota_used: 1,
  created_at: '2026-10-17T00:00:00Z',
};

/* ── ⑧ Evidence dock fixtures ──────────────────────────────────────────────
 * Response bodies mirror the real routes in backend/app/api/paper_portfolio.py:
 * PaperPortfolioResponse / PaperPositionResponse / PaperOrderResponse /
 * DecisionRecordResponse, and the `journal?format=json` envelope with its 27
 * JOURNAL_COLUMNS. Timestamps are naive ISO exactly as FastAPI serialises them,
 * and the unfilled order row omits the fill columns the way `_journal_rows`
 * does — an absent key, not a zero.
 */
const JOURNAL_COLUMNS_FIXTURE = [
  'record_type',
  'record_classification',
  'portfolio_id',
  'currency',
  'order_id',
  'fill_id',
  'decision_id',
  'asset_id',
  'side',
  'order_type',
  'order_status',
  'actor_type',
  'decision_type',
  'outcome_status',
  'data_cutoff_at',
  'execution_date',
  'bar_timestamp',
  'order_created_at_utc',
  'observed_price',
  'execution_price_before_costs',
  'slippage_amount',
  'commission_amount',
  'quantity',
  'gross_amount',
  'net_cash_change',
  'simulated_fill_reason',
  'rationale',
];

// A subset of the backend's JOURNAL_COLUMN_SEMANTICS, verbatim where copied; the
// full map is exercised against the real endpoint in the tier-B spec.
const JOURNAL_SEMANTICS_FIXTURE: Record<string, string> = {
  record_type:
    "'fill' = an executed fill; 'order' = an order that never filled (pending/cancelled/rejected).",
  bar_timestamp:
    'ISO-8601 naive exchange-local instant of the intraday bar the fill executed on (e.g. 2024-03-04T09:50:00). Empty for daily fills, which carry no intraday bar time.',
  order_created_at_utc: 'UTC timestamp, ISO-8601, naive (no offset suffix): the instant the order was created.',
  net_cash_change:
    'Decimal in `currency`, signed cash movement (negative on BUY, positive on SELL). Empty = null, never 0.',
  commission_amount: 'Decimal in `currency`. Empty = null.',
  simulated_fill_reason: 'Controlled vocabulary (e.g. rule_entry, rule_stop). Empty = null.',
};

const EVIDENCE_PORTFOLIO = {
  id: 'pf-1',
  owner_id: 'owner-1',
  simulation_session_id: null as string | null,
  currency: 'USD',
  initial_virtual_cash: 100000,
  current_virtual_cash: 95461.5,
  status: 'active',
  created_at: '2026-01-05T15:00:00',
  closed_at: null,
  // a returned 0 is a real zero; a returned null is not, and must not print 0.00
  total_realized_pnl: 0,
  total_unrealized_pnl: null as number | null,
  valuation: {
    mode: 'latest_bar',
    mode_label_zh: '最新收盤價',
    provider: 'Demo Provider',
    data_as_of: '2026-01-07',
    unpriced_assets: ['US:AAPL'],
    note: '部分資產沒有可用估價。',
  },
};

const EVIDENCE_POSITIONS = [
  {
    id: 'pos-aapl',
    portfolio_id: 'pf-1',
    asset_id: 'US:AAPL',
    quantity: 25,
    average_entry_price: 181.5,
    opened_at: '2026-01-05T15:31:00',
    closed_at: null,
    realized_pnl: null,
    unrealized_pnl: null,
    status: 'open',
  },
  {
    id: 'pos-0700',
    portfolio_id: 'pf-1',
    asset_id: 'HK:0700',
    quantity: 100,
    average_entry_price: 375.2,
    opened_at: '2026-01-07T02:10:00',
    closed_at: null,
    realized_pnl: null,
    unrealized_pnl: 1280,
    status: 'open',
  },
];

const EVIDENCE_ORDERS = [
  {
    id: 'ord-aapl',
    portfolio_id: 'pf-1',
    decision_id: 'dec-entry-1',
    asset_id: 'US:AAPL',
    side: 'BUY',
    quantity: 25,
    order_type: 'market',
    created_at: '2026-01-05T15:30:00',
    status: 'filled',
    model_or_strategy_version_id: null,
    data_cutoff_at: '2026-01-05',
    rationale: '動能確認後進場',
    risk_check_status: 'passed',
    actor_type: 'human',
    idempotency_key: 'ws-pf-1-1',
  },
  {
    id: 'ord-0700',
    portfolio_id: 'pf-1',
    decision_id: null,
    asset_id: 'HK:0700',
    side: 'SELL',
    quantity: 100,
    order_type: 'market',
    created_at: '2026-01-06T01:55:00',
    status: 'filled',
    model_or_strategy_version_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    data_cutoff_at: '2026-01-06',
    rationale: null,
    risk_check_status: 'passed',
    actor_type: 'system',
    idempotency_key: null,
  },
  {
    id: 'ord-pending',
    portfolio_id: 'pf-1',
    decision_id: null,
    asset_id: 'US:AAPL',
    side: 'SELL',
    quantity: 10,
    order_type: 'limit',
    created_at: '2026-01-08T15:25:00',
    status: 'pending',
    model_or_strategy_version_id: null,
    data_cutoff_at: '2026-01-08',
    rationale: null,
    risk_check_status: 'passed',
    actor_type: 'api_key',
    idempotency_key: null,
  },
];

const EVIDENCE_JOURNAL = [
  {
    record_type: 'fill',
    record_classification: 'closed',
    portfolio_id: 'pf-1',
    currency: 'USD',
    order_id: 'ord-aapl',
    fill_id: 'fill-aapl',
    decision_id: 'dec-entry-1',
    asset_id: 'US:AAPL',
    side: 'BUY',
    order_type: 'market',
    order_status: 'filled',
    actor_type: 'human',
    decision_type: 'simulated_entry',
    outcome_status: 'pending',
    data_cutoff_at: '2026-01-05',
    execution_date: '2026-01-06',
    bar_timestamp: null,
    order_created_at_utc: '2026-01-05T15:30:00',
    observed_price: 182,
    execution_price_before_costs: 181.5,
    slippage_amount: 0.45,
    commission_amount: 1,
    quantity: 25,
    gross_amount: 4537.5,
    net_cash_change: -4538.5,
    simulated_fill_reason: 'rule_entry',
    rationale: '動能確認後進場',
  },
  {
    record_type: 'fill',
    record_classification: 'closed',
    portfolio_id: 'pf-1',
    currency: 'USD',
    order_id: 'ord-0700',
    fill_id: 'fill-0700',
    decision_id: null,
    asset_id: 'HK:0700',
    side: 'SELL',
    order_type: 'market',
    order_status: 'filled',
    actor_type: 'system',
    decision_type: null,
    outcome_status: null,
    data_cutoff_at: '2026-01-06',
    execution_date: '2026-01-07',
    bar_timestamp: '2026-01-07T14:35:00',
    order_created_at_utc: '2026-01-06T01:55:00',
    observed_price: 388,
    execution_price_before_costs: 390.5,
    slippage_amount: 0,
    commission_amount: 3.2,
    quantity: 100,
    gross_amount: 39050,
    net_cash_change: 39046.8,
    simulated_fill_reason: 'rule_stop',
    rationale: null,
  },
  {
    record_type: 'order',
    record_classification: 'open',
    portfolio_id: 'pf-1',
    currency: 'USD',
    order_id: 'ord-pending',
    fill_id: null,
    decision_id: null,
    asset_id: 'US:AAPL',
    side: 'SELL',
    order_type: 'limit',
    order_status: 'pending',
    actor_type: 'api_key',
    decision_type: null,
    outcome_status: null,
    data_cutoff_at: '2026-01-08',
    order_created_at_utc: '2026-01-08T15:25:00',
    rationale: null,
  },
];

const EVIDENCE_DECISIONS = [
  {
    id: 'dec-entry-1',
    owner_id: 'owner-1',
    simulation_session_id: 'sess-1',
    actor_type: 'human',
    decision_type: 'simulated_entry',
    decision_time: '2026-01-05T15:30:00',
    data_cutoff_at: '2026-01-05',
    linked_experiment_version_id: null,
    linked_model_research_record_id: null,
    rationale: '動能確認後進場',
    expected_outcome: '30 天內上漲 5%',
    expected_timeframe_days: 30,
    self_reported_confidence: 'medium',
    falsification_condition: null,
    risk_flags: null,
    outcome_status: 'pending',
  },
  {
    id: 'dec-wait-1',
    owner_id: 'owner-1',
    simulation_session_id: 'sess-1',
    actor_type: 'system',
    decision_type: 'no_action',
    decision_time: '2026-01-04T15:00:00',
    data_cutoff_at: '2026-01-04',
    linked_experiment_version_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    linked_model_research_record_id: null,
    rationale: null,
    expected_outcome: null,
    expected_timeframe_days: null,
    self_reported_confidence: 'not_provided',
    falsification_condition: null,
    risk_flags: null,
    outcome_status: 'not_applicable',
  },
];

function journalCsvBody(rows: Record<string, unknown>[]): string {
  const cell = (value: unknown) => (value === null || value === undefined ? '' : String(value));
  const lines = [JOURNAL_COLUMNS_FIXTURE.join(',')];
  for (const row of rows) lines.push(JOURNAL_COLUMNS_FIXTURE.map((col) => cell(row[col])).join(','));
  return lines.join('\r\n') + '\r\n';
}

/**
 * GET-only stand-in for the portfolio evidence endpoints. Non-GET requests fall
 * through untouched so the order-creation flows keep exercising their own routes.
 */
function mockPaperPortfolioEvidence(page: Page) {
  page.route('**/api/v1/paper-portfolio/**', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const url = new URL(route.request().url());
    const segments = url.pathname.split('/').filter(Boolean);
    const portfolioId = segments[3] ?? 'pf-1';
    const child = segments[4] ?? '';

    if (!child) return json(route, { ...EVIDENCE_PORTFOLIO, id: portfolioId });
    if (child === 'positions') {
      return json(route, EVIDENCE_POSITIONS.map((p) => ({ ...p, portfolio_id: portfolioId })));
    }
    if (child === 'orders') {
      if (segments[5] === 'fills') return json(route, []);
      return json(route, EVIDENCE_ORDERS.map((o) => ({ ...o, portfolio_id: portfolioId })));
    }
    if (child === 'decisions') return json(route, EVIDENCE_DECISIONS);
    if (child === 'journal') {
      if (url.searchParams.get('format') === 'csv') {
        return route.fulfill({
          status: 200,
          contentType: 'text/csv; charset=utf-8',
          body: journalCsvBody(EVIDENCE_JOURNAL),
        });
      }
      return json(route, {
        portfolio_id: portfolioId,
        currency: 'USD',
        columns: JOURNAL_COLUMNS_FIXTURE,
        column_semantics: JOURNAL_SEMANTICS_FIXTURE,
        row_count: EVIDENCE_JOURNAL.length,
        records: EVIDENCE_JOURNAL.map((r) => ({ ...r, portfolio_id: portfolioId })),
      });
    }
    return route.fallback();
  });
}

function json(route: Route, body: unknown, status = 200) {
  route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function setupAuth(page: Page) {
  await page.goto(FRONTEND_URL);
  await page.evaluate((token) => {
    localStorage.setItem('token', token);
  }, MOCK_TOKEN);
}

interface WorkspaceMocks {
  savedLayout: unknown;
  lastIntent: Record<string, unknown> | null;
  /** Number of PUT /layouts/workspace calls the app issued by itself. */
  layoutWrites?: number;
  /**
   * GET payload the app cannot overwrite. The mock stores every PUT in
   * `savedLayout`, so a test that replays several stored payloads in a row would
   * otherwise have the next one replaced by the previous phase's write. (A mount
   * writes nothing any more — see the persistence spec — but a phase that drags a
   * handle and then reloads still needs the payload it chose to stay pinned.)
   */
  layoutOverride?: unknown;
}

function mockWorkspaceApis(page: Page, state: WorkspaceMocks) {
  page.route('**/api/v1/layouts/workspace', (route) => {
    if (route.request().method() === 'PUT') {
      const body = JSON.parse(route.request().postData() || '{}');
      state.savedLayout = body.layout;
      state.layoutWrites = (state.layoutWrites ?? 0) + 1;
      json(route, { layout: body.layout, updated_at: '2026-10-17T00:00:00Z' });
    } else {
      const served = state.layoutOverride !== undefined ? state.layoutOverride : state.savedLayout;
      json(route, { layout: served ?? null, updated_at: null });
    }
  });

  // The picker's browse list is fetched on mount, before any query exists.
  page.route('**/api/v1/instruments?limit=**', (route) => {
    json(route, {
      instruments: [INSTRUMENT_AAPL, INSTRUMENT_0700],
      total: 2,
      data_available: true,
      provider: 'Demo Provider',
    });
  });

  page.route('**/api/v1/instruments?q=**', (route) => {
    json(route, {
      instruments: [INSTRUMENT_AAPL, INSTRUMENT_0700],
      total: 2,
      data_available: true,
      provider: 'Demo Provider',
    });
  });

  page.route(`**/api/v1/instruments/US:AAPL/bars**`, (route) => json(route, BARS_RESPONSE_AAPL));
  page.route(`**/api/v1/instruments/HK:0700/bars**`, (route) => json(route, BARS_RESPONSE_0700));
  page.route(`**/api/v1/instruments/US:AAPL`, (route) => json(route, INSTRUMENT_AAPL));
  page.route(`**/api/v1/instruments/HK:0700`, (route) => json(route, INSTRUMENT_0700));

  page.route('**/api/v1/watchlists', (route) => json(route, WATCHLIST_RESPONSE));
  page.route('**/api/v1/watchlists/**', (route) => {
    if (route.request().method() === 'DELETE') {
      json(route, WATCHLIST_RESPONSE);
    } else {
      json(route, WATCHLIST_RESPONSE);
    }
  });

  page.route('**/api/v1/operational-status', (route) => json(route, OP_STATUS_RESPONSE));
  page.route('**/api/v1/pilot/usage-events/me', (route) => json(route, []));
  page.route('**/api/v1/experiments/', (route) => json(route, []));
  page.route('**/api/v1/ml/models', (route) => json(route, []));
  page.route('**/api/v1/paper-portfolio', (route) =>
    json(route, [
      {
        id: 'pf-1',
        currency: 'USD',
        current_virtual_cash: 100000,
        initial_virtual_cash: 100000,
        status: 'active',
      },
    ])
  );
  // The dock reads /paper-portfolio/{id}[/positions|/orders|/journal|/decisions];
  // unmocked those 401 and apiFetch redirects the whole page to /login.
  mockPaperPortfolioEvidence(page);
  page.route('**/api/v1/execution/intents**', (route) => {
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      const path = route.request().url();
      if (path.includes('/authorize')) {
        json(route, { ...state.lastIntent, status: 'authorized' });
      } else if (path.includes('/submit')) {
        json(route, { ...state.lastIntent, status: 'submitted' });
      } else {
        state.lastIntent = {
          intent_id: 'intent-abc-123',
          portfolio_id: body.portfolio_id,
          symbol: body.symbol,
          side: body.side,
          quantity: body.quantity,
          order_type: body.order_type || 'MARKET',
          limit_price: body.limit_price ?? null,
          status: 'pending',
          authorization_token: 'auth-token-xyz',
          created_at: '2026-10-17T00:00:00Z',
          updated_at: '2026-10-17T00:00:00Z',
        };
        json(route, state.lastIntent, 201);
      }
    } else {
      json(route, []);
    }
  });

  page.route('**/api/v1/ai/status', (route) => json(route, AI_STATUS_RESPONSE));
  page.route('**/api/v1/ai/test-connection', (route) =>
    json(route, { ok: true, detail: '模擬供應商連線正常', latency_ms: 1, kind: null, provider: 'mock' })
  );
  page.route('**/api/v1/ai/conversations', (route) => {
    if (route.request().method() === 'POST') {
      json(route, AI_CONVERSATION, 201);
    } else {
      json(route, [AI_CONVERSATION]);
    }
  });
  page.route('**/api/v1/ai/conversations/**', (route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (url.endsWith('/messages') && method === 'POST') {
      json(route, AI_ASSISTANT_REPLY);
    } else if (url.endsWith('/drafts') && method === 'POST') {
      json(
        route,
        {
          id: '33333333-3333-3333-3333-333333333333',
          title: 'AI 提議草稿',
          status: 'pending_review',
          source_conversation_id: AI_CONVERSATION.id,
          review_notice: 'AI 產生的草稿需要人工審核',
        },
        201
      );
    } else if (method === 'DELETE') {
      json(route, {});
    } else {
      json(route, { ...AI_CONVERSATION, messages: [] });
    }
  });
}

test.describe('Workspace shell', () => {
  test('renders chart, nature badge, status chips, tabs and watchlist', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('price-chart')).toBeVisible();
    await expect(page.getByTestId('chart-legend')).toContainText('AAPL');
    await expect(page.getByTestId('chart-nature-badge')).toHaveText('模擬數據');
    await expect(page.getByTestId('status-chips')).toContainText('Data: Demo data');
    await expect(page.getByTestId('status-chips')).toContainText('Broker: Paper only');
    await expect(page.getByTestId('watchlist-panel')).toContainText('AAPL');
    await expect(page.getByTestId('details-panel')).toContainText('Apple Inc.');
    await expect(page.getByTestId('bottom-tabs')).toBeVisible();

    // AI assistant panel lives in the right column above details/order
    await expect(page.getByTestId('ai-panel')).toBeVisible();
    await expect(page.getByTestId('ai-mode-badge')).toHaveText('AI 模擬');
    await expect(page.locator('.ws-ai-advisory')).toContainText('不構成交易授權');

    // candlestick canvas actually rendered by lightweight-charts
    const canvasCount = await page.locator('[data-testid="price-chart"] canvas').count();
    expect(canvasCount).toBeGreaterThan(0);
  });

  test('symbol search switches chart, legend and details panel', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('chart-legend')).toContainText('AAPL');

    await page.getByLabel('搜尋代號').fill('0700');
    await page.locator('.ws-search-item', { hasText: '0700' }).click();

    await expect(page.getByTestId('chart-legend')).toContainText('0700', { timeout: 10_000 });
    await expect(page.getByTestId('chart-legend')).toContainText('Tencent');
    await expect(page.getByTestId('details-panel')).toContainText('HKEX');
  });

  test('symbol picker offers a browsable, coverage-labelled list before typing', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await page.getByLabel('搜尋代號').click();
    await expect(page.getByTestId('symbol-search-context')).toContainText('可選符號');

    const rows = page.getByTestId('symbol-search-item');
    await expect(rows).toHaveCount(2);
    // Each row has to say which period it can actually answer for. A provider that
    // declares nothing shows UNKNOWN rather than borrowing another symbol's window.
    await expect(rows.filter({ hasText: 'AAPL' })).toContainText('2023-01-03 至 2025-12-31');
    await expect(rows.filter({ hasText: '0700' })).toContainText('數據期間未知（UNKNOWN）');
  });

  test('symbol picker drops the previous query rows while the next search is in flight', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    let releaseNext: () => void = () => {};
    const nextSearch = new Promise<void>((resolve) => {
      releaseNext = resolve;
    });
    // Registered after mockWorkspaceApis, so this route wins for the test.
    await page.route('**/api/v1/instruments?q=**', (route) => {
      const q = new URL(route.request().url()).searchParams.get('q') ?? '';
      const body = (rows: unknown[]) => ({
        instruments: rows,
        total: rows.length,
        data_available: true,
        provider: 'Demo Provider',
      });
      if (q === '0700') return nextSearch.then(() => json(route, body([INSTRUMENT_0700])));
      return json(route, body([INSTRUMENT_AAPL]));
    });

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const search = page.getByLabel('搜尋代號');
    const rows = page.getByTestId('symbol-search-item');
    await search.click();
    await search.fill('aapl');
    await expect(rows.filter({ hasText: 'AAPL' })).toHaveCount(1);

    // The 0700 response is held, so this is the window in which rows left over from
    // 'aapl' would sit under the 「搜尋「0700」」 heading and be clicked as a match.
    await search.fill('0700');
    await expect(page.getByText('搜尋中…')).toBeVisible();
    await expect(rows).toHaveCount(0);

    releaseNext();
    await expect(rows.filter({ hasText: '0700' })).toHaveCount(1);
    await expect(rows.filter({ hasText: 'AAPL' })).toHaveCount(0);
    await rows.filter({ hasText: '0700' }).click();

    await expect(page.getByTestId('chart-legend')).toContainText('0700', { timeout: 10_000 });
  });

  test('watchlist: an empty account explains 「＋」, and one click creates the list and adds the symbol', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    const lists: { id: string; name: string; items: string[] }[] = [];
    const added: string[] = [];
    const body = () => ({
      watchlists: lists.map((wl) => ({
        id: wl.id,
        name: wl.name,
        created_at: '2026-10-01T00:00:00Z',
        updated_at: '2026-10-01T00:00:00Z',
        items: wl.items.map((symbol, position) => ({ symbol, position, added_at: '2026-10-01T00:00:00Z' })),
      })),
    });
    // Registered after mockWorkspaceApis, so these win for the test.
    await page.route('**/api/v1/watchlists', (route) => {
      if (route.request().method() === 'POST') {
        lists.push({ id: 'wl-1', name: 'Default', items: [] });
        return json(route, { watchlist: body().watchlists[0] }, 201);
      }
      return json(route, body());
    });
    await page.route('**/api/v1/watchlists/**/items**', (route) => {
      if (route.request().method() === 'DELETE') {
        const symbol = decodeURIComponent(route.request().url().split('/items/')[1]);
        for (const wl of lists) wl.items = wl.items.filter((s) => s !== symbol);
        return json(route, { watchlist: body().watchlists[0] });
      }
      const { symbol } = JSON.parse(route.request().postData() ?? '{}');
      added.push(`${route.request().url().split('/api/v1')[1]} :: ${symbol}`);
      lists.find((wl) => route.request().url().includes(wl.id))?.items.push(symbol);
      return json(route, { watchlist: body().watchlists[0] });
    });

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('watchlist-panel');
    await expect(panel).toContainText('還沒有觀察列表');
    await expect(panel.getByTestId('watchlist-group-empty')).toHaveCount(0);

    await page.getByTestId('watchlist-add-current').click();

    await expect(panel.locator('.ws-symbol-row')).toHaveCount(1);
    await expect(panel.locator('.ws-symbol-row')).toContainText('AAPL');
    // One click, one create, one add — into the list that was just created.
    expect(added).toEqual(['/watchlists/wl-1/items :: US:AAPL']);

    await panel.locator('button[aria-label="從 Default 移除 US:AAPL"]').click();

    await expect(panel.locator('.ws-symbol-row')).toHaveCount(0);
    // An emptied group must not read as a dead end.
    await expect(panel.getByTestId('watchlist-group-empty')).toContainText('或以頂部搜尋框選取其他符號');
  });

  test('watchlist symbol click switches chart', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('chart-legend')).toContainText('AAPL');

    await page
      .getByTestId('watchlist-panel')
      .getByRole('button', { name: '選取標的 HK:0700' })
      .click();
    await expect(page.getByTestId('chart-legend')).toContainText('0700', { timeout: 10_000 });
  });

  test('layout (symbol, tab, range, panel size) persists across reload', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('chart-legend')).toContainText('AAPL');

    // change range, tab and symbol
    await page.locator('[data-testid="chart-range-buttons"]').getByText('6M').click();
    await page.getByTestId('tab-orders-fills').click();
    await page
      .getByTestId('watchlist-panel')
      .getByRole('button', { name: '選取標的 HK:0700' })
      .click();
    await expect(page.getByTestId('chart-legend')).toContainText('0700', { timeout: 10_000 });

    // resize left panel via drag handle
    const handle = page.locator('[data-panel-resize-handle-id]').first();
    const box = await handle.boundingBox();
    if (box) {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + 80, box.y + box.height / 2, { steps: 5 });
      await page.mouse.up();
    }

    // wait for debounced PUT
    await page.waitForTimeout(1500);
    expect(mocks.savedLayout).toMatchObject({
      symbol: 'HK:0700',
      bottomTab: 'orders-fills',
      range: '6M',
    });

    // reload — GET replays the saved layout
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('chart-legend')).toContainText('0700');
    await expect(page.getByTestId('tab-orders-fills')).toHaveClass(/ws-tab-active/);
    if (mocks.savedLayout && typeof mocks.savedLayout === 'object') {
      const panels = (mocks.savedLayout as { panels?: { left?: { size?: number } } }).panels;
      expect(panels?.left?.size).toBeGreaterThan(16);
    }
  });

  test('bottom tabs switch and render data', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    // give research tab some data
    await page.route('**/api/v1/experiments/', (route) =>
      json(route, [
        {
          id: 'exp-1',
          title: '動能策略驗證',
          status: 'exploring',
          created_at: '2026-10-10T00:00:00Z',
          updated_at: '2026-10-15T00:00:00Z',
        },
      ])
    );

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    // the dock opens on 持倉; the registry tabs are reached by clicking them
    await expect(page.getByTestId('tab-positions')).toHaveClass(/ws-tab-active/);
    await page.getByTestId('tab-research').click();
    await expect(page.getByTestId('evidence-tabpanel-research')).toContainText('動能策略驗證');

    await page.getByTestId('tab-models').click();
    await expect(page.getByTestId('evidence-tabpanel-models')).toContainText('沒有模型');
    await page.getByTestId('tab-orders-fills').click();
    await expect(page.getByTestId('evidence-tabpanel-orders-fills')).toContainText('沒有執行意向');
    await page.getByTestId('tab-log').click();
    await expect(page.getByTestId('evidence-tabpanel-log')).toContainText('沒有系統活動紀錄');
  });

  test('order preview panel: create → authorize → submit intent flow', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    // quantity derived from lot size (AAPL lot 1)
    await page.getByTestId('order-lots').fill('10');
    await expect(page.getByTestId('order-quantity')).toHaveText('10 股');

    await page.getByTestId('order-create-intent').click();
    await expect(page.getByTestId('order-intent-id')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('order-intent-status')).toContainText('pending');

    await page.getByRole('button', { name: '確認授權' }).click();
    await expect(page.getByTestId('order-intent-status')).toContainText('authorized', { timeout: 10_000 });

    await page.getByTestId('order-submit').click();
    await expect(page.getByTestId('order-intent-status')).toContainText('submitted', { timeout: 10_000 });
  });

  test('mobile: hamburger opens watchlist drawer', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('mobile-watchlist-toggle')).toBeVisible();
    await expect(page.getByTestId('watchlist-panel')).toBeHidden();
    await page.getByTestId('mobile-watchlist-toggle').click();
    await expect(page.getByTestId('watchlist-panel')).toBeVisible();
    await expect(page.getByTestId('price-chart')).toBeVisible();
    await expect(page.getByTestId('ai-panel')).toBeVisible();
  });

  test('watchlist width: 224px floor at 1280, collapses to drawer below 1280', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('watchlist-panel');
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    // exact design floor, no slack: the component adds the 8px of resize
    // handles back before converting the floor into a panel percentage
    expect(box?.width).toBeGreaterThanOrEqual(224);

    const pick = panel.getByRole('button', { name: '選取標的 US:AAPL' });
    await expect(pick).toBeVisible();
    // the instrument code must not be ellipsised away at the narrowest three-column width
    expect(
      await pick.locator('.ws-symbol-label').evaluate((el) => el.scrollWidth - el.clientWidth)
    ).toBeLessThanOrEqual(1);

    await page.setViewportSize({ width: 1024, height: 768 });
    await expect(panel).toBeHidden();
    await expect(page.getByTestId('mobile-watchlist-toggle')).toBeVisible();
    await page.getByTestId('mobile-watchlist-toggle').click();
    await expect(panel).toBeVisible();
    await expect(panel.getByRole('button', { name: '選取標的 HK:0700' })).toBeVisible();
    await expect(page.getByTestId('price-chart')).toBeVisible();
  });

  test('mobile: bottom navigation replaces the top nav below 768px', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByRole('navigation', { name: 'Primary', exact: true })).toBeHidden();

    const bar = page.getByRole('navigation', { name: 'Primary (mobile)' });
    await expect(bar).toBeVisible();
    await expect(bar.getByRole('link', { name: 'Workspace' })).toHaveAttribute('aria-current', 'page');

    // Short labels, full destinations: no ellipsis, and URLs stay unchanged.
    const expected: Array<[string, string]> = [
      ['Markets', '/market-data'],
      ['Workspace', '/workspace'],
      ['Research', '/dashboard'],
      ['Models', '/models'],
      ['Journal', '/paper-portfolio?view=journal'],
    ];
    for (const [label, href] of expected) {
      const item = bar.getByRole('link', { name: label });
      await expect(item).toBeVisible();
      await expect(item).toHaveAttribute('href', href);
      expect(await item.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
    }

    // The stacked phone workspace must scroll inside the shell instead of
    // clipping everything below the chart. The gesture has to start outside the
    // chart canvas, which consumes wheel events to zoom and pan, so it is
    // anchored on the first panel below the chart and read back from the
    // mobile scroller itself rather than from a fixed pixel.
    const scroller = page.getByTestId('workspace-mobile');
    const gestureAnchor = scroller.getByTestId('ai-panel').locator('.ws-ai-headrow');
    await expect(gestureAnchor).toBeInViewport();
    const anchorBox = await gestureAnchor.boundingBox();
    expect(anchorBox).not.toBeNull();

    await page.mouse.move(anchorBox!.x + anchorBox!.width / 2, anchorBox!.y + anchorBox!.height / 2);
    await page.mouse.wheel(0, 4000);
    await expect(page.getByTestId('bottom-tabs')).toBeInViewport();
    expect(await scroller.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

    // Landscape phones get the top nav back instead of a shrunken bottom bar.
    await page.setViewportSize({ width: 844, height: 390 });
    await expect(bar).toBeHidden();
    await expect(page.getByRole('navigation', { name: 'Primary', exact: true })).toBeVisible();
  });

  test('mobile: watchlist drawer traps Tab and returns focus to its toggle', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const toggle = page.getByTestId('mobile-watchlist-toggle');
    await toggle.click();

    const drawer = page.getByRole('dialog', { name: '觀察列表' });
    await expect(drawer).toBeVisible();
    await expect(drawer).toBeFocused();

    // Tab past every control the drawer holds: the extra press must wrap back
    // inside instead of escaping to the workspace behind the drawer.
    const controls = drawer.locator(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    const count = await controls.count();
    expect(count).toBeGreaterThanOrEqual(3);
    for (let i = 0; i < count + 1; i += 1) {
      await page.keyboard.press('Tab');
      expect(
        await drawer.evaluate((el) => el.contains(document.activeElement)),
        `tab press ${i + 1} of ${count + 1}`
      ).toBe(true);
    }
    await expect(controls.nth(0)).toBeFocused();

    await page.getByTestId('watchlist-drawer-close').click();
    await expect(drawer).toBeHidden();
    await expect(toggle).toBeFocused();

    await toggle.click();
    await expect(drawer).toBeVisible();
    await expect(drawer).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(toggle).toBeFocused();
  });

  // Replay fixture dates are anchored to the generated BARS window, which ends
  // at "today", so the clip can never fall before the first stored bar.
  const addDays = (date: string, days: number) =>
    new Date(new Date(`${date}T00:00:00Z`).getTime() + days * 86_400_000).toISOString().slice(0, 10);
  const REPLAY_CURSOR = BARS[BARS.length - 30].date;
  const REPLAY_ADVANCED = BARS[BARS.length - 10].date;
  const REPLAY_LAST_BAR = BARS[BARS.length - 1].date;
  const REPLAY_DAY = BARS[BARS.length - 3].date;
  const REPLAY_PREV_DAY = BARS[BARS.length - 4].date;

  test('replay: chart, summary and indicators are clipped to the server session cursor', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    // A replay-bound portfolio. The session cursor is owned by the "server":
    // advancing it jumps to a non-adjacent date on purpose, so a UI that
    // computed its own clock would request and display a different window.
    const session = {
      id: 'sess-1',
      experiment_version_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
      owner_id: 'owner-1',
      initial_virtual_cash: 100000,
      started_at: null,
      data_cutoff_at: '2026-06-30',
      current_snapshot_date: REPLAY_CURSOR,
      status: 'active',
      simulation_policy_version: '1.0.0',
      provider_id: 'demo-dataset',
      provider_confirmed: true,
      timeframe: null,
      current_bar_timestamp: null,
      paper_equity: null,
      current_position_json: null,
      warnings: null,
      total_decisions: 0,
      observed_days: 12,
      state_version: 3,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    };
    let cursor = REPLAY_CURSOR;
    const barsWindow: string[] = [];
    const barsStarts: string[] = [];

    await page.route('**/api/v1/paper-portfolio', (route) =>
      json(route, [
        {
          id: 'pf-1',
          currency: 'USD',
          current_virtual_cash: 100000,
          initial_virtual_cash: 100000,
          status: 'active',
          simulation_session_id: 'sess-1',
        },
      ])
    );
    await page.route('**/api/v1/simulations/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      const method = route.request().method();
      if (method === 'POST' && path.endsWith('/advance')) {
        cursor = REPLAY_ADVANCED;
        session.current_snapshot_date = cursor;
        session.observed_days = 13;
        json(route, {
          session,
          decision: { id: 'dec-1', step_number: 13, snapshot_date: cursor },
        });
        return;
      }
      if (method === 'POST' && path.endsWith('/pause')) {
        session.status = 'paused';
        json(route, session);
        return;
      }
      if (method === 'POST' && path.endsWith('/resume')) {
        session.status = 'active';
        json(route, session);
        return;
      }
      json(route, session);
    });
    // The mocked bars endpoint behaves like the real one: it serves the whole
    // stored window unless the caller constrains it with end_date.
    await page.route('**/api/v1/instruments/US:AAPL/bars**', (route) => {
      const params = new URL(route.request().url()).searchParams;
      const end = params.get('end_date');
      barsWindow.push(end ?? '(none)');
      barsStarts.push(params.get('start_date') ?? '(none)');
      const visible = end ? BARS.filter((b) => b.date <= end) : BARS;
      json(route, {
        ...BARS_RESPONSE_AAPL,
        end_date: end ?? '2099-01-01',
        bar_count: visible.length,
        bars: visible,
        data_quality: {
          ...BARS_RESPONSE_AAPL.data_quality,
          latest_bar_date: visible[visible.length - 1]?.date ?? null,
        },
      });
    });

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const controller = page.getByTestId('replay-controller');
    await expect(controller).toBeVisible();
    await expect(page.getByTestId('replay-clock-value')).toContainText(REPLAY_CURSOR);
    await expect(page.getByTestId('replay-status')).toHaveText('進行中');
    await expect(controller).toContainText('逐日');

    // ⑥ keeps one compact row by default: the provenance a user asks for once
    // (dataset, observed points, strategy version) costs the centre column
    // nothing until 資料摘要 is opened, and it opens on a real click.
    const summaryToggle = controller.getByTestId('replay-meta-toggle');
    await expect(page.getByTestId('replay-summary')).toHaveCount(0);
    await expect(summaryToggle).toHaveAccessibleName('資料摘要');
    await expect(summaryToggle).toHaveAttribute('aria-expanded', 'false');
    await summaryToggle.click();
    const summary = page.getByTestId('replay-summary');
    await expect(summary).toBeVisible();
    await expect(summaryToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(summary).toContainText('demo-dataset');
    await expect(summary).toContainText('12 個觀測點');
    await expect(summary).toContainText('aaaaaaaa');
    await summaryToggle.click();
    await expect(page.getByTestId('replay-summary')).toHaveCount(0);
    await summaryToggle.click();

    // The chart request itself is cut at the cursor, so future bars never reach
    // the client — and with them neither do the series, the indicators or the
    // header summary.
    await expect.poll(() => barsWindow.length).toBeGreaterThan(0);
    expect(barsWindow[barsWindow.length - 1]).toBe(REPLAY_CURSOR);
    // The window must be anchored on the cursor, not on today: a today-anchored
    // 1Y start (366 days back from today) sits AFTER a historical cursor, and the
    // real backend then answers with zero bars — an empty chart mid-replay.
    expect(barsStarts.length).toBe(barsWindow.length);
    expect(barsStarts[barsStarts.length - 1]).toBe(addDays(REPLAY_CURSOR, -366));
    expect(addDays(REPLAY_CURSOR, -366)).not.toBe(addDays(REPLAY_LAST_BAR, -366));
    for (let i = 0; i < barsWindow.length; i += 1) {
      // each cursor cut carries the start anchored on that same cursor
      if (barsWindow[i] !== REPLAY_CURSOR && barsWindow[i] !== REPLAY_ADVANCED) continue;
      expect(barsStarts[i]).toBe(addDays(barsWindow[i], -366));
    }
    // Every request must be cut at a server cursor: the shell holds the bars request
    // until the replay cut is known, so no future window is ever asked for — and with
    // the session bound at mount, an uncut request can only mean the gate is gone.
    expect(barsWindow.every((end) => end === REPLAY_CURSOR || end === REPLAY_ADVANCED)).toBe(true);
    await expect(page.getByTestId('instrument-header')).toContainText(REPLAY_CURSOR);
    await expect(page.getByTestId('chart-meta-line')).toContainText(`數據截至 ${REPLAY_CURSOR}`);
    // the price summary is the close of the last bar inside the cursor window,
    // not of the newest stored bar — proof it is computed from clipped data
    const priceAt = (end: string) => {
      const visible = BARS.filter((b) => b.date <= end);
      return visible[visible.length - 1].close.toFixed(2);
    };
    await expect(page.getByTestId('instrument-last-price')).toContainText(priceAt(REPLAY_CURSOR));
    expect(priceAt(REPLAY_CURSOR)).not.toBe(priceAt(REPLAY_LAST_BAR));
    // the dataset runs to "today"; the visible summary must not
    const lastDatasetBar = REPLAY_LAST_BAR;
    await expect(page.getByTestId('chart-meta-line')).not.toContainText(lastDatasetBar);

    // Advance: the new window must come from the session the API returned
    // (REPLAY_ADVANCED), not from a locally incremented next-day cursor.
    await page.getByTestId('replay-advance').click();
    await expect(page.getByTestId('replay-clock-value')).toContainText(REPLAY_ADVANCED);
    await expect
      .poll(() => barsWindow[barsWindow.length - 1])
      .toBe(REPLAY_ADVANCED);
    await expect(page.getByTestId('chart-meta-line')).toContainText(`數據截至 ${REPLAY_ADVANCED}`);
    await expect(page.getByTestId('instrument-last-price')).toContainText(priceAt(REPLAY_ADVANCED));
    expect(priceAt(REPLAY_ADVANCED)).not.toBe(priceAt(REPLAY_CURSOR));
    expect(barsWindow).not.toContain(addDays(REPLAY_CURSOR, 1));
    expect(REPLAY_ADVANCED).not.toBe(addDays(REPLAY_CURSOR, 1));
    // the whole window travels with the server cursor, start included
    expect(barsStarts[barsStarts.length - 1]).toBe(addDays(REPLAY_ADVANCED, -366));
    await expect(page.getByTestId('replay-summary')).toContainText('13 個觀測點');

    // Only the steps the backend implements are exposed: forward advance plus
    // pause/resume, and the toggle that opens the provenance. No backward, seek
    // or speed control — and nothing that could set a speed.
    const buttons = controller.getByRole('button');
    const labels = await buttons.allTextContents();
    expect(labels.sort()).toEqual(['暫停', '下一日', '資料摘要'].sort());
    await expect(controller.locator('input, select')).toHaveCount(0);
    await expect(page.getByTestId('replay-hint')).toContainText('沒有回溯、跳轉或變速');

    // pause → the advance step is withdrawn by the server status, resume → back
    await page.getByTestId('replay-pause').click();
    await expect(page.getByTestId('replay-status')).toHaveText('已暫停');
    await expect(page.getByTestId('replay-advance')).toBeDisabled();
    await expect(page.getByTestId('replay-advance-disabled-reason')).toContainText('已暫停');
    await expect(page.getByTestId('replay-pause')).toBeHidden();
    await page.getByTestId('replay-resume').click();
    await expect(page.getByTestId('replay-status')).toHaveText('進行中');
    await expect(page.getByTestId('replay-advance')).toBeEnabled();

    // the replay clock in the persistent context bar is the same server cursor
    await expect(page.getByTestId('ctx-replay-clock')).toContainText(REPLAY_ADVANCED);
  });

  test('replay: an intraday cursor clips the daily bars request to the last completed day', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    const barsWindow: string[] = [];
    const barsStarts: string[] = [];
    const session = {
      id: 'sess-5m',
      experiment_version_id: 'ffffffff-1111-2222-3333-444444444444',
      owner_id: 'owner-1',
      initial_virtual_cash: 100000,
      started_at: null,
      data_cutoff_at: REPLAY_DAY,
      current_snapshot_date: REPLAY_DAY,
      status: 'active',
      simulation_policy_version: '1.0.0',
      provider_id: 'synthetic-intraday',
      provider_confirmed: true,
      timeframe: '5m',
      current_bar_timestamp: `${REPLAY_DAY}T14:35:00`,
      paper_equity: null,
      current_position_json: null,
      warnings: null,
      total_decisions: 4,
      observed_days: 5,
      state_version: 9,
      created_at: '2026-01-13T00:00:00Z',
      updated_at: `${REPLAY_DAY}T14:35:00Z`,
    };

    await page.route('**/api/v1/paper-portfolio', (route) =>
      json(route, [
        {
          id: 'pf-1',
          currency: 'USD',
          current_virtual_cash: 100000,
          initial_virtual_cash: 100000,
          status: 'active',
          simulation_session_id: 'sess-5m',
        },
      ])
    );
    await page.route('**/api/v1/simulations/**', (route) => json(route, session));
    await page.route('**/api/v1/instruments/US:AAPL/bars**', (route) => {
      const params = new URL(route.request().url()).searchParams;
      const end = params.get('end_date');
      barsWindow.push(end ?? '(none)');
      barsStarts.push(params.get('start_date') ?? '(none)');
      const visible = end ? BARS.filter((b) => b.date <= end) : BARS;
      json(route, {
        ...BARS_RESPONSE_AAPL,
        bar_count: visible.length,
        bars: visible,
        data_quality: {
          ...BARS_RESPONSE_AAPL.data_quality,
          latest_bar_date: visible[visible.length - 1]?.date ?? null,
        },
      });
    });

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('replay-clock-value')).toContainText(`${REPLAY_DAY} 14:35`);
    await expect(page.getByTestId('replay-clock')).toContainText('逐根 K 線');
    // the per-bar step is labelled with the actual timeframe
    await expect(page.getByTestId('replay-advance')).toHaveText('下一根 5m K 線');

    // The timeframe itself is a secondary fact: open 資料摘要 to read it.
    const summaryToggle = page.getByTestId('replay-controller').getByTestId('replay-meta-toggle');
    await expect(page.getByTestId('replay-summary')).toHaveCount(0);
    await summaryToggle.click();
    await expect(page.getByTestId('replay-summary')).toContainText('5m');

    // The bars endpoint only serves daily data, so the in-progress REPLAY_DAY
    // would carry bars after the 14:35 cursor — the request must stop at the
    // previous completed day.
    await expect
      .poll(() => barsWindow[barsWindow.length - 1])
      .toBe(REPLAY_PREV_DAY);
    expect(barsWindow[barsWindow.length - 1]).not.toBe(REPLAY_DAY);
    expect(barsWindow.every((end) => end === REPLAY_PREV_DAY)).toBe(true);
    // the start travels with the clipped end, never with today
    expect(barsStarts[barsStarts.length - 1]).toBe(addDays(REPLAY_PREV_DAY, -366));
    expect(addDays(REPLAY_PREV_DAY, -366)).not.toBe(addDays(REPLAY_LAST_BAR, -366));
    await expect(page.getByTestId('replay-bars-end')).toHaveText(REPLAY_PREV_DAY);
    await expect(page.getByTestId('replay-hint')).toContainText('/bars 目前僅提供日線');
  });

  // ── ⑦ Action Panel ─────────────────────────────────────────────────────────
  // The session, its owning experiment version and manual-decision are mocked so the
  // panel's own rules hold: the cursor and state_version only move when the server
  // says so, the fill price only ever arrives with the reply, and a lost
  // reply can be reconciled by the idempotency key the panel generated.
  //
  // This lane is not the session-bound contract. The real POST /manual-decision books
  // a pending order with no fill and leaves the cursor to /advance; this fixture still
  // replies with a submission-time fill and an advanced cursor, because every
  // assertion below reads the receipt the panel echoes. The actual
  // 提交→待執行→成交→結算 record is proven against the live backend by
  // e2e/order-lifecycle-flow.spec.ts, not here.
  const ACTION_VERSION_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

  type TransportStep = 'abort-empty' | 'abort-after-create' | 'conflict' | 'ok';

  async function mockActionPanelBackend(
    page: Page,
    opts: {
      assetIds: string[] | null;
      versionOwner?: 'match' | 'other';
      transport?: TransportStep[];
    }
  ) {
    const state = {
      cursor: REPLAY_CURSOR,
      stateVersion: 3,
      observedDays: 12,
      session: {
        id: 'sess-1',
        experiment_version_id: ACTION_VERSION_ID,
        owner_id: 'owner-1',
        initial_virtual_cash: 100000,
        started_at: null,
        data_cutoff_at: '2026-06-30',
        current_snapshot_date: REPLAY_CURSOR,
        status: 'active',
        simulation_policy_version: '1.0.0',
        provider_id: 'demo-dataset',
        provider_confirmed: true,
        timeframe: null,
        current_bar_timestamp: null,
        paper_equity: 100000,
        current_position_json: null,
        warnings: null,
        total_decisions: 12,
        observed_days: 12,
        state_version: 3,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
      } as Record<string, any>,
      decisions: [] as Record<string, any>[],
      submissions: [] as Record<string, any>[],
      // ⑦ reads its 掛單簿 from GET /simulations/{id}/orders. Unmocked, that request reaches
      // the real backend with the mock token, 401s, and the whole page bounces to /login —
      // so every ⑦ locator would time out on the login screen. The book is served empty on
      // purpose: this lane only has to answer the read, it does not model pending/filled/
      // expired rows — those are asserted in e2e/order-lifecycle-flow.spec.ts on :8099.
      sessionOrders: [] as Record<string, any>[],
      barsWindow: [] as string[],
      plan: [...(opts.transport ?? ['ok'])],
      conflictSent: false,
    };

    // A portfolio bound to the replay session — the only case where a manual
    // paper order has a server-owned fill basis.
    await page.route('**/api/v1/paper-portfolio', (route) =>
      json(route, [
        {
          id: 'pf-1',
          currency: 'USD',
          current_virtual_cash: 100000,
          initial_virtual_cash: 100000,
          status: 'active',
          simulation_session_id: 'sess-1',
        },
      ])
    );
    await page.route('**/api/v1/experiments/', (route) =>
      json(route, [
        {
          id: 'exp-1',
          title: '動能反轉研究',
          status: 'locked',
          current_version_id: opts.versionOwner === 'other' ? 'v-other-0000' : ACTION_VERSION_ID,
        },
      ])
    );
    await page.route('**/api/v1/experiments/exp-1/versions', (route) =>
      json(route, [
        {
          id: ACTION_VERSION_ID,
          version_number: 2,
          strategy_spec: {
            template_type: 'sma_crossover',
            dataset: 'demo-dataset',
            start_date: '2024-01-02',
            end_date: '2024-03-01',
            initial_cash: 100000,
            synthetic: true,
            timeframe: '1d',
            asset_ids: opts.assetIds,
            risk_parameters: { stop_pct: 0.05 },
            signal_parameters: { fast: 5, slow: 20 },
          },
          // genuinely absent on the seeded runtime too: never filled in by the UI
          assumptions: null,
          change_reason: '加入停損風控',
          config_hash: null,
          locked_at: '2024-03-01T00:00:00Z',
          user_approved_at: null,
        },
      ])
    );
    // cursor-clipped bars, exactly like the real /bars endpoint
    await page.route('**/api/v1/instruments/US:AAPL/bars**', (route) => {
      const end = new URL(route.request().url()).searchParams.get('end_date');
      state.barsWindow.push(end ?? '(none)');
      const visible = end ? BARS.filter((b) => b.date <= end) : BARS;
      json(route, {
        ...BARS_RESPONSE_AAPL,
        end_date: end ?? '2099-01-01',
        bar_count: visible.length,
        bars: visible,
        data_quality: {
          ...BARS_RESPONSE_AAPL.data_quality,
          latest_bar_date: visible[visible.length - 1]?.date ?? null,
        },
      });
    });
    await page.route('**/api/v1/simulations/sess-1', (route) => json(route, state.session));
    await page.route('**/api/v1/simulations/sess-1/decisions', (route) => json(route, state.decisions));
    await page.route('**/api/v1/simulations/sess-1/orders', (route) => json(route, state.sessionOrders));
    await page.route('**/api/v1/simulations/sess-1/manual-decision', (route) => {
      const body = JSON.parse(route.request().postData() || '{}');
      state.submissions.push(body);
      const step = state.plan.shift() ?? 'ok';

      if (step === 'conflict') {
        // the server cursor moved under the review: the optimistic lock rejects it
        json(route, { detail: 'State version conflict: expected 3, current 9' }, 409);
        return;
      }
      if (typeof body.expected_version === 'number' && body.expected_version !== state.stateVersion) {
        json(route, { detail: `State version conflict: expected ${body.expected_version}, current ${state.stateVersion}` }, 409);
        return;
      }

      const nextCursor = REPLAY_ADVANCED;
      state.cursor = nextCursor;
      state.stateVersion += 1;
      state.observedDays += 1;
      state.session.current_snapshot_date = nextCursor;
      state.session.state_version = state.stateVersion;
      state.session.observed_days = state.observedDays;
      state.session.total_decisions += 1;

      // Fixture-only receipt: the live session-bound route answers this POST with a
      // pending order and fill=null, so nothing here is evidence of how the backend
      // books the trade. The shape exists so the assertions below can still check
      // that the panel echoes the server's own numbers verbatim into a decision row.
      const fill = {
        action: body.action,
        quantity: body.quantity ?? null,
        price: 191.5,
        execution_rule: 'next_open_no_lookahead',
        execution_date: nextCursor,
        observed_date: nextCursor,
        observed_close: 193.25,
        commission: 1.0,
        slippage: 0.2,
        total_cost: Math.round(((body.quantity ?? 0) * 191.5 + 1.2) * 1e4) / 1e4,
        net_proceeds: null,
        gross_pnl: null,
      };
      const decision = {
        id: '55555555-5555-5555-5555-555555555555',
        step_number: state.observedDays,
        snapshot_date: nextCursor,
        data_cutoff_at: '2026-06-30',
        engine_version: '1.1.0',
        decision_type:
          body.action === 'buy' ? 'simulated_entry' : body.action === 'sell' ? 'simulated_exit' : 'no_action',
        decision_detail: {
          action: body.action,
          idempotency_key: body.idempotency_key,
          rationale: body.rationale ?? null,
          reflection: null,
          symbol: 'US:AAPL',
          snapshot_date: nextCursor,
          fill,
        },
        paper_cash: 90000,
        paper_position: { symbol: 'US:AAPL', quantity: body.quantity ?? 0 },
        paper_equity: 100100,
        warnings: null,
        created_at: '2026-01-03T00:00:00Z',
      };
      state.decisions.push(decision);

      if (step === 'abort-empty') {
        // the request never reached the server: no decision exists to reconcile
        state.decisions.pop();
        state.session.current_snapshot_date = REPLAY_CURSOR;
        state.stateVersion -= 1;
        state.observedDays -= 1;
        state.session.state_version = state.stateVersion;
        state.session.observed_days = state.observedDays;
        state.session.total_decisions -= 1;
        state.cursor = REPLAY_CURSOR;
        void route.abort('connectionrefused');
        return;
      }
      if (step === 'abort-after-create') {
        // the decision IS recorded server-side, only the reply is lost
        void route.abort('connectionrefused');
        return;
      }
      json(route, { session: state.session, decision, fill, rejection: null });
    });

    return state;
  }

  test('action panel: ⑦ tabs are keyboard operable and keep the order form across switches', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('action-panel');
    await expect(panel.getByRole('tablist', { name: '操作面板' })).toHaveCount(1);
    const orderTab = panel.getByRole('tab', { name: '手動模擬單' });
    const rulesTab = panel.getByRole('tab', { name: '策略規則' });
    await expect(orderTab).toHaveAttribute('aria-selected', 'true');
    await expect(rulesTab).toHaveAttribute('aria-selected', 'false');
    await expect(orderTab).toHaveAttribute('tabindex', '0');
    await expect(rulesTab).toHaveAttribute('tabindex', '-1');

    // the inactive panel is mounted (so state survives) but out of the a11y tree
    const rulesPane = panel.getByTestId('action-tabpanel-rules');
    await expect(rulesPane).toHaveCount(1);
    await expect(rulesPane).toBeHidden();

    await panel.getByTestId('order-lots').fill('3');
    await rulesTab.click();
    await expect(rulesPane).toBeVisible();
    await expect(panel.getByTestId('rules-no-session')).toContainText('沒有綁定歷史回放工作階段');
    // no session means no effective version — the panel must not invent one
    await expect(panel.getByTestId('rules-table')).toHaveCount(0);

    await rulesTab.press('ArrowLeft');
    await expect(orderTab).toBeFocused();
    await expect(orderTab).toHaveAttribute('aria-selected', 'true');
    await expect(rulesPane).toBeHidden();
    // switching tabs to read the rules must not discard the half-built order
    await expect(panel.getByTestId('order-lots')).toHaveValue('3');

    await orderTab.press('ArrowRight');
    await expect(rulesTab).toBeFocused();
    await expect(rulesTab).toHaveAttribute('aria-selected', 'true');
  });

  test('action panel: replay order resolves rules, blocks on unprovided metadata, and fills at the server cursor', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    const backend = await mockActionPanelBackend(page, { assetIds: ['US:AAPL'] });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('action-panel');

    // 策略規則 tab: every row comes from the version response
    await panel.getByRole('tab', { name: '策略規則' }).click();
    await expect(panel.getByTestId('rules-table')).toBeVisible();
    await expect(panel.getByTestId('rules-experiment')).toContainText('動能反轉研究');
    await expect(panel.getByTestId('rules-effective-version')).toContainText('v2');
    await expect(panel.getByTestId('rules-saved-version')).toContainText('aaaaaaaa');
    await expect(panel.getByTestId('rules-assets')).toHaveText('US:AAPL');
    await expect(panel.getByTestId('rules-data-nature')).toContainText('合成（synthetic=true）');
    await expect(panel.getByTestId('rules-initial-cash')).toHaveText('100000');
    await expect(panel.getByTestId('rules-risk-parameters')).toContainText('stop_pct=0.05');
    // fields the API does not return stay 未提供 — never 0, never a guess
    await expect(panel.getByTestId('rules-config-hash')).toHaveText('未提供');
    await expect(panel.getByTestId('rules-approved-at')).toHaveText('未提供');
    await expect(panel.getByTestId('rules-assumptions')).toHaveText('未提供');
    // the version→experiment mapping limit is disclosed, not hidden
    await expect(panel.getByTestId('rules-mapping-note')).toContainText('current_version_id');

    await panel.getByRole('tab', { name: '手動模擬單' }).click();
    await expect(panel.getByTestId('order-target')).toContainText('歷史回放工作階段 sess-1');
    await expect(panel.getByTestId('order-target')).toContainText(REPLAY_CURSOR);
    await expect(panel.getByTestId('order-blocked')).toHaveCount(0);
    await expect(panel.getByTestId('order-manual-semantics')).toContainText('沒有限價');
    await expect(panel.getByTestId('order-manual-semantics')).toContainText('next_open_no_lookahead');
    // only the order kinds the backend implements
    expect(await panel.getByTestId('order-action').locator('option').allTextContents()).toEqual([
      '買入',
      '賣出',
      '觀望（不成交）',
    ]);

    await panel.getByTestId('order-quantity-input').fill('5');
    await panel.getByTestId('order-rationale').fill('動能確認後進場');
    await panel.getByTestId('order-build-review').click();
    const review = panel.getByTestId('order-review');
    await expect(review).toBeVisible();
    await expect(panel.getByTestId('order-review-cursor')).toContainText(REPLAY_CURSOR);
    await expect(panel.getByTestId('order-review-key')).not.toBeHidden();
    // the price is decided by the server after the cursor: nothing pre-fills it
    await expect(review).not.toContainText('191.5');

    // invalidation: editing a field the decision depends on voids the review
    await panel.getByTestId('order-quantity-input').fill('7');
    await expect(panel.getByTestId('order-review-stale')).toBeVisible();
    await expect(panel.getByTestId('order-submit-manual')).toBeDisabled();
    await panel.getByTestId('order-build-review').click();
    await expect(panel.getByTestId('order-review-stale')).toBeHidden();
    await expect(panel.getByTestId('order-quantity')).toContainText('7 股');

    await Promise.all([
      page.waitForResponse(
        (res) => res.url().includes('/manual-decision') && res.status() === 200
      ),
      panel.getByTestId('order-submit-manual').click(),
    ]);

    const sent = backend.submissions[backend.submissions.length - 1];
    expect(sent.action).toBe('buy');
    expect(sent.quantity).toBe(7);
    expect(sent.rationale).toBe('動能確認後進場');
    // the optimistic lock travels with the submission
    expect(sent.expected_version).toBe(3);
    expect(String(sent.idempotency_key)).toMatch(/^ws-sess-1-/);

    const result = panel.getByTestId('order-manual-result');
    // the panel echoes the server price verbatim — it never re-formats or re-prices it
    await expect(result).toContainText('191.5');
    await expect(result).toContainText('next_open_no_lookahead');
    await expect(result).toContainText('步驟 13');
    await expect(result).toContainText('90000 / 100100');
    await expect(result).not.toContainText('未提供 · 步驟');
    await expect(review).toBeHidden();

    // one clock only: the server-advanced cursor drives the controller, the chart
    // cut and the context bar — the panel keeps no local replay timeline
    await expect(page.getByTestId('replay-clock-value')).toContainText(REPLAY_ADVANCED);
    const summaryToggle = page.getByTestId('replay-controller').getByTestId('replay-meta-toggle');
    await summaryToggle.click();
    await expect(page.getByTestId('replay-bars-end')).toHaveText(REPLAY_ADVANCED);
    await expect
      .poll(() => backend.barsWindow[backend.barsWindow.length - 1])
      .toBe(REPLAY_ADVANCED);
    expect(backend.barsWindow).not.toContain('(none)');
  });

  test('action panel: another instrument\'s cursor blocks submit and must not clip this chart', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    const backend = await mockActionPanelBackend(page, { assetIds: ['DEMO-ALPHA'] });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('action-panel');
    // the rules DO resolve — the block is the instrument mismatch, not missing data
    await panel.getByRole('tab', { name: '策略規則' }).click();
    await expect(panel.getByTestId('rules-assets')).toHaveText('DEMO-ALPHA');

    await panel.getByRole('tab', { name: '手動模擬單' }).click();
    await expect(panel.getByTestId('order-blocked')).toContainText('US:AAPL');
    await expect(panel.getByTestId('order-blocked')).toContainText('DEMO-ALPHA');
    await expect(panel.getByTestId('order-target')).toContainText('停用');
    await expect(panel.getByTestId('order-action')).toHaveCount(0);
    await expect(panel.getByTestId('order-submit-manual')).toHaveCount(0);
    await expect(panel.getByTestId('order-build-review')).toHaveCount(0);
    // and nothing reached the server
    expect(backend.submissions.length).toBe(0);

    // The same fact has to reach the chart: DEMO-ALPHA's cursor is not a statement
    // about US:AAPL, so clipping this series at it would paint a replay view the
    // session never produced. The bars request must travel past the cursor again.
    await expect
      .poll(() => backend.barsWindow[backend.barsWindow.length - 1])
      .not.toBe(REPLAY_CURSOR);
    expect(REPLAY_LAST_BAR > REPLAY_CURSOR).toBe(true);
    await expect(page.getByTestId('chart-meta-line')).toContainText(
      `數據截至 ${REPLAY_LAST_BAR}`
    );

    // ⑥ says so out loud, and its 圖表截止 reports the truth instead of the foreign date
    const controller = page.getByTestId('replay-controller');
    await expect(controller.getByTestId('replay-instrument-note')).toContainText('DEMO-ALPHA');
    await expect(controller.getByTestId('replay-instrument-note')).toContainText('US:AAPL');
    await controller.getByTestId('replay-meta-toggle').click();
    await expect(page.getByTestId('replay-bars-end')).toHaveText('未裁切');
    await expect(page.getByTestId('replay-hint')).toContainText('圖表未依此游標裁切');
    // the session clock itself stays reported — it is a real fact about the session
    await expect(page.getByTestId('replay-clock-value')).toContainText(REPLAY_CURSOR);

    // the persistent strip must not read as 「this chart is cut at 2024-…」
    await expect(page.getByTestId('ctx-replay-clock')).toContainText(REPLAY_CURSOR);
    await expect(page.getByTestId('ctx-replay-mismatch')).toHaveText('Cursor is on another instrument');
    await page.getByTestId('ctx-details-toggle').click();
    await expect(page.getByTestId('ctx-replay-cut')).toContainText('not cut to this cursor');
    await expect(page.getByTestId('ctx-replay-cut')).not.toHaveText(REPLAY_CURSOR);
  });

  test('action panel: unresolvable rules block submit instead of guessing the strategy', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    await mockActionPanelBackend(page, { assetIds: ['US:AAPL'], versionOwner: 'other' });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('action-panel');
    await panel.getByRole('tab', { name: '策略規則' }).click();
    await expect(panel.getByTestId('rules-unresolved')).toContainText('未提供');
    await expect(panel.getByTestId('rules-unresolved')).toContainText('current_version_id');
    await expect(panel.getByTestId('rules-table')).toHaveCount(0);

    await panel.getByRole('tab', { name: '手動模擬單' }).click();
    await expect(panel.getByTestId('order-blocked')).toContainText('策略規則未提供');
    await expect(panel.getByTestId('order-submit-manual')).toHaveCount(0);
  });

  test('action panel: an unknown submit outcome is reconciled by the same idempotency key', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    const backend = await mockActionPanelBackend(page, {
      assetIds: ['US:AAPL'],
      transport: ['abort-empty', 'abort-after-create', 'ok'],
    });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('action-panel');
    await expect(panel.getByTestId('order-submit-manual')).toHaveCount(0);
    await panel.getByTestId('order-quantity-input').fill('4');
    await panel.getByTestId('order-build-review').click();
    await panel.getByTestId('order-submit-manual').click();

    // round 1 — the request died before the server saw it
    await expect(panel.getByTestId('order-unknown')).toContainText('提交結果未知');
    await expect(panel.getByTestId('order-unknown')).toContainText('同一冪等鍵');
    // the review must survive: dropping it would generate a new key and double-fill
    await expect(panel.getByTestId('order-review')).toBeVisible();
    await panel.getByTestId('order-reconcile-manual').click();
    await expect(panel.getByTestId('order-reconcile')).toContainText('可以安全重試');
    await expect(panel.getByTestId('order-manual-result')).toHaveCount(0);

    // round 2 — retry with the key the first review already generated
    await panel.getByTestId('order-submit-manual').click();
    await expect(panel.getByTestId('order-unknown')).toContainText('提交結果未知');
    expect(backend.submissions.length).toBe(2);
    expect(backend.submissions[1].idempotency_key).toBe(backend.submissions[0].idempotency_key);

    await panel.getByTestId('order-reconcile-manual').click();
    await expect(panel.getByTestId('order-reconcile')).toContainText('決策已成立');
    await expect(panel.getByTestId('order-manual-result')).toContainText('next_open_no_lookahead');
    // the recovered decision consumed the review, so no blind third POST is possible
    await expect(panel.getByTestId('order-review')).toHaveCount(0);
    await expect(panel.getByTestId('order-submit-manual')).toHaveCount(0);
    expect(backend.submissions.length).toBe(2);
    // and the server recorded exactly one decision for the two attempts
    expect(backend.decisions.length).toBe(1);
  });

  test('action panel: a state version conflict voids the review and asks for a fresh one', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    const backend = await mockActionPanelBackend(page, {
      assetIds: ['US:AAPL'],
      transport: ['conflict'],
    });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const panel = page.getByTestId('action-panel');
    await panel.getByTestId('order-build-review').click();
    await panel.getByTestId('order-submit-manual').click();

    await expect(panel.getByTestId('order-error')).toContainText('狀態版本衝突');
    await expect(panel.getByTestId('order-error')).toContainText('重新檢視');
    // a rejected decision must not look like a filled one
    await expect(panel.getByTestId('order-manual-result')).toHaveCount(0);
    expect(backend.decisions.length).toBe(0);
    // the stale review is dropped, so the next submit has to be re-reviewed
    await expect(panel.getByTestId('order-review')).toBeHidden();
    await expect(panel.getByTestId('order-build-review')).toBeVisible();
  });

  test('action panel: ⑦ keeps its 304px column floor at the three-column breakpoints', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    const panel = page.getByTestId('action-panel');
    await expect(panel).toBeVisible();
    const at1440 = await panel.boundingBox();
    expect(at1440?.width).toBeGreaterThanOrEqual(304);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.waitForLoadState('networkidle');
    const at1280 = await panel.boundingBox();
    // same convention as the watchlist floor: 8px of resize handles is added back
    // before converting 304px into a panel percentage. The percentage then resolves
    // against the group content box, which is itself 8px narrower than the window,
    // so the painted column lands between 304 and 312px — never below the design
    // minimum and never padded by more than one handle's width.
    expect(at1280?.width).toBeGreaterThanOrEqual(304);
    expect(at1280!.width).toBeLessThan(312);

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(panel).toBeVisible();
    // below 1280 the floor is intentionally released to keep the chart usable, but
    // the tab labels must still be fully legible
    for (const name of ['手動模擬單', '策略規則']) {
      const tab = panel.getByRole('tab', { name });
      await expect(tab).toBeVisible();
      expect(
        await tab.evaluate((el) => el.scrollWidth - el.clientWidth)
      ).toBeLessThanOrEqual(1);
    }
  });

  // ── ⑧ Evidence dock ────────────────────────────────────────────────────────
  // Tier A: every value below is a response the interception layer produced, so
  // these tests prove rendering, keying and focus behaviour — not that the real
  // backend stores them. The real-local-backend counterparts live in
  // e2e/workspace-replay-backend.spec.ts.
  test('⑧ dock: seven tabs, one tablist, and the portfolio it speaks for is named', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const dock = page.getByTestId('evidence-dock');
    await expect(dock).toBeVisible();

    // the ⑦ action panel is a second tablist on the same page — the dock's own
    // must be addressable by name, and must hold exactly the seven design tabs
    const tablist = page.getByRole('tablist', { name: '證據面板分頁' });
    await expect(tablist).toHaveCount(1);
    const tabs = tablist.getByRole('tab');
    await expect(tabs).toHaveCount(7);
    expect(await tabs.allTextContents()).toEqual([
      '持倉',
      '訂單與成交',
      '自動交易日誌',
      '結果',
      '研究',
      '模型',
      '系統紀錄',
    ]);

    // roving tabindex: only the selected tab is in the tab order
    await expect(page.getByTestId('tab-positions')).toHaveClass(/ws-tab-active/);
    await expect(page.getByTestId('tab-positions')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('tab-positions')).toHaveAttribute('tabindex', '0');
    const inactive = tablist.getByRole('tab', { name: '結果' });
    await expect(inactive).toHaveAttribute('aria-selected', 'false');
    await expect(inactive).toHaveAttribute('tabindex', '-1');
    await expect(inactive).toHaveAttribute('aria-controls', 'evidence-tabpanel-results');
    // the inactive pane is not mounted at all, so a screen reader cannot reach
    // another book's rows while 持倉 is selected
    await expect(page.getByTestId('evidence-tabpanel-results')).toHaveCount(0);

    // keyboard navigation among the seven
    await page.getByTestId('tab-journal').click();
    await expect(page.getByTestId('tab-journal')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('tab-journal')).toBeFocused();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByTestId('tab-results')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('tab-results')).toBeFocused();
    await page.keyboard.press('Home');
    await expect(page.getByTestId('tab-positions')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('tab-positions')).toBeFocused();
    await page.keyboard.press('End');
    await expect(page.getByTestId('tab-log')).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('ArrowLeft');
    await expect(page.getByTestId('tab-models')).toHaveAttribute('aria-selected', 'true');

    // which portfolio the dock is reporting for, stated rather than implied — and
    // it must be the same book the order form names, not a second resolution
    await expect(page.getByTestId('evidence-portfolio-id')).toHaveText('pf-1');
    await expect(page.getByTestId('evidence-resolution')).toContainText(
      '與「手動模擬單」目前所選組合一致'
    );
    await expect(page.getByTestId('order-account-context')).toContainText('pf-1');

    // the scope filter belongs to the three books that carry an instrument
    await page.getByTestId('tab-positions').click();
    await expect(page.getByTestId('evidence-scope')).toHaveCount(1);
    await page.getByTestId('tab-results').click();
    await expect(page.getByTestId('evidence-scope')).toHaveCount(0);
    await page.getByTestId('tab-log').click();
    await expect(page.getByTestId('evidence-scope')).toHaveCount(0);
  });

  test('⑧ positions: a null is 未提供／無法計算, never a fabricated 0.00', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const table = page.getByTestId('evidence-positions-table');
    await expect(table.locator('tbody tr')).toHaveCount(2);

    // US:AAPL has no mark price and no realized pnl in the fixture response
    const aapl = table.locator('tr', { has: page.getByTestId('evidence-position-pos-aapl') });
    await expect(aapl.locator('td').nth(1)).toHaveText('25');
    await expect(aapl.locator('td').nth(2)).toHaveText('181.50 USD');
    await expect(aapl.locator('td').nth(3)).toHaveText('未提供');
    await expect(aapl.locator('td').nth(4)).toHaveText('無法計算');
    await expect(aapl.locator('td').nth(5)).toHaveText('持有中');
    await expect(aapl.locator('td').nth(6)).toHaveText('2026-01-05 15:31:00');

    // HK:0700 IS priced, and the sign travels with it
    const hk = table.locator('tr', { has: page.getByTestId('evidence-position-pos-0700') });
    await expect(hk.locator('td').nth(4)).toHaveText('+1,280.00 USD');

    // the drawer for the unpriced position must repeat the distinction, not 0
    await page.getByTestId('evidence-position-pos-aapl').click();
    const drawer = page.getByTestId('evidence-drawer');
    await expect(drawer).toBeVisible();
    await expect(page.getByTestId('drawer-position-unrealized')).toHaveText('無法計算');
    await expect(page.getByTestId('drawer-position-realized')).toHaveText('未提供');
    await expect(page.getByTestId('drawer-position-closed')).toHaveText('不適用');
    await expect(page.getByTestId('drawer-position-cost')).toHaveText('181.50 USD');
    await page.getByTestId('evidence-drawer-close').click();
    await expect(drawer).toHaveCount(0);
  });

  test('⑧ scope filter: 目前標的 narrows the loaded rows and clears in one click', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    const WATCHLIST_WITH_TSLA = {
      watchlists: [
        {
          ...WATCHLIST_RESPONSE.watchlists[0],
          items: [
            ...WATCHLIST_RESPONSE.watchlists[0].items,
            { symbol: 'US:TSLA', position: 2, added_at: '2026-10-03T00:00:00Z' },
          ],
        },
      ],
    };
    const INSTRUMENT_TSLA = { ...INSTRUMENT_AAPL, instrument_id: 'US:TSLA', symbol: 'TSLA', name: 'Tesla Inc.' };
    const BARS_TSLA = { ...BARS_RESPONSE_AAPL, instrument_id: 'US:TSLA', symbol: 'TSLA', name: 'Tesla Inc.' };
    // registered last, so these win over the shared watchlist/instrument routes
    await page.route('**/api/v1/watchlists**', (route) => json(route, WATCHLIST_WITH_TSLA));
    await page.route('**/api/v1/instruments/US:TSLA/bars**', (route) => json(route, BARS_TSLA));
    await page.route('**/api/v1/instruments/US:TSLA', (route) => json(route, INSTRUMENT_TSLA));

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('evidence-scope-note')).toContainText('客戶端篩選');
    await expect(page.getByTestId('evidence-scope-current')).toContainText('目前標的（US:AAPL）');
    await expect(page.getByTestId('evidence-scope-clear')).toHaveCount(0);

    await page.getByTestId('evidence-scope-current').click();
    await expect(page.getByTestId('evidence-position-pos-0700')).toHaveCount(0);
    await expect(page.getByTestId('evidence-position-pos-aapl')).toBeVisible();
    // clearing is one click, and it is only offered while a filter is on
    await page.getByTestId('evidence-scope-clear').click();
    await expect(page.getByTestId('evidence-position-pos-0700')).toBeVisible();
    await expect(page.getByTestId('evidence-scope-clear')).toHaveCount(0);
    await expect(page.getByTestId('evidence-scope-all')).toHaveAttribute('aria-pressed', 'true');

    // switch the workspace symbol: the filter follows it, and an instrument with
    // no rows says so with the count it had — it does not claim an empty portfolio.
    // US:TSLA is added to the watchlist for this test only, because both stock
    // fixtures above have a position and the scoped-empty state would otherwise
    // be unreachable.
    await page
      .getByTestId('watchlist-panel')
      .getByRole('button', { name: '選取標的 US:TSLA' })
      .click();
    await expect(page.getByTestId('chart-legend')).toContainText('TSLA', { timeout: 10_000 });
    await page.getByTestId('evidence-scope-current').click();
    const empty = page.getByTestId('evidence-empty-持倉');
    await expect(empty).toContainText('目前標的（US:TSLA）');
    await expect(empty).toContainText('已載入的 2 筆持倉之中沒有紀錄');
    await expect(page.getByTestId('evidence-no-positions')).toHaveCount(0);
    await page.getByTestId('evidence-empty-clear-持倉').click();
    await expect(page.getByTestId('evidence-position-pos-0700')).toBeVisible();
  });

  test('⑧ 訂單與成交: three separate books, each with its own status and clock', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await page.getByTestId('tab-orders-fills').click();

    const pane = page.getByTestId('evidence-tabpanel-orders-fills');
    // the sections are labelled so a paper order is never read as an intent
    await expect(pane).toContainText('模擬訂單（paper orders）');
    await expect(pane).toContainText('模擬成交（fills）');
    await expect(pane).toContainText('執行意向（execution intents）');
    await expect(pane).toContainText('沒有執行意向。');

    const orders = page.getByTestId('evidence-orders-table');
    await expect(orders.locator('tbody tr')).toHaveCount(3);
    const human = orders.locator('tr', { has: page.getByTestId('evidence-order-ord-aapl') });
    await expect(human.locator('td').nth(1)).toHaveText('本人');
    await expect(human.locator('td').nth(2)).toHaveText('買入');
    await expect(human.locator('td').nth(4)).toHaveText('已成交');
    await expect(human.locator('td').nth(5)).toHaveText('2026-01-05 15:30:00');
    await expect(page.getByTestId('evidence-order-exec-time-ord-aapl')).toHaveText('2026-01-06');
    await expect(human.locator('td').nth(7)).toHaveText(/\u22124,538\.50 USD/);

    const system = orders.locator('tr', { has: page.getByTestId('evidence-order-ord-0700') });
    await expect(system.locator('td').nth(1)).toHaveText('系統規則');
    // the intraday fill shows both the trade date and the bar it filled on
    await expect(page.getByTestId('evidence-order-exec-time-ord-0700')).toHaveText(
      '2026-01-07 · 2026-01-07 14:35:00'
    );

    const pending = orders.locator('tr', { has: page.getByTestId('evidence-order-ord-pending') });
    await expect(pending.locator('td').nth(1)).toHaveText('API 程式');
    await expect(pending.locator('td').nth(4)).toHaveText('待成交');
    // an order that never filled has no execution time and no cash movement —
    // neither is rendered as a date or a zero
    await expect(page.getByTestId('evidence-order-exec-time-ord-pending')).toHaveText('不適用');
    await expect(pending.locator('td').nth(7)).toHaveText('未提供');

    const fills = page.getByTestId('evidence-fills-table');
    await expect(fills.locator('tbody tr')).toHaveCount(2);
    await expect(page.getByTestId('evidence-fill-bar-time-fill-aapl')).toHaveText('不適用');
    await expect(page.getByTestId('evidence-fill-bar-time-fill-0700')).toHaveText(
      '2026-01-07 14:35:00'
    );
    // the fixture's slippage of 0 is a returned zero and prints as zero
    const fill0700 = fills.locator('tr', { has: page.getByTestId('evidence-fill-fill-0700') });
    await expect(fill0700.locator('td').nth(4)).toHaveText('0.00 USD');
    await expect(fill0700.locator('td').nth(5)).toHaveText('3.20 USD');
  });

  test('⑧ detail drawer: keyed by record id, focus-trapped, closed by button or Escape', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    const urlBefore = page.url();
    await page.getByTestId('tab-orders-fills').click();

    const trigger = page.getByTestId('evidence-order-ord-aapl');
    await trigger.click();

    const drawer = page.getByTestId('evidence-drawer');
    await expect(drawer).toBeVisible();
    await expect(drawer).toHaveAttribute('aria-modal', 'true');
    await expect(drawer).toHaveAttribute('aria-label', '模擬訂單明細 ord-aapl');
    await expect(page.getByTestId('evidence-drawer-record-id')).toHaveText('ord-aapl');
    await expect(drawer).toBeFocused();
    // the workspace did not navigate: the same URL, the same chart
    expect(page.url()).toBe(urlBefore);
    await expect(page.getByTestId('chart-legend')).toContainText('AAPL');

    await expect(page.getByTestId('drawer-order-actor')).toHaveText('本人');
    await expect(page.getByTestId('drawer-order-created')).toHaveText('2026-01-05 15:30:00');
    await expect(page.getByTestId('drawer-order-cutoff')).toHaveText('2026-01-05');
    await expect(page.getByTestId('drawer-order-version')).toHaveText('未提供');
    await expect(page.getByTestId('drawer-order-decision')).toHaveText('dec-entry-1');
    await expect(page.getByTestId('drawer-order-idempotency')).toHaveText('ws-pf-1-1');
    await expect(page.getByTestId('drawer-order-fill-id-0')).toHaveText('fill-aapl');

    // Tab cannot escape into the tables behind the drawer
    const controls = drawer.locator(
      'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    const count = await controls.count();
    expect(count).toBeGreaterThanOrEqual(1);
    for (let i = 0; i < count + 1; i += 1) {
      await page.keyboard.press('Tab');
      expect(
        await drawer.evaluate((el) => el.contains(document.activeElement)),
        `tab press ${i + 1} of ${count + 1}`
      ).toBe(true);
    }

    // the close control is reachable by pointer and by keyboard alike
    await page.getByTestId('evidence-drawer-close').click();
    await expect(drawer).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await trigger.click();
    await expect(drawer).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
    await expect(trigger).toBeFocused();

    // an order with no fills says so instead of showing an empty price row
    await page.getByTestId('evidence-order-ord-pending').click();
    await expect(page.getByTestId('drawer-order-no-fills')).toHaveText(
      '此訂單在已載入的日誌中沒有成交列。'
    );
    await expect(page.getByTestId('drawer-order-rationale')).toHaveText('未提供');
    await page.getByTestId('evidence-drawer-close').click();
  });

  test('⑧ journal: one row per record, drawer carries all 27 columns and their semantics', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await page.getByTestId('tab-journal').click();

    const table = page.getByTestId('evidence-journal-table');
    await expect(table.locator('tbody tr')).toHaveCount(3);
    const fillRow = table.locator('tr', { has: page.getByTestId('evidence-journal-row-fill-aapl') });
    await expect(fillRow.locator('td').nth(0)).toHaveText('成交紀錄');
    await expect(fillRow.locator('td').nth(5)).toHaveText('模擬進場');
    await expect(fillRow.locator('td').nth(6)).toHaveText('2026-01-05');
    await expect(fillRow.locator('td').nth(9)).toHaveText('2026-01-05 15:30:00');

    const orderRow = table.locator('tr', {
      has: page.getByTestId('evidence-journal-row-ord-pending'),
    });
    await expect(orderRow.locator('td').nth(0)).toHaveText('未成交訂單');
    await expect(orderRow.locator('td').nth(5)).toHaveText('未提供');
    await expect(orderRow.locator('td').nth(7)).toHaveText('不適用');

    await page.getByTestId('evidence-journal-row-fill-aapl').click();
    const drawer = page.getByTestId('evidence-drawer');
    await expect(page.getByTestId('evidence-drawer-title')).toHaveText('成交日誌明細');
    await expect(page.getByTestId('evidence-drawer-record-id')).toHaveText('fill-aapl');
    // the drawer renders exactly the column set the endpoint declared
    await expect(drawer.locator('.ws-evidence-drawer-row')).toHaveCount(27);
    await expect(page.getByTestId('drawer-journal-net_cash_change')).toHaveText(
      /\u22124,538\.50 USD/
    );
    await expect(page.getByTestId('drawer-journal-bar_timestamp')).toHaveText('不適用');
    await expect(page.getByTestId('drawer-journal-execution_date')).toHaveText('2026-01-06');
    await expect(page.getByTestId('drawer-journal-simulated_fill_reason')).toHaveText('rule_entry');
    // the note behind each column is the backend's own semantics text
    await expect(page.getByTestId('drawer-journal-net_cash_change').locator('xpath=..')).toHaveAttribute(
      'title',
      'Decimal in `currency`, signed cash movement (negative on BUY, positive on SELL). Empty = null, never 0.'
    );
    await expect(page.getByTestId('drawer-journal-semantics')).toContainText('而不是零');

    await page.getByTestId('evidence-drawer-close').click();
    await page.getByTestId('evidence-journal-row-ord-pending').click();
    await expect(page.getByTestId('evidence-drawer-title')).toHaveText('未成交訂單日誌明細');
    await expect(page.getByTestId('drawer-journal-observed_price')).toHaveText('不適用');
    await expect(page.getByTestId('drawer-journal-fill_id')).toHaveText('未提供');
  });

  test('⑧ results: only what the endpoints compute, with the missing endpoint disclosed', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await page.getByTestId('tab-results').click();

    await expect(page.getByTestId('results-initial-cash')).toHaveText('100,000.00 USD');
    await expect(page.getByTestId('results-cash')).toHaveText('95,461.50 USD');
    await expect(page.getByTestId('results-cash-return')).toHaveText(/\u22124\.54%/);
    // 0 returned by the backend prints as 0; null prints as 無法計算
    await expect(page.getByTestId('results-realized-pnl')).toHaveText('0.00 USD');
    await expect(page.getByTestId('results-unrealized-pnl')).toHaveText('無法計算');
    await expect(page.getByTestId('results-valuation-basis')).toContainText('最新收盤價');
    await expect(page.getByTestId('results-unpriced-assets')).toHaveText('US:AAPL');
    await expect(page.getByTestId('results-open-positions')).toHaveText('2');
    await expect(page.getByTestId('results-order-count')).toHaveText('3');
    await expect(page.getByTestId('results-fill-count')).toHaveText('2');
    await expect(page.getByTestId('results-decision-count')).toHaveText('2');

    // the cash-only figure is named for what it is
    await expect(page.getByTestId('evidence-results-table')).toContainText('現金變動（不含未實現）');

    const gap = page.getByTestId('evidence-results-gap');
    await expect(gap).toHaveCount(1);
    await expect(gap).toContainText('後端沒有組合層級的成果端點');
    await expect(gap).toContainText('沒有淨值曲線');
    await expect(gap).toContainText('不是總報酬率');
    await expect(gap).toContainText('實驗回測指標屬於策略版本');
    // no backtest metric is allowed to stand in for this book's own result
    await expect(page.getByTestId('evidence-results-table')).not.toContainText('Sharpe');
    await expect(page.getByTestId('evidence-results-table')).not.toContainText('回撤');
    await expect(page.getByTestId('evidence-results-link')).toHaveAttribute('href', '/paper-portfolio/pf-1');
  });

  test('⑧ band: the joint ⑤/⑧ floors for the measured column, and no control left unreachable behind a clip', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    const dock = page.getByTestId('evidence-dock');
    await expect(dock).toBeVisible();
    await dock.getByTestId('tab-results').click();

    // The band's own clip box, the dock's scroll container, and whether the last control
    // of the tallest tab sits inside that clip. The defect this guards: the dock grew to
    // its content height inside a resize Panel that clips with overflow:hidden, so
    // everything under the line — the whole 結果 body — was painted and then lost.
    const geom = () =>
      dock.evaluate((el) => {
        const panel = el.closest('[data-panel-id]') as HTMLElement;
        const body = el.querySelector('.ws-panel-body') as HTMLElement;
        const clip = panel.getBoundingClientRect();
        const link = el.querySelector('[data-testid="evidence-results-link"]') as HTMLElement;
        const r = link.getBoundingClientRect();
        const chartPanel = document
          .querySelector('[data-testid="price-chart"]')
          ?.closest('[data-panel-id]') as HTMLElement;
        const canvas = document.querySelector('.ws-chart-canvas') as HTMLElement;
        // Measure the column exactly as the shell's ResizeObserver does — the group's
        // own box — instead of reconstructing it from panel heights plus an assumed
        // handle size. The vertical handle is 4px, so adding 8 overstated the column
        // by 4px and could flip the roomy/compact threshold at 1280x800 by one pixel.
        const groupWrapper = panel.closest('[data-panel-group]')
          ?.parentElement as HTMLElement | null;
        const columnH = groupWrapper
          ? groupWrapper.getBoundingClientRect().height
          : chartPanel.getBoundingClientRect().height + clip.height + 4;
        const chromeH = chartPanel.getBoundingClientRect().height - canvas.getBoundingClientRect().height;
        return {
          bandH: Math.round(clip.height),
          canvasH: Math.round(canvas.getBoundingClientRect().height),
          columnH: Math.round(columnH),
          chromeH: Math.round(chromeH),
          // ⑤'s 320px canvas plus ⑧'s 240px band both fit only if the column has the room;
          // that is the shell's own rule, so the test states it the same way.
          roomy: Math.round(columnH) >= Math.round(chromeH) + 320 + 8 + 240,
          overflow: panel.scrollHeight - panel.clientHeight,
          scrollable: ['auto', 'scroll'].includes(getComputedStyle(body).overflowY),
          bodyH: body.clientHeight,
          contentH: body.scrollHeight,
          linkInsideBand: r.top >= clip.top - 1 && r.bottom <= clip.bottom + 1,
        };
      });

    // The floors are joint, so they are asserted together: where the column can hold
    // ⑤'s 320px canvas next to ⑧'s 240px band it must, and where it cannot — a live
    // replay strip inside ⑤ eats the difference — the compact pair (200/160) applies
    // instead of the two minimums being locked against each other until one clips.
    const expectFloors = async (g: Awaited<ReturnType<typeof geom>>, where: string) => {
      expect(g.overflow, `${where}: the band must never overflow its own clip`).toBeLessThanOrEqual(1);
      expect(g.canvasH, `${where}: ⑤ keeps its canvas floor`).toBeGreaterThanOrEqual(200);
      expect(g.bandH, `${where}: ⑧ keeps its band floor`).toBeGreaterThanOrEqual(160);
      if (g.roomy) {
        expect(g.canvasH, `${where}: a roomy column must give ⑤ its 320px canvas`).toBeGreaterThanOrEqual(320);
        expect(g.bandH, `${where}: a roomy column must give ⑧ its 240px band`).toBeGreaterThanOrEqual(240);
      }
    };

    // the floor is applied after the group's pixel height is measured, so wait for it
    await expect
      .poll(() => geom().then((g) => g.canvasH), {
        timeout: 10_000,
        message: '⑤ must reach a measured canvas height before the floors are judged',
      })
      .toBeGreaterThan(0);
    await expect
      .poll(async () => {
        const g = await geom();
        return g.roomy ? g.bandH >= 240 && g.canvasH >= 320 : g.bandH >= 160 && g.canvasH >= 200;
      }, {
        timeout: 10_000,
        message: '⑧ and ⑤ must hold their joint floors for this column height',
      })
      .toBe(true);

    const at1440 = await geom();
    await expectFloors(at1440, '1440x900');
    expect(at1440.scrollable, 'the dock body is the scroll container for taller content').toBe(
      true
    );
    // taller content is the case under test, not an empty tab that trivially fits
    expect(at1440.contentH).toBeGreaterThan(at1440.bodyH);

    // a real wheel gesture, repeated until the last control is inside the band
    const box = await dock.boundingBox();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height - 12);
    await expect
      .poll(
        async () => {
          await page.mouse.wheel(0, 300);
          return (await geom()).linkInsideBand;
        },
        { timeout: 10_000, message: 'scrolling must bring 組合完整頁面 into the band' }
      )
      .toBe(true);

    // 1280x800 is the stage where the pair stops fitting: the shell used to keep a fixed
    // 240px band there and take it out of ⑤'s canvas.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.waitForLoadState('networkidle');
    await dock.getByTestId('tab-results').click();
    await expect
      .poll(async () => {
        const g = await geom();
        return g.roomy ? g.bandH >= 240 && g.canvasH >= 320 : g.bandH >= 160 && g.canvasH >= 200;
      }, {
        timeout: 10_000,
        message: 'the joint floors must hold at 1280x800 as well',
      })
      .toBe(true);
    await expectFloors(await geom(), '1280x800');

    // At 1024x768 a fixed 240px band can leave ⑤ less than the chart's ~320px, so the floor
    // relaxes by design. Reachability does not relax with it: whatever the band's height, it
    // must clip nothing and stay scrollable to the last control.
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.reload();
    await page.waitForLoadState('networkidle');
    await dock.getByTestId('tab-results').click();
    const shortWindow = await geom();
    await expectFloors(shortWindow, '1024x768');
    const shortBox = await dock.boundingBox();
    await page.mouse.move(shortBox!.x + shortBox!.width / 2, shortBox!.y + shortBox!.height - 12);
    await expect
      .poll(
        async () => {
          await page.mouse.wheel(0, 300);
          return (await geom()).linkInsideBand;
        },
        { timeout: 10_000, message: 'the shortened band must stay scrollable to the same record' }
      )
      .toBe(true);
  });

  test('D-2 persistence: only a real gesture writes, and a resize never ratchets the stored intent', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    const writes = () => mocks.layoutWrites ?? 0;
    const bandH = () =>
      page.evaluate(() => {
        const panel = document
          .querySelector('[data-testid="evidence-dock"]')
          ?.closest('[data-panel-id]') as HTMLElement;
        return panel ? Math.round(panel.getBoundingClientRect().height) : 0;
      });
    const handleBetweenChartAndDock = () =>
      page.evaluate(() => {
        const band = document
          .querySelector('[data-testid="evidence-dock"]')
          ?.closest('[data-panel-id]') as HTMLElement;
        if (!band) return null;
        const b = band.getBoundingClientRect();
        const sep = Array.from(document.querySelectorAll('[role="separator"]')).find(
          (x) => getComputedStyle(x).cursor === 'row-resize' && Math.abs(x.getBoundingClientRect().bottom - b.top) < 24
        );
        if (!sep) return null;
        const r = sep.getBoundingClientRect();
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
      });

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    const atLarge = await bandH();
    expect(atLarge).toBeGreaterThan(100);

    // Mount is not a gesture: the groups report their sizes on the first commit, and those
    // reports used to be written back as user intent.
    await page.waitForTimeout(1_500); // several persist debounces (700ms)
    expect(writes()).toBe(0);

    // shrink and grow again — the clamped presentation must neither be stored nor survive
    const seen: Record<string, number> = {};
    for (const [w, h] of [
      [1024, 768],
      [1440, 900],
      [1280, 800],
      [1440, 900],
    ]) {
      await page.setViewportSize({ width: w, height: h });
      await page.waitForTimeout(1_200);
      seen[`${w}x${h}`] = await bandH();
    }
    expect(writes(), 'a resize must not persist the sizes it forced').toBe(0);
    expect(seen['1440x900'], 'returning to the large window restores the same layout').toBe(atLarge);

    // a window too short for both floors clamps and may even snap the band shut; none of
    // that is intent, and the original proportions have to come back when it grows
    await page.setViewportSize({ width: 1440, height: 620 });
    await page.waitForTimeout(1_200);
    expect(writes(), 'a clamp is not a gesture').toBe(0);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForTimeout(1_500);
    expect(writes()).toBe(0);
    expect(await bandH(), 'the pre-clamp proportions are back, not the clamped ones').toBe(atLarge);

    // a real drag is the only thing that writes, and it writes the dragged slot once
    const pos = await handleBetweenChartAndDock();
    expect(pos, 'the ⑤/⑧ separator must be reachable').not.toBeNull();
    await page.mouse.move(pos!.x, pos!.y);
    await page.mouse.down();
    await page.mouse.move(pos!.x, pos!.y - 90, { steps: 10 });
    await page.mouse.up();
    await expect.poll(writes, { timeout: 10_000 }).toBe(1);
    await page.waitForTimeout(1_200); // let any second write land, if one were going to
    expect(writes(), 'one drag is one PUT, not one per reported size').toBe(1);
    const dragged = await bandH();
    expect(dragged, 'the dock grew upward as dragged').toBeGreaterThan(atLarge);
    const storedAfterDrag = mocks.savedLayout as { panels: Record<string, { size: number; collapsed: boolean }> };
    expect(storedAfterDrag.panels.bottom.size).not.toBe(DEFAULT_LAYOUT.panels.bottom.size);
    expect(storedAfterDrag.panels.left.size).toBe(DEFAULT_LAYOUT.panels.left.size);

    // reload: the stored intent has to be painted, because the panels take uncontrolled
    // defaultSize and a value that arrives after mount is otherwise silently dropped
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect
      .poll(bandH, { timeout: 10_000, message: 'the dragged band must come back after a reload' })
      .toBe(dragged);
    expect(writes()).toBe(1);

    // reset is an explicit action: exactly one PUT of the independent default slots, and
    // nothing that was clamped or hydrated afterwards may resurrect the dragged size
    await page.getByTestId('reset-layout').click();
    await expect.poll(writes, { timeout: 10_000 }).toBe(2);
    const storedAfterReset = mocks.savedLayout as { panels: typeof DEFAULT_LAYOUT.panels };
    for (const key of ['left', 'bottom', 'right', 'rightAI'] as const) {
      expect(storedAfterReset.panels[key], `reset writes ${key}`).toEqual(DEFAULT_LAYOUT.panels[key]);
    }
    expect(await bandH(), 'reset puts the live band back at its default height').toBe(atLarge);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect
      .poll(bandH, { timeout: 10_000, message: 'the reset has to survive a reload' })
      .toBe(atLarge);
    await page.waitForTimeout(1_500);
    expect(writes(), 'neither the reload nor the reset settle writes again').toBe(2);

    // the defaults are a fresh copy, not the shared object: a drag after a reset must not
    // have mutated DEFAULT_LAYOUT itself
    expect(DEFAULT_LAYOUT.panels.bottom.size).not.toBe(dragged);
  });

  test('D-2 intent paths: keyboard resize and 收合 persist, a non-persisted splitter and a stuck pointer do not', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    const writes = () => mocks.layoutWrites ?? 0;
    const bandH = () =>
      page.evaluate(() => {
        const panel = document
          .querySelector('[data-testid="evidence-dock"]')
          ?.closest('[data-panel-id]') as HTMLElement;
        return panel ? Math.round(panel.getBoundingClientRect().height) : 0;
      });
    const stored = () =>
      mocks.savedLayout as { panels: Record<string, { size: number; collapsed: boolean }> };

    /** Centre of a resize handle: by `data-ws-handle` value, or the 詳情/操作 splitter inside
     * ⑦ that deliberately carries no marker. */
    const handleRect = (which: 'dock' | 'unmarked-row-splitter') =>
      page.evaluate((which) => {
        const centre = (el: Element) => {
          const r = el.getBoundingClientRect();
          return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
        };
        const seps = Array.from(document.querySelectorAll('[role="separator"]'));
        if (which === 'dock') {
          const el = seps.find((x) => x.matches('[data-ws-handle="dock"]'));
          return { count: seps.filter((x) => x.matches('[data-ws-handle="dock"]')).length, pos: el ? centre(el) : null };
        }
        // The inner splitter of ⑦'s details/operations group: a row splitter the shell
        // deliberately leaves unmarked, because nothing in that group is persisted.
        const list = seps.filter(
          (x) => !x.hasAttribute('data-ws-handle') && getComputedStyle(x).cursor === 'row-resize'
        );
        return { count: list.length, pos: list.length === 1 ? centre(list[0]) : null };
      }, which);

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1_500);
    expect(writes(), 'mount is still not intent').toBe(0);
    const before = await bandH();

    // ① keyboard resize. 2.1.7 binds Arrow/Home/End on the handle element itself, so a
    // focused handle is resized without a pointer; that is a user action and must persist.
    const dock = page.locator('[data-ws-handle="dock"]');
    await expect(dock, 'the ⑤/⑧ handle must expose the marker the gate matches').toHaveCount(1);
    await dock.focus();
    await page.keyboard.press('ArrowDown');
    await expect.poll(writes, { timeout: 10_000 }).toBe(1);
    await page.waitForTimeout(1_200);
    expect(writes(), 'one key press is one PUT').toBe(1);
    expect(await bandH(), 'the keyboard step moved the band').not.toBe(before);
    expect(stored().panels.bottom.size).not.toBe(DEFAULT_LAYOUT.panels.bottom.size);

    // ② the gate is scoped to this shell's persisted slots. The 詳情/操作 splitter inside ⑦
    // reports sizes for a group nothing stores, so it must not open the gate — otherwise a
    // clamp report racing a drag would be written as intent.
    const inner = await handleRect('unmarked-row-splitter');
    expect(inner.count, '⑦ must expose exactly one unmarked row splitter to test the scope with').toBe(1);
    expect(inner.pos, 'the unmarked splitter must be on screen').not.toBeNull();
    const pos = inner.pos!;
    await page.mouse.move(pos.x, pos.y);
    await page.mouse.down();
    await page.mouse.move(pos.x, pos.y - 40, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(1_500);
    expect(writes(), 'dragging an unmarked splitter must not persist').toBe(1);

    // ③ a pointer that never gets its pointerup still cannot hold the gate open: the
    // window blur releases it, so the clamp that follows a lost focus writes nothing.
    await page.mouse.move(pos.x, pos.y);
    await page.mouse.down();
    await page.evaluate(() => window.dispatchEvent(new Event('blur')));
    await page.mouse.up();
    const beforeClamp = writes();
    await page.setViewportSize({ width: 1440, height: 620 });
    await page.waitForTimeout(1_200);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForTimeout(1_500);
    expect(writes(), 'a released gesture plus a clamp writes nothing').toBe(beforeClamp);

    // ④ an explicit collapse button is a user action, so the flag it changes is stored
    // exactly once — and it is the collapsed field, not a dragged size.
    await page.getByRole('button', { name: '收合詳情與操作' }).click();
    await expect.poll(writes, { timeout: 10_000 }).toBe(beforeClamp + 1);
    expect(stored().panels.right.collapsed, '收合 stores the collapse').toBe(true);
  });

  test('⑧ CSV export is labelled 匯出整個組合 and ignores the scope filter', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await page.getByTestId('tab-journal').click();

    await expect(page.getByTestId('evidence-export-csv')).toHaveText('匯出整個組合（CSV）');
    await expect(page.getByTestId('evidence-export-scope-note')).toContainText('匯出範圍：整個組合');
    await expect(page.getByTestId('evidence-export-scope-note')).toContainText('不受上方「標的範圍」篩選影響');

    // narrow the table to the current instrument first: the download must still
    // be the whole book, which is exactly what the button promises
    await page.getByTestId('evidence-scope-current').click();
    await expect(page.getByTestId('evidence-journal-row-fill-0700')).toHaveCount(0);

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('evidence-export-csv').click(),
    ]);
    expect(download.suggestedFilename()).toBe('trade-journal-pf-1.csv');
    const csv = readFileSync((await download.path()) as string, 'utf8');
    expect(csv.split('\r\n')[0].split(',')).toHaveLength(27);
    expect(csv).toContain('HK:0700');
    expect(csv).toContain('US:AAPL');
  });

  test('⑧ a stored legacy tab key still opens 訂單與成交', async ({ page }) => {
    // Every phase replays a stored payload through `layoutOverride` rather than
    // `savedLayout`: the app PUTs a legitimate floor correction on mount, and the
    // mock would otherwise serve that write-back as the next phase's stored layout.
    const mocks: WorkspaceMocks = {
      savedLayout: null,
      lastIntent: null,
      layoutOverride: { bottomTab: 'fills' },
    };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    // the tab this workspace used to call `fills` is now part of 訂單與成交
    await expect(page.getByTestId('tab-fills')).toHaveCount(0);
    await expect(page.getByTestId('tab-orders-fills')).toHaveClass(/ws-tab-active/);
    await expect(page.getByTestId('tab-orders-fills')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('evidence-tabpanel-orders-fills')).toBeVisible();

    // and the other retired key migrates to the same pane
    mocks.layoutOverride = { bottomTab: 'orders' };
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('tab-orders-fills')).toHaveAttribute('aria-selected', 'true');

    // an unrecognised stored value falls back to the design default, not a blank pane
    mocks.layoutOverride = { bottomTab: 'no-such-tab' };
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('tab-positions')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('evidence-tabpanel-positions')).toBeVisible();

    // a payload that stores only one panel slot still has to come back total: this
    // is the shape that used to crash the shell on `layout.panels.left.size`, and
    // the stored 30% has to win over the 24% default rather than be dropped.
    mocks.layoutOverride = { bottomTab: 'positions', panels: { right: { size: 30 } } };
    mocks.layoutWrites = 0;
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('watchlist-panel')).toBeVisible();
    await expect(page.getByTestId('action-panel')).toBeVisible();
    await expect(page.getByTestId('evidence-dock')).toBeVisible();
    const action = await page.getByTestId('action-panel').boundingBox();
    expect(action?.width).toBeGreaterThan(400);
    await expect(page.getByTestId('tab-positions')).toHaveAttribute('aria-selected', 'true');

    const rightShare = () =>
      page.evaluate(() => {
        const el = document
          .querySelector('[data-testid="action-panel"]')
          ?.closest('[data-panel-id]') as HTMLElement;
        const group = el?.parentElement as HTMLElement;
        if (!el || !group) return null;
        return (el.getBoundingClientRect().width / group.getBoundingClientRect().width) * 100;
      });
    // The stored 30% is painted, not dropped in favour of the 24% default.
    await expect
      .poll(rightShare, { timeout: 10_000, message: 'the stored right slot size must be applied' })
      .toBeGreaterThan(28);

    // Mount is not a gesture. The panels group reports its sizes on the same commit, and
    // those reports used to be persisted as if the user had dragged — which is the ratchet:
    // a window that was briefly narrow rewrote the stored proportions permanently. A mount
    // that only receives size reports must write nothing at all.
    await page.waitForTimeout(1_500); // several persist debounces (700ms)
    expect(mocks.layoutWrites).toBe(0);
  });

  test('AI panel: connection test reports provider status', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('ai-conversation-select')).toContainText('研究：AAPL 動能');
    await page.getByTestId('ai-test-connection').click();
    await expect(page.locator('.ws-ai-conn')).toContainText('模擬供應商連線正常', { timeout: 10_000 });
  });

  test('AI panel: send message renders reply and quota', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('ai-input').fill('AAPL 近期動能如何？');
    await page.getByTestId('ai-send').click();

    await expect(page.getByTestId('ai-thread')).toContainText('AAPL 近期動能如何？', { timeout: 10_000 });
    await expect(page.getByTestId('ai-thread')).toContainText('[symbol:US:AAPL]');
    await expect(page.locator('.ws-ai-quota')).toHaveText('AI 用量：1', { timeout: 10_000 });
  });

  test('AI panel: assistant reply can be saved as pending review draft', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await page.getByTestId('ai-input').fill('請整理動能假說');
    await page.getByTestId('ai-send').click();
    await expect(page.getByTestId('ai-save-draft')).toBeVisible({ timeout: 10_000 });

    await page.getByTestId('ai-save-draft').click();
    await expect(page.getByTestId('ai-draft-form')).toBeVisible();
    await page.getByTestId('ai-draft-form').locator('input').fill('AI 動能假說草稿');
    await page.getByTestId('ai-draft-form').getByRole('button', { name: '建立待審核草稿' }).click();

    await expect(page.getByTestId('ai-thread')).toContainText('已建立待審核草稿 33333333', { timeout: 10_000 });
    await expect(page.getByTestId('ai-thread')).toContainText('待人工審核');
  });
});

/**
 * Context strip semantics — the facts an order depends on must survive the
 * low-frame design: account identity, the three separate mode facts, and the
 * missing-value rules (never a fabricated 0, never a guessed account).
 */
test.describe('Workspace context semantics', () => {
  const REPLAY_SESSION = {
    id: 'sess-1',
    experiment_version_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    owner_id: 'owner-1',
    initial_virtual_cash: 100000,
    started_at: null,
    data_cutoff_at: '2026-06-30',
    current_snapshot_date: BARS[BARS.length - 30].date,
    status: 'active',
    simulation_policy_version: '1.0.0',
    provider_id: 'demo-dataset',
    provider_confirmed: true,
    timeframe: null,
    current_bar_timestamp: null,
    paper_equity: null,
    current_position_json: null,
    warnings: null,
    total_decisions: 0,
    observed_days: 12,
    state_version: 3,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
  };

  function servePortfolios(page: Page, rows: unknown[]) {
    page.route('**/api/v1/paper-portfolio', (route) => json(route, rows));
  }

  function serveOperationalStatus(page: Page, body: unknown, status = 200) {
    page.route('**/api/v1/operational-status', (route) => json(route, body, status));
  }

  function serveLiveBars(page: Page) {
    page.route('**/api/v1/instruments/US:AAPL/bars**', (route) =>
      json(route, {
        ...BARS_RESPONSE_AAPL,
        mode: 'live_realtime',
        mode_label_zh: '即時數據',
        market_status: 'open',
        market_status_label_zh: '交易中',
      })
    );
  }

  const openFormTab = async (page: Page) => {
    await page.getByTestId('action-panel').getByRole('tab', { name: '手動模擬單' }).click();
  };

  test('資料詳情 disclosure is keyboard operable and reports aria state', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    const toggle = page.getByTestId('ctx-details-toggle');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(toggle).toHaveAttribute('aria-controls', 'ctx-details-panel');
    await expect(page.getByTestId('ctx-details-panel')).toHaveCount(0);

    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const panel = page.getByTestId('ctx-details-panel');
    await expect(panel).toBeVisible();
    expect(await panel.getAttribute('id')).toBe('ctx-details-panel');

    await page.keyboard.press('Space');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByTestId('ctx-details-panel')).toHaveCount(0);
  });

  test('disclosure opens on click (not hover) and stays inside a mobile viewport', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await page.setViewportSize({ width: 375, height: 812 });
    const toggle = page.getByTestId('ctx-details-toggle');
    await toggle.hover();
    await expect(page.getByTestId('ctx-details-panel')).toHaveCount(0);

    await toggle.click();
    const panel = page.getByTestId('ctx-details-panel');
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(375 + 1);

    // one disclosure layer only — the panel is not a card wrapped in a card
    await expect(panel.locator('.ctx-details-panel')).toHaveCount(0);
    await expect(page.getByTestId('ctx-system-panel')).toHaveCount(0);
  });

  test('data mode, execution scope and system config stay three separate facts', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    // Live data + paper-only broker: the label must not fuse the two readings.
    serveOperationalStatus(page, {
      feature_switches: {},
      mode: 'test',
      data_mode: 'live',
      broker_mode: 'paper-only',
      ai_mode: 'mock',
    });
    serveLiveBars(page);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('status-data-mode')).toHaveText('Data: Live data');
    await expect(page.getByTestId('status-execution-scope')).toHaveText('Execution: Paper only');
    await expect(page.getByTestId('status-broker-mode')).toHaveText('Broker: Paper only');
    await expect(page.getByTestId('mode-badge')).toContainText('Live data · Paper only');
    await expect(page.getByTestId('mode-badge')).not.toContainText('Live execution');
  });

  test('broker_mode=live is shown as a config reading, never as live execution', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    serveOperationalStatus(page, {
      feature_switches: {},
      mode: 'test',
      data_mode: 'live',
      broker_mode: 'live',
      ai_mode: 'mock',
    });
    serveLiveBars(page);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    // the raw value survives; nothing maps it to a safe-sounding label
    await expect(page.getByTestId('status-broker-mode')).toContainText('live (raw)');
    await expect(page.getByTestId('status-broker-mode')).toHaveClass(/ws-chip-warn/);
    // and it does not promote the execution scope or the badge
    await expect(page.getByTestId('status-execution-scope')).toHaveText('Execution: Paper only');
    await expect(page.getByTestId('mode-badge')).not.toContainText('Live execution');

    await page.getByTestId('ctx-system-toggle').click();
    const note = page.getByTestId('ctx-config-consistency');
    await expect(note).toContainText('mismatch');
    await expect(note).toContainText('unconfirmed');
    await expect(note).toContainText('does not place live orders');
  });

  test('missing current_virtual_cash shows 未提供 instead of a fabricated 0', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    servePortfolios(page, [{ id: 'pf-nocsh', currency: 'USD', status: 'active' }]);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await openFormTab(page);

    const account = page.getByTestId('order-account-context');
    await expect(account).toContainText('pf-nocsh');
    await expect(account).toContainText('虛擬現金');
    await expect(account).toContainText('未提供');
    await expect(account).not.toContainText('0.00');
    await expect(account).not.toContainText(' USD 0');
  });

  test('buying power is 未提供 because no API field carries it', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await openFormTab(page);

    await expect(page.getByTestId('order-buying-power')).toHaveText('未提供');
    await expect(page.getByTestId('order-account-context')).toContainText('不由虛擬現金推算');
  });

  test('no portfolio: strip and form both say 未選擇 rather than inventing one', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    servePortfolios(page, []);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await openFormTab(page);

    await expect(page.getByTestId('ctx-portfolio-identity')).toHaveText('No paper portfolio selected');
    await expect(page.getByTestId('ctx-portfolio-identity')).toHaveAttribute(
      'title',
      /No paper portfolio created yet/
    );
    await expect(page.getByTestId('order-account-context')).toContainText('未選擇模擬組合');
    await expect(page.getByTestId('order-blocked')).toContainText('尚未建立模擬投資組合');
  });

  test('several active portfolios: nothing is auto-chosen, the form stays empty', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    servePortfolios(page, [
      { id: 'pf-alpha', currency: 'USD', current_virtual_cash: 100000, status: 'active' },
      { id: 'pf-beta', currency: 'USD', current_virtual_cash: 50000, status: 'active' },
    ]);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await openFormTab(page);

    // "active" is not a proxy for the account the user wants to operate
    await expect(page.getByTestId('ctx-portfolio-identity')).toHaveText('No paper portfolio selected');
    await expect(page.getByTestId('ctx-portfolio-identity')).toHaveAttribute(
      'title',
      /none was auto-selected/
    );
    await expect(page.locator('#order-portfolio-select')).toHaveValue('');
    await expect(page.getByTestId('order-blocked')).toContainText('不自動代選');
  });

  test('completed portfolio stays viewable in the strip and picker, but cannot be ordered', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    servePortfolios(page, [
      {
        id: 'pf-old',
        currency: 'USD',
        current_virtual_cash: 1234,
        status: 'completed',
      },
    ]);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await openFormTab(page);

    await expect(page.getByTestId('ctx-portfolio-identity')).toContainText('pf-old');
    await expect(page.getByTestId('ctx-portfolio-identity')).toContainText('(Completed)');
    await expect(page.locator('#order-portfolio-select option[value="pf-old"]')).toContainText(
      '已完成'
    );
    await expect(page.getByTestId('order-blocked')).toContainText('均非進行中');
    await expect(page.getByTestId('order-create-portfolio')).toBeVisible();

    // picking it explicitly is allowed for viewing, and the block names the rule
    await page.locator('#order-portfolio-select').selectOption('pf-old');
    await expect(page.getByTestId('order-account-context')).toContainText('pf-old');
    await expect(page.getByTestId('order-blocked')).toContainText('retired/completed 回傳 400');
  });

  test('explicit pick moves the workspace binding, and the chart cursor moves with it', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    servePortfolios(page, [
      {
        id: 'pf-bound',
        currency: 'USD',
        current_virtual_cash: 100000,
        status: 'active',
        simulation_session_id: 'sess-1',
      },
      { id: 'pf-free', currency: 'USD', current_virtual_cash: 8000, status: 'active' },
    ]);
    page.route('**/api/v1/simulations/**', (route) => json(route, REPLAY_SESSION));
    // The bars window is where the cursor actually lands, so record every request.
    const barsEnds: (string | null)[] = [];
    page.on('request', (req) => {
      const url = new URL(req.url());
      if (url.pathname.endsWith('/bars')) barsEnds.push(url.searchParams.get('end_date'));
    });
    const cursor = BARS[BARS.length - 30].date;

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await openFormTab(page);

    // unambiguous replay binding: strip, form and the chart's cut start on pf-bound
    await expect(page.getByTestId('ctx-portfolio-identity')).toContainText('pf-bound');
    await expect(page.locator('#order-portfolio-select')).toHaveValue('pf-bound');
    await expect.poll(() => barsEnds[barsEnds.length - 1]).toBe(cursor);
    await expect(page.getByTestId('ctx-replay-clock')).toBeVisible();

    await page.locator('#order-portfolio-select').selectOption('pf-free');

    // the account moved, so the cursor it never had moves away with it
    await expect(page.getByTestId('ctx-replay-clock')).toHaveCount(0);
    await expect
      .poll(() => barsEnds[barsEnds.length - 1])
      .not.toBe(cursor);
    await expect(page.getByTestId('order-target')).toContainText('受控執行器意向');

    await page.getByTestId('ctx-system-toggle').click();
    await expect(page.getByTestId('ctx-system-panel')).toContainText('Not bound');
    await expect(page.getByTestId('ctx-system-panel')).not.toContainText('sess-1');
  });

  test('⑧ dock never picks an active book for the user, and follows an explicit retired pick', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    servePortfolios(page, [
      { id: 'pf-alpha', currency: 'USD', current_virtual_cash: 100000, status: 'active' },
      { id: 'pf-beta', currency: 'USD', current_virtual_cash: 50000, status: 'active' },
      { id: 'pf-ret', currency: 'USD', current_virtual_cash: 1234, status: 'retired' },
    ]);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    // several orderable books, no replay binding: the dock reports for none of them
    await expect(page.getByTestId('evidence-no-portfolio')).toContainText('不自動選第一個');
    await expect(page.getByTestId('evidence-portfolio-id')).toHaveCount(0);

    await openFormTab(page);
    await page.locator('#order-portfolio-select').selectOption('pf-ret');

    // the retired account is viewable evidence, not a trade-ready context
    await expect(page.getByTestId('evidence-portfolio-id')).toHaveText('pf-ret');
    await expect(page.getByTestId('evidence-resolution')).toContainText(
      '與「手動模擬單」目前所選組合一致'
    );
    await expect(page.getByTestId('evidence-resolution')).toContainText('僅唯讀檢視');
    await expect(page.getByTestId('order-blocked')).toContainText('retired/completed 回傳 400');
  });

  test('a slow earlier book cannot repaint the dock after the user switches', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    servePortfolios(page, [
      { id: 'pf-slow', currency: 'USD', current_virtual_cash: 100000, status: 'active' },
      { id: 'pf-new', currency: 'USD', current_virtual_cash: 5000, status: 'active' },
    ]);

    // The user switches away before pf-slow's order list has answered. The gate
    // holds that one response until the test releases it, so the two books arrive
    // out of order — the race the dock must survive.
    let releaseSlow!: () => void;
    const slowGate = new Promise<void>((resolve) => {
      releaseSlow = resolve;
    });
    let slowRequestSeen!: () => void;
    const slowRequested = new Promise<void>((resolve) => {
      slowRequestSeen = resolve;
    });

    page.route('**/api/v1/paper-portfolio/**', async (route) => {
      const url = new URL(route.request().url());
      const segments = url.pathname.split('/').filter(Boolean);
      if (segments[3] === 'pf-slow' && segments[4] === 'orders') {
        slowRequestSeen();
        await slowGate;
        return json(route, [
          {
            id: 'ord-slow-only',
            portfolio_id: 'pf-slow',
            decision_id: null,
            asset_id: 'US:SLOW',
            side: 'BUY',
            quantity: 1,
            order_type: 'market',
            created_at: '2026-01-05T15:30:00',
            status: 'pending',
            model_or_strategy_version_id: null,
            data_cutoff_at: '2026-01-05',
            rationale: null,
            risk_check_status: 'passed',
            actor_type: 'human',
            idempotency_key: null,
          },
        ]);
      }
      return route.fallback();
    });

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await openFormTab(page);
    await expect(page.getByTestId('evidence-no-portfolio')).toBeVisible();

    await page.locator('#order-portfolio-select').selectOption('pf-slow');
    await slowRequested;

    // The second pick starts its own load while the first is still gated.
    await page.locator('#order-portfolio-select').selectOption('pf-new');
    await expect(page.getByTestId('evidence-portfolio-id')).toHaveText('pf-new');

    releaseSlow();

    // Bounded observation window: once the late pf-slow payload lands, the dock
    // must still speak for pf-new, and pf-slow's own order must never appear.
    for (let i = 0; i < 12; i += 1) {
      await expect(page.getByTestId('evidence-portfolio-id')).toHaveText('pf-new');
      await page.waitForTimeout(50);
    }
    await expect(page.getByTestId('evidence-order-ord-slow-only')).toHaveCount(0);
  });

  test('operational-status failure shows 未提供, not a default mode', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    serveOperationalStatus(page, { detail: 'operational status unavailable' }, 503);
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('status-data-mode')).toHaveText('Data: Not provided');
    await expect(page.getByTestId('status-broker-mode')).toHaveText('Broker: Not provided');
    await expect(page.getByTestId('mode-badge')).not.toContainText('Live execution');

    await page.getByTestId('ctx-system-toggle').click();
    await expect(page.getByTestId('ctx-config-consistency')).toContainText('could not be read');
    await expect(page.getByTestId('ctx-system-panel')).toContainText('AI config');
  });

  test('switching account replaces the workspace context instead of carrying it over', async ({
    page,
  }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);
    // owner-scoped, like GET /paper-portfolio: the token selects whose rows come back
    page.route('**/api/v1/paper-portfolio', (route) => {
      const auth = route.request().headers()['authorization'] ?? '';
      json(
        route,
        auth.includes('user-a')
          ? [{ id: 'pf-usera', currency: 'USD', current_virtual_cash: 1, status: 'active' }]
          : [{ id: 'pf-userb', currency: 'HKD', current_virtual_cash: 2, status: 'active' }]
      );
    });

    await page.evaluate(() => localStorage.setItem('token', 'mock-token-user-a'));
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('ctx-portfolio-identity')).toContainText('pf-usera');

    await page.evaluate(() => localStorage.setItem('token', 'mock-token-user-b'));
    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('ctx-portfolio-identity')).toContainText('pf-userb');
    await expect(page.getByTestId('workspace-context-strip')).not.toContainText('pf-usera');
    await expect(page.getByTestId('order-account-context')).not.toContainText('pf-usera');
  });
});
