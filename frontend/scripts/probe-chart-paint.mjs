import { chromium } from '@playwright/test';

const BASE = process.argv[2] || 'http://localhost:3099';

function bars() {
  const out = [];
  const t = new Date();
  for (let i = 89; i >= 0; i--) {
    const d = new Date(t.getTime() - i * 86400000);
    const close = 180 + ((i * 7) % 40);
    out.push({ date: d.toISOString().slice(0, 10), open: close - 1, high: close + 2, low: close - 3, close, volume: 1000000 + i });
  }
  return out;
}
const BARS = bars();

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-Hant' });
const page = await context.newPage();
page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE_ERR:', m.text().slice(0, 200)); });
page.on('pageerror', (e) => console.log('PAGE_ERR:', String(e).slice(0, 300)));

await page.route('**/api/**', async (route) => {
  const req = route.request();
  const url = req.url();
  const method = req.method();
  let body = {};
  if (url.includes('layouts/workspace')) body = { layout: null, updated_at: null };
  else if (url.includes('operational-status')) body = { feature_switches: {}, mode: 'test', data_mode: 'demo', broker_mode: 'paper-only', ai_mode: 'mock' };
  else if (url.includes('usage-events/me')) body = [];
  else if (url.includes('/ml/models')) body = { models: [] };
  else if (url.endsWith('/api/v1/experiments/')) body = [];
  else if (url.endsWith('/api/v1/paper-portfolio')) body = [];
  else if (url.includes('/watchlists')) body = { watchlists: [] };
  else if (/instruments\/US:AAPL\/bars/.test(url)) {
    body = {
      instrument_id: 'US:AAPL', symbol: 'AAPL', name: 'Apple Inc.', market: 'US', exchange: 'NASDAQ',
      currency: 'USD', timezone: 'America/New_York', provider: 'Demo', feed_mode: 'historical',
      start_date: BARS[0].date, end_date: BARS[89].date, bar_count: 90, timeframe: '1d',
      mode: 'synthetic_fixture', mode_label_zh: '合成歷史回放', bars: BARS, data_quality: null,
      disclaimer: '', disclaimer_zh: '',
    };
  } else if (/instruments\?/.test(url)) body = { instruments: [], total: 0, data_available: true, provider: 'x' };
  else if (url.includes('ai/status')) body = { configured: false };
  else if (url.includes('/ai/conversations/')) body = { messages: [] };
  else if (url.includes('/ai/conversations')) body = method === 'POST' ? { id: 'c1' } : [];
  else if (url.includes('/instruments/US:AAPL')) {
    body = {
      instrument_id: 'US:AAPL', symbol: 'AAPL', name: 'Apple Inc.', market: 'US', exchange: 'NASDAQ',
      asset_type: 'STOCK', currency: 'USD', timezone: 'America/New_York', lot_size: 1, lot_size_verified: true,
      latest_bar: BARS[89], data_quality: null, data_source: 'demo', feed_mode: 'historical',
      mode: 'synthetic_fixture', mode_label_zh: '合成歷史回放',
    };
  }
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
});

await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
await page.evaluate(() => localStorage.setItem('token', 'probe-token'));
await page.goto(`${BASE}/workspace`, { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(2500);

const report = await page.evaluate(() => {
  const host = document.querySelector('[data-testid="price-chart"]');
  if (!host) return { error: 'no price-chart host' };
  const hr = host.getBoundingClientRect();
  const chain = [];
  let el = host;
  while (el && chain.length < 8) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    chain.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 40),
      testid: el.getAttribute('data-testid'),
      rectH: Math.round(r.height),
      cssHeight: cs.height,
      minHeight: cs.minHeight,
      display: cs.display,
      position: cs.position,
    });
    el = el.parentElement;
  }
  const canvases = [...host.querySelectorAll('canvas')].map((cv) => {
    const b = cv.getBoundingClientRect();
    let painted = null;
    try {
      const ctx = cv.getContext('2d');
      if (ctx && cv.width > 0 && cv.height > 0) {
        const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
        let nz = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) nz++;
        painted = { nonTransparentPixels: nz, total: d.length / 4 };
      }
    } catch (e) {
      painted = { error: e.message };
    }
    return { cssW: Math.round(b.width), cssH: Math.round(b.height), attrW: cv.width, attrH: cv.height, painted };
  });
  return {
    shell: {
      appBar: !!document.querySelector('[data-testid="app-bar"]'),
      contextBar: !!document.querySelector('[data-testid="workspace-context-bar"]'),
    },
    host: { w: Math.round(hr.width), h: Math.round(hr.height) },
    chain,
    canvases,
  };
});

console.log(JSON.stringify(report, null, 1));
await browser.close();
