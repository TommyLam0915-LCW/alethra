/**
 * Visual baseline capture for the workspace redesign.
 *
 * Renders the real product components against deterministic intercepted API
 * responses, at the three acceptance viewports. This is VISUAL evidence only:
 * it proves layout/typography/density behaviour, not backend acceptance.
 *
 * Usage (frontend dir, with a dev server already running):
 *   node scripts/visual-baseline.mjs --base http://localhost:3099 --out /tmp/alethra-visual/before
 *   node scripts/visual-baseline.mjs --long-name        # stress long Chinese instrument names
 */

import { chromium } from '@playwright/test';

const argv = process.argv.slice(2);
const opts = new Map();
for (let i = 0; i < argv.length; i++) {
  if (!argv[i].startsWith('--')) continue;
  const raw = argv[i].slice(2);
  const eq = raw.indexOf('=');
  if (eq >= 0) {
    opts.set(`--${raw.slice(0, eq)}`, raw.slice(eq + 1));
  } else if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) {
    opts.set(`--${raw}`, argv[++i]);
  } else {
    opts.set(`--${raw}`, 'true');
  }
}
const get = (k, d) => (opts.has(k) ? opts.get(k) : d);

const BASE = get('--base', 'http://localhost:3099');
const OUT = get('--out', '/tmp/alethra-visual/before');
const LONG_NAME = get('--long-name', 'false') === 'true';

const VIEWPORTS = [
  { key: '1440', width: 1440, height: 900 },
  { key: '1280', width: 1280, height: 800 },
  { key: '1024', width: 1024, height: 768 },
  { key: '390', width: 390, height: 844 },
];

const NAME = LONG_NAME ? '中國工商銀行股份有限公司滬深港通專戶組合' : 'Apple Inc.';
const NAME_0700 = LONG_NAME ? '騰訊控股與關聯實體長期觀察備忘' : 'Tencent Holdings Ltd.';

function makeBars(count) {
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
const LAST = BARS[BARS.length - 1];

function barsPayload(symbol, name, market, exchange, currency, timezone, lotSize) {
  return {
    instrument_id: symbol,
    symbol: symbol.split(':')[1],
    name,
    market,
    exchange,
    currency,
    timezone,
    provider: 'Demo Provider',
    feed_mode: 'historical',
    start_date: BARS[0].date,
    end_date: LAST.date,
    bar_count: BARS.length,
    timeframe: '1d',
    mode: 'synthetic_fixture',
    mode_label_zh: '合成歷史回放 · 非真實行情',
    data_nature: 'synthetic',
    is_realtime_capable: false,
    data_as_of: LAST.date,
    timestamp_semantics: 'bar_close_exchange_local',
    market_status: 'closed',
    market_status_label_zh: '已收市',
    bars: BARS,
    data_quality: {
      data_nature: 'synthetic',
      adjustment: 'not_applicable',
      timezone,
      note_zh: '模擬數據',
      note: 'synthetic demo fixture',
      latest_bar_date: LAST.date,
      bar_age_days: 0,
      is_stale: false,
      stale_threshold_days: 5,
    },
    disclaimer: 'Synthetic demo data.',
    disclaimer_zh: '本系統數據為模擬用途，非即時行情，不進行實盤交易。',
  };
}

function detailPayload(symbol, name, market, exchange, currency, timezone, lotSize) {
  return {
    instrument_id: symbol,
    symbol: symbol.split(':')[1],
    name,
    market,
    exchange,
    asset_type: 'STOCK',
    currency,
    timezone,
    lot_size: lotSize,
    lot_size_verified: true,
    latest_bar: LAST,
    data_quality: barsPayload(symbol, name, market, exchange, currency, timezone, lotSize).data_quality,
    data_source: 'demo',
    feed_mode: 'historical',
    mode: 'synthetic_fixture',
    mode_label_zh: '合成歷史回放 · 非真實行情',
    market_status: 'closed',
    market_status_label_zh: '已收市',
    data_as_of: LAST.date,
  };
}

const AAPL_BARS = barsPayload('US:AAPL', NAME, 'US', 'NASDAQ', 'USD', 'America/New_York', 1);
const AAPL_DETAIL = detailPayload('US:AAPL', NAME, 'US', 'NASDAQ', 'USD', 'America/New_York', 1);
const HK_BARS = barsPayload('HK:0700', NAME_0700, 'HK', 'HKEX', 'HKD', 'Asia/Hong_Kong', 100);
const HK_DETAIL = detailPayload('HK:0700', NAME_0700, 'HK', 'HKEX', 'HKD', 'Asia/Hong_Kong', 100);

const WATCHLISTS = {
  watchlists: [
    {
      id: 'wl-1',
      name: '主要觀察',
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-17T00:00:00Z',
      items: [
        { symbol: 'US:AAPL', position: 0, added_at: '2026-10-01T00:00:00Z' },
        { symbol: 'HK:0700', position: 1, added_at: '2026-10-02T00:00:00Z' },
      ],
    },
  ],
};

const PORTFOLIOS = [
  {
    id: 'pf-00000000-0000-0000-0000-000000000001',
    owner_id: 'u-1',
    simulation_session_id: 'ss-00000000-0000-0000-0000-000000000001',
    currency: 'USD',
    initial_virtual_cash: 100000,
    current_virtual_cash: 98420.55,
    status: 'active',
    created_at: '2026-10-10T00:00:00Z',
    closed_at: null,
    total_realized_pnl: -1579.45,
    total_unrealized_pnl: null,
    valuation: null,
  },
];

function respond(url, method) {
  const p = new URL(url).pathname;
  if (p.endsWith('/layouts/workspace')) return { layout: null, updated_at: null };
  if (p === '/api/v1/operational-status') {
    return { feature_switches: {}, mode: 'test', data_mode: 'demo', broker_mode: 'paper-only', ai_mode: 'mock' };
  }
  if (p === '/api/v1/pilot/usage-events/me') return [];
  if (p === '/api/v1/ml/models') return { models: [] };
  if (p === '/api/v1/experiments/') return [];
  if (p === '/api/v1/paper-portfolio') return PORTFOLIOS;
  if (/\/paper-portfolio\/[^/]+\/positions/.test(p)) {
    return [
      {
        id: 'pos-1',
        asset_id: 'US:AAPL',
        quantity: 120,
        average_price: 181.4,
        status: 'open',
        unrealized_pnl: null,
      },
    ];
  }
  if (/\/paper-portfolio\/[^/]+\/(orders|decisions|journal)/.test(p)) return [];
  if (p === '/api/v1/watchlists') return WATCHLISTS;
  if (p.startsWith('/api/v1/watchlists/')) return WATCHLISTS;
  if (/\/instruments\/US:AAPL\/bars/.test(p)) return AAPL_BARS;
  if (/\/instruments\/HK:0700\/bars/.test(p)) return HK_BARS;
  if (/\/instruments\/US:AAPL$/.test(p)) return AAPL_DETAIL;
  if (/\/instruments\/HK:0700$/.test(p)) return HK_DETAIL;
  if (/\/instruments\?/.test(url)) {
    return { instruments: [AAPL_DETAIL, HK_DETAIL], total: 2, data_available: true, provider: 'Demo Provider' };
  }
  if (p === '/api/v1/execution/intents' || p.startsWith('/api/v1/execution/intents')) {
    if (method === 'POST') {
      return {
        intent_id: 'intent-visual-1',
        portfolio_id: PORTFOLIOS[0].id,
        symbol: 'US:AAPL',
        side: 'BUY',
        quantity: 10,
        order_type: 'MARKET',
        limit_price: null,
        status: 'pending',
        authorization_token: 'visual-token',
        created_at: '2026-10-17T00:00:00Z',
        updated_at: '2026-10-17T00:00:00Z',
      };
    }
    return [];
  }
  if (p === '/api/v1/ai/status') return { configured: false, provider: 'mock', model: 'mock-model' };
  if (p === '/api/v1/ai/test-connection') return { ok: true, detail: '模擬供應商連線正常', latency_ms: 1, kind: null, provider: 'mock' };
  if (p === '/api/v1/ai/conversations') return method === 'POST' ? { id: 'c-1', title: '工作台對話', created_at: '2026-10-17T00:00:00Z' } : [];
  if (p.startsWith('/api/v1/ai/conversations/')) return { messages: [] };
  return null;
}

const browser = await chromium.launch();
const unmocked = new Set();

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    locale: 'zh-Hant',
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const body = respond(req.url(), req.method());
    if (body === null) {
      unmocked.add(`${req.method()} ${new URL(req.url()).pathname}`);
      await route.fulfill({ status: 200, contentType: 'application/json', body: 'null' });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.setItem('token', 'visual-baseline-token'));
  await page.goto(`${BASE}/workspace`, { waitUntil: 'networkidle', timeout: 60_000 });
  await page.waitForSelector('[data-testid="price-chart"] canvas', { timeout: 30_000 }).catch(() => {});
  await page.waitForTimeout(600);
  const file = `${OUT}/workspace-${vp.key}.png`;
  await page.screenshot({ path: file, fullPage: false });
  console.log(`SHOT ${file}`);

  const measure = () =>
    page.evaluate(() => {
      const panel = document.querySelector('[data-testid="watchlist-panel"]');
      const toggle = document.querySelector('[data-testid="mobile-watchlist-toggle"]');
      const labels = [...document.querySelectorAll('.ws-symbol-label')].map((el) => ({
        text: el.textContent,
        clipped: el.scrollWidth > el.clientWidth + 1,
        widthPx: Math.round(el.getBoundingClientRect().width),
      }));
      const overflowing = [...document.querySelectorAll('.ws-main *, .ws-contextbar *')]
        .filter((el) => {
          if (el.scrollWidth <= el.clientWidth + 2) return false;
          const ox = getComputedStyle(el).overflowX;
          if (ox === 'auto' || ox === 'scroll') return false;
          return el.getBoundingClientRect().width > 0;
        })
        .slice(0, 12)
        .map((el) => ({
          cls: (typeof el.className === 'string' ? el.className : el.tagName).slice(0, 48),
          scrollWidth: el.scrollWidth,
          clientWidth: el.clientWidth,
        }));
      return {
        toggleVisible: !!toggle && getComputedStyle(toggle).display !== 'none',
        watchlistVisible: !!panel && panel.getBoundingClientRect().width > 0,
        watchlistWidthPx: panel ? Math.round(panel.getBoundingClientRect().width) : null,
        labels,
        overflowing,
        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      };
    });

  let metrics = await measure();
  console.log(`METRICS ${vp.key} initial ${JSON.stringify(metrics)}`);
  let openedDrawer = false;
  if (metrics.toggleVisible && !metrics.watchlistVisible) {
    await page.getByTestId('mobile-watchlist-toggle').click();
    await page.waitForTimeout(300);
    openedDrawer = true;
    metrics = await measure();
    await page.screenshot({ path: `${OUT}/workspace-${vp.key}-drawer.png` });
  }
  console.log(
    `METRICS ${vp.key} ${JSON.stringify({ ...metrics, openedDrawer, innerWidth: await page.evaluate(() => window.innerWidth) })}`
  );

  await context.close();
}

await browser.close();
if (unmocked.size) console.log(`UNMOCKED(${unmocked.size}):\n  ${[...unmocked].join('\n  ')}`);
else console.log('UNMOCKED: none');
