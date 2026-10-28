import { randomBytes } from 'crypto';
import { test, expect, type Page, type Response } from '@playwright/test';

/**
 * Tracked regression for two things Issue #26 calls out explicitly:
 *
 *  1. the price view must state what kind of data it is showing (symbol, market,
 *     currency, exchange timezone, timeframe, provider, market timestamp, receive
 *     time, mode label and market status) and must never dress a synthetic or
 *     historical series up as a live quote;
 *  2. a declared risk plan (stop / target) must travel from the new-experiment form
 *     into the locked version spec, come back as per-trade planned risk and realised
 *     R in the journal, and reconcile against the equity curve - while a run that
 *     declares no plan must show "cannot compute" instead of a fabricated 0.
 *
 * Runs against the disposable E2E runtime (frontend 127.0.0.1:3099, backend
 * 127.0.0.1:8099, PostgreSQL database alethra_e2e). Every user-facing step below is
 * a real click / fill / select on the rendered control; the network payloads are only
 * read to cross-check what the UI claims. Waits use waitForResponse / waitForURL,
 * never arbitrary delays.
 *
 * The account is registered inside the test with a generated one-off credential so
 * that the run quota starts fresh and no long-lived secret is stored anywhere. That
 * credential is never printed, and the runner config keeps traces off for this spec
 * so it cannot end up in an artifact.
 */

const FRONTEND_BASE = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3099';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const account = {
  email: `e2e-risk-${randomBytes(6).toString('hex')}@alethra.io`,
  password: `E2e${randomBytes(9).toString('base64url')}!7`,
  displayName: `E2E 風險計劃 ${randomBytes(3).toString('hex')}`,
};

const ASSET = 'US:AAPL';
const START_DATE = '2024-01-01';
const END_DATE = '2024-03-01';
const DECLARED_PLAN = { stop_pct: 0.05, target_pct: 0.1 };

const journey = {
  plannedExperimentId: '',
  plannedRunId: '',
  unplannedExperimentId: '',
  unplannedRunId: '',
};

interface JournalRow {
  side: string;
  stop: string;
  target: string;
  plannedRisk: string;
  realizedR: string;
  exitReason: string;
}

interface RunRecord {
  id: string;
  status: string;
  metrics?: Record<string, unknown>;
  result_json?: {
    transactions?: Array<{
      side?: string;
      quantity?: number;
      execution_price_before_costs?: number;
      stop_price?: number | null;
      target_price?: number | null;
      planned_risk?: number | null;
      realized_r?: number | null;
      actor?: string;
      exit_reason?: string | null;
    }>;
    round_trips?: unknown[];
    data_source_used?: string;
  };
}

// Mirrors the run-detail page's number rendering so the spec can compare the text
// a user reads against the number that was actually persisted.
function renderNumber(value: number): string {
  return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

function metricValue(metrics: Record<string, unknown>, key: string): unknown {
  const raw = metrics[key];
  if (raw !== null && typeof raw === 'object' && 'value' in (raw as Record<string, unknown>)) {
    return (raw as { value: unknown }).value;
  }
  return raw;
}

// Mirrors EXIT_REASON_LABELS in the run detail page; the spec asserts the label
// the user sees, not the raw ledger enum.
const EXIT_REASON_LABELS: Record<string, string> = {
  stop: '停損觸發',
  target: '停利觸發',
  time: '持有期滿',
  signal: '策略訊號',
  end_of_data: '資料結束強制平倉',
};

async function readJournalRows(table: ReturnType<Page['getByTestId']>): Promise<JournalRow[]> {
  const bodyRows = table.locator('tbody tr');
  const count = await bodyRows.count();
  const rows: JournalRow[] = [];
  for (let index = 0; index < count; index += 1) {
    const cells = bodyRows.nth(index).locator('td');
    rows.push({
      // Column order is fixed by the page: 日期 方向 數量 成交價 手續費 止損 目標 計劃風險 實現盈虧 實現 R 出場原因 來源 Config
      side: (await cells.nth(1).innerText()).trim(),
      stop: (await cells.nth(5).innerText()).trim(),
      target: (await cells.nth(6).innerText()).trim(),
      plannedRisk: (await cells.nth(7).innerText()).trim(),
      realizedR: (await cells.nth(9).innerText()).trim(),
      exitReason: (await cells.nth(10).innerText()).trim(),
    });
  }
  return rows;
}

async function createExperimentAndRun(
  page: Page,
  options: { title: string; declarePlan: boolean }
): Promise<{
  experimentId: string;
  runId: string;
  specRiskParameters: unknown;
  runPayload: RunRecord;
  storedRun: RunRecord;
}> {
  await page.goto(`${FRONTEND_BASE}/experiments/new`);
  await page.getByTestId('exp-title').fill(options.title);

  const asset = page.getByTestId('exp-asset');
  await expect(asset.locator(`option[value="${ASSET}"]`), 'real US:AAPL instrument must be selectable').toHaveCount(1);
  await asset.selectOption(ASSET);

  const template = page.locator('select').filter({ has: page.locator('option[value="sma_crossover"]') }).first();
  await template.selectOption('sma_crossover');
  await page.getByTestId('sma-short-window').fill('3');
  await page.getByTestId('sma-long-window').fill('8');

  await page.getByTestId('exp-start-date').fill(START_DATE);
  await page.getByTestId('exp-end-date').fill(END_DATE);

  if (options.declarePlan) {
    await page.getByTestId('risk-stop-pct').fill(String(DECLARED_PLAN.stop_pct));
    await page.getByTestId('risk-target-pct').fill(String(DECLARED_PLAN.target_pct));
  } else {
    await expect(page.getByTestId('risk-stop-pct')).toHaveValue('');
    await expect(page.getByTestId('risk-target-pct')).toHaveValue('');
  }

  const [createResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/experiments/') && response.request().method() === 'POST'),
    page.getByRole('button', { name: '建立實驗' }).click(),
  ]);
  expect(createResponse.status(), 'experiment creation must succeed').toBeLessThan(300);
  const created = (await createResponse.json()) as { id: string };
  const requestBody = createResponse.request().postDataJSON() as {
    strategy_spec?: { risk_parameters?: unknown; commission_rate?: number; slippage_rate?: number };
  };

  await page.waitForURL(`**/experiments/${created.id}`);

  const [runResponse] = await Promise.all([
    page.waitForResponse((response) => /\/api\/v1\/versions\/[^/]+\/runs/.test(response.url()) && response.request().method() === 'POST'),
    page.getByRole('button', { name: '執行回測' }).click(),
  ]);
  expect(runResponse.status(), 'run creation must succeed').toBeLessThan(300);
  const runPayload = (await runResponse.json()) as RunRecord;
  expect(runPayload.status, 'the runtime must finish the run synchronously, otherwise this spec would need polling').toBe('completed');

  const detailLink = page.getByTestId(`run-detail-link-${runPayload.id}`);
  await expect(detailLink, 'the new run must appear in 執行歷史').toHaveCount(1);
  const [storedResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes(`/api/v1/runs/${runPayload.id}`) && response.request().method() === 'GET'),
    detailLink.click(),
  ]);
  expect(storedResponse.status(), 'the run must be readable back from storage').toBe(200);
  const storedRun = (await storedResponse.json()) as RunRecord;
  await page.waitForURL(`**/runs/${runPayload.id}`);
  await expect(page.getByTestId('run-detail-page')).toBeVisible();

  return {
    experimentId: created.id,
    runId: runPayload.id,
    specRiskParameters: requestBody.strategy_spec?.risk_parameters,
    runPayload,
    storedRun,
  };
}

test.describe('risk plan journal and honest price view', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();

    await page.goto(`${FRONTEND_BASE}/login`);
    await page.getByRole('button', { name: '沒有帳號？註冊' }).click();
    await page.locator('input[type="text"]').first().fill(account.displayName);
    await page.locator('input[type="email"]').fill(account.email);
    await page.locator('input[type="password"]').fill(account.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test.afterAll(async () => {
    // Every failure path must release the browser context; Playwright closes the
    // browser itself, so closing the page here is enough to drop the session.
    if (page) await page.close();
  });

  test('a freshly registered account starts isolated, with its own quota and nobody else\'s experiments', async () => {
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.locator('.dash-empty-state'), 'a new account has no research history yet').toBeVisible();
    await expect(page.locator('.dash-experiment-item')).toHaveCount(0);
    await expect(
      page.getByText('E2E 合成研究實驗（僅供自動化測試）'),
      'the seeded account\'s experiment must not leak into a new account'
    ).toHaveCount(0);
  });

  test('the price view states symbol, currency, timeframe, exchange timezone, provider, timestamps, mode and market status', async () => {
    await page.goto(`${FRONTEND_BASE}/workspace`);
    const rangeButtons = page.getByTestId('chart-range-buttons');
    await expect(rangeButtons).toBeVisible();
    await expect(page.getByTestId('chart-meta-line')).toBeVisible();

    // The workspace applies DEFAULT_LAYOUT, then any cached layout, then the
    // server-stored layout, so a /bars request can still be in flight when a
    // listener is attached. Record every bars URL seen from navigation onward
    // and only accept a response the click actually produced. The bars route
    // takes start_date/end_date, and rangeToStart('ALL') is the 1900-01-01
    // sentinel, so the ALL request is unambiguously identifiable.
    const isBars = (url: string) =>
      url.includes('/api/v1/instruments/') && url.includes('/bars');
    const seenBarsUrls = new Set<string>();
    page.on('response', (response) => {
      if (isBars(response.url())) seenBarsUrls.add(response.url());
    });
    await expect(page.getByTestId('chart-meta-line')).toBeVisible();
    const urlsBeforeWarmup = new Set(seenBarsUrls);

    const [warmupResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          isBars(response.url()) &&
          response.request().method() === 'GET' &&
          !response.url().includes('start_date=1900-01-01') &&
          !urlsBeforeWarmup.has(response.url())
      ),
      rangeButtons.getByRole('button', { name: '6M' }).click(),
    ]);
    expect(warmupResponse.status(), 'the warm-up range request must succeed').toBe(200);
    const warmupPayload = (await warmupResponse.json()) as { bar_count: number };
    await expect(
      rangeButtons.getByRole('button', { name: '6M' }),
      'a stored layout resolving late must not revert the range the user just clicked'
    ).toHaveClass(/ws-range-btn-active/);
    await expect(page.getByTestId('chart-meta-line')).toContainText(`${warmupPayload.bar_count} 條`);

    const [barsResponse] = await Promise.all([
      page.waitForResponse(
        (response) =>
          isBars(response.url()) &&
          response.request().method() === 'GET' &&
          response.url().includes('start_date=1900-01-01')
      ),
      rangeButtons.getByRole('button', { name: '全部' }).click(),
    ]);
    expect(barsResponse.status(), 'bars request must succeed').toBe(200);
    await expect(
      rangeButtons.getByRole('button', { name: '全部' }),
      'the clicked range must become the active one'
    ).toHaveClass(/ws-range-btn-active/);
    const payload = (await barsResponse.json()) as {
      symbol: string;
      name: string;
      market: string;
      exchange: string;
      currency: string;
      timezone: string;
      provider: string;
      feed_mode: string;
      is_realtime_capable: boolean;
      mode: string;
      mode_label_zh: string;
      market_status: string;
      market_status_label_zh: string;
      timeframe: string;
      bar_count: number;
      data_as_of: string;
      series_received_at: string;
      exchange_local_time: string;
      data_quality: { data_nature: string; is_stale: boolean; adjustment: string };
      bars: Array<{ date: string }>;
    };

    // The runtime must not claim a live feed it does not have.
    expect(payload.is_realtime_capable, 'this runtime has no realtime-capable provider, so the payload must say so').toBe(false);
    expect(payload.feed_mode, 'feed_mode must be an offline/cached mode, not live').not.toBe('live');
    expect(payload.mode, 'mode must not be reported as live_realtime').not.toBe('live_realtime');
    expect(payload.mode_label_zh, 'a Chinese mode label must be supplied').not.toBe('');
    expect(payload.market_status_label_zh, 'a Chinese market-status label must be supplied').not.toBe('');

    const legend = page.getByTestId('chart-legend');
    await expect(legend).toContainText(`${payload.symbol} · ${payload.name}`);
    await expect(legend).toContainText(payload.currency);
    await expect(page.getByTestId('chart-mode-badge')).toHaveText(payload.mode_label_zh);
    await expect(page.getByTestId('chart-market-status-badge')).toHaveText(payload.market_status_label_zh);

    if (payload.data_quality.data_nature === 'synthetic') {
      await expect(page.getByTestId('chart-nature-badge'), 'a synthetic series must be labelled synthetic').toHaveText('模擬數據');
    }
    const staleBadge = page.getByTestId('chart-stale-badge');
    if (payload.data_quality.is_stale) {
      await expect(staleBadge, 'a stale series must carry the stale badge').toHaveText('陳舊');
    } else {
      await expect(staleBadge).toHaveCount(0);
    }

    const meta = page.getByTestId('chart-meta-line');
    await expect(meta).toContainText(`${payload.bar_count} 條`);
    await expect(meta).toContainText(payload.timeframe || '1d');
    await expect(meta).toContainText(`${payload.exchange}（${payload.timezone}）`);
    await expect(meta).toContainText(`供應商 ${payload.provider}`);
    if (payload.data_quality.adjustment) {
      await expect(meta, 'the adjustment status must carry a Chinese label').toContainText('復權');
      await expect(
        meta,
        'an internal enum token must not be printed to the user as-is'
      ).not.toContainText(payload.data_quality.adjustment);
    }
    await expect(meta).toContainText(`數據截至 ${payload.data_as_of || payload.bars[payload.bars.length - 1].date}`);
    await expect(meta).toContainText(`擷取於 ${payload.series_received_at.slice(11, 16)} UTC`);
    expect(payload.bars.length, 'the rendered bar count must match the series actually returned').toBe(payload.bar_count);
  });

  test('a declared stop/target plan reaches the locked version spec and comes back as per-trade planned risk and realised R', async () => {
    const result = await createExperimentAndRun(page, {
      title: `E2E 風險計劃（已宣稱停損） ${stamp}`,
      declarePlan: true,
    });
    journey.plannedExperimentId = result.experimentId;
    journey.plannedRunId = result.runId;

    expect(result.specRiskParameters, 'the POST body must carry the plan the user typed').toEqual(DECLARED_PLAN);

    const stored = result.storedRun;
    const metrics = stored.metrics ?? {};
    const storedFills = stored.result_json?.transactions ?? [];
    expect(storedFills.length, 'the stored run must carry a fill journal').toBeGreaterThan(0);

    const closedTrades = metricValue(metrics, 'closed_trade_count');
    expect(typeof closedTrades, 'closed_trade_count must be recorded as a number').toBe('number');
    expect(closedTrades as number, 'at least one round trip must exist').toBeGreaterThan(0);
    await expect(page.getByTestId('metric-closed_trade_count')).toHaveText(renderNumber(closedTrades as number));

    const netPnl = metricValue(metrics, 'net_pnl');
    expect(typeof netPnl, 'net_pnl must be recorded as a number').toBe('number');
    await expect(page.getByTestId('metric-net_pnl')).toHaveText(renderNumber(netPnl as number));

    const averageR = metricValue(metrics, 'average_realized_r');
    expect(typeof averageR, 'a declared plan must make average realised R computable').toBe('number');
    await expect(page.getByTestId('metric-average_realized_r')).toHaveText(renderNumber(averageR as number));

    const crossCheck = metrics.pnl_cross_check as
      | { consistent?: boolean; equity_net_pnl?: number; realized_net_pnl?: number }
      | undefined;
    expect(crossCheck?.consistent, 'the stored cross-check must report consistency').toBe(true);
    expect(
      Math.abs((crossCheck?.equity_net_pnl ?? Number.NaN) - (crossCheck?.realized_net_pnl ?? Number.NaN)),
      'equity P&L and FIFO realised P&L must agree in storage, not only on screen'
    ).toBeLessThan(0.01);
    await expect(page.getByTestId('pnl-cross-check-result')).toContainText('兩邊吻合');

    const journal = page.getByTestId('journal-table');
    await expect(journal).toBeVisible();
    const rows = await readJournalRows(journal);
    expect(rows.length, 'the journal must render one row per stored fill').toBe(storedFills.length);

    for (let index = 0; index < storedFills.length; index += 1) {
      const fill = storedFills[index];
      const rendered = rows[index];
      const isBuy = String(fill.side).toUpperCase().includes('BUY');

      expect(rendered.side, 'the rendered direction must be the stored one').toBe(String(fill.side));
      expect(rendered.stop, 'the rendered stop must be the stored one').toBe(
        fill.stop_price == null ? '—' : renderNumber(fill.stop_price)
      );
      expect(rendered.plannedRisk, 'the rendered planned risk must be the stored one').toBe(
        fill.planned_risk == null ? '—' : renderNumber(fill.planned_risk)
      );
      expect(rendered.realizedR, 'the rendered realised R must be the stored one').toBe(
        fill.realized_r == null ? '—' : renderNumber(fill.realized_r)
      );
      await expect(page.getByTestId(`journal-actor-${index}`), 'every fill must name its actor').toHaveText(String(fill.actor));

      if (isBuy) {
        const entry = Number(fill.execution_price_before_costs);
        expect(entry, 'an entry fill must carry its execution price').toBeGreaterThan(0);
        expect(fill.exit_reason, 'a buy has not exited, so it must not carry an exit reason').toBeNull();
        expect(rendered.exitReason, 'the journal must show a missing exit reason as an em dash').toBe('—');
        expect(fill.stop_price, 'the declared stop must produce a stored level').not.toBeNull();
        expect(fill.target_price, 'the declared target must produce a stored level').not.toBeNull();
        expect(fill.stop_price as number, 'stop_pct 0.05 must actually set the level').toBeCloseTo(entry * (1 - DECLARED_PLAN.stop_pct), 2);
        expect(fill.target_price as number, 'target_pct 0.1 must actually set the level').toBeCloseTo(entry * (1 + DECLARED_PLAN.target_pct), 2);
        expect(fill.planned_risk as number, 'planned risk must be (entry - stop) * quantity').toBeCloseTo(
          (entry - (fill.stop_price as number)) * Number(fill.quantity),
          2
        );
        expect(fill.planned_risk as number, 'planned risk must be positive, never a fabricated zero').toBeGreaterThan(0);
      } else {
        expect(fill.realized_r, 'every exit must carry a realised R once a plan exists').not.toBeNull();
        expect(
          EXIT_REASON_LABELS[String(fill.exit_reason)],
          `every exit must name a known reason, got ${String(fill.exit_reason)}`
        ).toBeDefined();
        expect(
          rendered.exitReason,
          'the rendered exit reason must be the label for the stored reason, not a placeholder'
        ).toBe(EXIT_REASON_LABELS[String(fill.exit_reason)]);
      }
    }

    const roundTrips = page.getByTestId('round-trip-table');
    await expect(roundTrips, 'FIFO pairings must be listed').toBeVisible();
    await expect(roundTrips.locator('tbody tr')).toHaveCount(stored.result_json?.round_trips?.length ?? 0);
    expect(stored.result_json?.round_trips?.length, 'FIFO pairings must match the closed trade count').toBe(closedTrades as number);

    await expect(page.getByTestId('weekday-breakdown'), 'exchange-local weekday attribution must render').toBeVisible();
    await expect(page.getByTestId('time-of-day-unavailable'), 'the intraday gap must be disclosed, not silently omitted').toBeVisible();
  });

  test('a run that declares no plan reports planned risk as unavailable instead of zero', async () => {
    const result = await createExperimentAndRun(page, {
      title: `E2E 風險計劃（未宣稱停損） ${stamp}`,
      declarePlan: false,
    });
    journey.unplannedExperimentId = result.experimentId;
    journey.unplannedRunId = result.runId;

    const declared = result.specRiskParameters as Record<string, unknown> | undefined;
    expect(
      declared === undefined || Object.keys(declared).length === 0,
      'leaving the plan blank must send no plan at all, never a zero-filled one'
    ).toBe(true);

    const stored = result.storedRun;
    const metrics = stored.metrics ?? {};
    const storedFills = stored.result_json?.transactions ?? [];
    expect(storedFills.length, 'the run must still have fills to inspect').toBeGreaterThan(0);

    await expect(page.getByTestId('pnl-cross-check-result'), 'cash accounting must reconcile independently of the risk plan').toContainText(
      '兩邊吻合'
    );
    const crossCheck = metrics.pnl_cross_check as { consistent?: boolean } | undefined;
    expect(crossCheck?.consistent, 'the stored cross-check must still report consistency').toBe(true);

    const averageCell = page.getByTestId('metric-average_realized_r');
    const storedAverage = metrics.average_realized_r as { value?: unknown; reason?: string } | number | null | undefined;
    expect(storedAverage, 'average realised R must be recorded').not.toBeUndefined();
    expect(metricValue(metrics, 'average_realized_r'), 'an undefined R must be stored as null, never as 0').toBeNull();
    await expect(averageCell).toHaveText('未計算');
    const reason = (typeof storedAverage === 'object' && storedAverage !== null ? storedAverage.reason : undefined) ?? '';
    expect(reason.length, 'the engine must explain why R is undefined').toBeGreaterThan(0);
    await expect(
      averageCell.locator('xpath=..'),
      'the explanation must reach the user, not stay in the payload'
    ).toContainText(reason.slice(0, 40));

    const rows = await readJournalRows(page.getByTestId('journal-table'));
    expect(rows.length, 'the journal must render one row per stored fill').toBe(storedFills.length);
    for (let index = 0; index < storedFills.length; index += 1) {
      const fill = storedFills[index];
      expect(fill.stop_price, 'no stop was declared, so none may be invented').toBeNull();
      expect(fill.planned_risk, 'planned risk must stay null, never 0').toBeNull();
      expect(fill.realized_r, 'realised R must stay null without a risk unit').toBeNull();
      expect(rows[index].stop, 'the journal must show absence as an em dash').toBe('—');
      expect(rows[index].plannedRisk, 'the journal must show absence as an em dash').toBe('—');
      expect(rows[index].realizedR, 'the journal must show absence as an em dash').toBe('—');

      const isBuy = String(fill.side).toUpperCase().includes('BUY');
      if (isBuy) {
        expect(rows[index].exitReason, 'an entry has not exited').toBe('—');
      } else {
        // With no declared stop/target nothing can fire one, so the exit may only be
        // the strategy's own rule (signal) or the end-of-data liquidation. Asserting
        // "not a stop/target exit" — rather than one specific reason — is the control
        // that proves a stop exit in the planned run came from the plan and not a default,
        // without over-specifying which honest non-plan reason the strategy reached.
        const reason = String(fill.exit_reason);
        expect(['stop', 'target'], 'an undeclared plan must not produce a stop or target exit').not.toContain(reason);
        expect(EXIT_REASON_LABELS[reason], `every exit must name a known reason, got ${reason}`).toBeDefined();
        expect(rows[index].exitReason, 'the journal must render the label for the stored reason').toBe(
          EXIT_REASON_LABELS[reason]
        );
      }
    }
  });

  test('the two runs stay separately addressable so the comparison is reproducible', async () => {
    expect(journey.plannedRunId).not.toBe('');
    expect(journey.unplannedRunId).not.toBe(journey.plannedRunId);

    await page.goto(`${FRONTEND_BASE}/experiments/${journey.plannedExperimentId}/runs/${journey.plannedRunId}`);
    await expect(page.getByTestId('run-detail-page')).toContainText(journey.plannedRunId.slice(0, 8));
    await expect(page.getByTestId('pnl-cross-check-result')).toContainText('兩邊吻合');
    await expect(page.getByTestId('metric-average_realized_r')).toHaveText(/^-?\d+(\.\d+)?$/);

    await page.goto(`${FRONTEND_BASE}/experiments/${journey.unplannedExperimentId}/runs/${journey.unplannedRunId}`);
    await expect(page.getByTestId('run-detail-page')).toContainText(journey.unplannedRunId.slice(0, 8));
    await expect(page.getByTestId('pnl-cross-check-result')).toContainText('兩邊吻合');
    await expect(page.getByTestId('metric-average_realized_r')).toHaveText('未計算');
  });

  test('the run detail discloses the exit policy it actually executed', async () => {
    expect(journey.plannedRunId).not.toBe('');

    await page.goto(`${FRONTEND_BASE}/experiments/${journey.plannedExperimentId}/runs/${journey.plannedRunId}`);
    await expect(page.getByTestId('run-detail-page')).toContainText(journey.plannedRunId.slice(0, 8));

    const panel = page.getByTestId('methodology-panel');
    await expect(panel).toBeVisible();

    const exitPolicy = page.getByTestId('exit-policy-block');
    await expect(exitPolicy, 'the executed exit policy must reach the user, not stay in the payload').toBeVisible();
    await expect(exitPolicy).toContainText('min(session_open, stop_level)');
    await expect(exitPolicy).toContainText('fills at the target level exactly');
    // The same-bar ordering is an assumption about unknowable intra-bar data, and
    // the panel must say so rather than let it read as evidence.
    await expect(exitPolicy).toContainText('stop is assumed to fill first');
    await expect(exitPolicy).toContainText('not inferred from the data');
    await expect(exitPolicy).toContainText('bar after the entry execution bar');

    await expect(panel, 'a gapped stop can lose more than 1R and that must be disclosed').toContainText(
      'never floored at -1R'
    );
    // The old disclosure claimed declared levels were never simulated. If it ever
    // comes back, the panel would contradict the fills in the journal above it.
    await expect(panel).not.toContainText('No intraday stops or targets');
    await expect(panel).not.toContainText('neither level is ever executed');

    // Provenance must describe the bars actually used, not a blanket demo claim.
    await expect(panel).toContainText('Synthetic demo bars only');
    await expect(panel).not.toContainText('Demo data only');

    await expect(panel).toContainText('next_session_open * (1 + slippage_rate)');
  });
});
