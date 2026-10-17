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

import { test, expect, type Page, type Route } from '@playwright/test';

const FRONTEND_URL = 'http://localhost:3001';
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
}

function mockWorkspaceApis(page: Page, state: WorkspaceMocks) {
  page.route('**/api/v1/layouts/workspace', (route) => {
    if (route.request().method() === 'PUT') {
      const body = JSON.parse(route.request().postData() || '{}');
      state.savedLayout = body.layout;
      json(route, { layout: body.layout, updated_at: '2026-10-17T00:00:00Z' });
    } else {
      json(route, { layout: state.savedLayout ?? null, updated_at: null });
    }
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
    await expect(page.getByTestId('status-chips')).toContainText('數據：演示數據');
    await expect(page.getByTestId('status-chips')).toContainText('經紀：僅紙上模擬');
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

  test('watchlist symbol click switches chart', async ({ page }) => {
    const mocks: WorkspaceMocks = { savedLayout: null, lastIntent: null };
    await setupAuth(page);
    mockWorkspaceApis(page, mocks);

    await page.goto(`${FRONTEND_URL}/workspace`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('chart-legend')).toContainText('AAPL');

    await page.locator('.ws-symbol-row', { hasText: '0700' }).click();
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
    await page.getByTestId('tab-fills').click();
    await page.locator('.ws-symbol-row', { hasText: '0700' }).click();
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
      bottomTab: 'fills',
      range: '6M',
    });

    // reload — GET replays the saved layout
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('chart-legend')).toContainText('0700');
    await expect(page.getByTestId('tab-fills')).toHaveClass(/ws-tab-active/);
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
    await expect(page.getByTestId('bottom-tabs')).toContainText('動能策略驗證');

    await page.getByTestId('tab-models').click();
    await expect(page.getByTestId('bottom-tabs')).toContainText('沒有模型');
    await page.getByTestId('tab-orders').click();
    await expect(page.getByTestId('bottom-tabs')).toContainText('沒有執行意向');
    await page.getByTestId('tab-log').click();
    await expect(page.getByTestId('bottom-tabs')).toContainText('沒有系統活動紀錄');
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
